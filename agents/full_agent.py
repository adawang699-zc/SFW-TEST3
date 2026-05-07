#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全功能 Agent - 合并 packet_agent 和 industrial_protocol_agent 的所有功能
运行在本地 Ubuntu 上，绑定指定网卡

环境变量配置（systemd 传入）:
- AGENT_ID: Agent 标识，如 agent_eth1
- BIND_IP: 绑定的网卡 IP
- BIND_INTERFACE: 发送报文使用的网卡名
- AGENT_PORT: 监听端口

功能包括：
- 报文发送（TCP/UDP/ICMP/ARP + 攻击测试：Ping of Death、Teardrop、Smurf）
- 服务监听（TCP/UDP/FTP/HTTP/Mail）
- 客户端服务（TCP/UDP/FTP/HTTP/Mail）
- 端口扫描（nmap + socket）
- 报文回放（PCAP 文件）
- 工控协议（Modbus/S7/ENIP/DNP3/BACnet/MMS/GOOSE/SV）
- DHCP 客户端
"""

import os
import sys
import logging
import argparse
import threading
import time
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('agent')

# ========== 从环境变量读取配置 ==========
AGENT_ID = os.environ.get('AGENT_ID', 'agent_eth0')
BIND_IP = os.environ.get('BIND_IP', '0.0.0.0')
BIND_INTERFACE = os.environ.get('BIND_INTERFACE', 'eth0')
AGENT_PORT = int(os.environ.get('AGENT_PORT', '8888'))

logger.info(f"Agent 配置: ID={AGENT_ID}, IP={BIND_IP}, Interface={BIND_INTERFACE}, Port={AGENT_PORT}")

# ========== Flask 应用初始化 ==========
try:
    from flask import Flask, request, jsonify, make_response
    from flask_cors import CORS
except ImportError:
    logger.error("请安装 flask 和 flask-cors: pip install flask flask-cors")
    sys.exit(1)

# 导入网卡获取函数
try:
    from agents.full_agent_base import get_interfaces
except ImportError:
    # 如果导入失败，定义一个简化版本
    def get_interfaces():
        import psutil
        import socket
        interfaces = []
        if_stats = psutil.net_if_stats()
        if_addrs = psutil.net_if_addrs()
        for ifname, stats in if_stats.items():
            if 'Loopback' in ifname:
                continue
            addrs = if_addrs.get(ifname, [])
            ip = None
            mac = None
            for addr in addrs:
                if addr.family == socket.AF_INET and addr.address not in ('127.0.0.1', '0.0.0.0'):
                    ip = addr.address
                elif addr.family == psutil.AF_LINK:
                    mac = addr.address
            if mac and ip:
                interfaces.append({
                    'name': ifname,
                    'ip': ip,
                    'mac': mac.replace('-', ':'),
                    'status': 'UP' if stats.isup else 'DOWN'
                })
        return interfaces

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ========== CORS 处理 ==========
@app.before_request
def handle_cors_preflight():
    """处理 OPTIONS 预检请求"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response

