"""
Agent 功能模块
包含报文发送、端口扫描、报文回放、报文捕获等功能
"""

# 导入函数而不是类
from .packet_sender import send_tcp_packet, send_udp_packet
from .port_scanner import (
    port_scan, start_async_scan, stop_scan,
    get_scan_progress, get_scan_results, get_scan_types, COMMON_PORTS
)
from .packet_replay import start_replay, stop_replay, get_replay_status
from .packet_capture import start_capture, stop_capture, save_capture_to_pcap

__all__ = [
    'send_tcp_packet',
    'send_udp_packet',
    'port_scan',
    'start_async_scan',
    'stop_scan',
    'get_scan_progress',
    'get_scan_results',
    'start_replay',
    'stop_replay',
    'get_replay_status',
    'start_capture',
    'stop_capture',
    'save_capture'
]