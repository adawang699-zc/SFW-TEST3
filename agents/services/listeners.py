#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端口监听服务模块
提供 TCP/UDP 端口监听、服务可用性检查功能
"""

import socket
import threading
import logging
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# 监听器状态管理
listener_states: Dict[str, dict] = {
    'tcp': {},  # {port: {'running': bool, 'socket': socket, 'thread': Thread}}
    'udp': {},
}
state_lock = threading.Lock()


def start_tcp_listener(port: int, interface: str = '0.0.0.0',
                       backlog: int = 5,
                       on_connection: callable = None) -> Tuple[bool, str]:
    """
    启动 TCP 端口监听

    Args:
        port: 监听端口
        interface: 绑定接口（默认所有接口）
        backlog: 最大连接队列长度
        on_connection: 连接回调函数 on_connection(conn, addr)

    Returns:
        (成功标志，消息/错误信息)
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((interface, port))
        sock.listen(backlog)
        sock.settimeout(1.0)  # 1 秒超时，方便停止

        running = True
        connections = []

        def accept_loop():
            nonlocal running
            while running:
                try:
                    conn, addr = sock.accept()
                    logger.info(f"TCP {port} 收到连接：{addr}")

                    if on_connection:
                        threading.Thread(
                            target=on_connection,
                            args=(conn, addr),
                            daemon=True
                        ).start()
                    else:
                        # 默认处理：接收数据并关闭
                        threading.Thread(
                            target=_handle_connection_default,
                            args=(conn, addr, port),
                            daemon=True
                        ).start()

                except socket.timeout:
                    continue
                except Exception as e:
                    if running:
                        logger.error(f"TCP 监听异常：{e}")
                    break

        thread = threading.Thread(target=accept_loop, daemon=True)
        thread.start()

        with state_lock:
            listener_states['tcp'][port] = {
                'running': True,
                'socket': sock,
                'thread': thread,
                'connections': connections
            }

        logger.info(f"TCP 监听器已启动：{interface}:{port}")
        return True, f"监听器已启动：{interface}:{port}"

    except OSError as e:
        logger.error(f"TCP 监听器启动失败：{e}")
        return False, f"启动失败：{e}"
    except Exception as e:
        logger.exception(f"TCP 监听器启动异常：{e}")
        return False, f"启动异常：{e}"


def _handle_connection_default(conn, addr, port):
    """默认连接处理：记录并关闭"""
    try:
        data = conn.recv(4096)
        if data:
            logger.info(f"TCP {port} 收到数据：{len(data)} bytes")
        conn.close()
    except:
        pass


def stop_tcp_listener(port: int) -> Tuple[bool, str]:
    """停止 TCP 端口监听"""
    with state_lock:
        if port not in listener_states['tcp']:
            return False, f"端口 {port} 没有运行的监听器"

        state = listener_states['tcp'][port]
        state['running'] = False

        # 关闭 socket
        try:
            if state['socket']:
                state['socket'].close()
        except:
            pass

        # 等待线程结束
        if state['thread']:
            state['thread'].join(timeout=3)

        del listener_states['tcp'][port]
        logger.info(f"TCP 监听器已停止：端口 {port}")
        return True, "监听器已停止"


def start_udp_listener(port: int, interface: str = '0.0.0.0') -> Tuple[bool, str]:
    """启动 UDP 端口监听"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((interface, port))
        sock.settimeout(1.0)

        running = True

        def recv_loop():
            nonlocal running
            while running:
                try:
                    data, addr = sock.recvfrom(65535)
                    logger.info(f"UDP {port} 收到数据：{len(data)} bytes from {addr}")
                except socket.timeout:
                    continue
                except Exception as e:
                    if running:
                        logger.error(f"UDP 监听异常：{e}")
                    break

        thread = threading.Thread(target=recv_loop, daemon=True)
        thread.start()

        with state_lock:
            listener_states['udp'][port] = {
                'running': True,
                'socket': sock,
                'thread': thread
            }

        return True, f"UDP 监听器已启动：{interface}:{port}"

    except Exception as e:
        logger.exception(f"UDP 监听器启动失败：{e}")
        return False, str(e)


def stop_udp_listener(port: int) -> Tuple[bool, str]:
    """停止 UDP 端口监听"""
    with state_lock:
        if port not in listener_states['udp']:
            return False, f"端口 {port} 没有运行的监听器"

        state = listener_states['udp'][port]
        state['running'] = False

        try:
            if state['socket']:
                state['socket'].close()
        except:
            pass

        if state['thread']:
            state['thread'].join(timeout=3)

        del listener_states['udp'][port]
        return True, "UDP 监听器已停止"


def check_port_service(host: str, port: int, protocol: str = 'tcp',
                       timeout: int = 3) -> Tuple[bool, str]:
    """
    检查端口服务可用性

    Args:
        host: 目标主机
        port: 目标端口
        protocol: 协议类型 ('tcp' 或 'udp')
        timeout: 超时时间

    Returns:
        (服务是否可用，详情)
    """
    try:
        if protocol == 'tcp':
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()

            if result == 0:
                return True, f"端口 {port} 服务可用"
            else:
                return False, f"端口 {port} 无服务"

        elif protocol == 'udp':
            # UDP 检查：发送空数据，看是否返回 ICMP 端口不可达
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(timeout)
            try:
                sock.sendto(b'', (host, port))
                # 等待响应（如果有错误会返回 ICMP）
                sock.settimeout(timeout)
                data, addr = sock.recvfrom(1)
                return False, f"收到响应：{data}"
            except socket.timeout:
                # 超时说明端口可能开放（UDP 无响应是正常的）
                return True, f"端口 {port} 可能开放（UDP 无响应）"
            except Exception as e:
                return False, str(e)

        else:
            return False, f"不支持的协议：{protocol}"

    except Exception as e:
        return False, f"检查失败：{e}"


def get_listener_status() -> dict:
    """获取所有监听器状态"""
    with state_lock:
        status = {}
        for proto, ports in listener_states.items():
            status[proto] = {
                port: {'running': state['running']}
                for port, state in ports.items()
            }
        return status
