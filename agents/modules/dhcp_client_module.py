#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DHCP 客户端模块 - 支持多客户端并发
提供 DHCP 客户端功能，包括：
- 多客户端并发启动
- 共享 socket 接收 DHCP 响应
- IP 冲突检测和重试
- DHCP 选项解析（子网掩码、网关、DNS）
"""

import threading
import socket
import time
import random
import uuid
import struct
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, List, Any

# DHCP 消息类型常量
DHCP_DISCOVER = 1
DHCP_OFFER = 2
DHCP_REQUEST = 3
DHCP_DECLINE = 4
DHCP_ACK = 5
DHCP_NAK = 6
DHCP_RELEASE = 7
DHCP_INFORM = 8

# DHCP 选项类型常量
DHCP_OPT_MESSAGE_TYPE = 53
DHCP_OPT_REQUESTED_IP = 50
DHCP_OPT_SERVER_ID = 54
DHCP_OPT_PARAM_REQUEST_LIST = 55
DHCP_OPT_SUBNET_MASK = 1
DHCP_OPT_ROUTER = 3
DHCP_OPT_DNS_SERVER = 6
DHCP_OPT_DOMAIN_NAME = 15

# DHCP 客户端会话管理
dhcp_client_sessions: Dict[str, dict] = {}
dhcp_client_lock = threading.Lock()

# 共享的 DHCP 响应接收 socket
dhcp_receiver_socket: Optional[socket.socket] = None
dhcp_receiver_lock = threading.Lock()
dhcp_pending_responses: Dict[int, dict] = {}  # {xid: response_data}
dhcp_receiver_thread: Optional[threading.Thread] = None
dhcp_receiver_stop = threading.Event()
dhcp_receiver_interface: Optional[str] = None

# 日志回调函数
_log_callback = None


def set_log_callback(callback):
    """设置日志回调函数"""
    global _log_callback
    _log_callback = callback


def add_dhcp_log(source: str, message: str, level: str = 'info'):
    """添加日志"""
    if _log_callback:
        _log_callback(source, message, level)
    else:
        print(f"[{level.upper()}] {source}: {message}")


def mac_str_to_bytes(mac_str: str) -> bytes:
    """将 MAC 字符串转换为字节串"""
    return bytes.fromhex(mac_str.replace(':', '').replace('-', ''))


def mac_bytes_to_str(mac_bytes: bytes) -> str:
    """将 MAC 字节串转换为格式化字符串"""
    return ':'.join(f'{b:02x}' for b in mac_bytes)


def generate_mac_list(start_mac: str, count: int) -> List[bytes]:
    """生成连续的 MAC 地址列表"""
    start_mac_int = int(start_mac.replace(':', '').replace('-', ''), 16)
    mac_list = []
    for i in range(count):
        current_mac_int = start_mac_int + i
        current_mac_bytes = current_mac_int.to_bytes(6, byteorder='big')
        mac_list.append(current_mac_bytes)
    return mac_list


def build_dhcp_discover(xid: int, mac: bytes) -> bytes:
    """构建 DHCP Discover 报文"""
    header = struct.pack(
        '!BBBBIHH4s4s4s4s6s10s64s128s',
        1, 1, 6, 0,                # op, htype, hlen, hops
        xid, 0, 0x8000,            # xid, secs, flags (广播标志)
        b'\x00'*4, b'\x00'*4,      # ciaddr, yiaddr
        b'\x00'*4, b'\x00'*4,      # siaddr, giaddr
        mac, b'\x00'*10,           # chaddr (MAC) + pad
        b'\x00'*64, b'\x00'*128    # sname, file
    )

    options = b''
    options += struct.pack('!I', 0x63825363)  # Magic Cookie
    options += struct.pack('!BBB', DHCP_OPT_MESSAGE_TYPE, 1, DHCP_DISCOVER)
    options += struct.pack('!BBBBBB', DHCP_OPT_PARAM_REQUEST_LIST, 4,
                           DHCP_OPT_SUBNET_MASK, DHCP_OPT_ROUTER, DHCP_OPT_DNS_SERVER, DHCP_OPT_DOMAIN_NAME)
    options += b'\xff'  # 选项结束符

    return header + options


def build_dhcp_request(xid: int, mac: bytes, requested_ip: bytes, server_ip: bytes) -> bytes:
    """构建 DHCP Request 报文"""
    header = struct.pack(
        '!BBBBIHH4s4s4s4s6s10s64s128s',
        1, 1, 6, 0,                # op, htype, hlen, hops
        xid, 0, 0x8000,            # xid, secs, flags (广播标志)
        b'\x00'*4, b'\x00'*4,      # ciaddr, yiaddr
        b'\x00'*4, b'\x00'*4,      # siaddr, giaddr
        mac, b'\x00'*10,           # chaddr (MAC) + pad
        b'\x00'*64, b'\x00'*128    # sname, file
    )

    options = b''
    options += struct.pack('!I', 0x63825363)  # Magic Cookie
    options += struct.pack('!BBB', DHCP_OPT_MESSAGE_TYPE, 1, DHCP_REQUEST)
    options += struct.pack('!BB4s', DHCP_OPT_REQUESTED_IP, 4, requested_ip)
    options += struct.pack('!BB4s', DHCP_OPT_SERVER_ID, 4, server_ip)
    options += b'\xff'

    return header + options


def parse_dhcp_response(response: bytes) -> dict:
    """解析 DHCP 响应报文"""
    header = struct.unpack('!BBBBIHH4s4s4s4s6s10s64s128s', response[:236])
    op, htype, hlen, hops, xid, secs, flags, ciaddr, yiaddr, siaddr, giaddr, chaddr, _, _, _ = header

    options = response[236:]
    magic_cookie = options[:4]
    if magic_cookie != struct.pack('!I', 0x63825363):
        raise ValueError("无效的 DHCP Magic Cookie")

    opt_data = options[4:]
    parsed_options = {}
    i = 0
    while i < len(opt_data):
        opt_type = opt_data[i]
        if opt_type == 0:  # 填充选项
            i += 1
            continue
        if opt_type == 255:  # 结束选项
            break
        opt_len = opt_data[i+1]
        opt_value = opt_data[i+2:i+2+opt_len]
        parsed_options[opt_type] = opt_value
        i += 2 + opt_len

    return {
        'xid': xid,
        'yiaddr': yiaddr,
        'siaddr': siaddr,
        'options': parsed_options
    }


def dhcp_receiver_worker(interface: Optional[str] = None):
    """DHCP 响应接收工作线程"""
    global dhcp_receiver_socket, dhcp_pending_responses, dhcp_receiver_interface
    client_port = 68
    dhcp_receiver_interface = interface

    try:
        bind_ip = '0.0.0.0'

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        try:
            sock.bind((bind_ip, client_port))
            add_dhcp_log('DHCP客户端', f'响应接收器已启动，监听 {bind_ip}:{client_port}', 'info')
        except OSError:
            try:
                sock.bind((bind_ip, 0))
                actual_port = sock.getsockname()[1]
                add_dhcp_log('DHCP客户端', f'端口68被占用，使用随机端口 {bind_ip}:{actual_port}', 'warning')
            except Exception as e:
                add_dhcp_log('DHCP客户端', f'绑定失败: {e}', 'error')
                raise

        sock.settimeout(1.0)
        dhcp_receiver_socket = sock

        while not dhcp_receiver_stop.is_set():
            try:
                data, addr = sock.recvfrom(1024)

                if len(data) < 240:
                    continue

                magic_cookie = data[236:240]
                if magic_cookie != struct.pack('!I', 0x63825363):
                    continue

                try:
                    parsed = parse_dhcp_response(data)
                    xid = parsed['xid']
                    msg_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)

                    if msg_type:
                        if len(msg_type) > 1:
                            msg_type = msg_type[:1]
                        if isinstance(msg_type, int):
                            msg_type = bytes([msg_type])

                    msg_type_name = {b'\x02': 'Offer', b'\x05': 'Ack', b'\x06': 'NAK'}.get(
                        msg_type, f'Unknown({msg_type.hex() if msg_type else "None"})')
                    add_dhcp_log('DHCP客户端', f'收到响应: xid={hex(xid)}, 类型={msg_type_name}', 'info')

                    with dhcp_client_lock:
                        dhcp_pending_responses[xid] = {
                            'data': data,
                            'parsed': parsed,
                            'timestamp': time.time()
                        }

                except Exception as parse_err:
                    add_dhcp_log('DHCP客户端', f'解析响应失败: {parse_err}', 'warning')

            except socket.timeout:
                continue
            except Exception as e:
                if not dhcp_receiver_stop.is_set():
                    add_dhcp_log('DHCP客户端', f'接收响应出错: {e}', 'error')
                time.sleep(0.1)

    except Exception as e:
        add_dhcp_log('DHCP客户端', f'响应接收器启动失败: {e}', 'error')
    finally:
        if dhcp_receiver_socket:
            try:
                dhcp_receiver_socket.close()
            except:
                pass
        dhcp_receiver_socket = None
        add_dhcp_log('DHCP客户端', '响应接收器已停止', 'info')


def start_dhcp_receiver(interface: Optional[str] = None):
    """启动 DHCP 响应接收器"""
    global dhcp_receiver_thread, dhcp_receiver_interface
    with dhcp_client_lock:
        if dhcp_receiver_thread is not None and dhcp_receiver_thread.is_alive():
            if dhcp_receiver_interface == interface:
                return
            else:
                dhcp_receiver_stop.set()
                dhcp_receiver_thread.join(timeout=2.0)

        dhcp_receiver_stop.clear()
        dhcp_receiver_thread = threading.Thread(target=dhcp_receiver_worker, args=(interface,), daemon=True)
        dhcp_receiver_thread.start()
        time.sleep(0.5)


def get_dhcp_response(xid: int, timeout: float = 10, message_type: Optional[bytes] = None) -> Optional[dict]:
    """从共享接收器获取指定 xid 的 DHCP 响应"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        with dhcp_client_lock:
            if xid in dhcp_pending_responses:
                response_info = dhcp_pending_responses.pop(xid)
                parsed = response_info['parsed']

                if message_type is not None:
                    opt_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)
                    if opt_type:
                        if len(opt_type) > 1:
                            opt_type = opt_type[:1]
                        if isinstance(opt_type, int):
                            opt_type = bytes([opt_type])

                    if opt_type != message_type:
                        dhcp_pending_responses[xid] = response_info
                        time.sleep(0.1)
                        continue

                return parsed
        time.sleep(0.1)

    # 超时后再检查一次
    with dhcp_client_lock:
        if xid in dhcp_pending_responses:
            response_info = dhcp_pending_responses.pop(xid)
            parsed = response_info['parsed']
            if message_type is not None:
                opt_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)
                if opt_type:
                    if len(opt_type) > 1:
                        opt_type = opt_type[:1]
                    if isinstance(opt_type, int):
                        opt_type = bytes([opt_type])
                if opt_type == message_type:
                    return parsed
                else:
                    dhcp_pending_responses[xid] = response_info
            else:
                return parsed

    return None


