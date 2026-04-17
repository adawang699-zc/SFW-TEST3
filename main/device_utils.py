"""
设备管理工具模块
提供 SSH 连接、命令执行、资源监控等功能
"""

import paramiko
import time
import logging
import socket
import os
import re
from typing import Optional, Dict, List, Any, Union

logger = logging.getLogger('main')

# 默认 SSH 凭据
DEFAULT_USER = 'admin'
DEFAULT_PASSWORD = ''

# 默认后台密码（根据设备类型）
DEFAULT_BACKEND_PASSWORD_FIREWALL = 'tdhx@2017'
DEFAULT_BACKEND_PASSWORD_AUDIT = 'tdhx@2017'
DEFAULT_BACKEND_PASSWORD_OTHER = 'tdhx@2017'


def get_backend_password(device_type: Optional[str] = None, custom_password: Optional[str] = None) -> Optional[str]:
    """
    获取后台密码

    Args:
        device_type: 设备类型 ('ic_firewall', 'ic_audit', 'ids', 'other')
        custom_password: 自定义密码（优先使用）

    Returns:
        后台密码
    """
    # 优先使用自定义密码
    if custom_password:
        return custom_password

    # 根据设备类型选择默认密码
    if device_type == 'ic_firewall':
        return DEFAULT_BACKEND_PASSWORD_FIREWALL
    elif device_type in ('ic_audit', 'ids'):
        return DEFAULT_BACKEND_PASSWORD_AUDIT
    else:
        return DEFAULT_BACKEND_PASSWORD_OTHER


def execute_ssh_command(
    cmd: str,
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    port: int = 22,
    timeout: int = 10
) -> Union[str, bool]:
    """
    执行 SSH 命令（简单模式）

    Args:
        cmd: 要执行的命令
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        port: SSH 端口
        timeout: 超时时间

    Returns:
        命令输出或 False（失败时）
    """
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, user, password, timeout=timeout)

        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
        output = stdout.read().decode('utf-8', errors='ignore')
        error = stderr.read().decode('utf-8', errors='ignore')

        ssh.close()

        if error and not output:
            logger.error(f"SSH 命令执行出错: {error}")
            return False

        return output.strip() if output else False

    except paramiko.AuthenticationException as err:
        logger.error(f"SSH 认证失败: {host}:{port}@{user}, 错误: {err}")
        return False
    except paramiko.SSHException as err:
        logger.error(f"SSH 异常: {host}:{port}, 错误: {err}")
        return False
    except socket.timeout:
        logger.error(f"SSH 连接超时: {host}:{port}")
        return False
    except Exception as e:
        logger.error(f"执行命令失败: {host}:{port}, 命令: {cmd}, 错误: {e}")
        return False


def execute_in_vtysh(
    cmds: Union[str, List[str]],
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    port: int = 22
) -> Union[str, bool]:
    """
    在 vtysh 中执行命令（FRR/Quagga 路由命令）

    Args:
        cmds: 命令（字符串或列表）
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        port: SSH 端口

    Returns:
        命令输出或 False（失败时）
    """
    if isinstance(cmds, (list, tuple)):
        cmd = '\n'.join(cmds)
    else:
        cmd = cmds

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, user, password, timeout=30)

        chan = ssh.invoke_shell()
        chan.settimeout(60)

        # 进入 vtysh
        chan.send('vtysh\n')
        time.sleep(0.5)

        # 清空初始输出
        if chan.recv_ready():
            chan.recv(4096)

        # 执行命令
        chan.send(cmd + '\n')
        time.sleep(0.5)

        output = ''
        while chan.recv_ready():
            data = chan.recv(4096).decode('utf-8', errors='ignore')
            output += data

            # 处理分页提示
            if '--More--' in output:
                chan.send(' ')
                time.sleep(0.3)

        # 退出 vtysh
        chan.send('exit\n')
        time.sleep(0.3)

        ssh.close()

        # 清理输出
        lines = output.split('\n')
        cleaned_lines = []
        for line in lines:
            line_stripped = line.strip()
            # 跳过提示符和空行
            if line_stripped and not line_stripped.endswith('#') and not line_stripped.endswith('>'):
                if 'vtysh' not in line_stripped.lower() and cmd not in line_stripped:
                    cleaned_lines.append(line)

        return '\n'.join(cleaned_lines).strip()

    except Exception as e:
        logger.error(f"vtysh 命令执行失败: {host}:{port}, 错误: {e}")
        return False


