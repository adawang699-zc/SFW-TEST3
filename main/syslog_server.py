#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Syslog服务器模块
实现UDP syslog日志接收功能
"""

import socket
import threading
import logging
import time
from datetime import datetime
from collections import deque
import re

logger = logging.getLogger(__name__)

# 全局状态
syslog_server_state = {
    'running': False,
    'port': 5140,  # 默认使用非特权端口
    'logs': deque(maxlen=10000),  # 最多保存10000条日志
    'filter_ip': '',  # 过滤IP，空字符串表示不过滤
    'lock': threading.Lock(),
    'socket': None,
    'thread': None
}


def parse_syslog_message(data, client_address):
    """
    解析syslog消息

    Args:
        data: 接收到的原始数据
        client_address: 客户端地址 (ip, port)

    Returns:
        dict: 解析后的日志信息
    """
    try:
        # 解码数据
        if isinstance(data, bytes):
            message = data.decode('utf-8', errors='ignore').strip()
        else:
            message = str(data).strip()

        if not message:
            return None

        # 提取IP地址
        ip = client_address[0] if client_address else '0.0.0.0'

        # 尝试解析syslog格式
        # RFC 3164格式: <PRI>timestamp hostname tag: message
        # RFC 5424格式: <PRI>VERSION timestamp hostname app-name procid msgid structured-data msg

        # 解析优先级
        priority_match = re.match(r'<(\d+)>', message)
        if priority_match:
            priority = int(priority_match.group(1))
            facility = priority // 8
            severity = priority % 8
            message = message[priority_match.end():].strip()
        else:
            facility = 0
            severity = 6  # Informational

        # 解析时间戳（尝试多种格式）
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')

        # 尝试解析RFC 3164时间戳 (MMM DD HH:MM:SS)
        time_match = re.match(r'([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})', message)
        if time_match:
            try:
                month_str, day, hour, minute, second = time_match.groups()
                current_year = datetime.now().year
                # 简化的月份映射
                months = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                         'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
                month = months.get(month_str, datetime.now().month)
                timestamp = datetime(current_year, month, int(day), int(hour), int(minute), int(second))
                timestamp_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')
                message = message[time_match.end():].strip()
            except:
                pass

        # 尝试解析RFC 5424时间戳
        iso_match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)', message)
        if iso_match:
            try:
                timestamp_str = iso_match.group(1)
                message = message[iso_match.end():].strip()
            except:
                pass

        # 解析hostname和tag
        parts = message.split(':', 1)
        if len(parts) == 2:
            hostname_tag = parts[0].strip()
            log_message = parts[1].strip()
            # 尝试分离hostname和tag
            hostname_tag_parts = hostname_tag.split(None, 1)
            if len(hostname_tag_parts) >= 2:
                hostname = hostname_tag_parts[0]
                tag = hostname_tag_parts[1]
            else:
                hostname = hostname_tag_parts[0] if hostname_tag_parts else ''
                tag = ''
        else:
            hostname = ''
            tag = ''
            log_message = message

        return {
            'timestamp': timestamp_str,
            'ip': ip,
            'hostname': hostname,
            'facility': facility,
            'severity': severity,
            'tag': tag,
            'message': log_message,
            'raw': data.decode('utf-8', errors='ignore') if isinstance(data, bytes) else str(data)
        }
    except Exception as e:
        logger.error(f'解析syslog消息失败: {e}')
        return {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ip': client_address[0] if client_address else '0.0.0.0',
            'hostname': '',
            'facility': 0,
            'severity': 6,
            'tag': '',
            'message': data.decode('utf-8', errors='ignore') if isinstance(data, bytes) else str(data),
            'raw': data.decode('utf-8', errors='ignore') if isinstance(data, bytes) else str(data)
        }


def syslog_server_worker():
    """Syslog服务器工作线程"""
    global syslog_server_state

    try:
        # 创建UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('0.0.0.0', syslog_server_state['port']))
        sock.settimeout(1.0)  # 设置超时，便于检查运行状态

        syslog_server_state['socket'] = sock
        logger.info(f'Syslog服务器已启动，监听端口: {syslog_server_state["port"]}')

        while syslog_server_state['running']:
            try:
                # 接收数据
                data, addr = sock.recvfrom(8192)  # 最大8192字节

                # 解析消息
                log_entry = parse_syslog_message(data, addr)
                if not log_entry:
                    continue

                # IP过滤
                filter_ip = syslog_server_state.get('filter_ip', '').strip()
                if filter_ip and log_entry['ip'] != filter_ip:
                    continue

                # 添加到日志列表
                with syslog_server_state['lock']:
                    syslog_server_state['logs'].append(log_entry)

                logger.debug(f'收到syslog消息: {log_entry["ip"]} - {log_entry["message"][:50]}')

            except socket.timeout:
                # 超时是正常的，继续循环
                continue
            except Exception as e:
                if syslog_server_state['running']:
                    logger.error(f'接收syslog消息时出错: {e}')
                break

        sock.close()
        logger.info('Syslog服务器已停止')

    except Exception as e:
        logger.exception(f'Syslog服务器工作线程异常: {e}')
        syslog_server_state['running'] = False
    finally:
        syslog_server_state['socket'] = None
        syslog_server_state['thread'] = None


def start_syslog_server(port=5140):
    """
    启动syslog服务器

    Args:
        port: 监听端口，默认5140（非特权端口）
              注意：使用514等特权端口需要root权限

    Returns:
        tuple: (success: bool, message: str)
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        if syslog_server_state['running']:
            return False, 'Syslog服务器已在运行中'

        # 检查端口权限和可用性
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            test_sock.bind(('0.0.0.0', port))
            test_sock.close()
        except PermissionError:
            return False, f'端口 {port} 需要root权限（特权端口<1024），请使用非特权端口如5140'
        except OSError as e:
            if 'Permission denied' in str(e):
                return False, f'端口 {port} 需要root权限（特权端口<1024），请使用非特权端口如5140'
            return False, f'端口 {port} 已被占用: {str(e)}'

        syslog_server_state['port'] = port
        syslog_server_state['running'] = True

        # 启动工作线程
        thread = threading.Thread(target=syslog_server_worker, daemon=True)
        thread.start()
        syslog_server_state['thread'] = thread

        # 等待一下确保服务器启动
        time.sleep(0.5)

        return True, f'Syslog服务器已启动，监听端口: {port}'


