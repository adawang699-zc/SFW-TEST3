#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端口扫描模块
支持 nmap 多种扫描类型，实时进度回调
"""

import subprocess
import logging
import re
import socket
import threading
import time
from typing import List, Dict, Tuple, Optional, Callable

logger = logging.getLogger(__name__)

# 常见端口和服务映射
COMMON_PORTS = {
    20: 'FTP-DATA', 21: 'FTP', 22: 'SSH', 23: 'Telnet', 25: 'SMTP',
    53: 'DNS', 67: 'DHCP', 68: 'DHCP-Client', 69: 'TFTP', 80: 'HTTP',
    110: 'POP3', 111: 'RPC', 123: 'NTP', 135: 'RPC', 139: 'NetBIOS',
    143: 'IMAP', 161: 'SNMP', 162: 'SNMP-Trap', 389: 'LDAP', 443: 'HTTPS',
    445: 'SMB', 465: 'SMTPS', 514: 'Syslog', 587: 'SMTP-Auth',
    636: 'LDAPS', 993: 'IMAPS', 995: 'POP3S', 1080: 'SOCKS',
    1433: 'MS-SQL', 1434: 'MS-SQL-Monitor', 3306: 'MySQL',
    3389: 'RDP', 5432: 'PostgreSQL', 5631: 'pcAnywhere',
    5900: 'VNC', 5901: 'VNC-1', 6379: 'Redis', 8080: 'HTTP-Proxy',
    8443: 'HTTPS-Alt', 9000: 'PHP-FPM', 9200: 'Elasticsearch',
    27017: 'MongoDB', 27018: 'MongoDB-Shard', 28017: 'MongoDB-Web'
}

# nmap 扫描类型映射
SCAN_TYPES = {
    'S': {'name': 'SYN 扫描', 'nmap_flag': '-sS', 'desc': '半开放扫描，速度快，需要 root'},
    'T': {'name': 'TCP Connect', 'nmap_flag': '-sT', 'desc': '全连接扫描，不需要特权'},
    'U': {'name': 'UDP 扫描', 'nmap_flag': '-sU', 'desc': 'UDP 端口扫描'},
    'N': {'name': 'Null 扫描', 'nmap_flag': '-sN', 'desc': '发送空标志包'},
    'F': {'name': 'FIN 扫描', 'nmap_flag': '-sF', 'desc': '发送 FIN 包'},
    'X': {'name': 'Xmas 扫描', 'nmap_flag': '-sX', 'desc': '发送 FIN/PSH/URG 包'},
    'A': {'name': 'ACK 扫描', 'nmap_flag': '-sA', 'desc': '用于防火墙探测'},
    'W': {'name': 'Window 扫描', 'nmap_flag': '-sW', 'desc': '类似 ACK 扫描'},
    'M': {'name': 'Maimon 扫描', 'nmap_flag': '-sM', 'desc': 'FIN/ACK 扫描'},
}

# 扫描状态管理
scan_state = {
    'running': False,
    'progress': 0,
    'results': [],
    'target': '',
    'error': None,
    'stop_flag': False,
    'thread': None
}


def get_scan_types() -> Dict:
    """获取支持的扫描类型"""
    return SCAN_TYPES


def parse_port_range(port_range: str) -> List[int]:
    """解析端口范围字符串"""
    ports = []
    for part in port_range.split(','):
        part = part.strip()
        if '-' in part:
            start, end = map(int, part.split('-'))
            ports.extend(range(start, end + 1))
        elif part.isdigit():
            ports.append(int(part))
    return sorted(set(ports))


def nmap_async_scan(target_ip: str, ports: str, scan_type: str = 'S',
                    timeout: int = 300, progress_callback: Callable = None,
                    stop_check: Callable = None) -> Tuple[bool, Dict]:
    """
    使用 nmap 进行异步端口扫描

    Args:
        target_ip: 目标 IP
        ports: 端口范围
        scan_type: 扫描类型 (S/T/U/N/F/X/A/W/M)
        timeout: 超时时间
        progress_callback: 进度回调函数
        stop_check: 停止检查函数

    Returns:
        (成功标志，扫描结果)
    """
    scan_info = SCAN_TYPES.get(scan_type, SCAN_TYPES['S'])
    nmap_flag = scan_info['nmap_flag']

    # 构建命令
    cmd = ['nmap', nmap_flag, '-p', ports, '-T4', '--open', '-Pn', target_ip]

    try:
        # 启动 nmap 进程
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1
        )

        open_ports = []
        progress = 0
        start_time = time.time()

        # 实时读取输出
        while True:
            # 检查是否需要停止
            if stop_check and stop_check():
                process.terminate()
                logger.info("扫描被用户中断")
                return True, {'stopped': True, 'open_ports': open_ports}

            # 检查超时
            if time.time() - start_time > timeout:
                process.terminate()
                return False, {'error': f'扫描超时（{timeout}秒）'}

            # 读取一行输出
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break

            line = line.strip()

            # 解析端口信息
            match = re.match(r'(\d+)/(\w+)\s+open\s+(.+)', line)
            if match:
                port = int(match.group(1))
                protocol = match.group(2)
                service_raw = match.group(3).strip()

                # 识别服务
                service = COMMON_PORTS.get(port, service_raw.split()[0] if service_raw else 'unknown')

                open_ports.append({
                    'port': port,
                    'protocol': protocol,
                    'service': service,
                    'state': 'open'
                })

                # 更新进度（基于发现数量估算）
                if progress_callback:
                    progress_callback(len(open_ports), open_ports)

            # 解析进度信息（nmap 可能输出进度百分比）
            progress_match = re.search(r'about (\d+)% done', line)
            if progress_match:
                progress = int(progress_match.group(1))
                if progress_callback:
                    progress_callback(progress, open_ports)

        process.wait()

        if process.returncode != 0:
            stderr = process.stderr.read()
            return False, {'error': stderr.strip() if stderr else 'nmap 执行失败'}

        return True, {
            'target': target_ip,
            'open_ports': open_ports,
            'total_open': len(open_ports),
            'scan_type': scan_info['name']
        }

    except FileNotFoundError:
        logger.warning("nmap 未安装，回退到 socket 扫描")
        return socket_async_scan(target_ip, ports, progress_callback, stop_check)
    except Exception as e:
        logger.exception(f"nmap 扫描失败：{e}")
        return False, {'error': str(e)}


def socket_async_scan(target_ip: str, ports: str,
                      progress_callback: Callable = None,
                      stop_check: Callable = None) -> Tuple[bool, Dict]:
    """
    使用 socket 进行异步 TCP 扫描（nmap 不可用时回退）
    """
    port_list = parse_port_range(ports)
    total = len(port_list)
    open_ports = []

    for i, port in enumerate(port_list):
        # 检查是否需要停止
        if stop_check and stop_check():
            logger.info("Socket 扫描被中断")
            return True, {'stopped': True, 'open_ports': open_ports}

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            result = sock.connect_ex((target_ip, port))
            sock.close()

            if result == 0:
                service = COMMON_PORTS.get(port, 'unknown')
                open_ports.append({
                    'port': port,
                    'protocol': 'tcp',
                    'service': service,
                    'state': 'open'
                })

        except Exception:
            pass

        # 更新进度
        progress = int((i + 1) / total * 100)
        if progress_callback:
            progress_callback(progress, open_ports)

    return True, {
        'target': target_ip,
        'open_ports': open_ports,
        'total_open': len(open_ports),
        'method': 'socket'
    }


def start_async_scan(target_ip: str, ports: str, scan_type: str = 'S',
                     timeout: int = 300) -> Dict:
    """
    启动异步扫描（后台线程）

    Returns:
        启动状态
    """
    global scan_state

    if scan_state['running']:
        return {'status': 'already_running', 'error': '已有扫描任务在运行'}

    # 重置状态
    scan_state['running'] = True
    scan_state['progress'] = 0
    scan_state['results'] = []
    scan_state['target'] = target_ip
    scan_state['error'] = None
    scan_state['stop_flag'] = False

    def scan_thread():
        global scan_state

        def progress_cb(progress, results):
            scan_state['progress'] = progress
            scan_state['results'] = results

        def stop_check():
            return scan_state['stop_flag']

        success, result = nmap_async_scan(
            target_ip, ports, scan_type, timeout,
            progress_callback=progress_cb,
            stop_check=stop_check
        )

        scan_state['running'] = False
        scan_state['progress'] = 100

        if success:
            if result.get('stopped'):
                scan_state['error'] = '扫描被中断'
            else:
                scan_state['results'] = result.get('open_ports', [])
        else:
            scan_state['error'] = result.get('error', '扫描失败')

    scan_state['thread'] = threading.Thread(target=scan_thread, daemon=True)
    scan_state['thread'].start()

    return {
        'status': 'scanning',
        'target': target_ip,
        'ports': ports,
        'scan_type': SCAN_TYPES.get(scan_type, SCAN_TYPES['S'])['name']
    }


def stop_scan() -> Dict:
    """停止当前扫描"""
    global scan_state
    scan_state['stop_flag'] = True
    return {'status': 'stopping'}


def get_scan_progress() -> Dict:
    """获取扫描进度"""
    global scan_state
    return {
        'running': scan_state['running'],
        'progress': scan_state['progress'],
        'target': scan_state['target'],
        'results_count': len(scan_state['results']),
        'error': scan_state['error']
    }


def get_scan_results() -> Dict:
    """获取扫描结果"""
    global scan_state
    return {
        'running': scan_state['running'],
        'results': scan_state['results'],
        'target': scan_state['target'],
        'total': len(scan_state['results']),
        'error': scan_state['error']
    }


def port_scan(target_ip: str, ports: str = '1-1000', scan_type: str = 'S',
              timeout: int = 120) -> Tuple[bool, Dict]:
    """
    端口扫描（同步接口，兼容旧代码）

    Args:
        target_ip: 目标 IP
        ports: 端口范围
        scan_type: 扫描类型
        timeout: 超时时间

    Returns:
        (成功标志，扫描结果)
    """
    return nmap_async_scan(target_ip, ports, scan_type, timeout)


def scan_common_ports(target_ip: str) -> Tuple[bool, Dict]:
    """扫描常见端口"""
    common_port_list = ','.join(map(str, COMMON_PORTS.keys()))
    return socket_async_scan(target_ip, common_port_list)


def check_single_port(target_ip: str, port: int,
                      protocol: str = 'tcp', timeout: int = 2) -> Tuple[bool, Dict]:
    """检查单个端口"""
    try:
        if protocol == 'tcp':
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((target_ip, port))
            sock.close()

            if result == 0:
                return True, {
                    'open': True,
                    'port': port,
                    'protocol': 'tcp',
                    'service': COMMON_PORTS.get(port, 'unknown')
                }
            else:
                return True, {'open': False, 'port': port, 'protocol': 'tcp'}
        else:
            return False, {'error': '不支持的协议'}

    except Exception as e:
        return False, {'error': str(e)}