def dhcp_client_task(client_id: int, mac: bytes, interface: Optional[str],
                     timeout: float = 10, session_id: Optional[str] = None) -> dict:
    """单个 DHCP 客户端任务"""
    # 导入 scapy（用于发送 DHCP 包）
    try:
        from scapy.all import IP, UDP, Ether, Raw, sendp, send
    except ImportError:
        add_dhcp_log('DHCP客户端', '需要安装 scapy: pip install scapy', 'error')
        return {
            'client_id': client_id,
            'mac': mac_bytes_to_str(mac),
            'success': False,
            'error': 'scapy 未安装',
            'status': 'error'
        }

    client_port = 68
    server_port = 67
    xid = random.randint(0, 0xFFFFFFFF)
    task_start_time = time.time()

    try:
        start_dhcp_receiver(interface)
        time.sleep(0.2)

        dst_mac = "ff:ff:ff:ff:ff:ff"

        # 1. 发送 Discover
        discover_pkt_bytes = build_dhcp_discover(xid, mac)
        discover_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=discover_pkt_bytes)
        discover_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / discover_udp

        if interface:
            sendp(discover_ether, iface=interface, verbose=False)
        else:
            send(discover_udp, verbose=False)

        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 发送Discover（xid: {hex(xid)}）', 'info')

        # 初始化会话状态
        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id] = {
                        'client_id': client_id,
                        'mac': mac_bytes_to_str(mac),
                        'status': 'discover_sent',
                        'success': None,
                        'ip': '-',
                        'subnet_mask': '-',
                        'gateway': '-',
                        'dns': '-',
                        'server_ip': '-'
                    }

        # 2. 接收 Offer
        elapsed_time = time.time() - task_start_time
        remaining_timeout = max(1.0, timeout - elapsed_time)
        offer_response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=b'\x02')

        if not offer_response:
            raise socket.timeout("未收到Offer响应")

        offered_ip = offer_response['yiaddr']
        server_ip = offer_response['siaddr']
        offered_ip_str = socket.inet_ntoa(offered_ip)
        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] 收到Offer，分配IP: {offered_ip_str}', 'info')

        # IP 冲突检测
        ip_conflict = False
        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    for other_client_id, other_client_data in dhcp_client_sessions[session_id]['clients'].items():
                        if other_client_id != client_id and other_client_data.get('offered_ip') == offered_ip_str:
                            other_status = other_client_data.get('status', '')
                            if other_status in ['request_sent', 'completed', 'offer_received']:
                                ip_conflict = True
                                add_dhcp_log('DHCP客户端', f'[警告] 客户端{client_id}收到与客户端{other_client_id}相同的IP', 'warning')
                                break

        # 重试处理
        retry_count = 0
        max_retries = 3
        while ip_conflict and retry_count < max_retries:
            retry_count += 1
            xid = random.randint(0, 0xFFFFFFFF)

            discover_pkt_bytes = build_dhcp_discover(xid, mac)
            discover_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=discover_pkt_bytes)
            discover_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / discover_udp

            if interface:
                sendp(discover_ether, iface=interface, verbose=False)
            else:
                send(discover_udp, verbose=False)

            elapsed_time = time.time() - task_start_time
            remaining_timeout = max(1.0, timeout - elapsed_time)
            offer_response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=b'\x02')

            if not offer_response:
                raise socket.timeout("未收到Offer响应（重试后）")

            offered_ip = offer_response['yiaddr']
            server_ip = offer_response['siaddr']
            offered_ip_str = socket.inet_ntoa(offered_ip)

            ip_conflict = False
            if session_id:
                with dhcp_client_lock:
                    if session_id in dhcp_client_sessions:
                        for other_client_id, other_client_data in dhcp_client_sessions[session_id]['clients'].items():
                            if other_client_id != client_id and other_client_data.get('offered_ip') == offered_ip_str:
                                other_status = other_client_data.get('status', '')
                                if other_status in ['request_sent', 'completed', 'offer_received']:
                                    ip_conflict = True
                                    break

            if not ip_conflict:
                break

        if ip_conflict:
            raise ValueError(f'多次重试后仍收到冲突的IP: {offered_ip_str}')

        # 更新状态
        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'offer_received'
                    dhcp_client_sessions[session_id]['clients'][client_id]['offered_ip'] = socket.inet_ntoa(offered_ip)

        # 3. 发送 Request
        request_pkt_bytes = build_dhcp_request(xid, mac, offered_ip, server_ip)
        request_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=request_pkt_bytes)
        request_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / request_udp

        if interface:
            sendp(request_ether, iface=interface, verbose=False)
        else:
            send(request_udp, verbose=False)

        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] 发送Request，请求IP: {socket.inet_ntoa(offered_ip)}', 'info')

        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'request_sent'

        # 4. 接收 Ack
        elapsed_time = time.time() - task_start_time
        remaining_timeout = max(1.0, timeout - elapsed_time)
        response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=None)

        if not response:
            raise socket.timeout("未收到Ack或NAK响应")

        msg_type = response['options'].get(DHCP_OPT_MESSAGE_TYPE)
        if msg_type:
            if len(msg_type) > 1:
                msg_type = msg_type[:1]
            if isinstance(msg_type, int):
                msg_type = bytes([msg_type])

        if msg_type == b'\x06':  # NAK
            raise ValueError(f'服务器拒绝请求（NAK），请求的IP: {socket.inet_ntoa(offered_ip)}')
        elif msg_type != b'\x05':
            raise ValueError(f'收到意外的响应类型: {msg_type.hex() if msg_type else "None"}')

        ack_response = response

        # 解析结果
        result = {
            'client_id': client_id,
            'mac': mac_bytes_to_str(mac),
            'ip': socket.inet_ntoa(ack_response['yiaddr']),
            'server_ip': socket.inet_ntoa(ack_response['siaddr']),
            'success': True
        }

        if DHCP_OPT_SUBNET_MASK in ack_response['options']:
            result['subnet_mask'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_SUBNET_MASK])
        if DHCP_OPT_ROUTER in ack_response['options']:
            result['gateway'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_ROUTER])
        if DHCP_OPT_DNS_SERVER in ack_response['options']:
            result['dns'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_DNS_SERVER])

        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] IP分配成功！IP: {result["ip"]}', 'info')

        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id] = result
                    dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'completed'
                    dhcp_client_sessions[session_id]['success_count'] = dhcp_client_sessions[session_id].get('success_count', 0) + 1

        return result

    except socket.timeout:
        error_msg = f'超时（{timeout}秒）'
        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] {error_msg}', 'error')
        result = {
            'client_id': client_id,
            'mac': mac_bytes_to_str(mac),
            'success': False,
            'error': error_msg,
            'status': 'timeout',
            'ip': '-',
            'subnet_mask': '-',
            'gateway': '-',
            'dns': '-',
            'server_ip': '-'
        }
        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id] = result
                    dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'timeout'
                    dhcp_client_sessions[session_id]['failed_count'] = dhcp_client_sessions[session_id].get('failed_count', 0) + 1
        return result

    except Exception as e:
        error_msg = str(e)
        add_dhcp_log('DHCP客户端', f'[客户端{client_id}] 错误: {error_msg}', 'error')
        result = {
            'client_id': client_id,
            'mac': mac_bytes_to_str(mac),
            'success': False,
            'error': error_msg,
            'status': 'error',
            'ip': '-',
            'subnet_mask': '-',
            'gateway': '-',
            'dns': '-',
            'server_ip': '-'
        }
        if session_id:
            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['clients'][client_id] = result
                    dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'error'
                    dhcp_client_sessions[session_id]['failed_count'] = dhcp_client_sessions[session_id].get('failed_count', 0) + 1
        return result


