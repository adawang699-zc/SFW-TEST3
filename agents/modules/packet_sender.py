#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报文发送模块
使用 Scapy 发送 TCP/UDP/ICMP 报文
"""

import logging
import time
from typing import Tuple, Dict, Optional
from scapy.all import IP, TCP, UDP, ICMP, Raw, send, sendp, sr1
from scapy.layers.l2 import Ether

logger = logging.getLogger(__name__)


def send_tcp_packet(src_ip: str, dst_ip: str, src_port: int, dst_port: int,
                    payload: str = '', count: int = 1,
                    interval: float = 0,
                    interface: str = None) -> Tuple[bool, Dict]:
    """
    发送 TCP 报文

    Args:
        src_ip: 源 IP
        dst_ip: 目标 IP
        src_port: 源端口
        dst_port: 目标端口
        payload: 载荷数据
        count: 发送数量
        interval: 发送间隔（秒）
        interface: 网络接口（可选）

    Returns:
        (成功标志，统计信息)
    """
    try:
        # 构建报文
        ip_layer = IP(src=src_ip, dst=dst_ip)
        tcp_layer = TCP(sport=src_port, dport=dst_port, flags='PA')  # PSH+ACK

        if payload:
            pkt = ip_layer / tcp_layer / Raw(load=payload.encode() if isinstance(payload, str) else payload)
        else:
            pkt = ip_layer / tcp_layer

        # 发送统计
        start_time = time.time()
        packets_sent = 0

        for i in range(count):
            if interface:
                sendp(pkt, iface=interface, verbose=0)
            else:
                send(pkt, verbose=0)
            packets_sent += 1

            if interval > 0 and i < count - 1:
                time.sleep(interval)

        elapsed = time.time() - start_time

        stats = {
            'packets_sent': packets_sent,
            'elapsed_time': elapsed,
            'rate': packets_sent / elapsed if elapsed > 0 else 0,
            'total_bytes': len(pkt) * packets_sent
        }

        logger.info(f"TCP 报文发送完成：{packets_sent} packets, {stats['total_bytes']} bytes")
        return True, stats

    except Exception as e:
        logger.exception(f"TCP 报文发送失败：{e}")
        return False, {'error': str(e)}


def send_udp_packet(src_ip: str, dst_ip: str, src_port: int, dst_port: int,
                    payload: str = '', count: int = 1,
                    interval: float = 0,
                    interface: str = None) -> Tuple[bool, Dict]:
    """发送 UDP 报文"""
    try:
        ip_layer = IP(src=src_ip, dst=dst_ip)
        udp_layer = UDP(sport=src_port, dport=dst_port)

        if payload:
            pkt = ip_layer / udp_layer / Raw(load=payload.encode() if isinstance(payload, str) else payload)
        else:
            pkt = ip_layer / udp_layer

        start_time = time.time()
        packets_sent = 0

        for i in range(count):
            if interface:
                sendp(pkt, iface=interface, verbose=0)
            else:
                send(pkt, verbose=0)
            packets_sent += 1

            if interval > 0 and i < count - 1:
                time.sleep(interval)

        elapsed = time.time() - start_time

        stats = {
            'packets_sent': packets_sent,
            'elapsed_time': elapsed,
            'rate': packets_sent / elapsed if elapsed > 0 else 0,
            'total_bytes': len(pkt) * packets_sent
        }

        logger.info(f"UDP 报文发送完成：{packets_sent} packets")
        return True, stats

    except Exception as e:
        logger.exception(f"UDP 报文发送失败：{e}")
        return False, {'error': str(e)}


def send_icmp_packet(src_ip: str, dst_ip: str,
                     data: str = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
                     count: int = 4,
                     timeout: int = 2) -> Tuple[bool, Dict]:
    """
    发送 ICMP ping 报文

    Args:
        src_ip: 源 IP
        dst_ip: 目标 IP
        data: 数据载荷
        count: 发送数量
        timeout: 超时时间

    Returns:
        (成功标志，统计信息)
    """
    try:
        results = {
            'sent': 0,
            'received': 0,
            'lost': 0,
            'rtts': [],
            'responses': []
        }

        for i in range(count):
            pkt = IP(src=src_ip, dst=dst_ip) / ICMP() / Raw(load=data)
            results['sent'] += 1

            try:
                response = sr1(pkt, timeout=timeout, verbose=0)
                if response:
                    results['received'] += 1
                    # 计算 RTT（如果有时间戳）
                    results['responses'].append({
                        'src': response[IP].src if IP in response else 'unknown',
                        'type': response[ICMP].type if ICMP in response else 'unknown'
                    })
                else:
                    results['lost'] += 1

            except Exception as e:
                results['lost'] += 1
                logger.warning(f"ICMP 无响应：{e}")

        results['loss_rate'] = results['lost'] / results['sent'] if results['sent'] > 0 else 0
        results['success'] = results['received'] > 0

        logger.info(f"ICMP 发送完成：{results['sent']} sent, {results['received']} received")
        return True, results

    except Exception as e:
        logger.exception(f"ICMP 报文发送失败：{e}")
        return False, {'error': str(e)}


def send_custom_packet(packet_hex: str, interface: str = None,
                       count: int = 1, interval: float = 0) -> Tuple[bool, Dict]:
    """
    发送自定义十六进制报文

    Args:
        packet_hex: 十六进制报文数据（如 "08004500..."）
        interface: 网络接口
        count: 发送数量
        interval: 间隔

    Returns:
        (成功标志，统计信息)
    """
    try:
        # 解析十六进制报文
        pkt = Ether(bytes.fromhex(packet_hex))

        start_time = time.time()
        packets_sent = 0

        for i in range(count):
            sendp(pkt, iface=interface, verbose=0)
            packets_sent += 1

            if interval > 0 and i < count - 1:
                time.sleep(interval)

        elapsed = time.time() - start_time

        stats = {
            'packets_sent': packets_sent,
            'elapsed_time': elapsed,
            'total_bytes': len(bytes(pkt)) * packets_sent
        }

        return True, stats

    except Exception as e:
        logger.exception(f"自定义报文发送失败：{e}")
        return False, {'error': str(e)}