def execute_in_backend(
    cmds: Union[str, List[str]],
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    backend_password: Optional[str] = None,
    device_type: Optional[str] = None,
    port: int = 22
) -> Union[str, bool]:
    """
    进入后台执行命令（输入 enter，然后输入密码获取 root 权限）

    Args:
        cmds: 命令（字符串或列表）
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        backend_password: 后台 root 密码（如果为 None，根据 device_type 自动选择）
        device_type: 设备类型
        port: SSH 端口

    Returns:
        命令输出或 False（失败时）
    """
    # 如果是列表，合并为单个命令
    if isinstance(cmds, (list, tuple)):
        cmd = ' && '.join(cmds)
    else:
        cmd = cmds

    # 获取后台密码
    backend_pwd = get_backend_password(device_type, backend_password)

    if not backend_pwd:
        logger.error(f"后台密码未配置，设备类型: {device_type}")
        return False

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, user, password, timeout=10)

        chan = ssh.invoke_shell()
        chan.settimeout(30)

        # 清空初始输出
        time.sleep(0.1)
        if chan.recv_ready():
            chan.recv(4096)

        # 输入 enter 进入后台
        chan.send('enter\n')
        time.sleep(0.3)
        if chan.recv_ready():
            chan.recv(4096)

        # 输入密码获取 root 权限
        chan.send(backend_pwd + '\n')
        time.sleep(0.3)
        if chan.recv_ready():
            chan.recv(4096)

        # 发送要执行的命令
        chan.send(cmd + '\n')
        time.sleep(0.5)

        # 读取输出
        output = ''
        max_wait = 15
        start_time = time.time()

        while time.time() - start_time < max_wait:
            if chan.recv_ready():
                data = chan.recv(4096).decode('utf-8', errors='ignore')
                output += data
                # 如果出现提示符，说明命令执行完成
                if output.count('\n') > 1:
                    if any(p in output[-20:] for p in ['# ', '$ ', '\n#', '\n$']):
                        time.sleep(0.2)
                        if chan.recv_ready():
                            output += chan.recv(4096).decode('utf-8', errors='ignore')
                        break
            else:
                time.sleep(0.1)

        ssh.close()

        # 检查错误
        if '% Command incomplete.' in output:
            logger.warning(f"命令执行被中断: {cmd}")
            return False

        # 清理输出
        lines = output.split('\n')
        cleaned_lines = []
        skip_until_command = True

        for line in lines:
            line_stripped = line.strip()

            if skip_until_command:
                if 'enter' in line_stripped.lower() or 'Password:' in line_stripped:
                    continue
                if cmd.strip() in line or (len(cmd) > 20 and cmd[:20] in line):
                    skip_until_command = False
                    continue

            if line_stripped:
                if line_stripped in ['#', '$', '>']:
                    continue
                if 'Command incomplete' in line_stripped:
                    continue
                cleaned_lines.append(line)

        result = '\n'.join(cleaned_lines).strip()
        return result if result else False

    except Exception as e:
        logger.error(f"后台命令执行失败: {host}:{port}, 错误: {e}")
        return False


def get_cpu_info(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    device_type: Optional[str] = None,
    backend_password: Optional[str] = None,
    port: int = 22
) -> float:
    """
    获取 CPU 使用率

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        device_type: 设备类型
        backend_password: 后台密码
        port: SSH 端口

    Returns:
        CPU 使用率百分比
    """
    cpu_usage = 0.0

    try:
        # 获取 CPU 使用率（top 命令）
        cmd = "top -n1 | grep 'Cpu(s)' | awk '{print 100 - $8}'"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        if result:
            lines = result.strip().split('\n')
            for line in reversed(lines):
                line = line.strip()
                if 'root@' in line or line.startswith('#'):
                    continue
                # 提取数字
                cleaned = ''.join(c if c.isdigit() or c == '.' else '' for c in line)
                if cleaned:
                    try:
                        cpu_value = float(cleaned)
                        if 0 <= cpu_value <= 100:
                            cpu_usage = round(cpu_value, 2)
                            logger.info(f"获取 CPU 使用率: {cpu_usage:.2f}%")
                            break
                    except ValueError:
                        continue

        return cpu_usage

    except Exception as e:
        logger.error(f"获取 CPU 信息失败: {e}")
        return 0.0


def get_memory_info(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    device_type: Optional[str] = None,
    backend_password: Optional[str] = None,
    port: int = 22
) -> Dict[str, int]:
    """
    获取内存使用信息

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        device_type: 设备类型
        backend_password: 后台密码
        port: SSH 端口

    Returns:
        dict: {'total': 总内存(MB), 'used': 已用内存(MB), 'free': 空闲内存(MB), 'usage': 使用率(%)}
    """
    try:
        cmd = "free -m"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        if result:
            lines = result.strip().split('\n')
            total = 0
            used = 0
            free = 0

            for line in lines:
                if 'Mem:' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if part == 'Mem:':
                            if i + 1 < len(parts):
                                total = int(parts[i + 1])
                            if i + 2 < len(parts):
                                used = int(parts[i + 2])
                            break
                    if total > 0:
                        break

            # 解析 -/+ buffers/cache 行
            buffers_cache_used = 0
            buffers_cache_free = 0
            for line in lines:
                if 'buffers/cache' in line or '-/+ buffers' in line:
                    parts = line.split()
                    for i, part in enumerate(parts):
                        if 'buffers' in part or '-/+':
                            if i + 2 < len(parts):
                                buffers_cache_used = int(parts[i + 1])
                                buffers_cache_free = int(parts[i + 2])
                            break

            if total > 0:
                real_used = used - buffers_cache_used if buffers_cache_used > 0 else used
                real_free = buffers_cache_free if buffers_cache_free > 0 else free
                usage = round((real_used / total) * 100, 2) if total > 0 else 0

                logger.info(f"内存: total={total}MB, used={real_used}MB, usage={usage}%")
                return {
                    'total': total,
                    'used': real_used,
                    'free': real_free,
                    'usage': usage
                }

    except Exception as e:
        logger.error(f"获取内存信息失败: {e}")

    return {'total': 0, 'used': 0, 'free': 0, 'usage': 0}


# 存储上次网络统计信息（用于计算速率）
_network_cache: Dict[str, Dict[str, Any]] = {}


def get_network_info(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    device_type: Optional[str] = None,
    backend_password: Optional[str] = None,
    port: int = 22
) -> Dict[str, int]:
    """
    获取网络流量信息

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        device_type: 设备类型
        backend_password: 后台密码
        port: SSH 端口

    Returns:
        dict: {'rx_bytes': 接收字节数, 'tx_bytes': 发送字节数, 'rx_rate': 接收速率, 'tx_rate': 发送速率}
    """
    try:
        cmd = "cat /proc/net/dev | grep -E 'eth|ens|enp|agl0|ext' | awk '{rx+=$2; tx+=$10} END {print rx, tx}'"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        if result:
            lines = result.strip().split('\n')
            rx_bytes = None
            tx_bytes = None

            for line in reversed(lines):
                line = line.strip()
                cleaned = ''.join(c if c.isdigit() or c.isspace() else ' ' for c in line)
                parts = cleaned.strip().split()
                if len(parts) >= 2:
                    try:
                        rx_bytes = int(parts[0])
                        tx_bytes = int(parts[1])
                        break
                    except ValueError:
                        continue

            if rx_bytes is not None and tx_bytes is not None:
                # 计算速率
                cache_key = f"{host}_{user}"
                rx_rate = 0
                tx_rate = 0

                if cache_key in _network_cache:
                    last_data = _network_cache[cache_key]
                    time_diff = time.time() - last_data['timestamp']
                    if time_diff > 0:
                        rx_rate = int((rx_bytes - last_data['rx_bytes']) / time_diff)
                        tx_rate = int((tx_bytes - last_data['tx_bytes']) / time_diff)

                _network_cache[cache_key] = {
                    'rx_bytes': rx_bytes,
                    'tx_bytes': tx_bytes,
                    'timestamp': time.time()
                }

                logger.info(f"网络: rx={rx_bytes}, tx={tx_bytes}, rx_rate={rx_rate}/s")
                return {
                    'rx_bytes': rx_bytes,
                    'tx_bytes': tx_bytes,
                    'rx_rate': max(0, rx_rate),
                    'tx_rate': max(0, tx_rate)
                }

    except Exception as e:
        logger.error(f"获取网络信息失败: {e}")

    return {'rx_bytes': 0, 'tx_bytes': 0, 'rx_rate': 0, 'tx_rate': 0}


def get_disk_info(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    device_type: Optional[str] = None,
    backend_password: Optional[str] = None,
    port: int = 22
) -> Dict[str, float]:
    """
    获取磁盘使用信息（包含 /data 目录）

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        device_type: 设备类型
        backend_password: 后台密码
        port: SSH 端口

    Returns:
        dict: 磁盘使用信息
    """
    try:
        # 获取所有物理磁盘使用情况
        cmd = "df -BG | grep '^/dev/' | awk '{print $1, $2, $3, $4, $5}'"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        total_gb = 0.0
        used_gb = 0.0
        free_gb = 0.0

        if result:
            lines = result.strip().split('\n')
            seen_devices = set()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 5:
                    device_name = parts[0]
                    try:
                        size_gb = float(parts[1].replace('G', ''))
                        used_val = float(parts[2].replace('G', ''))
                        avail_gb = float(parts[3].replace('G', ''))

                        if device_name not in seen_devices:
                            seen_devices.add(device_name)
                            total_gb += size_gb
                            used_gb += used_val
                            free_gb += avail_gb
                    except (ValueError, IndexError):
                        continue

        usage = round((used_gb / total_gb) * 100, 1) if total_gb > 0 else 0.0

        # 获取 /data 目录使用情况
        data_total_gb = 0.0
        data_used_gb = 0.0
        data_usage = 0.0

        cmd = "df -BG /data 2>/dev/null | tail -1"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        if result:
            parts = result.strip().split()
            if len(parts) >= 5:
                try:
                    data_total_gb = float(parts[1].replace('G', ''))
                    data_used_gb = float(parts[2].replace('G', ''))
                    data_usage = float(parts[4].replace('%', ''))
                except (ValueError, IndexError):
                    pass

        data_percent_of_total = round((data_used_gb / total_gb) * 100, 1) if total_gb > 0 and data_used_gb > 0 else 0.0

        logger.info(f"磁盘: total={total_gb}GB, used={used_gb}GB, data_used={data_used_gb}GB")

        return {
            'total': round(total_gb, 1),
            'used': round(used_gb, 1),
            'free': round(free_gb, 1),
            'usage': usage,
            'data_total': round(data_total_gb, 1),
            'data_used': round(data_used_gb, 1),
            'data_usage': round(data_usage, 1),
            'data_percent_of_total': data_percent_of_total
        }

    except Exception as e:
        logger.error(f"获取磁盘信息失败: {e}")

    return {
        'total': 0, 'used': 0, 'free': 0, 'usage': 0.0,
        'data_total': 0, 'data_used': 0, 'data_usage': 0.0, 'data_percent_of_total': 0.0
    }


def get_coredump_files(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    coredump_dir: str = '/data/coredump',
    device_type: Optional[str] = None,
    backend_password: Optional[str] = None,
    port: int = 22
) -> List[Dict[str, Any]]:
    """
    获取 coredump 文件列表

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        coredump_dir: coredump 目录
        device_type: 设备类型
        backend_password: 后台密码
        port: SSH 端口

    Returns:
        文件列表 [{'name': 文件名, 'size': 大小, 'time': 时间}]
    """
    files = []

    try:
        cmd = f"ls -lh {coredump_dir} 2>/dev/null | grep -E '\\.core|coredump'"
        result = execute_in_backend(cmd, host, user, password, backend_password, device_type, port)

        if result:
            lines = result.strip().split('\n')
            for line in lines:
                parts = line.split()
                if len(parts) >= 9:
                    try:
                        name = parts[-1]
                        size = parts[4]
                        time_str = f"{parts[5]} {parts[6]} {parts[7]}"

                        files.append({
                            'name': name,
                            'size': size,
                            'time': time_str
                        })
                    except IndexError:
                        continue

        logger.info(f"获取 coredump 文件: {len(files)} 个")

    except Exception as e:
        logger.error(f"获取 coredump 文件失败: {e}")

    return files


def test_ssh_connection(
    host: str,
    user: str = DEFAULT_USER,
    password: str = DEFAULT_PASSWORD,
    port: int = 22,
    timeout: int = 5
) -> Dict[str, Any]:
    """
    测试 SSH 连接

    Args:
        host: 主机地址
        user: SSH 用户名
        password: SSH 密码
        port: SSH 端口
        timeout: 超时时间

    Returns:
        dict: {'success': 是否成功, 'message': 消息}
    """
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port, user, password, timeout=timeout)
        ssh.close()

        return {'success': True, 'message': 'SSH 连接成功'}

    except paramiko.AuthenticationException:
        return {'success': False, 'message': 'SSH 认证失败，请检查用户名和密码'}
    except paramiko.SSHException as e:
        return {'success': False, 'message': f'SSH 异常: {e}'}
    except socket.timeout:
        return {'success': False, 'message': 'SSH 连接超时'}
    except Exception as e:
        return {'success': False, 'message': f'连接失败: {e}'}