#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报文发送代理程序
运行在远程主机上，通过HTTP API接收报文配置并发送原始报文
"""

import argparse
import json
import threading
import time
import subprocess
from collections import deque
from datetime import datetime
import socket
import select
import uuid
import ftplib
import ipaddress
import struct
import random
import sqlite3
import os
from concurrent.futures import ThreadPoolExecutor

# 全局变量
start_time = None

try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError:
    print("请安装 flask 和 flask-cors: pip install flask flask-cors")
    exit(1)

try:
    from scapy.all import *
    from scapy.layers.inet import IP, TCP, UDP, ICMP
    from scapy.layers.l2 import Ether
    from scapy.sendrecv import sr1, send, sendp, sniff
    from scapy.volatile import RandShort
    # Raw 已通过 scapy.all 导入，无需单独导入
except ImportError as e:
    print(f"请安装 scapy: pip install scapy")
    print(f"导入错误详情: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

try:
    import psutil
    import socket
    PSUTIL_AVAILABLE = True
except ImportError:
    print("警告: psutil未安装，网卡获取功能可能受限。建议安装: pip install psutil")
    PSUTIL_AVAILABLE = False

app = Flask(__name__)
# 配置CORS，允许所有来源、所有方法和所有头部
CORS(app, 
     resources={r"/api/*": {"origins": "*"}},
     supports_credentials=True,
     allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
)

# 全局变量
sending_thread = None
stop_sending = threading.Event()
statistics = {
    'total_sent': 0,
    'start_time': None,
    'last_update': None,
    'rate': 0,
    'bandwidth': 0
}
stats_lock = threading.Lock()

service_logs = deque(maxlen=500)
service_lock = threading.Lock()

# 报文回放相关全局变量
replay_thread = None
stop_replay = threading.Event()
replay_statistics = {
    'running': False,
    'packets_sent': 0,
    'start_time': None,
    'rate': 0,
    'current_file': None,
    'total_files': 0,
    'current_file_index': 0
}
replay_lock = threading.Lock()

listener_states = {
    'tcp': {'running': False},
    'udp': {'running': False},
    'ftp': {'running': False},
    'http': {'running': False},
    'mail': {'running': False}
}

client_states = {
    'tcp': {'running': False},
    'udp': {'running': False},
    'ftp': {'running': False},
    'http': {'running': False},
    'mail': {'running': False}
}


def add_service_log(source, message, level='info'):
    """记录服务相关日志"""
    entry = {
        'timestamp': datetime.now().strftime('%H:%M:%S'),
        'source': source,
        'level': level,
        'message': message
    }
    with service_lock:
        service_logs.appendleft(entry)
    # 同时输出到控制台（用于调试）- 过滤 emoji 字符避免 Windows GBK 编码问题
    filtered_message = message
    try:
        # 尝试用 GBK 编码，如果失败则移除 emoji
        filtered_message.encode('gbk')
    except UnicodeEncodeError:
        # 移除 emoji 和其他非 GBK 字符
        import re
        filtered_message = re.sub(r'[^\x00-\x7F\u4e00-\u9fa5]', '', message)
    print(f"[{entry['timestamp']}] [{level.upper()}] {source}: {filtered_message}")



def get_interfaces():
    """获取可用网卡列表（使用psutil，兼容Windows 7/10）"""
    if not PSUTIL_AVAILABLE:
        print("错误: psutil未安装，无法获取网卡信息。请安装: pip install psutil")
        return []
    
    interfaces = []
    seen_macs = set()  # 用于去重，避免重复的MAC地址
    
    try:
        print("使用psutil获取网卡信息...")
        # 获取所有网卡的统计信息
        if_stats = psutil.net_if_stats()
        # 获取所有网卡的地址信息
        if_addrs = psutil.net_if_addrs()
        
        # 遍历所有网卡
        for ifname, stats in if_stats.items():
            try:
                # 跳过回环网卡
                if ifname == 'Loopback Pseudo-Interface 1' or 'Loopback' in ifname:
                    continue
                
                # 获取该网卡的地址信息
                addrs = if_addrs.get(ifname, [])
                ip = None
                mac = None
                
                for addr in addrs:
                    # 提取 IPv4 地址
                    if addr.family == socket.AF_INET:
                        # 跳过回环地址和0.0.0.0
                        if addr.address not in ('127.0.0.1', '0.0.0.0'):
                            ip = addr.address
                    # 提取 MAC 地址
                    elif addr.family == psutil.AF_LINK:
                        mac = addr.address
                
                # 如果没有MAC地址，跳过（虚拟网卡可能没有MAC）
                if not mac:
                    continue
                
                # 如果IP为0.0.0.0或None，不显示该网卡
                if not ip or ip == '0.0.0.0':
                    continue
                
                # 标准化MAC地址格式（用于去重）
                mac_normalized = mac.replace('-', ':').upper()
                
                # 去重：如果MAC地址已存在，跳过
                if mac_normalized in seen_macs:
                    continue
                seen_macs.add(mac_normalized)
                
                # 获取网卡状态
                status = '已启用' if stats.isup else '已禁用'
                
                # 查找对应的Scapy接口名称（用于发送报文）
                scapy_name = None
                try:
                    # 尝试通过MAC地址匹配Scapy接口
                    scapy_if_list = get_if_list()
                    for scapy_if in scapy_if_list:
                        try:
                            scapy_mac = get_if_hwaddr(scapy_if)
                            if scapy_mac:
                                scapy_mac_normalized = scapy_mac.replace('-', ':').upper()
                                if scapy_mac_normalized == mac_normalized:
                                    scapy_name = scapy_if
                                    break
                        except:
                            continue
                except:
                    pass
                
                # 如果找不到Scapy接口，尝试使用网卡名称（Linux）或NPF设备（Windows）
                if not scapy_name:
                    # Windows系统：尝试查找NPF设备
                    try:
                        scapy_if_list = get_if_list()
                        for scapy_if in scapy_if_list:
                            if scapy_if.startswith('\\Device\\NPF_'):
                                try:
                                    scapy_mac = get_if_hwaddr(scapy_if)
                                    if scapy_mac:
                                        scapy_mac_normalized = scapy_mac.replace('-', ':').upper()
                                        if scapy_mac_normalized == mac_normalized:
                                            scapy_name = scapy_if
                                            break
                                except:
                                    continue
                    except:
                        pass
                
                # 如果还是找不到，使用网卡名称作为Scapy接口名（Linux系统通常可以）
                if not scapy_name:
                    scapy_name = ifname
                
                # 添加接口信息
                interfaces.append({
                    'name': scapy_name,  # Scapy使用的接口名称（用于发送报文）
                    'display_name': ifname,  # 友好显示名称（如"以太网"、"以太网 2"）
                    'ip': ip,  # IP地址
                    'mac': mac,  # MAC地址
                    'status': status,  # 状态（已启用/已禁用）
                    'mtu': stats.mtu,  # MTU
                    'speed': stats.speed if stats.speed > 0 else None  # 网卡速率（Mbps）
                })
                print(f"添加接口: {ifname} ({scapy_name}) - IP: {ip}, MAC: {mac}, 状态: {status}")
            except Exception as e:
                print(f"处理网卡 {ifname} 时出错: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"使用psutil获取到 {len(interfaces)} 个网卡")
        return interfaces
        
    except Exception as e:
        print(f"使用psutil获取网卡信息失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def parse_hex_data(data_str):
    """解析十六进制数据字符串"""
    if not data_str:
        return b''
    # 移除空格和换行
    data_str = data_str.replace(' ', '').replace('\n', '').replace('\r', '')
    try:
        return bytes.fromhex(data_str)
    except:
        # 如果不是十六进制，尝试作为ASCII
        return data_str.encode('utf-8')


def parse_number(value):
    """解析数字（支持十六进制）"""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        if value.startswith('0x') or value.startswith('0X'):
            return int(value, 16)
        return int(value)
    return 0


def build_packet(packet_config, variation_index=0):
    """构造报文"""
    # 解析MAC地址（支持多种格式）
    def normalize_mac(mac_str):
        if not mac_str:
            return None
        # 统一转换为冒号格式
        mac_str = mac_str.replace('-', ':').replace('.', ':').upper()
        return mac_str
    
    src_mac = normalize_mac(packet_config.get('src_mac', ''))
    dst_mac = normalize_mac(packet_config.get('dst_mac', ''))
    
    # 解析IP地址（支持变化）
    src_ip = packet_config.get('src_ip', '127.0.0.1')
    dst_ip = packet_config.get('dst_ip', '127.0.0.1')
    
    # 处理报文变化
    variations = packet_config.get('variations', {})
    
    # IP地址递增逻辑
    def increment_ip(ip_str, increment_value):
        """IP地址递增，增长到255.255.255.255后循环回初始值"""
        try:
            import ipaddress
            # 将IP字符串转换为整数
            base_ip_int = int(ipaddress.IPv4Address(ip_str))
            max_ip_int = int(ipaddress.IPv4Address('255.255.255.255'))
            # 计算循环范围（从base_ip到max_ip的范围）
            cycle_range = max_ip_int - base_ip_int + 1
            # 计算递增后的值（考虑循环）
            new_ip_int = base_ip_int + (increment_value % cycle_range)
            # 转换回IP字符串
            return str(ipaddress.IPv4Address(new_ip_int))
        except Exception as e:
            print(f"IP递增失败: {e}, 使用原始IP: {ip_str}")
            return ip_str
    
    # 端口递增逻辑
    def increment_port(port, increment_value, max_port=65535):
        """端口递增，增长到max_port后循环回初始值"""
        try:
            base_port = int(port)
            # 计算循环范围（从base_port到max_port的范围）
            cycle_range = max_port - base_port + 1
            # 计算递增后的值（考虑循环）
            new_port = base_port + (increment_value % cycle_range)
            return new_port
        except Exception as e:
            print(f"端口递增失败: {e}, 使用原始端口: {port}")
            return port
    
    # 处理源IP递增
    if 'src_ip' in variations and variations['src_ip'].get('type') == 'increment':
        base_src_ip = src_ip
        increment_count = variation_index
        src_ip = increment_ip(base_src_ip, increment_count)
        print(f"源IP递增: {base_src_ip} -> {src_ip} (递增: {increment_count})")
    
    # 处理目标IP递增
    if 'dst_ip' in variations and variations['dst_ip'].get('type') == 'increment':
        base_dst_ip = dst_ip
        increment_count = variation_index
        dst_ip = increment_ip(base_dst_ip, increment_count)
        print(f"目标IP递增: {base_dst_ip} -> {dst_ip} (递增: {increment_count})")
    
    # 解析端口（支持变化）
    src_port = packet_config.get('src_port', 0)
    dst_port = packet_config.get('dst_port', 0)
    
    # 处理源端口递增
    if 'src_port' in variations and variations['src_port'].get('type') == 'increment':
        base_src_port = src_port
        increment_count = variation_index
        src_port = increment_port(base_src_port, increment_count)
        print(f"源端口递增: {base_src_port} -> {src_port} (递增: {increment_count})")
    
    # 处理目标端口递增
    if 'dst_port' in variations and variations['dst_port'].get('type') == 'increment':
        base_dst_port = dst_port
        increment_count = variation_index
        dst_port = increment_port(base_dst_port, increment_count)
        print(f"目标端口递增: {base_dst_port} -> {dst_port} (递增: {increment_count})")
    
    # 构造报文
    protocol = packet_config.get('protocol', 'tcp').lower()
    
    # ARP协议
    if protocol == 'arp':
        # 解析MAC地址格式（支持多种格式）
        def normalize_mac(mac_str):
            if not mac_str:
                return None
            # 统一转换为冒号格式
            mac_str = mac_str.replace('-', ':').replace('.', ':').upper()
            return mac_str
        
        src_mac_norm = normalize_mac(src_mac)
        dst_mac_norm = normalize_mac(dst_mac)
        
        # 以太网层
        if src_mac_norm and dst_mac_norm:
            ether = Ether(src=src_mac_norm, dst=dst_mac_norm)
        elif dst_mac_norm:
            ether = Ether(dst=dst_mac_norm)
        else:
            ether = Ether()
        
        # ARP类型
        arp_type = packet_config.get('arp_type', 'arp_req').lower()
        
        # 构造ARP层
        if arp_type == 'arp_req':
            op = 1  # ARP请求
        elif arp_type == 'arp_reply':
            op = 2  # ARP应答
        elif arp_type == 'rarp_req':
            op = 3  # RARP请求
        elif arp_type == 'rarp_reply':
            op = 4  # RARP应答
        else:
            op = 1  # 默认ARP请求
        
        arp_layer = ARP(
            op=op,
            hwsrc=src_mac_norm if src_mac_norm else '00:00:00:00:00:00',
            psrc=src_ip,
            hwdst=dst_mac_norm if dst_mac_norm else '00:00:00:00:00:00',
            pdst=dst_ip
        )
        
        packet = ether / arp_layer
        return packet
    
    # ICMP协议
    if protocol == 'icmp':
        # ICMP参数（先获取，用于IP层配置）
        icmp_type = packet_config.get('icmp_type', 'echo').lower()
        data_length = packet_config.get('data_length', 0)
        
        # 以太网层
        if src_mac and dst_mac:
            ether = Ether(src=src_mac, dst=dst_mac)
        elif dst_mac:
            ether = Ether(dst=dst_mac)
        else:
            ether = Ether()
        
        # IP层配置
        # 对于超大包（Ping of Death），需要设置flags允许分片
        # IP flags: 0x4000 = Don't Fragment (DF), 0x2000 = More Fragments (MF)
        # 对于超大包，不设置DF标志（flags=0），允许分片
        ip_flags = 0  # 默认允许分片
        
        ip_layer = IP(src=src_ip, dst=dst_ip, flags=ip_flags)
        
        # 构造ICMP载荷数据（普通ICMP包）
        payload_data = b''
        if data_length > 0 and icmp_type != 'ping_of_death':
            # 生成指定长度的数据（可以使用时间戳等）
            import struct
            import time
            # 生成包含时间戳的数据
            timestamp = int(time.time() * 1000)  # 毫秒时间戳
            # 填充数据
            payload_data = struct.pack('!Q', timestamp)  # 8字节时间戳
            # 如果数据长度大于8，填充剩余部分
            if data_length > 8:
                remaining = data_length - 8
                payload_data += b'\x00' * remaining
        
        # ICMP类型
        if icmp_type == 'echo':
            # ICMP Echo Request (type=8, code=0)
            icmp_layer = ICMP(type=8, code=0)
        elif icmp_type == 'echo_reply':
            # ICMP Echo Reply (type=0, code=0)
            icmp_layer = ICMP(type=0, code=0)
        elif icmp_type == 'smurf':
            # ICMP Smurf: 使用广播MAC和广播IP的ICMP Echo Request
            # 目的MAC应该是FF:FF:FF:FF:FF:FF，目的IP应该是广播地址（如4.4.4.255）
            # 这些值应该在前端已经自动填充
            icmp_layer = ICMP(type=8, code=0)
        elif icmp_type == 'ping_of_death':
            # Ping of Death: 发送超大ICMP包（超过65535字节）
            # 注意：此功能仅用于产品功能测试
            # 重要：IP包总长度字段只有16位（最大65535），所以需要使用IP分片来发送超大包
            # 我们将构造一个特殊的标记，表示这是一个需要分片发送的Ping of Death包
            # 实际的分片发送将在send_packets_worker中处理
            
            # 对于Ping of Death，确保数据长度足够大
            if data_length < 65536:
                data_length = 65536  # 至少65536字节
            
            # Ping of Death: 生成超大载荷
            import struct
            import time
            import random
            # 生成包含时间戳和随机数据的大包
            timestamp = int(time.time() * 1000)
            # 前8字节是时间戳
            payload_data = struct.pack('!Q', timestamp)
            # 剩余部分填充随机数据（使用更高效的方式生成）
            remaining = data_length - 8
            if remaining > 0:
                # 使用random.choices生成随机字节，比循环更高效
                payload_data += bytes(random.choices(range(256), k=remaining))
            
            # 对于Ping of Death，我们不在build_packet中构造完整包
            # 而是返回一个标记，表示需要分片发送
            # 将载荷数据存储在packet_config中，供发送时使用
            icmp_layer = ICMP(type=8, code=0)
            # 注意：这里不附加载荷，载荷将在发送时通过分片处理
        else:
            # 默认Echo Request
            icmp_layer = ICMP(type=8, code=0)
        
        # 注意：普通ICMP包的payload_data已经在上面生成（如果不是ping_of_death）
        
        # 对于Ping of Death，使用特殊处理：不在这里构造完整包，而是标记需要分片
        if icmp_type == 'ping_of_death':
            # 将载荷数据存储在packet_config中，供发送时使用
            # 构造一个小的ICMP包作为占位符
            icmp_layer = ICMP(type=8, code=0)
            # 不附加载荷，载荷将在发送时通过分片处理
            packet = ether / ip_layer / icmp_layer
            # 在packet对象上添加一个标记，表示这是Ping of Death包
            packet._ping_of_death_payload = payload_data
            packet._ping_of_death = True
            return packet
        
        # 普通ICMP包：附加载荷数据
        if payload_data:
            icmp_layer = icmp_layer / payload_data
        
        # 组装完整报文
        packet = ether / ip_layer / icmp_layer
        
        return packet
    
    # UDP协议（单独处理，因为需要支持teardrop）
    if protocol == 'udp':
        # 解析MAC地址格式（支持多种格式）
        def normalize_mac(mac_str):
            if not mac_str:
                return None
            # 统一转换为冒号格式
            mac_str = mac_str.replace('-', ':').replace('.', ':').upper()
            return mac_str
        
        src_mac_norm = normalize_mac(src_mac)
        dst_mac_norm = normalize_mac(dst_mac)
        
        # 以太网层
        if src_mac_norm and dst_mac_norm:
            ether = Ether(src=src_mac_norm, dst=dst_mac_norm)
        elif dst_mac_norm:
            ether = Ether(dst=dst_mac_norm)
        else:
            ether = Ether()
        
        # IP层
        ip_layer = IP(src=src_ip, dst=dst_ip)
        
        # UDP类型
        udp_type = packet_config.get('udp_type', 'udp').lower()
        data_length = packet_config.get('data_length', 0)
        
        # 对于teardrop，使用特殊处理（类似Ping of Death）
        if udp_type == 'teardrop':
            # Teardrop: 使用IP分片发送恶意UDP包
            # 注意：此功能仅用于产品功能测试
            # 参考用户代码：构造两个分片
            # 第一个分片：flags=1, frag=0, 包含UDP头和部分数据
            # 第二个分片：flags=0, frag=1, 包含剩余数据
            payload_data = (b'aaaa2bbb' * 8) + (b'00001000' * 10)
            
            # 标记为teardrop包，将在发送时进行分片处理
            udp_layer = UDP(sport=src_port, dport=dst_port)
            packet = ether / ip_layer / udp_layer
            packet._teardrop_payload = payload_data
            packet._teardrop = True
            return packet
        else:
            # 普通UDP包
            udp_layer = UDP(sport=src_port, dport=dst_port)
            
            # 生成UDP载荷数据
            payload_data = b''
            if data_length > 0:
                import struct
                import time
                # 生成包含时间戳的数据
                timestamp = int(time.time() * 1000)  # 毫秒时间戳
                payload_data = struct.pack('!Q', timestamp)  # 8字节时间戳
                if data_length > 8:
                    remaining = data_length - 8
                    payload_data += b'\x00' * remaining
            
            # 组装UDP报文
            if payload_data:
                packet = ether / ip_layer / udp_layer / payload_data
            else:
                packet = ether / ip_layer / udp_layer
            
            return packet
    
    # 以太网层（TCP等其他协议）
    if src_mac and dst_mac:
        packet = Ether(src=src_mac, dst=dst_mac)
    else:
        packet = Ether()
    
    # IP层
    ip_layer = IP(src=src_ip, dst=dst_ip)
    
    # 传输层
    if protocol == 'tcp':
        # TCP参数
        seq = parse_number(packet_config.get('sequence', 0))
        ack = parse_number(packet_config.get('ack', 0))
        flags = packet_config.get('flags', [])
        window = packet_config.get('window', 8192)
        urgent = packet_config.get('urgent', 0)
        
        # 转换标志位
        tcp_flags = 0
        if 'SYN' in flags:
            tcp_flags |= 0x02
        if 'ACK' in flags:
            tcp_flags |= 0x10
        if 'FIN' in flags:
            tcp_flags |= 0x01
        if 'RST' in flags:
            tcp_flags |= 0x04
        if 'PSH' in flags:
            tcp_flags |= 0x08
        if 'URG' in flags:
            tcp_flags |= 0x20
        
        transport_layer = TCP(
            sport=src_port,
            dport=dst_port,
            seq=seq,
            ack=ack,
            flags=tcp_flags,
            window=window,
            urgptr=urgent
        )
    else:
        transport_layer = None
    
    # 数据载荷
    data = packet_config.get('data', '')
    payload = parse_hex_data(data) if data else b''
    
    # 组装报文
    if transport_layer:
        packet = packet / ip_layer / transport_layer / payload
    else:
        packet = packet / ip_layer / payload
    
    return packet


def send_packets_worker(interface, packet_config, send_config):
    """发送报文的工作线程"""
    global statistics, stop_sending, start_time
    
    count = send_config.get('count', 1)
    interval = send_config.get('interval', 0) / 1000.0  # 转换为秒
    continuous = send_config.get('continuous', False)
    
    # 调试：打印配置信息
    print(f"开始发送报文 - 接口: {interface}, 协议: {packet_config.get('protocol')}, 连续: {continuous}, 数量: {count}")
    
    variations = packet_config.get('variations', {})
    # 检查是否有递增配置
    has_increment = any(var_config.get('type') == 'increment' for var_config in variations.values())
    
    total_to_send = count if not continuous else float('inf')
    
    sent = 0
    start_time = time.time()
    last_update_time = start_time
    
    with stats_lock:
        statistics['start_time'] = start_time
        statistics['last_update'] = start_time
    
    try:
        while not stop_sending.is_set() and sent < total_to_send:
            # 计算变化索引：每次发送都递增，递增逻辑在build_packet中处理循环
            # var_index直接等于sent，这样每次发送都会递增
            var_index = sent if has_increment else 0
            
            # 构造报文
            try:
                packet = build_packet(packet_config, var_index)
            except Exception as e:
                print(f"构造报文失败: {e}")
                import traceback
                traceback.print_exc()
                break
            
            # 发送报文
            try:
                # 检查是否是Teardrop包（需要分片发送）
                if hasattr(packet, '_teardrop') and packet._teardrop:
                    # Teardrop: 使用IP分片发送恶意UDP包（参考用户提供的代码）
                    payload_data = packet._teardrop_payload
                    src_ip = packet[IP].src
                    dst_ip = packet[IP].dst
                    
                    # 获取MAC地址（从原始包中）
                    src_mac = None
                    dst_mac = None
                    if Ether in packet:
                        src_mac = packet[Ether].src
                        dst_mac = packet[Ether].dst
                    
                    # 获取UDP端口（从packet_config获取）
                    src_port = packet_config.get('src_port', 0)
                    dst_port = packet_config.get('dst_port', 0)
                    # 如果packet中有UDP层，优先使用packet中的端口
                    if UDP in packet:
                        src_port = packet[UDP].sport
                        dst_port = packet[UDP].dport
                    
                    # 生成一个唯一的ID用于分片
                    import random
                    frag_id = random.randint(1, 65535)
                    
                    # Teardrop分片：参考用户提供的代码实现
                    # 第一个分片：UDP头（8字节）+ 数据(64字节) = 72字节，frag=0，flags=1 (More Fragments)
                    # 第二个分片：UDP头（8字节）+ 数据(80字节) = 88字节，frag=1（偏移8字节），flags=0 (Last Fragment)
                    # 第二个分片从偏移8字节开始，其UDP头会与第一个分片的数据部分重叠
                    
                    # 第一个分片：UDP头 + 数据(64字节)
                    udp_header = UDP(sport=src_port, dport=dst_port)
                    first_frag_data = b'aaaa2bbb' * 8  # 64字节数据
                    first_frag_payload = bytes(udp_header) + first_frag_data
                    
                    # 第二个分片：UDP头 + 数据(80字节)，偏移8字节（frag=1）
                    second_frag_data = b'00001000' * 10  # 80字节数据
                    second_frag_payload = bytes(udp_header) + second_frag_data
                    
                    print(f"发送Teardrop分片包: ID={frag_id}, 源端口={src_port}, 目的端口={dst_port}")
                    print(f"  第一个分片: UDP头(8字节) + 数据(64字节) = 72字节, frag=0, flags=1")
                    print(f"  第二个分片: UDP头(8字节) + 数据(80字节) = 88字节, frag=1(偏移8字节), flags=0")
                    
                    # 发送第一个分片：包含UDP头和数据
                    frag_packet1 = IP(
                        src=src_ip,
                        dst=dst_ip,
                        flags=1,  # More Fragments
                        frag=0,   # 第一个分片，偏移0
                        id=frag_id,
                        proto=17  # UDP协议号
                    ) / first_frag_payload
                    
                    # 构造以太网层
                    if src_mac and dst_mac:
                        ether_layer = Ether(src=src_mac, dst=dst_mac)
                    elif dst_mac:
                        ether_layer = Ether(dst=dst_mac)
                    else:
                        ether_layer = Ether()
                    
                    sendp(ether_layer / frag_packet1, iface=interface, verbose=False)
                    
                    # 发送第二个分片：包含UDP头和数据，偏移8字节（导致重叠）
                    frag_packet2 = IP(
                        src=src_ip,
                        dst=dst_ip,
                        flags=0,  # Last Fragment
                        frag=1,   # 第二个分片，偏移1*8=8字节（导致重叠）
                        id=frag_id,
                        proto=17  # UDP协议号
                    ) / second_frag_payload
                    
                    sendp(ether_layer / frag_packet2, iface=interface, verbose=False)
                    
                    sent += 1
                    if sent % 10 == 0:  # 每10个包打印一次
                        print(f"已发送 {sent} 个Teardrop分片包")
                # 检查是否是Ping of Death包（需要分片发送）
                elif hasattr(packet, '_ping_of_death') and packet._ping_of_death:
                    # Ping of Death: 使用IP分片发送超大ICMP包（参考用户提供的代码）
                    payload_data = packet._ping_of_death_payload
                    src_ip = packet[IP].src
                    dst_ip = packet[IP].dst
                    
                    # 获取MAC地址（从原始包中）
                    src_mac = None
                    dst_mac = None
                    if Ether in packet:
                        src_mac = packet[Ether].src
                        dst_mac = packet[Ether].dst
                    
                    # 生成一个唯一的ID用于分片（每个包使用不同的ID）
                    import random
                    frag_id = random.randint(1, 65535)
                    
                    # 分片参数（参考用户代码）
                    # 每个分片的数据大小（字节），用户代码使用800字节
                    # 但为了更可靠，我们使用更小的分片，比如每个分片808字节（101*8）
                    frag_data_size = 808  # 101 * 8，确保是8的倍数
                    total_payload_size = len(payload_data)
                    
                    # 计算需要多少个分片
                    # frag字段是分片偏移量，以8字节为单位
                    num_fragments = (total_payload_size + frag_data_size - 1) // frag_data_size
                    
                    print(f"发送Ping of Death分片包: {total_payload_size} 字节，分成 {num_fragments} 个片段，ID={frag_id}")
                    
                    # ICMP头大小（8字节）
                    icmp_header_size = 8
                    
                    # 发送所有分片
                    for i in range(num_fragments):
                        frag_offset = i * frag_data_size
                        frag_data = payload_data[frag_offset:frag_offset + frag_data_size]
                        
                        # 构造分片IP包
                        # 注意：第一个分片包含ICMP头，后续分片只包含数据
                        if i == 0:
                            # 第一个分片：包含ICMP头（8字节）和数据
                            icmp_header = ICMP(type=8, code=0)
                            frag_payload = bytes(icmp_header) + frag_data
                            # 第一个分片的frag=0（从IP载荷开始位置）
                            frag_value = 0
                        else:
                            # 后续分片：只包含数据（不包含ICMP头）
                            frag_payload = frag_data
                            # frag字段表示整个IP载荷（包括ICMP头）的偏移量，以8字节为单位
                            # 第一个分片包含：ICMP头(8字节) + 数据(808字节) = 816字节
                            # 第二个分片从816字节开始，frag = 816 / 8 = 102
                            # 第三个分片从1624字节开始，frag = 1624 / 8 = 203
                            # 计算公式：frag = (ICMP头大小 + 前面所有分片的数据大小) / 8
                            frag_value = (icmp_header_size + frag_offset) // 8
                        
                        # 如果不是最后一个分片，设置More Fragments标志（flags=1）
                        # 如果是最后一个分片，flags=0
                        is_last = (i == num_fragments - 1)
                        flags = 0 if is_last else 1  # 0=最后一片，1=还有更多分片
                        
                        # 构造IP分片包（参考用户代码）
                        frag_packet = IP(
                            src=src_ip,
                            dst=dst_ip,
                            flags=flags,
                            frag=frag_value,
                            id=frag_id,
                            proto=1  # ICMP协议号
                        ) / frag_payload
                        
                        # 调试信息：打印分片详情（仅前几个分片）
                        if i < 3 or is_last:
                            print(f"  分片 {i+1}/{num_fragments}: frag={frag_value}, flags={flags}, 载荷大小={len(frag_payload)}字节, 偏移={frag_offset}字节")
                        
                        # 构造以太网层（保留原始MAC地址）
                        if src_mac and dst_mac:
                            ether_layer = Ether(src=src_mac, dst=dst_mac)
                        elif dst_mac:
                            ether_layer = Ether(dst=dst_mac)
                        else:
                            ether_layer = Ether()
                        
                        # 发送分片
                        sendp(ether_layer / frag_packet, iface=interface, verbose=False)
                    
                    sent += 1
                    if sent % 10 == 0:  # 每10个包打印一次
                        print(f"已发送 {sent} 个Ping of Death分片包")
                elif hasattr(packet, '_teardrop') and packet._teardrop:
                    # Teardrop: 使用IP分片发送恶意UDP包（参考用户提供的代码）
                    payload_data = packet._teardrop_payload
                    src_ip = packet[IP].src
                    dst_ip = packet[IP].dst
                    
                    # 获取MAC地址（从原始包中）
                    src_mac = None
                    dst_mac = None
                    if Ether in packet:
                        src_mac = packet[Ether].src
                        dst_mac = packet[Ether].dst
                    
                    # 获取UDP端口（从packet_config获取）
                    src_port = packet_config.get('src_port', 0)
                    dst_port = packet_config.get('dst_port', 0)
                    # 如果packet中有UDP层，优先使用packet中的端口
                    if UDP in packet:
                        src_port = packet[UDP].sport
                        dst_port = packet[UDP].dport
                    
                    # 生成一个唯一的ID用于分片
                    import random
                    frag_id = random.randint(1, 65535)
                    
                    # Teardrop分片：参考用户提供的代码实现
                    # 第一个分片：UDP头（8字节）+ 数据(64字节) = 72字节，frag=0，flags=1 (More Fragments)
                    # 第二个分片：UDP头（8字节）+ 数据(80字节) = 88字节，frag=1（偏移8字节），flags=0 (Last Fragment)
                    # 第二个分片从偏移8字节开始，其UDP头会与第一个分片的数据部分重叠
                    
                    # 第一个分片：UDP头 + 数据(64字节)
                    udp_header = UDP(sport=src_port, dport=dst_port)
                    first_frag_data = b'aaaa2bbb' * 8  # 64字节数据
                    first_frag_payload = bytes(udp_header) + first_frag_data
                    
                    # 第二个分片：UDP头 + 数据(80字节)，偏移8字节（frag=1）
                    second_frag_data = b'00001000' * 10  # 80字节数据
                    second_frag_payload = bytes(udp_header) + second_frag_data
                    
                    print(f"发送Teardrop分片包: ID={frag_id}, 源端口={src_port}, 目的端口={dst_port}")
                    print(f"  第一个分片: UDP头(8字节) + 数据(64字节) = 72字节, frag=0, flags=1")
                    print(f"  第二个分片: UDP头(8字节) + 数据(80字节) = 88字节, frag=1(偏移8字节), flags=0")
                    
                    # 发送第一个分片：包含UDP头和数据
                    frag_packet1 = IP(
                        src=src_ip,
                        dst=dst_ip,
                        flags=1,  # More Fragments
                        frag=0,   # 第一个分片，偏移0
                        id=frag_id,
                        proto=17  # UDP协议号
                    ) / first_frag_payload
                    
                    # 构造以太网层
                    if src_mac and dst_mac:
                        ether_layer = Ether(src=src_mac, dst=dst_mac)
                    elif dst_mac:
                        ether_layer = Ether(dst=dst_mac)
                    else:
                        ether_layer = Ether()
                    
                    sendp(ether_layer / frag_packet1, iface=interface, verbose=False)
                    
                    # 发送第二个分片：包含UDP头和数据，偏移8字节（导致重叠）
                    frag_packet2 = IP(
                        src=src_ip,
                        dst=dst_ip,
                        flags=0,  # Last Fragment
                        frag=1,   # 第二个分片，偏移1*8=8字节（导致重叠）
                        id=frag_id,
                        proto=17  # UDP协议号
                    ) / second_frag_payload
                    
                    sendp(ether_layer / frag_packet2, iface=interface, verbose=False)
                    
                    sent += 1
                    if sent % 10 == 0:  # 每10个包打印一次
                        print(f"已发送 {sent} 个Teardrop分片包")
                else:
                    # 普通包：正常发送
                    sendp(packet, iface=interface, verbose=False)
                    sent += 1
                
                # 每100个包打印一次进度（仅用于调试，Ping of Death已在上面打印）
                if not (hasattr(packet, '_ping_of_death') and packet._ping_of_death):
                    if sent % 100 == 0:
                        print(f"已发送 {sent} 个报文")
                
                # 更新统计
                current_time = time.time()
                if current_time - last_update_time >= 1.0:  # 每秒更新一次
                    elapsed = current_time - start_time
                    rate = sent / elapsed if elapsed > 0 else 0
                    # 估算带宽（假设平均报文大小）
                    # 对于Ping of Death，使用实际载荷大小
                    if hasattr(packet, '_ping_of_death') and packet._ping_of_death:
                        avg_packet_size = len(packet._ping_of_death_payload) * 8  # 转换为比特
                    else:
                        try:
                            avg_packet_size = len(packet) * 8  # 转换为比特
                        except:
                            avg_packet_size = 1500 * 8  # 默认值
                    bandwidth = rate * avg_packet_size
                    
                    with stats_lock:
                        statistics['total_sent'] = sent
                        statistics['rate'] = int(rate)
                        statistics['bandwidth'] = int(bandwidth)
                        statistics['last_update'] = current_time
                    
                    last_update_time = current_time
                
                # 延迟（interval已经是秒，不需要再转换）
                if interval > 0:
                    time.sleep(interval)
            except Exception as e:
                error_msg = str(e)
                print(f"发送报文失败: {error_msg}")
                # 记录详细错误信息用于调试
                import traceback
                traceback.print_exc()
                # 对于超大包，提供更详细的错误信息
                try:
                    packet_size = len(packet)
                    if packet_size > 65535:
                        print(f"超大包大小: {packet_size} 字节，可能需要系统支持IP分片")
                        print("提示: 某些系统可能无法发送超过65535字节的单个IP包")
                        print("建议: 尝试使用较小的数据长度（如65535字节）或检查系统是否支持IP分片")
                except:
                    pass
                # 不立即break，继续尝试发送（可能只是单个包失败）
                # 但如果连续失败多次，应该停止
                if sent == 0:  # 如果第一个包就失败，停止
                    break
    
    except Exception as e:
        print(f"发送线程错误: {e}")
    finally:
        stop_sending.clear()
        with stats_lock:
            statistics['total_sent'] = sent
            if start_time:
                elapsed = time.time() - start_time
                statistics['rate'] = int(sent / elapsed) if elapsed > 0 else 0


@app.route('/api/interfaces', methods=['GET'])
def api_interfaces():
    """获取网卡列表"""
    try:
        print(f"[API] 收到获取网卡列表请求，来源: {request.remote_addr}")
        interfaces = get_interfaces()
        print(f"[API] 返回 {len(interfaces)} 个接口")
        return jsonify({
            'success': True,
            'interfaces': interfaces
        })
    except Exception as e:
        print(f"[API] 获取网卡列表失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/send_packet', methods=['POST'])
def api_send_packet():
    """发送报文"""
    global sending_thread, stop_sending
    
    try:
        data = request.json
        interface = data.get('interface')
        packet_config = data.get('packet_config', {})
        send_config = data.get('send_config', {})
        
        if not interface:
            return jsonify({
                'success': False,
                'error': '缺少网卡参数'
            }), 400
        
        # 停止之前的发送
        if sending_thread and sending_thread.is_alive():
            stop_sending.set()
            sending_thread.join(timeout=2)
        
        # 重置停止标志
        stop_sending.clear()
        
        # 启动发送线程
        sending_thread = threading.Thread(
            target=send_packets_worker,
            args=(interface, packet_config, send_config)
        )
        sending_thread.daemon = True
        sending_thread.start()
        
        return jsonify({
            'success': True,
            'message': '开始发送报文'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/statistics', methods=['GET'])
def api_statistics():
    """获取发送统计"""
    with stats_lock:
        return jsonify({
            'success': True,
            'statistics': statistics.copy()
        })


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """停止发送"""
    global stop_sending
    
    stop_sending.set()
    return jsonify({
        'success': True,
        'message': '已停止发送'
    })


@app.route('/api/health', methods=['GET'])
def api_health():
    """健康检查"""
    return jsonify({
        'success': True,
        'status': 'running',
        'timestamp': datetime.now().isoformat()
    })


# 服务部署相关类与函数

class TCPListenerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.stop_event = threading.Event()
        self.server_socket = None
        self.connection_sockets = {}

    def run(self):
        host = self.state['host']
        port = self.state['port']
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((host, port))
            self.server_socket.listen(100)
            self.server_socket.settimeout(1.0)
            add_service_log('TCP监听', f'监听启动: {host}:{port}')
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.server_socket.accept()
                    client_socket.settimeout(1.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'bytes_received': 0
                        }
                    self.connection_sockets[conn_id] = client_socket
                    add_service_log('TCP监听', f'连接建立: {addr[0]}:{addr[1]}')
                    threading.Thread(
                        target=self._handle_client,
                        args=(client_socket, conn_id),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        add_service_log('TCP监听', '监听套接字异常', 'error')
                    break
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('TCP监听', f'接收连接异常: {e}', 'error')
            add_service_log('TCP监听', f'监听停止: {host}:{port}')
        except Exception as e:
            add_service_log('TCP监听', f'启动失败: {e}', 'error')
        finally:
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
            with service_lock:
                self.state['running'] = False

    def _handle_client(self, client_socket, conn_id):
        try:
            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(4096)
                    if not data:
                        break
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['bytes_received'] += len(data)
                    add_service_log('TCP监听', f'收到数据 ({len(data)} bytes)')
                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('TCP监听', f'客户端异常: {e}', 'error')
                    break
        finally:
            try:
                client_socket.close()
            except:
                pass
            with service_lock:
                conn_info = self.state['connections'].pop(conn_id, None)
            self.connection_sockets.pop(conn_id, None)
            if conn_info:
                add_service_log('TCP监听', f"连接断开: {conn_info['address']}")

    def stop(self):
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
        for conn_id, sock in list(self.connection_sockets.items()):
            try:
                sock.close()
            except:
                pass


class UDPListenerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.stop_event = threading.Event()
        self.sock = None

    def run(self):
        host = self.state['host']
        port = self.state['port']
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((host, port))
            self.sock.settimeout(1.0)
            add_service_log('UDP监听', f'监听启动: {host}:{port}')
            while not self.stop_event.is_set():
                try:
                    data, addr = self.sock.recvfrom(4096)
                    with service_lock:
                        self.state['packets'] += 1
                    add_service_log('UDP监听', f'收到来自 {addr[0]}:{addr[1]} 的数据 ({len(data)} bytes)')
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        add_service_log('UDP监听', '监听套接字异常', 'error')
                    break
                except Exception as e:
                    add_service_log('UDP监听', f'接收异常: {e}', 'error')
            add_service_log('UDP监听', f'监听停止: {host}:{port}')
        except Exception as e:
            add_service_log('UDP监听', f'启动失败: {e}', 'error')
        finally:
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
            with service_lock:
                self.state['running'] = False

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.close()
            except:
                pass


class SimpleFTPServerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.stop_event = threading.Event()
        self.server_socket = None
        # 获取FTP配置
        self.username = state.get('username', 'tdhx')
        self.password = state.get('password', 'tdhx@2017')
        # 目录设置为当前脚本路径
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.ftp_root = state.get('directory', script_dir)
        # 确保目录存在并有所有权限
        os.makedirs(self.ftp_root, exist_ok=True)
        # 设置目录权限（Windows上可能不支持，但Linux上会生效）
        try:
            os.chmod(self.ftp_root, 0o777)
        except:
            pass  # Windows上忽略权限设置错误

    def run(self):
        host = self.state['host']
        port = self.state['port']
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((host, port))
            self.server_socket.listen(5)
            self.server_socket.settimeout(1.0)
            add_service_log('FTP服务器', f'服务器启动: {host}:{port}')
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.server_socket.accept()
                    client_socket.settimeout(1.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'commands': 0
                        }
                    add_service_log('FTP服务器', f'客户端连接: {addr[0]}:{addr[1]}')
                    threading.Thread(
                        target=self._handle_session,
                        args=(client_socket, conn_id),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        add_service_log('FTP服务器', '监听套接字异常', 'error')
                    break
                except Exception as e:
                    add_service_log('FTP服务器', f'会话异常: {e}', 'error')
            add_service_log('FTP服务器', f'服务器停止: {host}:{port}')
        except Exception as e:
            add_service_log('FTP服务器', f'启动失败: {e}', 'error')
        finally:
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
            with service_lock:
                self.state['running'] = False

    def _handle_session(self, client_socket, conn_id):
        import os
        import stat
        import time
        
        # 使用配置的目录
        ftp_root = self.ftp_root
        host = self.state.get('host', '0.0.0.0')
        current_dir = '/'
        logged_in = False
        username = None
        data_socket = None
        data_port = None
        pasv_socket = None
        pasv_port = None
        
        # 使用配置的账号密码
        valid_users = {
            self.username: self.password
        }
        
        try:
            client_socket.sendall(b"220 Simple FTP Server Ready\r\n")
            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break
                    command = data.decode('utf-8', errors='ignore').strip()
                    if not command:
                        continue
                    upper_cmd = command.upper()
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['commands'] += 1
                    
                    # USER 命令
                    if upper_cmd.startswith('USER'):
                        parts = command.split(None, 1)
                        username = parts[1] if len(parts) > 1 else ''
                        client_socket.sendall(b"331 Username ok, need password\r\n")
                    
                    # PASS 命令
                    elif upper_cmd.startswith('PASS'):
                        parts = command.split(None, 1)
                        password = parts[1] if len(parts) > 1 else ''
                        if username and valid_users.get(username) == password:
                            logged_in = True
                            client_socket.sendall(b"230 Login successful\r\n")
                            add_service_log('FTP服务器', f'用户登录成功: {username}')
                        else:
                            client_socket.sendall(b"530 Login incorrect\r\n")
                            add_service_log('FTP服务器', f'登录失败: {username}')
                    
                    # SYST 命令
                    elif upper_cmd == 'SYST':
                        client_socket.sendall(b"215 UNIX Type: L8\r\n")
                    
                    # PWD 命令
                    elif upper_cmd == 'PWD':
                        client_socket.sendall(f'257 "{current_dir}" is current directory\r\n'.encode('utf-8'))

                    # CWD 命令（进入目录）
                    elif upper_cmd.startswith('CWD'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            parts = command.split(None, 1)
                            new_dir = parts[1] if len(parts) > 1 else '/'

                            # 路径规范化处理
                            if new_dir == '..':
                                # 返回上级目录
                                if current_dir == '/':
                                    # 已经在根目录，无法再返回上级
                                    client_socket.sendall(b"550 Cannot go up from root directory\r\n")
                                else:
                                    # 计算上级目录路径
                                    parent_dir = os.path.dirname(current_dir.rstrip('/'))
                                    if parent_dir == '' or parent_dir == '.':
                                        parent_dir = '/'
                                    current_dir = parent_dir
                                    client_socket.sendall(f'250 CWD successful. "{current_dir}" is current directory\r\n'.encode('utf-8'))
                            elif new_dir.startswith('/'):
                                # 绝对路径
                                current_dir = new_dir
                                client_socket.sendall(f'250 CWD successful. "{current_dir}" is current directory\r\n'.encode('utf-8'))
                            else:
                                # 相对路径，拼接当前目录
                                if current_dir == '/':
                                    current_dir = '/' + new_dir
                                else:
                                    current_dir = current_dir.rstrip('/') + '/' + new_dir
                                client_socket.sendall(f'250 CWD successful. "{current_dir}" is current directory\r\n'.encode('utf-8'))

                    # CDUP 命令（返回上级目录）
                    elif upper_cmd == 'CDUP':
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            if current_dir == '/':
                                # 已经在根目录，无法再返回上级
                                client_socket.sendall(b"550 Cannot go up from root directory\r\n")
                            else:
                                # 计算上级目录路径
                                parent_dir = os.path.dirname(current_dir.rstrip('/'))
                                if parent_dir == '' or parent_dir == '.':
                                    parent_dir = '/'
                                current_dir = parent_dir
                                client_socket.sendall(f'250 CDUP successful. "{current_dir}" is current directory\r\n'.encode('utf-8'))
                                add_service_log('FTP服务器', f'目录切换到: {current_dir}')

                    # MKD 命令（创建目录）
                    elif upper_cmd.startswith('MKD') or upper_cmd.startswith('XMKD'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            parts = command.split(None, 1)
                            dirname = parts[1] if len(parts) > 1 else ''
                            if not dirname:
                                client_socket.sendall(b"550 Missing directory name\r\n")
                            else:
                                try:
                                    # 构建目录路径
                                    if dirname.startswith('/'):
                                        dir_path = os.path.join(ftp_root, dirname.lstrip('/'))
                                    else:
                                        if current_dir == '/':
                                            dir_path = os.path.join(ftp_root, dirname)
                                        else:
                                            dir_path = os.path.join(ftp_root, current_dir.lstrip('/'), dirname)

                                    os.makedirs(dir_path, exist_ok=True)
                                    client_socket.sendall(f'257 "{dirname}" directory created\r\n'.encode('utf-8'))
                                    add_service_log('FTP服务器', f'创建目录: {dirname}')
                                except Exception as e:
                                    client_socket.sendall(b"550 Failed to create directory\r\n")
                                    add_service_log('FTP服务器', f'创建目录失败: {e}', 'error')

                    # RMD 命令（删除目录）
                    elif upper_cmd.startswith('RMD') or upper_cmd.startswith('XRMD'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            parts = command.split(None, 1)
                            dirname = parts[1] if len(parts) > 1 else ''
                            if not dirname:
                                client_socket.sendall(b"550 Missing directory name\r\n")
                            else:
                                try:
                                    # 构建目录路径
                                    if dirname.startswith('/'):
                                        dir_path = os.path.join(ftp_root, dirname.lstrip('/'))
                                    else:
                                        if current_dir == '/':
                                            dir_path = os.path.join(ftp_root, dirname)
                                        else:
                                            dir_path = os.path.join(ftp_root, current_dir.lstrip('/'), dirname)

                                    if os.path.exists(dir_path) and os.path.isdir(dir_path):
                                        os.rmdir(dir_path)
                                        client_socket.sendall(b"250 Directory removed\r\n")
                                        add_service_log('FTP服务器', f'删除目录: {dirname}')
                                    else:
                                        client_socket.sendall(b"550 Directory not found or not empty\r\n")
                                except Exception as e:
                                    client_socket.sendall(b"550 Failed to remove directory\r\n")
                                    add_service_log('FTP服务器', f'删除目录失败: {e}', 'error')

                    # DELE 命令（删除文件）
                    elif upper_cmd.startswith('DELE'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            parts = command.split(None, 1)
                            filename = parts[1] if len(parts) > 1 else ''
                            if not filename:
                                client_socket.sendall(b"550 Missing file name\r\n")
                            else:
                                try:
                                    # 构建文件路径
                                    if filename.startswith('/'):
                                        file_path = os.path.join(ftp_root, filename.lstrip('/'))
                                    else:
                                        if current_dir == '/':
                                            file_path = os.path.join(ftp_root, filename)
                                        else:
                                            file_path = os.path.join(ftp_root, current_dir.lstrip('/'), filename)

                                    if os.path.exists(file_path) and os.path.isfile(file_path):
                                        os.remove(file_path)
                                        client_socket.sendall(b"250 File deleted\r\n")
                                        add_service_log('FTP服务器', f'删除文件: {filename}')
                                    else:
                                        client_socket.sendall(b"550 File not found\r\n")
                                except Exception as e:
                                    client_socket.sendall(b"550 Failed to delete file\r\n")
                                    add_service_log('FTP服务器', f'删除文件失败: {e}', 'error')
                    
                    # LIST 命令
                    elif upper_cmd == 'LIST' or upper_cmd.startswith('LIST '):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            try:
                                # 获取实际目录路径
                                if current_dir == '/':
                                    list_path = ftp_root
                                else:
                                    list_path = os.path.join(ftp_root, current_dir.lstrip('/'))
                                
                                if not os.path.exists(list_path):
                                    client_socket.sendall(b"550 Directory not found\r\n")
                                else:
                                    client_socket.sendall(b"150 Opening ASCII mode data connection for file list\r\n")
                                    # 建立数据连接
                                    if pasv_socket:
                                        data_conn, _ = pasv_socket.accept()
                                        try:
                                            file_list = ""
                                            for item in os.listdir(list_path):
                                                item_path = os.path.join(list_path, item)
                                                try:
                                                    stat_info = os.stat(item_path)
                                                    if os.path.isdir(item_path):
                                                        file_list += f"drwxrwxrwx 1 user user {stat_info.st_size} {time.strftime('%b %d %H:%M', time.localtime(stat_info.st_mtime))} {item}\r\n"
                                                    else:
                                                        file_list += f"-rw-rw-rw- 1 user user {stat_info.st_size} {time.strftime('%b %d %H:%M', time.localtime(stat_info.st_mtime))} {item}\r\n"
                                                except:
                                                    pass
                                            data_conn.sendall(file_list.encode('utf-8'))
                                            data_conn.close()
                                        except Exception as e:
                                            add_service_log('FTP服务器', f'发送文件列表失败: {e}', 'error')
                                        finally:
                                            pasv_socket.close()
                                            pasv_socket = None
                                    client_socket.sendall(b"226 Transfer complete\r\n")
                                    add_service_log('FTP服务器', '文件列表已发送')
                            except Exception as e:
                                client_socket.sendall(b"550 Error listing directory\r\n")
                                add_service_log('FTP服务器', f'列表目录失败: {e}', 'error')
                    
                    # PASV 命令（被动模式）
                    elif upper_cmd == 'PASV':
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            try:
                                # 创建被动模式数据套接字
                                pasv_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                pasv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                                pasv_socket.bind((host, 0))  # 绑定到任意可用端口
                                pasv_socket.listen(1)
                                pasv_port = pasv_socket.getsockname()[1]
                                # 获取服务器IP地址
                                server_ip = host if host != '0.0.0.0' else '127.0.0.1'
                                # 转换为FTP PASV格式 (h1,h2,h3,h4,p1,p2)
                                ip_parts = server_ip.split('.')
                                p1 = pasv_port // 256
                                p2 = pasv_port % 256
                                pasv_response = f"227 Entering Passive Mode ({ip_parts[0]},{ip_parts[1]},{ip_parts[2]},{ip_parts[3]},{p1},{p2})\r\n"
                                client_socket.sendall(pasv_response.encode('utf-8'))
                                add_service_log('FTP服务器', f'被动模式已启用，端口: {pasv_port}')
                            except Exception as e:
                                client_socket.sendall(b"425 Can't open data connection\r\n")
                                add_service_log('FTP服务器', f'启用被动模式失败: {e}', 'error')
                    
                    # RETR 命令（下载文件）
                    elif upper_cmd.startswith('RETR'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            try:
                                parts = command.split(None, 1)
                                filename = parts[1] if len(parts) > 1 else ''
                                # 构建文件路径
                                if current_dir == '/':
                                    file_path = os.path.join(ftp_root, filename)
                                else:
                                    file_path = os.path.join(ftp_root, current_dir.lstrip('/'), filename)
                                
                                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                                    client_socket.sendall(b"550 File not found\r\n")
                                else:
                                    client_socket.sendall(b"150 Opening ASCII mode data connection\r\n")
                                    # 建立数据连接
                                    if pasv_socket:
                                        data_conn, _ = pasv_socket.accept()
                                        try:
                                            with open(file_path, 'rb') as f:
                                                while True:
                                                    chunk = f.read(8192)
                                                    if not chunk:
                                                        break
                                                    data_conn.sendall(chunk)
                                            data_conn.close()
                                        except Exception as e:
                                            add_service_log('FTP服务器', f'发送文件失败: {e}', 'error')
                                        finally:
                                            pasv_socket.close()
                                            pasv_socket = None
                                    client_socket.sendall(b"226 Transfer complete\r\n")
                                    add_service_log('FTP服务器', f'文件下载: {filename}')
                            except Exception as e:
                                client_socket.sendall(b"550 Error retrieving file\r\n")
                                add_service_log('FTP服务器', f'下载文件失败: {e}', 'error')
                    
                    # STOR 命令（上传文件）
                    elif upper_cmd.startswith('STOR'):
                        if not logged_in:
                            client_socket.sendall(b"530 Please login first\r\n")
                        else:
                            try:
                                parts = command.split(None, 1)
                                filename = parts[1] if len(parts) > 1 else ''
                                # 构建文件路径
                                if current_dir == '/':
                                    file_path = os.path.join(ftp_root, filename)
                                else:
                                    file_path = os.path.join(ftp_root, current_dir.lstrip('/'), filename)
                                
                                # 确保目录存在
                                os.makedirs(os.path.dirname(file_path) if os.path.dirname(file_path) else ftp_root, exist_ok=True)
                                
                                client_socket.sendall(b"150 Ok to send data\r\n")
                                # 建立数据连接
                                if pasv_socket:
                                    data_conn, _ = pasv_socket.accept()
                                    try:
                                        with open(file_path, 'wb') as f:
                                            while True:
                                                chunk = data_conn.recv(8192)
                                                if not chunk:
                                                    break
                                                f.write(chunk)
                                        # 设置文件权限
                                        try:
                                            os.chmod(file_path, 0o666)
                                        except:
                                            pass
                                    except Exception as e:
                                        add_service_log('FTP服务器', f'接收文件失败: {e}', 'error')
                                    finally:
                                        pasv_socket.close()
                                        pasv_socket = None
                                client_socket.sendall(b"226 Transfer complete\r\n")
                                add_service_log('FTP服务器', f'文件上传: {filename}')
                            except Exception as e:
                                client_socket.sendall(b"550 Error storing file\r\n")
                                add_service_log('FTP服务器', f'上传文件失败: {e}', 'error')
                    
                    # QUIT 命令
                    elif upper_cmd == 'QUIT':
                        client_socket.sendall(b"221 Goodbye\r\n")
                        break
                    
                    # TYPE 命令
                    elif upper_cmd.startswith('TYPE'):
                        client_socket.sendall(b"200 Type set to A\r\n")
                    
                    # MODE 命令
                    elif upper_cmd.startswith('MODE'):
                        client_socket.sendall(b"200 Mode set to S\r\n")
                    
                    # 其他命令
                    else:
                        client_socket.sendall(b"502 Command not implemented\r\n")
                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('FTP服务器', f'命令处理异常: {e}', 'error')
                    break
        finally:
            try:
                client_socket.close()
                if data_socket:
                    data_socket.close()
                if pasv_socket:
                    pasv_socket.close()
            except:
                pass
            # 不再清理目录，因为使用的是配置的目录
            with service_lock:
                conn_info = self.state['connections'].pop(conn_id, None)
            if conn_info:
                add_service_log('FTP服务器', f"客户端断开: {conn_info['address']}")

    def stop(self):
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass


class TCPClientManager:
    def __init__(self, state, config):
        self.state = state
        self.server_ip = config['server_ip']
        self.server_port = config['server_port']
        self.connection_target = config['connections']
        self.connect_rate = max(config.get('connect_rate', 1), 0.1)
        self.message = config.get('message', '')
        self.interval = max(config.get('send_interval', 1), 0.1)
        # MAC地址配置（用于日志记录，实际socket连接不使用）
        self.src_mac = config.get('src_mac', '')
        self.dst_mac = config.get('dst_mac', '')
        # 本地地址绑定
        self.use_local_address = config.get('use_local_address', False)
        self.local_address = config.get('local_address', '')
        self.stop_event = threading.Event()
        self.send_stop_event = threading.Event()  # 用于停止发送但保持连接
        self.connection_context = {}
        self.threads = []
        self.send_threads = []

    def connect(self):
        """只建立连接，不发送数据"""
        add_service_log('TCP客户端', f"准备连接 {self.server_ip}:{self.server_port}，连接数: {self.connection_target}")
        self.send_stop_event.clear()  # 重置发送停止标志
        for _ in range(self.connection_target):
            if self.stop_event.is_set():
                break
            conn_id = str(uuid.uuid4())
            conn_stop = threading.Event()
            self.connection_context[conn_id] = {
                'stop_event': conn_stop,
                'socket': None,
                'connected': False
            }
            with service_lock:
                self.state['connections'][conn_id] = {
                    'id': conn_id,
                    'address': f"{self.server_ip}:{self.server_port}",
                    'bytes_sent': 0,
                    'status': 'connecting'
                }
            thread = threading.Thread(
                target=self._run_connection,
                args=(conn_id, conn_stop),
                daemon=True
            )
            self.threads.append(thread)
            thread.start()
            time.sleep(max(0.01, 1.0 / self.connect_rate))

    def start_send(self):
        """开始发送数据（需要先连接）"""
        if not self.connection_context:
            return False, '请先建立连接'
        self.send_stop_event.clear()
        payload = self.message.encode('utf-8')
        if not payload:
            return False, '发送内容为空'
        add_service_log('TCP客户端', '开始发送数据')
        for conn_id, context in self.connection_context.items():
            if context.get('connected') and context.get('socket'):
                thread = threading.Thread(
                    target=self._run_send,
                    args=(conn_id, payload),
                    daemon=True
                )
                self.send_threads.append(thread)
                thread.start()
        return True, '开始发送'

    def stop_send(self):
        """停止发送数据（保持连接）"""
        self.send_stop_event.set()
        for thread in self.send_threads:
            thread.join(timeout=0.5)
        self.send_threads.clear()
        add_service_log('TCP客户端', '已停止发送数据')
        return True, '已停止发送'

    def _run_connection(self, conn_id, conn_stop):
        """建立连接"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            
            # 如果配置了使用本地地址，绑定到指定的本地IP
            if self.use_local_address and self.local_address:
                try:
                    sock.bind((self.local_address, 0))  # 0表示让系统自动分配端口
                    add_service_log('TCP客户端', f'绑定本地地址: {self.local_address}', 'info')
                except Exception as e:
                    add_service_log('TCP客户端', f'绑定本地地址失败: {e}', 'warning')
            
            # 记录MAC地址信息（如果配置了）
            mac_info = ''
            if self.src_mac:
                mac_info += f'源MAC: {self.src_mac}'
            if self.dst_mac:
                if mac_info:
                    mac_info += ', '
                mac_info += f'目的MAC: {self.dst_mac}'
            if mac_info:
                add_service_log('TCP客户端', f'MAC地址配置: {mac_info}', 'info')
            
            sock.connect((self.server_ip, self.server_port))
            sock.settimeout(10)  # 连接后设置较长的超时
            self.connection_context[conn_id]['socket'] = sock
            self.connection_context[conn_id]['connected'] = True
            with service_lock:
                if conn_id in self.state['connections']:
                    self.state['connections'][conn_id]['status'] = 'connected'
            add_service_log('TCP客户端', f'连接成功: {self.server_ip}:{self.server_port} (ID: {conn_id[:8]})')
            # 保持连接，等待发送命令
            while not self.stop_event.is_set() and not conn_stop.is_set():
                time.sleep(0.5)
        except Exception as e:
            add_service_log('TCP客户端', f'连接异常: {e}', 'error')
        finally:
            if sock:
                try:
                    sock.close()
                except:
                    pass
            with service_lock:
                conn_info = self.state['connections'].get(conn_id)
                if conn_info:
                    conn_info['status'] = 'closed'
            self.connection_context.pop(conn_id, None)

    def _run_send(self, conn_id, payload):
        """发送数据"""
        context = self.connection_context.get(conn_id)
        if not context or not context.get('socket'):
            return
        sock = context['socket']
        try:
            while not self.send_stop_event.is_set() and not self.stop_event.is_set():
                try:
                    sock.sendall(payload)
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['bytes_sent'] += len(payload)
                    time.sleep(self.interval)
                except (socket.error, OSError) as e:
                    add_service_log('TCP客户端', f'发送数据异常: {e}', 'error')
                    break
        except Exception as e:
            add_service_log('TCP客户端', f'发送线程异常: {e}', 'error')

    def start(self):
        """兼容旧接口：连接并立即开始发送"""
        self.connect()
        time.sleep(0.5)  # 等待连接建立
        self.start_send()

    def stop(self):
        """停止所有连接"""
        self.stop_event.set()
        self.send_stop_event.set()
        for context in self.connection_context.values():
            context['stop_event'].set()
            sock = context.get('socket')
            if sock:
                try:
                    sock.close()
                except:
                    pass
        for thread in self.threads:
            thread.join(timeout=1)
        for thread in self.send_threads:
            thread.join(timeout=0.5)
        self.send_threads.clear()
        with service_lock:
            self.state['running'] = False
            self.state['connections'].clear()
        add_service_log('TCP客户端', '已停止所有连接')

    def disconnect(self, conn_id=None):
        """断开连接"""
        if conn_id:
            # 断开指定连接
            context = self.connection_context.get(conn_id)
            if not context:
                return False, '连接不存在或已关闭'
            context['stop_event'].set()
            sock = context.get('socket')
            if sock:
                try:
                    sock.close()
                except:
                    pass
            add_service_log('TCP客户端', f'连接已断开: {conn_id}')
            return True, '连接已断开'
        else:
            # 断开所有连接
            self.stop()
            return True, '所有连接已断开'


class UDPClientManager(threading.Thread):
    def __init__(self, state, config):
        super().__init__(daemon=True)
        self.state = state
        self.server_ip = config['server_ip']
        self.server_port = config['server_port']
        self.connection_target = config['connections']
        self.interval = max(config.get('send_interval', 1), 0.1)
        self.message = config.get('message', '').encode('utf-8')
        self.stop_event = threading.Event()

    def run(self):
        add_service_log('UDP客户端', f"开始发送到 {self.server_ip}:{self.server_port}")
        threads = []
        for _ in range(self.connection_target):
            conn_id = str(uuid.uuid4())
            worker = threading.Thread(
                target=self._sender_loop,
                args=(conn_id,),
                daemon=True
            )
            threads.append(worker)
            with service_lock:
                self.state['connections'][conn_id] = {
                    'id': conn_id,
                    'address': f"{self.server_ip}:{self.server_port}",
                    'bytes_sent': 0
                }
            worker.start()
        for worker in threads:
            worker.join()
        with service_lock:
            self.state['running'] = False
            self.state['connections'].clear()
        add_service_log('UDP客户端', '发送已结束')

    def _sender_loop(self, conn_id):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            while not self.stop_event.is_set():
                if self.message:
                    sock.sendto(self.message, (self.server_ip, self.server_port))
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['bytes_sent'] += len(self.message)
                time.sleep(self.interval)
        finally:
            sock.close()

    def stop(self):
        self.stop_event.set()


class FTPClientWorker:
    def __init__(self, state, config):
        self.state = state
        self.server_ip = config['server_ip']
        self.server_port = config['server_port']
        self.username = config.get('username', 'tdhx')
        self.password = config.get('password', 'tdhx@2017')
        self.stop_event = threading.Event()
        self.ftp = None
        self.connected = False

    def connect(self):
        """连接FTP服务器（使用被动模式）"""
        try:
            self.ftp = ftplib.FTP()
            # 优化连接：使用较短的超时时间，快速失败
            # 先尝试快速连接（3秒）
            try:
                self.ftp.connect(self.server_ip, self.server_port, timeout=3)
            except:
                # 如果快速连接失败，尝试中等超时（8秒）
                try:
                    self.ftp.connect(self.server_ip, self.server_port, timeout=8)
                except:
                    # 最后尝试较长超时（12秒）
                    self.ftp.connect(self.server_ip, self.server_port, timeout=12)
            
            # 设置socket超时，避免在登录时超时
            if self.ftp.sock:
                self.ftp.sock.settimeout(5)  # 登录阶段使用短超时
            
            # 登录（这是最可能超时的步骤）
            self.ftp.login(self.username, self.password)
            
            # 设置被动模式（在登录成功后）
            self.ftp.set_pasv(True)
            
            # 登录成功后，设置更长的超时用于数据传输
            if self.ftp.sock:
                self.ftp.sock.settimeout(30)
            
            # 获取当前目录（测试连接是否正常）
            try:
                current_dir = self.ftp.pwd()
            except:
                current_dir = '/'
            
            self.connected = True
            with service_lock:
                self.state['running'] = True
                self.state['current_dir'] = current_dir
            add_service_log('FTP客户端', f'已连接 {self.server_ip}:{self.server_port} (被动模式)')
            return True, '连接成功'
        except Exception as e:
            add_service_log('FTP客户端', f'连接失败: {e}', 'error')
            # 清理连接
            try:
                if self.ftp:
                    self.ftp.close()
            except:
                pass
            self.ftp = None
            self.connected = False
            return False, str(e)

    def disconnect(self):
        """断开FTP连接"""
        self.stop_event.set()
        try:
            if self.ftp and self.connected:
                self.ftp.quit()
                add_service_log('FTP客户端', '连接已关闭')
        except:
            try:
                if self.ftp:
                    self.ftp.close()
            except:
                pass
        finally:
            self.ftp = None
            self.connected = False
            with service_lock:
                self.state['running'] = False
        return True, '已断开连接'

    def list_files(self):
        """获取文件列表"""
        if not self.connected or not self.ftp:
            return False, '未连接'
        try:
            # 重新设置socket超时，确保数据传输有足够时间
            if self.ftp.sock:
                self.ftp.sock.settimeout(30)

            raw_lines = []
            # 使用retrlines获取文件列表，设置超时
            self.ftp.retrlines('LIST', raw_lines.append)

            # 解析FTP LIST输出为结构化数据
            parsed_files = self._parse_ftp_list(raw_lines)
            file_list_str = '\n'.join(raw_lines)  # 保留原始格式用于日志

            # 获取当前目录
            try:
                current_dir = self.ftp.pwd()
            except:
                current_dir = self.state.get('current_dir', '/')

            with service_lock:
                self.state['file_list'] = file_list_str
                self.state['current_dir'] = current_dir
            add_service_log('FTP客户端', f'文件列表已获取 ({len(parsed_files)} 项)')
            return True, {'files': parsed_files, 'current_dir': current_dir}
        except Exception as e:
            add_service_log('FTP客户端', f'获取文件列表失败: {e}', 'error')
            # 如果获取文件列表失败，检查连接是否仍然有效
            try:
                if self.ftp and self.ftp.sock:
                    # 尝试发送NOOP命令测试连接
                    self.ftp.voidcmd('NOOP')
            except:
                # 连接可能已断开，标记为未连接
                self.connected = False
                with service_lock:
                    self.state['running'] = False
            return False, str(e)

    def _parse_ftp_list(self, lines):
        """解析FTP LIST输出为结构化数据

        FTP LIST 格式示例:
        - drwxrwxrwx   1 user     group          0 Jan 01 00:00 dirname
        - -rwxrwxrwx   1 user     group       1234 Jan 01 00:00 filename
        - lrwxrwxrwx   1 user     group         10 Jan 01 00:00 linkname -> target

        返回: [{'name': 'dirname', 'is_dir': True, 'size': 0, 'modified': 'Jan 01 00:00'}, ...]
        """
        parsed = []
        for line in lines:
            if not line.strip():
                continue
            try:
                parts = line.strip().split()
                if len(parts) < 9:
                    # 非标准格式，尝试简单解析
                    # 至少需要: permissions, links, owner, group, size, date(3 parts), name
                    continue

                # 第一个字符判断类型
                first_char = parts[0][0] if parts[0] else '-'
                is_dir = first_char == 'd'
                is_link = first_char == 'l'

                # 尝试解析大小（第5个字段，索引4）
                try:
                    size = int(parts[4])
                except (ValueError, IndexError):
                    size = 0

                # 日期时间（第6-8个字段，索引5-7）
                date_str = ' '.join(parts[5:8]) if len(parts) >= 8 else ''

                # 文件名（第9个字段开始，索引8）
                name_parts = parts[8:]
                if is_link and '->' in name_parts:
                    # 符号链接，去掉 -> target 部分
                    arrow_idx = name_parts.index('->')
                    name = ' '.join(name_parts[:arrow_idx])
                else:
                    name = ' '.join(name_parts)

                if name:
                    parsed.append({
                        'name': name,
                        'is_dir': is_dir,
                        'size': size,
                        'modified': date_str
                    })
            except Exception as e:
                add_service_log('FTP客户端', f'解析行失败: {line[:50]}... - {e}', 'warning')
                continue

        return parsed

    def upload_file(self, filename, content):
        """上传文件"""
        if not self.connected or not self.ftp:
            return False, '未连接'
        try:
            # 重新设置socket超时，确保数据传输有足够时间
            if self.ftp.sock:
                self.ftp.sock.settimeout(30)
            
            from io import BytesIO
            # 支持二进制和文本内容
            if isinstance(content, bytes):
                bio = BytesIO(content)
                self.ftp.storbinary(f'STOR {filename}', bio)
            else:
                bio = BytesIO(content.encode('utf-8'))
                self.ftp.storlines(f'STOR {filename}', bio)
            add_service_log('FTP客户端', f'上传完成: {filename}')
            # 上传成功后自动刷新文件列表（在后台线程中，避免阻塞）
            import threading
            import time
            def refresh_list():
                try:
                    time.sleep(0.5)  # 稍微延迟，确保上传完成
                    if self.connected and self.ftp:
                        self.list_files()
                except:
                    pass
            threading.Thread(target=refresh_list, daemon=True).start()
            return True, f'上传成功: {filename}'
        except Exception as e:
            add_service_log('FTP客户端', f'上传失败: {e}', 'error')
            # 检查连接是否断开
            try:
                if self.ftp and self.ftp.sock:
                    self.ftp.voidcmd('NOOP')
            except:
                self.connected = False
                with service_lock:
                    self.state['running'] = False
            return False, str(e)

    def download_file(self, filename):
        """下载文件（不返回内容，只返回文件信息）"""
        if not self.connected or not self.ftp:
            return False, '未连接'
        try:
            # 重新设置socket超时，确保数据传输有足够时间
            if self.ftp.sock:
                self.ftp.sock.settimeout(30)
            
            # 获取文件大小
            try:
                file_size = self.ftp.size(filename)
            except:
                file_size = None
            
            # 执行下载但不读取内容
            try:
                # 使用retrbinary下载但不保存内容
                self.ftp.retrbinary(f'RETR {filename}', lambda data: None)
                add_service_log('FTP客户端', f'下载完成: {filename} (大小: {file_size or "未知"} 字节)')
                return True, {'filename': filename, 'file_size': file_size}
            except Exception as e:
                add_service_log('FTP客户端', f'下载失败: {e}', 'error')
                return False, str(e)
        except Exception as e:
            add_service_log('FTP客户端', f'下载失败: {e}', 'error')
            # 检查连接是否断开
            try:
                if self.ftp and self.ftp.sock:
                    self.ftp.voidcmd('NOOP')
            except:
                self.connected = False
                with service_lock:
                    self.state['running'] = False
            return False, str(e)

    def cd_dir(self, dirname):
        """切换目录"""
        if not self.connected or not self.ftp:
            return False, '未连接'
        try:
            # 重新设置socket超时
            if self.ftp.sock:
                self.ftp.sock.settimeout(30)

            # 处理特殊目录名
            if dirname == '..':
                # 返回上级目录
                try:
                    self.ftp.cwd('..')
                except Exception as e:
                    add_service_log('FTP客户端', f'返回上级目录失败: {e}', 'error')
                    return False, str(e)
            elif dirname == '/' or dirname == '':
                # 返回根目录
                try:
                    self.ftp.cwd('/')
                except Exception as e:
                    add_service_log('FTP客户端', f'返回根目录失败: {e}', 'error')
                    return False, str(e)
            else:
                # 进入指定目录
                try:
                    self.ftp.cwd(dirname)
                except Exception as e:
                    add_service_log('FTP客户端', f'进入目录失败: {e}', 'error')
                    return False, str(e)

            # 获取当前目录
            try:
                current_dir = self.ftp.pwd()
            except:
                current_dir = dirname

            with service_lock:
                self.state['current_dir'] = current_dir
            add_service_log('FTP客户端', f'目录切换成功: {current_dir}')
            return True, {'current_dir': current_dir}
        except Exception as e:
            add_service_log('FTP客户端', f'切换目录失败: {e}', 'error')
            # 检查连接是否断开
            try:
                if self.ftp and self.ftp.sock:
                    self.ftp.voidcmd('NOOP')
            except:
                self.connected = False
                with service_lock:
                    self.state['running'] = False
            return False, str(e)

    def stop(self):
        """停止（兼容旧接口）"""
        return self.disconnect()


class HTTPListenerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.stop_event = threading.Event()
        self.server_socket = None
        import os
        script_dir = os.path.dirname(os.path.abspath(__file__))
        directory = state.get('directory', '')
        if directory:
            if not os.path.isabs(directory):
                directory = os.path.abspath(directory)
            self.http_root = directory
        else:
            # 默认使用脚本目录下的http目录
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.http_root = os.path.join(script_dir, 'http')
            print(f"[DEBUG] 脚本目录: {script_dir}")
            print(f"[DEBUG] HTTP根目录将设置为: {self.http_root}")
        # 确保目录存在
        os.makedirs(self.http_root, exist_ok=True)
        print(f"[DEBUG] HTTP监听器初始化完成，根目录: {self.http_root}")
        print(f"[DEBUG] 目录是否存在: {os.path.exists(self.http_root)}")
        if os.path.exists(self.http_root):
            files = os.listdir(self.http_root)
            print(f"[DEBUG] 目录中的文件: {files}")
        add_service_log('HTTP服务器', f'HTTP根目录设置为: {self.http_root}', 'info')

    def run(self):
        host = self.state['host']
        port = self.state['port']
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((host, port))
            self.server_socket.listen(100)
            self.server_socket.settimeout(1.0)
            add_service_log('HTTP服务器', f'服务器启动: {host}:{port}，目录: {self.http_root}')
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.server_socket.accept()
                    client_socket.settimeout(10.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'requests': 0
                        }
                    threading.Thread(
                        target=self._handle_request,
                        args=(client_socket, conn_id, addr),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.stop_event.is_set():
                        add_service_log('HTTP服务器', '监听套接字异常', 'error')
                    break
                except Exception as e:
                    add_service_log('HTTP服务器', f'接收连接异常: {e}', 'error')
            add_service_log('HTTP服务器', f'服务器停止: {host}:{port}')
        except Exception as e:
            add_service_log('HTTP服务器', f'启动失败: {e}', 'error')
        finally:
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
            with service_lock:
                self.state['running'] = False

    def _handle_request(self, client_socket, conn_id, addr):
        import os
        import urllib.parse
        try:
            # 接收HTTP请求
            request_data = client_socket.recv(4096)
            if not request_data:
                return
            
            request_str = request_data.decode('utf-8', errors='ignore')
            lines = request_str.split('\r\n')
            if not lines:
                return
            
            # 解析请求行
            request_line = lines[0]
            parts = request_line.split()
            if len(parts) < 2:
                return
            
            method = parts[0]
            path = parts[1]
            
            # 解析路径
            parsed_path = urllib.parse.urlparse(path)
            file_path = parsed_path.path
            
            # 更新连接统计
            with service_lock:
                if conn_id in self.state['connections']:
                    self.state['connections'][conn_id]['requests'] += 1
            
            add_service_log('HTTP服务器', f'{method} {path} from {addr[0]}:{addr[1]}')
            
            # 处理请求
            if method == 'GET':
                # 如果是根路径，显示文件列表
                if file_path == '/' or file_path == '':
                    self._send_file_list(client_socket)
                else:
                    # 尝试发送文件
                    self._send_file(client_socket, file_path)
            elif method == 'POST':
                # 处理文件上传
                self._receive_upload(client_socket, file_path, request_data)
            else:
                # 不支持的方法
                self._send_error(client_socket, 405, 'Method Not Allowed')
        except Exception as e:
            add_service_log('HTTP服务器', f'处理请求异常: {e}', 'error')
            try:
                self._send_error(client_socket, 500, 'Internal Server Error')
            except:
                pass
        finally:
            try:
                # 优雅关闭 socket，确保响应完全发送
                client_socket.shutdown(socket.SHUT_WR)
            except:
                pass
            try:
                client_socket.close()
            except:
                pass
            # 从连接列表中移除
            with service_lock:
                if conn_id in self.state['connections']:
                    del self.state['connections'][conn_id]

    def _send_file_list(self, client_socket):
        import os
        try:
            files = []
            print(f"[DEBUG] HTTP服务器根目录: {self.http_root}")
            add_service_log('HTTP服务器', f'访问目录: {self.http_root}', 'info')
            
            if os.path.exists(self.http_root):
                print(f"[DEBUG] 目录存在，开始扫描文件")
                for item in os.listdir(self.http_root):
                    item_path = os.path.join(self.http_root, item)
                    is_dir = os.path.isdir(item_path)
                    size = os.path.getsize(item_path) if not is_dir else 0
                    size_str = self._format_size(size) if not is_dir else '-'
                    files.append({
                        'name': item,
                        'is_dir': is_dir,
                        'size': size
                    })
                    print(f"[DEBUG] 发现文件: {item}, 是目录: {is_dir}, 原始大小: {size} 字节, 格式化大小: {size_str}")
                    print(f"[DEBUG] 文件路径: {item_path}")
                    
                    # 验证文件大小
                    if not is_dir and os.path.exists(item_path):
                        try:
                            with open(item_path, 'rb') as f:
                                actual_content = f.read()
                                actual_size = len(actual_content)
                                print(f"[DEBUG] 实际读取大小: {actual_size} 字节")
                                if actual_size != size:
                                    print(f"[WARNING] 大小不匹配! os.path.getsize={size}, 实际读取={actual_size}")
                        except Exception as e:
                            print(f"[DEBUG] 读取文件失败: {e}")
                print(f"[DEBUG] 总共找到 {len(files)} 个文件/目录")
                add_service_log('HTTP服务器', f'找到 {len(files)} 个文件/目录', 'info')
            else:
                print(f"[DEBUG] 目录不存在: {self.http_root}")
                add_service_log('HTTP服务器', f'目录不存在: {self.http_root}', 'error')
            
            # 生成HTML文件列表
            html = '<!DOCTYPE html><html><head><meta charset="utf-8"><title>文件列表</title>'
            html += '<style>body{font-family:Arial;margin:20px;}table{border-collapse:collapse;width:100%;}'
            html += 'th,td{border:1px solid #ddd;padding:8px;text-align:left;}th{background-color:#4CAF50;color:white;}'
            html += 'a{text-decoration:none;color:#0066cc;}a:hover{text-decoration:underline;}</style></head><body>'
            html += '<h1>文件列表</h1><table><tr><th>名称</th><th>类型</th><th>大小</th></tr>'
            
            for file_info in files:
                name = file_info['name']
                is_dir = file_info['is_dir']
                size = file_info['size']
                if is_dir:
                    html += f'<tr><td><a href="/{name}/">{name}/</a></td><td>目录</td><td>-</td></tr>'
                else:
                    size_str = self._format_size(size)
                    html += f'<tr><td><a href="/{name}">{name}</a></td><td>文件</td><td>{size_str}</td></tr>'
            
            html += '</table></body></html>'
            
            response = f"HTTP/1.1 200 OK\r\n"
            response += f"Content-Type: text/html; charset=utf-8\r\n"
            response += f"Content-Length: {len(html.encode('utf-8'))}\r\n"
            response += f"Connection: close\r\n\r\n"
            response += html
            
            client_socket.sendall(response.encode('utf-8'))
        except Exception as e:
            add_service_log('HTTP服务器', f'发送文件列表失败: {e}', 'error')
            self._send_error(client_socket, 500, 'Internal Server Error')

    def _send_file(self, client_socket, file_path):
        import os
        from urllib.parse import unquote
        try:
            # 移除开头的/
            if file_path.startswith('/'):
                file_path = file_path[1:]

            # URL解码文件路径，处理空格和中文字符
            file_path = unquote(file_path)

            # 构建完整路径
            full_path = os.path.join(self.http_root, file_path)
            
            # 安全检查：确保文件在http_root目录下
            full_path = os.path.normpath(full_path)
            if not full_path.startswith(os.path.normpath(self.http_root)):
                self._send_error(client_socket, 403, 'Forbidden')
                return
            
            if not os.path.exists(full_path) or os.path.isdir(full_path):
                self._send_error(client_socket, 404, 'Not Found')
                return
            
            # 读取文件
            with open(full_path, 'rb') as f:
                file_data = f.read()
            
            # 确定Content-Type
            content_type = self._get_content_type(full_path)
            
            # 发送响应
            response = f"HTTP/1.1 200 OK\r\n"
            response += f"Content-Type: {content_type}\r\n"
            response += f"Content-Length: {len(file_data)}\r\n"
            response += f"Connection: close\r\n\r\n"
            
            client_socket.sendall(response.encode('utf-8'))
            client_socket.sendall(file_data)
            
            add_service_log('HTTP服务器', f'发送文件: {file_path} ({len(file_data)} 字节)')
        except Exception as e:
            add_service_log('HTTP服务器', f'发送文件失败: {e}', 'error')
            self._send_error(client_socket, 500, 'Internal Server Error')

    def _send_error(self, client_socket, code, message):
        try:
            error_html = f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{code} {message}</title></head>'
            error_html += f'<body><h1>{code} {message}</h1></body></html>'
            response = f"HTTP/1.1 {code} {message}\r\n"
            response += f"Content-Type: text/html; charset=utf-8\r\n"
            response += f"Content-Length: {len(error_html.encode('utf-8'))}\r\n"
            response += f"Connection: close\r\n\r\n"
            response += error_html
            client_socket.sendall(response.encode('utf-8'))
        except:
            pass

    def _get_content_type(self, file_path):
        import mimetypes
        content_type, _ = mimetypes.guess_type(file_path)
        return content_type or 'application/octet-stream'

    def _format_size(self, size):
        if size < 1024:
            return f'{size} B'
        elif size < 1024 * 1024:
            return f'{size / 1024:.2f} KB'
        else:
            return f'{size / (1024 * 1024):.2f} MB'

    def _receive_upload(self, client_socket, file_path, initial_data):
        """接收上传的文件（POST请求）"""
        import os
        from urllib.parse import unquote
        try:
            # 移除开头的/
            if file_path.startswith('/'):
                file_path = file_path[1:]

            # URL解码文件名，还原空格和中文字符
            file_path = unquote(file_path)

            # 如果file_path为空，从请求中获取文件名
            if not file_path:
                file_path = 'uploaded_file'

            # 构建完整路径
            full_path = os.path.join(self.http_root, file_path)

            # 安全检查：确保文件在http_root目录下
            full_path = os.path.normpath(full_path)
            if not full_path.startswith(os.path.normpath(self.http_root)):
                self._send_error(client_socket, 403, 'Forbidden')
                return

            # 解析请求头获取Content-Length
            request_str = initial_data.decode('utf-8', errors='ignore')
            headers_end = request_str.find('\r\n\r\n')
            if headers_end == -1:
                self._send_error(client_socket, 400, 'Bad Request')
                return

            headers = request_str[:headers_end]
            content_length = 0

            # 解析Content-Length
            for line in headers.split('\r\n'):
                if line.lower().startswith('content-length:'):
                    try:
                        content_length = int(line.split(':')[1].strip())
                    except:
                        pass
                    break

            # 获取已接收的body部分
            body_start = headers_end + 4
            body_received = len(initial_data) - body_start

            # 读取剩余数据
            file_data = initial_data[body_start:]
            remaining = content_length - body_received

            while remaining > 0:
                chunk = client_socket.recv(min(4096, remaining))
                if not chunk:
                    break
                file_data += chunk
                remaining -= len(chunk)

            # 写入文件
            with open(full_path, 'wb') as f:
                f.write(file_data)

            add_service_log('HTTP服务器', f'接收上传文件: {file_path} ({len(file_data)} 字节)')

            # 发送成功响应
            response_body = "Upload successful"
            response_body_bytes = response_body.encode('utf-8')
            response = f"HTTP/1.1 200 OK\r\n"
            response += f"Content-Type: text/plain; charset=utf-8\r\n"
            response += f"Content-Length: {len(response_body_bytes)}\r\n"
            response += f"Connection: close\r\n\r\n"

            client_socket.sendall(response.encode('utf-8'))
            client_socket.sendall(response_body_bytes)
        except Exception as e:
            add_service_log('HTTP服务器', f'接收上传失败: {e}', 'error')
            self._send_error(client_socket, 500, 'Internal Server Error')

    def stop(self):
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass


class MailListenerThread(threading.Thread):
    def __init__(self, state):
        super().__init__(daemon=True)
        self.state = state
        self.stop_event = threading.Event()
        self.smtp_server = None
        self.imap_server = None
        self.pop3_server = None
        self.smtp_port = state.get('smtp_port', 25)
        self.imap_port = state.get('imap_port', 143)
        self.pop3_port = state.get('pop3_port', 110)
        self.domain = state.get('domain', 'test.local')
        self.accounts = state.get('accounts', [])
        self.smtp_socket = None  # SMTP socket
        self.imap_socket = None  # IMAP socket
        self.pop3_socket = None  # POP3 socket

        # 邮件存储配置 - 使用脚本所在目录，确保路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.mail_storage_dir = os.path.join(script_dir, 'mail_storage')
        self.accounts_file = os.path.join(self.mail_storage_dir, 'accounts.json')
        self.db_path = os.path.join(self.mail_storage_dir, 'mails.db')
        add_service_log('邮件服务器', f'邮件存储目录: {self.mail_storage_dir}')
        self._init_mail_storage()
        self._init_db()

        add_service_log('邮件服务器', f'初始化邮件服务器，域名: {self.domain}')
        add_service_log('邮件服务器', f'SMTP: {self.smtp_port}, IMAP: {self.imap_port}, POP3: {self.pop3_port}')
        add_service_log('邮件服务器', f'接收到的账户数量: {len(self.accounts)}')

        # Init connections
        if "connections" not in self.state:
            self.state["connections"] = {}

        for i, account in enumerate(self.accounts):
            username = account.get('username', 'N/A')
            email = account.get('email', 'N/A')
            password_len = len(account.get('password', ''))
            log_msg = f'账户{i}: 用户名="{username}", 邮箱="{email}", 密码长度={password_len}'
            add_service_log('邮件服务器', log_msg)
        
        # 如果没有接收到账户，创建一个默认测试账户
        if len(self.accounts) == 0:
            msg = '*** 没有接收到账户，创建默认测试账户 ***'
            add_service_log('邮件服务器', msg)
            print(msg)
            self._create_default_test_account()
        add_service_log('邮件服务器', f'邮件存储目录: {self.mail_storage_dir}')
    
    def _init_mail_storage(self):
        """初始化邮件存储目录和账户文件"""
        try:
            # 创建邮件存储目录
            os.makedirs(self.mail_storage_dir, exist_ok=True)
            
            # 创建用户邮箱目录
            for account in self.accounts:
                username = account.get('username', '')
                if username:
                    user_mail_dir = os.path.join(self.mail_storage_dir, username)
                    os.makedirs(user_mail_dir, exist_ok=True)
                    # 创建收件箱和发件箱
                    os.makedirs(os.path.join(user_mail_dir, 'INBOX'), exist_ok=True)
                    os.makedirs(os.path.join(user_mail_dir, 'SENT'), exist_ok=True)
                    os.makedirs(os.path.join(user_mail_dir, 'DRAFTS'), exist_ok=True)
            
            # 初始化账户文件
            if not os.path.exists(self.accounts_file):
                accounts_data = {}
                for account in self.accounts:
                    username = account.get('username', '')
                    password = account.get('password', '')
                    if username and password:
                        accounts_data[username] = {
                            'password': password,
                            'email': f"{username}@{self.domain}",
                            'created': time.strftime('%Y-%m-%d %H:%M:%S')
                        }
                
                with open(self.accounts_file, 'w', encoding='utf-8') as f:
                    json.dump(accounts_data, f, indent=2, ensure_ascii=False)
                
                add_service_log('邮件服务器', f'创建账户文件: {len(accounts_data)} 个账户')
        except Exception as e:
            add_service_log('邮件服务器', f'初始化邮件存储失败: {str(e)}', 'error')
    

    def _init_db(self):
        """初始化 SQLite 数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # 创建邮件表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mails (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mail_from TEXT NOT NULL,
                    mail_to TEXT NOT NULL,
                    subject TEXT,
                    body TEXT,
                    raw_content BLOB,
                    has_attachment BOOLEAN DEFAULT 0,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_received_at ON mails(received_at DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_mail_to ON mails(mail_to)')

            conn.commit()
            conn.close()
            add_service_log('邮件服务器', f'SQLite 数据库初始化成功：{self.db_path}')
        except Exception as e:
            add_service_log('邮件服务器', f'初始化数据库失败：{str(e)}', 'error')

    def _save_mail_to_db(self, mail_from, mail_to, subject, body, raw_content=None, has_attachment=False):
        """保存邮件到 SQLite 数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO mails (mail_from, mail_to, subject, body, raw_content, has_attachment)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (mail_from, mail_to, subject, body, raw_content, has_attachment))

            conn.commit()
            mail_id = cursor.lastrowid
            conn.close()

            add_service_log('邮件服务器', f'邮件已保存到数据库：ID={mail_id}, {mail_from} -> {mail_to}')
            return mail_id
        except Exception as e:
            add_service_log('邮件服务器', f'保存邮件到数据库失败：{str(e)}', 'error')
            return None

    def get_recent_mails(self, limit=10):
        """获取最近 limit 封邮件"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM mails
                ORDER BY received_at DESC
                LIMIT ?
            """, (limit,))

            rows = cursor.fetchall()
            mails = []
            for row in rows:
                mails.append({
                    'id': row['id'],
                    'mail_from': row['mail_from'],
                    'mail_to': row['mail_to'],
                    'subject': row['subject'] or '',
                    'body': row['body'] or '',
                    'has_attachment': bool(row['has_attachment']),
                    'received_at': row['received_at']
                })

            conn.close()
            return mails
        except Exception as e:
            add_service_log('邮件服务器', f'查询邮件列表失败：{str(e)}', 'error')
            return []


    def _load_accounts(self):
        """加载账户信息"""
        try:
            if os.path.exists(self.accounts_file):
                with open(self.accounts_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            add_service_log('邮件服务器', f'加载账户失败: {str(e)}', 'error')
            return {}
    
    def _save_mail(self, sender, recipients, subject, content, mail_data=None):
        """保存邮件到收件人的邮箱"""
        try:
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            mail_id = f"{timestamp}_{hash(content) % 10000:04d}"

            # 生成当前日期
            current_date = time.strftime('%a, %d %b %Y %H:%M:%S +0800')

            # 为每个收件人保存邮件
            for recipient in recipients:
                # 提取用户名（去掉@domain部分）
                username = recipient.split('@')[0] if '@' in recipient else recipient
                user_mail_dir = os.path.join(self.mail_storage_dir, username, 'INBOX')

                # 确保用户邮箱目录存在
                os.makedirs(user_mail_dir, exist_ok=True)

                mail_file = os.path.join(user_mail_dir, f"{mail_id}.eml")

                # 如果有原始邮件数据，处理并添加缺失的字段
                if mail_data:
                    mail_content = mail_data.decode('utf-8', errors='ignore')

                    # 检查并添加 Date 字段
                    import email
                    msg = email.message_from_string(mail_content)
                    if not msg.get('Date'):
                        # 在邮件头后添加 Date 字段
                        lines = mail_content.split('\n')
                        new_lines = []
                        header_done = False
                        for line in lines:
                            new_lines.append(line)
                            if not header_done and line.strip() == '':
                                # 在空行之前插入 Date
                                new_lines.insert(-1, f'Date: {current_date}')
                                header_done = True
                        if not header_done:
                            # 如果没有找到空行，在开头添加 Date
                            new_lines.insert(0, f'Date: {current_date}')
                        mail_content = '\n'.join(new_lines)
                else:
                    # 构造邮件内容
                    mail_content = f"""From: {sender}
To: {', '.join(recipients)}
Subject: {subject}
Date: {current_date}
Message-ID: <{mail_id}@{self.domain}>

{content}"""

                with open(mail_file, 'w', encoding='utf-8') as f:
                    f.write(mail_content)

                add_service_log('邮件服务器', f'邮件已保存: {username} <- {sender} ({subject})')

                # 清理旧邮件，只保留最新的20封
                self._cleanup_old_mails(username, user_mail_dir, max_mails=20)

        except Exception as e:
            add_service_log('邮件服务器', f'保存邮件失败: {str(e)}', 'error')

    def _cleanup_old_mails(self, username, user_mail_dir, max_mails=20):
        """清理旧邮件，只保留最新的 max_mails 封"""
        try:
            # 获取所有邮件文件及其修改时间
            mail_files = []
            for filename in os.listdir(user_mail_dir):
                if filename.endswith('.eml'):
                    mail_path = os.path.join(user_mail_dir, filename)
                    try:
                        mtime = os.path.getmtime(mail_path)
                        mail_files.append((mtime, mail_path, filename))
                    except:
                        continue

            # 如果邮件数量超过限制，删除最旧的
            if len(mail_files) > max_mails:
                # 按修改时间排序（最旧的在前）
                mail_files.sort(key=lambda x: x[0], reverse=False)

                # 计算需要删除的数量
                to_delete = len(mail_files) - max_mails
                deleted_count = 0

                for i in range(to_delete):
                    try:
                        os.remove(mail_files[i][1])
                        deleted_count += 1
                    except Exception as e:
                        pass

                add_service_log('邮件服务器', f'清理旧邮件: {username} 删除了 {deleted_count} 封，保留 {max_mails} 封')

        except Exception as e:
            add_service_log('邮件服务器', f'清理旧邮件失败: {str(e)}', 'error')
    

    def _handle_pop3_client(self, client_socket, addr, conn_id):
        """处理POP3客户端连接 - 标准POP3服务器"""
        try:
            # 发送欢迎消息
            client_socket.send(b'+OK POP3 server ready\r\n')
            
            # POP3会话状态
            authenticated = False
            current_user = None
            user_mails = []
            deleted_mails = set()
            
            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break
                        
                    command = data.decode('utf-8', errors='ignore').strip()
                    add_service_log('邮件服务器', f'📬 POP3命令: {command}')
                    
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['commands'] += 1
                    
                    # 解析POP3命令
                    parts = command.split()
                    if not parts:
                        client_socket.send(b'-ERR Invalid command\r\n')
                        continue
                    
                    cmd = parts[0].upper()
                    
                    if cmd == 'USER':
                        if len(parts) < 2:
                            client_socket.send(b'-ERR Missing username\r\n')
                            continue
                        username = parts[1]
                        accounts = self._load_accounts()

                        # 支持邮箱地址登录 - 提取用户名
                        input_username = username.split('@')[0] if '@' in username else username

                        if input_username in accounts:
                            current_user = input_username
                            client_socket.send(b'+OK User accepted\r\n')
                            add_service_log('邮件服务器', f'📬 POP3用户: {username}')
                        else:
                            client_socket.send(b'-ERR Invalid user\r\n')
                    
                    elif cmd == 'PASS':
                        if not current_user:
                            client_socket.send(b'-ERR No user specified\r\n')
                            continue
                        if len(parts) < 2:
                            client_socket.send(b'-ERR Missing password\r\n')
                            continue
                        
                        password = parts[1]
                        accounts = self._load_accounts()
                        if accounts.get(current_user, {}).get('password') == password:
                            authenticated = True
                            user_mails = self._get_user_mails(current_user, 'INBOX')
                            deleted_mails = set()
                            client_socket.send(f'+OK {len(user_mails)} messages\r\n'.encode())
                            add_service_log('邮件服务器', f'✅ POP3认证成功: {current_user}')
                            
                            with service_lock:
                                if conn_id in self.state['connections']:
                                    self.state['connections'][conn_id]['authenticated'] = True
                                    self.state['connections'][conn_id]['username'] = current_user
                        else:
                            client_socket.send(b'-ERR Invalid password\r\n')
                            add_service_log('邮件服务器', f'❌ POP3认证失败: {current_user}')
                    
                    elif cmd == 'STAT':
                        if not authenticated:
                            client_socket.send(b'-ERR Not authenticated\r\n')
                            continue
                        
                        available_mails = [mail for i, mail in enumerate(user_mails) if i not in deleted_mails]
                        total_size = sum(mail.get('size', 0) for mail in available_mails)
                        client_socket.send(f'+OK {len(available_mails)} {total_size}\r\n'.encode())
                    
                    elif cmd == 'LIST':
                        if not authenticated:
                            client_socket.send(b'-ERR Not authenticated\r\n')
                            continue
                        
                        if len(parts) > 1:
                            # LIST specific message
                            try:
                                msg_num = int(parts[1])
                                if 1 <= msg_num <= len(user_mails) and (msg_num - 1) not in deleted_mails:
                                    mail = user_mails[msg_num - 1]
                                    client_socket.send(f'+OK {msg_num} {mail.get("size", 0)}\r\n'.encode())
                                else:
                                    client_socket.send(b'-ERR No such message\r\n')
                            except ValueError:
                                client_socket.send(b'-ERR Invalid message number\r\n')
                        else:
                            # LIST all messages
                            available_mails = [(i, mail) for i, mail in enumerate(user_mails) if i not in deleted_mails]
                            client_socket.send(f'+OK {len(available_mails)} messages\r\n'.encode())
                            for i, mail in available_mails:
                                client_socket.send(f'{i + 1} {mail.get("size", 0)}\r\n'.encode())
                            client_socket.send(b'.\r\n')
                    
                    elif cmd == 'RETR':
                        if not authenticated:
                            client_socket.send(b'-ERR Not authenticated\r\n')
                            continue

                        if len(parts) < 2:
                            client_socket.send(b'-ERR Missing message number\r\n')
                            continue

                        try:
                            msg_num = int(parts[1])
                            if 1 <= msg_num <= len(user_mails) and (msg_num - 1) not in deleted_mails:
                                mail = user_mails[msg_num - 1]

                                # 直接读取原始邮件文件内容（包含附件）
                                mail_file_path = mail.get('file_path')
                                if mail_file_path and os.path.exists(mail_file_path):
                                    with open(mail_file_path, 'r', encoding='utf-8') as f:
                                        mail_content = f.read()
                                else:
                                    # 后备方案：使用解析后的数据构建 MIME 邮件
                                    from email.mime.text import MIMEText
                                    from email.mime.multipart import MIMEMultipart
                                    from email.header import Header
                                    import email.utils

                                    msg = MIMEMultipart()
                                    msg['From'] = Header(mail.get('from', 'unknown'), 'utf-8')
                                    msg['To'] = Header(mail.get('to', 'unknown'), 'utf-8')
                                    msg['Subject'] = Header(mail.get('subject', 'No Subject'), 'utf-8')
                                    msg['Date'] = mail.get('date', email.utils.formatdate(localtime=True))

                                    body_text = mail.get('body', '')
                                    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
                                    mail_content = msg.as_string()

                                client_socket.send(f'+OK {len(mail_content)} octets\r\n'.encode())
                                client_socket.send(mail_content.encode('utf-8') + b'\r\n.\r\n')
                                add_service_log('邮件服务器', f'📬 POP3发送邮件: {msg_num} ({len(mail_content)} bytes)')
                            else:
                                client_socket.send(b'-ERR No such message\r\n')
                        except ValueError:
                            client_socket.send(b'-ERR Invalid message number\r\n')
                    
                    elif cmd == 'DELE':
                        if not authenticated:
                            client_socket.send(b'-ERR Not authenticated\r\n')
                            continue
                        
                        if len(parts) < 2:
                            client_socket.send(b'-ERR Missing message number\r\n')
                            continue
                        
                        try:
                            msg_num = int(parts[1])
                            if 1 <= msg_num <= len(user_mails) and (msg_num - 1) not in deleted_mails:
                                deleted_mails.add(msg_num - 1)
                                client_socket.send(f'+OK Message {msg_num} deleted\r\n'.encode())
                                add_service_log('邮件服务器', f'🗑️ POP3删除邮件: {msg_num}')
                            else:
                                client_socket.send(b'-ERR No such message\r\n')
                        except ValueError:
                            client_socket.send(b'-ERR Invalid message number\r\n')
                    
                    elif cmd == 'QUIT':
                        client_socket.send(b'+OK POP3 server signing off\r\n')
                        add_service_log('邮件服务器', f'👋 POP3客户端退出: {current_user or "未认证"}')
                        break
                    
                    else:
                        client_socket.send(b'-ERR Unknown command\r\n')
                        add_service_log('邮件服务器', f'❓ 未知POP3命令: {cmd}', 'warning')
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('邮件服务器', f'POP3处理异常: {e}', 'error')
                    break
                    
        except Exception as e:
            add_service_log('邮件服务器', f'POP3客户端处理失败: {e}', 'error')
        finally:
            try:
                client_socket.close()
            except:
                pass
            with service_lock:
                if conn_id in self.state['connections']:
                    del self.state['connections'][conn_id]
            add_service_log('邮件服务器', f'📬 POP3连接已关闭: {addr[0]}:{addr[1]}')

    def _get_user_mails(self, username, folder='INBOX'):
        """获取用户的邮件列表"""
        try:
            user_mail_dir = os.path.join(self.mail_storage_dir, username, folder)
            if not os.path.exists(user_mail_dir):
                return []

            mails = []
            for filename in os.listdir(user_mail_dir):
                if filename.endswith('.eml'):
                    mail_path = os.path.join(user_mail_dir, filename)
                    try:
                        with open(mail_path, 'r', encoding='utf-8') as f:
                            content = f.read()

                        # 使用 email 模块解析邮件
                        import email
                        msg = email.message_from_string(content)

                        # 提取邮件头部
                        from_raw = msg.get('From', '')
                        to_raw = msg.get('To', '')
                        subject_raw = msg.get('Subject', '')
                        date_raw = msg.get('Date', '')

                        # 如果 email 模块没有解析到关键头部，可能是格式错误的 MIME 部件
                        # 使用手动解析作为后备方案
                        if not from_raw and not subject_raw and 'Content-Type' in content:
                            # 手动解析：清除头部之间的空行，然后重新解析
                            lines = content.split('\n')
                            cleaned_lines = []
                            header_section = True
                            for line in lines:
                                if header_section:
                                    # 如果是头部行（包含冒号），保留
                                    if ':' in line and not line.startswith('--'):
                                        cleaned_lines.append(line)
                                    # 如果是 MIME boundary，头部部分结束
                                    elif line.startswith('--'):
                                        header_section = False
                                        cleaned_lines.append('')
                                        cleaned_lines.append(line)
                                    # 空行在头部部分中跳过（这是格式错误的根源）
                                    elif line.strip() == '':
                                        continue
                                    else:
                                        cleaned_lines.append(line)
                                else:
                                    cleaned_lines.append(line)

                            cleaned_content = '\n'.join(cleaned_lines)
                            msg = email.message_from_string(cleaned_content)
                            from_raw = msg.get('From', '')
                            to_raw = msg.get('To', '')
                            subject_raw = msg.get('Subject', '')
                            date_raw = msg.get('Date', '')

                        # 解码MIME编码的邮件头
                        decoded_from = self._decode_mime_header(from_raw) if from_raw else ''
                        decoded_to = self._decode_mime_header(to_raw) if to_raw else ''
                        decoded_subject = self._decode_mime_header(subject_raw) if subject_raw else ''
                        decoded_date = self._decode_mime_header(date_raw) if date_raw else ''

                        # 提取正文（简单文本邮件）
                        body = ''
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == 'text/plain':
                                    try:
                                        payload = part.get_payload(decode=True)
                                        if isinstance(payload, bytes):
                                            body = payload.decode('utf-8', errors='ignore')
                                        else:
                                            body = str(payload) if payload else ''
                                        break
                                    except:
                                        pass
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                if isinstance(payload, bytes):
                                    body = payload.decode('utf-8', errors='ignore')
                                else:
                                    body = str(payload) if payload else ''

                        add_service_log('邮件服务器', f'解析邮件 {filename}: From={decoded_from}, Subject={decoded_subject}')

                        # 从文件名提取时间戳（格式：YYYYMMDD_HHMMSS_XXXX）
                        filename_timestamp = filename.replace('.eml', '')
                        formatted_date = decoded_date or '未知日期'

                        # 如果日期为空，从文件名提取
                        if (not decoded_date or decoded_date == '未知日期') and len(filename_timestamp) >= 15:
                            try:
                                # 提取 YYYYMMDD_HHMMSS 部分
                                ts = filename_timestamp[:15]
                                from datetime import datetime
                                dt = datetime.strptime(ts, '%Y%m%d_%H%M%S')
                                formatted_date = dt.strftime('%Y-%m-%d %H:%M:%S')
                            except:
                                pass

                        mails.append({
                            'id': filename.replace('.eml', ''),
                            'from': decoded_from or '未知发件人',
                            'to': decoded_to or '未知收件人',
                            'subject': decoded_subject or '无主题',
                            'date': formatted_date,
                            'date_raw': date_raw,
                            'size': len(content),
                            'body': body,
                            'file_path': mail_path,  # 添加文件路径，用于 POP3 读取原始邮件
                            '_sort_key': filename_timestamp,  # 用于排序
                        })
                    except Exception as e:
                        add_service_log('邮件服务器', f'解析邮件失败 {filename}: {str(e)}', 'error')

            # 按文件名时间戳排序（最老的在前，符合IMAP/POP3传统：ID=1最老，ID=n最新）
            mails.sort(key=lambda x: x.get('_sort_key', ''), reverse=False)
            # 移除临时排序字段
            for mail in mails:
                mail.pop('_sort_key', None)
            return mails
            
        except Exception as e:
            add_service_log('邮件服务器', f'获取邮件列表失败: {str(e)}', 'error')
            return []

    def run(self):
        host = self.state['host']
        
        try:
            # 启动SMTP服务器
            smtp_thread = threading.Thread(target=self._run_smtp_server, args=(host,), daemon=True)
            smtp_thread.start()
            
            # 启动IMAP服务器
            imap_thread = threading.Thread(target=self._run_imap_server, args=(host,), daemon=True)
            imap_thread.start()
            
            # 启动POP3服务器
            pop3_thread = threading.Thread(target=self._run_pop3_server, args=(host,), daemon=True)
            pop3_thread.start()
            
            add_service_log('邮件服务器', f'✅ 邮件服务器启动成功')
            add_service_log('邮件服务器', f'📧 SMTP服务: {host}:{self.smtp_port}')
            add_service_log('邮件服务器', f'📥 IMAP服务: {host}:{self.imap_port}')
            add_service_log('邮件服务器', f'📬 POP3服务: {host}:{self.pop3_port}')
            add_service_log('邮件服务器', f'🌐 邮件域名: {self.domain}')
            
            # 等待停止信号
            while not self.stop_event.is_set():
                time.sleep(1)
                
        except Exception as e:
            add_service_log('邮件服务器', f'❌ 邮件服务器启动失败: {str(e)}', 'error')
    
    def _run_smtp_server(self, host):
        """运行标准 SMTP 服务器 - 纯 socket 实现"""
        try:
            self.smtp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.smtp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.smtp_socket.bind((host, self.smtp_port))
            self.smtp_socket.listen(100)
            self.smtp_socket.settimeout(1.0)

            add_service_log('邮件服务器', f'📬 SMTP 服务器启动：{host}:{self.smtp_port}', 'info')

            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.smtp_socket.accept()
                    client_socket.settimeout(30.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'protocol': 'SMTP',
                            'mail_from': None,
                            'rcpt_tos': [],
                            'commands': 0,
                            'data_buffer': b''
                        }

                    add_service_log('邮件服务器', f'📬 SMTP 客户端连接：{addr[0]}:{addr[1]}', 'info')
                    threading.Thread(
                        target=self._handle_smtp_client,
                        args=(client_socket, addr, conn_id),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('邮件服务器', f'SMTP 接受连接失败：{e}', 'error')

        except Exception as e:
            error_msg = str(e)
            if 'Address already in use' in error_msg or 'WinError 10048' in error_msg:
                add_service_log('邮件服务器', f'SMTP 端口{self.smtp_port}已被占用，请检查端口冲突', 'error')
            elif 'Permission denied' in error_msg or 'WinError 10013' in error_msg:
                add_service_log('邮件服务器', f'SMTP 端口{self.smtp_port}权限不足，请使用管理员权限运行', 'error')
            else:
                add_service_log('邮件服务器', f'SMTP 服务器启动失败：{error_msg}', 'error')
        finally:
            if self.smtp_socket:
                try:
                    self.smtp_socket.close()
                except:
                    pass
            self.smtp_socket = None

    def _handle_smtp_client(self, client_socket, addr, conn_id):
        """处理 SMTP 客户端连接 - 纯 socket 实现"""
        try:
            # 发送欢迎消息 (220)
            client_socket.send(b'220 mail.local ESMTP Service Ready\r\n')
            add_service_log('邮件服务器', f'SMTP 连接建立：{addr[0]}:{addr[1]}')

            # SMTP 会话状态
            mail_from = None
            rcpt_tos = []
            data_mode = False
            data_buffer = b''

            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break

                    # 处理 DATA 模式下的数据接收
                    if data_mode:
                        data_buffer += data
                        # 检查结束标志：单独一行的 "."
                        if data_buffer.endswith(b'\r\n.\r\n'):
                            data_mode = False
                            # 移除结束的 "."
                            mail_data = data_buffer[:-5]
                            add_service_log('邮件服务器', f'邮件数据接收完成，长度：{len(mail_data)}')

                            # 解析邮件内容
                            try:
                                import email
                                msg = email.message_from_bytes(mail_data)
                                subject = msg.get('Subject', '无主题')
                                body = msg.get_payload()
                                if isinstance(body, list):
                                    body = body[0].get_payload() if body else ''
                            except Exception as e:
                                add_service_log('邮件服务器', f'邮件解析失败：{e}', 'error')
                                subject = 'Unknown'
                                body = mail_data.decode('utf-8', errors='ignore')

                            # 保存邮件到数据库
                            self._save_mail_to_db(
                                mail_from,
                                ', '.join(rcpt_tos),
                                subject,
                                body if isinstance(body, str) else str(body),
                                raw_content=mail_data,
                                has_attachment=msg.get_content_maintype() == 'multipart' if 'msg' in dir() else False
                            )

                            # 同时保存到文件系统 (兼容性)
                            self._save_mail(mail_from, rcpt_tos, subject, body, mail_data)

                            add_service_log('邮件服务器', f'邮件已保存：{mail_from} -> {rcpt_tos}')
                            client_socket.send(b'250 OK\r\n')

                            # 重置状态
                            mail_from = None
                            rcpt_tos = []
                            data_buffer = b''
                        continue

                    # 解析 SMTP 命令
                    command = data.decode('utf-8', errors='ignore').strip()
                    add_service_log('邮件服务器', f'SMTP 命令：{command}')

                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['commands'] += 1

                    parts = command.split(' ', 1)
                    cmd = parts[0].upper()
                    arg = parts[1] if len(parts) > 1 else ''

                    # EHLO/HELO - 客户端握手
                    if cmd in ('EHLO', 'HELO'):
                        client_socket.send(b'250-mail.local\r\n')
                        client_socket.send(b'250-SIZE 10485760\r\n')
                        client_socket.send(b'250 OK\r\n')
                        add_service_log('邮件服务器', f'SMTP {cmd} 响应：250 OK')

                    # MAIL FROM - 指定发件人
                    elif cmd == 'MAIL FROM':
                        # 提取发件人邮箱地址
                        mail_from = arg.split('<')[1].split('>')[0] if '<' in arg else arg.strip()
                        add_service_log('邮件服务器', f'MAIL FROM: {mail_from}')
                        client_socket.send(b'250 OK\r\n')

                    # RCPT TO - 指定收件人
                    elif cmd == 'RCPT TO':
                        # 提取收件人邮箱地址
                        rcpt_to = arg.split('<')[1].split('>')[0] if '<' in arg else arg.strip()
                        rcpt_tos.append(rcpt_to)
                        add_service_log('邮件服务器', f'RCPT TO: {rcpt_to}')
                        client_socket.send(b'250 OK\r\n')

                    # DATA - 开始传输邮件数据
                    elif cmd == 'DATA':
                        data_mode = True
                        data_buffer = b''
                        add_service_log('邮件服务器', '进入 DATA 模式')
                        client_socket.send(b'354 Start mail input; end with <CRLF>.<CRLF>\r\n')

                    # QUIT - 结束连接
                    elif cmd == 'QUIT':
                        client_socket.send(b'221 Bye\r\n')
                        add_service_log('邮件服务器', 'SMTP 连接关闭')
                        break

                    # RSET - 重置当前会话
                    elif cmd == 'RSET':
                        mail_from = None
                        rcpt_tos = []
                        data_buffer = b''
                        client_socket.send(b'250 OK\r\n')

                    # NOOP - 空操作
                    elif cmd == 'NOOP':
                        client_socket.send(b'250 OK\r\n')

                    # VRFY - 验证用户 (不支持)
                    elif cmd == 'VRFY':
                        client_socket.send(b'252 Cannot VRFY user\r\n')

                    # 未知命令
                    else:
                        client_socket.send(b'502 Command not recognized\r\n')
                        add_service_log('邮件服务器', f'未知 SMTP 命令：{cmd}')

                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('邮件服务器', f'SMTP 处理错误：{e}', 'error')
                    break

        except Exception as e:
            add_service_log('邮件服务器', f'SMTP 连接处理失败：{e}', 'error')
        finally:
            try:
                client_socket.close()
            except:
                pass
            with service_lock:
                if conn_id in self.state['connections']:
                    del self.state['connections'][conn_id]
            add_service_log('邮件服务器', f'SMTP 连接已关闭：{addr[0]}:{addr[1]}')


    def _run_imap_server(self, host):
        """运行标准IMAP服务器"""
        try:
            self.imap_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.imap_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.imap_socket.bind((host, self.imap_port))
            self.imap_socket.listen(100)
            self.imap_socket.settimeout(1.0)
            
            msg = f'*** IMAP服务器启动成功: {host}:{self.imap_port} ***'
            add_service_log('邮件服务器', msg, 'info')
            print(msg)
            
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.imap_socket.accept()
                    client_socket.settimeout(30.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'protocol': 'IMAP',
                            'authenticated': False,
                            'username': '',
                            'selected_folder': None,
                            'commands': 0
                        }
                    
                    add_service_log('邮件服务器', f'📥 IMAP客户端连接: {addr[0]}:{addr[1]}', 'info')
                    # 处理IMAP客户端连接
                    threading.Thread(target=self._handle_imap_client, args=(client_socket, addr, conn_id), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('邮件服务器', f'IMAP接受连接失败: {e}', 'error')
            
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.imap_socket.accept()
                    client_socket.settimeout(30.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'protocol': 'IMAP',
                            'authenticated': False,
                            'username': '',
                            'commands': 0
                        }
                    threading.Thread(
                        target=self._handle_imap_client,
                        args=(client_socket, addr, conn_id),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('邮件服务器', f'IMAP接受连接失败: {e}', 'error')
        except Exception as e:
            error_msg = str(e)
            if 'Address already in use' in error_msg or 'WinError 10048' in error_msg:
                add_service_log('邮件服务器', f'IMAP端口{self.imap_port}已被占用，请检查端口冲突', 'error')
            elif 'Permission denied' in error_msg or 'WinError 10013' in error_msg:
                add_service_log('邮件服务器', f'IMAP端口{self.imap_port}权限不足，请使用管理员权限运行', 'error')
            else:
                add_service_log('邮件服务器', f'IMAP服务器启动失败: {error_msg}', 'error')
        finally:
            if self.imap_socket:
                try:
                    self.imap_socket.close()
                except:
                    pass
            self.imap_socket = None
    
    def _run_pop3_server(self, host):
        """运行标准POP3服务器"""
        try:
            self.pop3_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.pop3_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.pop3_socket.bind((host, self.pop3_port))
            self.pop3_socket.listen(100)
            self.pop3_socket.settimeout(1.0)
            
            add_service_log('邮件服务器', f'📬 POP3服务器启动: {host}:{self.pop3_port}', 'info')
            
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.pop3_socket.accept()
                    client_socket.settimeout(30.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'protocol': 'POP3',
                            'authenticated': False,
                            'username': '',
                            'commands': 0
                        }
                    
                    add_service_log('邮件服务器', f'📬 POP3客户端连接: {addr[0]}:{addr[1]}', 'info')
                    # 处理POP3客户端连接
                    threading.Thread(target=self._handle_pop3_client, args=(client_socket, addr, conn_id), daemon=True).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('邮件服务器', f'POP3接受连接失败: {e}', 'error')
            
            while not self.stop_event.is_set():
                try:
                    client_socket, addr = self.pop3_socket.accept()
                    client_socket.settimeout(30.0)
                    conn_id = str(uuid.uuid4())
                    with service_lock:
                        self.state['connections'][conn_id] = {
                            'id': conn_id,
                            'address': f"{addr[0]}:{addr[1]}",
                            'protocol': 'POP3',
                            'authenticated': False,
                            'username': '',
                            'commands': 0
                        }
                    threading.Thread(
                        target=self._handle_pop3_client,
                        args=(client_socket, addr, conn_id),
                        daemon=True
                    ).start()
                except socket.timeout:
                    continue
                except Exception as e:
                    if not self.stop_event.is_set():
                        add_service_log('邮件服务器', f'POP3接受连接失败: {e}', 'error')
        except Exception as e:
            error_msg = str(e)
            if 'Address already in use' in error_msg or 'WinError 10048' in error_msg:
                add_service_log('邮件服务器', f'POP3端口{self.pop3_port}已被占用，请检查端口冲突', 'error')
            elif 'Permission denied' in error_msg or 'WinError 10013' in error_msg:
                add_service_log('邮件服务器', f'POP3端口{self.pop3_port}权限不足，请使用管理员权限运行', 'error')
            else:
                add_service_log('邮件服务器', f'POP3服务器启动失败: {error_msg}', 'error')
        finally:
            if self.pop3_socket:
                try:
                    self.pop3_socket.close()
                except:
                    pass
            self.pop3_socket = None

    def _authenticate_user(self, username, password):
        """验证用户账户（兼容性方法）"""
        is_valid, email, _ = self._authenticate_user_detailed(username, password)
        return is_valid, email
    
    def _authenticate_user_detailed(self, username, password):
        """详细验证用户账户，返回认证方式 - 支持用户名和邮箱地址两种登录方式"""
        add_service_log('邮件服务器', f'开始详细验证用户: "{username}", 密码: "{password}"')
        
        # 首先尝试从JSON文件加载账户
        accounts = self._load_accounts()
        add_service_log('邮件服务器', f'JSON账户列表: {list(accounts.keys())}')
        
        # 检查JSON账户

        # 提取用户名（支持邮箱地址登录）
        input_username = username.split('@')[0] if '@' in username else username
        add_service_log('邮件服务器', f'输入用户名：\"{username}\", 提取后：\"{input_username}\"')

        for stored_username, account_info in accounts.items():
            stored_password = account_info.get('password', '')
            stored_email = account_info.get('email', f"{stored_username}@{self.domain}")
            
            add_service_log('邮件服务器', f'JSON账户检查: 存储用户名="{stored_username}", 存储密码="{stored_password}"')
            add_service_log('邮件服务器', f'密码比较: 输入密码="{password}" vs 存储密码="{stored_password}"')
            add_service_log('邮件服务器', f'密码长度: 输入={len(password)}, 存储={len(stored_password)}')
            add_service_log('邮件服务器', f'密码字节: 输入={password.encode("utf-8")}, 存储={stored_password.encode("utf-8")}')
            
            # 支持用户名登录和邮箱地址登录
            username_matches = (stored_username == username or
                               stored_username == input_username or
                               stored_email == username)

            if username_matches:
                # 尝试多种密码匹配方式
                password_matches = [
                    stored_password == password,  # 直接匹配
                    stored_password == password.strip(),  # 去除空格
                    stored_password.strip() == password,  # 存储密码去除空格
                    stored_password.strip() == password.strip(),  # 双方都去除空格
                ]
                
                add_service_log('邮件服务器', f'密码匹配测试结果: {password_matches}')
                
                if any(password_matches):
                    add_service_log('邮件服务器', f'JSON账户验证成功: {username} -> {stored_email}')
                    return True, stored_email, 'JSON文件'
                else:
                    add_service_log('邮件服务器', f'用户名匹配但密码不匹配: "{username}"')
                    # 尝试常见的密码变体
                    common_passwords = ['tdx@2017', 'tdhx@2017', 'test123', 'password']
                    for test_pwd in common_passwords:
                        if stored_password == test_pwd:
                            add_service_log('邮件服务器', f'发现存储密码是常见密码: "{test_pwd}"')
                            break
        
        # 如果JSON文件中没有，尝试从初始化参数中查找
        add_service_log('邮件服务器', f'初始化账户数量: {len(self.accounts)}')
        for i, account in enumerate(self.accounts):
            account_user = account.get('username', '')
            account_password = account.get('password', '')
            account_email = account.get('email', f"{account_user}@{self.domain}")
            
            add_service_log('邮件服务器', f'初始化账户{i}检查: 用户名="{account_user}", 密码="{account_password}", 邮箱="{account_email}"')
            add_service_log('邮件服务器', f'初始化密码比较: 输入密码="{password}" vs 存储密码="{account_password}"')
            
            if account_user == username and account_password == password:
                add_service_log('邮件服务器', f'初始化账户验证成功: {username} -> {account_email}')
                return True, account_email, '初始化参数'
            elif account_user == username:
                add_service_log('邮件服务器', f'初始化用户名匹配但密码不匹配: "{username}"')
        
        add_service_log('邮件服务器', f'账户验证失败: 用户名="{username}", 密码="{password}"')
        return False, None, '验证失败'
    
    def _get_all_usernames(self):
        """获取所有可用的用户名列表"""
        usernames = set()
        
        # 从JSON文件获取
        accounts = self._load_accounts()
        usernames.update(accounts.keys())
        add_service_log('邮件服务器', f'从JSON文件获取的用户名: {list(accounts.keys())}')
        
        # 从初始化参数获取
        init_usernames = []
        for account in self.accounts:
            username = account.get('username', '')
            if username:
                usernames.add(username)
                init_usernames.append(username)
        add_service_log('邮件服务器', f'从初始化参数获取的用户名: {init_usernames}')
        
        all_usernames = list(usernames)
        add_service_log('邮件服务器', f'所有可用用户名: {all_usernames}')
        return all_usernames
    
    def _create_default_test_account(self):
        """创建默认测试账户 - sender 和 receiver"""
        try:
            # 默认密码 - 必须与前端默认密码保持一致 (service_deploy.html)
            default_password = 'test123'

            # 创建两个默认账户：sender 和 receiver
            default_accounts = {
                'sender': {
                    'password': default_password,
                    'email': f'sender@{self.domain}',
                    'created': time.strftime('%Y-%m-%d %H:%M:%S')
                },
                'receiver': {
                    'password': default_password,
                    'email': f'receiver@{self.domain}',
                    'created': time.strftime('%Y-%m-%d %H:%M:%S')
                }
            }

            # 确保目录存在
            os.makedirs(os.path.dirname(self.accounts_file), exist_ok=True)

            # 保存到JSON文件
            with open(self.accounts_file, 'w', encoding='utf-8') as f:
                json.dump(default_accounts, f, indent=2, ensure_ascii=False)

            add_service_log('邮件服务器', f'已创建默认测试账户:')
            add_service_log('邮件服务器', f'  - sender@{self.domain} (密码: {default_password})')
            add_service_log('邮件服务器', f'  - receiver@{self.domain} (密码: {default_password})')
            add_service_log('邮件服务器', f'账户文件保存到: {self.accounts_file}')

            # 立即验证创建的账户
            accounts = self._load_accounts()
            for username in ['sender', 'receiver']:
                if username in accounts:
                    stored_password = accounts[username].get('password', '')
                    add_service_log('邮件服务器', f'验证创建结果: {username} 存储密码匹配={stored_password == default_password}')

        except Exception as e:
            add_service_log('邮件服务器', f'创建默认测试账户失败: {e}', 'error')

    def _get_user_mail_paths(self, conn_id):
        """获取用户的邮件文件路径列表（用于IMAP FETCH等需要自己解析的场景）"""
        try:
            # 从连接状态获取用户名
            with service_lock:
                if conn_id not in self.state['connections']:
                    return []
                username = self.state['connections'][conn_id].get('username', '')
            
            if not username:
                return []
            
            # 获取用户收件箱目录
            user_inbox = os.path.join(self.mail_storage_dir, username, 'INBOX')
            if not os.path.exists(user_inbox):
                return []
            
            # 获取所有邮件文件
            mail_files = []
            for filename in os.listdir(user_inbox):
                if filename.endswith('.eml'):
                    mail_path = os.path.join(user_inbox, filename)
                    try:
                        # 获取文件修改时间作为排序依据
                        mtime = os.path.getmtime(mail_path)
                        mail_files.append((mtime, mail_path, filename))
                    except:
                        continue
            
            # 按时间排序（最老的在前，符合IMAP/POP3传统：ID=1最老，ID=n最新）
            mail_files.sort(key=lambda x: x[0], reverse=False)

            add_service_log('邮件服务器', f'用户 {username} 收件箱中有 {len(mail_files)} 封邮件')
            # 限制返回最近的10封邮件（取最后10封，即最新的10封）
            if len(mail_files) > 10:
                limited_mail_files = mail_files[-10:]
            else:
                limited_mail_files = mail_files
            return [mail_info[1] for mail_info in limited_mail_files]  # 返回文件路径列表
            
        except Exception as e:
            add_service_log('邮件服务器', f'获取用户邮件失败: {e}', 'error')
            return []

    def _decode_mime_header(self, header_value):
        """解码MIME编码的邮件头"""
        try:
            if not header_value:
                return header_value
            
            from email.header import decode_header
            decoded_parts = decode_header(header_value)
            
            result = ''
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    if encoding:
                        try:
                            result += part.decode(encoding)
                        except (UnicodeDecodeError, LookupError):
                            # 如果指定编码失败，尝试UTF-8
                            try:
                                result += part.decode('utf-8')
                            except UnicodeDecodeError:
                                # 最后尝试忽略错误
                                result += part.decode('utf-8', errors='ignore')
                    else:
                        # 没有指定编码，尝试UTF-8
                        try:
                            result += part.decode('utf-8')
                        except UnicodeDecodeError:
                            result += part.decode('utf-8', errors='ignore')
                else:
                    # 已经是字符串
                    result += part
            
            return result.strip()
            
        except Exception as e:
            add_service_log('邮件服务器', f'MIME头解码失败: {str(e)}', 'error')
            return header_value  # 解码失败时返回原始值

    def _handle_imap_client(self, client_socket, addr, conn_id):
        """处理IMAP客户端连接"""
        try:
            add_service_log('邮件服务器', f'IMAP客户端连接: {addr[0]}:{addr[1]}')
            
            # 发送欢迎消息，包含CAPABILITY信息
            add_service_log('邮件服务器', f'*** IMAP客户端连接成功: {addr[0]}:{addr[1]} ***')
            client_socket.send(b'* OK [CAPABILITY IMAP4rev1 ID LOGIN-REFERRALS AUTH=PLAIN IDLE ENABLE LITERAL+ SASL-IR] IMAP4rev1 Service Ready\r\n')
            add_service_log('邮件服务器', '*** IMAP欢迎消息已发送 ***')
            
            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break
                        
                    command = data.decode('utf-8', errors='ignore').strip()
                    add_service_log('邮件服务器', f'IMAP命令: {command}')
                    
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['commands'] += 1
                    
                    # 解析IMAP命令 (格式: TAG COMMAND [参数...])
                    parts = command.split()
                    if len(parts) < 2:
                        client_socket.send(b'* BAD Invalid command format\r\n')
                        continue
                        
                    tag = parts[0]  # 客户端标签
                    cmd = parts[1].upper()  # 命令
                    
                    add_service_log('邮件服务器', f'IMAP命令解析: 标签={tag}, 命令={cmd}, 参数数量={len(parts)-2}')
                    
                    # LOGIN命令处理
                    if cmd == 'LOGIN':
                        if len(parts) >= 4:
                            # 更智能的参数解析，处理带引号的参数
                            raw_username = parts[2]
                            raw_password = parts[3]
                            
                            # 移除引号（支持单引号和双引号）
                            username = raw_username.strip('"').strip("'")
                            password = raw_password.strip('"').strip("'")
                            
                            add_service_log('邮件服务器', f'IMAP LOGIN解析: 原始用户名="{raw_username}", 解析后用户名="{username}"')
                            add_service_log('邮件服务器', f'IMAP LOGIN解析: 原始密码="{raw_password}", 解析后密码="{password}"')
                            add_service_log('邮件服务器', f'IMAP登录尝试: 用户名={username}, 密码长度={len(password)}')
                            
                            # 尝试直接认证（完整邮箱地址）
                            is_valid, email, auth_method = self._authenticate_user_detailed(username, password)
                            
                            # 如果直接认证失败，且用户名包含@，尝试提取用户名部分
                            if not is_valid and '@' in username:
                                username_part = username.split('@')[0]
                                add_service_log('邮件服务器', f'IMAP尝试用户名部分认证: {username_part}')
                                is_valid, email, auth_method = self._authenticate_user_detailed(username_part, password)
                                if is_valid:
                                    username = username_part  # 更新用户名为提取的部分
                            
                            if is_valid:
                                response = f'{tag} OK LOGIN completed\r\n'
                                client_socket.send(response.encode())
                                with service_lock:
                                    if conn_id in self.state['connections']:
                                        self.state['connections'][conn_id]['authenticated'] = True
                                        self.state['connections'][conn_id]['username'] = username
                                        self.state['connections'][conn_id]['email'] = email
                                add_service_log('邮件服务器', f'IMAP用户登录成功: {email} (认证方式: {auth_method})')
                            else:
                                # 获取所有可用用户名进行详细分析
                                all_usernames = self._get_all_usernames()
                                
                                # 分析失败原因
                                username_part = username.split('@')[0] if '@' in username else username
                                
                                if username in all_usernames:
                                    error_msg = f'{tag} NO [AUTHENTICATIONFAILED] Invalid password\r\n'
                                    reason = "密码错误"
                                elif username_part in all_usernames:
                                    error_msg = f'{tag} NO [AUTHENTICATIONFAILED] Invalid password\r\n'
                                    reason = f"密码错误 (用户名部分 '{username_part}' 存在)"
                                else:
                                    error_msg = f'{tag} NO [AUTHENTICATIONFAILED] Invalid username\r\n'
                                    reason = f"用户名不存在 (尝试了 '{username}' 和 '{username_part}')"
                                
                                client_socket.send(error_msg.encode())
                                add_service_log('邮件服务器', f'IMAP用户登录失败: {username} - {reason}')
                                add_service_log('邮件服务器', f'可用用户名列表: {all_usernames}')
                        else:
                            response = f'{tag} BAD LOGIN command syntax error\r\n'
                            client_socket.send(response.encode())
                            add_service_log('邮件服务器', f'IMAP LOGIN命令格式错误: {command}')
                    
                    # LIST命令处理
                    elif cmd == 'LIST':
                        client_socket.send(b'* LIST () "/" "INBOX"\r\n')
                        response = f'{tag} OK LIST completed\r\n'
                        client_socket.send(response.encode())
                    
                    # SELECT命令处理
                    elif cmd == 'SELECT':
                        # 获取当前用户的邮件数量
                        mail_list = self._get_user_mail_paths(conn_id)
                        mail_count = len(mail_list)
                        
                        client_socket.send(f'* {mail_count} EXISTS\r\n'.encode())
                        client_socket.send(f'* {mail_count} RECENT\r\n'.encode())
                        client_socket.send(b'* OK [UIDVALIDITY 1] UIDs valid\r\n')
                        client_socket.send(f'* OK [UIDNEXT {mail_count + 1}] Predicted next UID\r\n'.encode())
                        client_socket.send(b'* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)\r\n')
                        client_socket.send(b'* OK [PERMANENTFLAGS (\\Deleted \\Seen \\*)] Limited\r\n')
                        response = f'{tag} OK [READ-WRITE] SELECT completed\r\n'
                        client_socket.send(response.encode())
                        add_service_log('邮件服务器', f'IMAP SELECT命令: INBOX选中，包含 {mail_count} 封邮件')
                    
                    # CAPABILITY命令处理
                    elif cmd == 'CAPABILITY':
                        client_socket.send(b'* CAPABILITY IMAP4rev1 ID LOGIN-REFERRALS AUTH=PLAIN IDLE ENABLE LITERAL+ SASL-IR\r\n')
                        response = f'{tag} OK CAPABILITY completed\r\n'
                        client_socket.send(response.encode())
                    
                    # ID命令处理 (RFC 2971 - IMAP4 ID extension)
                    elif cmd == 'ID':
                        add_service_log('邮件服务器', f'IMAP收到ID命令: {" ".join(parts[2:])}')
                        # 解析客户端ID信息（可选）
                        client_info = {}
                        if len(parts) > 2:
                            id_params = " ".join(parts[2:])
                            add_service_log('邮件服务器', f'客户端ID信息: {id_params}')
                            # 简单解析客户端信息
                            if 'name' in id_params and 'foxmail' in id_params.lower():
                                client_info['client'] = 'Foxmail'
                            elif 'name' in id_params and 'outlook' in id_params.lower():
                                client_info['client'] = 'Outlook'
                            else:
                                client_info['client'] = 'Unknown'
                        
                        # 返回服务器ID信息
                        server_id = '* ID ("name" "AutoTest-MailServer" "version" "1.0" "vendor" "AutoTest")\r\n'
                        client_socket.send(server_id.encode())
                        response = f'{tag} OK ID completed\r\n'
                        client_socket.send(response.encode())
                        add_service_log('邮件服务器', f'IMAP ID命令处理完成，客户端: {client_info.get("client", "Unknown")}')
                    
                    # LOGOUT命令处理
                    elif cmd == 'LOGOUT':
                        client_socket.send(b'* BYE IMAP4rev1 Server logging out\r\n')
                        response = f'{tag} OK LOGOUT completed\r\n'
                        client_socket.send(response.encode())
                        break
                    
                    # NOOP命令处理
                    elif cmd == 'NOOP':
                        response = f'{tag} OK NOOP completed\r\n'
                        client_socket.send(response.encode())
                    
                    # ENABLE命令处理 (RFC 5161)
                    elif cmd == 'ENABLE':
                        # 简单响应，不实际启用扩展
                        response = f'{tag} OK ENABLE completed\r\n'
                        client_socket.send(response.encode())
                        add_service_log('邮件服务器', f'IMAP ENABLE命令: {" ".join(parts[2:])}')
                    
                    # IDLE命令处理 (RFC 2177)
                    elif cmd == 'IDLE':
                        # 简单的IDLE支持
                        client_socket.send(b'+ idling\r\n')
                        add_service_log('邮件服务器', 'IMAP进入IDLE模式')
                        # 等待DONE命令或超时
                        try:
                            client_socket.settimeout(30)  # 30秒超时
                            done_data = client_socket.recv(1024)
                            if done_data and b'DONE' in done_data.upper():
                                response = f'{tag} OK IDLE terminated\r\n'
                                client_socket.send(response.encode())
                                add_service_log('邮件服务器', 'IMAP IDLE模式结束')
                        except socket.timeout:
                            response = f'{tag} OK IDLE terminated (timeout)\r\n'
                            client_socket.send(response.encode())
                            add_service_log('邮件服务器', 'IMAP IDLE超时结束')
                        finally:
                            client_socket.settimeout(None)
                    
                    # SEARCH命令处理
                    elif cmd == 'SEARCH':
                        # 获取当前用户的邮件列表
                        mail_list = self._get_user_mail_paths(conn_id)
                        mail_ids = ' '.join([str(i+1) for i in range(len(mail_list))])
                        search_result = f'* SEARCH {mail_ids}\r\n' if mail_ids else '* SEARCH\r\n'
                        client_socket.send(search_result.encode())
                        response = f'{tag} OK SEARCH completed\r\n'
                        client_socket.send(response.encode())
                        add_service_log('邮件服务器', f'IMAP SEARCH命令: {" ".join(parts[2:])}, 找到 {len(mail_list)} 封邮件')
                    
                    # FETCH命令处理
                    elif cmd == 'FETCH':
                        if len(parts) >= 4:
                            try:
                                # 解析FETCH参数
                                msg_set = parts[2]  # 邮件序号或范围
                                fetch_items = ' '.join(parts[3:])  # FETCH项目
                                
                                # 获取当前用户的邮件列表
                                mail_list = self._get_user_mail_paths(conn_id)
                                
                                # 解析邮件序号
                                if ':' in msg_set:
                                    # 范围，如 "1:*"
                                    start, end = msg_set.split(':', 1)
                                    start_idx = int(start) - 1 if start.isdigit() else 0
                                    end_idx = len(mail_list) if end == '*' else int(end)
                                    msg_indices = list(range(start_idx, min(end_idx, len(mail_list))))
                                else:
                                    # 单个邮件序号
                                    msg_idx = int(msg_set) - 1
                                    msg_indices = [msg_idx] if 0 <= msg_idx < len(mail_list) else []
                                
                                # 为每个请求的邮件返回数据
                                for msg_idx in msg_indices:
                                    if msg_idx < len(mail_list):
                                        mail_file = mail_list[msg_idx]
                                        msg_num = msg_idx + 1
                                        
                                        # 读取邮件内容
                                        try:
                                            with open(mail_file, 'r', encoding='utf-8') as f:
                                                mail_content = f.read()
                                            
                                            # 解析邮件头
                                            import email
                                            from email.header import decode_header
                                            msg = email.message_from_string(mail_content)
                                            
                                            # 解码邮件主题
                                            subject_raw = msg.get('Subject', '无主题')
                                            subject = self._decode_mime_header(subject_raw)
                                            
                                            # 解码发件人
                                            from_raw = msg.get('From', '未知发件人')
                                            from_addr = self._decode_mime_header(from_raw)
                                            
                                            date = msg.get('Date', '未知日期')
                                            
                                            # 构建FETCH响应
                                            if 'ENVELOPE' in fetch_items.upper():
                                                # 返回信封信息
                                                envelope = f'ENVELOPE ("{date}" "{subject}" (("{from_addr}" NIL "test" "autotest.com")) (("{from_addr}" NIL "test" "autotest.com")) (("{from_addr}" NIL "test" "autotest.com")) NIL NIL NIL NIL NIL)'
                                                fetch_response = f'* {msg_num} FETCH ({envelope})\r\n'
                                            elif 'BODY[]' in fetch_items.upper() or 'RFC822' in fetch_items.upper():
                                                # 返回完整邮件内容
                                                content_len = len(mail_content.encode('utf-8'))
                                                fetch_response = f'* {msg_num} FETCH (RFC822 {{{content_len}}}\r\n{mail_content})\r\n'
                                            else:
                                                # 返回基本信息
                                                flags = '(\\Seen)'
                                                size = len(mail_content.encode('utf-8'))
                                                fetch_response = f'* {msg_num} FETCH (FLAGS {flags} RFC822.SIZE {size})\r\n'
                                            
                                            client_socket.send(fetch_response.encode())
                                            add_service_log('邮件服务器', f'IMAP FETCH: 返回邮件 {msg_num} - {subject}')
                                            
                                        except Exception as e:
                                            add_service_log('邮件服务器', f'IMAP FETCH读取邮件失败: {str(e)}', 'error')
                                
                                response = f'{tag} OK FETCH completed\r\n'
                                client_socket.send(response.encode())
                                add_service_log('邮件服务器', f'IMAP FETCH命令完成: {msg_set} {fetch_items}')
                                
                            except Exception as e:
                                response = f'{tag} BAD FETCH command error: {str(e)}\r\n'
                                client_socket.send(response.encode())
                                add_service_log('邮件服务器', f'IMAP FETCH命令错误: {str(e)}', 'error')
                        else:
                            response = f'{tag} BAD FETCH command syntax error\r\n'
                            client_socket.send(response.encode())
                    
                    # UID命令处理
                    elif cmd == 'UID':
                        # 处理UID SEARCH, UID FETCH等
                        if len(parts) >= 3:
                            uid_cmd = parts[2].upper()
                            if uid_cmd == 'SEARCH':
                                # 获取当前用户的邮件列表
                                mail_list = self._get_user_mail_paths(conn_id)
                                # UID通常从1开始，与邮件序号相同
                                mail_uids = ' '.join([str(i+1) for i in range(len(mail_list))])
                                search_result = f'* SEARCH {mail_uids}\r\n' if mail_uids else '* SEARCH\r\n'
                                client_socket.send(search_result.encode())
                                response = f'{tag} OK UID SEARCH completed\r\n'
                                client_socket.send(response.encode())
                                add_service_log('邮件服务器', f'IMAP UID SEARCH命令: {" ".join(parts[3:])}, 找到 {len(mail_list)} 封邮件')
                            elif uid_cmd == 'FETCH':
                                if len(parts) >= 5:
                                    try:
                                        # UID FETCH的处理与FETCH类似，但使用UID
                                        uid_set = parts[3]
                                        fetch_items = ' '.join(parts[4:])
                                        
                                        # 获取当前用户的邮件列表
                                        mail_list = self._get_user_mail_paths(conn_id)
                                        
                                        # 解析UID（在这个简单实现中，UID等于序号）
                                        if ':' in uid_set:
                                            start, end = uid_set.split(':', 1)
                                            start_idx = int(start) - 1 if start.isdigit() else 0
                                            end_idx = len(mail_list) if end == '*' else int(end)
                                            msg_indices = list(range(start_idx, min(end_idx, len(mail_list))))
                                        else:
                                            uid_idx = int(uid_set) - 1
                                            msg_indices = [uid_idx] if 0 <= uid_idx < len(mail_list) else []
                                        
                                        # 为每个请求的邮件返回数据
                                        for msg_idx in msg_indices:
                                            if msg_idx < len(mail_list):
                                                mail_file = mail_list[msg_idx]
                                                uid = msg_idx + 1
                                                
                                                try:
                                                    with open(mail_file, 'r', encoding='utf-8') as f:
                                                        mail_content = f.read()
                                                    
                                                    import email
                                                    from email.header import decode_header
                                                    msg = email.message_from_string(mail_content)
                                                    
                                                    # 解码邮件主题
                                                    subject_raw = msg.get('Subject', '无主题')
                                                    subject = self._decode_mime_header(subject_raw)
                                                    
                                                    # 返回UID FETCH响应
                                                    if 'ENVELOPE' in fetch_items.upper():
                                                        from_raw = msg.get('From', '未知发件人')
                                                        from_addr = self._decode_mime_header(from_raw)
                                                        date = msg.get('Date', '未知日期')
                                                        envelope = f'ENVELOPE ("{date}" "{subject}" (("{from_addr}" NIL "test" "autotest.com")) (("{from_addr}" NIL "test" "autotest.com")) (("{from_addr}" NIL "test" "autotest.com")) NIL NIL NIL NIL NIL)'
                                                        fetch_response = f'* {uid} FETCH (UID {uid} {envelope})\r\n'
                                                    else:
                                                        flags = '(\\Seen)'
                                                        size = len(mail_content.encode('utf-8'))
                                                        fetch_response = f'* {uid} FETCH (UID {uid} FLAGS {flags} RFC822.SIZE {size})\r\n'
                                                    
                                                    client_socket.send(fetch_response.encode())
                                                    add_service_log('邮件服务器', f'IMAP UID FETCH: 返回邮件 UID {uid} - {subject}')
                                                    
                                                except Exception as e:
                                                    add_service_log('邮件服务器', f'IMAP UID FETCH读取邮件失败: {str(e)}', 'error')
                                        
                                        response = f'{tag} OK UID FETCH completed\r\n'
                                        client_socket.send(response.encode())
                                        add_service_log('邮件服务器', f'IMAP UID FETCH命令完成: {uid_set} {fetch_items}')
                                        
                                    except Exception as e:
                                        response = f'{tag} BAD UID FETCH command error: {str(e)}\r\n'
                                        client_socket.send(response.encode())
                                        add_service_log('邮件服务器', f'IMAP UID FETCH命令错误: {str(e)}', 'error')
                                else:
                                    response = f'{tag} BAD UID FETCH command syntax error\r\n'
                                    client_socket.send(response.encode())
                            else:
                                response = f'{tag} BAD UID command not recognized\r\n'
                                client_socket.send(response.encode())
                        else:
                            response = f'{tag} BAD UID command syntax error\r\n'
                            client_socket.send(response.encode())
                    
                    # 未知命令
                    else:
                        response = f'{tag} BAD Command not recognized\r\n'
                        client_socket.send(response.encode())
                        add_service_log('邮件服务器', f'IMAP未知命令: {command}')
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('邮件服务器', f'IMAP处理错误: {e}', 'error')
                    break
                    
        except Exception as e:
            add_service_log('邮件服务器', f'IMAP客户端处理失败: {e}', 'error')
        finally:
            try:
                client_socket.close()
            except:
                pass
            with service_lock:
                if conn_id in self.state['connections']:
                    del self.state['connections'][conn_id]

    def _handle_smtp_client(self, client_socket, addr, conn_id):
        """处理SMTP客户端连接"""
        try:
            add_service_log('邮件服务器', f'SMTP客户端连接: {addr[0]}:{addr[1]}')
            
            # 发送欢迎消息
            client_socket.send(b'220 ' + self.domain.encode() + b' SMTP Service Ready\r\n')
            
            current_sender = None
            recipients = []
            
            while not self.stop_event.is_set():
                try:
                    data = client_socket.recv(1024)
                    if not data:
                        break
                        
                    command = data.decode('utf-8', errors='ignore').strip()
                    add_service_log('邮件服务器', f'SMTP命令: {command}')
                    
                    with service_lock:
                        if conn_id in self.state['connections']:
                            self.state['connections'][conn_id]['commands'] += 1
                    
                    # 简单的SMTP命令处理
                    if command.upper().startswith('HELO'):
                        client_socket.send(b'250 Hello\r\n')
                    elif command.upper().startswith('EHLO'):
                        # EHLO响应需要支持扩展
                        response = f'250-{self.domain} Hello\r\n'
                        response += '250-AUTH PLAIN LOGIN\r\n'  # 支持认证
                        response += '250-SIZE 10240000\r\n'     # 支持大小限制
                        response += '250 HELP\r\n'              # 最后一行
                        client_socket.send(response.encode())
                    elif command.upper().startswith('AUTH'):
                        # 处理认证命令
                        auth_parts = command.split()
                        if len(auth_parts) >= 2:
                            auth_method = auth_parts[1].upper()
                            if auth_method in ['PLAIN', 'LOGIN']:
                                # 简单认证：接受任何认证
                                client_socket.send(b'235 Authentication successful\r\n')
                                add_service_log('邮件服务器', f'SMTP认证成功: {command}')
                            else:
                                client_socket.send(b'504 Authentication method not supported\r\n')
                        else:
                            client_socket.send(b'501 Syntax error in parameters\r\n')
                    elif command.upper().startswith('MAIL FROM:'):
                        # 提取发件人地址
                        try:
                            sender_part = command[10:].strip()  # 去掉 "MAIL FROM:"
                            if sender_part.startswith('<') and sender_part.endswith('>'):
                                current_sender = sender_part[1:-1]
                            else:
                                current_sender = sender_part
                            client_socket.send(b'250 OK\r\n')
                            add_service_log('邮件服务器', f'SMTP发件人: {current_sender}')
                        except:
                            client_socket.send(b'501 Syntax error in parameters\r\n')
                    elif command.upper().startswith('RCPT TO:'):
                        # 提取收件人地址
                        try:
                            recipient_part = command[8:].strip()  # 去掉 "RCPT TO:"
                            if recipient_part.startswith('<') and recipient_part.endswith('>'):
                                recipient = recipient_part[1:-1]
                            else:
                                recipient = recipient_part
                            recipients.append(recipient)
                            client_socket.send(b'250 OK\r\n')
                            add_service_log('邮件服务器', f'SMTP收件人: {recipient}')
                        except:
                            client_socket.send(b'501 Syntax error in parameters\r\n')
                    elif command.upper().startswith('DATA'):
                        if current_sender and recipients:
                            client_socket.send(b'354 Start mail input; end with <CRLF>.<CRLF>\r\n')
                            # 接收邮件数据
                            mail_data = b''
                            while True:
                                line = client_socket.recv(1024)
                                if not line:
                                    break
                                mail_data += line
                                if mail_data.endswith(b'\r\n.\r\n'):
                                    mail_data = mail_data[:-5]  # 去掉结束标记
                                    break
                            
                            # 处理邮件内容
                            mail_content = mail_data.decode('utf-8', errors='ignore')
                            
                            # 解析主题
                            subject = '无主题'
                            for line in mail_content.split('\n'):
                                if line.lower().startswith('subject:'):
                                    subject = line[8:].strip()
                                    break
                            
                            # 保存邮件到收件人邮箱
                            self._save_mail(current_sender, recipients, subject, mail_content, mail_data)
                            
                            client_socket.send(b'250 OK: Message accepted for delivery\r\n')
                            add_service_log('邮件服务器', f'✅ 邮件已接收并存储: {current_sender} -> {", ".join(recipients)} ({subject})')
                        else:
                            client_socket.send(b'503 Bad sequence of commands\r\n')
                        
                        # 重置状态
                        current_sender = None
                        recipients = []
                    elif command.upper().startswith('RSET'):
                        current_sender = None
                        recipients = []
                        client_socket.send(b'250 OK\r\n')
                    elif command.upper().startswith('NOOP'):
                        client_socket.send(b'250 OK\r\n')
                    elif command.upper().startswith('QUIT'):
                        client_socket.send(b'221 Bye\r\n')
                        break
                    else:
                        client_socket.send(b'502 Command not implemented\r\n')
                        
                except socket.timeout:
                    continue
                except Exception as e:
                    add_service_log('邮件服务器', f'SMTP处理错误: {e}', 'error')
                    break
                    
        except Exception as e:
            add_service_log('邮件服务器', f'SMTP客户端处理失败: {e}', 'error')
        finally:
            try:
                client_socket.close()
            except:
                pass
            with service_lock:
                if conn_id in self.state['connections']:
                    del self.state['connections'][conn_id]

    def stop(self):
        self.stop_event.set()
        
        # 停止SMTP服务器
        if hasattr(self, 'smtp_server') and self.smtp_server:
            try:
                self.smtp_server.close()
            except:
                pass
        
        # 停止IMAP服务器
        if hasattr(self, 'imap_socket') and self.imap_socket:
            try:
                self.imap_socket.close()
            except:
                pass
        
        # 停止POP3服务器
        if hasattr(self, 'pop3_socket') and self.pop3_socket:
            try:
                self.pop3_socket.close()
            except:
                pass
        
        add_service_log('邮件服务器', f'🛑 邮件服务器已停止', 'info')


def start_listener(protocol, host, port, **kwargs):
    host = host or '0.0.0.0'
    protocol = protocol.lower()

    # 邮件协议需要验证三个端口
    if protocol == 'mail':
        smtp_port = kwargs.get('smtp_port', 25)
        imap_port = kwargs.get('imap_port', 143)
        pop3_port = kwargs.get('pop3_port', 110)
        if not smtp_port or smtp_port < 1 or smtp_port > 65535:
            return False, 'SMTP端口无效'
        if not imap_port or imap_port < 1 or imap_port > 65535:
            return False, 'IMAP端口无效'
        if not pop3_port or pop3_port < 1 or pop3_port > 65535:
            return False, 'POP3端口无效'
    else:
        # 其他协议验证单一端口
        if not port or port < 1 or port > 65535:
            return False, '端口无效'
    if protocol not in listener_states:
        return False, '不支持的监听协议'

    # 在获取锁之前准备目录（避免阻塞其他服务）
    import os
    import platform
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ftp_directory = None
    http_directory = None

    if protocol == 'ftp':
        directory = kwargs.get('directory', '')
        # 过滤无效的目录值（空字符串、'/'、空白等）
        if directory and directory.strip() and directory != '/':
            if not os.path.isabs(directory):
                directory = os.path.abspath(directory)
            ftp_directory = directory
        else:
            # 默认目录：Windows 使用 C:\packet_agent，Linux 使用 /opt/packet_agent
            if platform.system() == 'Windows':
                ftp_directory = 'C:\\packet_agent'
            else:
                ftp_directory = '/opt/packet_agent'
        try:
            os.makedirs(ftp_directory, exist_ok=True)
        except Exception as e:
            return False, f'无法创建目录: {e}'
        add_service_log('FTP服务器', f'FTP根目录设置为: {ftp_directory}', 'info')

    if protocol == 'http':
        directory = kwargs.get('directory', '')
        # 过滤无效的目录值
        if directory and directory.strip() and directory != '/':
            if not os.path.isabs(directory):
                directory = os.path.abspath(directory)
            http_directory = directory
        else:
            # 默认目录：Windows 使用 C:\packet_agent\http，Linux 使用 /opt/packet_agent/http
            if platform.system() == 'Windows':
                http_directory = 'C:\\packet_agent\\http'
            else:
                http_directory = '/opt/packet_agent/http'
        try:
            os.makedirs(http_directory, exist_ok=True)
        except Exception as e:
            return False, f'无法创建目录: {e}'
        add_service_log('HTTP服务器', f'HTTP根目录设置为: {http_directory}', 'info')

    with service_lock:
        state = listener_states.get(protocol)
        if state and state.get('running'):
            return False, f'{protocol.upper()}监听已在运行'
        state = {
            'protocol': protocol,
            'host': host,
            'port': port,
            'running': True,
            'connections': {},
            'packets': 0
        }
        # FTP服务器特殊配置
        if protocol == 'ftp':
            state['username'] = kwargs.get('username', 'tdhx')
            state['password'] = kwargs.get('password', 'tdhx@2017')
            state['directory'] = ftp_directory
        # HTTP服务器特殊配置
        if protocol == 'http':
            state['directory'] = http_directory
        # 邮件服务器特殊配置
        if protocol == 'mail':
            state['smtp_port'] = kwargs.get('smtp_port', 25)
            state['imap_port'] = kwargs.get('imap_port', 143)
            state['pop3_port'] = kwargs.get('pop3_port', 110)
            state['domain'] = kwargs.get('domain', 'autotest.com')
            state['ssl_enabled'] = kwargs.get('ssl_enabled', False)
            state['accounts'] = kwargs.get('accounts', [])
        listener_states[protocol] = state
    if protocol == 'tcp':
        thread = TCPListenerThread(state)
    elif protocol == 'udp':
        thread = UDPListenerThread(state)
    elif protocol == 'ftp':
        thread = SimpleFTPServerThread(state)
    elif protocol == 'http':
        thread = HTTPListenerThread(state)
    elif protocol == 'mail':
        thread = MailListenerThread(state)
    else:
        return False, '不支持的协议'
    state['thread'] = thread
    thread.start()
    return True, {'message': f'{protocol.upper()}监听已启动'}


def stop_listener(protocol):
    protocol = protocol.lower()
    with service_lock:
        state = listener_states.get(protocol)
        if not state or not state.get('running'):
            return False, '监听未在运行'
        thread = state.get('thread')
    if thread:
        thread.stop()
    with service_lock:
        listener_states[protocol] = {'running': False}
    add_service_log('服务监听', f'{protocol.upper()}监听已停止')
    return True, {'message': '监听已停止'}


def start_tcp_client(config):
    server_ip = config.get('server_ip')
    server_port = int(config.get('server_port', 0))
    if not server_ip or server_port <= 0:
        return False, '服务器地址或端口无效'
    connections = max(1, int(config.get('connections', 1)))
    connect_rate = max(float(config.get('connect_rate', 1)), 0.1)
    interval = max(float(config.get('send_interval', 1)), 0.1)
    message = config.get('message', '')
    with service_lock:
        state = client_states.get('tcp')
        if state and state.get('running'):
            return False, 'TCP客户端已在运行'
        state = {
            'protocol': 'tcp',
            'server_ip': server_ip,
            'server_port': server_port,
            'connections': {},
            'running': True,
            'message': message,
            'send_interval': interval
        }
        client_states['tcp'] = state
    manager = TCPClientManager(state, {
        'server_ip': server_ip,
        'server_port': server_port,
        'connections': connections,
        'connect_rate': connect_rate,
        'message': message,
        'send_interval': interval
    })
    state['manager'] = manager
    manager.start()
    return True, {'message': 'TCP客户端已启动'}


def connect_tcp_client(config):
    """只建立连接，不发送数据"""
    server_ip = config.get('server_ip')
    server_port = int(config.get('server_port', 0))
    if not server_ip or server_port <= 0:
        return False, '服务器地址或端口无效'
    connections = max(1, int(config.get('connections', 1)))
    connect_rate = max(float(config.get('connect_rate', 1)), 0.1)
    interval = max(float(config.get('send_interval', 1)), 0.1)
    message = config.get('message', '')
    with service_lock:
        state = client_states.get('tcp')
        if state and state.get('running'):
            return False, 'TCP客户端已在运行'
        state = {
            'protocol': 'tcp',
            'server_ip': server_ip,
            'server_port': server_port,
            'connections': {},
            'running': True,
            'message': message,
            'send_interval': interval
        }
        client_states['tcp'] = state
    manager = TCPClientManager(state, {
        'server_ip': server_ip,
        'server_port': server_port,
        'connections': connections,
        'connect_rate': connect_rate,
        'message': message,
        'send_interval': interval,
        'src_mac': config.get('src_mac', ''),
        'dst_mac': config.get('dst_mac', ''),
        'use_local_address': config.get('use_local_address', False),
        'local_address': config.get('local_address', '')
    })
    state['manager'] = manager
    manager.connect()
    return True, {'message': 'TCP客户端已连接'}


def start_tcp_send(config):
    """开始发送数据"""
    with service_lock:
        state = client_states.get('tcp')
    if not state or not state.get('running'):
        return False, 'TCP客户端未连接'
    manager = state.get('manager')
    if not manager:
        return False, '连接管理器不存在'
    # 更新发送内容
    if 'message' in config:
        state['message'] = config['message']
        manager.message = config['message']
    success, message = manager.start_send()
    if success:
        return True, {'message': message}
    else:
        return False, message


def stop_tcp_send():
    """停止发送数据"""
    with service_lock:
        state = client_states.get('tcp')
    if not state or not state.get('running'):
        return False, 'TCP客户端未运行'
    manager = state.get('manager')
    if not manager:
        return False, '连接管理器不存在'
    success, message = manager.stop_send()
    if success:
        return True, {'message': message}
    else:
        return False, message


def stop_tcp_client():
    with service_lock:
        state = client_states.get('tcp')
    if not state or not state.get('running'):
        return False, 'TCP客户端未运行'
    manager = state.get('manager')
    if manager:
        manager.stop()
    with service_lock:
        client_states['tcp'] = {'running': False}
    return True, {'message': 'TCP客户端已停止'}


def disconnect_tcp_connection(conn_id=None):
    with service_lock:
        state = client_states.get('tcp')
    if not state or not state.get('running'):
        return False, 'TCP客户端未运行'
    manager = state.get('manager')
    if not manager:
        return False, '连接管理器不存在'
    return manager.disconnect(conn_id)


def start_udp_client(config):
    server_ip = config.get('server_ip')
    server_port = int(config.get('server_port', 0))
    if not server_ip or server_port <= 0:
        return False, '服务器地址或端口无效'
    connections = max(1, int(config.get('connections', 1)))
    interval = max(float(config.get('send_interval', 1)), 0.1)
    message = config.get('message', '')
    with service_lock:
        state = client_states.get('udp')
        if state and state.get('running'):
            return False, 'UDP客户端已在运行'
        state = {
            'protocol': 'udp',
            'server_ip': server_ip,
            'server_port': server_port,
            'connections': {},
            'running': True,
            'message': message,
            'send_interval': interval
        }
        client_states['udp'] = state
    manager = UDPClientManager(state, {
        'server_ip': server_ip,
        'server_port': server_port,
        'connections': connections,
        'send_interval': interval,
        'message': message
    })
    state['manager'] = manager
    manager.start()
    return True, {'message': 'UDP客户端已启动'}


def stop_udp_client():
    with service_lock:
        state = client_states.get('udp')
    if not state or not state.get('running'):
        return False, 'UDP客户端未运行'
    manager = state.get('manager')
    if manager:
        manager.stop()
        manager.join(timeout=5)
    with service_lock:
        client_states['udp'] = {'running': False}
    return True, {'message': 'UDP客户端已停止'}


def connect_ftp_client(config):
    """连接FTP服务器"""
    server_ip = config.get('server_ip')
    server_port = int(config.get('server_port', 21))
    if not server_ip or server_port <= 0:
        return False, 'FTP服务器地址或端口无效'
    with service_lock:
        state = client_states.get('ftp')
        if state and state.get('running'):
            return False, 'FTP客户端已连接'
        state = {
            'protocol': 'ftp',
            'server_ip': server_ip,
            'server_port': server_port,
            'running': False,
            'current_dir': '/',
            'file_list': ''
        }
        client_states['ftp'] = state
    worker = FTPClientWorker(state, config)
    state['worker'] = worker
    success, message = worker.connect()
    if success:
        # 连接成功后，延迟在后台线程中获取文件列表，给连接一些稳定时间
        def get_file_list_async():
            import time
            # 延迟1秒，确保连接稳定
            time.sleep(1)
            try:
                # 检查连接是否仍然有效
                if worker.connected and worker.ftp:
                    worker.list_files()
            except Exception as e:
                add_service_log('FTP客户端', f'获取文件列表失败: {e}', 'error')
        
        # 启动后台线程获取文件列表
        import threading
        threading.Thread(target=get_file_list_async, daemon=True).start()
        
        return True, {
            'message': message, 
            'current_dir': state.get('current_dir', '/'),
            'file_list': state.get('file_list', '')
        }
    else:
        with service_lock:
            # 清理失败的连接状态
            if client_states.get('ftp') == state:
                client_states['ftp'] = {'running': False}
        return False, {'error': message}


def disconnect_ftp_client():
    """断开FTP连接"""
    with service_lock:
        state = client_states.get('ftp')
    if not state:
        return False, 'FTP客户端未连接'
    worker = state.get('worker')
    if worker:
        success, message = worker.disconnect()
        if success:
            with service_lock:
                client_states['ftp'] = {'running': False}
            return True, {'message': message}
        else:
            return False, message
    return False, 'FTP客户端工作器不存在'


def list_ftp_files():
    """获取FTP文件列表"""
    with service_lock:
        state = client_states.get('ftp')
    if not state or not state.get('running'):
        return False, 'FTP客户端未连接'
    worker = state.get('worker')
    if not worker:
        return False, 'FTP客户端工作器不存在'
    return worker.list_files()


def upload_ftp_file(filename, content=None, local_file_path=None):
    """上传文件到FTP服务器"""
    with service_lock:
        state = client_states.get('ftp')
    if not state or not state.get('running'):
        return False, 'FTP客户端未连接'
    worker = state.get('worker')
    if not worker:
        return False, 'FTP客户端工作器不存在'
    
    # 如果提供了本地文件路径，从文件读取内容
    if local_file_path:
        try:
            import os
            # 如果是相对路径，使用脚本所在目录
            if not os.path.isabs(local_file_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                local_file_path = os.path.join(script_dir, local_file_path)
            
            if not os.path.exists(local_file_path):
                return False, f'本地文件不存在: {local_file_path}'
            if not os.path.isfile(local_file_path):
                return False, f'不是文件: {local_file_path}'
            
            # 读取文件内容（二进制模式，支持文本和二进制文件）
            with open(local_file_path, 'rb') as f:
                content = f.read()
        except Exception as e:
            return False, f'读取本地文件失败: {e}'
    
    if content is None:
        return False, '缺少文件内容'
    
    # 如果content是bytes，直接传递；如果是字符串，需要编码
    if isinstance(content, str):
        content = content.encode('utf-8')
    
    return worker.upload_file(filename, content)


def download_ftp_file(filename):
    """从FTP服务器下载文件"""
    with service_lock:
        state = client_states.get('ftp')
    if not state or not state.get('running'):
        return False, 'FTP客户端未连接'
    worker = state.get('worker')
    if not worker:
        return False, 'FTP客户端工作器不存在'
    return worker.download_file(filename)


def get_local_file_list(directory=None):
    """获取客户端本地文件列表"""
    try:
        import os
        if directory is None or directory == '':
            # 默认使用脚本所在目录
            directory = os.path.dirname(os.path.abspath(__file__))
        
        if not os.path.exists(directory):
            return False, f'目录不存在: {directory}'
        if not os.path.isdir(directory):
            return False, f'不是目录: {directory}'
        
        files = []
        for item in os.listdir(directory):
            item_path = os.path.join(directory, item)
            try:
                stat_info = os.stat(item_path)
                files.append({
                    'name': item,
                    'is_dir': os.path.isdir(item_path),
                    'size': stat_info.st_size,
                    'modified': stat_info.st_mtime
                })
            except:
                pass
        
        return True, {'files': files, 'directory': directory}
    except Exception as e:
        return False, str(e)


def start_ftp_client(config):
    """兼容旧接口：连接FTP服务器"""
    return connect_ftp_client(config)


def stop_ftp_client():
    """兼容旧接口：断开FTP连接"""
    return disconnect_ftp_client()


class HTTPClientWorker:
    def __init__(self, state, config):
        self.state = state
        self.server_ip = config['server_ip']
        self.server_port = config['server_port']
        self.stop_event = threading.Event()
        self.connected = False

    def connect(self):
        """连接HTTP服务器（测试连接）"""
        try:
            import urllib.request
            import urllib.error
            url = f'http://{self.server_ip}:{self.server_port}/'
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'HTTPClient/1.0')
            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.connected = True
                    with service_lock:
                        self.state['running'] = True
                    add_service_log('HTTP客户端', f'连接成功: {self.server_ip}:{self.server_port}')
                    return True, 'HTTP连接成功'
            except urllib.error.HTTPError as e:
                # HTTP错误（如404）也算连接成功，只是路径不存在
                if e.code in [200, 404, 403]:
                    self.connected = True
                    with service_lock:
                        self.state['running'] = True
                    add_service_log('HTTP客户端', f'连接成功: {self.server_ip}:{self.server_port}')
                    return True, 'HTTP连接成功'
                else:
                    raise
        except Exception as e:
            add_service_log('HTTP客户端', f'连接失败: {e}', 'error')
            return False, str(e)

    def list_files(self):
        """获取文件列表"""
        if not self.connected:
            return False, '未连接'
        try:
            import urllib.request
            import urllib.error
            import json
            url = f'http://{self.server_ip}:{self.server_port}/'
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'HTTPClient/1.0')
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                # 解析HTML文件列表
                import re
                files = []
                # 匹配文件列表项
                pattern = r'<tr>.*?<td>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?</td>.*?<td>([^<]+)</td>.*?<td>([^<]+)</td>.*?</tr>'
                matches = re.finditer(pattern, html, re.DOTALL)
                for match in matches:
                    href = match.group(1)
                    name = match.group(2).strip()
                    file_type = match.group(3).strip()
                    size = match.group(4).strip()
                    if name and name != '/':
                        # 移除路径开头的/
                        if name.startswith('/'):
                            name = name[1:]
                        if name.endswith('/'):
                            name = name[:-1]
                        # 解析文件大小（支持 "25.34 KB", "1.2 MB", "100 B" 等格式）
                        size_bytes = 0
                        if size and size != '-':
                            size_parts = size.split()
                            if len(size_parts) >= 2:
                                try:
                                    size_value = float(size_parts[0])
                                    size_unit = size_parts[1].upper()
                                    if size_unit.startswith('B'):
                                        size_bytes = int(size_value)
                                    elif size_unit.startswith('K'):
                                        size_bytes = int(size_value * 1024)
                                    elif size_unit.startswith('M'):
                                        size_bytes = int(size_value * 1024 * 1024)
                                    elif size_unit.startswith('G'):
                                        size_bytes = int(size_value * 1024 * 1024 * 1024)
                                except (ValueError, IndexError):
                                    size_bytes = 0
                        files.append({
                            'name': name,
                            'is_dir': file_type == '目录',
                            'size': size_bytes
                        })
                with service_lock:
                    self.state['file_list'] = files
                add_service_log('HTTP客户端', f'获取文件列表成功: {len(files)} 项')
                return True, files
        except Exception as e:
            add_service_log('HTTP客户端', f'获取文件列表失败: {e}', 'error')
            return False, str(e)

    def download_file(self, filename):
        """下载文件"""
        if not self.connected:
            return False, '未连接'
        try:
            import urllib.request
            import urllib.parse
            import urllib.error
            # 对文件名进行URL编码，处理空格和中文字符
            encoded_filename = urllib.parse.quote(filename, safe='')
            url = f'http://{self.server_ip}:{self.server_port}/{encoded_filename}'
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'HTTPClient/1.0')
            with urllib.request.urlopen(req, timeout=30) as response:
                file_size = int(response.headers.get('Content-Length', 0))
                # 读取文件内容但不保存（只用于测试下载）
                data = response.read()
                actual_size = len(data)
                add_service_log('HTTP客户端', f'下载完成: {filename} (大小: {actual_size} 字节)')
                return True, {'filename': filename, 'file_size': actual_size}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                add_service_log('HTTP客户端', f'文件不存在: {filename}', 'error')
                return False, '文件不存在'
            else:
                add_service_log('HTTP客户端', f'下载失败: {e}', 'error')
                return False, str(e)
        except Exception as e:
            add_service_log('HTTP客户端', f'下载失败: {e}', 'error')
            return False, str(e)

    def upload_file(self, filename, content):
        """上传文件（使用HTTP POST）"""
        if not self.connected:
            return False, '未连接'
        try:
            import urllib.request
            import urllib.parse
            import urllib.error

            # 支持二进制和文本内容
            if isinstance(content, str):
                content = content.encode('utf-8')

            # 对文件名进行URL编码，处理空格和中文字符
            encoded_filename = urllib.parse.quote(filename, safe='')
            url = f'http://{self.server_ip}:{self.server_port}/{encoded_filename}'
            req = urllib.request.Request(url, data=content, method='POST')
            req.add_header('User-Agent', 'HTTPClient/1.0')
            req.add_header('Content-Type', 'application/octet-stream')
            req.add_header('Content-Length', str(len(content)))

            with urllib.request.urlopen(req, timeout=30) as response:
                response_data = response.read()
                add_service_log('HTTP客户端', f'上传完成: {filename} (大小: {len(content)} 字节)')
                # 上传成功后自动刷新文件列表
                import threading
                import time
                def refresh_list():
                    try:
                        time.sleep(0.5)
                        if self.connected:
                            self.list_files()
                    except:
                        pass
                threading.Thread(target=refresh_list, daemon=True).start()
                return True, f'上传成功: {filename}'
        except urllib.error.HTTPError as e:
            add_service_log('HTTP客户端', f'上传失败: {e}', 'error')
            return False, str(e)
        except Exception as e:
            add_service_log('HTTP客户端', f'上传失败: {e}', 'error')
            return False, str(e)

    def cd_dir(self, dirname):
        """切换目录"""
        if not self.connected:
            return False, '未连接'
        try:
            import urllib.request
            import urllib.parse
            import urllib.error

            # 处理目录切换逻辑
            if dirname == '..':
                # 返回上级目录 - 需要从当前 URL 路径中移除最后一段
                current_path = self.state.get('current_dir', '/')
                if current_path == '/' or current_path == '':
                    # 已经在根目录
                    new_path = '/'
                else:
                    # 移除最后一段路径
                    new_path = current_path.rstrip('/')
                    if '/' in new_path:
                        new_path = new_path.rsplit('/', 1)[0]
                        if new_path == '':
                            new_path = '/'
                    else:
                        new_path = '/'
            elif dirname == '/' or dirname == '':
                # 返回根目录
                new_path = '/'
            else:
                # 进入子目录 - 对目录名进行URL编码
                encoded_dirname = urllib.parse.quote(dirname, safe='')
                current_path = self.state.get('current_dir', '/')
                if current_path == '/' or current_path == '':
                    new_path = '/' + encoded_dirname
                else:
                    new_path = current_path.rstrip('/') + '/' + encoded_dirname

            # 测试新路径是否可访问
            url = f'http://{self.server_ip}:{self.server_port}{new_path}'
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'HTTPClient/1.0')
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    # 路径可访问
                    with service_lock:
                        self.state['current_dir'] = new_path
                    add_service_log('HTTP客户端', f'目录切换成功: {new_path}')
                    return True, {'current_dir': new_path}
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    add_service_log('HTTP客户端', f'目录不存在: {dirname}', 'error')
                    return False, '目录不存在'
                else:
                    # 其他错误（如403）可能目录存在但无权限，仍然更新路径
                    with service_lock:
                        self.state['current_dir'] = new_path
                    add_service_log('HTTP客户端', f'目录切换: {new_path} (权限受限)')
                    return True, {'current_dir': new_path}
        except Exception as e:
            add_service_log('HTTP客户端', f'切换目录失败: {e}', 'error')
            return False, str(e)

    def disconnect(self):
        """断开连接"""
        self.connected = False
        with service_lock:
            self.state['running'] = False
        add_service_log('HTTP客户端', '连接已断开')
        return True, '连接已断开'


def connect_http_client(config):
    """连接HTTP服务器"""
    server_ip = config.get('server_ip')
    server_port = int(config.get('server_port', 80))
    if not server_ip or server_port <= 0:
        return False, 'HTTP服务器地址或端口无效'
    with service_lock:
        state = client_states.get('http')
        if state and state.get('running'):
            return False, 'HTTP客户端已连接'
        state = {
            'protocol': 'http',
            'server_ip': server_ip,
            'server_port': server_port,
            'running': False,
            'file_list': []
        }
        client_states['http'] = state
    worker = HTTPClientWorker(state, config)
    state['worker'] = worker
    success, message = worker.connect()
    if success:
        # 连接成功后，延迟在后台线程中获取文件列表
        def get_file_list_async():
            import time
            time.sleep(1)
            try:
                if worker.connected:
                    worker.list_files()
            except Exception as e:
                add_service_log('HTTP客户端', f'获取文件列表失败: {e}', 'error')
        
        threading.Thread(target=get_file_list_async, daemon=True).start()
        
        return True, {
            'message': message,
            'file_list': state.get('file_list', [])
        }
    else:
        with service_lock:
            if client_states.get('http') == state:
                client_states['http'] = {'running': False}
        return False, {'error': message}


def disconnect_http_client():
    """断开HTTP连接"""
    with service_lock:
        state = client_states.get('http')
    if not state:
        return False, 'HTTP客户端未连接'
    worker = state.get('worker')
    if worker:
        worker.disconnect()
    with service_lock:
        client_states['http'] = {'running': False}
    return True, {'message': 'HTTP连接已断开'}


def upload_http_file(filename, content=None, local_file_path=None):
    """上传文件到HTTP服务器"""
    with service_lock:
        state = client_states.get('http')
    if not state or not state.get('running'):
        return False, 'HTTP客户端未连接'
    worker = state.get('worker')
    if not worker:
        return False, 'HTTP客户端工作器不存在'

    # 如果提供了本地文件路径，从文件读取内容
    if local_file_path:
        try:
            import os
            # 如果是相对路径，使用脚本所在目录
            if not os.path.isabs(local_file_path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                local_file_path = os.path.join(script_dir, local_file_path)

            if not os.path.exists(local_file_path):
                return False, f'本地文件不存在: {local_file_path}'
            if not os.path.isfile(local_file_path):
                return False, f'不是文件: {local_file_path}'

            # 读取文件内容（二进制模式）
            with open(local_file_path, 'rb') as f:
                content = f.read()
        except Exception as e:
            return False, f'读取本地文件失败: {e}'

    if content is None:
        return False, '缺少文件内容'

    # 如果content是字符串，需要编码
    if isinstance(content, str):
        content = content.encode('utf-8')

    return worker.upload_file(filename, content)


def send_mail_via_smtp(smtp_config, mail_data, source_ip=''):
    """通过SMTP发送邮件"""
    print("*** send_mail_via_smtp 函数被调用！***")
    add_service_log('邮件客户端', '*** send_mail_via_smtp 函数开始执行 ***', 'info')
    try:
        server = smtp_config.get('server', '').strip()
        port = int(smtp_config.get('port', 0))
        ssl = smtp_config.get('ssl', False)
        email = smtp_config.get('email', '').strip()
        password = smtp_config.get('password', '').strip()
        no_auth = smtp_config.get('no_auth', False)
        
        # 检查是否应该使用本地邮件存储
        # 只有明确勾选了"使用本地存储"才使用本地存储，否则都走真实的网络连接
        use_local_storage = smtp_config.get('use_local_storage', False)
        
        print(f"*** 服务器检测: server='{server}', use_local_storage={use_local_storage}, source_ip='{source_ip}' ***")
        add_service_log('邮件客户端', f'*** 服务器检测: server="{server}", use_local_storage={use_local_storage}, source_ip="{source_ip}" ***', 'info')
        
        if use_local_storage:
            # 直接保存到本地存储
            add_service_log('邮件客户端', '*** 使用本地邮件存储 ***', 'info')
            return send_mail_to_local_storage(mail_data)
        
        print(f"*** 解析配置: server={server}, port={port}, ssl={ssl}, no_auth={no_auth} ***")
        
        # 验证SMTP配置
        if not server or port <= 0:
            print(f"*** 配置验证失败: server={server}, port={port} ***")
            return False, 'SMTP服务器地址和端口不能为空'
        
        if not no_auth and (not email or not password):
            print(f"*** 认证验证失败: no_auth={no_auth}, email={email}, password={password} ***")
            return False, '非无认证模式下，邮箱地址和密码不能为空'
        
        # 验证邮件数据
        from_addr = mail_data.get('from', '').strip() or 'test1@test.com'  # 默认发件人
        to_addr = mail_data.get('to', '').strip() or 'test2@test.com'  # 默认收件人
        subject = mail_data.get('subject', '').strip()
        content = mail_data.get('content', '').strip()
        content_type = mail_data.get('content_type', 'plain')
        cc_addr = mail_data.get('cc', '').strip()
        
        print(f"*** 邮件数据: from={from_addr}, to={to_addr}, subject={subject}, content_len={len(content)} ***")
        
        if not to_addr or not subject or not content:
            print(f"*** 邮件数据验证失败: to_addr={to_addr}, subject={subject}, content={content} ***")
            return False, '收件人、主题和内容不能为空'
        
        print(f"*** 邮件数据验证通过，准备发送邮件 ***")
        add_service_log('邮件客户端', f'准备发送邮件: {to_addr} - {subject}', 'info')
        print(f"*** add_service_log 调用完成 ***")
        add_service_log('邮件客户端', f'*** SMTP服务器配置: {server}:{port}, SSL={ssl}, 无认证={no_auth} ***', 'info')
        print(f"*** SMTP配置日志记录完成 ***")
        
        # 构造邮件
        print(f"*** 开始导入邮件模块 ***")
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        from email.header import Header
        print(f"*** 邮件模块导入完成 ***")
        
        print(f"*** 开始构造邮件对象 ***")
        msg = MIMEMultipart()
        print(f"*** MIMEMultipart 创建完成 ***")
        
        msg['From'] = Header(from_addr, 'utf-8')
        print(f"*** From 头设置完成: {from_addr} ***")
        
        msg['To'] = Header(to_addr, 'utf-8')
        print(f"*** To 头设置完成: {to_addr} ***")
        
        if cc_addr:
            msg['Cc'] = Header(cc_addr, 'utf-8')
            print(f"*** Cc 头设置完成 ***")
            
        msg['Subject'] = Header(subject, 'utf-8')
        print(f"*** Subject 头设置完成 ***")
        
        # 添加邮件正文
        print(f"*** 开始添加邮件正文 ***")
        msg.attach(MIMEText(content, content_type, 'utf-8'))
        print(f"*** 邮件正文添加完成 ***")
        
        # 处理附件
        attachments = mail_data.get('attachments', [])
        if attachments:
            print(f"*** 开始处理 {len(attachments)} 个附件 ***")
            from email.mime.base import MIMEBase
            from email import encoders
            import base64
            
            for i, attachment in enumerate(attachments):
                try:
                    filename = attachment.get('filename', f'attachment_{i+1}')
                    content_data = attachment.get('content', '')
                    file_type = attachment.get('type', 'application/octet-stream')
                    
                    print(f"*** 处理附件 {i+1}: {filename} ({file_type}) ***")
                    
                    # 解码base64内容
                    file_data = base64.b64decode(content_data)
                    
                    # 创建附件对象
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(file_data)
                    encoders.encode_base64(part)
                    
                    # 设置附件头信息
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename="{filename}"'
                    )
                    
                    # 添加到邮件
                    msg.attach(part)
                    print(f"*** 附件 {filename} 添加完成 ***")
                    
                except Exception as e:
                    print(f"*** 附件处理失败 {i+1}: {str(e)} ***")
                    add_service_log('邮件客户端', f'附件处理失败 {filename}: {str(e)}', 'error')
            
            print(f"*** 所有附件处理完成 ***")
        else:
            print(f"*** 无附件需要处理 ***")
        
        # 连接SMTP服务器
        print(f"*** 开始导入smtplib ***")
        import smtplib
        print(f"*** smtplib 导入完成 ***")
        
        print(f"*** 开始连接SMTP服务器 ***")
        try:
            # 智能选择SMTP连接方式
            print(f"*** 检查连接方式: port={port}, ssl={ssl} ***")
            if port == 465 or (ssl and port != 587):
                # 端口465或明确要求SSL且不是587端口，使用SMTP_SSL
                print(f"*** 选择SMTP_SSL连接方式 ***")
                add_service_log('邮件客户端', f'使用SMTP_SSL连接: {server}:{port}', 'info')
                if source_ip:
                    print(f"*** 使用源IP地址: {source_ip} ***")
                    add_service_log('邮件客户端', f'绑定源地址: {source_ip}', 'info')
                    smtp_server = smtplib.SMTP_SSL(server, port, timeout=10, source_address=(source_ip, 0))
                else:
                    smtp_server = smtplib.SMTP_SSL(server, port, timeout=10)
                print(f"*** SMTP_SSL连接创建成功 ***")
            elif port == 587 or (ssl and port == 587):
                # 端口587，使用STARTTLS
                print(f"*** 选择SMTP+STARTTLS连接方式 ***")
                add_service_log('邮件客户端', f'使用SMTP+STARTTLS连接: {server}:{port}', 'info')
                if source_ip:
                    print(f"*** 使用源IP地址: {source_ip} ***")
                    add_service_log('邮件客户端', f'绑定源地址: {source_ip}', 'info')
                    smtp_server = smtplib.SMTP(server, port, timeout=10, source_address=(source_ip, 0))
                else:
                    smtp_server = smtplib.SMTP(server, port, timeout=10)
                print(f"*** SMTP连接创建成功，开始STARTTLS ***")
                smtp_server.starttls()
                print(f"*** STARTTLS完成 ***")
            else:
                # 其他情况，使用普通SMTP
                print(f"*** 选择普通SMTP连接方式 ***")
                add_service_log('邮件客户端', f'使用普通SMTP连接: {server}:{port}', 'info')
                print(f"*** 准备创建SMTP连接: {server}:{port} ***")
                if source_ip:
                    print(f"*** 使用源IP地址: {source_ip} ***")
                    add_service_log('邮件客户端', f'绑定源地址: {source_ip}', 'info')
                    smtp_server = smtplib.SMTP(server, port, timeout=10, source_address=(source_ip, 0))
                else:
                    smtp_server = smtplib.SMTP(server, port, timeout=10)
                print(f"*** 普通SMTP连接创建成功 ***")
            
            # 参考Foxmail流程：EHLO之后立即进行AUTH LOGIN认证（如果提供了凭据）
            # 标准流程：EHLO → AUTH LOGIN → 输入用户名/密码 → MAIL FROM → RCPT TO → DATA
            print(f"*** 检查是否需要认证: no_auth={no_auth}, email={email}, password={'***' if password else 'None'} ***")
            
            # 如果提供了email和password，无论no_auth设置如何，都主动进行认证（参考Foxmail流程）
            if email and password:
                print(f"*** 参考Foxmail流程：EHLO后立即进行AUTH LOGIN认证 ***")
                add_service_log('邮件客户端', f'参考Foxmail流程：EHLO后立即进行AUTH LOGIN认证', 'info')
                try:
                    smtp_server.login(email, password)
                    print(f"*** SMTP认证成功（AUTH LOGIN） ***")
                    add_service_log('邮件客户端', f'SMTP认证成功: {email}', 'info')
                except smtplib.SMTPAuthenticationError as e:
                    print(f"*** SMTP认证失败: {str(e)} ***")
                    add_service_log('邮件客户端', f'SMTP认证失败: {str(e)}', 'error')
                    return False, f'SMTP认证失败: {str(e)}'
                except smtplib.SMTPException as e:
                    # Server does not support AUTH extension
                    error_str = str(e).lower()
                    if 'auth' in error_str and ('not supported' in error_str or 'unsupported' in error_str):
                        print(f"*** SMTP server does not support AUTH, trying without auth ***")
                        add_service_log('youjiankehuduan', f'SMTP server does not support AUTH, using no-auth mode', 'warning')
                    else:
                        print(f"*** SMTP auth exception: {str(e)} ***")
                        add_service_log('youjiankehuduan', f'SMTP auth exception: {str(e)}', 'error')
                        return False, f'SMTP auth exception: {str(e)}'
            elif no_auth:
                print(f"*** 跳过SMTP认证（无认证模式，且未提供凭据） ***")
                add_service_log('邮件客户端', '跳过SMTP认证（无认证模式）', 'info')
            else:
                print(f"*** 警告：未提供认证凭据，但no_auth=False，可能发送失败 ***")
                add_service_log('邮件客户端', '警告：未提供认证凭据，但no_auth=False', 'warning')
            
            # 发送邮件（参考Foxmail流程：认证后直接发送 MAIL FROM → RCPT TO → DATA）
            print(f"*** 准备发送邮件（MAIL FROM → RCPT TO → DATA） ***")
            recipients = [to_addr]
            if cc_addr:
                recipients.append(cc_addr)
            print(f"*** 收件人列表: {recipients} ***")
            
            add_service_log('邮件客户端', f'*** 开始发送邮件到: {recipients} ***', 'info')
            print(f"*** 调用sendmail方法 ***")
            print(f"*** 发件人: {from_addr}, 收件人: {recipients} ***")
            
            # 发送邮件
            smtp_server.sendmail(from_addr, recipients, msg.as_string())
            print(f"*** sendmail调用成功 ***")
            
            print(f"*** 关闭SMTP连接 ***")
            smtp_server.quit()
            print(f"*** SMTP连接已关闭 ***")
            
            add_service_log('邮件客户端', f'*** 邮件发送成功: {to_addr} ***', 'info')
            print(f"*** 邮件发送流程完成 ***")
            return True, f'邮件发送成功 ({to_addr})'
            
        except smtplib.SMTPAuthenticationError as e:
            print(f"*** SMTP认证异常: {str(e)} ***")
            add_service_log('邮件客户端', f'SMTP认证失败: {str(e)}', 'error')
            return False, f'SMTP认证失败: {str(e)}'
        except smtplib.SMTPConnectError as e:
            print(f"*** SMTP连接异常: {str(e)} ***")
            add_service_log('邮件客户端', f'SMTP连接失败: {str(e)}', 'error')
            return False, f'SMTP连接失败: {str(e)}'
        except smtplib.SMTPRecipientsRefused as e:
            print(f"*** SMTP收件人异常: {str(e)} ***")
            add_service_log('邮件客户端', f'收件人被拒绝: {str(e)}', 'error')
            return False, f'收件人被拒绝: {str(e)}'
        except Exception as e:
            error_str = str(e)
            print(f"*** SMTP内层通用异常: {error_str} ***")
            add_service_log('邮件客户端', f'邮件发送异常: {error_str}', 'error')
            return False, f'邮件发送失败: {error_str}'
            
    except Exception as e:
        print(f"*** send_mail_via_smtp 外层异常: {str(e)} ***")
        add_service_log('邮件客户端', f'邮件发送异常: {str(e)}', 'error')
        return False, f'邮件发送失败: {str(e)}'


def decode_mime_header(header_value):
    """解码MIME编码的邮件头（独立函数版本）

    处理多种输入类型：
    - 字符串
    - Header对象（email.header.Header）
    - bytes
    """
    try:
        if not header_value:
            return header_value

        # 如果是Header对象，先转换为字符串
        from email.header import Header
        if isinstance(header_value, Header):
            header_value = str(header_value)

        # 如果已经是普通字符串且不包含编码标记，直接返回
        if isinstance(header_value, str) and not header_value.startswith('=?'):
            return header_value

        from email.header import decode_header
        decoded_parts = decode_header(header_value)

        result = ''
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                if encoding:
                    try:
                        result += part.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        # 如果指定编码失败，尝试UTF-8
                        try:
                            result += part.decode('utf-8')
                        except UnicodeDecodeError:
                            # 最后尝试忽略错误
                            result += part.decode('utf-8', errors='ignore')
                else:
                    # 没有指定编码，尝试UTF-8
                    try:
                        result += part.decode('utf-8')
                    except UnicodeDecodeError:
                        result += part.decode('utf-8', errors='ignore')
            else:
                # 已经是字符串
                result += part

        return result.strip()

    except Exception as e:
        add_service_log('邮件客户端', f'MIME头解码失败: {str(e)}', 'error')
        # 确保返回值是字符串（避免JSON序列化错误）
        if hasattr(header_value, '__str__'):
            return str(header_value)
        return header_value


def format_email_date(date_str):
    """格式化邮件日期为中文格式

    Args:
        date_str: 日期字符串，可能是：
            - 字符串格式的日期
            - '未知' 或空值
            - Header对象（会被转换为字符串）

    Returns:
        str: 格式化后的中文日期，如 "2025年12月2日 15:19:23"
            解析失败时返回 '未知'
    """
    try:
        # 确保输入是字符串
        if not date_str:
            return '未知'

        # 如果是Header对象或其他非字符串类型，转换为字符串
        if not isinstance(date_str, str):
            date_str = str(date_str)

        if date_str == '未知' or date_str.strip() == '':
            return '未知'

        from email.utils import parsedate_to_datetime
        from datetime import datetime

        # 解析邮件日期
        try:
            dt = parsedate_to_datetime(date_str)
        except:
            # 如果解析失败，尝试其他格式
            try:
                # 尝试直接解析常见格式
                dt = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
            except:
                return '未知'  # 解析失败，返回'未知'而非原始值

        # 格式化为中文日期时间
        # 格式：2025年12月2日 15:19:23
        chinese_date = dt.strftime('%Y年%m月%d日 %H:%M:%S')
        return chinese_date

    except Exception as e:
        add_service_log('邮件客户端', f'日期格式化失败: {str(e)}', 'warning')
        return '未知'  # 格式化失败，返回'未知'而非可能含Header对象的原始值


def get_inbox_mails(receive_config, source_ip=''):
    """获取收件箱邮件"""
    try:
        protocol = receive_config.get('protocol', 'imap').lower()
        server = receive_config.get('server', '').strip()
        port = int(receive_config.get('port', 0))
        ssl = receive_config.get('ssl', False)
        email = receive_config.get('email', '').strip()
        password = receive_config.get('password', '').strip()
        
        # 验证配置
        if not server or port <= 0 or not email or not password:
            return False, '接收服务器配置不完整'
        
        add_service_log('邮件客户端', f'连接{protocol.upper()}服务器: {server}:{port}', 'info')
        
        # 检查是否应该使用本地邮件存储
        # 只有明确勾选了"使用本地存储"才使用本地存储，否则都走真实的网络连接
        use_local_storage = receive_config.get('use_local_storage', False)
        
        print(f"*** 接收服务器检测: server='{server}', protocol='{protocol}', use_local_storage={use_local_storage}, source_ip='{source_ip}' ***")
        add_service_log('邮件客户端', f'*** 接收服务器检测: server="{server}", protocol="{protocol}", use_local_storage={use_local_storage}, source_ip="{source_ip}" ***', 'info')
        
        if use_local_storage and protocol == 'imap':
            # 直接从本地存储读取邮件
            add_service_log('邮件客户端', '*** 使用本地邮件存储 ***', 'info')
            return get_local_inbox_mails(email)
        
        mails = []
        
        if protocol == 'imap':
            # IMAP协议获取邮件
            import imaplib
            
            try:
                # 连接IMAP服务器
                if source_ip:
                    print(f"*** IMAP使用源IP地址: {source_ip} ***")
                    add_service_log('邮件客户端', f'IMAP绑定源地址: {source_ip}', 'info')
                    
                    # 暂时使用标准连接，后续优化源地址绑定
                    # TODO: 实现IMAP的源地址绑定
                    if ssl:
                        mail_server = imaplib.IMAP4_SSL(server, port)
                    else:
                        mail_server = imaplib.IMAP4(server, port)
                    
                    add_service_log('邮件客户端', f'IMAP连接已建立（源地址绑定待实现）', 'info')
                else:
                    # 使用标准连接方法
                    if ssl:
                        mail_server = imaplib.IMAP4_SSL(server, port)
                    else:
                        mail_server = imaplib.IMAP4(server, port)
                
                # 登录
                mail_server.login(email, password)
                add_service_log('邮件客户端', f'IMAP登录成功: {email}', 'info')
                
                # 选择收件箱
                mail_server.select('INBOX')
                
                # 搜索邮件（按日期排序，最新的在前）
                typ, data = mail_server.search(None, 'ALL')
                if typ == 'OK':
                    mail_ids = data[0].split()
                    
                    # 获取邮件总数
                    total_count = len(mail_ids)
                    print(f"*** IMAP收件箱中共有 {total_count} 封邮件 ***")
                    
                    # 获取最近的10封邮件（mail_ids已经是按时间顺序的，最后的是最新的）
                    # 取最后10个，然后反转，确保最新的在前面
                    if total_count > 0:
                        recent_ids = mail_ids[-10:] if total_count > 10 else mail_ids
                        # 反转顺序，确保最新的邮件在前面
                        recent_ids = list(reversed(recent_ids))
                        print(f"*** 将获取最新的 {len(recent_ids)} 封邮件 ***")
                    else:
                        recent_ids = []
                        print(f"*** 收件箱为空 ***")
                    
                    for mail_id in recent_ids:
                        try:
                            # 获取邮件
                            typ, msg_data = mail_server.fetch(mail_id, '(RFC822)')
                            if typ == 'OK':
                                import email
                                raw_content = msg_data[0][1]

                                # 预处理邮件内容：修复邮件头之间有空行的格式问题
                                # 某些邮件服务器（如远程测试服务器）的邮件格式不符合RFC标准
                                # 邮件头之间有空行，导致解析器在第一个空行后停止解析邮件头
                                def fix_mail_header_format(raw_bytes):
                                    """修复邮件头格式问题：移除邮件头区域内的空行"""
                                    lines = raw_bytes.split(b'\n')
                                    header_lines = []
                                    body_lines = []
                                    header_section = True

                                    # 找出所有邮件头行（包含常见邮件头关键字）
                                    mail_header_keys = [b'From:', b'To:', b'Subject:', b'Date:', b'Cc:', b'Bcc:',
                                                        b'Content-Type:', b'Content-Transfer-Encoding:',
                                                        b'MIME-Version:', b'Message-ID:', b'References:',
                                                        b'In-Reply-To:', b'Reply-To:', b'Sender:',
                                                        b'Received:', b'X-']

                                    for line in lines:
                                        stripped = line.strip()
                                        if header_section:
                                            # 检查是否是邮件头行
                                            is_header = False
                                            for key in mail_header_keys:
                                                if stripped.startswith(key):
                                                    is_header = True
                                                    break
                                            # 空行跳过（格式错误：邮件头之间的空行）
                                            if stripped == b'' or stripped == b'\r':
                                                continue
                                            if is_header:
                                                header_lines.append(line)
                                            else:
                                                # 非邮件头行，进入邮件体区域
                                                header_section = False
                                                body_lines.append(line)
                                        else:
                                            body_lines.append(line)

                                    # 构建修复后的邮件内容
                                    # 邮件头区域连续排列，最后用一个空行分隔邮件体
                                    fixed_content = b'\n'.join(header_lines) + b'\n\n' + b'\n'.join(body_lines)
                                    return fixed_content

                                # 尝试正常解析，如果邮件头缺失则使用预处理
                                msg = email.message_from_bytes(raw_content)
                                if not msg.get('From') or not msg.get('Subject'):
                                    # 预处理邮件内容
                                    fixed_content = fix_mail_header_format(raw_content)
                                    msg = email.message_from_bytes(fixed_content)
                                    add_service_log('邮件客户端', f'邮件格式预处理: 原始邮件头缺失，已修复', 'info')

                                # 解析邮件信息
                                from email.header import decode_header

                                # 获取邮件头字段原始值
                                subject_raw = msg.get('Subject', '')
                                from_raw = msg.get('From', '')
                                to_raw = msg.get('To', '')
                                date_raw = msg.get('Date', '')

                                # 解码邮件主题
                                subject = decode_mime_header(subject_raw) if subject_raw else '无主题'

                                # 解码发件人
                                from_addr = decode_mime_header(from_raw) if from_raw else '未知'

                                # 解码收件人
                                to_addr = decode_mime_header(to_raw) if to_raw else '未知'

                                # 解码日期（Header对象需要转换为字符串）
                                date_str = decode_mime_header(date_raw) if date_raw else '未知'

                                # 格式化日期为中文格式
                                date_formatted = format_email_date(date_str)

                                # 获取邮件正文和附件
                                body = ''
                                attachments = []

                                if msg.is_multipart():
                                    for part in msg.walk():
                                        content_type = part.get_content_type()
                                        content_disposition = part.get('Content-Disposition', '')

                                        if content_type == 'text/plain' and 'attachment' not in content_disposition:
                                            # 邮件正文
                                            if not body:  # 只取第一个文本部分作为正文
                                                body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                        elif 'attachment' in content_disposition:
                                            # 附件
                                            filename = part.get_filename()
                                            if filename:
                                                try:
                                                    file_data = part.get_payload(decode=True)
                                                    attachments.append({
                                                        'filename': filename,
                                                        'size': len(file_data) if file_data else 0,
                                                        'content_type': content_type
                                                    })
                                                except Exception as e:
                                                    add_service_log('邮件客户端', f'解析附件失败 {filename}: {str(e)}', 'error')
                                else:
                                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                                mail_info = {
                                    'id': mail_id.decode(),
                                    'from': from_addr,
                                    'to': to_addr,
                                    'subject': subject,
                                    'date': date_formatted,
                                    'date_raw': date_str,  # 使用解码后的字符串，而非Header对象
                                    'body': body,
                                    'size': len(msg_data[0][1]),
                                    'protocol': 'IMAP'  # 添加协议类型
                                }
                                
                                # 如果有附件，添加附件信息
                                if attachments:
                                    mail_info['attachments'] = attachments
                                    add_service_log('邮件客户端', f'邮件包含 {len(attachments)} 个附件', 'info')
                                
                                mails.append(mail_info)
                        except Exception as e:
                            add_service_log('邮件客户端', f'解析邮件失败 {mail_id}: {str(e)}', 'error')
                
                mail_server.logout()
                add_service_log('邮件客户端', f'IMAP获取邮件成功: {len(mails)} 封', 'info')
                
            except imaplib.IMAP4.error as e:
                add_service_log('邮件客户端', f'IMAP错误: {str(e)}', 'error')
                return False, f'IMAP连接或操作失败: {str(e)}'
            except Exception as e:
                add_service_log('邮件客户端', f'IMAP异常: {str(e)}', 'error')
                return False, f'IMAP操作失败: {str(e)}'
                
        elif protocol == 'pop3':
            # POP3协议获取邮件
            import poplib
            
            try:
                # 连接POP3服务器
                if ssl:
                    mail_server = poplib.POP3_SSL(server, port)
                else:
                    mail_server = poplib.POP3(server, port)
                
                # 登录
                mail_server.user(email)
                mail_server.pass_(password)
                add_service_log('邮件客户端', f'POP3登录成功: {email}', 'info')
                
                # 获取邮件数量
                num_messages = len(mail_server.list()[1])
                print(f"*** POP3收件箱中共有 {num_messages} 封邮件 ***")
                
                # 获取最近的10封邮件（POP3中编号从1开始，最大的编号是最新的）
                # 计算起始编号：如果总数>10，则从 num_messages-9 开始；否则从1开始
                start_msg = max(1, num_messages - 9) if num_messages > 10 else 1
                end_msg = num_messages
                
                print(f"*** 将获取最新的邮件，编号范围: {start_msg} 到 {end_msg} ***")
                
                # 倒序处理邮件，确保最新的在前面（从最大编号开始）
                for i in range(end_msg, start_msg - 1, -1):
                    try:
                        # 获取邮件
                        raw_email = b'\n'.join(mail_server.retr(i)[1])

                        # 预处理邮件内容：修复邮件头之间有空行的格式问题（与IMAP相同的处理）
                        def fix_mail_header_format(raw_bytes):
                            """修复邮件头格式问题：移除邮件头区域内的空行"""
                            lines = raw_bytes.split(b'\n')
                            header_lines = []
                            body_lines = []
                            header_section = True

                            mail_header_keys = [b'From:', b'To:', b'Subject:', b'Date:', b'Cc:', b'Bcc:',
                                                b'Content-Type:', b'Content-Transfer-Encoding:',
                                                b'MIME-Version:', b'Message-ID:', b'References:',
                                                b'In-Reply-To:', b'Reply-To:', b'Sender:',
                                                b'Received:', b'X-']

                            for line in lines:
                                stripped = line.strip()
                                if header_section:
                                    is_header = False
                                    for key in mail_header_keys:
                                        if stripped.startswith(key):
                                            is_header = True
                                            break
                                    if stripped == b'' or stripped == b'\r':
                                        continue
                                    if is_header:
                                        header_lines.append(line)
                                    else:
                                        header_section = False
                                        body_lines.append(line)
                                else:
                                    body_lines.append(line)

                            fixed_content = b'\n'.join(header_lines) + b'\n\n' + b'\n'.join(body_lines)
                            return fixed_content

                        import email
                        msg = email.message_from_bytes(raw_email)
                        if not msg.get('From') or not msg.get('Subject'):
                            fixed_content = fix_mail_header_format(raw_email)
                            msg = email.message_from_bytes(fixed_content)
                            add_service_log('邮件客户端', f'POP3邮件格式预处理: 原始邮件头缺失，已修复', 'info')

                        # 解析邮件信息
                        from email.header import decode_header

                        # 解码邮件主题
                        subject_raw = msg.get('Subject', '无主题')
                        subject = decode_mime_header(subject_raw)

                        # 解码发件人
                        from_raw = msg.get('From', '未知')
                        from_addr = decode_mime_header(from_raw)

                        # 解码收件人
                        to_raw = msg.get('To', '未知')
                        to_addr = decode_mime_header(to_raw)

                        # 解码日期（Header对象需要转换为字符串）
                        date_raw = msg.get('Date', '')
                        date_str = decode_mime_header(date_raw) if date_raw else '未知'

                        # 格式化日期为中文格式
                        date_formatted = format_email_date(date_str)

                        # 获取邮件正文
                        body = ''
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == 'text/plain':
                                    # 获取正确的编码方式
                                    charset = part.get_content_charset() or 'utf-8'
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        try:
                                            body = payload.decode(charset)
                                        except (UnicodeDecodeError, LookupError):
                                            # 如果指定编码失败，尝试常见中文编码
                                            for fallback_charset in ['gbk', 'gb2312', 'gb18030', 'utf-8']:
                                                try:
                                                    body = payload.decode(fallback_charset)
                                                    break
                                                except (UnicodeDecodeError, LookupError):
                                                    continue
                                            else:
                                                body = payload.decode('utf-8', errors='ignore')
                                    break
                        else:
                            payload = msg.get_payload(decode=True)
                            if payload:
                                # 尝试从邮件头获取编码
                                charset = msg.get_content_charset() or 'utf-8'
                                try:
                                    body = payload.decode(charset)
                                except (UnicodeDecodeError, LookupError):
                                    # 尝试常见中文编码
                                    for fallback_charset in ['gbk', 'gb2312', 'gb18030', 'utf-8']:
                                        try:
                                            body = payload.decode(fallback_charset)
                                            break
                                        except (UnicodeDecodeError, LookupError):
                                            continue
                                    else:
                                        body = payload.decode('utf-8', errors='ignore')

                        mails.append({
                            'id': str(i),
                            'from': from_addr,
                            'to': to_addr,
                            'subject': subject,
                            'date': date_formatted,
                            'date_raw': date_str,  # 使用解码后的字符串，而非Header对象
                            'body': body,
                            'size': len(raw_email),
                            'protocol': 'POP3'  # 添加协议类型
                        })
                    except Exception as e:
                        add_service_log('邮件客户端', f'解析邮件失败 {i}: {str(e)}', 'error')
                
                mail_server.quit()
                add_service_log('邮件客户端', f'POP3获取邮件成功: {len(mails)} 封', 'info')
                
            except poplib.error_proto as e:
                add_service_log('邮件客户端', f'POP3错误: {str(e)}', 'error')
                return False, f'POP3连接或操作失败: {str(e)}'
            except Exception as e:
                add_service_log('邮件客户端', f'POP3异常: {str(e)}', 'error')
                return False, f'POP3操作失败: {str(e)}'
        else:
            return False, f'不支持的协议: {protocol}'
        
        return True, mails
        
    except Exception as e:
        add_service_log('邮件客户端', f'获取收件箱异常: {str(e)}', 'error')
        return False, f'获取收件箱失败: {str(e)}'


def send_mail_to_local_storage(mail_data):
    """直接保存邮件到本地存储（支持附件）"""
    print("*** send_mail_to_local_storage 函数被调用！***")
    add_service_log('邮件客户端', '*** send_mail_to_local_storage 开始执行 ***', 'info')
    try:
        from_addr = mail_data.get('from', 'noreply@autotest.com')
        to_addr = mail_data.get('to', '')
        cc_addr = mail_data.get('cc', '')
        subject = mail_data.get('subject', '无主题')
        content = mail_data.get('content', '')
        attachments = mail_data.get('attachments', [])

        print(f"*** 邮件数据: from={from_addr}, to={to_addr}, subject={subject} ***")

        if not to_addr:
            print("*** 错误: 收件人为空 ***")
            return False, '收件人不能为空'

        # 构建收件人列表
        recipients = [to_addr]
        if cc_addr:
            recipients.append(cc_addr)

        add_service_log('邮件客户端', f'*** 直接保存邮件到本地存储: {from_addr} -> {recipients} ***', 'info')
        if attachments:
            add_service_log('邮件客户端', f'*** 包含 {len(attachments)} 个附件 ***', 'info')

        # 邮件存储目录 - 使用脚本所在目录，确保与邮件服务器路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mail_storage_dir = os.path.join(script_dir, 'mail_storage')

        print(f"*** 邮件存储目录: {mail_storage_dir} ***")
        print(f"*** script_dir: {script_dir} ***")
        add_service_log('邮件客户端', f'*** 邮件存储目录: {mail_storage_dir} ***', 'info')

        # 保存邮件到每个收件人的收件箱
        for recipient in recipients:
            # 提取用户名
            username = recipient.split('@')[0] if '@' in recipient else recipient
            user_inbox = os.path.join(mail_storage_dir, username, 'INBOX')

            print(f"*** 创建用户收件箱目录: {user_inbox} ***")

            # 确保目录存在
            os.makedirs(user_inbox, exist_ok=True)

            # 生成邮件文件名
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            mail_id = f"{timestamp}_{hash(content) % 10000:04d}"
            mail_file = os.path.join(user_inbox, f"{mail_id}.eml")
            
            # 构造完整的MIME邮件
            if attachments:
                # 有附件，使用MIME格式
                from email.mime.multipart import MIMEMultipart
                from email.mime.text import MIMEText
                from email.mime.base import MIMEBase
                from email import encoders
                import base64
                
                msg = MIMEMultipart()
                msg['From'] = from_addr
                msg['To'] = ', '.join(recipients)
                msg['Subject'] = subject
                msg['Date'] = time.strftime('%a, %d %b %Y %H:%M:%S %z')
                msg['Message-ID'] = f'<{mail_id}@autotest.com>'
                
                # 添加邮件正文
                msg.attach(MIMEText(content, 'plain', 'utf-8'))
                
                # 添加附件
                for attachment in attachments:
                    try:
                        filename = attachment.get('filename', 'attachment')
                        content_data = attachment.get('content', '')
                        file_type = attachment.get('type', 'application/octet-stream')
                        
                        # 解码base64内容
                        file_data = base64.b64decode(content_data)
                        
                        # 创建附件对象
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(file_data)
                        encoders.encode_base64(part)
                        
                        # 设置附件头信息
                        part.add_header(
                            'Content-Disposition',
                            f'attachment; filename="{filename}"'
                        )
                        
                        # 添加到邮件
                        msg.attach(part)
                        add_service_log('邮件客户端', f'*** 附件已添加: {filename} ***', 'info')
                        
                    except Exception as e:
                        add_service_log('邮件客户端', f'附件处理失败 {filename}: {str(e)}', 'error')
                
                # 生成完整的邮件内容
                mail_content = msg.as_string()
            else:
                # 无附件，使用简单格式
                mail_content = f"""From: {from_addr}
To: {', '.join(recipients)}
Subject: {subject}
Date: {time.strftime('%a, %d %b %Y %H:%M:%S %z')}
Message-ID: <{mail_id}@autotest.com>

{content}"""
            
            # 保存邮件文件
            print(f"*** 准备写入邮件文件: {mail_file} ***")
            with open(mail_file, 'w', encoding='utf-8') as f:
                f.write(mail_content)

            print(f"*** 邮件文件写入成功: {mail_file} ***")
            add_service_log('邮件客户端', f'*** 邮件已保存到: {mail_file} ***', 'info')

        print(f"*** 本地邮件保存完成，共 {len(recipients)} 个收件人 ***")
        add_service_log('邮件客户端', f'*** 本地邮件保存成功: {len(recipients)} 个收件人 ***', 'info')
        return True, f'邮件发送成功 ({", ".join(recipients)})'

    except Exception as e:
        print(f"*** 本地邮件保存异常: {str(e)} ***")
        import traceback
        print(f"*** 异常堆栈: {traceback.format_exc()} ***")
        add_service_log('邮件客户端', f'本地邮件保存失败: {str(e)}', 'error')
        return False, f'邮件保存失败: {str(e)}'


def get_local_inbox_mails(email):
    """从本地存储获取收件箱邮件"""
    try:
        # 提取用户名
        username = email.split('@')[0] if '@' in email else email

        # 邮件存储目录 - 使用脚本所在目录，确保与保存邮件路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mail_storage_dir = os.path.join(script_dir, 'mail_storage')
        user_inbox = os.path.join(mail_storage_dir, username, 'INBOX')
        
        add_service_log('邮件客户端', f'*** 读取本地收件箱: {user_inbox} ***', 'info')
        
        if not os.path.exists(user_inbox):
            add_service_log('邮件客户端', f'*** 用户收件箱不存在: {user_inbox} ***', 'info')
            return True, []
        
        mails = []
        mail_files = []
        
        # 获取所有邮件文件
        for filename in os.listdir(user_inbox):
            if filename.endswith('.eml'):
                mail_path = os.path.join(user_inbox, filename)
                try:
                    # 获取文件修改时间作为排序依据
                    mtime = os.path.getmtime(mail_path)
                    mail_files.append((mtime, mail_path, filename))
                except:
                    continue
        
        # 按时间排序（最新的在前）
        mail_files.sort(key=lambda x: x[0], reverse=True)
        
        add_service_log('邮件客户端', f'*** 找到 {len(mail_files)} 封邮件 ***', 'info')
        
        # 解析邮件
        for i, (mtime, mail_path, filename) in enumerate(mail_files[:10]):  # 最多返回10封
            try:
                with open(mail_path, 'r', encoding='utf-8') as f:
                    mail_content = f.read()
                
                import email
                msg = email.message_from_string(mail_content)
                
                # 解析邮件信息
                from email.header import decode_header
                
                # 解码邮件主题
                subject_raw = msg.get('Subject', '无主题')
                subject = decode_mime_header(subject_raw)
                
                # 解码发件人
                from_raw = msg.get('From', '未知')
                from_addr = decode_mime_header(from_raw)
                
                # 解码收件人
                to_raw = msg.get('To', '未知')
                to_addr = decode_mime_header(to_raw)

                # 解码日期并格式化（Header对象需要转换为字符串）
                date_raw = msg.get('Date', '')
                date_str = decode_mime_header(date_raw) if date_raw else '未知'
                date_formatted = format_email_date(date_str)

                message_id = msg.get('Message-ID', filename)

                # 获取邮件正文和附件
                body = ''
                attachments = []

                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = part.get('Content-Disposition', '')

                        if content_type == 'text/plain' and 'attachment' not in content_disposition:
                            # 邮件正文
                            if not body:  # 只取第一个文本部分作为正文
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body = payload.decode('utf-8', errors='ignore')
                        elif 'attachment' in content_disposition:
                            # 附件
                            filename_att = part.get_filename()
                            if filename_att:
                                try:
                                    file_data = part.get_payload(decode=True)
                                    attachments.append({
                                        'filename': filename_att,
                                        'size': len(file_data) if file_data else 0,
                                        'content_type': content_type
                                    })
                                except Exception as e:
                                    add_service_log('邮件客户端', f'解析附件失败 {filename_att}: {str(e)}', 'error')
                else:
                    payload = msg.get_payload()
                    if isinstance(payload, bytes):
                        body = payload.decode('utf-8', errors='ignore')
                    else:
                        body = payload or '无内容'

                mail_info = {
                    'id': message_id,
                    'from': from_addr,
                    'to': to_addr,
                    'subject': subject,
                    'date': date_formatted,  # 使用格式化后的日期
                    'date_raw': date_str,  # 使用解码后的字符串
                    'body': body or '无内容',
                    'size': len(mail_content),
                    'protocol': 'LOCAL'  # 添加协议类型
                }
                
                # 如果有附件，添加附件信息
                if attachments:
                    mail_info['attachments'] = attachments
                    add_service_log('邮件客户端', f'*** 邮件包含 {len(attachments)} 个附件 ***', 'info')
                
                mails.append(mail_info)
                add_service_log('邮件客户端', f'*** 解析邮件 {i+1}: {subject} ***', 'info')
                
            except Exception as e:
                add_service_log('邮件客户端', f'解析邮件失败 {filename}: {str(e)}', 'error')
        
        add_service_log('邮件客户端', f'*** 本地收件箱获取成功: {len(mails)} 封邮件 ***', 'info')
        return True, mails
        
    except Exception as e:
        add_service_log('邮件客户端', f'获取本地收件箱失败: {str(e)}', 'error')
        return False, f'获取本地收件箱失败: {str(e)}'


def test_network_connectivity(server, port):
    """测试网络连通性"""
    try:
        if not server or not port or port <= 0:
            return False, '服务器地址或端口无效'
        
        add_service_log('网络测试', f'测试连通性: {server}:{port}', 'info')
        
        import socket
        import time
        
        # 创建socket连接测试
        start_time = time.time()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)  # 5秒超时
            result = sock.connect_ex((server, port))
            sock.close()
            
            elapsed_time = int((time.time() - start_time) * 1000)  # 毫秒
            
            if result == 0:
                add_service_log('网络测试', f'连通性测试成功: {server}:{port} ({elapsed_time}ms)', 'info')
                return True, f'网络连通性正常 ({server}:{port}, {elapsed_time}ms)'
            else:
                add_service_log('网络测试', f'连通性测试失败: {server}:{port} (错误码: {result})', 'error')
                if result == 10061:  # Windows: Connection refused
                    return False, f'连接被拒绝: 端口{port}未开放或服务未运行'
                elif result == 10060:  # Windows: Connection timed out
                    return False, f'连接超时: 无法连接到{server}:{port}'
                else:
                    return False, f'连接失败: 错误码{result}'
                    
        except socket.gaierror as e:
            add_service_log('网络测试', f'DNS解析失败: {server} - {str(e)}', 'error')
            return False, f'DNS解析失败: 无法解析主机名 {server}'
        except socket.timeout:
            add_service_log('网络测试', f'连接超时: {server}:{port}', 'error')
            return False, f'连接超时: {server}:{port} (5秒)'
        except Exception as e:
            add_service_log('网络测试', f'网络测试异常: {str(e)}', 'error')
            return False, f'网络测试失败: {str(e)}'
            
    except Exception as e:
        add_service_log('网络测试', f'网络连通性测试异常: {str(e)}', 'error')
        return False, f'网络连通性测试失败: {str(e)}'


def test_mail_connection(test_type, config):
    """测试邮件服务器连接"""
    try:
        server = config.get('server', '').strip()
        port = int(config.get('port', 0))
        ssl = config.get('ssl', False)
        email = config.get('email', '').strip()
        password = config.get('password', '').strip()
        no_auth = config.get('no_auth', False)
        use_local_storage = config.get('use_local_storage', False)

        # Use local storage mode, skip connection test
        if use_local_storage:
            add_service_log('youjiankehuduan', f'Local storage mode, skip {test_type.upper()} test', 'info')
            return True, f'Local storage mode enabled (SMTP: {server}:{port})'
        
        # 验证必要参数
        if not server or port <= 0:
            return False, '服务器地址和端口不能为空'
        
        # 如果不是无认证模式，检查邮箱和密码
        if not no_auth and (not email or not password):
            return False, '邮件服务器配置不完整'
        
        add_service_log('邮件客户端', f'测试{test_type.upper()}连接: {server}:{port}', 'info')
        
        if test_type == 'smtp':
            # 测试SMTP连接
            import smtplib
            import socket
            
            try:
                # 智能选择SMTP连接方式
                if port == 465 or (ssl and port != 587):
                    # 端口465或明确要求SSL且不是587端口，使用SMTP_SSL
                    add_service_log('邮件客户端', f'使用SMTP_SSL连接: {server}:{port}', 'info')
                    smtp_server = smtplib.SMTP_SSL(server, port, timeout=10)
                elif port == 587 or (ssl and port == 587):
                    # 端口587，使用STARTTLS
                    add_service_log('邮件客户端', f'使用SMTP+STARTTLS连接: {server}:{port}', 'info')
                    smtp_server = smtplib.SMTP(server, port, timeout=10)
                    smtp_server.starttls()
                else:
                    # 其他情况，使用普通SMTP
                    add_service_log('邮件客户端', f'使用普通SMTP连接: {server}:{port}', 'info')
                    smtp_server = smtplib.SMTP(server, port, timeout=10)
                
                # 检查服务器是否支持认证并尝试登录
                supports_auth = True
                auth_attempted = False
                
                try:
                    # 发送EHLO命令获取服务器能力
                    smtp_server.ehlo()
                    
                    # 检查服务器是否支持AUTH扩展
                    if hasattr(smtp_server, 'ehlo_resp') and smtp_server.ehlo_resp:
                        extensions_str = smtp_server.ehlo_resp.decode('utf-8', errors='ignore').upper()
                        supports_auth = 'AUTH' in extensions_str
                        add_service_log('邮件客户端', f'SMTP服务器扩展检查: AUTH支持={supports_auth}', 'info')
                    
                    # 尝试登录（根据认证模式）
                    if no_auth:
                        add_service_log('邮件客户端', 'SMTP无认证模式，跳过登录步骤', 'info')
                    elif email and password:
                        if supports_auth:
                            try:
                                smtp_server.login(email, password)
                                add_service_log('邮件客户端', f'SMTP认证成功: {email}', 'info')
                                auth_attempted = True
                            except smtplib.SMTPAuthenticationError:
                                add_service_log('邮件客户端', f'SMTP认证失败: {email}', 'error')
                                return False, 'SMTP认证失败，请检查邮箱地址和密码'
                        else:
                            add_service_log('邮件客户端', f'SMTP服务器不支持认证，但提供了认证信息', 'warning')
                            return False, 'SMTP服务器不支持认证，请启用"无认证连接"选项'
                    else:
                        add_service_log('邮件客户端', 'SMTP连接成功（无认证信息）', 'info')
                    
                except Exception as auth_err:
                    auth_error_str = str(auth_err)
                    if 'AUTH extension not supported' in auth_error_str or 'not supported' in auth_error_str.lower():
                        add_service_log('邮件客户端', f'SMTP服务器不支持AUTH扩展: {auth_error_str}', 'warning')
                        supports_auth = False
                    else:
                        # 其他认证相关错误
                        add_service_log('邮件客户端', f'SMTP认证异常: {auth_error_str}', 'error')
                        return False, f'SMTP认证异常: {auth_error_str}'
                
                # 测试连接成功，退出
                smtp_server.quit()
                
                add_service_log('邮件客户端', f'SMTP连接测试成功: {server}:{port}', 'info')
                
                # 返回详细的连接结果
                if no_auth:
                    return True, f'SMTP服务器连接成功，无认证模式 ({server}:{port})'
                elif auth_attempted:
                    return True, f'SMTP服务器连接成功，认证通过 ({server}:{port})'
                elif supports_auth and email and password:
                    return True, f'SMTP服务器连接成功，支持认证但未测试 ({server}:{port})'
                elif supports_auth:
                    return True, f'SMTP服务器连接成功，支持认证 ({server}:{port})'
                else:
                    return True, f'SMTP服务器连接成功，不支持认证 ({server}:{port})'
                
            except smtplib.SMTPAuthenticationError as auth_err:
                # 这个异常现在应该在上面的代码中处理了，但保留作为备用
                add_service_log('邮件客户端', f'SMTP认证失败（备用处理）: {email}', 'error')
                return False, f'SMTP认证失败: {str(auth_err)}'
            except smtplib.SMTPConnectError:
                add_service_log('邮件客户端', f'SMTP连接失败: {server}:{port}', 'error')
                return False, f'无法连接到SMTP服务器 {server}:{port}'
            except socket.timeout:
                add_service_log('邮件客户端', f'SMTP连接超时: {server}:{port}', 'error')
                return False, f'连接SMTP服务器超时 {server}:{port}'
            except Exception as e:
                error_str = str(e)
                add_service_log('邮件客户端', f'SMTP连接异常: {error_str}', 'error')
                
                # 提供更友好的SSL错误提示
                if 'WRONG_VERSION_NUMBER' in error_str or 'wrong version number' in error_str:
                    if port == 587:
                        return False, f'SSL版本错误: 端口587通常使用STARTTLS而不是直接SSL连接，请检查SSL设置'
                    elif port == 465:
                        return False, f'SSL版本错误: 端口465需要SSL连接，请确保启用SSL选项'
                    else:
                        return False, f'SSL版本错误: 端口{port}的SSL配置可能不正确，请检查服务器SSL设置'
                elif 'SSL' in error_str.upper():
                    return False, f'SSL连接错误: {error_str}，请检查SSL/TLS设置和端口配置'
                elif 'WinError 10061' in error_str or 'Connection refused' in error_str:
                    return False, f'连接被拒绝: 无法连接到SMTP服务器 {server}:{port}，请检查：1) 服务器地址是否正确 2) 端口是否开放 3) 防火墙设置 4) SMTP服务是否运行'
                elif 'WinError 10060' in error_str or 'timed out' in error_str:
                    return False, f'连接超时: 无法连接到SMTP服务器 {server}:{port}，请检查网络连接和服务器状态'
                elif 'WinError' in error_str:
                    return False, f'Windows网络错误: {error_str}，请检查网络配置和服务器连接'
                else:
                    return False, f'SMTP连接失败: {error_str}'
                
        elif test_type == 'receive':
            # 测试IMAP/POP3连接
            protocol = config.get('protocol', 'imap').lower()
            
            if protocol == 'imap':
                import imaplib
                import socket
                
                try:
                    # 创建IMAP连接（测试连接暂时不绑定源地址）
                    if ssl:
                        imap_server = imaplib.IMAP4_SSL(server, port)
                    else:
                        imap_server = imaplib.IMAP4(server, port)
                    
                    # 尝试登录
                    imap_server.login(email, password)
                    imap_server.logout()
                    
                    add_service_log('邮件客户端', f'IMAP连接测试成功: {server}:{port}', 'info')
                    return True, f'IMAP服务器连接成功 ({server}:{port})'
                    
                except imaplib.IMAP4.error as e:
                    add_service_log('邮件客户端', f'IMAP认证失败: {str(e)}', 'error')
                    return False, f'IMAP认证失败: {str(e)}'
                except socket.timeout:
                    add_service_log('邮件客户端', f'IMAP连接超时: {server}:{port}', 'error')
                    return False, f'连接IMAP服务器超时 {server}:{port}'
                except Exception as e:
                    add_service_log('邮件客户端', f'IMAP连接异常: {str(e)}', 'error')
                    return False, f'IMAP连接失败: {str(e)}'
                    
            elif protocol == 'pop3':
                import poplib
                import socket
                
                try:
                    # 创建POP3连接
                    if ssl:
                        pop3_server = poplib.POP3_SSL(server, port)
                    else:
                        pop3_server = poplib.POP3(server, port)
                    
                    # 尝试登录
                    pop3_server.user(email)
                    pop3_server.pass_(password)
                    pop3_server.quit()
                    
                    add_service_log('邮件客户端', f'POP3连接测试成功: {server}:{port}', 'info')
                    return True, f'POP3服务器连接成功 ({server}:{port})'
                    
                except poplib.error_proto as e:
                    add_service_log('邮件客户端', f'POP3认证失败: {str(e)}', 'error')
                    return False, f'POP3认证失败: {str(e)}'
                except socket.timeout:
                    add_service_log('邮件客户端', f'POP3连接超时: {server}:{port}', 'error')
                    return False, f'连接POP3服务器超时 {server}:{port}'
                except Exception as e:
                    add_service_log('邮件客户端', f'POP3连接异常: {str(e)}', 'error')
                    return False, f'POP3连接失败: {str(e)}'
            else:
                return False, f'不支持的接收协议: {protocol}'
        else:
            return False, f'不支持的测试类型: {test_type}'
            
    except Exception as e:
        add_service_log('邮件客户端', f'邮件连接测试异常: {str(e)}', 'error')
        return False, f'邮件连接测试失败: {str(e)}'


def get_service_status():
    try:
        with service_lock:
            listeners_summary = {}
            for protocol in listener_states.keys():
                state = listener_states.get(protocol)
                if state and state.get('running'):
                    # 邮件协议简化处理
                    if protocol == 'mail':
                        thread = state.get('thread')
                        
                        # 简化状态检查，只检查线程是否活跃
                        if thread and thread.is_alive() and not thread.stop_event.is_set():
                            summary = {
                                'running': True,
                                'host': state['host'],
                                'smtp_port': getattr(thread, 'smtp_port', 25),
                                'imap_port': getattr(thread, 'imap_port', 143),
                                'pop3_port': getattr(thread, 'pop3_port', 110),
                                'domain': getattr(thread, 'domain', 'autotest.com'),
                                'connections': list(state.get('connections', {}).values()),
                                'packets': state.get('packets', 0)
                            }
                            # 添加调试日志
                            add_service_log('状态检查', f'邮件服务器状态: 运行中, SMTP:{summary["smtp_port"]}, IMAP:{summary["imap_port"]}, POP3:{summary["pop3_port"]}, 域名:{summary["domain"]}', 'info')
                        else:
                            # 线程不活跃，标记为未运行
                            state['running'] = False
                            summary = {'running': False}
                            add_service_log('状态检查', f'邮件服务器状态: 未运行, 线程状态: {thread.is_alive() if thread else "None"}, 停止事件: {thread.stop_event.is_set() if thread else "None"}', 'warning')
                            if thread and not thread.is_alive():
                                add_service_log('邮件服务器', '检测到邮件服务器线程已停止', 'warning')
                                state['thread'] = None
                    else:
                        # 其他协议的正常处理
                        summary = {
                            'running': True,
                            'host': state['host'],
                            'port': state['port'],
                            'connections': list(state.get('connections', {}).values()),
                            'packets': state.get('packets', 0)
                        }
            else:
                summary = {'running': False}
            listeners_summary[protocol] = summary
        clients_summary = {}
        for protocol in client_states.keys():
            state = client_states.get(protocol)
            if state and state.get('running'):
                summary = {
                    'running': True,
                    'server_ip': state.get('server_ip'),
                    'server_port': state.get('server_port'),
                    'connections': list(state.get('connections', {}).values()),
                    'message': state.get('message'),
                    'send_interval': state.get('send_interval')
                }
                # FTP客户端特殊处理
                if protocol == 'ftp':
                    summary['current_dir'] = state.get('current_dir', '/')
                    summary['file_list'] = state.get('file_list', '')
            else:
                summary = {'running': False}
            clients_summary[protocol] = summary
            logs_preview = list(service_logs)[:20]
        return {
            'success': True,
            'listeners': listeners_summary,
            'clients': clients_summary,
            'logs': logs_preview
        }
    except Exception as e:
        add_service_log('状态检查', f'获取服务状态失败: {str(e)}', 'error')
        return {
            'success': False,
            'error': str(e),
            'listeners': {},
            'clients': {},
            'logs': []
        }


@app.route('/api/services/listener', methods=['POST'])
def api_services_listener():
    try:
        data = request.json or {}

        protocol = (data.get('protocol') or 'tcp').lower()
        action = (data.get('action') or 'start').lower()
        host = data.get('host') or '0.0.0.0'
        port = int(data.get('port', 0) or 0)

        if action == 'start':
            # 不同协议的特殊参数
            kwargs = {}
            if protocol == 'ftp':
                kwargs['username'] = data.get('username', 'tdhx')
                kwargs['password'] = data.get('password', 'tdhx@2017')
                kwargs['directory'] = data.get('directory', '')
            elif protocol == 'http':
                kwargs['directory'] = data.get('directory', '')
            elif protocol == 'mail':
                kwargs['smtp_port'] = data.get('smtp_port', 25)
                kwargs['imap_port'] = data.get('imap_port', 143)
                kwargs['pop3_port'] = data.get('pop3_port', 110)
                kwargs['domain'] = data.get('domain', 'autotest.com')
                kwargs['ssl_enabled'] = data.get('ssl_enabled', False)
                accounts_data = data.get('accounts', [])
                kwargs['accounts'] = accounts_data

            success, result = start_listener(protocol, host, port, **kwargs)
        elif action == 'stop':
            success, result = stop_listener(protocol)
        else:
            return jsonify({'success': False, 'error': '不支持的操作'}), 400

        status_code = 200 if success else 400
        # 确保 result 是字典才能解包
        if isinstance(result, dict):
            return jsonify({'success': success, **result}), status_code
        else:
            # result 是字符串（如错误消息）
            key = 'message' if success else 'error'
            return jsonify({'success': success, key: str(result)}), status_code
    except Exception as e:
        add_service_log('监听服务', f'API异常: {str(e)}', 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mail/recent', methods=['GET'])
def api_mail_recent():
    """获取最近邮件列表"""
    try:
        limit = int(request.args.get('limit', 10))

        # 获取邮件监听器状态
        with service_lock:
            mail_state = listener_states.get('mail', {})

        # 如果没有运行中的邮件服务器，返回空列表
        if not mail_state.get('running'):
            return jsonify({'success': True, 'mails': [], 'message': '邮件服务器未运行'})

        # 从状态中获取 mail_listener 引用（如果有）
        # 注意：这里需要访问 MailListenerThread 实例
        # 由于 mail listener 在状态中没有直接引用，我们需要通过其他方式获取

        # 临时方案：直接查询数据库
        import sqlite3
        # 使用脚本所在目录，确保与邮件服务器路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.join(script_dir, 'mail_storage', 'mails.db')

        if not os.path.exists(db_path):
            return jsonify({'success': True, 'mails': [], 'message': '暂无邮件数据'})

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM mails
            ORDER BY received_at DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        mails = []
        for row in rows:
            mails.append({
                'id': row['id'],
                'mail_from': row['mail_from'],
                'mail_to': row['mail_to'],
                'subject': row['subject'] or '',
                'body': row['body'] or '',
                'has_attachment': bool(row['has_attachment']),
                'received_at': row['received_at']
            })

        conn.close()
        return jsonify({'success': True, 'mails': mails})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mail/send_test', methods=['POST'])
def api_mail_send_test():
    """发送测试邮件（用于测试防火墙协议解析）"""
    try:
        data = request.json or {}
        subject = data.get('subject', '测试邮件')
        body = data.get('body', '这是一封测试邮件')
        to_email = data.get('to', 'receiver@autotest.com')
        from_email = data.get('from', 'sender@autotest.com')
        attachment = data.get('attachment', None)

        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.base import MIMEBase
        from email import encoders

        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        if attachment:
            with open(attachment, 'rb') as f:
                part = MIMEBase('application', 'octet-stream')
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(attachment)}')
                msg.attach(part)

        # 发送到本地 SMTP 服务器
        server = smtplib.SMTP('127.0.0.1', 25)
        server.sendmail(from_email, [to_email], msg.as_string())
        server.quit()

        return jsonify({'success': True, 'message': '邮件发送成功'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 邮件用户管理 API ====================

@app.route('/api/mail/users', methods=['GET'])
def api_mail_users_list():
    """获取邮件用户列表"""
    try:
        # 获取邮件服务器状态
        with service_lock:
            mail_state = listener_states.get('mail', {})

        # 检查邮件服务器是否运行
        if not mail_state.get('running'):
            return jsonify({'success': False, 'error': '邮件服务器未运行'}), 400

        # 获取邮件存储目录 - 使用脚本所在目录，确保与邮件服务器路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mail_storage_dir = os.path.join(script_dir, 'mail_storage')
        accounts_file = os.path.join(mail_storage_dir, 'accounts.json')

        users = []

        # 从 accounts.json 加载用户
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    accounts = json.load(f)
                    for username, info in accounts.items():
                        users.append({
                            'username': username,
                            'email': info.get('email', f"{username}@autotest.com"),
                            'created': info.get('created', '-')
                        })
            except Exception as e:
                add_service_log('邮件服务器', f'读取用户列表失败: {str(e)}', 'error')

        # 从初始化参数获取用户（如果存在）
        thread = mail_state.get('thread')
        if thread and hasattr(thread, 'accounts'):
            init_accounts = thread.accounts
            init_usernames = [u.get('username') for u in init_accounts if u.get('username')]
            # 添加初始化账户中不存在于JSON的用户
            for account in init_accounts:
                username = account.get('username')
                if username and username not in [u['username'] for u in users]:
                    domain = getattr(thread, 'domain', 'autotest.com')
                    users.append({
                        'username': username,
                        'email': account.get('email', f"{username}@{domain}"),
                        'created': '-'
                    })

        return jsonify({'success': True, 'users': users})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mail/users', methods=['POST'])
def api_mail_users_create():
    """创建邮件用户"""
    try:
        data = request.json or {}
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        email = data.get('email', '').strip()

        if not username:
            return jsonify({'success': False, 'error': '用户名不能为空'}), 400
        if not password or len(password) < 4:
            return jsonify({'success': False, 'error': '密码至少需要4位'}), 400

        # 获取邮件服务器状态
        with service_lock:
            mail_state = listener_states.get('mail', {})

        # 检查邮件服务器是否运行
        if not mail_state.get('running'):
            return jsonify({'success': False, 'error': '邮件服务器未运行'}), 400

        # 获取邮件存储目录 - 使用脚本所在目录，确保与邮件服务器路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mail_storage_dir = os.path.join(script_dir, 'mail_storage')
        accounts_file = os.path.join(mail_storage_dir, 'accounts.json')

        # 确保目录存在
        os.makedirs(mail_storage_dir, exist_ok=True)

        # 加载现有账户
        accounts = {}
        if os.path.exists(accounts_file):
            try:
                with open(accounts_file, 'r', encoding='utf-8') as f:
                    accounts = json.load(f)
            except:
                accounts = {}

        # 检查用户是否已存在
        if username in accounts:
            return jsonify({'success': False, 'error': '用户已存在'}), 400

        # 获取域名
        thread = mail_state.get('thread')
        domain = getattr(thread, 'domain', 'autotest.com') if thread else 'autotest.com'

        # 创建用户
        user_email = email if email else f"{username}@{domain}"
        accounts[username] = {
            'password': password,
            'email': user_email,
            'created': time.strftime('%Y-%m-%d %H:%M:%S')
        }

        # 保存账户
        with open(accounts_file, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, indent=2, ensure_ascii=False)

        # 创建用户邮箱目录
        user_mail_dir = os.path.join(mail_storage_dir, username)
        os.makedirs(user_mail_dir, exist_ok=True)
        os.makedirs(os.path.join(user_mail_dir, 'INBOX'), exist_ok=True)
        os.makedirs(os.path.join(user_mail_dir, 'SENT'), exist_ok=True)
        os.makedirs(os.path.join(user_mail_dir, 'DRAFTS'), exist_ok=True)

        add_service_log('邮件服务器', f'创建用户成功: {username} ({user_email})')

        return jsonify({
            'success': True,
            'message': '用户创建成功',
            'user': {
                'username': username,
                'email': user_email
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        add_service_log('邮件服务器', f'创建用户失败: {str(e)}', 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/mail/users/<username>', methods=['DELETE'])
def api_mail_users_delete(username):
    """删除邮件用户"""
    try:
        # 获取邮件服务器状态
        with service_lock:
            mail_state = listener_states.get('mail', {})

        # 检查邮件服务器是否运行
        if not mail_state.get('running'):
            return jsonify({'success': False, 'error': '邮件服务器未运行'}), 400

        # 获取邮件存储目录 - 使用脚本所在目录，确保与邮件服务器路径一致
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mail_storage_dir = os.path.join(script_dir, 'mail_storage')
        accounts_file = os.path.join(mail_storage_dir, 'accounts.json')

        # 加载现有账户
        if not os.path.exists(accounts_file):
            return jsonify({'success': False, 'error': '用户不存在'}), 404

        with open(accounts_file, 'r', encoding='utf-8') as f:
            accounts = json.load(f)

        # 检查用户是否存在
        if username not in accounts:
            return jsonify({'success': False, 'error': '用户不存在'}), 404

        # 删除用户
        del accounts[username]

        # 保存账户
        with open(accounts_file, 'w', encoding='utf-8') as f:
            json.dump(accounts, f, indent=2, ensure_ascii=False)

        add_service_log('邮件服务器', f'删除用户成功: {username}')

        return jsonify({
            'success': True,
            'message': f'用户 {username} 已删除'
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        add_service_log('邮件服务器', f'删除用户失败: {str(e)}', 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/services/client', methods=['POST'])
def api_services_client():
    print(f"[DEBUG] ===== api_services_client 函数被调用 =====")
    print(f"[DEBUG] 请求方法: {request.method}")
    print(f"[DEBUG] 请求路径: {request.path}")
    print(f"[DEBUG] 请求头: {dict(request.headers)}")
    print(f"[DEBUG] 原始请求体: {request.get_data()}")
    add_service_log('API调试', 'api_services_client 函数被调用', 'info')
    
    # 强制刷新输出
    import sys
    sys.stdout.flush()
    
    try:
        data = request.json or {}
        protocol = (data.get('protocol') or 'tcp').lower()
        action = (data.get('action') or 'start').lower()

        # 提取 config（前端可能将参数包装在 config 键中）
        config = data.get('config', data)

        # 添加调试信息
        print(f"[DEBUG] api_services_client 收到请求: protocol={protocol}, action={action}")
        print(f"[DEBUG] 完整请求数据: {data}")
        print(f"[DEBUG] 提取的config: {config}")
        add_service_log('API调试', f'收到客户端请求: protocol={protocol}, action={action}', 'info')

        # 检查 HTTP 协议的特殊处理
        if protocol == 'http':
            print(f"[DEBUG] HTTP协议请求，action={action}")
            if action == 'connect':
                server_ip = config.get('server_ip', '')
                server_port = config.get('server_port', 80)
                print(f"[DEBUG] HTTP连接参数: server_ip={server_ip}, server_port={server_port}")
                add_service_log('HTTP客户端', f'尝试连接: {server_ip}:{server_port}', 'info')
        if protocol == 'tcp':
            if action == 'start':
                success, result = start_tcp_client(config)
            elif action == 'connect':
                success, result = connect_tcp_client(config)
            elif action == 'start_send':
                success, result = start_tcp_send(config)
            elif action == 'stop_send':
                success, result = stop_tcp_send()
            elif action == 'stop':
                success, result = stop_tcp_client()
            elif action == 'disconnect':
                conn_id = config.get('connection_id')
                success, message = disconnect_tcp_connection(conn_id)
                result = {'message': message}
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        elif protocol == 'udp':
            if action == 'start':
                success, result = start_udp_client(config)
            elif action == 'stop':
                success, result = stop_udp_client()
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        elif protocol == 'ftp':
            if action == 'start':
                success, result = start_ftp_client(config)
            elif action == 'connect':
                success, result = connect_ftp_client(config)
                if not success:
                    # 确保错误信息格式正确
                    if isinstance(result, str):
                        result = {'error': result}
                    elif not isinstance(result, dict):
                        result = {'error': str(result)}
            elif action == 'disconnect':
                success, result = disconnect_ftp_client()
            elif action == 'list':
                success, result = list_ftp_files()
            elif action == 'upload':
                filename = config.get('filename', '')
                content = config.get('content', '')
                local_file_path = config.get('local_file_path', '')
                success, result = upload_ftp_file(filename, content, local_file_path)
                if success:
                    result = {'message': result}
                else:
                    result = {'error': result}
            elif action == 'get_local_files':
                directory = config.get('directory', '')
                success, result = get_local_file_list(directory)
                if not success:
                    result = {'error': result}
            elif action == 'download':
                filename = config.get('filename', '')
                if not filename:
                    return jsonify({'success': False, 'error': '缺少文件名参数'}), 400
                success, result = download_ftp_file(filename)
                if success:
                    # result是字典，包含filename和file_size
                    result = {'filename': result.get('filename', filename), 'file_size': result.get('file_size')}
                else:
                    # result是错误消息字符串
                    result = {'error': result}
            elif action == 'cd':
                # 目录切换操作
                dirname = config.get('dirname', '')
                if not dirname:
                    return jsonify({'success': False, 'error': '缺少目录名参数'}), 400
                with service_lock:
                    state = client_states.get('ftp')
                if not state or not state.get('running'):
                    success, result = False, {'error': 'FTP客户端未连接'}
                else:
                    worker = state.get('worker')
                    if worker:
                        success, result = worker.cd_dir(dirname)
                        if success:
                            result = {'current_dir': result.get('current_dir', dirname)}
                        else:
                            result = {'error': result}
                    else:
                        success, result = False, {'error': 'FTP客户端工作器不存在'}
            elif action == 'stop':
                success, result = stop_ftp_client()
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        elif protocol == 'http':
            if action == 'connect':
                success, result = connect_http_client(config)
                if not success:
                    if isinstance(result, str):
                        result = {'error': result}
                    elif isinstance(result, dict) and 'error' in result:
                        pass
                    else:
                        result = {'error': str(result)}
            elif action == 'disconnect':
                success, result = disconnect_http_client()
            elif action == 'list':
                with service_lock:
                    state = client_states.get('http')
                if not state or not state.get('running'):
                    success, result = False, {'error': 'HTTP客户端未连接'}
                else:
                    worker = state.get('worker')
                    if worker:
                        success, result = worker.list_files()
                        if success:
                            result = {'files': result}
                        else:
                            result = {'error': result}
                    else:
                        success, result = False, {'error': 'HTTP客户端工作器不存在'}
            elif action == 'download':
                filename = config.get('filename', '')
                with service_lock:
                    state = client_states.get('http')
                if not state or not state.get('running'):
                    success, result = False, {'error': 'HTTP客户端未连接'}
                else:
                    worker = state.get('worker')
                    if worker:
                        success, result = worker.download_file(filename)
                        if success:
                            result = {'filename': result.get('filename'), 'file_size': result.get('file_size')}
                        else:
                            result = {'error': result}
                    else:
                        success, result = False, {'error': 'HTTP客户端工作器不存在'}
            elif action == 'upload':
                filename = config.get('filename', '')
                content = config.get('content', '')
                local_file_path = config.get('local_file_path', '')
                success, result = upload_http_file(filename, content, local_file_path)
                if success:
                    result = {'message': result}
                else:
                    result = {'error': result}
            elif action == 'cd':
                # 目录切换操作
                dirname = config.get('dirname', '')
                if not dirname:
                    return jsonify({'success': False, 'error': '缺少目录名参数'}), 400
                with service_lock:
                    state = client_states.get('http')
                if not state or not state.get('running'):
                    success, result = False, {'error': 'HTTP客户端未连接'}
                else:
                    worker = state.get('worker')
                    if worker:
                        success, result = worker.cd_dir(dirname)
                        if success:
                            result = {'current_dir': result.get('current_dir', dirname)}
                        else:
                            result = {'error': result}
                    else:
                        success, result = False, {'error': 'HTTP客户端工作器不存在'}
            else:
                print(f"[ERROR] HTTP协议不支持的操作: {action}")
                add_service_log('API错误', f'HTTP协议不支持的操作: {action}', 'error')
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        elif protocol == 'mail':
            print(f"[DEBUG] 邮件协议请求，action={action}")
            if action == 'test_connection':
                test_type = data.get('type', 'smtp')  # smtp 或 receive
                config = data.get('config', {})
                print(f"[DEBUG] 邮件连接测试: type={test_type}, config={config}")
                add_service_log('邮件客户端', f'开始测试{test_type}连接', 'info')
                success, result = test_mail_connection(test_type, config)
                print(f"[DEBUG] 邮件连接测试结果: success={success}, result={result}")
                if success:
                    result = {'message': result}
                else:
                    result = {'error': result}
            elif action == 'send_mail':
                smtp_config = data.get('smtp_config', {})
                mail_data = data.get('mail_data', {})
                source_ip = data.get('source_ip', '')  # 获取源IP地址
                print(f"[DEBUG] 邮件发送请求: smtp_config={smtp_config}, source_ip={source_ip}")
                print(f"[DEBUG] mail_data keys: {list(mail_data.keys())}")
                print(f"[DEBUG] mail_data.subject: {mail_data.get('subject', 'N/A')}")
                print(f"[DEBUG] mail_data.attachments type: {type(mail_data.get('attachments', []))}")
                print(f"[DEBUG] mail_data.attachments: {mail_data.get('attachments', 'NOT FOUND')}")
                attachments = mail_data.get('attachments', [])
                if attachments:
                    print(f"[DEBUG] 附件数量: {len(attachments)}")
                    for i, att in enumerate(attachments):
                        print(f"[DEBUG] 附件{i}: filename={att.get('filename')}, content_len={len(att.get('content', '')) if att.get('content') else 0}")
                else:
                    print(f"[DEBUG] 没有附件数据！")
                add_service_log('邮件客户端', f'开始发送邮件: {mail_data.get("subject", "无主题")}', 'info')
                print("*** 准备调用 send_mail_via_smtp 函数 ***")
                success, result = send_mail_via_smtp(smtp_config, mail_data, source_ip)
                print("*** send_mail_via_smtp 函数调用完成 ***")
                print(f"[DEBUG] 邮件发送结果: success={success}, result={result}")
                if success:
                    result = {'message': result}
                else:
                    result = {'error': result}
            elif action == 'get_inbox':
                receive_config = data.get('receive_config', {})
                source_ip = data.get('source_ip', '')  # 获取源IP地址
                print(f"[DEBUG] 获取收件箱请求: receive_config={receive_config}, source_ip={source_ip}")
                add_service_log('邮件客户端', f'开始获取收件箱邮件', 'info')
                success, result = get_inbox_mails(receive_config, source_ip)
                print(f"[DEBUG] 获取收件箱结果: success={success}, mails={len(result) if isinstance(result, list) else 0}")
                if success:
                    result = {'mails': result}
                else:
                    result = {'error': result}
            else:
                print(f"[ERROR] 邮件协议不支持的操作: {action}")
                add_service_log('API错误', f'邮件协议不支持的操作: {action}', 'error')
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        elif protocol == 'network':
            print(f"[DEBUG] 网络协议请求，action={action}")
            if action == 'ping':
                server = data.get('server', '')
                port = int(data.get('port', 0))
                print(f"[DEBUG] 网络连通性测试: server={server}, port={port}")
                add_service_log('网络测试', f'开始测试连通性: {server}:{port}', 'info')
                success, result = test_network_connectivity(server, port)
                print(f"[DEBUG] 网络连通性测试结果: success={success}, result={result}")
                if success:
                    result = {'message': result}
                else:
                    result = {'error': result}
            else:
                print(f"[ERROR] 网络协议不支持的操作: {action}")
                add_service_log('API错误', f'网络协议不支持的操作: {action}', 'error')
                return jsonify({'success': False, 'error': '不支持的操作'}), 400
        else:
            print(f"[ERROR] 不支持的协议: {protocol}")
            add_service_log('API错误', f'不支持的协议: {protocol}', 'error')
            return jsonify({'success': False, 'error': '不支持的协议'}), 400
        # 对于FTP和HTTP连接，即使失败也返回200，让前端能正确处理错误信息
        if (protocol == 'ftp' and action == 'connect') or (protocol == 'http' and action == 'connect'):
            status_code = 200
        else:
            status_code = 200 if success else 400
        
        if isinstance(result, dict):
            return jsonify({'success': success, **result}), status_code
        else:
            return jsonify({'success': success, 'message': result}), status_code
    except Exception as e:
        import traceback
        error_msg = f'api_services_client异常: {e}\n{traceback.format_exc()}'
        add_service_log('API错误', error_msg, 'error')
        print(f"[ERROR] {error_msg}")  # 添加控制台输出
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/services/status', methods=['GET'])
def api_services_status():
    try:
        # 最简化的状态检查，只返回基本信息
        listeners_summary = {}
        clients_summary = {}
        
        # 快速检查监听器状态
        for protocol in ['tcp', 'udp', 'ftp', 'http', 'mail']:
            state = listener_states.get(protocol, {'running': False})
            is_running = state.get('running', False)
            
            if is_running and protocol == 'mail':
                thread = state.get('thread')
                if thread and hasattr(thread, 'is_alive'):
                    try:
                        if thread.is_alive() and not thread.stop_event.is_set():
                            # 从状态中获取连接信息
                            connections = list(state.get('connections', {}).values()) if isinstance(state.get('connections'), dict) else []
                            listeners_summary[protocol] = {
                                'running': True,
                                'host': state.get('host', '0.0.0.0'),
                                'smtp_port': getattr(thread, 'smtp_port', 25),
                                'imap_port': getattr(thread, 'imap_port', 143),
                                'pop3_port': getattr(thread, 'pop3_port', 110),
                                'domain': getattr(thread, 'domain', 'autotest.com'),
                                'connections': connections,
                                'packets': state.get('packets', 0)
                            }
                        else:
                            listeners_summary[protocol] = {'running': False}
                    except:
                        listeners_summary[protocol] = {'running': False}
                else:
                    listeners_summary[protocol] = {'running': False}
            elif is_running:
                # 从状态中获取连接信息
                connections = list(state.get('connections', {}).values()) if isinstance(state.get('connections'), dict) else []
                listeners_summary[protocol] = {
                    'running': True,
                    'host': state.get('host', '0.0.0.0'),
                    'port': state.get('port', 0),
                    'connections': connections,
                    'packets': state.get('packets', 0)
                }
            else:
                listeners_summary[protocol] = {'running': False}
        
        # 快速检查客户端状态
        for protocol in ['tcp', 'udp', 'ftp', 'http', 'mail']:
            state = client_states.get(protocol, {'running': False})
            is_running = state.get('running', False)

            if is_running:
                # 从状态中获取连接信息
                connections = list(state.get('connections', {}).values()) if isinstance(state.get('connections'), dict) else []
                clients_summary[protocol] = {
                    'running': True,
                    'server_ip': state.get('server_ip', ''),
                    'server_port': state.get('server_port', 0),
                    'connections': connections,
                    'message': state.get('message', ''),
                    'send_interval': state.get('send_interval', 0)
                }

                # TCP 协议需要额外返回 sending 状态
                # 用于前端控制"停止发送"按钮的可用状态
                if protocol == 'tcp':
                    manager = state.get('manager')
                    if manager and hasattr(manager, 'send_stop_event') and hasattr(manager, 'send_threads'):
                        # send_stop_event 未设置且有活跃发送线程 = 正在发送
                        is_sending = (not manager.send_stop_event.is_set()) and any(t.is_alive() for t in manager.send_threads)
                        clients_summary[protocol]['sending'] = is_sending
                    else:
                        clients_summary[protocol]['sending'] = False
            else:
                clients_summary[protocol] = {'running': False}
        
        return jsonify({
            'success': True,
            'listeners': listeners_summary,
            'clients': clients_summary,
            'logs': []
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/services/logs', methods=['GET'])
def api_services_logs():
    try:
        limit = int(request.args.get('limit', 100))
        with service_lock:
            logs = list(service_logs)[:limit]
        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# 端口扫描相关全局变量
port_scan_sessions = {}
port_scan_lock = threading.Lock()


def scan_port_with_flags(target_ip, port, timeout=1, scan_type='tcp_syn', interface=None, src_port=None, cached_src_ip=None, cached_src_mac=None, cached_target_mac=None):
    """
    使用 TCP 标志位扫描端口 - 支持多种扫描类型

    支持的扫描类型：
    - tcp_syn: SYN 扫描（半开放扫描）
    - tcp_fin: FIN 扫描（隐蔽扫描）
    - tcp_rst: RST 扫描
    - tcp_null: Null 扫描（无标志位）
    - tcp_xmas: Xmas 扫描（FIN+PSH+URG）
    - tcp_ack: ACK 扫描（探测防火墙）
    - tcp_fin_syn: FIN+SYN 扫描（异常组合）
    - tcp_syn_rst: SYN+RST 扫描（异常组合）
    - tcp_fin_rst: FIN+RST 扫描（异常组合）
    - tcp_psh: PSH 扫描
    - tcp_urg: URG 扫描
    """
    # 常见端口服务映射表
    PORT_SERVICES = {
        21: 'FTP', 22: 'SSH', 23: 'Telnet', 25: 'SMTP', 53: 'DNS',
        80: 'HTTP', 110: 'POP3', 143: 'IMAP', 443: 'HTTPS', 445: 'SMB',
        465: 'SMTPS', 587: 'Submission', 993: 'IMAPS', 995: 'POP3S',
        1433: 'MSSQL', 1521: 'Oracle', 3306: 'MySQL', 3389: 'RDP',
        5432: 'PostgreSQL', 5900: 'VNC', 6379: 'Redis', 8080: 'HTTP-Proxy',
        8443: 'HTTPS-Alt', 27017: 'MongoDB'
    }

    def get_service_name(port_num):
        """获取端口对应的服务名称"""
        return PORT_SERVICES.get(port_num, '')

    try:
        # 使用随机源端口，避免被防火墙识别（如果未指定）
        if src_port is None:
            import random
            src_port = random.randint(49152, 65535)  # 动态端口范围

        # 根据扫描类型设置 TCP 标志位
        tcp_flags_map = {
            'tcp_syn': 'S',           # SYN 扫描
            'tcp_fin': 'F',           # FIN 扫描
            'tcp_rst': 'R',           # RST 扫描
            'tcp_null': '',           # Null 扫描（无标志位）
            'tcp_xmas': 'FPU',        # Xmas 扫描（FIN+PSH+URG）
            'tcp_ack': 'A',           # ACK 扫描
            'tcp_fin_syn': 'FS',      # FIN+SYN 扫描
            'tcp_syn_rst': 'SR',      # SYN+RST 扫描
            'tcp_fin_rst': 'FR',      # FIN+RST 扫描
            'tcp_psh': 'P',           # PSH 扫描
            'tcp_urg': 'U',           # URG 扫描
        }
        tcp_flags = tcp_flags_map.get(scan_type, 'S')  # 默认 SYN

        # 使用缓存的 IP 和 MAC 地址，避免每次都获取和发送 ARP 请求
        src_ip = cached_src_ip
        src_mac = cached_src_mac
        dst_mac = cached_target_mac
        selected_iface = interface

        # 如果没有缓存，才获取（向后兼容）
        if not src_ip:
            try:
                from scapy.arch import get_if_addr
                if selected_iface:
                    src_ip = get_if_addr(selected_iface)
                else:
                    # 没有指定接口，自动选择路由到目标 IP 的接口
                    from scapy.route import conf
                    route_info = conf.route.route(target_ip)
                    if route_info:
                        selected_iface = route_info[0]
                        src_ip = get_if_addr(selected_iface)
            except:
                src_ip = None

        if not src_mac:
            try:
                from scapy.arch import get_if_hwaddr
                if selected_iface:
                    src_mac = get_if_hwaddr(selected_iface)
                else:
                    # 尝试获取第一个可用的非回环接口
                    from scapy.arch import get_interfaces
                    ifaces = get_interfaces()
                    for iface in ifaces:
                        if iface.get('ip') and not iface.get('ip', '').startswith('127.'):
                            src_mac = iface.get('mac', '').replace(':', '-')
                            if not selected_iface:
                                selected_iface = iface.get('name', iface.get('display_name', ''))
                            break
            except:
                src_mac = None

        if not dst_mac:
            # 如果没有缓存的 MAC，尝试从 ARP 缓存获取（不发送 ARP 请求）
            try:
                from scapy.layers.l2 import getmacbyip
                dst_mac = getmacbyip(target_ip)
            except:
                # 如果 ARP 缓存中没有，使用广播 MAC（避免发送 ARP 请求）
                dst_mac = "ff:ff:ff:ff:ff:ff"

        # 构造 IP/TCP 包
        if src_ip:
            ip_packet = IP(src=src_ip, dst=target_ip)
        else:
            ip_packet = IP(dst=target_ip)

        tcp_packet = TCP(sport=src_port, dport=port, flags=tcp_flags)
        packet = ip_packet / tcp_packet

        # 记录发送前的时间戳用于计算响应时间
        send_time = time.time()
        response = None
        response_time = None

        # 使用 L2（Ether 层）发送原始包，绕过系统 TCP 栈
        # 在 Windows 上，需要禁用系统 TCP 栈以避免自动发送 RST
        # 保存原始 conf 设置
        original_L3socket = None
        try:
            original_L3socket = conf.L3socket
            # 禁用 L3 socket，强制使用 L2 发送
            conf.L3socket = None
        except:
            pass

        if selected_iface and src_mac and dst_mac:
            try:
                # 使用 L2 发送（Ether 层）
                ether_packet = Ether(src=src_mac, dst=dst_mac)
                l2_packet = ether_packet / packet
                # 使用 sendp 发送 L2 包（绕过系统 TCP 栈）
                sendp(l2_packet, iface=selected_iface, verbose=0)
                # 使用 sniff 接收响应
                response = sniff(
                    timeout=timeout,
                    filter="tcp and host {} and port {}".format(target_ip, port),
                    iface=selected_iface,
                    count=1,
                    verbose=0
                )
                if response and len(response) > 0:
                    response = response[0]
                else:
                    response = None
            except Exception as e:
                # 如果 L2 发送失败，回退到 L3 发送
                response = sr1(packet, timeout=timeout, verbose=0, retry=0)
        else:
            # 如果没有指定接口或 MAC，使用 L3 发送
            response = sr1(packet, timeout=timeout, verbose=0, retry=0)

            # 恢复原始 conf 设置
            if original_L3socket is not None:
                try:
                    conf.L3socket = original_L3socket
                except:
                    pass

        # 计算响应时间
        if response is not None:
            response_time = round((time.time() - send_time) * 1000, 2)  # 转换为毫秒

        # 分析响应
        if response is None:
            # 无响应：端口可能被过滤或关闭（取决于扫描类型）
            # FIN/Null/Xmas 扫描：无响应表示 open|filtered（防火墙丢弃）
            # SYN/RST/ACK扫描：无响应表示 filtered（被防火墙过滤）
            if scan_type in ['tcp_fin', 'tcp_null', 'tcp_xmas']:
                status = 'open|filtered'
            elif scan_type in ['tcp_ack']:
                status = 'filtered'  # ACK 扫描无响应表示被防火墙过滤
            else:
                status = 'filtered'  # SYN/RST 扫描无响应通常是被防火墙过滤
            return {
                'port': port,
                'status': status,
                'service': get_service_name(port),
                'response_time': None
            }

        # 检查响应包类型
        if TCP in response:
            tcp_layer = response[TCP]
            flags = tcp_layer.flags

            # ========== SYN 扫描 ==========
            if scan_type == 'tcp_syn':
                if flags == 0x12:  # SYN-ACK
                    return {'port': port, 'status': 'open', 'service': get_service_name(port), 'response_time': response_time}
                elif flags == 0x04:  # RST
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== FIN 扫描 ==========
            elif scan_type == 'tcp_fin':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                elif flags == 0x12:  # SYN-ACK - 异常响应，端口可能开放
                    return {'port': port, 'status': 'open', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    # 其他响应或无响应表示 open|filtered
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== RST 扫描 ==========
            elif scan_type == 'tcp_rst':
                # RST 扫描：大多数系统忽略 RST 包
                # 收到 RST 表示端口关闭或防火墙规则
                if flags == 0x04:  # RST
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== Null 扫描 ==========
            elif scan_type == 'tcp_null':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== Xmas 扫描 ==========
            elif scan_type == 'tcp_xmas':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== ACK 扫描 ==========
            elif scan_type == 'tcp_ack':
                # ACK 扫描用于探测防火墙规则
                # 收到 RST 表示防火墙允许（端口可能是开放的或关闭的）
                # 无响应或 ICMP 错误表示防火墙有状态过滤
                if flags == 0x04:  # RST
                    return {'port': port, 'status': 'unfiltered', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== FIN+SYN 扫描 ==========
            elif scan_type == 'tcp_fin_syn':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== SYN+RST 扫描 ==========
            elif scan_type == 'tcp_syn_rst':
                if flags == 0x04:  # RST
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== FIN+RST 扫描 ==========
            elif scan_type == 'tcp_fin_rst':
                if flags == 0x04:  # RST
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== PSH 扫描 ==========
            elif scan_type == 'tcp_psh':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

            # ========== URG 扫描 ==========
            elif scan_type == 'tcp_urg':
                if flags == 0x04:  # RST - 端口关闭
                    return {'port': port, 'status': 'closed', 'service': get_service_name(port), 'response_time': response_time}
                else:
                    return {'port': port, 'status': 'open|filtered', 'service': get_service_name(port), 'response_time': response_time}

        return {
            'port': port,
            'status': 'filtered',
            'service': get_service_name(port),
            'response_time': response_time
        }

    except (OSError, IOError) as e:
        # 文件描述符错误或其他 IO 错误，忽略并返回关闭状态
        # 这在 Windows 并发场景下很常见
        return {
            'port': port,
            'status': 'closed',
            'service': get_service_name(port),
            'response_time': None
        }
    except Exception as e:
        # 扫描出错，记录但不阻塞其他端口扫描
        # 不打印详细错误，避免在并发场景下产生大量输出
        return {
            'port': port,
            'status': 'closed',
            'service': get_service_name(port),
            'response_time': None
        }


@app.route('/api/port_scan', methods=['POST'])
def api_port_scan():
    """端口扫描API（通过代理程序）"""
    try:
        data = request.json
        target_ip = data.get('target_ip', '').strip()
        ports = data.get('ports', [])
        timeout = data.get('timeout', 1)  # 默认超时时间1秒
        scan_type = data.get('scan_type', 'tcp_syn')
        interface = data.get('interface', None)
        max_threads = data.get('threads', 200)
        # 速率控制参数
        scan_rate = int(data.get('scan_rate', 0))  # 包/秒，0 表示不限速
        port_delay = float(data.get('port_delay', 0))  # 毫秒
        
        if not target_ip:
            return jsonify({
                'success': False,
                'error': '目标IP地址不能为空'
            }), 400
        
        if not ports or len(ports) == 0:
            return jsonify({
                'success': False,
                'error': '端口列表不能为空'
            }), 400
        
        # 支持所有扫描类型
        supported_scan_types = [
            'tcp_syn', 'tcp_fin', 'tcp_rst', 'tcp_null', 'tcp_xmas',
            'tcp_ack', 'tcp_fin_syn', 'tcp_syn_rst', 'tcp_fin_rst',
            'tcp_psh', 'tcp_urg'
        ]
        if scan_type not in supported_scan_types:
            return jsonify({
                'success': False,
                'error': f'不支持的扫描类型: {scan_type}'
            }), 400
        
        # 生成扫描会话ID
        import uuid
        scan_id = str(uuid.uuid4())
        
            # 初始化扫描会话
        with port_scan_lock:
            port_scan_sessions[scan_id] = {
                'target_ip': target_ip,
                'total_ports': len(ports),
                'scanned': 0,
                'open_ports': [],
                'closed_ports': [],
                'results': [],
                'completed': False,
                'cancelled': False,  # 添加取消标志
                'start_time': time.time(),
                'current_port': None
            }
        
        # 启动扫描线程
        scan_thread = threading.Thread(
            target=perform_agent_port_scan,
            args=(scan_id, target_ip, ports, timeout, scan_type, interface, max_threads, scan_rate, port_delay)
        )
        scan_thread.daemon = True
        scan_thread.start()
        
        print("[API] 端口扫描已启动: scan_id={}, target={}, ports={}, type={}, interface={}".format(
            scan_id, target_ip, len(ports), scan_type, interface))
        
        return jsonify({
            'success': True,
            'scan_id': scan_id,
            'message': '扫描已启动',
            'total_ports': len(ports)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


def perform_agent_port_scan(scan_id, target_ip, ports, timeout, scan_type, interface, max_threads, scan_rate=0, port_delay=0):
    """
    在代理程序中执行端口扫描

    参数:
        scan_id: 扫描会话 ID
        target_ip: 目标 IP 地址
        ports: 端口列表
        timeout: 超时时间（秒）
        scan_type: 扫描类型
        interface: 网卡名称
        max_threads: 最大线程数
        scan_rate: 速率限制（包/秒），0 表示不限速
        port_delay: 端口间延迟（毫秒），用于慢速扫描绕过防火墙
    """
    try:
        # 使用随机源端口，避免被防火墙识别
        import random
        fixed_src_port = random.randint(49152, 65535)  # 随机选择一个源端口
        
        print("[扫描] 开始扫描 {}，端口数: {}，类型: {}，接口: {}，源端口: {}".format(
            target_ip, len(ports), scan_type, interface, fixed_src_port))
        
        # 在扫描开始前，先获取目标IP的MAC地址并缓存，避免每次扫描都发送ARP请求
        target_mac = None
        src_ip = None
        src_mac = None
        selected_interface = None  # 记录实际使用的接口名称
        
        if interface:
            try:
                from scapy.arch import get_if_addr, get_if_hwaddr
                # 获取源IP和源MAC
                src_ip = get_if_addr(interface)
                src_mac = get_if_hwaddr(interface)
                print("[扫描] 源IP: {}, 源MAC: {}".format(src_ip, src_mac))
                
                # 尝试获取目标MAC地址（只获取一次，避免大量ARP请求）
                try:
                    from scapy.layers.l2 import getmacbyip
                    target_mac = getmacbyip(target_ip)
                    print("[扫描] 目标MAC (从ARP缓存): {}".format(target_mac))
                except:
                    # 如果ARP缓存中没有，发送一次ARP请求
                    try:
                        print("[扫描] ARP缓存中无目标MAC，发送ARP请求...")
                        arp_request = ARP(pdst=target_ip)
                        arp_response = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / arp_request, timeout=2, verbose=0, iface=interface)
                        if arp_response and len(arp_response[0]) > 0:
                            target_mac = arp_response[0][0][1].hwsrc
                            print("[扫描] 目标MAC (从ARP响应): {}".format(target_mac))
                        else:
                            # 如果ARP失败，使用广播MAC（可能无法收到响应，但可以尝试）
                            target_mac = "ff:ff:ff:ff:ff:ff"
                            print("[扫描] ARP请求无响应，使用广播MAC")
                    except Exception as e:
                        target_mac = "ff:ff:ff:ff:ff:ff"
                        print("[扫描] ARP请求失败: {}，使用广播MAC".format(e))
            except Exception as e:
                print("[扫描] 获取接口信息失败: {}".format(e))
        else:
            # 没有指定接口，自动选择能够路由到目标 IP 的接口
            try:
                from scapy.arch import get_if_addr, get_if_hwaddr
                from scapy.route import conf

                # 使用路由表确定出接口
                route_info = conf.route.route(target_ip)
                if route_info:
                    iface_name = route_info[0]
                    src_ip = get_if_addr(iface_name)
                    src_mac = get_if_hwaddr(iface_name)

                    # 验证选择的接口是否有效
                    if src_ip and not src_ip.startswith('11.') and not src_ip.startswith('127.'):
                        selected_interface = iface_name
                        print("[扫描] 自动选择接口：{}，源 IP: {}, 源 MAC: {}".format(selected_interface, src_ip, src_mac))

                        # 判断目标是否在同一网段，如果不在则需要使用网关 MAC
                        import socket
                        def ip_to_int(ip):
                            return int(ip.encode('hex'), 16) if isinstance(ip, str) else 0
                        def ip_in_same_subnet(ip1, ip2, netmask):
                            import struct
                            try:
                                ip1_int = struct.unpack("!I", socket.inet_aton(ip1))[0]
                                ip2_int = struct.unpack("!I", socket.inet_aton(ip2))[0]
                                mask_int = struct.unpack("!I", socket.inet_aton(netmask))[0]
                                return (ip1_int & mask_int) == (ip2_int & mask_int)
                            except:
                                return False

                        # 获取接口 netmask
                        try:
                            from scapy.arch import get_if_netmask
                            netmask = get_if_netmask(selected_interface)
                        except:
                            netmask = "255.255.255.0"

                        # 检查目标是否在同一网段
                        target_in_same_subnet = ip_in_same_subnet(src_ip, target_ip, netmask)

                        if not target_in_same_subnet:
                            # 目标不在同一网段，需要使用网关 MAC
                            print("[扫描] 目标不在同一网段，需要使用网关 MAC")
                            # 从系统路由表获取网关
                            try:
                                route_output = subprocess.check_output(
                                    "powershell -Command \"Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1 -ExpandProperty NextHop\"",
                                    shell=True, stderr=subprocess.STDOUT
                                ).decode('utf-8', errors='ignore').strip()
                                gateway = route_output
                                print("[扫描] 网关地址：{}".format(gateway))

                                # 使用 psutil 直接获取所有接口的所有 IP 地址，查找与网关在同一网段的接口
                                if_addrs = psutil.net_if_addrs()
                                if_stats = psutil.net_if_stats()

                                print("[扫描] 遍历所有接口查找与网关在同一网段的接口:")
                                gateway_iface = None
                                gateway_iface_mac = None
                                gateway_iface_ip = None

                                for ifname, addrs in if_addrs.items():
                                    if ifname == 'Loopback Pseudo-Interface 1' or 'Loopback' in ifname:
                                        continue

                                    iface_mac = None
                                    for addr in addrs:
                                        # 获取 MAC 地址
                                        if addr.family == psutil.AF_LINK:
                                            iface_mac = addr.address
                                        # 获取 IPv4 地址
                                        elif addr.family == socket.AF_INET:
                                            iface_ip = addr.address
                                            if iface_ip and not iface_ip.startswith('127.') and not iface_ip.startswith('0.'):
                                                # 检查是否与网关在同一网段
                                                try:
                                                    if ip_in_same_subnet(iface_ip, gateway, "255.255.255.0"):
                                                        gateway_iface = ifname
                                                        gateway_iface_ip = iface_ip
                                                        gateway_iface_mac = iface_mac.replace('-', ':') if iface_mac else None
                                                        print("[扫描] 找到与网关在同一网段的接口：{}，IP: {}, MAC: {}".format(
                                                            ifname, iface_ip, gateway_iface_mac))
                                                        break
                                                except:
                                                    pass

                                    if gateway_iface:
                                        break

                                if not gateway_iface:
                                    print("[扫描] 未找到与网关在同一网段的接口，使用广播 MAC")
                                    target_mac = "ff:ff:ff:ff:ff:ff"
                                else:
                                    try:
                                        arp_output = subprocess.check_output(
                                            "powershell -Command \"Get-NetNeighbor -IPAddress '{}' | Select-Object -ExpandProperty LinkLayerAddress\"".format(gateway),
                                            shell=True, stderr=subprocess.STDOUT, timeout=5
                                        ).decode('utf-8', errors='ignore').strip()
                                        # 清理输出，去除空行和空格
                                        arp_lines = [line.strip() for line in arp_output.split('\n') if line.strip()]
                                        if arp_lines:
                                            target_mac = arp_lines[0].replace('-', ':')
                                            print("[扫描] 网关 MAC (从系统 ARP 缓存): {}".format(target_mac))
                                        else:
                                            target_mac = "ff:ff:ff:ff:ff:ff"
                                            print("[扫描] ARP 缓存中无网关 MAC，使用广播 MAC")
                                    except subprocess.TimeoutExpired:
                                        print("[扫描] 获取网关 MAC 超时，使用广播 MAC")
                                        target_mac = "ff:ff:ff:ff:ff:ff"
                                    except Exception as arp_e:
                                        print("[扫描] 获取网关 MAC 失败：{}，使用广播 MAC".format(arp_e))
                                        target_mac = "ff:ff:ff:ff:ff:ff"
                            except Exception as e:
                                print("[扫描] 获取网关地址失败：{}，使用广播 MAC".format(e))
                                target_mac = "ff:ff:ff:ff:ff:ff"
                        else:
                            # 目标在同一网段，直接获取目标 MAC
                            try:
                                from scapy.layers.l2 import getmacbyip
                                target_mac = getmacbyip(target_ip)
                                if target_mac:
                                    print("[扫描] 目标 MAC (从 ARP 缓存): {}".format(target_mac))
                                else:
                                    target_mac = "ff:ff:ff:ff:ff:ff"
                                    print("[扫描] ARP 缓存中无目标 MAC，使用广播 MAC")
                            except:
                                target_mac = "ff:ff:ff:ff:ff:ff"
                                print("[扫描] 获取目标 MAC 失败，使用广播 MAC")
                    else:
                        # IP 无效，使用备用逻辑
                        raise Exception("选择的接口 IP 无效：{}".format(src_ip))
            except Exception as e:
                print("[扫描] 自动选择接口失败：{}，使用备用逻辑".format(e))
                # 备用逻辑：选择与目标 IP 在同一网段的接口
                try:
                    ifaces = get_interfaces()
                    target_octets = target_ip.split('.')

                    # 第一优先：选择同网段接口（/24）
                    for iface in ifaces:
                        iface_ip = iface.get('ip', '')
                        iface_name = iface.get('name', iface.get('display_name', ''))

                        # 跳过无效接口
                        if not iface_ip or iface_ip.startswith('127.'):
                            continue
                        # 跳过虚拟接口（11.x.x.x 等）
                        if iface_ip.startswith('11.') or iface_ip.startswith('100.64.'):
                            continue
                        # 跳过 VMware 虚拟网卡
                        if 'VMware' in iface_name or 'vmnet' in iface_name.lower():
                            continue

                        iface_octets = iface_ip.split('.')
                        if iface_octets[0:3] == target_octets[0:3]:
                            src_ip = iface_ip
                            src_mac = iface.get('mac', '').replace(':', '-')
                            selected_interface = iface_name
                            print("[扫描] 选择同网段接口：{}，源 IP: {}, 源 MAC: {}".format(selected_interface, src_ip, src_mac))
                            break

                    # 第二优先：选择第一个有效的物理网卡
                    if not src_ip or src_ip.startswith('11.'):
                        for iface in ifaces:
                            iface_ip = iface.get('ip', '')
                            iface_name = iface.get('name', iface.get('display_name', ''))
                            iface_mac = iface.get('mac', '')

                            # 跳过无效接口
                            if not iface_ip or iface_ip.startswith('127.'):
                                continue
                            # 跳过虚拟接口
                            if iface_ip.startswith('11.') or iface_ip.startswith('100.64.'):
                                continue
                            # 跳过 VMware 虚拟网卡
                            if 'VMware' in iface_name or 'vmnet' in iface_name.lower():
                                continue
                            # 跳过没有 MAC 地址的接口
                            if not iface_mac:
                                continue
                            # 优先选择状态为"已启用"的接口
                            if iface.get('status') == '已启用':
                                src_ip = iface_ip
                                src_mac = iface_mac.replace(':', '-')
                                selected_interface = iface_name
                                print("[扫描] 使用物理网卡：{}，源 IP: {}, 源 MAC: {}".format(selected_interface, src_ip, src_mac))

                                # 检查目标是否在同一网段，如果不在则需要使用网关 MAC
                                import socket
                                import struct
                                def ip_in_same_subnet(ip1, ip2, netmask):
                                    try:
                                        ip1_int = struct.unpack("!I", socket.inet_aton(ip1))[0]
                                        ip2_int = struct.unpack("!I", socket.inet_aton(ip2))[0]
                                        mask_int = struct.unpack("!I", socket.inet_aton(netmask))[0]
                                        return (ip1_int & mask_int) == (ip2_int & mask_int)
                                    except:
                                        return False

                                # 获取接口 netmask
                                try:
                                    from scapy.arch import get_if_netmask
                                    netmask = get_if_netmask(selected_interface)
                                except:
                                    netmask = "255.255.255.0"

                                # 检查目标是否在同一网段
                                target_in_same_subnet = ip_in_same_subnet(src_ip, target_ip, netmask)

                                if not target_in_same_subnet:
                                    # 目标不在同一网段，需要使用网关 MAC
                                    print("[扫描] 目标不在同一网段，需要使用网关 MAC")
                                    # 从系统路由表获取网关
                                    try:
                                        route_output = subprocess.check_output(
                                            "powershell -Command \"Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Select-Object -First 1 -ExpandProperty NextHop\"",
                                            shell=True, stderr=subprocess.STDOUT
                                        ).decode('utf-8', errors='ignore').strip()
                                        gateway = route_output
                                        print("[扫描] 网关地址：{}".format(gateway))
                                        # 从系统 ARP 缓存获取网关 MAC（使用 PowerShell 命令）
                                        try:
                                            arp_output = subprocess.check_output(
                                                "powershell -Command \"Get-NetNeighbor -IPAddress '{}' | Select-Object -ExpandProperty LinkLayerAddress\"".format(gateway),
                                                shell=True, stderr=subprocess.STDOUT, timeout=5
                                            ).decode('utf-8', errors='ignore').strip()
                                            # 清理输出，去除空行和空格
                                            arp_lines = [line.strip() for line in arp_output.split('\n') if line.strip()]
                                            if arp_lines:
                                                target_mac = arp_lines[0].replace('-', ':')
                                                print("[扫描] 网关 MAC (从系统 ARP 缓存): {}".format(target_mac))
                                            else:
                                                # ARP 缓存没有，发送 ARP 请求
                                                print("[扫描] ARP 缓存中无网关 MAC，发送 ARP 请求...")
                                                arp_request = ARP(pdst=gateway)
                                                arp_response = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / arp_request, timeout=2, verbose=0, iface=selected_interface)
                                                if arp_response and len(arp_response[0]) > 0:
                                                    target_mac = arp_response[0][0][1].hwsrc
                                                    print("[扫描] 网关 MAC (从 ARP 响应): {}".format(target_mac))
                                                else:
                                                    target_mac = "ff:ff:ff:ff:ff:ff"
                                                    print("[扫描] ARP 请求无响应，使用广播 MAC")
                                        except subprocess.TimeoutExpired:
                                            print("[扫描] 获取网关 MAC 超时，使用广播 MAC")
                                            target_mac = "ff:ff:ff:ff:ff:ff"
                                        except Exception as arp_e:
                                            print("[扫描] 获取网关 MAC 失败：{}，尝试发送 ARP 请求".format(arp_e))
                                            # 尝试发送 ARP 请求
                                            try:
                                                arp_request = ARP(pdst=gateway)
                                                arp_response = srp(Ether(dst="ff:ff:ff:ff:ff:ff") / arp_request, timeout=2, verbose=0, iface=selected_interface)
                                                if arp_response and len(arp_response[0]) > 0:
                                                    target_mac = arp_response[0][0][1].hwsrc
                                                    print("[扫描] 网关 MAC (从 ARP 响应): {}".format(target_mac))
                                                else:
                                                    target_mac = "ff:ff:ff:ff:ff:ff"
                                                    print("[扫描] ARP 请求无响应，使用广播 MAC")
                                            except:
                                                target_mac = "ff:ff:ff:ff:ff:ff"
                                                print("[扫描] ARP 请求失败，使用广播 MAC")
                                    except Exception as e:
                                        print("[扫描] 获取网关地址失败：{}，使用广播 MAC".format(e))
                                        target_mac = "ff:ff:ff:ff:ff:ff"
                                else:
                                    # 目标在同一网段，直接获取目标 MAC
                                    try:
                                        from scapy.layers.l2 import getmacbyip
                                        target_mac = getmacbyip(target_ip)
                                        if target_mac:
                                            print("[扫描] 目标 MAC (从 ARP 缓存): {}".format(target_mac))
                                        else:
                                            target_mac = "ff:ff:ff:ff:ff:ff"
                                            print("[扫描] ARP 缓存中无目标 MAC，使用广播 MAC")
                                    except:
                                        target_mac = "ff:ff:ff:ff:ff:ff"
                                        print("[扫描] 获取目标 MAC 失败，使用广播 MAC")
                                break
                except Exception as e:
                    print("[扫描] 备用逻辑选择接口失败：{}".format(e))

        open_ports = []
        closed_ports = []
        results = []  # 包含所有端口的结果（开放和关闭）
        scanned_count = 0
        last_update_time = time.time()  # 记录上次更新时间

        # 判断是否使用速率控制
        use_rate_control = scan_rate > 0 or port_delay > 0

        if use_rate_control:
            # 顺序扫描模式 - 用于速率限制绕过防火墙
            print("[扫描] 使用速率控制模式：scan_rate={} 包/秒，port_delay={}ms".format(scan_rate, port_delay))

            last_send_time = time.time()
            for port in ports:
                # 检查是否已取消
                with port_scan_lock:
                    if scan_id not in port_scan_sessions:
                        break
                    if port_scan_sessions[scan_id].get('cancelled', False):
                        break

                # 速率控制：包间隔延迟
                if scan_rate > 0:
                    target_interval = 1.0 / scan_rate
                    elapsed = time.time() - last_send_time
                    if elapsed < target_interval:
                        time.sleep(target_interval - elapsed)

                # 端口延迟（额外延迟）
                if port_delay > 0:
                    time.sleep(port_delay / 1000.0)

                last_send_time = time.time()
                scanned_count += 1

                try:
                    random_src_port = random.randint(49152, 65535)
                    result = scan_port_with_flags(target_ip, port, timeout, scan_type, selected_interface,
                                                   random_src_port, src_ip, src_mac, target_mac)

                    # 保存所有端口的结果（开放和关闭）
                    if result:
                        results.append(result)
                        if result.get('status') == 'open':
                            open_ports.append(port)
                            print("[扫描] 发现开放端口：{}".format(port))
                        elif result.get('status') == 'closed':
                            closed_ports.append(port)

                    # 每 5 秒更新一次会话状态，或者每扫描 10 个端口，或者发现开放端口时
                    current_time = time.time()
                    should_update_full = (
                        (current_time - last_update_time) >= 5.0 or  # 每 5 秒更新一次
                        scanned_count % 10 == 0 or
                        (result and result.get('status') == 'open')
                    )

                    with port_scan_lock:
                        if scan_id in port_scan_sessions:
                            session = port_scan_sessions[scan_id]
                            session['scanned'] = scanned_count
                            session['current_port'] = port

                            if should_update_full:
                                session['open_ports'] = open_ports.copy()
                                session['closed_ports'] = closed_ports.copy()
                                session['results'] = results.copy()
                                last_update_time = current_time

                            if scanned_count >= session['total_ports']:
                                session['completed'] = True
                                session['current_port'] = None

                            # 每 100 个端口打印一次进度
                            if scanned_count % 100 == 0:
                                print("[扫描] 进度：{}/{} ({} 个开放端口，{} 个关闭端口)".format(
                                    scanned_count, session['total_ports'], len(open_ports), len(closed_ports)))

                except Exception as e:
                    # 忽略错误，继续扫描
                    print("[扫描] 端口 {} 扫描出错：{}".format(port, e))
                    with port_scan_lock:
                        if scan_id in port_scan_sessions:
                            session = port_scan_sessions[scan_id]
                            session['scanned'] = scanned_count
                            if scanned_count >= session['total_ports']:
                                session['completed'] = True
        else:
            # 并发扫描模式 - 原有逻辑
            # 使用线程池进行并发扫描
            from concurrent.futures import ThreadPoolExecutor, as_completed

            # 限制最大线程数，避免过多并发导致资源问题（Windows 上 Scapy 并发有限制）
            # 提高并发数以加快扫描速度，但需要更好的错误处理
            actual_max_threads = min(max_threads, 500)  # 最多 500 个并发线程

            with ThreadPoolExecutor(max_workers=actual_max_threads) as executor:
                # 提交所有扫描任务（使用相同的源端口，并传递缓存的 MAC 地址）
                future_to_port = {
                    executor.submit(scan_port_with_flags, target_ip, port, timeout, scan_type, selected_interface, random.randint(49152, 65535), src_ip, src_mac, target_mac): port
                    for port in ports
                }

                print("[扫描] 已提交 {} 个扫描任务".format(len(future_to_port)))

                # 处理完成的任务
                for future in as_completed(future_to_port):
                    # 检查是否已取消
                    with port_scan_lock:
                        if scan_id not in port_scan_sessions:
                            # 扫描会话已删除
                            break
                        if port_scan_sessions[scan_id].get('cancelled', False):
                            # 扫描已取消
                            print("[扫描] 扫描已取消：{}".format(scan_id))
                            break

                    port = future_to_port[future]
                    scanned_count += 1

                    try:
                        result = future.result()

                        # 保存所有端口的结果（开放和关闭）
                        if result:
                            results.append(result)
                            if result.get('status') == 'open':
                                open_ports.append(port)
                                print("[扫描] 发现开放端口：{}".format(port))
                            elif result.get('status') == 'closed':
                                closed_ports.append(port)

                        # 每 5 秒更新一次会话状态，或者每扫描 10 个端口，或者发现开放端口时
                        current_time = time.time()
                        should_update_full = (
                            (current_time - last_update_time) >= 5.0 or  # 每 5 秒更新一次
                            scanned_count % 10 == 0 or
                            (result and result.get('status') == 'open')
                        )

                        with port_scan_lock:
                            if scan_id in port_scan_sessions:
                                session = port_scan_sessions[scan_id]
                                session['scanned'] = scanned_count
                                session['current_port'] = port

                                if should_update_full:
                                    session['open_ports'] = open_ports.copy()
                                    session['closed_ports'] = closed_ports.copy()
                                    session['results'] = results.copy()
                                    last_update_time = current_time

                                if scanned_count >= session['total_ports']:
                                    session['completed'] = True
                                    session['current_port'] = None

                                # 每 100 个端口打印一次进度
                                if scanned_count % 100 == 0:
                                    print("[扫描] 进度：{}/{} ({} 个开放端口，{} 个关闭端口)".format(
                                        scanned_count, session['total_ports'], len(open_ports), len(closed_ports)))

                    except Exception as e:
                        # 忽略错误，继续扫描
                        print("[扫描] 端口 {} 扫描出错：{}".format(port, e))
                        with port_scan_lock:
                            if scan_id in port_scan_sessions:
                                session = port_scan_sessions[scan_id]
                                session['scanned'] = scanned_count
                                if scanned_count >= session['total_ports']:
                                    session['completed'] = True

        
        # 更新最终状态
        print("[扫描] 扫描完成: {}/{}，开放端口: {}，关闭端口: {}".format(
            scanned_count, len(ports), len(open_ports), len(closed_ports)))
        with port_scan_lock:
            if scan_id in port_scan_sessions:
                port_scan_sessions[scan_id]['completed'] = True
                port_scan_sessions[scan_id]['current_port'] = None
                port_scan_sessions[scan_id]['results'] = results
                port_scan_sessions[scan_id]['open_ports'] = open_ports
                port_scan_sessions[scan_id]['closed_ports'] = closed_ports
                port_scan_sessions[scan_id]['scanned'] = scanned_count
    
    except Exception as e:
        # 不打印详细错误，避免在程序关闭时产生输出错误
        error_msg = str(e)
        if 'shutdown' not in error_msg.lower() and 'finalizing' not in error_msg.lower():
            print("[扫描] 执行端口扫描时出错: {}".format(error_msg))
        
        with port_scan_lock:
            if scan_id in port_scan_sessions:
                port_scan_sessions[scan_id]['completed'] = True
                port_scan_sessions[scan_id]['error'] = error_msg


@app.route('/api/port_scan/stop', methods=['POST'])
def api_port_scan_stop():
    """停止端口扫描"""
    try:
        data = request.json
        scan_id = data.get('scan_id', '').strip()
        
        if not scan_id:
            return jsonify({
                'success': False,
                'error': '缺少scan_id参数'
            }), 400
        
        with port_scan_lock:
            if scan_id not in port_scan_sessions:
                return jsonify({
                    'success': False,
                    'error': '扫描会话不存在'
                }), 404
            
            # 标记为已取消
            port_scan_sessions[scan_id]['cancelled'] = True
            port_scan_sessions[scan_id]['completed'] = True
        
        print("[API] 端口扫描已停止: scan_id={}".format(scan_id))
        
        return jsonify({
            'success': True,
            'message': '扫描已停止'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ========== 报文回放相关函数 ==========

def replay_pcap_worker(interface, pcap_files, replay_rate, replay_count, replay_throughput):
    """回放pcap文件的工作线程"""
    global replay_statistics, stop_replay
    
    try:
        with replay_lock:
            replay_statistics['running'] = True
            replay_statistics['packets_sent'] = 0
            replay_statistics['start_time'] = time.time()
            replay_statistics['total_files'] = len(pcap_files)
            replay_statistics['current_file_index'] = 0
        
        add_service_log('报文回放', f'开始回放 {len(pcap_files)} 个pcap文件', 'info')
        
        # 计算速率倍数
        if replay_rate == 'max':
            multiplier = None  # 最大速率
        elif replay_rate == '1':
            multiplier = 1.0
        else:
            try:
                multiplier = float(replay_rate)
            except:
                multiplier = 1.0
        
        # 回放次数（-1表示无限循环）
        loop_count = replay_count if replay_count > 0 else -1
        current_loop = 0
        
        # 吞吐限制（Mbps）
        mbps_limit = replay_throughput if replay_throughput and replay_throughput > 0 else None
        
        while (loop_count == -1 or current_loop < loop_count) and not stop_replay.is_set():
            for file_index, pcap_file in enumerate(pcap_files):
                if stop_replay.is_set():
                    break
                
                with replay_lock:
                    replay_statistics['current_file'] = pcap_file
                    replay_statistics['current_file_index'] = file_index + 1
                
                add_service_log('报文回放', f'回放文件 {file_index + 1}/{len(pcap_files)}: {pcap_file}', 'info')
                
                try:
                    # 读取pcap文件
                    packets = rdpcap(pcap_file)
                    total_packets = len(packets)
                    
                    if total_packets == 0:
                        add_service_log('报文回放', f'文件 {pcap_file} 为空，跳过', 'warning')
                        continue
                    
                    add_service_log('报文回放', f'文件包含 {total_packets} 个报文', 'info')
                    
                    # 计算时间间隔（用于速率控制）
                    if multiplier and multiplier != 1.0 and multiplier != 'max':
                        # 需要根据原始时间戳和倍数计算间隔
                        # 使用PcapReader来获取时间戳
                        try:
                            from scapy.utils import PcapReader
                            reader = PcapReader(pcap_file)
                            last_timestamp = None
                            packet_index = 0
                            
                            for packet in reader:
                                if stop_replay.is_set():
                                    break
                                
                                # 获取报文时间戳
                                packet_time = packet.time if hasattr(packet, 'time') else time.time()
                                
                                # 计算时间间隔
                                if last_timestamp is not None:
                                    interval = (packet_time - last_timestamp) / multiplier
                                    if interval > 0:
                                        time.sleep(interval)
                                
                                last_timestamp = packet_time
                                
                                # 发送报文
                                try:
                                    # 获取报文的原始字节，确保不修改任何字段
                                    raw_bytes = bytes(packet)
                                    
                                    # 检查报文是否包含以太网层
                                    if Ether in packet:
                                        # 包含以太网层，使用原始套接字发送原始字节
                                        # 这样可以保持所有字段不变（包括校验和）
                                        try:
                                            # 使用原始套接字发送
                                            s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                            s.bind((interface, 0))
                                            s.send(raw_bytes)
                                            s.close()
                                        except Exception as e:
                                            # 如果原始套接字失败，回退到sendp
                                            add_service_log('报文回放', f'使用原始套接字失败，回退到sendp: {e}', 'warning')
                                            sendp(packet, iface=interface, verbose=False)
                                    elif IP in packet:
                                        # 只有IP层，需要添加以太网层
                                        # 获取接口的MAC地址
                                        try:
                                            iface_info = get_if_raw_hwaddr(interface)
                                            if iface_info:
                                                src_mac = iface_info[1]
                                                dst_ip = packet[IP].dst
                                                # 尝试获取目标MAC
                                                try:
                                                    dst_mac = getmacbyip(dst_ip)
                                                    if not dst_mac:
                                                        dst_mac = "ff:ff:ff:ff:ff:ff"
                                                except:
                                                    dst_mac = "ff:ff:ff:ff:ff:ff"
                                                
                                                ether = Ether(src=src_mac, dst=dst_mac)
                                                full_packet = ether / packet
                                                # 使用原始套接字发送，保持原始字段
                                                try:
                                                    raw_bytes = bytes(full_packet)
                                                    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                                    s.bind((interface, 0))
                                                    s.send(raw_bytes)
                                                    s.close()
                                                except Exception as e:
                                                    # 如果原始套接字失败，回退到sendp
                                                    sendp(full_packet, iface=interface, verbose=False)
                                            else:
                                                # 无法获取接口信息，尝试使用默认MAC地址
                                                try:
                                                    # 使用接口名称获取MAC地址
                                                    import psutil
                                                    if PSUTIL_AVAILABLE:
                                                        net_if_addrs = psutil.net_if_addrs()
                                                        if interface in net_if_addrs:
                                                            for addr in net_if_addrs[interface]:
                                                                if addr.family == psutil.AF_LINK:
                                                                    src_mac = addr.address
                                                                    break
                                                    if not src_mac:
                                                        src_mac = "00:00:00:00:00:00"
                                                    
                                                    dst_ip = packet[IP].dst
                                                    dst_mac = "ff:ff:ff:ff:ff:ff"  # 使用广播MAC
                                                    
                                                    ether = Ether(src=src_mac, dst=dst_mac)
                                                    full_packet = ether / packet
                                                    sendp(full_packet, iface=interface, verbose=False)
                                                except:
                                                    # 最后回退：使用send但不带iface参数（会产生警告但能发送）
                                                    send(packet, verbose=False)
                                        except Exception as e:
                                            # 出错时，尝试添加以太网层
                                            try:
                                                src_mac = "00:00:00:00:00:00"
                                                dst_ip = packet[IP].dst
                                                dst_mac = "ff:ff:ff:ff:ff:ff"
                                                ether = Ether(src=src_mac, dst=dst_mac)
                                                full_packet = ether / packet
                                                sendp(full_packet, iface=interface, verbose=False)
                                            except:
                                                # 最后回退：使用send但不带iface参数（会产生警告但能发送）
                                                send(packet, verbose=False)
                                    else:
                                        # 其他情况，尝试使用sendp
                                        sendp(packet, iface=interface, verbose=False)
                                    
                                    with replay_lock:
                                        replay_statistics['packets_sent'] += 1
                                        
                                        # 更新速率
                                        elapsed = time.time() - replay_statistics['start_time']
                                        if elapsed > 0:
                                            replay_statistics['rate'] = int(replay_statistics['packets_sent'] / elapsed)
                                    
                                    # 吞吐限制
                                    if mbps_limit:
                                        packet_size = len(packet) if hasattr(packet, '__len__') else 1500
                                        bytes_per_sec = (mbps_limit * 1024 * 1024) / 8
                                        interval = packet_size / bytes_per_sec
                                        if interval > 0:
                                            time.sleep(interval)
                                
                                except Exception as e:
                                    add_service_log('报文回放', f'发送报文失败: {e}', 'error')
                                    continue
                            
                            reader.close()
                        except Exception as e:
                            add_service_log('报文回放', f'使用PcapReader失败，使用简单模式: {e}', 'warning')
                            # 回退到简单模式
                            for i, packet in enumerate(packets):
                                if stop_replay.is_set():
                                    break
                                
                                try:
                                    # 获取报文的原始字节，确保不修改任何字段
                                    raw_bytes = bytes(packet)
                                    
                                    # 检查报文是否包含以太网层
                                    if Ether in packet:
                                        # 包含以太网层，使用原始套接字发送原始字节
                                        try:
                                            s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                            s.bind((interface, 0))
                                            s.send(raw_bytes)
                                            s.close()
                                        except Exception as e:
                                            # 如果原始套接字失败，回退到sendp
                                            add_service_log('报文回放', f'使用原始套接字失败，回退到sendp: {e}', 'warning')
                                            sendp(packet, iface=interface, verbose=False)
                                    elif IP in packet:
                                        # 只有IP层，需要添加以太网层
                                        try:
                                            iface_info = get_if_raw_hwaddr(interface)
                                            if iface_info:
                                                src_mac = iface_info[1]
                                                dst_ip = packet[IP].dst
                                                try:
                                                    dst_mac = getmacbyip(dst_ip)
                                                    if not dst_mac:
                                                        dst_mac = "ff:ff:ff:ff:ff:ff"
                                                except:
                                                    dst_mac = "ff:ff:ff:ff:ff:ff"
                                                
                                                ether = Ether(src=src_mac, dst=dst_mac)
                                                full_packet = ether / packet
                                                # 使用原始套接字发送
                                                try:
                                                    raw_bytes = bytes(full_packet)
                                                    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                                    s.bind((interface, 0))
                                                    s.send(raw_bytes)
                                                    s.close()
                                                except Exception as e:
                                                    sendp(full_packet, iface=interface, verbose=False)
                                            else:
                                                send(packet, verbose=False)
                                        except:
                                            send(packet, verbose=False)
                                    else:
                                        # 其他情况，尝试使用sendp
                                        sendp(packet, iface=interface, verbose=False)
                                    
                                    with replay_lock:
                                        replay_statistics['packets_sent'] += 1
                                        elapsed = time.time() - replay_statistics['start_time']
                                        if elapsed > 0:
                                            replay_statistics['rate'] = int(replay_statistics['packets_sent'] / elapsed)
                                    
                                    if mbps_limit:
                                        packet_size = len(packet) if hasattr(packet, '__len__') else 1500
                                        bytes_per_sec = (mbps_limit * 1024 * 1024) / 8
                                        interval = packet_size / bytes_per_sec
                                        if interval > 0:
                                            time.sleep(interval)
                                
                                except Exception as e:
                                    add_service_log('报文回放', f'发送报文失败: {e}', 'error')
                                    continue
                    else:
                        # 最大速率或1x速率，直接发送
                        for i, packet in enumerate(packets):
                            if stop_replay.is_set():
                                break
                            
                            try:
                                # 获取报文的原始字节，确保不修改任何字段
                                raw_bytes = bytes(packet)
                                
                                # 检查报文是否包含以太网层
                                if Ether in packet:
                                    # 包含以太网层，使用原始套接字发送原始字节
                                    try:
                                        s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                        s.bind((interface, 0))
                                        s.send(raw_bytes)
                                        s.close()
                                    except Exception as e:
                                        # 如果原始套接字失败，回退到sendp
                                        add_service_log('报文回放', f'使用原始套接字失败，回退到sendp: {e}', 'warning')
                                        sendp(packet, iface=interface, verbose=False)
                                elif IP in packet:
                                    # 只有IP层，需要添加以太网层
                                    try:
                                        iface_info = get_if_raw_hwaddr(interface)
                                        if iface_info:
                                            src_mac = iface_info[1]
                                            dst_ip = packet[IP].dst
                                            try:
                                                dst_mac = getmacbyip(dst_ip)
                                                if not dst_mac:
                                                    dst_mac = "ff:ff:ff:ff:ff:ff"
                                            except:
                                                dst_mac = "ff:ff:ff:ff:ff:ff"
                                            
                                            ether = Ether(src=src_mac, dst=dst_mac)
                                            full_packet = ether / packet
                                            # 使用原始套接字发送
                                            try:
                                                raw_bytes = bytes(full_packet)
                                                s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
                                                s.bind((interface, 0))
                                                s.send(raw_bytes)
                                                s.close()
                                            except Exception as e:
                                                sendp(full_packet, iface=interface, verbose=False)
                                        else:
                                            # 无法获取接口信息，尝试使用默认MAC地址
                                            try:
                                                src_mac = "00:00:00:00:00:00"
                                                dst_ip = packet[IP].dst
                                                dst_mac = "ff:ff:ff:ff:ff:ff"
                                                ether = Ether(src=src_mac, dst=dst_mac)
                                                full_packet = ether / packet
                                                sendp(full_packet, iface=interface, verbose=False)
                                            except:
                                                send(packet, verbose=False)
                                    except Exception as e:
                                        # 出错时，尝试添加以太网层
                                        try:
                                            src_mac = "00:00:00:00:00:00"
                                            dst_ip = packet[IP].dst
                                            dst_mac = "ff:ff:ff:ff:ff:ff"
                                            ether = Ether(src=src_mac, dst=dst_mac)
                                            full_packet = ether / packet
                                            sendp(full_packet, iface=interface, verbose=False)
                                        except:
                                            send(packet, verbose=False)
                                else:
                                    # 其他情况，尝试使用sendp
                                    sendp(packet, iface=interface, verbose=False)
                                
                                with replay_lock:
                                    replay_statistics['packets_sent'] += 1
                                    
                                    # 更新速率
                                    elapsed = time.time() - replay_statistics['start_time']
                                    if elapsed > 0:
                                        replay_statistics['rate'] = int(replay_statistics['packets_sent'] / elapsed)
                                
                                # 吞吐限制
                                if mbps_limit:
                                    packet_size = len(packet) if hasattr(packet, '__len__') else 1500
                                    bytes_per_sec = (mbps_limit * 1024 * 1024) / 8
                                    interval = packet_size / bytes_per_sec
                                    if interval > 0:
                                        time.sleep(interval)
                            
                            except Exception as e:
                                add_service_log('报文回放', f'发送报文失败: {e}', 'error')
                                continue
                    
                    add_service_log('报文回放', f'文件 {pcap_file} 回放完成', 'success')
                
                except Exception as e:
                    add_service_log('报文回放', f'回放文件 {pcap_file} 失败: {e}', 'error')
                    import traceback
                    add_service_log('报文回放', traceback.format_exc(), 'error')
                    continue
            
            if loop_count != -1:
                current_loop += 1
                if current_loop < loop_count:
                    add_service_log('报文回放', f'开始第 {current_loop + 1} 次循环回放', 'info')
        
        add_service_log('报文回放', '回放完成', 'success')
    
    except Exception as e:
        add_service_log('报文回放', f'回放工作线程出错: {e}', 'error')
        import traceback
        add_service_log('报文回放', traceback.format_exc(), 'error')
    
    finally:
        with replay_lock:
            replay_statistics['running'] = False
            replay_statistics['current_file'] = None


@app.route('/api/packet_replay/start', methods=['POST'])
def api_packet_replay_start():
    """启动报文回放"""
    global replay_thread, stop_replay
    
    try:
        data = request.json or {}
        interface = data.get('interface', '').strip()
        pcap_files = data.get('pcap_files', [])
        replay_rate = data.get('replay_rate', '1')
        replay_count = data.get('replay_count', 1)
        replay_throughput = data.get('replay_throughput')
        
        if not interface:
            return jsonify({'success': False, 'error': '缺少网口参数'}), 400
        
        if not pcap_files:
            return jsonify({'success': False, 'error': '缺少pcap文件列表'}), 400
        
        # 检查文件是否存在
        import os
        valid_files = []
        for pcap_file in pcap_files:
            if os.path.exists(pcap_file):
                valid_files.append(pcap_file)
            else:
                add_service_log('报文回放', f'文件不存在: {pcap_file}', 'warning')
        
        if not valid_files:
            return jsonify({'success': False, 'error': '没有找到有效的pcap文件'}), 400
        
        # 检查是否已在运行
        with replay_lock:
            if replay_statistics['running']:
                return jsonify({'success': False, 'error': '回放已在运行中'}), 400
        
        # 重置停止事件
        stop_replay.clear()
        
        # 启动回放线程
        replay_thread = threading.Thread(
            target=replay_pcap_worker,
            args=(interface, valid_files, replay_rate, replay_count, replay_throughput),
            daemon=True
        )
        replay_thread.start()
        
        return jsonify({
            'success': True,
            'message': '回放已启动',
            'files_count': len(valid_files)
        })
    
    except Exception as e:
        add_service_log('报文回放', f'启动回放失败: {e}', 'error')
        import traceback
        add_service_log('报文回放', traceback.format_exc(), 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/packet_replay/stop', methods=['POST'])
def api_packet_replay_stop():
    """停止报文回放"""
    global stop_replay
    
    try:
        stop_replay.set()
        
        with replay_lock:
            if not replay_statistics['running']:
                return jsonify({'success': False, 'error': '回放未运行'}), 400
        
        add_service_log('报文回放', '正在停止回放...', 'info')
        
        return jsonify({'success': True, 'message': '回放已停止'})
    
    except Exception as e:
        add_service_log('报文回放', f'停止回放失败: {e}', 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/packet_replay/status', methods=['GET'])
def api_packet_replay_status():
    """获取回放状态"""
    try:
        with replay_lock:
            if not replay_statistics['running']:
                return jsonify({
                    'success': True,
                    'running': False
                })
            
            elapsed = time.time() - replay_statistics['start_time'] if replay_statistics['start_time'] else 0
            
            return jsonify({
                'success': True,
                'running': True,
                'packets_sent': replay_statistics['packets_sent'],
                'rate': replay_statistics['rate'],
                'current_file': replay_statistics['current_file'],
                'current_file_index': replay_statistics['current_file_index'],
                'total_files': replay_statistics['total_files'],
                'elapsed_time': elapsed
            })
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/packet_replay/upload', methods=['POST'])
def api_packet_replay_upload():
    """上传pcap文件到agent"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '文件名为空'}), 400
        
        # 检查文件扩展名
        if not file.filename.lower().endswith(('.pcap', '.cap')):
            return jsonify({'success': False, 'error': '只支持pcap/cap文件'}), 400
        
        # 保存文件
        import os
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, file.filename)
        file.save(file_path)
        
        add_service_log('报文回放', f'文件上传成功: {file.filename}', 'success')
        
        return jsonify({
            'success': True,
            'file_path': file_path,
            'filename': file.filename
        })
    
    except Exception as e:
        add_service_log('报文回放', f'文件上传失败: {e}', 'error')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/port_scan/progress', methods=['GET'])
def api_port_scan_progress():
    """获取端口扫描进度"""
    try:
        scan_id = request.args.get('scan_id', '')
        
        if not scan_id:
            return jsonify({
                'success': False,
                'error': '缺少scan_id参数'
            }), 400
        
        with port_scan_lock:
            if scan_id not in port_scan_sessions:
                return jsonify({
                    'success': False,
                    'error': '扫描会话不存在'
                }), 404
            
            session = port_scan_sessions[scan_id]
            
            # 如果扫描完成，返回完整结果
            if session.get('completed', False):
                return jsonify({
                    'success': True,
                    'completed': True,
                    'cancelled': session.get('cancelled', False),  # 返回是否被取消
                    'progress': {
                        'scanned': session['scanned'],
                        'total': session['total_ports'],
                        'open_ports': session.get('open_ports', []),
                        'closed_ports': session.get('closed_ports', []),
                        'results': session.get('results', [])
                    },
                    'total_ports': session['total_ports'],
                    'open_ports': session.get('open_ports', []),
                    'closed_ports': session.get('closed_ports', []),
                    'results': session.get('results', [])
                })
            
            # 返回进度信息（每5秒更新一次）
            return jsonify({
                'success': True,
                'completed': False,
                'cancelled': session.get('cancelled', False),  # 返回是否被取消
                'progress': {
                    'scanned': session['scanned'],
                    'total': session['total_ports'],
                    'open_ports': session.get('open_ports', []),
                    'closed_ports': session.get('closed_ports', []),
                    'current_port': session.get('current_port'),
                    'results': session.get('results', [])  # 包含所有端口结果（开放和关闭）
                }
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='报文发送代理程序')
    parser.add_argument('--host', default='0.0.0.0', help='监听地址')
    parser.add_argument('--port', type=int, default=8888, help='监听端口')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--test', action='store_true', help='测试模式，检查环境后退出')
    
    args = parser.parse_args()
    
    try:
        import platform
        import socket
        
        print("=" * 60)
        print("*** 报文发送代理程序启动中... VERSION: 2025-11-27-10:45-MAIL-FIX ***")
        print("*** [文件下发] 代理程序文件已部署到远程主机 ***")
        print("*** [文件下发] 包含文件: packet_agent.py, industrial_protocol_agent.py 及相关依赖文件 ***")
        print("=" * 60)
        print("操作系统: {}".format(platform.system()))
        print("Python版本: {}".format(platform.python_version()))
        print("监听地址: {}:{}".format(args.host, args.port))
        print("API文档: http://{}:{}/api/interfaces".format(args.host, args.port))
        
        # 快速测试网卡获取功能 (仅在测试模式下详细显示)
        if args.test:
            print("\n测试网卡获取功能...")
            try:
                test_interfaces = get_interfaces()
                print("测试成功，检测到 {} 个可用网卡".format(len(test_interfaces)))
                for iface in test_interfaces:
                    print("  - {} ({}): IP={}, MAC={}".format(
                        iface.get('display_name', 'N/A'),
                        iface.get('name', 'N/A'),
                        iface.get('ip', 'N/A'),
                        iface.get('mac', 'N/A')
                    ))
            except Exception as e:
                print("测试网卡获取失败: {}".format(e))
                import traceback
                traceback.print_exc()
        else:
            # 正常启动时只做快速检查
            try:
                test_interfaces = get_interfaces()
                print("网卡检查: 检测到 {} 个可用网卡".format(len(test_interfaces)))
            except Exception as e:
                print("网卡检查失败: {}".format(e))
        
        print("\n" + "=" * 60)
        print("代理程序已启动，等待连接...")
        print("=" * 60 + "\n")
        
        # 添加请求日志中间件
        @app.before_request
        def log_request():
            # 跳过OPTIONS请求的日志（CORS预检请求）
            if request.method == 'OPTIONS':
                return
            print(f"[REQUEST] {request.method} {request.path} from {request.remote_addr}")
            if request.path.startswith('/api/services/client'):
                print(f"[REQUEST] *** 邮件客户端请求 *** Headers: {dict(request.headers)}")
                print(f"[REQUEST] *** 邮件客户端请求 *** Body: {request.get_data()}")
                # 强制刷新输出
                import sys
                sys.stdout.flush()
        
        # 健康检查API已在上面定义
        
        # 添加状态检查API
        @app.route('/api/status', methods=['GET'])
        def api_status():
            """Agent状态检查"""
            try:
                import psutil
                import os
                
                # 获取当前进程信息
                current_process = psutil.Process(os.getpid())
                
                status_info = {
                    'status': 'running',
                    'pid': os.getpid(),
                    'port': args.port,
                    'uptime': time.time() - start_time if start_time else 0,
                    'memory_usage': current_process.memory_info().rss / 1024 / 1024,  # MB
                    'cpu_percent': current_process.cpu_percent(),
                    'listeners': {
                        protocol: state.get('running', False)
                        for protocol, state in listener_states.items()
                    },
                    'clients': {
                        protocol: state.get('running', False)
                        for protocol, state in client_states.items()
                    }
                }
                
                return jsonify({'success': True, 'data': status_info})
            except Exception as e:
                return jsonify({'success': False, 'error': str(e)}), 500
        
        # 添加优雅停止API
        @app.route('/api/shutdown', methods=['POST'])
        def api_shutdown():
            """优雅停止Agent"""
            import os
            import signal
            def shutdown_handler():
                time.sleep(1)
                os.kill(os.getpid(), signal.SIGTERM)
            threading.Thread(target=shutdown_handler, daemon=True).start()
            return jsonify({'success': True, 'message': '正在关闭...'})
        
        # ========== DHCP客户端功能 ==========
        
        # DHCP消息类型常量
        DHCP_DISCOVER = 1
        DHCP_OFFER = 2
        DHCP_REQUEST = 3
        DHCP_DECLINE = 4
        DHCP_ACK = 5
        DHCP_NAK = 6
        DHCP_RELEASE = 7
        DHCP_INFORM = 8
        
        # DHCP选项类型常量
        DHCP_OPT_MESSAGE_TYPE = 53
        DHCP_OPT_REQUESTED_IP = 50
        DHCP_OPT_SERVER_ID = 54
        DHCP_OPT_PARAM_REQUEST_LIST = 55
        DHCP_OPT_SUBNET_MASK = 1
        DHCP_OPT_ROUTER = 3
        DHCP_OPT_DNS_SERVER = 6
        DHCP_OPT_DOMAIN_NAME = 15
        
        # DHCP客户端会话管理
        dhcp_client_sessions = {}
        dhcp_client_lock = threading.Lock()
        
        # 共享的DHCP响应接收socket（所有客户端共享）
        dhcp_receiver_socket = None
        dhcp_receiver_lock = threading.Lock()
        dhcp_pending_responses = {}  # {xid: response_data}
        dhcp_receiver_thread = None
        dhcp_receiver_stop = threading.Event()
        dhcp_receiver_interface = None  # 接收器使用的接口
        
        def dhcp_receiver_worker(interface=None):
            """DHCP响应接收工作线程（使用socket接收，更可靠）"""
            global dhcp_receiver_socket, dhcp_pending_responses, dhcp_receiver_interface
            client_port = 68  # DHCP客户端端口
            dhcp_receiver_interface = interface
            
            try:
                # 在Windows上，接收广播包必须绑定到0.0.0.0，绑定到特定IP无法接收广播
                # DHCP响应是广播包，所以必须绑定到0.0.0.0才能接收
                bind_ip = '0.0.0.0'  # 必须绑定到0.0.0.0以接收广播包
                
                # 创建UDP socket接收DHCP响应
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                
                # 尝试绑定到0.0.0.0:68（必须绑定到0.0.0.0才能接收广播）
                try:
                    sock.bind((bind_ip, client_port))
                    add_service_log('DHCP客户端', f'DHCP响应接收器已启动，监听 {bind_ip}:{client_port} (接收广播包)', 'info')
                except OSError as bind_err:
                    # 端口68可能被占用（如系统DHCP客户端），使用随机端口
                    # 注意：使用随机端口时，DHCP服务器可能无法正确响应
                    try:
                        sock.bind((bind_ip, 0))
                        actual_port = sock.getsockname()[1]
                        add_service_log('DHCP客户端', f'端口68被占用，使用随机端口 {bind_ip}:{actual_port} 接收DHCP响应', 'warning')
                        add_service_log('DHCP客户端', '警告：使用随机端口可能导致DHCP服务器无法正确响应', 'warning')
                    except Exception as e:
                        add_service_log('DHCP客户端', f'绑定失败: {e}', 'error')
                        raise
                
                sock.settimeout(1.0)
                dhcp_receiver_socket = sock
                actual_bind = sock.getsockname()
                add_service_log('DHCP客户端', f'接收器开始监听，实际绑定地址: {actual_bind[0]}:{actual_bind[1]}', 'info')
                
                # 持续接收DHCP响应
                while not dhcp_receiver_stop.is_set():
                    try:
                        data, addr = sock.recvfrom(1024)
                        add_service_log('DHCP客户端', f'收到UDP数据包，来源: {addr[0]}:{addr[1]}, 长度: {len(data)}', 'debug')
                        
                        # 验证是否是DHCP响应（检查Magic Cookie）
                        if len(data) < 240:  # DHCP最小长度
                            continue
                        
                        # 检查Magic Cookie（偏移236字节）
                        magic_cookie = data[236:240]
                        if magic_cookie != struct.pack('!I', 0x63825363):
                            continue
                        
                        add_service_log('DHCP客户端', f'收到DHCP响应，来源: {addr[0]}:{addr[1]}, 长度: {len(data)}', 'info')
                        
                        # 解析DHCP响应获取xid
                        try:
                            parsed = parse_dhcp_response(data)
                            xid = parsed['xid']
                            msg_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)
                            
                            # 处理消息类型：可能是单字节或包含长度的字节串
                            if msg_type:
                                # 如果长度大于1，取第一个字节
                                if len(msg_type) > 1:
                                    msg_type = msg_type[:1]
                                # 确保是字节类型
                                if isinstance(msg_type, int):
                                    msg_type = bytes([msg_type])
                            
                            msg_type_name = {b'\x02': 'Offer', b'\x05': 'Ack', b'\x06': 'NAK'}.get(msg_type, f'Unknown({msg_type.hex() if msg_type else "None"})')
                            msg_type_str = msg_type.hex() if msg_type else 'None'
                            add_service_log('DHCP客户端', f'解析DHCP响应成功: xid={hex(xid)}, 类型={msg_type_name}, 原始值={msg_type_str}', 'info')
                            
                            # 存储响应，等待对应的客户端线程读取
                            with dhcp_client_lock:
                                dhcp_pending_responses[xid] = {
                                    'data': data,
                                    'parsed': parsed,
                                    'timestamp': time.time()
                                }
                            add_service_log('DHCP客户端', f'已存储响应，等待客户端读取: xid={hex(xid)}', 'info')
                        except Exception as parse_err:
                            # 解析失败，记录错误但继续
                            add_service_log('DHCP客户端', f'解析DHCP响应失败: {parse_err}', 'warning')
                            import traceback
                            traceback.print_exc()
                            
                    except socket.timeout:
                        # 超时是正常的，继续循环
                        continue
                    except Exception as e:
                        if not dhcp_receiver_stop.is_set():
                            add_service_log('DHCP客户端', f'接收响应时出错: {e}', 'error')
                            import traceback
                            traceback.print_exc()
                        time.sleep(0.1)  # 出错后稍作等待
                        continue
            except Exception as e:
                add_service_log('DHCP客户端', f'DHCP响应接收器启动失败: {e}', 'error')
                import traceback
                traceback.print_exc()
            finally:
                if dhcp_receiver_socket:
                    try:
                        dhcp_receiver_socket.close()
                    except:
                        pass
                dhcp_receiver_socket = None
                add_service_log('DHCP客户端', 'DHCP响应接收器已停止', 'info')
        
        def start_dhcp_receiver(interface=None):
            """启动DHCP响应接收器"""
            global dhcp_receiver_thread, dhcp_receiver_interface
            with dhcp_client_lock:
                # 如果接收器已启动且接口相同，不需要重启
                if dhcp_receiver_thread is not None and dhcp_receiver_thread.is_alive():
                    if dhcp_receiver_interface == interface:
                        return  # 已启动且接口相同，直接返回
                    else:
                        # 接口不同，需要停止旧接收器
                        dhcp_receiver_stop.set()
                        dhcp_receiver_thread.join(timeout=2.0)
                
                # 启动新接收器
                dhcp_receiver_stop.clear()
                dhcp_receiver_thread = threading.Thread(target=dhcp_receiver_worker, args=(interface,), daemon=True)
                dhcp_receiver_thread.start()
                time.sleep(0.5)  # 等待接收器就绪
        
        def get_dhcp_response(xid, timeout=10, message_type=None):
            """从共享接收器获取指定xid的DHCP响应"""
            start_time = time.time()
            while time.time() - start_time < timeout:
                with dhcp_client_lock:
                    if xid in dhcp_pending_responses:
                        response_info = dhcp_pending_responses.pop(xid)
                        parsed = response_info['parsed']
                        # 如果指定了消息类型，检查是否匹配
                        if message_type is not None:
                            opt_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)
                            
                            # 处理消息类型：可能是单字节或包含长度的字节串
                            if opt_type:
                                # 如果长度大于1，取第一个字节
                                if len(opt_type) > 1:
                                    opt_type = opt_type[:1]
                                # 确保是字节类型
                                if isinstance(opt_type, int):
                                    opt_type = bytes([opt_type])
                            
                            # 比较消息类型
                            if opt_type != message_type:
                                # 类型不匹配，放回去继续等待
                                dhcp_pending_responses[xid] = response_info
                                time.sleep(0.1)
                                continue
                        return parsed
                time.sleep(0.1)
            
            # 超时后，再检查一次响应（防止响应在超时边界到达）
            with dhcp_client_lock:
                if xid in dhcp_pending_responses:
                    response_info = dhcp_pending_responses.pop(xid)
                    parsed = response_info['parsed']
                    # 如果指定了消息类型，检查是否匹配
                    if message_type is not None:
                        opt_type = parsed['options'].get(DHCP_OPT_MESSAGE_TYPE)
                        
                        # 处理消息类型：可能是单字节或包含长度的字节串
                        if opt_type:
                            # 如果长度大于1，取第一个字节
                            if len(opt_type) > 1:
                                opt_type = opt_type[:1]
                            # 确保是字节类型
                            if isinstance(opt_type, int):
                                opt_type = bytes([opt_type])
                        
                        if opt_type == message_type:
                            add_service_log('DHCP客户端', f'在超时后找到匹配的响应: xid={hex(xid)}', 'info')
                            return parsed
                        else:
                            # 类型不匹配，放回去
                            dhcp_pending_responses[xid] = response_info
                    else:
                        # 没有指定类型，直接返回
                        add_service_log('DHCP客户端', f'在超时后找到响应: xid={hex(xid)}', 'info')
                        return parsed
            
            return None
        
        def mac_str_to_bytes(mac_str):
            """将MAC字符串（如'00:11:22:33:44:55'）转换为字节串"""
            return bytes.fromhex(mac_str.replace(':', '').replace('-', ''))
        
        def mac_bytes_to_str(mac_bytes):
            """将MAC字节串转换为格式化字符串"""
            return ':'.join(f'{b:02x}' for b in mac_bytes)
        
        def generate_mac_list(start_mac, count):
            """生成连续的MAC地址列表"""
            start_mac_int = int(start_mac.replace(':', '').replace('-', ''), 16)
            mac_list = []
            for i in range(count):
                current_mac_int = start_mac_int + i
                current_mac_bytes = current_mac_int.to_bytes(6, byteorder='big')
                mac_list.append(current_mac_bytes)
            return mac_list
        
        def build_dhcp_discover(xid, mac):
            """构建DHCP Discover报文"""
            header = struct.pack(
                '!BBBBIHH4s4s4s4s6s10s64s128s',
                1, 1, 6, 0,                # op, htype, hlen, hops
                xid, 0, 0x8000,             # xid, secs, flags (广播标志)
                b'\x00'*4, b'\x00'*4,       # ciaddr, yiaddr
                b'\x00'*4, b'\x00'*4,       # siaddr, giaddr
                mac, b'\x00'*10,            # chaddr (MAC) + pad
                b'\x00'*64, b'\x00'*128     # sname, file
            )
            
            options = b''
            options += struct.pack('!I', 0x63825363)  # Magic Cookie
            options += struct.pack('!BBB', DHCP_OPT_MESSAGE_TYPE, 1, DHCP_DISCOVER)  # 消息类型：Discover
            options += struct.pack('!BBBBBB', DHCP_OPT_PARAM_REQUEST_LIST, 4, DHCP_OPT_SUBNET_MASK, DHCP_OPT_ROUTER, DHCP_OPT_DNS_SERVER, DHCP_OPT_DOMAIN_NAME)  # 请求参数
            options += b'\xff'  # 选项结束符
            
            return header + options
        
        def build_dhcp_request(xid, mac, requested_ip, server_ip):
            """构建DHCP Request报文"""
            header = struct.pack(
                '!BBBBIHH4s4s4s4s6s10s64s128s',
                1, 1, 6, 0,                # op, htype, hlen, hops
                xid, 0, 0x8000,             # xid, secs, flags (广播标志)
                b'\x00'*4, b'\x00'*4,       # ciaddr, yiaddr
                b'\x00'*4, b'\x00'*4,       # siaddr, giaddr
                mac, b'\x00'*10,            # chaddr (MAC) + pad
                b'\x00'*64, b'\x00'*128     # sname, file
            )
            
            options = b''
            options += struct.pack('!I', 0x63825363)  # Magic Cookie
            options += struct.pack('!BBB', DHCP_OPT_MESSAGE_TYPE, 1, DHCP_REQUEST)  # 消息类型：Request
            options += struct.pack('!BB4s', DHCP_OPT_REQUESTED_IP, 4, requested_ip)  # 请求的IP
            options += struct.pack('!BB4s', DHCP_OPT_SERVER_ID, 4, server_ip)  # 服务器ID
            options += b'\xff'  # 选项结束符
            
            return header + options
        
        def parse_dhcp_response(response):
            """解析DHCP响应报文（Offer/Ack）"""
            header = struct.unpack('!BBBBIHH4s4s4s4s6s10s64s128s', response[:236])
            op, htype, hlen, hops, xid, secs, flags, ciaddr, yiaddr, siaddr, giaddr, chaddr, _, _, _ = header
            
            options = response[236:]
            magic_cookie = options[:4]
            if magic_cookie != struct.pack('!I', 0x63825363):
                raise ValueError("无效的DHCP Magic Cookie")
            
            opt_data = options[4:]
            parsed_options = {}
            i = 0
            while i < len(opt_data):
                opt_type = opt_data[i]
                if opt_type == 0:  # 填充选项，跳过
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
                'yiaddr': yiaddr,  # 分配的IP
                'siaddr': siaddr,  # 服务器IP
                'options': parsed_options
            }
        
        def dhcp_client_task(client_id, mac, interface, timeout=10, session_id=None):
            """单个DHCP客户端任务（线程执行）- 使用scapy发送，共享socket接收"""
            client_port = 68
            server_port = 67
            xid = random.randint(0, 0xFFFFFFFF)  # 每个客户端独立事务ID
            task_start_time = time.time()  # 记录任务开始时间
            
            try:
                # 确保DHCP响应接收器已启动（使用相同的接口）
                start_dhcp_receiver(interface)
                # 等待接收器就绪
                time.sleep(0.2)
                
                # 获取目标MAC地址（广播地址）
                dst_mac = "ff:ff:ff:ff:ff:ff"
                
                # 1. 发送Discover
                discover_pkt_bytes = build_dhcp_discover(xid, mac)
                # 使用scapy构造完整的UDP包
                discover_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=discover_pkt_bytes)
                discover_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / discover_udp
                
                # 发送Discover包（使用L2发送以指定接口）
                if interface:
                    sendp(discover_ether, iface=interface, verbose=False)
                else:
                    send(discover_udp, verbose=False)
                
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 发送Discover（xid: {hex(xid)}）', 'info')
                
                # 更新会话状态 - 初始化客户端条目，设置完整的默认值
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
                
                # 2. 接收Offer - 从共享接收器获取
                # 计算剩余超时时间
                elapsed_time = time.time() - task_start_time
                remaining_timeout = max(1.0, timeout - elapsed_time)  # 至少保留1秒
                offer_response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=b'\x02')
                
                if not offer_response:
                    raise socket.timeout("未收到Offer响应")
                
                offered_ip = offer_response['yiaddr']
                server_ip = offer_response['siaddr']
                offered_ip_str = socket.inet_ntoa(offered_ip)
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 收到Offer，分配IP: {offered_ip_str}', 'info')
                
                # 检查是否有其他客户端也收到了相同的IP（并发问题检测）
                ip_conflict = False
                if session_id:
                    with dhcp_client_lock:
                        if session_id in dhcp_client_sessions:
                            for other_client_id, other_client_data in dhcp_client_sessions[session_id]['clients'].items():
                                if other_client_id != client_id and other_client_data.get('offered_ip') == offered_ip_str:
                                    # 检查其他客户端是否已经发送了Request（状态为request_sent或completed）
                                    other_status = other_client_data.get('status', '')
                                    if other_status in ['request_sent', 'completed', 'offer_received']:
                                        ip_conflict = True
                                        add_service_log('DHCP客户端', f'[警告] 客户端{client_id}收到与客户端{other_client_id}相同的IP: {offered_ip_str}，将重新发送Discover', 'warning')
                                        break
                
                # 如果检测到IP冲突，重新发送Discover（最多重试3次）
                retry_count = 0
                max_retries = 3
                while ip_conflict and retry_count < max_retries:
                    retry_count += 1
                    add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 重新发送Discover（重试{retry_count}/{max_retries}）', 'info')
                    
                    # 生成新的xid
                    xid = random.randint(0, 0xFFFFFFFF)
                    
                    # 重新发送Discover
                    discover_pkt_bytes = build_dhcp_discover(xid, mac)
                    discover_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=discover_pkt_bytes)
                    discover_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / discover_udp
                    
                    if interface:
                        sendp(discover_ether, iface=interface, verbose=False)
                    else:
                        send(discover_udp, verbose=False)
                    
                    # 等待新的Offer
                    elapsed_time = time.time() - task_start_time
                    remaining_timeout = max(1.0, timeout - elapsed_time)
                    offer_response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=b'\x02')
                    
                    if not offer_response:
                        raise socket.timeout("未收到Offer响应（重试后）")
                    
                    offered_ip = offer_response['yiaddr']
                    server_ip = offer_response['siaddr']
                    offered_ip_str = socket.inet_ntoa(offered_ip)
                    add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 收到新的Offer，分配IP: {offered_ip_str}', 'info')
                    
                    # 再次检查IP冲突
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
                    error_msg = f'多次重试后仍收到冲突的IP: {offered_ip_str}'
                    add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} {error_msg}', 'error')
                    raise ValueError(error_msg)
                
                # 更新会话状态
                if session_id:
                    with dhcp_client_lock:
                        if session_id in dhcp_client_sessions:
                            dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'offer_received'
                            dhcp_client_sessions[session_id]['clients'][client_id]['offered_ip'] = socket.inet_ntoa(offered_ip)
                
                # 3. 发送Request
                request_pkt_bytes = build_dhcp_request(xid, mac, offered_ip, server_ip)
                request_udp = IP(src="0.0.0.0", dst="255.255.255.255") / UDP(sport=client_port, dport=server_port) / Raw(load=request_pkt_bytes)
                request_ether = Ether(src=mac_bytes_to_str(mac), dst=dst_mac) / request_udp
                
                if interface:
                    sendp(request_ether, iface=interface, verbose=False)
                else:
                    send(request_udp, verbose=False)
                
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 发送Request，请求IP: {socket.inet_ntoa(offered_ip)}', 'info')
                
                # 更新会话状态
                if session_id:
                    with dhcp_client_lock:
                        if session_id in dhcp_client_sessions:
                            dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'request_sent'
                
                # 4. 接收Ack或NAK - 从共享接收器获取
                # 计算剩余超时时间
                elapsed_time = time.time() - task_start_time
                remaining_timeout = max(1.0, timeout - elapsed_time)  # 至少保留1秒
                
                # 等待Ack或NAK响应（不指定类型，接收任何响应）
                response = get_dhcp_response(xid, timeout=remaining_timeout, message_type=None)
                
                if not response:
                    raise socket.timeout("未收到Ack或NAK响应")
                
                # 检查响应类型
                msg_type = response['options'].get(DHCP_OPT_MESSAGE_TYPE)
                if msg_type:
                    if len(msg_type) > 1:
                        msg_type = msg_type[:1]
                    if isinstance(msg_type, int):
                        msg_type = bytes([msg_type])
                
                if msg_type == b'\x06':  # NAK
                    # 收到NAK，服务器拒绝了请求
                    error_msg = f'服务器拒绝请求（NAK），请求的IP: {socket.inet_ntoa(offered_ip)}'
                    add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} {error_msg}', 'error')
                    raise ValueError(error_msg)
                elif msg_type != b'\x05':  # 不是Ack
                    error_msg = f'收到意外的响应类型: {msg_type.hex() if msg_type else "None"}'
                    add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} {error_msg}', 'error')
                    raise ValueError(error_msg)
                
                ack_response = response
                
                # 解析结果
                result = {
                    'client_id': client_id,
                    'mac': mac_bytes_to_str(mac),
                    'ip': socket.inet_ntoa(ack_response['yiaddr']),
                    'server_ip': socket.inet_ntoa(ack_response['siaddr']),
                    'success': True
                }
                
                # 解析额外选项
                if DHCP_OPT_SUBNET_MASK in ack_response['options']:
                    result['subnet_mask'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_SUBNET_MASK])
                if DHCP_OPT_ROUTER in ack_response['options']:
                    result['gateway'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_ROUTER])
                if DHCP_OPT_DNS_SERVER in ack_response['options']:
                    result['dns'] = socket.inet_ntoa(ack_response['options'][DHCP_OPT_DNS_SERVER])
                
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {result["mac"]} IP分配成功！IP: {result["ip"]}', 'success')
                
                # 更新会话状态
                if session_id:
                    with dhcp_client_lock:
                        if session_id in dhcp_client_sessions:
                            dhcp_client_sessions[session_id]['clients'][client_id] = result
                            dhcp_client_sessions[session_id]['clients'][client_id]['status'] = 'completed'
                            dhcp_client_sessions[session_id]['success_count'] = dhcp_client_sessions[session_id].get('success_count', 0) + 1
                
                return result
                
            except socket.timeout:
                error_msg = f'超时（{timeout}秒）'
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} {error_msg}', 'error')
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
                add_service_log('DHCP客户端', f'[客户端{client_id}] MAC: {mac_bytes_to_str(mac)} 错误: {error_msg}', 'error')
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
        
        @app.route('/api/dhcp_client/start', methods=['POST'])
        def api_dhcp_client_start():
            """启动DHCP客户端"""
            try:
                data = request.get_json()
                count = int(data.get('count', 1))
                start_mac = data.get('start_mac', '00:11:22:33:44:01')
                interface = data.get('interface', '')
                timeout = int(data.get('timeout', 10))
                max_workers = int(data.get('max_workers', 10))
                
                # 验证MAC地址格式
                try:
                    mac_str_to_bytes(start_mac)
                except ValueError:
                    return jsonify({
                        'success': False,
                        'error': '无效的MAC地址格式，请使用如00:11:22:33:44:55的格式'
                    }), 400
                
                # 生成MAC地址列表
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
                
                add_service_log('DHCP客户端', f'启动 {count} 个DHCP客户端，起始MAC: {start_mac}', 'info')
                
                # 使用线程池并行执行
                def run_clients():
                    results = []
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_client = {
                            executor.submit(dhcp_client_task, i+1, mac, interface, timeout, session_id): i+1
                            for i, mac in enumerate(mac_list)
                        }
                        
                        for future in future_to_client:
                            result = future.result()
                            results.append(result)
                    
                    # 标记会话完成
                    with dhcp_client_lock:
                        if session_id in dhcp_client_sessions:
                            dhcp_client_sessions[session_id]['completed'] = True
                            dhcp_client_sessions[session_id]['end_time'] = time.time()
                    
                    add_service_log('DHCP客户端', f'DHCP客户端任务完成，成功: {sum(1 for r in results if r.get("success"))}/{count}', 'info')
                
                # 在后台线程中运行
                thread = threading.Thread(target=run_clients, daemon=True)
                thread.start()
                
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'message': f'已启动 {count} 个DHCP客户端'
                })
                
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        
        @app.route('/api/dhcp_client/status', methods=['GET'])
        def api_dhcp_client_status():
            """获取DHCP客户端状态"""
            try:
                session_id = request.args.get('session_id', '')
                
                if not session_id:
                    return jsonify({
                        'success': False,
                        'error': '缺少session_id参数'
                    }), 400
                
                with dhcp_client_lock:
                    if session_id not in dhcp_client_sessions:
                        return jsonify({
                            'success': False,
                            'error': '会话不存在'
                        }), 404
                    
                    session = dhcp_client_sessions[session_id]
                    clients = list(session['clients'].values())
                    
                    return jsonify({
                        'success': True,
                        'session_id': session_id,
                        'count': session['count'],
                        'completed': session.get('completed', False),
                        'success_count': session.get('success_count', 0),
                        'failed_count': session.get('failed_count', 0),
                        'clients': clients,
                        'start_time': session.get('start_time'),
                        'end_time': session.get('end_time')
                    })
                    
            except Exception as e:
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
        def api_shutdown():
            """优雅停止Agent"""
            try:
                print("[INFO] 收到停止请求，准备关闭Agent...")
                
                # 停止所有服务
                stop_all_services()
                
                # 延迟关闭，给响应时间
                import threading
                def delayed_shutdown():
                    import time
                    time.sleep(1)
                    print("[INFO] Agent正在关闭...")
                    import os
                    os._exit(0)
                
                shutdown_thread = threading.Thread(target=delayed_shutdown)
                shutdown_thread.daemon = True
                shutdown_thread.start()
                
                return jsonify({'success': True, 'message': 'Agent正在关闭...'})
            except Exception as e:
                print(f"[ERROR] 停止Agent异常: {e}")
                return jsonify({'success': False, 'error': str(e)}), 500

        def stop_all_services():
            """停止所有正在运行的服务"""
            try:
                # 停止所有监听服务
                for protocol in list(listener_states.keys()):
                    if listener_states[protocol].get('running', False):
                        print(f"[INFO] 停止监听服务: {protocol}")
                        stop_listener(protocol)
                
                # 停止所有客户端服务  
                for protocol in list(client_states.keys()):
                    if client_states[protocol].get('running', False):
                        print(f"[INFO] 停止客户端服务: {protocol}")
                        # 这里可以添加客户端停止逻辑
                
                print("[INFO] 所有服务已停止")
            except Exception as e:
                print(f"[ERROR] 停止服务异常: {e}")
        
        # 记录启动时间
        start_time = time.time()
        
        # 测试模式：检查环境后退出
        if args.test:
            print("\n" + "=" * 60)
            print("[TEST] 测试模式 - 环境检查")
            print("=" * 60)
            
            # 测试Flask导入
            try:
                from flask import Flask
                print("[OK] Flask: 可用")
            except ImportError as e:
                print(f"[ERROR] Flask: 导入失败 - {e}")
                exit(1)
            
            # 测试其他关键模块
            test_modules = [
                ('requests', 'requests'),
                ('scapy', 'scapy.all'),
                ('psutil', 'psutil'),
                ('threading', 'threading'),
                ('socket', 'socket'),
                ('json', 'json'),
                ('time', 'time'),
                ('os', 'os')
            ]
            
            for module_name, import_name in test_modules:
                try:
                    __import__(import_name)
                    print(f"[OK] {module_name}: 可用")
                except ImportError as e:
                    print(f"[ERROR] {module_name}: 导入失败 - {e}")
            
            # 测试端口绑定
            try:
                import socket
                test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                test_socket.bind((args.host, args.port))
                test_socket.close()
                print(f"[OK] 端口 {args.port}: 可用")
            except Exception as e:
                print(f"[ERROR] 端口 {args.port}: 绑定失败 - {e}")
            
            # 测试网卡获取
            try:
                test_interfaces = get_interfaces()
                print(f"[OK] 网卡获取: 成功，检测到 {len(test_interfaces)} 个网卡")
            except Exception as e:
                print(f"[ERROR] 网卡获取: 失败 - {e}")
            
            print("\n[DONE] 测试模式完成，Agent环境检查结束")
            print("=" * 60)
            exit(0)
        
        print(f"*** [INFO] 启动Packet Agent，端口: {args.port}，版本: 2025-11-27-10:45-MAIL-FIX ***")
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    except Exception as e:
        print("启动失败: {}".format(str(e)))
        import traceback
        traceback.print_exc()
        exit(1)