def api_dhcp_client_start(data: dict, bind_interface: str = None) -> dict:
    """启动 DHCP 客户端 API"""
    try:
        count = int(data.get('count', 1))
        start_mac = data.get('start_mac', '00:11:22:33:44:01')
        interface = data.get('interface', bind_interface or '')
        timeout = int(data.get('timeout', 10))
        max_workers = int(data.get('max_workers', 10))

        # 验证 MAC 地址格式
        try:
            mac_str_to_bytes(start_mac)
        except ValueError:
            return {
                'success': False,
                'error': '无效的MAC地址格式'
            }

        # 生成 MAC 地址列表
        mac_list = generate_mac_list(start_mac, count)

        # 创建会话
        session_id = str(uuid.uuid4())
        with dhcp_client_lock:
            dhcp_client_sessions[session_id] = {
                'session_id': session_id,
                'count': count,
                'start_mac': start_mac,
                'interface': interface,
                'timeout': timeout,
                'max_workers': max_workers,
                'clients': {},
                'success_count': 0,
                'failed_count': 0,
                'completed': False,
                'start_time': time.time()
            }

        add_dhcp_log('DHCP客户端', f'启动 {count} 个DHCP客户端，起始MAC: {start_mac}', 'info')

        # 后台线程执行
        def run_clients():
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_client = {
                    executor.submit(dhcp_client_task, i+1, mac, interface, timeout, session_id): i+1
                    for i, mac in enumerate(mac_list)
                }

                for future in future_to_client:
                    future.result()

            with dhcp_client_lock:
                if session_id in dhcp_client_sessions:
                    dhcp_client_sessions[session_id]['completed'] = True
                    dhcp_client_sessions[session_id]['end_time'] = time.time()

            add_dhcp_log('DHCP客户端', f'DHCP任务完成', 'info')

        thread = threading.Thread(target=run_clients, daemon=True)
        thread.start()

        return {
            'success': True,
            'session_id': session_id,
            'message': f'已启动 {count} 个DHCP客户端'
        }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def api_dhcp_client_status(session_id: str) -> dict:
    """获取 DHCP 客户端状态 API"""
    try:
        if not session_id:
            return {
                'success': False,
                'error': '缺少session_id参数'
            }

        with dhcp_client_lock:
            if session_id not in dhcp_client_sessions:
                return {
                    'success': False,
                    'error': '会话不存在'
                }

            session = dhcp_client_sessions[session_id]
            clients = list(session['clients'].values())

            return {
                'success': True,
                'session_id': session_id,
                'count': session['count'],
                'completed': session.get('completed', False),
                'success_count': session.get('success_count', 0),
                'failed_count': session.get('failed_count', 0),
                'clients': clients,
                'start_time': session.get('start_time'),
                'end_time': session.get('end_time')
            }

    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }