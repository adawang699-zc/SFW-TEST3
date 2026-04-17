"""
设备监测后台任务模块
用于后台监测设备 coredump 文件和资源使用情况，并发送告警邮件
同时定时获取设备监控数据并存储到数据库
"""

import threading
import time
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Set

from django.utils import timezone

logger = logging.getLogger('main')

# 全局监测状态
monitor_tasks: Dict[str, Dict[str, Any]] = {}  # {device_id: {'enabled': bool, 'thread': Thread, 'last_files': set}}
monitor_lock = threading.Lock()

# 全局数据采集线程
data_collector_thread = None
data_collector_enabled = False

# 告警配置（从文件读取）
alert_config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'alert_config.json')
alert_config: Dict[str, Any] = {
    'smtp_server': 'smtp.example.com',
    'smtp_port': 587,
    'sender_email': '',
    'sender_password': '',
    'use_tls': True,
    'recipients': [],
    'check_interval': 300,  # 默认 5 分钟检查一次
    'cpu_threshold': 80,  # CPU 告警阈值（%）
    'memory_threshold': 80  # 内存告警阈值（%）
}


def check_alert_ignored(device_id: str, alert_type: str) -> bool:
    """
    检查设备告警是否已被忽略（忽略后一周内不发邮件）

    Args:
        device_id: 设备 ID
        alert_type: 告警类型 ('cpu', 'memory', 'coredump')

    Returns:
        bool: True 表示已忽略，不应发送邮件
    """
    try:
        from main.models import DeviceAlertStatus
        alert = DeviceAlertStatus.objects.filter(
            device_id=device_id,
            alert_type=alert_type,
            is_ignored=True,
            ignore_until__gt=timezone.now()
        ).first()
        return alert is not None
    except Exception as e:
        logger.error(f"检查告警忽略状态失败: {e}")
        return False


def create_or_update_alert(device_id: str, device_name: str, alert_type: str, alert_value: float) -> bool:
    """
    创建或更新告警记录

    Args:
        device_id: 设备 ID
        device_name: 设备名称
        alert_type: 告警类型
        alert_value: 告警值

    Returns:
        bool: True 表示是新告警或需要发送邮件
    """
    try:
        from main.models import DeviceAlertStatus

        existing_alert = DeviceAlertStatus.objects.filter(
            device_id=device_id,
            alert_type=alert_type,
            has_alert=True,
            is_ignored=False
        ).first()

        if existing_alert:
            existing_alert.alert_value = alert_value
            existing_alert.alert_time = timezone.now()
            existing_alert.save()

            if existing_alert.last_email_time:
                cooldown_end = existing_alert.last_email_time + timedelta(days=7)
                if timezone.now() < cooldown_end:
                    return False
            return True
        else:
            DeviceAlertStatus.objects.create(
                device_id=device_id,
                device_name=device_name,
                alert_type=alert_type,
                alert_value=alert_value,
                has_alert=True,
                is_ignored=False
            )
            return True

    except Exception as e:
        logger.error(f"创建告警记录失败: {e}")
        return True


def mark_email_sent(device_id: str, alert_type: str) -> None:
    """标记邮件已发送"""
    try:
        from main.models import DeviceAlertStatus
        DeviceAlertStatus.objects.filter(
            device_id=device_id,
            alert_type=alert_type,
            has_alert=True
        ).update(last_email_time=timezone.now(), email_sent=True)
    except Exception as e:
        logger.error(f"标记邮件发送状态失败: {e}")


def clear_alert(device_id: str, alert_type: str) -> None:
    """清除告警（资源恢复正常时）"""
    try:
        from main.models import DeviceAlertStatus
        DeviceAlertStatus.objects.filter(
            device_id=device_id,
            alert_type=alert_type,
            has_alert=True
        ).delete()
    except Exception as e:
        logger.error(f"清除告警失败: {e}")