def stop_syslog_server():
    """
    停止syslog服务器

    Returns:
        tuple: (success: bool, message: str)
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        if not syslog_server_state['running']:
            return False, 'Syslog服务器未运行'

        syslog_server_state['running'] = False

        # 关闭socket以中断recvfrom
        if syslog_server_state['socket']:
            try:
                syslog_server_state['socket'].close()
            except:
                pass

        # 等待线程结束
        if syslog_server_state['thread']:
            syslog_server_state['thread'].join(timeout=2)

        return True, 'Syslog服务器已停止'


def get_syslog_status():
    """
    获取syslog服务器状态

    Returns:
        dict: 状态信息
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        return {
            'running': syslog_server_state['running'],
            'port': syslog_server_state['port'],
            'filter_ip': syslog_server_state.get('filter_ip', ''),
            'log_count': len(syslog_server_state['logs'])
        }


def get_syslog_logs(limit=1000, filter_ip=''):
    """
    获取syslog日志

    Args:
        limit: 返回的日志条数
        filter_ip: 过滤IP，空字符串表示不过滤

    Returns:
        list: 日志列表
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        logs = list(syslog_server_state['logs'])

        # IP过滤
        if filter_ip and filter_ip.strip():
            logs = [log for log in logs if log['ip'] == filter_ip.strip()]

        # 返回最新的日志
        return logs[-limit:]


def clear_syslog_logs():
    """
    清空syslog日志

    Returns:
        tuple: (success: bool, message: str)
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        syslog_server_state['logs'].clear()
        return True, '日志已清空'


def set_syslog_filter_ip(filter_ip):
    """
    设置IP过滤

    Args:
        filter_ip: 要过滤的IP地址，空字符串表示不过滤

    Returns:
        tuple: (success: bool, message: str)
    """
    global syslog_server_state

    with syslog_server_state['lock']:
        syslog_server_state['filter_ip'] = filter_ip.strip()
        return True, f'IP过滤已设置为: {filter_ip if filter_ip.strip() else "不过滤"}'