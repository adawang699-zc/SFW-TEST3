#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端口扫描模块
支持 nmap 和 socket 两种扫描方式
"""

import subprocess
import logging
import re
import socket
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# 常见端口列表
COMMON_PORTS = {
    21: 'FTP', 22: 'SSH', 23: 'Telnet', 25: 'SMTP', 53: 'DNS',
    80: 'HTTP', 110: 'POP3', 143: 'IMAP', 443: 'HTTPS', 993: 'IMAPS',
    995: 'POP3S', 3306: 'MySQL', 5432: 'PostgreSQL', 6379: 'Redis',
    8080: 'HTTP-Proxy', 8443: 'HTTPS-Alt', 27017: 'MongoDB'
}


def port_scan(target_ip: str, ports: str = '1-1000', scan_type: str = 'S',
             timeout: int = 120, use_nmap: bool = True) -> Tuple[bool, Dict]:
    """
    端口扫描

    Args:
        target_ip: 目标 IP
        ports: 端口范围（如 "1-1000" 或 "22,80,443"）
        scan_type: 扫描类型 ('S'=SYN, 'T'=TCP connect)
        timeout: 超时时间（秒）
        use_nmap: 是否使用 nmap（否则使用 socket 扫描）

    Returns:
        (成功标志，扫描结果)
    """
    if use_nmap:
        return _scan_with_nmap(target_ip, ports, scan_type, timeout)
    else:
        return _scan_with_socket(target_ip, ports)


def _scan_with_nmap(target_ip: str, ports: str, scan_type: str,
                   timeout: int) -> Tuple[bool, Dict]:
    """使用 nmap 进行端口扫描"""
    try:
        cmd = f'nmap -p{ports} -s{scan_type} -T4 --open {target_ip}'
        result = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout
        )

        if result.returncode != 0:
            return False, {'error': result.stderr}

        # 解析 nmap 输出
        open_ports = []
        for line in result.stdout.split('\n'):
            match = re.match(r'(\d+)/(\w+)\s+open\s+(\w+)?', line)
            if match:
                port = int(match.group(1))
                protocol = match.group(2)
                service = match.group(3) or COMMON_PORTS.get(port, 'unknown')

                open_ports.append({
                    'port': port,
                    'protocol': protocol,
                    'service': service
                })

        return True, {
            'target': target_ip,
            'open_ports': open_ports,
            'total_open': len(open_ports),
            'raw_output': result.stdout
        }

    except subprocess.TimeoutExpired:
        return False, {'error': f'扫描超时（{timeout}秒）'}
    except Exception as e:
        logger.exception(f"nmap 扫描失败：{e}")
        return False, {'error': str(e)}


def _scan_with_socket(target_ip: str, ports: str) -> Tuple[bool, Dict]:
    """使用 socket 进行 TCP 连接扫描（nmap 不可用时回退）"""
    try:
        # 解析端口范围
        port_list = []
        for part in ports.split(','):
            part = part.strip()
            if '-' in part:
                start, end = map(int, part.split('-'))
                port_list.extend(range(start, end + 1))
            else:
                port_list.append(int(part))

        open_ports = []

        for port in port_list:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex((target_ip, port))
                sock.close()

                if result == 0:
                    service = COMMON_PORTS.get(port, 'unknown')
                    open_ports.append({
                        'port': port,
                        'protocol': 'tcp',
                        'service': service
                    })
            except:
                continue

        return True, {
            'target': target_ip,
            'open_ports': open_ports,
            'total_open': len(open_ports),
            'method': 'socket'
        }

    except Exception as e:
        return False, {'error': str(e)}


def scan_common_ports(target_ip: str) -> Tuple[bool, Dict]:
    """扫描常见端口"""
    common_port_list = ','.join(map(str, COMMON_PORTS.keys()))
    return port_scan(target_ip, common_port_list, 'T', timeout=60, use_nmap=False)


def check_single_port(target_ip: str, port: int,
                     protocol: str = 'tcp',
                     timeout: int = 2) -> Tuple[bool, Dict]:
    """
    检查单个端口

    Args:
        target_ip: 目标 IP
        port: 端口号
        protocol: 协议类型
        timeout: 超时时间

    Returns:
        (端口是否开放，详情)
    """
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