def load_alert_config() -> None:
    """从文件加载告警配置"""
    global alert_config
    try:
        if os.path.exists(alert_config_file):
            with open(alert_config_file, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                alert_config.update(loaded_config)
                logger.info("告警配置加载成功")
        else:
            save_alert_config()
    except Exception as e:
        logger.error(f"加载告警配置失败: {e}")


def save_alert_config() -> bool:
    """保存告警配置到文件"""
    try:
        with open(alert_config_file, 'w', encoding='utf-8') as f:
            json.dump(alert_config, f, indent=2, ensure_ascii=False)
        logger.info("告警配置保存成功")
        return True
    except Exception as e:
        logger.error(f"保存告警配置失败: {e}")
        return False


def get_alert_config() -> Dict[str, Any]:
    """获取告警配置"""
    return alert_config.copy()


def update_alert_config(new_config: Dict[str, Any]) -> bool:
    """更新告警配置"""
    global alert_config
    # 如果新配置中密码为空或未提供，保留原有密码
    if not new_config.get('sender_password') or new_config.get('sender_password') == '***':
        if 'sender_password' in new_config:
            del new_config['sender_password']
    alert_config.update(new_config)
    return save_alert_config()


def monitor_device_worker(device_id: str, device_info: Dict[str, Any]) -> None:
    """
    设备监测工作线程

    Args:
        device_id: 设备 ID
        device_info: 设备信息字典，包含 name, ip, type, user, password 等
    """
    from main.device_utils import get_cpu_info, get_memory_info, get_coredump_files
    from main.email_utils import send_alert_email, format_alert_email_content

    last_files: Set[str] = set()

    while True:
        with monitor_lock:
            task = monitor_tasks.get(device_id)
            if not task or not task.get('enabled', False):
                logger.info(f"设备 {device_id} 监测已停止")
                break

        try:
            device_type = device_info.get('type', '')
            device_user = device_info.get('user', 'admin')
            device_password = device_info.get('password', '')
            backend_password = device_info.get('backend_password', '')

            # 获取当前系统资源信息
            cpu_info = get_cpu_info(
                device_info['ip'],
                device_user,
                device_password,
                device_type=device_type,
                backend_password=backend_password
            )
            memory_info = get_memory_info(
                device_info['ip'],
                device_user,
                device_password,
                device_type=device_type,
                backend_password=backend_password
            )

            resource_info = {
                'cpu_usage': cpu_info if isinstance(cpu_info, (int, float)) else 0,
                'memory_usage': memory_info.get('usage', 0) if isinstance(memory_info, dict) else 0,
                'memory_total': memory_info.get('total', 0) if isinstance(memory_info, dict) else 0,
                'memory_used': memory_info.get('used', 0) if isinstance(memory_info, dict) else 0,
                'memory_free': memory_info.get('free', 0) if isinstance(memory_info, dict) else 0
            }

            # 检查资源使用率告警
            cpu_usage = resource_info['cpu_usage']
            memory_usage = resource_info['memory_usage']

            cpu_threshold = alert_config.get('cpu_threshold', 80)
            memory_threshold = alert_config.get('memory_threshold', 80)

            # 检查 CPU 告警
            if cpu_usage > cpu_threshold:
                alert_type = 'cpu'
                if check_alert_ignored(device_id, alert_type):
                    logger.info(f"设备 {device_id} CPU 告警已被忽略")
                else:
                    should_notify = create_or_update_alert(
                        device_id, device_info.get('name', '未知'), alert_type, cpu_usage
                    )
                    if should_notify:
                        alert_details = {
                            'cpu_usage': cpu_usage,
                            'cpu_threshold': cpu_threshold,
                            'memory_usage': memory_usage,
                            'memory_total': resource_info['memory_total'],
                            'memory_used': resource_info['memory_used'],
                            'memory_free': resource_info['memory_free'],
                            'resource_info': resource_info
                        }
                        content = format_alert_email_content(device_info, 'resource', alert_details)
                        subject = f'[设备告警] {device_info.get("name", "未知设备")} - CPU使用率超过阈值({cpu_threshold}%)'
                        try:
                            if send_alert_email(alert_config, subject, content, alert_config.get('recipients', [])):
                                mark_email_sent(device_id, alert_type)
                                logger.info(f"设备 {device_id} CPU 告警邮件已发送")
                        except Exception as e:
                            logger.error(f"设备 {device_id} CPU 告警邮件发送失败: {e}")
            else:
                clear_alert(device_id, 'cpu')

            # 检查内存告警
            if memory_usage > memory_threshold:
                alert_type = 'memory'
                if check_alert_ignored(device_id, alert_type):
                    logger.info(f"设备 {device_id} 内存告警已被忽略")
                else:
                    should_notify = create_or_update_alert(
                        device_id, device_info.get('name', '未知'), alert_type, memory_usage
                    )
                    if should_notify:
                        alert_details = {
                            'cpu_usage': cpu_usage,
                            'memory_usage': memory_usage,
                            'memory_threshold': memory_threshold,
                            'memory_total': resource_info['memory_total'],
                            'memory_used': resource_info['memory_used'],
                            'memory_free': resource_info['memory_free'],
                            'resource_info': resource_info
                        }
                        content = format_alert_email_content(device_info, 'resource', alert_details)
                        subject = f'[设备告警] {device_info.get("name", "未知设备")} - 内存使用率超过阈值({memory_threshold}%)'
                        try:
                            if send_alert_email(alert_config, subject, content, alert_config.get('recipients', [])):
                                mark_email_sent(device_id, alert_type)
                                logger.info(f"设备 {device_id} 内存告警邮件已发送")
                        except Exception as e:
                            logger.error(f"设备 {device_id} 内存告警邮件发送失败: {e}")
            else:
                clear_alert(device_id, 'memory')

            # 检查 coredump 文件
            coredump_files = get_coredump_files(
                device_info['ip'],
                device_user,
                device_password,
                device_type=device_type,
                backend_password=backend_password
            )
            current_files = {f['name'] for f in coredump_files}

            new_files = current_files - last_files
            if new_files:
                alert_type = 'coredump'
                if check_alert_ignored(device_id, alert_type):
                    logger.info(f"设备 {device_id} Coredump 告警已被忽略")
                else:
                    new_file_list = [f for f in coredump_files if f['name'] in new_files]
                    alert_value = len(new_files)

                    should_notify = create_or_update_alert(
                        device_id, device_info.get('name', '未知'), alert_type, alert_value
                    )
                    if should_notify:
                        alert_details = {
                            'file_count': len(new_files),
                            'files': new_file_list,
                            'resource_info': resource_info
                        }
                        content = format_alert_email_content(device_info, 'coredump', alert_details)
                        subject = f'[设备告警] {device_info.get("name", "未知设备")} - 检测到新的Coredump文件'

                        try:
                            if send_alert_email(alert_config, subject, content, alert_config.get('recipients', [])):
                                mark_email_sent(device_id, alert_type)
                                logger.info(f"设备 {device_id} coredump 告警邮件已发送")
                        except Exception as e:
                            logger.error(f"设备 {device_id} coredump 告警邮件发送失败: {e}")

            last_files = current_files

        except Exception as e:
            logger.error(f"设备 {device_id} 监测出错: {e}")

        # 等待检查间隔
        check_interval = alert_config.get('check_interval', 300)
        time.sleep(check_interval)


def start_device_monitoring(device_id: str, device_info: Dict[str, Any]) -> None:
    """
    启动设备监测

    Args:
        device_id: 设备 ID
        device_info: 设备信息字典
    """
    with monitor_lock:
        if device_id in monitor_tasks:
            stop_device_monitoring(device_id)

        monitor_tasks[device_id] = {
            'enabled': True,
            'thread': None,
            'last_files': set()
        }

        thread = threading.Thread(
            target=monitor_device_worker,
            args=(device_id, device_info),
            daemon=True
        )
        thread.start()

        monitor_tasks[device_id]['thread'] = thread
        logger.info(f"设备 {device_id} 监测已启动")


def stop_device_monitoring(device_id: str) -> None:
    """
    停止设备监测

    Args:
        device_id: 设备 ID
    """
    with monitor_lock:
        if device_id in monitor_tasks:
            monitor_tasks[device_id]['enabled'] = False
            del monitor_tasks[device_id]
            logger.info(f"设备 {device_id} 监测已停止")


def is_device_monitoring(device_id: str) -> bool:
    """
    检查设备是否正在监测

    Args:
        device_id: 设备 ID

    Returns:
        bool: 是否正在监测
    """
    with monitor_lock:
        task = monitor_tasks.get(device_id)
        return task is not None and task.get('enabled', False)


def get_monitoring_status() -> Dict[str, bool]:
    """
    获取所有设备的监测状态

    Returns:
        dict: {device_id: enabled}
    """
    with monitor_lock:
        return {
            device_id: task.get('enabled', False)
            for device_id, task in monitor_tasks.items()
        }


# 初始化时加载配置
load_alert_config()


def collect_device_monitor_data(device: Any) -> None:
    """
    采集单个设备的监控数据并存储到数据库

    Args:
        device: TestDevice 模型实例
    """
    from main.models import DeviceMonitorData, TestDevice
    from main.device_utils import (
        get_cpu_info, get_memory_info, get_network_info, get_disk_info,
        test_ssh_connection, execute_in_backend, execute_in_vtysh
    )

    device_id = device.id
    device_name = device.name
    device_ip = device.ip
    device_type = device.type
    backend_password = device.backend_password or ''

    logger.info(f"开始采集设备 {device_name} ({device_ip}) 监控数据")

    # 测试 SSH 连接
    conn_result = test_ssh_connection(device_ip, device.user, device.password, device.port)
    if not conn_result['success']:
        # 设备离线，更新数据库状态
        DeviceMonitorData.objects.update_or_create(
            device_id=device_id,
            defaults={
                'device_name': device_name,
                'device_ip': device_ip,
                'is_online': False,
                'last_error': conn_result['message'],
            }
        )
        logger.warning(f"设备 {device_name} ({device_ip}) 离线: {conn_result['message']}")
        return

    # 获取硬件信息（只获取一次，如果设备表中为空）
    if not device.cpu_model or not device.hardware_model:
        try:
            # 使用 lscpu 获取 CPU 信息
            lscpu_result = execute_in_backend('lscpu', device_ip, device.user, device.password, backend_password, device_type, device.port)
            if lscpu_result:
                cpu_model = ''
                cpu_cores = 0
                for line in lscpu_result.strip().split('\n'):
                    line = line.strip()
                    if 'Model name:' in line:
                        cpu_model = line.split(':', 1)[-1].strip()
                    elif 'CPU(s):' in line and 'On-line' not in line:
                        try:
                            cpu_cores = int(line.split(':', 1)[-1].strip())
                        except:
                            pass

                # 使用 vtysh 获取硬件型号
                hwtype_result = execute_in_vtysh('show hwtype', device_ip, device.user, device.password, device.port)
                hardware_model = ''
                if hwtype_result:
                    for line in hwtype_result.strip().split('\n'):
                        line = line.strip()
                        if line and 'hwtype' not in line.lower() and 'show' not in line.lower():
                            hardware_model = line
                            break

                # 更新设备表中的硬件信息
                if cpu_model or hardware_model or cpu_cores > 0:
                    TestDevice.objects.filter(id=device_id).update(
                        cpu_model=cpu_model or device.cpu_model,
                        cpu_cores=cpu_cores or device.cpu_cores,
                        hardware_model=hardware_model or device.hardware_model
                    )
                    logger.info(f"设备 {device_name} 硬件信息已更新: CPU={cpu_model}, 核数={cpu_cores}, 硬件={hardware_model}")
        except Exception as e:
            logger.error(f"获取设备 {device_name} 硬件信息失败: {e}")

    # 获取 CPU 使用率
    cpu_usage = get_cpu_info(device_ip, device.user, device.password, device_type, backend_password, device.port)

    # 使用设备表中的 CPU 型号
    cpu_name = device.cpu_model or 'ARM/x86 Processor'

    # 获取内存信息
    mem_info = get_memory_info(device_ip, device.user, device.password, device_type, backend_password, device.port)

    # 获取网络信息
    net_info = get_network_info(device_ip, device.user, device.password, device_type, backend_password, device.port)

    # 获取磁盘信息
    disk_info = get_disk_info(device_ip, device.user, device.password, device_type, backend_password, device.port)

    # 存储到数据库
    DeviceMonitorData.objects.update_or_create(
        device_id=device_id,
        defaults={
            'device_name': device_name,
            'device_ip': device_ip,
            'cpu_usage': cpu_usage,
            'cpu_name': cpu_name,
            'memory_usage': mem_info.get('usage', 0),
            'memory_used': mem_info.get('used', 0),
            'memory_total': mem_info.get('total', 0),
            'disk_usage': disk_info.get('usage', 0),
            'disk_used': disk_info.get('used', 0),
            'disk_total': disk_info.get('total', 0),
            'rx_rate': net_info.get('rx_rate', 0),
            'tx_rate': net_info.get('tx_rate', 0),
            'is_online': True,
            'last_error': None,
        }
    )

    logger.info(f"设备 {device_name} 监控数据已更新: CPU={cpu_usage}%, MEM={mem_info.get('usage', 0)}%")


def data_collector_worker() -> None:
    """
    数据采集工作线程
    定时采集所有设备的监控数据并存储到数据库
    """
    from main.models import TestDevice

    logger.info("数据采集线程启动")

    while data_collector_enabled:
        try:
            # 获取所有设备
            devices = TestDevice.objects.all()

            for device in devices:
                try:
                    collect_device_monitor_data(device)
                except Exception as e:
                    logger.error(f"采集设备 {device.name} 数据失败: {e}")

                # 每个设备采集间隔 5 秒，避免并发过高
                time.sleep(5)

        except Exception as e:
            logger.error(f"数据采集循环出错: {e}")

        # 等待下一次采集周期（60秒）
        time.sleep(60)

    logger.info("数据采集线程停止")


def start_data_collector() -> None:
    """
    启动数据采集线程
    """
    global data_collector_thread, data_collector_enabled

    if data_collector_thread and data_collector_thread.is_alive():
        logger.warning("数据采集线程已运行")
        return

    data_collector_enabled = True
    data_collector_thread = threading.Thread(
        target=data_collector_worker,
        daemon=True
    )
    data_collector_thread.start()
    logger.info("数据采集线程已启动")


def stop_data_collector() -> None:
    """
    停止数据采集线程
    """
    global data_collector_enabled

    data_collector_enabled = False
    logger.info("数据采集线程已停止")


def is_data_collector_running() -> bool:
    """
    检查数据采集线程是否运行
    """
    return data_collector_enabled and (data_collector_thread and data_collector_thread.is_alive())


# 自动启动数据采集线程
start_data_collector()