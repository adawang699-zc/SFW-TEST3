#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DHCP 客户端模块
实现 DHCP 客户端功能，用于获取 IP 地址
"""

import logging
import threading
import socket
import time
import struct
import random
from typing import Dict, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# DHCP 状态
dhcp_states: Dict[str, dict] = {}
state_lock = threading.Lock()


class DhcpClient:
    """DHCP 客户端"""

    def __init__(self):
        self.clients = {}
        self.lock = threading.Lock()

    def start(self, client_id: str = 'default', interface: str = None) -> Tuple[bool, str]:
        """启动 DHCP 客户端"""
        with self.lock:
            if client_id in self.clients and self.clients[client_id].get('running'):
                return False, "DHCP 客户端已在运行"

            # 在后台线程中运行 DHCP 过程
            def dhcp_thread():
                try:
                    # 创建 UDP socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

                    # 绑定到指定接口（如果提供）
                    if interface:
                        try:
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, interface.encode())
                        except:
                            pass

                    sock.bind(('0.0.0.0', 68))
                    sock.settimeout(10)

                    # 构造 DHCP Discover 包
                    transaction_id = random.randint(0, 0xFFFFFFFF)
                    mac_addr = self._get_interface_mac(interface)

                    discover_packet = self._build_dhcp_discover(transaction_id, mac_addr)

                    # 发送 DHCP Discover
                    sock.sendto(discover_packet, ('255.255.255.255', 67))
                    logger.info(f"发送 DHCP Discover (interface={interface})")

                    # 接收 DHCP Offer
                    try:
                        offer_data, addr = sock.recvfrom(1024)
                        offer_ip, server_ip = self._parse_dhcp_offer(offer_data, transaction_id)

                        if offer_ip:
                            logger.info(f"收到 DHCP Offer: IP={offer_ip}, Server={server_ip}")

                            # 发送 DHCP Request
                            request_packet = self._build_dhcp_request(transaction_id, mac_addr, offer_ip, server_ip)
                            sock.sendto(request_packet, ('255.255.255.255', 67))
                            logger.info(f"发送 DHCP Request for {offer_ip}")

                            # 接收 DHCP ACK
                            try:
                                ack_data, addr = sock.recvfrom(1024)
                                assigned_ip = self._parse_dhcp_ack(ack_data, transaction_id)

                                if assigned_ip:
                                    logger.info(f"收到 DHCP ACK: 分配 IP={assigned_ip}")

                                    with self.lock:
                                        self.clients[client_id] = {
                                            'running': False,  # 完成后停止
                                            'interface': interface,
                                            'assigned_ip': assigned_ip,
                                            'server_ip': server_ip,
                                            'lease_time': 'unknown',
                                            'status': 'success'
                                        }
                                else:
                                    logger.error("DHCP ACK 解析失败")

                            except socket.timeout:
                                logger.error("等待 DHCP ACK 超时")

                        else:
                            logger.error("DHCP Offer 解析失败")

                    except socket.timeout:
                        logger.error("等待 DHCP Offer 超时")

                    sock.close()

                except Exception as e:
                    logger.exception(f"DHCP 客户端异常: {e}")
                    with self.lock:
                        self.clients[client_id] = {
                            'running': False,
                            'interface': interface,
                            'assigned_ip': None,
                            'status': 'failed',
                            'error': str(e)
                        }

            thread = threading.Thread(target=dhcp_thread, daemon=True)
            thread.start()

            self.clients[client_id] = {
                'running': True,
                'interface': interface,
                'thread': thread,
                'start_time': datetime.now().isoformat()
            }

            logger.info(f"DHCP 客户端启动: {interface}")
            return True, "DHCP 客户端启动中..."

    def status(self, client_id: str = 'default') -> Dict:
        """获取 DHCP 客户端状态"""
        with self.lock:
            if client_id in self.clients:
                return self.clients[client_id]
            return {'running': False, 'status': 'unknown'}

    def _get_interface_mac(self, interface: str) -> str:
        """获取网卡 MAC 地址"""
        if not interface:
            return '00:00:00:00:00:00'

        try:
            import psutil
            addrs = psutil.net_if_addrs().get(interface, [])
            for addr in addrs:
                if addr.family == psutil.AF_LINK:
                    return addr.address.replace('-', ':')
        except:
            pass

        return '00:00:00:00:00:00'

    def _build_dhcp_discover(self, transaction_id: int, mac_addr: str) -> bytes:
        """构造 DHCP Discover 包"""
        # DHCP 包结构
        # op: 1 (request), htype: 1 (ethernet), hlen: 6, hops: 0
        # xid: transaction_id
        # secs: 0, flags: 0x8000 (broadcast)
        # ciaddr: 0, yiaddr: 0, siaddr: 0, giaddr: 0
        # chaddr: mac_addr (16 bytes, padded)
        # sname: 64 bytes empty
        # file: 128 bytes empty
        # options: magic cookie + DHCP message type (DISCOVER) + client ID + end

        mac_bytes = bytes.fromhex(mac_addr.replace(':', ''))

        packet = struct.pack(
            '!BBBBIHH4s4s4s4s16s64s128s',
            1,      # op: BOOTREQUEST
            1,      # htype: Ethernet
            6,      # hlen: MAC length
            0,      # hops
            transaction_id,
            0,      # secs
            0x8000, # flags: broadcast
            b'\x00\x00\x00\x00',  # ciaddr
            b'\x00\x00\x00\x00',  # yiaddr
            b'\x00\x00\x00\x00',  # siaddr
            b'\x00\x00\x00\x00',  # giaddr
            mac_bytes + b'\x00' * 10,  # chaddr
            b'\x00' * 64,  # sname
            b'\x00' * 128  # file
        )

        # DHCP Options
        # Magic Cookie: 99.130.83.99
        options = b'\x63\x82\x53\x63'
        # DHCP Message Type: DISCOVER (53, 1, 1)
        options += b'\x35\x01\x01'
        # Client Identifier (61, 7, 1, mac)
        options += b'\x3d\x07\x01' + mac_bytes
        # Parameter Request List (55, 4, subnet mask, router, DNS, lease time)
        options += b'\x37\x04\x01\x03\x06\x33'
        # End
        options += b'\xff'

        return packet + options

    def _build_dhcp_request(self, transaction_id: int, mac_addr: str,
                            request_ip: str, server_ip: str) -> bytes:
        """构造 DHCP Request 包"""
        mac_bytes = bytes.fromhex(mac_addr.replace(':', ''))
        request_ip_bytes = bytes([int(x) for x in request_ip.split('.')])
        server_ip_bytes = bytes([int(x) for x in server_ip.split('.')])

        packet = struct.pack(
            '!BBBBIHH4s4s4s4s16s64s128s',
            1, 1, 6, 0,
            transaction_id,
            0, 0x8000,
            b'\x00\x00\x00\x00',
            b'\x00\x00\x00\x00',
            b'\x00\x00\x00\x00',
            b'\x00\x00\x00\x00',
            mac_bytes + b'\x00' * 10,
            b'\x00' * 64,
            b'\x00' * 128
        )

        options = b'\x63\x82\x53\x63'  # Magic Cookie
        options += b'\x35\x01\x03'     # DHCP Message Type: REQUEST
        options += b'\x32\x04' + request_ip_bytes  # Requested IP
        options += b'\x36\x04' + server_ip_bytes   # Server Identifier
        options += b'\x3d\x07\x01' + mac_bytes      # Client Identifier
        options += b'\xff'                          # End

        return packet + options

    def _parse_dhcp_offer(self, data: bytes, expected_xid: int) -> Tuple[Optional[str], Optional[str]]:
        """解析 DHCP Offer"""
        if len(data) < 240:
            return None, None

        try:
            xid = struct.unpack('!I', data[4:8])[0]
            if xid != expected_xid:
                return None, None

            yiaddr = '.'.join(str(b) for b in data[16:20])
            siaddr = '.'.join(str(b) for b in data[20:24])

            return yiaddr, siaddr

        except:
            return None, None

    def _parse_dhcp_ack(self, data: bytes, expected_xid: int) -> Optional[str]:
        """解析 DHCP ACK"""
        if len(data) < 240:
            return None

        try:
            xid = struct.unpack('!I', data[4:8])[0]
            if xid != expected_xid:
                return None

            yiaddr = '.'.join(str(b) for b in data[16:20])
            return yiaddr

        except:
            return None


# 全局 DHCP 客户端实例
dhcp_client = DhcpClient()