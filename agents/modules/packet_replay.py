#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
报文回放模块 - 使用 tcpreplay
支持速率控制、回放次数、实时统计
"""

import subprocess
import threading
import time
import re
import os
import logging
from typing import Tuple, Dict, List, Optional

logger = logging.getLogger(__name__)

# 全局状态
replay_state = {
    'running': False,
    'process': None,
    'packets_sent': 0,
    'packets_total': 0,
    'current_file': None,
    'start_time': None,
    'rate': 0,  # 当前速率 (pps)
    'bps': 0,   # 当前速率 (bps)
    'error': None
}
replay_lock = threading.Lock()
stop_replay_event = threading.Event()

# PCAP 默认目录
DEFAULT_PCAP_DIR = '/opt/pcap'


def get_pcap_files(directory: str = DEFAULT_PCAP_DIR, search: str = '') -> Dict:
    """
    获取 PCAP 文件列表

    Args:
        directory: 目录路径
        search: 搜索关键词

    Returns:
        文件列表和目录信息
    """
    try:
        # 确保目录存在
        if not os.path.exists(directory):
            return {
                'success': False,
                'error': f'目录不存在: {directory}',
                'directory': directory
            }

        files = []
        directories = []

        for entry in os.listdir(directory):
            full_path = os.path.join(directory, entry)

            # 搜索过滤
            if search and search.lower() not in entry.lower():
                continue

            if os.path.isfile(full_path):
                # 检查是否是 pcap 文件
                if entry.lower().endswith(('.pcap', '.pcapng', '.cap')):
                    size = os.path.getsize(full_path)
                    files.append({
                        'name': entry,
                        'path': full_path,
                        'size': size,
                        'size_str': format_size(size)
                    })
            elif os.path.isdir(full_path):
                directories.append({
                    'name': entry,
                    'path': full_path,
                    'type': 'directory'
                })

        # 排序：目录在前，文件按名称排序
        directories.sort(key=lambda x: x['name'].lower())
        files.sort(key=lambda x: x['name'].lower())

        # 获取上级目录
        parent_dir = os.path.dirname(directory) if directory != '/' else None

        return {
            'success': True,
            'directory': directory,
            'parent': parent_dir,
            'directories': directories,
            'files': files,
            'total_files': len(files),
            'total_dirs': len(directories)
        }

    except PermissionError:
        return {
            'success': False,
            'error': '无权限访问该目录',
            'directory': directory
        }
    except Exception as e:
        logger.error(f'获取文件列表失败: {e}')
        return {
            'success': False,
            'error': str(e),
            'directory': directory
        }


def format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f'{size} B'
    elif size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    elif size < 1024 * 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} MB'
    else:
        return f'{size / (1024 * 1024 * 1024):.2f} GB'


def get_pcap_info(pcap_file: str) -> Dict:
    """
    获取 PCAP 文件信息（使用 capinfos）

    Returns:
        文件信息：报文数量、大小、时长等
    """
    try:
        cmd = ['capinfos', pcap_file]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        info = {
            'path': pcap_file,
            'packets': 0,
            'bytes': 0,
            'duration': 0
        }

        # 解析 capinfos 输出
        for line in result.stdout.split('\n'):
            if 'Number of packets:' in line:
                info['packets'] = int(line.split(':')[1].strip())
            elif 'File size:' in line:
                match = re.search(r'(\d+)', line.split(':')[1])
                if match:
                    info['bytes'] = int(match.group(1))
            elif 'Capture duration:' in line:
                match = re.search(r'(\d+\.?\d*)', line.split(':')[1])
                if match:
                    info['duration'] = float(match.group(1))

        return {'success': True, 'info': info}

    except Exception as e:
        logger.error(f'获取 PCAP 信息失败: {e}')
        return {'success': False, 'error': str(e)}


def start_replay_tcpreplay(pcap_files: List[str], interface: str,
                           loop: int = 1, rate: float = None,
                           rate_bps: str = None, multiplier: float = None,
                           preload: int = None) -> Tuple[bool, str]:
    """
    使用 tcpreplay 开始报文回放

    Args:
        pcap_files: PCAP 文件列表
        interface: 发送接口
        loop: 回放次数（0 = 无限循环）
        rate: 速率（每秒报文数，pps）
        rate_bps: 速率（每秒比特数，如 '10Mbps'）
        multiplier: 速率倍数（相对于原始速率）
        preload: 预加载报文数

    Returns:
        (成功标志，消息)
    """
    with replay_lock:
        if replay_state['running']:
            return False, "已有回放任务在运行"

        # 重置状态
        replay_state['running'] = False
        replay_state['packets_sent'] = 0
        replay_state['packets_total'] = 0
        replay_state['rate'] = 0
        replay_state['bps'] = 0
        replay_state['error'] = None
        replay_state['current_file'] = None

    stop_replay_event.clear()

    # 获取总报文数
    total_packets = 0
    for pcap_file in pcap_files:
        info = get_pcap_info(pcap_file)
        if info.get('success'):
            total_packets += info['info'].get('packets', 0)

    with replay_lock:
        replay_state['packets_total'] = total_packets * loop if loop > 0 else total_packets

    def replay_thread_func():
        for file_idx, pcap_file in enumerate(pcap_files):
            if stop_replay_event.is_set():
                break

            with replay_lock:
                replay_state['current_file'] = pcap_file
                replay_state['running'] = True
                replay_state['start_time'] = time.time()

            # 构建 tcpreplay 命令
            cmd = ['sudo', 'tcpreplay', '-i', interface]

            # 回放次数
            if loop > 0:
                cmd.extend(['--loop', str(loop)])

            # 速率控制
            if rate:
                cmd.extend(['--pps', str(rate)])
            elif rate_bps:
                cmd.extend(['--mbps', rate_bps.replace('Mbps', '').replace('Mbps', '')])
            elif multiplier:
                cmd.extend(['--multiplier', str(multiplier)])

            # 预加载
            if preload:
                cmd.extend(['--preload-packets', str(preload)])

            # 统计输出间隔（每秒输出一次）
            cmd.extend(['--stats', '1'])

            # 文件路径放在最后
            cmd.append(pcap_file)

            logger.info(f'执行 tcpreplay: {cmd}')

            try:
                # 启动 tcpreplay
                process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1
                )

                with replay_lock:
                    replay_state['process'] = process

                # 实时读取输出
                while True:
                    if stop_replay_event.is_set():
                        process.terminate()
                        logger.info('回放被用户中断')
                        break

                    line = process.stdout.readline()
                    if not line and process.poll() is not None:
                        break

                    line = line.strip()

                    # 解析统计信息
                    # tcpreplay 输出格式:
                    # Actual: 1000 packets (64000 bytes) sent in 1.00 seconds
                    # Rated: 64000.00 Bps, 512.00 Mbps, 1000.00 pps
                    if 'packets' in line and 'sent' in line:
                        match = re.search(r'(\d+)\s+packets', line)
                        if match:
                            with replay_lock:
                                replay_state['packets_sent'] += int(match.group(1))

                    if 'Rated:' in line:
                        match_bps = re.search(r'(\d+\.?\d*)\s+Bps', line)
                        match_mbps = re.search(r'(\d+\.?\d*)\s+Mbps', line)
                        match_pps = re.search(r'(\d+\.?\d*)\s+pps', line)

                        with replay_lock:
                            if match_pps:
                                replay_state['rate'] = float(match_pps.group(1))
                            if match_mbps:
                                replay_state['bps'] = float(match_mbps.group(1)) * 1000000
                            elif match_bps:
                                replay_state['bps'] = float(match_bps.group(1))

                process.wait()

                if process.returncode != 0 and not stop_replay_event.is_set():
                    stderr = process.stderr.read()
                    with replay_lock:
                        replay_state['error'] = stderr.strip()
                    logger.error(f'tcpreplay 失败: {stderr}')

            except Exception as e:
                logger.error(f'回放文件失败: {pcap_file}: {e}')
                with replay_lock:
                    replay_state['error'] = str(e)

        with replay_lock:
            replay_state['running'] = False
            replay_state['process'] = None
            if not stop_replay_event.is_set():
                replay_state['current_file'] = None

        logger.info(f"报文回放完成，发送 {replay_state['packets_sent']} 个报文")

    thread = threading.Thread(target=replay_thread_func, daemon=True)
    thread.start()

    logger.info(f"开始报文回放：{len(pcap_files)} files, interface={interface}, loop={loop}")
    return True, f"报文回放已启动，共 {total_packets} 个报文"


def stop_replay() -> Tuple[bool, str]:
    """停止报文回放"""
    stop_replay_event.set()

    with replay_lock:
        if not replay_state['running']:
            stop_replay_event.clear()
            return False, "没有运行的回放任务"

        # 终止 tcpreplay 进程
        if replay_state['process']:
            try:
                replay_state['process'].terminate()
                replay_state['process'].wait(timeout=5)
            except:
                try:
                    replay_state['process'].kill()
                except:
                    pass

        sent = replay_state['packets_sent']
        replay_state['running'] = False
        replay_state['process'] = None

    logger.info(f"报文回放已停止，发送 {sent} 个报文")
    return True, f"已停止，发送 {sent} 个报文"


def get_replay_status() -> Dict:
    """获取回放状态"""
    with replay_lock:
        duration = 0
        if replay_state['start_time']:
            duration = time.time() - replay_state['start_time']

        return {
            'running': replay_state['running'],
            'packets_sent': replay_state['packets_sent'],
            'packets_total': replay_state['packets_total'],
            'current_file': replay_state['current_file'],
            'rate_pps': replay_state['rate'],
            'rate_bps': replay_state['bps'],
            'rate_mbps': replay_state['bps'] / 1000000 if replay_state['bps'] else 0,
            'duration': round(duration, 2),
            'error': replay_state['error'],
            'progress': (replay_state['packets_sent'] / replay_state['packets_total'] * 100) if replay_state['packets_total'] > 0 else 0
        }


# 兼容旧接口
def start_replay(pcap_files: List[str], interface: str,
                count: int = 1, interval: float = 0,
                use_layer2: bool = True) -> Tuple[bool, str]:
    """兼容旧接口的回放函数"""
    return start_replay_tcpreplay(pcap_files, interface, loop=count)


def replay_from_pcap(pcap_file: str, interface: str,
                    count: int = 1, interval: float = 0) -> Tuple[bool, str]:
    """便捷方法：从单个 PCAP 文件回放"""
    return start_replay([pcap_file], interface, count, interval)