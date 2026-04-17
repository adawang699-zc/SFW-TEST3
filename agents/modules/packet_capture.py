#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报文捕获模块
使用 Scapy 进行网络流量捕获和保存
"""

import logging
import threading
import time
from typing import Tuple, Dict, Optional
from scapy.all import sniff, wrpcap, rdpcap, Packet

logger = logging.getLogger(__name__)

# 全局状态
capture_state = {
    'running': False,
    'packets_captured': 0,
    'start_time': None,
    'thread': None,
    'packets': []
}
capture_lock = threading.Lock()


def start_capture(interface: str = None, filter_str: str = None,
                 count: int = 0, timeout: int = 60,
                 on_packet: callable = None) -> Tuple[bool, str]:
    """
    开始报文捕获

    Args:
        interface: 网络接口
        filter_str: BPF 过滤器（如 "tcp" 或 "port 80"）
        count: 捕获数量（0=无限）
        timeout: 超时时间（秒）
        on_packet: 每个报包的回调函数

    Returns:
        (成功标志，消息)
    """
    with capture_lock:
        if capture_state['running']:
            return False, "已有捕获任务在运行"

        capture_state['running'] = True
        capture_state['packets_captured'] = 0
        capture_state['start_time'] = time.time()
        capture_state['packets'] = []
        capture_state['thread'] = None

    def packet_callback(pkt: Packet):
        with capture_lock:
            capture_state['packets_captured'] += 1
            capture_state['packets'].append(pkt)

        if on_packet:
            try:
                on_packet(pkt)
            except Exception as e:
                logger.warning(f"回调异常：{e}")

    def capture_thread_func():
        try:
            sniff(
                iface=interface,
                filter=filter_str,
                prn=packet_callback,
                count=count if count > 0 else None,
                timeout=timeout,
                store=False  # 不存储，使用自定义回调
            )
        except Exception as e:
            logger.error(f"捕获异常：{e}")
        finally:
            with capture_lock:
                capture_state['running'] = False

    thread = threading.Thread(target=capture_thread_func, daemon=True)
    thread.start()

    with capture_lock:
        capture_state['thread'] = thread

    logger.info(f"开始报文捕获：interface={interface}, filter={filter_str}")
    return True, "报文捕获已启动"


def stop_capture() -> Tuple[bool, str]:
    """停止报文捕获"""
    with capture_lock:
        if not capture_state['running']:
            return False, "没有运行的捕获任务"

        capture_state['running'] = False

        # 等待线程结束
        if capture_state['thread']:
            capture_state['thread'].join(timeout=5)

        captured = capture_state['packets_captured']

    logger.info(f"报文捕获已停止，捕获 {captured} 个报文")
    return True, f"已停止，捕获 {captured} 个报文"


def save_capture_to_pcap(file_path: str) -> Tuple[bool, str]:
    """
    保存捕获的报文到 PCAP 文件

    Args:
        file_path: 文件路径

    Returns:
        (成功标志，消息)
    """
    with capture_lock:
        packets = capture_state['packets'].copy()

    if not packets:
        return False, "没有捕获到报文"

    try:
        wrpcap(file_path, packets)
        logger.info(f"报文已保存到：{file_path} ({len(packets)} packets)")
        return True, f"已保存 {len(packets)} 个报文到 {file_path}"
    except Exception as e:
        logger.exception(f"保存失败：{e}")
        return False, str(e)


def get_capture_status() -> Dict:
    """获取捕获状态"""
    with capture_lock:
        return {
            'running': capture_state['running'],
            'packets_captured': capture_state['packets_captured'],
            'start_time': capture_state['start_time'],
            'duration': time.time() - capture_state['start_time'] if capture_state['start_time'] else 0
        }
