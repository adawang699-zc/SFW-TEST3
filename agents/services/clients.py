#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
客户端服务模块
提供 TCP/UDP/FTP/HTTP/Mail 客户端连接功能
"""

import socket
import threading
import logging
import time
from typing import Dict, Tuple, Optional
from collections import deque

logger = logging.getLogger(__name__)

# 客户端状态管理
client_states: Dict[str, dict] = {
    'tcp': {},
    'udp': {},
    'ftp': {},
    'http': {},
    'mail': {}
}
state_lock = threading.Lock()

# 服务日志
client_logs = deque(maxlen=500)
log_lock = threading.Lock()


def add_client_log(source: str, message: str, level: str = 'info'):
    """记录客户端日志"""
    entry = {
        'timestamp': time.strftime('%H:%M:%S'),
        'source': source,
        'level': level,
        'message': message
    }
    with log_lock:
        client_logs.appendleft(entry)
    logger.info(f"[{source}] {message}")


class ClientManager:
    """客户端管理器"""

    def __init__(self):
        self.connections = {}
        self.lock = threading.Lock()

    def start_tcp_client(self, client_id: str, host: str, port: int,
                        interface: str = None) -> Tuple[bool, str]:
        """启动 TCP 客户端"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)

            # 绑定指定接口（可选）
            if interface:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
                except:
                    pass

            sock.connect((host, port))

            with self.lock:
                self.connections[f'tcp_{client_id}'] = {
                    'socket': sock,
                    'host': host,
                    'port': port,
                    'connected': True,
                    'start_time': time.time()
                }

            add_client_log('TCP', f'连接成功: {host}:{port}')
            return True, f'TCP 客户端连接成功: {host}:{port}'

        except Exception as e:
            add_client_log('TCP', f'连接失败: {str(e)}', 'error')
            return False, str(e)

    def stop_tcp_client(self, client_id: str) -> Tuple[bool, str]:
        """停止 TCP 客户端"""
        with self.lock:
            conn_key = f'tcp_{client_id}'
            if conn_key in self.connections:
                try:
                    self.connections[conn_key]['socket'].close()
                except:
                    pass
                del self.connections[conn_key]
                add_client_log('TCP', '连接已关闭')
                return True, 'TCP 客户端已断开'
            return False, '连接不存在'

    def start_udp_client(self, client_id: str, host: str, port: int,
                        interface: str = None) -> Tuple[bool, str]:
        """启动 UDP 客户端"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(5)

            # 绑定指定接口（可选）
            if interface:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
                except:
                    pass

            with self.lock:
                self.connections[f'udp_{client_id}'] = {
                    'socket': sock,
                    'host': host,
                    'port': port,
                    'connected': True,
                    'start_time': time.time()
                }

            add_client_log('UDP', f'UDP 客户端启动: {host}:{port}')
            return True, f'UDP 客户端启动成功: {host}:{port}'

        except Exception as e:
            add_client_log('UDP', f'启动失败: {str(e)}', 'error')
            return False, str(e)

    def stop_udp_client(self, client_id: str) -> Tuple[bool, str]:
        """停止 UDP 客户端"""
        with self.lock:
            conn_key = f'udp_{client_id}'
            if conn_key in self.connections:
                try:
                    self.connections[conn_key]['socket'].close()
                except:
                    pass
                del self.connections[conn_key]
                add_client_log('UDP', 'UDP 客户端已关闭')
                return True, 'UDP 客户端已关闭'
            return False, '连接不存在'

    def send_data(self, client_type: str, client_id: str, data: bytes) -> Tuple[bool, str]:
        """发送数据"""
        conn_key = f'{client_type}_{client_id}'

        with self.lock:
            if conn_key not in self.connections:
                return False, '连接不存在'

            conn = self.connections[conn_key]

            try:
                if client_type == 'tcp':
                    conn['socket'].sendall(data)
                elif client_type == 'udp':
                    conn['socket'].sendto(data, (conn['host'], conn['port']))

                add_client_log(client_type.upper(), f'发送数据: {len(data)} bytes')
                return True, '发送成功'

            except Exception as e:
                add_client_log(client_type.upper(), f'发送失败: {str(e)}', 'error')
                return False, str(e)

    def receive_data(self, client_type: str, client_id: str, timeout: int = 5) -> Tuple[bool, bytes]:
        """接收数据"""
        conn_key = f'{client_type}_{client_id}'

        with self.lock:
            if conn_key not in self.connections:
                return False, b''

            conn = self.connections[conn_key]

            try:
                conn['socket'].settimeout(timeout)

                if client_type == 'tcp':
                    data = conn['socket'].recv(4096)
                elif client_type == 'udp':
                    data, addr = conn['socket'].recvfrom(4096)

                add_client_log(client_type.upper(), f'接收数据: {len(data)} bytes')
                return True, data

            except socket.timeout:
                return False, b''
            except Exception as e:
                add_client_log(client_type.upper(), f'接收失败: {str(e)}', 'error')
                return False, b''

    def get_status(self) -> Dict:
        """获取所有客户端状态"""
        with self.lock:
            status = {}
            for conn_key, conn in self.connections.items():
                client_type, client_id = conn_key.split('_', 1)
                if client_type not in status:
                    status[client_type] = {}

                status[client_type][client_id] = {
                    'connected': conn['connected'],
                    'host': conn['host'],
                    'port': conn['port'],
                    'uptime': int(time.time() - conn['start_time'])
                }
            return status

    def get_logs(self, lines: int = 50) -> list:
        """获取客户端日志"""
        with log_lock:
            return list(client_logs)[:lines]


# 全局客户端管理器
client_manager = ClientManager()