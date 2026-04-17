#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报文回放模块
从 PCAP 文件读取并回放网络流量
"""

import logging
import threading
import time
from typing import Tuple, Dict, List, Optional
from scapy.all import rdpcap, sendp, send, Ether

logger = logging.getLogger(__name__)

# 全局状态
replay_state = {
    'running': False,
    'packets_sent': 0,
    'start_time': None,
    'thread': None,
    'current_file': None
}
replay_lock = threading.Lock()
stop_replay_event = threading.Event()


def start_replay(pcap_files: List[str], interface: str,
                count: int = 1, interval: float = 0,
                use_layer2: bool = True) -> Tuple[bool, str]:
    """
    开始报文回放

    Args:
        pcap_files: PCAP 文件列表
        interface: 发送接口
        count: 回放次数（每个文件）
        interval: 报文间隔（秒）
        use_layer2: 是否使用二层发送（sendp），否则用 send

    Returns:
        (成功标志，消息)
    """
    with replay_lock:
        if replay_state['running']:
            return False, "已有回放任务在运行"

        replay_state['running'] = True
        replay_state['packets_sent'] = 0
        replay_state['start_time'] = time.time()
        replay_state['thread'] = None
        replay_state['current_file'] = None

    stop_replay_event.clear()

    def replay_thread_func():
        nonlocal pcap_files, interface, count, interval, use_layer2

        for file_idx, pcap_file in enumerate(pcap_files):
            if stop_replay_event.is_set():
                break

            try:
                with replay_lock:
                    replay_state['current_file'] = pcap_file

                packets = rdpcap(pcap_file)
                logger.info(f"回放文件 {file_idx+1}/{len(pcap_files)}: {pcap_file} ({len(packets)} packets)")

                for i in range(count):
                    if stop_replay_event.is_set():
                        break

                    for pkt in packets:
                        if stop_replay_event.is_set():
                            break

                        try:
                            if use_layer2:
                                sendp(pkt, iface=interface, verbose=0)
                            else:
                                send(pkt, verbose=0)

                            with replay_lock:
                                replay_state['packets_sent'] += 1

                            if interval > 0:
                                time.sleep(interval)

                        except Exception as e:
                            logger.warning(f"发送报文失败：{e}")

            except Exception as e:
                logger.error(f"回放文件失败：{pcap_file}: {e}")

        with replay_lock:
            replay_state['running'] = False
            replay_state['current_file'] = None

        logger.info(f"报文回放完成，发送 {replay_state['packets_sent']} 个报文")

    thread = threading.Thread(target=replay_thread_func, daemon=True)
    thread.start()

    with replay_lock:
        replay_state['thread'] = thread

    logger.info(f"开始报文回放：{len(pcap_files)} files, interface={interface}")
    return True, "报文回放已启动"


def stop_replay() -> Tuple[bool, str]:
    """停止报文回放"""
    stop_replay_event.set()

    with replay_lock:
        if not replay_state['running']:
            return False, "没有运行的回放任务"

        # 等待线程结束
        if replay_state['thread']:
            replay_state['thread'].join(timeout=5)

        sent = replay_state['packets_sent']
        replay_state['running'] = False

    logger.info(f"报文回放已停止，发送 {sent} 个报文")
    return True, f"已停止，发送 {sent} 个报文"


def get_replay_status() -> Dict:
    """获取回放状态"""
    with replay_lock:
        return {
            'running': replay_state['running'],
            'packets_sent': replay_state['packets_sent'],
            'start_time': replay_state['start_time'],
            'current_file': replay_state['current_file'],
            'duration': time.time() - replay_state['start_time'] if replay_state['start_time'] else 0
        }


def replay_from_pcap(pcap_file: str, interface: str,
                    count: int = 1, interval: float = 0) -> Tuple[bool, str]:
    """便捷方法：从单个 PCAP 文件回放"""
    return start_replay([pcap_file], interface, count, interval)
