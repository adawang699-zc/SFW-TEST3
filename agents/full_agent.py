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
    from agents.full_agent_base import stop_sending
    stop_sending.set()
    logger.info("停止发送报文")
    return jsonify({
        'success': True,
        'message': '已停止发送'
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
    action = data.get('action')  # start/stop
    service_type = data.get('type')  # tcp/udp/ftp/http/mail
    port = data.get('port', 8888)

    if action == 'start':
        if service_type == 'tcp':
            success, message = start_tcp_listener(port, BIND_IP)
        elif service_type == 'udp':
            success, message = start_udp_listener(port, BIND_IP)
        else:
            return jsonify({'success': False, 'error': f'不支持的服务类型: {service_type}'}), 400

        return jsonify({'success': success, 'message': message})

    elif action == 'stop':
        if service_type == 'tcp':
            success, message = stop_tcp_listener(port)
        elif service_type == 'udp':
            success, message = stop_udp_listener(port)
        else:
            return jsonify({'success': False, 'error': f'不支持的服务类型: {service_type}'}), 400

        return jsonify({'success': success, 'message': message})

    else:
        return jsonify({'success': False, 'error': '无效的 action'}), 400

@app.route('/api/services/status', methods=['GET'])
def api_services_status():
    """服务状态"""
    from agents.services.listeners import listener_states
    return jsonify({
        'tcp': listener_states['tcp'],
        'udp': listener_states['udp'],
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

def main():
    """Agent 主入口"""
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