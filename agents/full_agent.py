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
from agents.modules.port_scanner import port_scan
from agents.modules.packet_replay import start_replay, stop_replay, get_replay_status
from agents.services.listeners import start_tcp_listener, stop_tcp_listener, start_udp_listener, stop_udp_listener
# 导入完整监听功能（支持 FTP/HTTP/Mail）
from agents.full_agent_base import start_listener, stop_listener, listener_states, service_lock

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
    return jsonify({'status': 'healthy', 'agent_id': AGENT_ID})

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

@app.route('/api/port_scan', methods=['POST'])
def api_port_scan():
    """端口扫描 API"""
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

@app.route('/api/port_scan/stop', methods=['POST'])
def api_port_scan_stop():
    """停止扫描"""
    return jsonify({'success': True, 'message': '扫描已停止'})

@app.route('/api/port_scan/progress', methods=['GET'])
def api_scan_progress():
    """扫描进度"""
    return jsonify({'progress': 0, 'running': False})

# ========== 报文回放功能 ==========

@app.route('/api/packet_replay/start', methods=['POST'])
def api_replay_start():
    """启动报文回放"""
    data = request.get_json()
    pcap_file = data.get('pcap_file')

    if not pcap_file:
        return jsonify({'success': False, 'error': '缺少 PCAP 文件路径'}), 400

    success, message = start_replay([pcap_file], BIND_INTERFACE)
    if success:
        return jsonify({'success': True, 'message': message})
    else:
        return jsonify({'success': False, 'error': message}), 500

@app.route('/api/packet_replay/stop', methods=['POST'])
def api_replay_stop():
    """停止回放"""
    success, message = stop_replay()
    return jsonify({'success': success, 'message': message})

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
            from agents.full_agent_base import get_mail_users
            users = get_mail_users()
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
    from agents.protocols.modbus_client import ModbusClient
    # TODO: 实现连接逻辑
    data = request.get_json()
    return jsonify({'success': True, 'message': 'Modbus 客户端连接功能待实现'})

@app.route('/api/industrial_protocol/modbus_client/disconnect', methods=['POST'])
def modbus_client_disconnect():
    """断开 Modbus 客户端"""
    return jsonify({'success': True, 'message': '断开成功'})

@app.route('/api/industrial_protocol/modbus_client/status', methods=['GET'])
def modbus_client_status():
    """Modbus 客户端状态"""
    return jsonify({'success': True, 'connected': False})

@app.route('/api/industrial_protocol/modbus_client/read', methods=['POST'])
def modbus_client_read():
    """读取 Modbus 数据"""
    return jsonify({'success': True, 'data': []})

@app.route('/api/industrial_protocol/modbus_client/write', methods=['POST'])
def modbus_client_write():
    """写入 Modbus 数据"""
    return jsonify({'success': True})

# Modbus 服务端 API
@app.route('/api/industrial_protocol/modbus_server/start', methods=['POST'])
def modbus_server_start():
    """启动 Modbus 服务端"""
    from agents.protocols.modbus_server import ModbusServer
    # TODO: 实现启动逻辑
    return jsonify({'success': True, 'message': 'Modbus 服务端启动功能待实现'})

@app.route('/api/industrial_protocol/modbus_server/stop', methods=['POST'])
def modbus_server_stop():
    """停止 Modbus 服务端"""
    return jsonify({'success': True})

@app.route('/api/industrial_protocol/modbus_server/status', methods=['GET'])
def modbus_server_status():
    """Modbus 服务端状态"""
    return jsonify({'success': True, 'running': False})

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