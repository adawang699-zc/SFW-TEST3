#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP工具模块 - 使用 Net-SNMP 命令行工具
使用 snmpget/snmpwalk/snmptrapd 实现 SNMP 功能
"""

import subprocess
import logging
import threading
import json
import os
import re
from datetime import datetime
from typing import Tuple, Dict, List, Optional

logger = logging.getLogger(__name__)

# Trap 数据文件路径
TRAP_LOG_FILE = '/var/log/snmptraps.json'

# 全局 TRAP 接收状态（通过 snmptrapd 服务）
trap_receiver_state = {
    'running': False,
    'port': 162,
    'traps': [],
    'lock': threading.Lock()
}


def check_snmp_tools() -> Tuple[bool, str]:
    """
    检查 SNMP 命令行工具是否可用

    Returns:
        tuple: (available: bool, message: str)
    """
    tools = ['snmpget', 'snmpwalk', 'snmptrapd']
    missing = []

    for tool in tools:
        # snmptrapd 在 /usr/sbin/
        tool_paths = ['/usr/bin/' + tool, '/usr/sbin/' + tool]
        found = False
        for path in tool_paths:
            if os.path.exists(path):
                found = True
                break
        if not found:
            missing.append(tool)

    if missing:
        return False, f'SNMP 工具未安装: {", ".join(missing)}。请执行: apt install snmp snmpd'

    return True, 'SNMP 工具已安装'


def snmp_get(ip: str, oid: str, community: str = 'public', version: str = '2c',
             port: int = 161, security_username: str = '', security_level: str = 'noAuthNoPriv',
             auth_protocol: str = 'MD5', auth_password: str = '',
             priv_protocol: str = 'DES', priv_password: str = '') -> Tuple[bool, any]:
    """
    SNMP GET 操作（使用 snmpget 命令）

    Args:
        ip: 设备 IP 地址
        oid: OID 字符串，例如 '1.3.6.1.2.1.1.1.0'
        community: SNMP community（v1/v2c 使用）
        version: SNMP 版本 ('v1', 'v2c', 'v3')
        port: SNMP 端口，默认 161
        security_username: SNMPv3 安全用户名
        security_level: SNMPv3 安全级别 ('noAuthNoPriv', 'authNoPriv', 'authPriv')
        auth_protocol: SNMPv3 认证协议 ('MD5', 'SHA')
        auth_password: SNMPv3 认证密码
        priv_protocol: SNMPv3 加密协议 ('DES', 'AES')
        priv_password: SNMPv3 加密密码

    Returns:
        tuple: (success: bool, result: dict or error_message: str)
    """
    # 检查工具
    available, msg = check_snmp_tools()
    if not available:
        return False, msg

    # 验证参数
    if not ip or not oid:
        return False, 'IP 和 OID 不能为空'

    try:
        # 构建 snmpget 命令
        cmd = ['snmpget', '-v', version.lstrip('v')]  # v1, v2c, 3

        # 添加超时和重试参数
        cmd.extend(['-t', '5', '-r', '2'])

        if version in ('v1', 'v2c'):
            # V1/V2C: 使用 community
            cmd.extend(['-c', community])
        else:
            # V3: 配置安全参数
            cmd.extend(['-u', security_username or ''])
            cmd.extend(['-l', security_level])

            if security_level in ('authNoPriv', 'authPriv'):
                cmd.extend(['-a', auth_protocol])
                cmd.extend(['-A', auth_password or ''])

            if security_level == 'authPriv':
                cmd.extend(['-x', priv_protocol])
                cmd.extend(['-X', priv_password or ''])

        # 目标地址
        target = f'{ip}:{port}'
        cmd.append(target)
        cmd.append(oid)

        logger.debug(f'执行 snmpget: {cmd}')

        # 执行命令
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            return False, f'SNMP 错误: {error_msg}'

        # 解析输出
        # 格式: OID = TYPE: VALUE
        # 例如: SNMPv2-MIB::sysDescr.0 = STRING: "Linux ..."
        parsed = parse_snmp_output(result.stdout)
        return True, parsed

    except subprocess.TimeoutExpired:
        return False, 'SNMP 查询超时'
    except Exception as e:
        logger.exception(f'SNMP GET 失败: {e}')
        return False, f'SNMP GET 失败: {str(e)}'


def snmp_walk(ip: str, oid: str, community: str = 'public', version: str = '2c',
              port: int = 161, security_username: str = '', security_level: str = 'noAuthNoPriv',
              auth_protocol: str = 'MD5', auth_password: str = '',
              priv_protocol: str = 'DES', priv_password: str = '') -> Tuple[bool, any]:
    """
    SNMP WALK 操作（使用 snmpwalk 命令）

    Args:
        参数同 snmp_get

    Returns:
        tuple: (success: bool, result: list or error_message: str)
    """
    # 检查工具
    available, msg = check_snmp_tools()
    if not available:
        return False, msg

    # 验证参数
    if not ip or not oid:
        return False, 'IP 和 OID 不能为空'

    try:
        # 构建 snmpwalk 命令
        cmd = ['snmpwalk', '-v', version.lstrip('v')]

        # 添加超时和重试参数
        cmd.extend(['-t', '5', '-r', '2'])

        if version in ('v1', 'v2c'):
            cmd.extend(['-c', community])
        else:
            cmd.extend(['-u', security_username or ''])
            cmd.extend(['-l', security_level])

            if security_level in ('authNoPriv', 'authPriv'):
                cmd.extend(['-a', auth_protocol])
                cmd.extend(['-A', auth_password or ''])

            if security_level == 'authPriv':
                cmd.extend(['-x', priv_protocol])
                cmd.extend(['-X', priv_password or ''])

        target = f'{ip}:{port}'
        cmd.append(target)
        cmd.append(oid)

        logger.debug(f'执行 snmpwalk: {cmd}')

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            return False, f'SNMP 错误: {error_msg}'

        # 解析输出（多行）
        parsed = parse_snmp_output(result.stdout)
        return True, parsed

    except subprocess.TimeoutExpired:
        return False, 'SNMP WALK 超时'
    except Exception as e:
        logger.exception(f'SNMP WALK 失败: {e}')
        return False, f'SNMP WALK 失败: {str(e)}'


def parse_snmp_output(output: str) -> List[Dict]:
    """
    解析 snmpget/snmpwalk 输出

    格式: OID = TYPE: VALUE
    例如: SNMPv2-MIB::sysDescr.0 = STRING: "Linux ..."
    或: .1.3.6.1.2.1.1.1.0 = STRING: "Linux ..."

    Returns:
        list: [{oid, value, type}]
    """
    results = []

    for line in output.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # 匹配格式: OID = TYPE: VALUE 或 OID = TYPE "VALUE"
        match = re.match(r'^(.+?)\s*=\s*(\w+):\s*(.+)$', line)
        if not match:
            # 尝试其他格式: OID = TYPE "VALUE"
            match = re.match(r'^(.+?)\s*=\s*(\w+)\s+"(.+)"$', line)
            if not match:
                # 尝试: OID = VALUE (无类型)
                match = re.match(r'^(.+?)\s*=\s*(.+)$', line)
                if match:
                    results.append({
                        'oid': match.group(1).strip(),
                        'value': match.group(2).strip(),
                        'type': 'Unknown'
                    })
                continue

        oid = match.group(1).strip()
        type_str = match.group(2).strip()
        value = match.group(3).strip()

        # 清理值（去除引号等）
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]

        results.append({
            'oid': oid,
            'value': value,
            'type': type_str
        })

    return results


def start_trap_receiver(port: int = 162, security_username: str = '',
                        security_level: str = 'noAuthNoPriv',
                        auth_protocol: str = 'MD5', auth_password: str = '',
                        priv_protocol: str = 'DES', priv_password: str = '') -> Tuple[bool, str]:
    """
    启动 SNMPTRAP 接收器（使用 snmptrapd）

    Args:
        port: 监听端口，默认 162
        security_username: SNMPv3 安全用户名
        security_level: SNMPv3 安全级别
        auth_protocol: SNMPv3 认证协议
        auth_password: SNMPv3 认证密码
        priv_protocol: SNMPv3 加密协议
        priv_password: SNMPv3 加密密码

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    # 先检查是否运行中（不持有锁）
    if trap_receiver_state['running']:
        return False, 'SNMPTRAP 接收器已在运行中'

    # 检查工具（不持有锁）
    available, msg = check_snmp_tools()
    if not available:
        return False, msg

    # 检查端口权限并设置 setcap（不持有锁）
    if port < 1024:
        try:
            subprocess.run(['sudo', 'setcap', 'cap_net_bind_service=+ep', '/usr/sbin/snmptrapd'],
                           capture_output=True, check=False, timeout=5)
            logger.info('已设置 snmptrapd 端口绑定权限')
        except subprocess.TimeoutExpired:
            logger.warning('设置端口权限超时，可能需要手动执行')
        except Exception as e:
            logger.warning(f'设置端口权限失败: {e}')

    # 停止 systemd snmptrapd.socket（避免端口冲突）
    try:
        subprocess.run(['sudo', 'systemctl', 'stop', 'snmptrapd.socket'],
                       capture_output=True, check=False, timeout=5)
        subprocess.run(['sudo', 'systemctl', 'stop', 'snmptrapd.service'],
                       capture_output=True, check=False, timeout=5)
        logger.info('已停止 systemd snmptrapd 服务')
    except Exception as e:
        logger.warning(f'停止 systemd snmptrapd 失败: {e}')

    # 生成配置文件（不持有锁）
    config_content = generate_snmptrapd_config(port, security_username, security_level,
                                                auth_protocol, auth_password, priv_protocol, priv_password)

    config_file = '/tmp/snmptrapd_custom.conf'
    try:
        with open(config_file, 'w') as f:
            f.write(config_content)
    except Exception as e:
        return False, f'写入配置文件失败: {e}'

    # 确保 trap 日志文件存在且有正确权限
    try:
        if not os.path.exists(TRAP_LOG_FILE):
            # 创建文件并设置权限
            subprocess.run(['sudo', 'touch', TRAP_LOG_FILE],
                           capture_output=True, check=False, timeout=5)
        subprocess.run(['sudo', 'chmod', '666', TRAP_LOG_FILE],
                       capture_output=True, check=False, timeout=5)
        logger.info(f'已设置 {TRAP_LOG_FILE} 权限')
    except Exception as e:
        logger.warning(f'设置日志文件权限失败: {e}')

    # 启动 snmptrapd（不持有锁）
    # -Ln 不输出日志到文件，只通过 traphandle 处理
    cmd = ['snmptrapd', '-C', '-c', config_file, '-Ln', '-p', '/tmp/snmptrapd.pid']
    if port != 162:
        cmd.extend(['-n', str(port)])

    logger.info(f'启动 snmptrapd: {cmd}')

    try:
        # 直接启动（已通过 setcap 设置权限，不需要 sudo）
        subprocess.run(cmd, capture_output=True, check=False, timeout=5)

        # 等待启动
        import time
        time.sleep(1)

        # 检查是否启动成功
        result = subprocess.run(['pgrep', 'snmptrapd'], capture_output=True, timeout=5)

        # 更新状态（持有锁）
        with trap_receiver_state['lock']:
            if result.returncode == 0:
                trap_receiver_state['running'] = True
                trap_receiver_state['port'] = port
                return True, f'SNMPTRAP 接收器已启动，监听端口: {port}'
            else:
                return False, 'SNMPTRAP 接收器启动失败'

    except subprocess.TimeoutExpired:
        return False, '启动 snmptrapd 超时'
    except Exception as e:
        logger.exception(f'启动 snmptrapd 失败: {e}')
        return False, f'启动失败: {str(e)}'