@app.after_request
def add_cors_headers(response):
    """添加 CORS 头"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

# ========== 全局状态 ==========
start_time = None
sending_thread = None
stop_sending = False
statistics = {
    'total_sent': 0,
    'start_time': None,
    'last_update': None,
    'rate': 0,
    'bandwidth': 0
}

# 导入功能模块
from agents.modules.packet_sender import send_tcp_packet, send_udp_packet
from agents.modules.port_scanner import (
    port_scan, get_scan_types, start_async_scan, stop_scan,
    get_scan_progress, get_scan_results, COMMON_PORTS
)
from agents.modules.packet_replay import (
    start_replay, stop_replay, get_replay_status,
    get_pcap_files, start_replay_tcpreplay, DEFAULT_PCAP_DIR,
    get_pcap_info
)
from agents.services.listeners import start_tcp_listener, stop_tcp_listener, start_udp_listener, stop_udp_listener
# 导入完整监听功能（支持 FTP/HTTP/Mail）
from agents.full_agent_base import (
    start_listener, stop_listener, listener_states, service_lock,
    # TCP 客户端
    start_tcp_client, connect_tcp_client, start_tcp_send, stop_tcp_send,
    stop_tcp_client, disconnect_tcp_connection,
    # UDP 客户端
    start_udp_client, start_udp_send, stop_udp_send, stop_udp_client,
    # FTP 客户端
    start_ftp_client, connect_ftp_client, disconnect_ftp_client,
    list_ftp_files, upload_ftp_file, download_ftp_file,
    # HTTP 客户端
    connect_http_client, disconnect_http_client, upload_http_file,
    list_http_files, download_http_file,
    # Mail 客户端
    send_mail_via_smtp, get_inbox_mails, test_mail_connection,
    # 日志
    add_service_log, service_logs,
    # 状态
    client_states
)
# 导入 DHCP 客户端模块
from agents.modules.dhcp_client_module import (
    api_dhcp_client_start, api_dhcp_client_status,
    set_log_callback, add_dhcp_log
)

# ========== 通用 API ==========

@app.route('/api/status', methods=['GET'])
def get_status():
    """Agent 状态查询"""
    return jsonify({
        'agent_id': AGENT_ID,
        'bind_ip': BIND_IP,
        'bind_interface': BIND_INTERFACE,
        'port': AGENT_PORT,
        'status': 'running',
        'uptime': int((datetime.now() - start_time).total_seconds()) if start_time else 0,
        'start_time': start_time.isoformat() if start_time else None
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({'success': True, 'status': 'healthy', 'agent_id': AGENT_ID})

@app.route('/api/interfaces', methods=['GET'])
def api_interfaces():
    """获取网卡列表"""
    try:
        interfaces = get_interfaces()
        return jsonify({
            'success': True,
            'interfaces': interfaces,
            'agent_id': AGENT_ID
        })
    except Exception as e:
        logger.exception(f"获取网卡列表失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/interface_info', methods=['GET'])
def interface_info():
    """返回网卡信息"""
    return jsonify({
        'agent_id': AGENT_ID,
        'interface': BIND_INTERFACE,
        'ip': BIND_IP,
        'port': AGENT_PORT
    })

@app.route('/api/shutdown', methods=['POST'])
def shutdown():
    """优雅关闭"""
    logger.info(f"收到关闭请求: {AGENT_ID}")
    # TODO: 停止所有正在运行的任务
    return jsonify({'status': 'shutdown_requested', 'agent_id': AGENT_ID})

# ========== 报文发送功能 ==========

@app.route('/api/send_packet', methods=['POST'])
def api_send_packet():
    """发送报文 API"""
    # 直接使用 full_agent_base 中的函数
    from agents.full_agent_base import send_packets_worker, statistics, stop_sending, stats_lock
    import threading
    import time as time_module

    global sending_thread

    try:
        data = request.json
        interface = data.get('interface', BIND_INTERFACE)
        packet_config = data.get('packet_config', {})
        send_config = data.get('send_config', {})

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
        logger.exception(f"发送报文失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/stop', methods=['POST'])
def api_stop():
    """停止发送报文"""
    from agents.full_agent_base import stop_sending, statistics, stats_lock
    stop_sending.set()

    # 清除统计数据
    with stats_lock:
        statistics['total_sent'] = 0
        statistics['rate'] = 0
        statistics['bandwidth'] = 0
        statistics['start_time'] = None
        statistics['last_update'] = None

    logger.info("停止发送报文，统计数据已清除")
    return jsonify({
        'success': True,
        'message': '已停止发送',
        'statistics': {
            'total_sent': 0,
            'rate': 0,
            'bandwidth': 0
        }
    })

@app.route('/api/statistics', methods=['GET'])
def api_statistics():
    """获取发送统计"""
    from agents.full_agent_base import statistics, stats_lock
    with stats_lock:
        return jsonify({
            'success': True,
            'statistics': statistics.copy()
        })

# ========== 端口扫描功能 ==========

@app.route('/api/start_scan', methods=['POST'])
def api_start_scan():
    """启动端口扫描 - 异步"""
    data = request.get_json()
    target_ip = data.get('target_ip')
    port_range = data.get('port_range', '1-1000')
    scan_type = data.get('scan_type', 'S')  # 默认 SYN 扫描

    if not target_ip:
        return jsonify({'success': False, 'error': '缺少目标 IP'}), 400

    result = start_async_scan(target_ip, port_range, scan_type)
    if result.get('status') == 'already_running':
        return jsonify({'success': False, 'error': result.get('error')}), 400

    return jsonify({
        'success': True,
        'status': result.get('status'),
        'target': result.get('target'),
        'scan_type': result.get('scan_type'),
        'agent_id': AGENT_ID
    })

@app.route('/api/stop_scan', methods=['POST'])
def api_stop_scan():
    """停止端口扫描"""
    result = stop_scan()
    return jsonify({
        'success': True,
        'status': result.get('status'),
        'agent_id': AGENT_ID
    })

@app.route('/api/scan_progress', methods=['GET'])
def api_scan_progress():
    """获取扫描进度"""
    result = get_scan_progress()
    return jsonify({
        'success': True,
        'running': result.get('running'),
        'progress': result.get('progress'),
        'target': result.get('target'),
        'results_count': result.get('results_count', 0),
        'error': result.get('error'),
        'agent_id': AGENT_ID
    })

@app.route('/api/scan_results', methods=['POST'])
def api_scan_results():
    """获取扫描结果"""
    result = get_scan_results()
    return jsonify({
        'success': True,
        'running': result.get('running'),
        'results': result.get('results', []),
        'target': result.get('target'),
        'total': result.get('total', 0),
        'error': result.get('error'),
        'agent_id': AGENT_ID
    })

@app.route('/api/scan_types', methods=['GET'])
def api_scan_types():
    """获取支持的扫描类型"""
    types = get_scan_types()
    types_list = []
    for code, info in types.items():
        types_list.append({
            'code': code,
            'name': info['name'],
            'desc': info['desc']
        })
    return jsonify({
        'success': True,
        'scan_types': types_list,
        'agent_id': AGENT_ID
    })

# 保留旧 API 以兼容
@app.route('/api/port_scan', methods=['POST'])
def api_port_scan():
    """端口扫描 API（同步，已废弃）"""
    data = request.get_json()
    target_ip = data.get('target_ip')
    port_range = data.get('port_range', '1-1000')

    if not target_ip:
        return jsonify({'success': False, 'error': '缺少目标 IP'}), 400

    success, result = port_scan(target_ip, port_range)
    if success:
        return jsonify({'success': True, 'result': result})
    else:
        return jsonify({'success': False, 'error': result.get('error', '扫描失败')}), 500

# ========== 报文回放功能 ==========

@app.route('/api/pcap_files/', methods=['POST'])
def api_pcap_files():
    """获取 PCAP 文件列表"""
    data = request.get_json() or {}
    directory = data.get('directory', DEFAULT_PCAP_DIR)
    search = data.get('search', '')

    result = get_pcap_files(directory, search)
    return jsonify(result)

@app.route('/api/packet_replay/start', methods=['POST'])
def api_replay_start():
    """启动报文回放"""
    data = request.get_json()

    # 支持单个文件或多个文件
    pcap_files = data.get('pcap_files', [])
    if not pcap_files:
        pcap_file = data.get('pcap_file')
        if pcap_file:
            pcap_files = [pcap_file]

    if not pcap_files:
        return jsonify({'success': False, 'error': '缺少 PCAP 文件'}), 400

    # tcpreplay 参数
    loop = data.get('loop', 1)
    multiplier = data.get('multiplier')
    rate_pps = data.get('rate_pps')
    rate_mbps = data.get('rate_mbps')
    topspeed = data.get('topspeed', False)

    # 获取总报文数
    total_packets = 0
    for f in pcap_files:
        try:
            info_result = get_pcap_info(f)
            if info_result.get('success'):
                total_packets += info_result['info'].get('packets', 0) * loop
        except Exception as e:
            logger.warning(f'获取 pcap 信息失败: {f} - {e}')

    success, message = start_replay_tcpreplay(
        pcap_files=pcap_files,
        interface=BIND_INTERFACE,
        loop=loop,
        rate=rate_pps,
        multiplier=multiplier,
        topspeed=topspeed
    )

    if success:
        return jsonify({
            'success': True,
            'message': message,
            'status': 'replaying',
            'total_packets': total_packets
        })
    else:
        return jsonify({'success': False, 'error': message}), 500

@app.route('/api/packet_replay/stop', methods=['POST'])
def api_replay_stop():
    """停止回放"""
    success, message = stop_replay()
    return jsonify({'success': success, 'message': message, 'status': 'stopped'})

@app.route('/api/packet_replay/status', methods=['GET'])
def api_replay_status():
    """回放状态"""
    status = get_replay_status()
    return jsonify(status)

# ========== 服务监听功能 ==========

@app.route('/api/services/listener', methods=['POST'])
def api_service_listener():
    """服务监听管理"""
    data = request.get_json()
    action = data.get('action')  # start/stop/disconnect_connection/create_user/delete_user/list_users

    # 同时支持 protocol 和 type 字段（前端使用 protocol）
    protocol = data.get('protocol') or data.get('type') or 'tcp'
    protocol = protocol.lower()

    host = data.get('host', BIND_IP)
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
        return jsonify({'success': success, 'message': result if isinstance(result, str) else result.get('message', '启动成功')})

    elif action == 'stop':
        success, result = stop_listener(protocol)
        return jsonify({'success': success, 'message': result if isinstance(result, str) else '已停止'})

    elif action == 'disconnect_connection':
        conn_id = data.get('connection_id')
        if protocol == 'tcp':
            # 断开指定 TCP 连接
            from agents.full_agent_base import disconnect_tcp_listener_connection
            success, message = disconnect_tcp_listener_connection(conn_id)
            return jsonify({'success': success, 'message': message})
        else:
            return jsonify({'success': False, 'error': '只有TCP支持断开单个连接'}), 400

    elif action == 'create_user':
        if protocol == 'mail':
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            if not username:
                return jsonify({'success': False, 'error': '用户名不能为空'}), 400
            if not password or len(password) < 4:
                return jsonify({'success': False, 'error': '密码至少需要4位'}), 400
            from agents.full_agent_base import create_mail_user
            success, message = create_mail_user(username, password)
            return jsonify({'success': success, 'message': message}), 200 if success else 400
        else:
            return jsonify({'success': False, 'error': '只有Mail协议支持创建用户'}), 400

    elif action == 'delete_user':
        if protocol == 'mail':
            username = data.get('username', '').strip()
            if not username:
                return jsonify({'success': False, 'error': '用户名不能为空'}), 400
            from agents.full_agent_base import delete_mail_user
            success, message = delete_mail_user(username)
            return jsonify({'success': success, 'message': message}), 200 if success else 400
        else:
            return jsonify({'success': False, 'error': '只有Mail协议支持删除用户'}), 400

    elif action == 'list_users':
        if protocol == 'mail':
            from agents.full_agent_base import list_mail_users
            users = list_mail_users()
            return jsonify({'success': True, 'mail_users': users})
        else:
            return jsonify({'success': False, 'error': '只有Mail协议支持列出用户'}), 400

    else:
        return jsonify({'success': False, 'error': '无效的 action'}), 400

@app.route('/api/services/status', methods=['GET'])
def api_services_status():
    """服务状态"""
    # 返回所有协议的监听和客户端状态
    listeners_summary = {}
    clients_summary = {}

    for protocol in ['tcp', 'udp', 'ftp', 'http', 'mail']:
        state = listener_states.get(protocol, {'running': False})
        is_running = state.get('running', False)
        if is_running:
            connections = list(state.get('connections', {}).values()) if isinstance(state.get('connections'), dict) else []
            listeners_summary[protocol] = {
                'running': True,
                'host': state.get('host', '0.0.0.0'),
                'port': state.get('port', 0),
                'connections': connections,
                'packets': state.get('packets', 0)
            }
            if protocol == 'mail':
                thread = state.get('thread')
                if thread:
                    listeners_summary[protocol]['smtp_port'] = getattr(thread, 'smtp_port', 25)
                    listeners_summary[protocol]['imap_port'] = getattr(thread, 'imap_port', 143)
                    listeners_summary[protocol]['pop3_port'] = getattr(thread, 'pop3_port', 110)
                    listeners_summary[protocol]['domain'] = getattr(thread, 'domain', 'autotest.com')
        else:
            listeners_summary[protocol] = {'running': False}

        # 客户端状态
        from agents.services.clients import client_states
        client_state = client_states.get(protocol, {'running': False})
        clients_summary[protocol] = {
            'running': client_state.get('running', False),
            'connected': client_state.get('connected', False)
        }

    return jsonify({
        'success': True,
        'listeners': listeners_summary,
        'clients': clients_summary,
        'agent_id': AGENT_ID
    })


# ========== 客户端服务 API ==========

@app.route('/api/services/client', methods=['POST'])
def api_services_client():
    """客户端服务管理"""
    try:
        data = request.json or {}
        protocol = (data.get('protocol') or 'tcp').lower()
        action = (data.get('action') or 'start').lower()
        config = data.get('config', data)

        add_service_log('API', f'收到客户端请求: protocol={protocol}, action={action}', 'info')

        # TCP 客户端
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

        # UDP 客户端
        elif protocol == 'udp':
            if action == 'start':
                success, result = start_udp_client(config)
            elif action == 'start_send':
                success, result = start_udp_send(config)
            elif action == 'stop_send':
                success, result = stop_udp_send()
            elif action == 'stop':
                success, result = stop_udp_client()
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400

        # FTP 客户端
        elif protocol == 'ftp':
            if action == 'start':
                success, result = start_ftp_client(config)
            elif action == 'connect':
                success, result = connect_ftp_client(config)
                if not success and isinstance(result, str):
                    result = {'error': result}
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
            elif action == 'download':
                filename = config.get('filename', '')
                if not filename:
                    return jsonify({'success': False, 'error': '缺少文件名参数'}), 400
                success, result = download_ftp_file(filename)
            elif action == 'stop':
                success, result = disconnect_ftp_client()
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400

        # HTTP 客户端
        elif protocol == 'http':
            if action == 'connect':
                success, result = connect_http_client(config)
            elif action == 'disconnect' or action == 'stop':
                success, result = disconnect_http_client()
            elif action == 'list':
                success, result = list_http_files()
            elif action == 'upload':
                filename = config.get('filename', '')
                content = config.get('content', '')
                local_file_path = config.get('local_file_path', '')
                success, result = upload_http_file(filename, content, local_file_path)
                if success:
                    result = {'message': '上传成功'}
                else:
                    result = {'error': result if isinstance(result, str) else '上传失败'}
            elif action == 'download':
                filename = config.get('filename', '')
                if not filename:
                    return jsonify({'success': False, 'error': '缺少文件名参数'}), 400
                success, result = download_http_file(filename)
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400

        # Mail 客户端
        elif protocol == 'mail':
            if action == 'test_connection':
                test_type = data.get('type', 'smtp')
                mail_config = {
                    'server': config.get('server', ''),
                    'port': int(config.get('port', 25)),
                    'ssl': config.get('ssl', False),
                    'email': config.get('email', ''),
                    'password': config.get('password', ''),
                    'no_auth': config.get('no_auth', False)
                }
                success, result = test_mail_connection(test_type, mail_config)
            elif action == 'send':
                smtp_config = {
                    'server': config.get('smtp_server', ''),
                    'port': int(config.get('smtp_port', 25)),
                    'ssl': config.get('smtp_ssl', False),
                    'email': config.get('from', ''),
                    'password': config.get('password', ''),
                    'no_auth': config.get('no_auth', False)
                }
                mail_data = {
                    'from': config.get('from', ''),
                    'to': config.get('to', ''),
                    'subject': config.get('subject', ''),
                    'content': config.get('content', ''),
                    'content_type': config.get('content_type', 'plain'),
                    'cc': config.get('cc', ''),
                    'attachments': config.get('attachments', [])
                }
                source_ip = BIND_IP if BIND_IP != '0.0.0.0' else ''
                success, result = send_mail_via_smtp(smtp_config, mail_data, source_ip)
            elif action == 'receive' or action == 'get_inbox':
                # 支持两种action名称，兼容原项目
                receive_config = data.get('receive_config', config)
                protocol_type = receive_config.get('protocol', 'imap').lower()  # imap 或 pop3
                server = receive_config.get('server', '') or receive_config.get('imap_server', '')
                port = int(receive_config.get('port', 143 if protocol_type == 'imap' else 110) or receive_config.get('imap_port', 143))
                email = receive_config.get('email', '') or receive_config.get('user', '')
                password = receive_config.get('password', '')

                add_service_log('邮件客户端', f'获取收件箱 ({protocol_type}): {email}@{server}:{port}', 'info')

                add_service_log('邮件客户端', f'获取收件箱 ({protocol_type}): {email}@{server}:{port}', 'info')

                final_config = {
                    'protocol': protocol_type,
                    'server': server,
                    'port': port,
                    'ssl': receive_config.get('ssl', False) or receive_config.get('imap_ssl', False),
                    'email': email,
                    'password': password
                }
                source_ip = BIND_IP if BIND_IP != '0.0.0.0' else ''

                success, result = get_inbox_mails(final_config, source_ip)

                add_service_log('邮件客户端', f'获取结果: {len(result) if isinstance(result, list) else 0}封邮件', 'info')

                if success and isinstance(result, list):
                    result = {'mails': result, 'count': len(result)}
            else:
                return jsonify({'success': False, 'error': '不支持的操作'}), 400

        else:
            return jsonify({'success': False, 'error': f'不支持的协议: {protocol}'}), 400

        # 返回结果
        if success:
            if isinstance(result, dict):
                return jsonify({'success': True, **result})
            else:
                return jsonify({'success': True, 'message': result})
        else:
            if isinstance(result, dict):
                return jsonify({'success': False, **result}), 400
            else:
                return jsonify({'success': False, 'error': result}), 400

    except Exception as e:
        logger.exception(f'客户端服务请求失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/services/logs', methods=['GET'])
def api_services_logs():
    """获取服务日志"""
    try:
        limit = int(request.args.get('limit', 100))
        with service_lock:
            logs = list(service_logs)[:limit]
        return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== 工控协议功能 ==========

# 注册所有工控协议 API（从 industrial_protocol_base 导入）
# 由于工控协议 API 很多（50+），这里只注册核心接口

@app.route('/api/protocols', methods=['GET'])
def api_get_protocols():
    """获取支持的工控协议列表"""
    protocols = [
        {'name': 'modbus-tcp', 'description': 'Modbus TCP 协议', 'default_port': 502},
        {'name': 's7', 'description': '西门子 S7 协议', 'default_port': 102},
        {'name': 'enip', 'description': 'Ethernet/IP 协议', 'default_port': 44818},
        {'name': 'dnp3', 'description': 'DNP3 协议', 'default_port': 20000},
        {'name': 'bacnet', 'description': 'BACnet 协议', 'default_port': 47808},
        {'name': 'mms', 'description': 'MMS/IEC61850 协议', 'default_port': 102},
        {'name': 'goose', 'description': 'IEC61850 GOOSE', 'default_port': 0},
        {'name': 'sv', 'description': 'IEC61850 SV', 'default_port': 0},
    ]
    return jsonify({
        'protocols': protocols,
        'agent_id': AGENT_ID,
        'interface': BIND_INTERFACE
    })

# Modbus 客户端 API
@app.route('/api/industrial_protocol/modbus_client/connect', methods=['POST'])
def modbus_client_connect():
    """连接 Modbus 客户端"""
    from agents.protocols.modbus_client import modbus_client, PYMODBUS_AVAILABLE
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus 未安装'})

    data = request.get_json() or {}
    ip = data.get('ip', '')
    port = int(data.get('port', 502))
    unit_id = int(data.get('unit_id', 1))
    client_id = data.get('client_id', 'default')
    timeout = int(data.get('timeout', 3))

    if not ip:
        return jsonify({'success': False, 'error': 'IP 地址不能为空'})

    success, message = modbus_client.connect(ip, port, client_id, unit_id, timeout)
    return jsonify({'success': success, 'message': message})

@app.route('/api/industrial_protocol/modbus_client/disconnect', methods=['POST'])
def modbus_client_disconnect():
    """断开 Modbus 客户端"""
    from agents.protocols.modbus_client import modbus_client
    data = request.get_json() or {}
    client_id = data.get('client_id', 'default')

    success, message = modbus_client.disconnect(client_id)
    return jsonify({'success': success, 'message': message})

@app.route('/api/industrial_protocol/modbus_client/status', methods=['GET', 'POST'])
def modbus_client_status():
    """Modbus 客户端状态"""
    from agents.protocols.modbus_client import modbus_client
    data = request.get_json() or {}
    client_id = data.get('client_id', 'default')

    status = modbus_client.status(client_id)
    status['success'] = True
    return jsonify(status)

@app.route('/api/industrial_protocol/modbus_client/read', methods=['POST'])
def modbus_client_read():
    """读取 Modbus 数据"""
    from agents.protocols.modbus_client import modbus_client, PYMODBUS_AVAILABLE
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus 未安装'})

    data = request.get_json() or {}
    client_id = data.get('client_id', 'default')
    function_code = int(data.get('function_code', 3))
    address = int(data.get('address', 0))
    count = int(data.get('count', 1))

    success, values = modbus_client.read(client_id, function_code, address, count)
    return jsonify({'success': success, 'values': values})

@app.route('/api/industrial_protocol/modbus_client/write', methods=['POST'])
def modbus_client_write():
    """写入 Modbus 数据"""
    from agents.protocols.modbus_client import modbus_client, PYMODBUS_AVAILABLE
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus 未安装'})

    data = request.get_json() or {}
    client_id = data.get('client_id', 'default')
    function_code = int(data.get('function_code', 6))
    address = int(data.get('address', 0))
    values = data.get('values', [])

    success, message = modbus_client.write(client_id, function_code, address, values)
    return jsonify({'success': success, 'message': message})

# Modbus 服务端 API
@app.route('/api/industrial_protocol/modbus_server/start', methods=['POST'])
def modbus_server_start():
    """启动 Modbus 服务端"""
    from agents.protocols.modbus_server import modbus_server, PYMODBUS_AVAILABLE, add_modbus_log

    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus 未安装'})

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        port = data.get('port', 502)
        unit_id = data.get('unit_id', 1)
        interface = data.get('interface', '0.0.0.0')

        add_modbus_log('INFO', '收到启动请求', {
            'server_id': server_id,
            'port': port,
            'unit_id': unit_id
        })

        success, message = modbus_server.start(server_id, port=port, interface=interface, unit_id=unit_id)
        return jsonify({'success': success, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/stop', methods=['POST'])
def modbus_server_stop():
    """停止 Modbus 服务端"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')

        success, message = modbus_server.stop(server_id)
        return jsonify({'success': success, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/status', methods=['GET', 'POST'])
def modbus_server_status():
    """Modbus 服务端状态"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')

        status = modbus_server.status(server_id)
        return jsonify({'success': True, **status})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/get_data', methods=['POST'])
def modbus_server_get_data():
    """获取 Modbus 服务端数据"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        function_code = data.get('function_code', 3)
        address = data.get('address', 0)
        count = data.get('count', 10)

        success, values = modbus_server.get_data(server_id, function_code, address, count)
        return jsonify({'success': success, 'values': values})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/set_data', methods=['POST'])
def modbus_server_set_data():
    """设置 Modbus 服务端数据"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        function_code = data.get('function_code', 3)
        address = data.get('address', 0)
        values = data.get('values', [])

        success, message = modbus_server.set_data(server_id, function_code, address, values)
        return jsonify({'success': success, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/bulk_set_data', methods=['POST'])
def modbus_server_bulk_set_data():
    """批量设置 Modbus 服务端数据"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        function_code = data.get('function_code', 3)
        address = data.get('address', 0)
        values = data.get('values', [])

        success, message = modbus_server.set_data(server_id, function_code, address, values)
        return jsonify({'success': success, 'message': message})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/logs', methods=['GET', 'POST'])
def modbus_server_logs():
    """获取 Modbus 服务端操作日志"""
    from agents.protocols.modbus_server import modbus_server

    try:
        data = request.json or {}
        limit = data.get('limit', 100)

        logs = modbus_server.get_logs(limit)
        return jsonify({'success': True, 'logs': logs})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/industrial_protocol/modbus_server/clear_logs', methods=['POST'])
def modbus_server_clear_logs():
    """清空 Modbus 服务端日志"""
    from agents.protocols.modbus_server import modbus_server

    try:
        modbus_server.clear_logs()
        return jsonify({'success': True, 'message': '日志已清空'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# GOOSE/SV API
@app.route('/api/industrial_protocol/goose-sv/interfaces', methods=['GET'])
def goose_sv_interfaces():
    """获取 GOOSE/SV 可用网卡"""
    import psutil
    import socket as sock

    interfaces = []
    if_stats = psutil.net_if_stats()
    if_addrs = psutil.net_if_addrs()

    for ifname, stats in if_stats.items():
        if 'Loopback' in ifname:
            continue

        addrs = if_addrs.get(ifname, [])
        ip = None
        mac = None

        for addr in addrs:
            if addr.family == sock.AF_INET and addr.address not in ('127.0.0.1', '0.0.0.0'):
                ip = addr.address
            elif addr.family == psutil.AF_LINK:
                mac = addr.address

        if ip and mac:
            interfaces.append({
                'name': ifname,
                'ip': ip,
                'mac': mac,
                'status': 'UP' if stats.isup else 'DOWN'
            })

    return jsonify({'success': True, 'interfaces': interfaces})

# ========== DHCP 客户端功能 ==========

# 设置 DHCP 模块的日志回调
set_log_callback(add_service_log)

@app.route('/api/dhcp_client/start', methods=['POST'])
def api_start_dhcp_client():
    """启动 DHCP 客户端"""
    try:
        data = request.get_json()
        result = api_dhcp_client_start(data, BIND_INTERFACE)
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.exception(f"DHCP 客户端启动失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dhcp_client/status', methods=['GET'])
def api_get_dhcp_client_status():
    """获取 DHCP 客户端状态"""
    try:
        session_id = request.args.get('session_id', '')
        result = api_dhcp_client_status(session_id)
        if result['success']:
            return jsonify(result)
        else:
            return jsonify(result), 404 if '会话不存在' in result.get('error', '') else 400
    except Exception as e:
        logger.exception(f"获取 DHCP 状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== S7 Server API ==========
# 导入 S7 Server 相关变量和辅助函数
try:
    from agents.industrial_protocol_base import (
        SNAP7_AVAILABLE, Snap7Server, snap7, snap7_srv_area,
        s7_servers, s7_server_lock, s7_data_storage, S7_DB_MAX_SIZE,
        s7_clients, s7_client_lock, Snap7Client, snap7_area,
        add_log, save_s7_db_to_database, load_s7_db_from_database,
        sync_s7_data_to_server, register_s7_areas
    )
    logger.info(f"S7 Server 模块导入成功: SNAP7_AVAILABLE={SNAP7_AVAILABLE}")
except ImportError as e:
    logger.warning(f"S7 Server 模块导入失败: {e}")
    SNAP7_AVAILABLE = False
    Snap7Server = None
    s7_servers = {}
    s7_server_lock = threading.Lock()
    s7_data_storage = {}
    S7_DB_MAX_SIZE = 32768
    s7_clients = {}
    s7_client_lock = threading.Lock()
    Snap7Client = None
    snap7_area = None
    snap7_srv_area = None

    def add_log(level, msg):
        logger.log(logging.getLevelName(level), msg)

    def save_s7_db_to_database(*args, **kwargs):
        pass

    def load_s7_db_from_database(*args, **kwargs):
        return None

    def sync_s7_data_to_server(*args, **kwargs):
        pass

    def register_s7_areas(*args, **kwargs):
        pass

@app.route('/api/industrial_protocol/s7_server/start', methods=['POST'])
def s7_server_start():
    """启动S7服务端"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装或导入失败'}), 500

    try:
        data = request.get_json(silent=True) or {}
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = int(data.get('port', 102))

        # 第一步：在锁内停止旧服务器和初始化数据存储
        with s7_server_lock:
            # 停止旧服务端
            if server_id in s7_servers:
                old_server_info = s7_servers[server_id]
                old_server_info['running'] = False
                server = old_server_info.get('server')
                if server:
                    try:
                        server.stop()
                        server.destroy()
                        add_log('INFO', '停止旧S7服务器')
                    except Exception as e:
                        add_log('WARNING', f'停止旧S7服务器时出错: {e}')
                if 'thread' in old_server_info:
                    old_thread = old_server_info['thread']
                    if old_thread.is_alive():
                        old_thread.join(timeout=2)
                del s7_servers[server_id]

            # 初始化数据存储
            if server_id not in s7_data_storage:
                s7_data_storage[server_id] = {
                    'db': {},
                    'm': bytearray(S7_DB_MAX_SIZE),
                    'i': bytearray(S7_DB_MAX_SIZE),
                    'q': bytearray(S7_DB_MAX_SIZE),
                }

            storage = s7_data_storage[server_id]
            for db_num in [1, 2, 3]:
                if db_num not in storage['db']:
                    storage['db'][db_num] = bytearray([db_num] * S7_DB_MAX_SIZE)

        # 第二步：在锁外创建并启动服务器（避免阻塞太久）
        server = Snap7Server()

        # 设置保护级别
        try:
            if hasattr(server, 'set_protection_level'):
                server.set_protection_level(0)
        except Exception as e:
            add_log('WARNING', f'设置保护级别失败: {e}')

        # 启动服务器
        try:
            if hasattr(server, 'set_socket_params'):
                server.set_socket_params(port=port)
                server.start()
            elif hasattr(server, 'start'):
                try:
                    server.start(tcp_port=port)
                except (AttributeError, TypeError):
                    server.start()
            else:
                raise AttributeError('无法找到S7服务器启动方法')
        except Exception as e:
            raise Exception(f'S7服务器启动失败: {e}')

        # 第三步：在锁内存储 server_info（register_s7_areas 需要这个）
        server_running = {'value': True}
        def server_loop():
            while server_running['value']:
                time.sleep(0.1)
        server_thread = threading.Thread(target=server_loop, daemon=True)
        server_thread.start()

        with s7_server_lock:
            s7_servers[server_id] = {
                'server': server,
                'thread': server_thread,
                'running': server_running,
                'host': host,
                'port': port,
                'start_time': datetime.now().isoformat(),
            }

        # 第四步：等待服务器稳定
        time.sleep(0.5)

        # 第五步：在锁外调用 register_s7_areas 和 sync（这些函数内部有自己的锁）
        try:
            register_s7_areas(server_id, db_list=[1, 2, 3])
            sync_s7_data_to_server(server_id)
            add_log('INFO', f'S7 DB区域注册成功: DB1, DB2, DB3')
        except Exception as e:
            add_log('WARNING', f'S7区域注册失败: {e}（数据可能无法正常读写）')

        add_log('INFO', f'S7服务端启动成功: {host}:{port} (server_id={server_id})')
        return jsonify({
            'success': True,
            'message': 'S7服务端启动成功',
            'host': host,
            'port': port
        })

    except Exception as e:
        add_log('ERROR', f'S7服务端启动失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/industrial_protocol/s7_server/stop', methods=['POST'])
def s7_server_stop():
    """停止S7服务端"""
    try:
        data = request.get_json(silent=True) or {}
        server_id = data.get('server_id', 'default')

        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404

            server_info = s7_servers[server_id]
            server = server_info.get('server')
            server_running = server_info.get('running')

            if server_running and isinstance(server_running, dict):
                server_running['value'] = False

            if server:
                try:
                    server.stop()
                    server.destroy()
                    add_log('INFO', f'S7服务端已停止: server_id={server_id}')
                except Exception as e:
                    add_log('WARNING', f'停止S7服务器时出错: {e}')

            del s7_servers[server_id]
            return jsonify({'success': True, 'message': 'S7服务端已停止'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/industrial_protocol/s7_server/status', methods=['GET', 'POST'])
def s7_server_status():
    """获取S7服务端状态"""
    try:
        # 支持 GET (args) 和 POST (json)
        if request.method == 'POST':
            data = request.json or {}
            server_id = data.get('server_id', 'default')
        else:
            server_id = request.args.get('server_id', 'default')

        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': True, 'running': False})

            server_info = s7_servers[server_id]
            running = server_info.get('running', {})
            if isinstance(running, dict):
                is_running = running.get('value', False)
            else:
                is_running = bool(running)

            return jsonify({
                'success': True,
                'running': is_running,
                'host': server_info.get('host'),
                'port': server_info.get('port'),
                'start_time': server_info.get('start_time')
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/industrial_protocol/s7_server/get_data', methods=['POST'])
def s7_server_get_data():
    """读取S7服务端数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装'}), 500

    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        area = data.get('area', 'DB')
        db_number = int(data.get('db_number', 1))
        start = int(data.get('start', 0))
        size = int(data.get('size', 1))

        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404

            if server_id not in s7_data_storage:
                return jsonify({'success': False, 'error': '数据存储不存在'}), 404

            storage = s7_data_storage[server_id]

            if area == 'DB':
                if db_number not in storage['db']:
                    storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                db_data = storage['db'][db_number]
                if start + size <= len(db_data):
                    data_bytes = db_data[start:start+size]
                else:
                    data_bytes = bytearray(size)
                values = [int(b) for b in data_bytes]
                add_log('INFO', f'[DEBUG-get_data] area={area}, db={db_number}, start={start}, size={size}, 返回值={values[:10]}...')
                return jsonify({
                    'success': True,
                    'values': values,
                    'area': area,
                    'db_number': db_number,
                    'start': start,
                    'size': size
                })
            elif area == 'M':
                m_data = storage['m']
                if start + size <= len(m_data):
                    data_bytes = m_data[start:start+size]
                else:
                    data_bytes = bytearray(size)
                values = [int(b) for b in data_bytes]
                return jsonify({'success': True, 'values': values})
            elif area == 'I':
                i_data = storage['i']
                if start + size <= len(i_data):
                    data_bytes = i_data[start:start+size]
                else:
                    data_bytes = bytearray(size)
                values = [int(b) for b in data_bytes]
                return jsonify({'success': True, 'values': values})
            elif area == 'Q':
                q_data = storage['q']
                if start + size <= len(q_data):
                    data_bytes = q_data[start:start+size]
                else:
                    data_bytes = bytearray(size)
                values = [int(b) for b in data_bytes]
                return jsonify({'success': True, 'values': values})
            else:
                return jsonify({'success': False, 'error': f'不支持的区域类型: {area}'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/industrial_protocol/s7_server/set_data', methods=['POST'])
def s7_server_set_data():
    """设置S7服务端数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装'}), 500

    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        area = data.get('area', 'DB')
        db_number = int(data.get('db_number', 1))
        start = int(data.get('start', 0))
        values = data.get('values', [])

        if not values:
            return jsonify({'success': False, 'error': 'values 不能为空'})

        data_bytes = bytearray(values)

        # 第一步：在锁内写入数据到存储
        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404

            if server_id not in s7_data_storage:
                return jsonify({'success': False, 'error': '数据存储不存在'}), 404

            storage = s7_data_storage[server_id]

            if area == 'DB':
                if db_number not in storage['db']:
                    storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                db_data = storage['db'][db_number]
                if start + len(data_bytes) <= len(db_data):
                    db_data[start:start+len(data_bytes)] = data_bytes
                    # 不在锁内调用 sync（避免死锁）
                    data_updated = True
                else:
                    return jsonify({'success': False, 'error': '地址超出范围'})
            elif area == 'M':
                storage['m'][start:start+len(data_bytes)] = data_bytes
                data_updated = True
            elif area == 'I':
                storage['i'][start:start+len(data_bytes)] = data_bytes
                data_updated = True
            elif area == 'Q':
                storage['q'][start:start+len(data_bytes)] = data_bytes
                data_updated = True
            else:
                return jsonify({'success': False, 'error': f'不支持的区域类型: {area}'})

        # 第二步：在锁外同步数据到服务器（避免死锁）
        if area == 'DB' and data_updated:
            add_log('INFO', f'[DEBUG-set_data] area={area}, db={db_number}, start={start}, 写入值={list(data_bytes[:10])}...')
            try:
                sync_s7_data_to_server(server_id, db_number)
                add_log('INFO', f'[DEBUG-set_data] sync_s7_data_to_server 完成')
            except Exception as e:
                add_log('WARNING', f'同步S7数据失败: {e}')

        return jsonify({'success': True, 'message': '数据已设置'})

    except Exception as e:
        add_log('ERROR', f's7_server_set_data 错误: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500

# ========== S7 Client API ==========
@app.route('/api/industrial_protocol/s7_client/connect', methods=['POST'])
def s7_client_connect():
    """连接S7服务器"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装或导入失败'}), 500

    if Snap7Client is None:
        return jsonify({'success': False, 'error': 'snap7.Client类不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        ip = data.get('ip') or data.get('server_ip')
        port = data.get('port', 102)
        rack = data.get('rack', 0)
        slot = data.get('slot', 1)

        if not ip:
            return jsonify({'success': False, 'error': '缺少服务器IP地址'}), 400

        add_log('INFO', f'S7客户端连接请求: {ip}:{port}, Rack={rack}, Slot={slot}')

        with s7_client_lock:
            # 如果已存在连接，先断开
            if client_id in s7_clients:
                old_client = s7_clients[client_id].get('client')
                if old_client:
                    try:
                        old_client.disconnect()
                    except Exception:
                        pass
                del s7_clients[client_id]

            # 创建新的客户端连接
            client = Snap7Client()
            client.connect(ip, rack, slot, port)

            # 存储连接信息
            s7_clients[client_id] = {
                'client': client,
                'server_ip': ip,
                'port': port,
                'rack': rack,
                'slot': slot,
                'connected': True
            }

            add_log('INFO', f'S7客户端连接成功: {ip}:{port}')
            return jsonify({
                'success': True,
                'message': f'已连接到 {ip}:{port}',
                'client_id': client_id
            })

    except Exception as e:
        add_log('ERROR', f'S7客户端连接失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_client/disconnect', methods=['POST'])
def s7_client_disconnect():
    """断开S7客户端连接"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': True, 'message': '客户端未连接'})

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if client:
                try:
                    client.disconnect()
                except Exception as e:
                    add_log('WARNING', f'断开S7客户端时出错: {e}')

            del s7_clients[client_id]
            add_log('INFO', f'S7客户端已断开: {client_id}')
            return jsonify({'success': True, 'message': '已断开连接'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_client/status', methods=['GET', 'POST'])
def s7_client_status():
    """获取S7客户端状态"""
    try:
        if request.method == 'POST':
            data = request.json or {}
            client_id = data.get('client_id', 'default')
        else:
            client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': True, 'connected': False})

            client_info = s7_clients[client_id]
            return jsonify({
                'success': True,
                'connected': client_info.get('connected', False),
                'server_ip': client_info.get('server_ip'),
                'port': client_info.get('port'),
                'rack': client_info.get('rack'),
                'slot': client_info.get('slot')
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_client/read', methods=['POST'])
def s7_client_read():
    """读取S7服务器数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        area = data.get('area', 'DB')
        db_number = data.get('db_number', 1)
        start = data.get('start', 0)
        size = data.get('size', 1)

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client = s7_clients[client_id].get('client')
            if not client:
                return jsonify({'success': False, 'error': '客户端连接丢失'}), 500

            # 读取数据
            if area == 'DB':
                result = client.read_area(snap7_area.DB, db_number, start, size)
            elif area == 'I' or area == 'PE':
                result = client.read_area(snap7_area.PE, 0, start, size)
            elif area == 'Q' or area == 'PA':
                result = client.read_area(snap7_area.PA, 0, start, size)
            elif area == 'M' or area == 'MK':
                result = client.read_area(snap7_area.MK, 0, start, size)
            else:
                return jsonify({'success': False, 'error': f'不支持的区域类型: {area}'}), 400

            # 转换为整数列表
            values = list(result) if result else []
            add_log('INFO', f'[DEBUG-read] area={area}, db={db_number}, start={start}, size={size}, 返回值={values[:10]}...')

            return jsonify({
                'success': True,
                'values': values,
                'area': area,
                'db_number': db_number,
                'start': start,
                'size': size
            })

    except Exception as e:
        add_log('ERROR', f'S7读取失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_client/write', methods=['POST'])
def s7_client_write():
    """写入S7服务器数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        area = data.get('area', 'DB')
        db_number = data.get('db_number', 1)
        start = data.get('start', 0)
        values = data.get('values', []) or data.get('data', [])

        if not values:
            return jsonify({'success': False, 'error': '缺少写入数据'}), 400

        # 转换数据
        data_bytes = bytearray(values)

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client = s7_clients[client_id].get('client')
            if not client:
                return jsonify({'success': False, 'error': '客户端连接丢失'}), 500

            # 写入数据
            if area == 'DB':
                client.write_area(snap7_area.DB, db_number, start, data_bytes)
            elif area == 'I' or area == 'PE':
                client.write_area(snap7_area.PE, 0, start, data_bytes)
            elif area == 'Q' or area == 'PA':
                client.write_area(snap7_area.PA, 0, start, data_bytes)
            elif area == 'M' or area == 'MK':
                client.write_area(snap7_area.MK, 0, start, data_bytes)
            else:
                return jsonify({'success': False, 'error': f'不支持的区域类型: {area}'}), 400

            add_log('INFO', f'S7写入成功: area={area}, db={db_number}, start={start}, size={len(values)}')
            return jsonify({
                'success': True,
                'message': '写入成功',
                'area': area,
                'db_number': db_number,
                'start': start,
                'size': len(values)
            })

    except Exception as e:
        add_log('ERROR', f'S7写入失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_client/upload_block', methods=['POST'])
def s7_client_upload_block():
    """上传块到S7 PLC (PC → PLC)"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        block_type = data.get('block_type', 'db')
        block_number = data.get('block_number', 1)
        block_data_b64 = data.get('block_data', '')
        block_data_array = data.get('data', [])

        if block_data_b64:
            import base64
            block_bytes = base64.b64decode(block_data_b64)
            block_bytearray = bytearray(block_bytes)
        elif block_data_array:
            block_bytearray = bytearray(block_data_array)
        else:
            return jsonify({'success': False, 'error': '缺少块数据'}), 400

        if len(block_bytearray) < 32:
            return jsonify({'success': False, 'error': f'块数据太小({len(block_bytearray)}字节)'}), 400

        try:
            from snap7.type import Block
            block_type_map = {
                'db': Block.DB, 'ob': Block.OB, 'fc': Block.FC,
                'fb': Block.FB, 'sdb': Block.SDB, 'sfc': Block.SFC, 'sfb': Block.SFB
            }
        except ImportError:
            block_type_map = {
                'db': 0x41, 'ob': 0x38, 'fc': 0x43, 'fb': 0x45,
                'sdb': 0x42, 'sfc': 0x44, 'sfb': 0x46
            }

        block_type_enum = block_type_map.get(block_type.lower(), block_type_map['db'])

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            client.download(block_bytearray, block_number)

            add_log('INFO', f'S7 上传块: client_id={client_id}, type={block_type}, number={block_number}, size={len(block_bytearray)}')
            return jsonify({'success': True, 'message': f'块{block_number}上传成功', 'size': len(block_bytearray)})

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 上传块失败: {error_msg}')
        if 'protection' in error_msg.lower() or 'not authorized' in error_msg.lower():
            return jsonify({'success': False, 'error': 'S7模拟器不支持块上传功能，需连接真实PLC。'}), 500
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/download_block', methods=['POST'])
def s7_client_download_block():
    """从S7 PLC下载块 (PLC → PC)"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        block_number = data.get('block_number', 1)

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            block_data = client.upload(block_number)

            import base64
            block_base64 = base64.b64encode(bytes(block_data)).decode('utf-8')

            add_log('INFO', f'S7 下载块: client_id={client_id}, number={block_number}, size={len(block_data)}')
            return jsonify({'success': True, 'message': f'块{block_number}下载成功', 'data': block_base64, 'size': len(block_data)})

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 下载块失败: {error_msg}')
        if 'protection' in error_msg.lower() or 'not authorized' in error_msg.lower():
            return jsonify({'success': False, 'error': 'S7模拟器不支持块下载功能，需连接真实PLC。'}), 500
        return jsonify({'success': False, 'error': error_msg}), 500


# ========== S7 Client PLC 操作 ==========

@app.route('/api/industrial_protocol/s7_client/get_cpu_info', methods=['GET', 'POST'])
def s7_client_get_cpu_info():
    """获取S7 CPU信息"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        # 支持 GET 和 POST 两种方式获取参数
        if request.method == 'POST':
            data = request.json or {}
            client_id = data.get('client_id', 'default')
        else:
            client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            cpu_info = client.get_cpu_info()

            # 转换为可序列化的字典（bytes需要decode）
            def safe_decode(val):
                """安全解码bytes或字符串"""
                if isinstance(val, bytes):
                    try:
                        return val.decode('utf-8').rstrip('\x00')
                    except:
                        return val.hex()
                return str(val) if val else ''

            result = {
                'module_type': safe_decode(getattr(cpu_info, 'ModuleType', '')),
                'serial_number': safe_decode(getattr(cpu_info, 'SerialNumber', '')),
                'as_name': safe_decode(getattr(cpu_info, 'ASName', '')),
                'module_name': safe_decode(getattr(cpu_info, 'ModuleName', '')),
                'copyright': safe_decode(getattr(cpu_info, 'Copyright', '')),
            }

            add_log('INFO', f'S7 CPU信息: client_id={client_id}, module={result["module_name"]}')

            return jsonify({
                'success': True,
                **result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'获取S7 CPU信息失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/get_cpu_state', methods=['GET', 'POST'])
def s7_client_get_cpu_state():
    """获取S7 CPU运行状态"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        # 支持 GET 和 POST 两种方式获取参数
        if request.method == 'POST':
            data = request.json or {}
            client_id = data.get('client_id', 'default')
        else:
            client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            state = client.get_cpu_state()

            # 处理bytes类型的返回值
            if isinstance(state, bytes):
                state = state.decode('utf-8').rstrip('\x00')

            # 标准化状态字符串
            state_str = str(state).upper() if state else 'UNKNOWN'

            # 判断是否运行中
            is_running = 'RUN' in state_str and 'STOP' not in state_str

            add_log('DEBUG', f'S7 CPU状态: client_id={client_id}, state={state_str}, running={is_running}')

            return jsonify({
                'success': True,
                'state': state_str,
                'running': is_running
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'获取S7 CPU状态失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/plc_cold_start', methods=['POST'])
def s7_client_plc_cold_start():
    """S7 PLC冷启动"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_cold_start()
            add_log('INFO', f'S7 PLC冷启动: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC冷启动成功',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC冷启动失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/plc_hot_start', methods=['POST'])
def s7_client_plc_hot_start():
    """S7 PLC热启动"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_hot_start()
            add_log('INFO', f'S7 PLC热启动: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC热启动成功',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC热启动失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/plc_stop', methods=['POST'])
def s7_client_plc_stop():
    """S7 PLC停止"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_stop()
            add_log('INFO', f'S7 PLC停止: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC停止成功',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC停止失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/list_blocks', methods=['GET', 'POST'])
def s7_client_list_blocks():
    """列出S7块"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        if request.method == 'POST':
            data = request.json or {}
            client_id = data.get('client_id', 'default')
        else:
            client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            # 列出块信息（简化版）
            blocks = []
            for block_type in ['DB', 'OB', 'FC', 'FB', 'SDB']:
                try:
                    # 尝试读取块列表
                    blocks.append({'type': block_type, 'info': '可用'})
                except:
                    pass

            add_log('INFO', f'S7列出块: client_id={client_id}')

            return jsonify({
                'success': True,
                'blocks': blocks
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7列出块失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


# ========== 主入口 ==========

# Gunicorn 入口：在模块导入时初始化 start_time
start_time = datetime.now()
logger.info(f"全功能 Agent 初始化: ID={AGENT_ID}, Interface={BIND_INTERFACE}, IP={BIND_IP}, Port={AGENT_PORT}")

# Gunicorn 会直接导入此模块并使用 app 对象
# 命令: gunicorn -w 1 -b {BIND_IP}:{AGENT_PORT} --preload agents.full_agent:app


def main():
    """Agent 主入口（直接运行 Flask，用于开发/测试）"""
    global start_time
    start_time = datetime.now()

    logger.info(f"全功能 Agent 启动: {AGENT_ID}")
    logger.info(f"绑定网卡: {BIND_INTERFACE}, IP: {BIND_IP}")
    logger.info(f"监听地址: http://{BIND_IP}:{AGENT_PORT}")

    # 启动 Flask 服务
    app.run(
        host=BIND_IP,
        port=AGENT_PORT,
        threaded=True,
        use_reloader=False
    )


if __name__ == '__main__':
    # 支持命令行参数（用于测试）
    parser = argparse.ArgumentParser(description='全功能 Agent')
    parser.add_argument('--port', type=int, default=AGENT_PORT, help='监听端口')
    parser.add_argument('--interface', type=str, default=BIND_INTERFACE, help='绑定网卡')
    parser.add_argument('--ip', type=str, default=BIND_IP, help='绑定 IP')
    args = parser.parse_args()

    # 更新配置
    AGENT_PORT = args.port
    BIND_INTERFACE = args.interface
    BIND_IP = args.ip

    main()