def generate_snmptrapd_config(port: int, security_username: str,
                               security_level: str, auth_protocol: str,
                               auth_password: str, priv_protocol: str,
                               priv_password: str) -> str:
    """
    生成 snmptrapd 配置内容
    """
    config = []

    # 允许所有 trap（不验证 community）
    config.append('disableAuthorization yes')

    # V1/V2C 配置
    config.append('authCommunity log,execute,net public')
    config.append('authCommunity log,execute,net private')

    # V3 配置
    if security_username:
        if security_level == 'noAuthNoPriv':
            config.append(f'createUser {security_username}')
            config.append(f'authUser log {security_username} noAuthNoPriv')
        elif security_level == 'authNoPriv':
            config.append(f'createUser {security_username} {auth_protocol} "{auth_password}"')
            config.append(f'authUser log {security_username} authNoPriv')
        else:  # authPriv
            config.append(f'createUser {security_username} {auth_protocol} "{auth_password}" {priv_protocol} "{priv_password}"')
            config.append(f'authUser log {security_username} authPriv')

    # traphandle 处理脚本
    handler_script = '/opt/snmptrap_handler.py'
    config.append(f'traphandle default {handler_script}')

    return '\n'.join(config) + '\n'


def stop_trap_receiver() -> Tuple[bool, str]:
    """
    停止 SNMPTRAP 接收器

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    # 先检查是否运行中（不持有锁）
    if not trap_receiver_state['running']:
        return False, 'SNMPTRAP 接收器未运行'

    # 停止 snmptrapd（不持有锁，有超时）
    try:
        subprocess.run(['pkill', 'snmptrapd'], capture_output=True, check=False, timeout=5)
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        logger.warning(f'pkill snmptrapd 失败: {e}')

    # 更新状态（持有锁）
    with trap_receiver_state['lock']:
        trap_receiver_state['running'] = False
        return True, 'SNMPTRAP 接收器已停止'


def get_trap_receiver_status() -> Dict:
    """
    获取 SNMPTRAP 接收器状态

    Returns:
        dict: 状态信息
    """
    global trap_receiver_state

    # 检查 snmptrapd 进程是否运行（不持有锁，有超时）
    try:
        result = subprocess.run(['pgrep', 'snmptrapd'], capture_output=True, timeout=5)
        running = result.returncode == 0
    except subprocess.TimeoutExpired:
        running = False
    except Exception:
        running = False

    # 读取 trap 数量（不持有锁）
    traps = get_trap_receiver_traps()

    # 更新状态（持有锁）
    with trap_receiver_state['lock']:
        trap_receiver_state['running'] = running

        return {
            'running': trap_receiver_state['running'],
            'port': trap_receiver_state['port'],
            'trap_count': len(traps)
        }


def get_trap_receiver_traps(limit: int = 1000) -> List[Dict]:
    """
    获取接收到的 TRAP 列表

    Args:
        limit: 返回的 TRAP 条数

    Returns:
        list: TRAP 列表
    """
    global trap_receiver_state

    traps = []

    # 从 JSON 文件读取
    if os.path.exists(TRAP_LOG_FILE):
        try:
            with open(TRAP_LOG_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            trap = json.loads(line)
                            traps.append(trap)
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f'读取 TRAP 文件失败: {e}')

    with trap_receiver_state['lock']:
        trap_receiver_state['traps'] = traps

    return traps[-limit:]


def clear_trap_receiver_traps() -> Tuple[bool, str]:
    """
    清空 TRAP 列表

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    # 清空文件
    if os.path.exists(TRAP_LOG_FILE):
        try:
            with open(TRAP_LOG_FILE, 'w') as f:
                f.write('')
        except Exception as e:
            return False, f'清空文件失败: {e}'

    with trap_receiver_state['lock']:
        trap_receiver_state['traps'] = []

    return True, 'TRAP 列表已清空'