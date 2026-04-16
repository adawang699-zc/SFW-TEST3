#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP工具模块
实现SNMP GET和SNMPTRAP接收功能
支持v1, v2c, v3版本
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    # pysnmp 7.x版本使用v1arch和v3arch子模块，函数名使用下划线
    from pysnmp.hlapi.v1arch import (
        get_cmd as v1_get_cmd, next_cmd as v1_next_cmd,
        CommunityData, UdpTransportTarget,
        ObjectType, ObjectIdentity
    )
    from pysnmp.hlapi.v3arch import (
        get_cmd as v3_get_cmd, next_cmd as v3_next_cmd,
        UsmUserData, ContextData as v3_ContextData,
        usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol,
        usmDESPrivProtocol, usmAesCfb128Protocol as usmAESPrivProtocol,
        usmNoAuthProtocol, usmNoPrivProtocol
    )
    # v1arch 可能不需要 ContextData，或者使用不同的方式
    try:
        from pysnmp.hlapi.v1arch import ContextData as v1_ContextData
    except ImportError:
        # v1arch 可能不需要 ContextData
        v1_ContextData = None
    # 尝试从 asyncio 子模块导入 SnmpDispatcher（用于 v1/v2c）
    try:
        from pysnmp.hlapi.v1arch.asyncio import SnmpDispatcher
        V1ARCH_ASYNC = True
    except ImportError:
        V1ARCH_ASYNC = False
        SnmpDispatcher = None
    # SnmpEngine从entity导入
    from pysnmp.entity.engine import SnmpEngine
    from pysnmp.entity import engine, config
    # pysnmp 7.x使用asyncio而不是asyncore
    try:
        from pysnmp.carrier.asyncio.dgram import udp
        from pysnmp.carrier.asyncio.dgram.udp import UdpAsyncioTransport
        UDP_ASYNC_AVAILABLE = True
    except ImportError:
        try:
            from pysnmp.carrier.asyncore.dgram import udp
            UDP_ASYNC_AVAILABLE = False
        except ImportError:
            udp = None
            UdpAsyncioTransport = None
            UDP_ASYNC_AVAILABLE = False
            raise ImportError("无法导入UDP传输层")

    # 检查加密库是否可用（用于SNMPv3加密）
    CRYPTO_AVAILABLE = False
    # 方法1: 检查pysnmpcrypto扩展（推荐，pysnmp 7.x需要）
    try:
        import pysnmpcrypto
        CRYPTO_AVAILABLE = True
        logger.info("pysnmpcrypto扩展已安装，加密功能可用")
    except ImportError:
        # 方法2: 检查pycryptodome（基础加密库）
        try:
            from Crypto.Cipher import DES, AES
            # 然后尝试导入pysnmp的加密模块
            try:
                from pysnmp.proto.secmod.rfc3414.priv import des
                # 尝试导入AES（如果pycryptodome已安装，应该可用）
                try:
                    from pysnmp.proto.secmod.rfc3414.priv import aes
                    CRYPTO_AVAILABLE = True
                    logger.info("pysnmp加密模块（DES和AES）可用")
                except ImportError:
                    # 只有DES可用
                    CRYPTO_AVAILABLE = True
                    logger.info("pysnmp加密模块（仅DES）可用，AES可能不可用")
            except ImportError:
                # pycryptodome已安装，但pysnmp加密模块导入失败
                CRYPTO_AVAILABLE = True
                logger.warning("pycryptodome已安装，但pysnmp加密模块导入失败，加密功能可能受限")
        except ImportError:
            CRYPTO_AVAILABLE = False
            logger.warning("加密库未安装，SNMPv3加密功能不可用。请安装: pip install pysnmpcrypto 或 pip install pycryptodome")

    PYSNMP_AVAILABLE = True
    logger.info("pysnmp 7.x模块导入成功")
except ImportError as e:
    PYSNMP_AVAILABLE = False
    CRYPTO_AVAILABLE = False
    import traceback
    logger.warning(f"pysnmp未安装或导入失败，SNMP功能不可用。错误: {e}")
    logger.debug(traceback.format_exc())
except Exception as e:
    PYSNMP_AVAILABLE = False
    CRYPTO_AVAILABLE = False
    import traceback
    logger.warning(f"pysnmp导入时发生异常，SNMP功能不可用。错误: {e}")
    logger.debug(traceback.format_exc())


# 全局TRAP接收状态
trap_receiver_state = {
    'running': False,
    'port': 162,
    'traps': deque(maxlen=10000),  # 最多保存10000条TRAP
    'lock': threading.Lock(),
    'transport': None,
    'thread': None,
    'loop_thread': None,
    'loop': None
}


def snmp_get(ip, oid, community='public', version='2c', port=161,
             security_username='', security_level='noAuthNoPriv',
             auth_protocol='MD5', auth_password='',
             priv_protocol='DES', priv_password=''):
    """
    SNMP GET操作

    Args:
        ip: 设备IP地址
        oid: OID字符串，例如 '1.3.6.1.2.1.1.1.0'
        community: SNMP community（v1/v2c使用）
        version: SNMP版本 ('v1', 'v2c', 'v3')
        port: SNMP端口，默认161
        security_username: SNMPv3安全用户名
        security_level: SNMPv3安全级别 ('noAuthNoPriv', 'authNoPriv', 'authPriv')
        auth_protocol: SNMPv3认证协议 ('MD5', 'SHA')
        auth_password: SNMPv3认证密码
        priv_protocol: SNMPv3加密协议 ('DES', 'AES')
        priv_password: SNMPv3加密密码

    Returns:
        tuple: (success: bool, result: dict or error_message: str)
    """
    if not PYSNMP_AVAILABLE:
        return False, 'pysnmp未安装或导入失败，请检查pysnmp是否正确安装: pip install pysnmp'

    try:
        # 构建OID对象
        # 清理OID字符串：去除首尾空格，过滤空字符串
        oid_clean = oid.strip()
        if not oid_clean:
            return False, 'OID不能为空'
        # 分割并过滤空字符串（处理连续的点或多个点的情况）
        oid_parts = [p for p in oid_clean.split('.') if p.strip()]
        if not oid_parts:
            return False, 'OID格式无效'
        try:
            oid_tuple = tuple(map(int, oid_parts))
        except ValueError as e:
            return False, f'OID格式错误，包含非数字字符: {e}'

        # pysnmp 7.x的get_cmd是异步的，需要使用asyncio
        import asyncio

        async def async_snmp_get():
            # 创建传输目标
            transport_target = await UdpTransportTarget.create((ip, port))

            if version == 'v1':
                # SNMPv1 - 使用 SnmpDispatcher 而不是 SnmpEngine
                if V1ARCH_ASYNC and SnmpDispatcher is not None:
                    snmp_dispatcher = SnmpDispatcher()
                else:
                    # 如果没有 SnmpDispatcher，尝试使用 SnmpEngine
                    snmp_dispatcher = SnmpEngine()
                errorIndication, errorStatus, errorIndex, varBinds = await v1_get_cmd(
                    snmp_dispatcher,
                    CommunityData(community, mpModel=0),
                    transport_target,
                    ObjectType(ObjectIdentity(oid_tuple)),
                    lexicographicMode=False
                )
            elif version == 'v2c':
                # SNMPv2c - 使用 SnmpDispatcher 而不是 SnmpEngine
                if V1ARCH_ASYNC and SnmpDispatcher is not None:
                    snmp_dispatcher = SnmpDispatcher()
                else:
                    # 如果没有 SnmpDispatcher，尝试使用 SnmpEngine
                    snmp_dispatcher = SnmpEngine()
                errorIndication, errorStatus, errorIndex, varBinds = await v1_get_cmd(
                    snmp_dispatcher,
                    CommunityData(community, mpModel=1),
                    transport_target,
                    ObjectType(ObjectIdentity(oid_tuple)),
                    lexicographicMode=False
                )
            elif version == 'v3':
                # SNMPv3
                # 配置安全参数
                if security_level == 'noAuthNoPriv':
                    auth_proto = None
                    priv_proto = None
                elif security_level == 'authNoPriv':
                    auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                    priv_proto = None
                else:  # authPriv
                    if not CRYPTO_AVAILABLE:
                        return None, None, None, None, '加密库未安装，无法使用SNMPv3加密功能。请安装: pip install pycryptodome'
                    auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                    priv_proto = usmDESPrivProtocol if priv_protocol == 'DES' else usmAESPrivProtocol

                errorIndication, errorStatus, errorIndex, varBinds = await v3_get_cmd(
                    SnmpEngine(),
                    UsmUserData(security_username,
                              authKey=auth_password if auth_proto else None,
                              privKey=priv_password if priv_proto else None,
                              authProtocol=auth_proto,
                              privProtocol=priv_proto),
                    transport_target,
                    v3_ContextData(),
                    ObjectType(ObjectIdentity(oid_tuple)),
                    lexicographicMode=False
                )
            else:
                return None, None, None, None, f'不支持的SNMP版本: {version}'

            return errorIndication, errorStatus, errorIndex, varBinds, None

        # 运行异步函数
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        errorIndication, errorStatus, errorIndex, varBinds, version_error = loop.run_until_complete(async_snmp_get())

        if version_error:
            return False, version_error

        # 处理结果
        if errorIndication:
            error_msg = str(errorIndication)
            # 如果是加密服务不可用的错误，提供更详细的提示
            if 'Ciphering services not available' in error_msg or 'ciphering' in error_msg.lower():
                return False, f'SNMP错误: {errorIndication}。请安装 pysnmpcrypto 扩展: pip install pysnmpcrypto，然后重启服务。如果已安装，请确保已重启服务以使更改生效。'
            return False, f'SNMP错误: {errorIndication}'
        elif errorStatus:
            return False, f'SNMP错误状态: {errorStatus.prettyPrint()} at {errorIndex and varBinds[int(errorIndex) - 1][0] or "?"}'
        else:
            # 成功获取值
            result = []
            for varBind in varBinds:
                oid_str = '.'.join([str(x) for x in varBind[0]])
                value = varBind[1]
                # 转换值为字符串
                if hasattr(value, 'prettyPrint'):
                    value_str = value.prettyPrint()
                else:
                    value_str = str(value)

                result.append({
                    'oid': oid_str,
                    'value': value_str,
                    'type': value.__class__.__name__
                })

            return True, result

    except Exception as e:
        logger.exception(f"SNMP GET操作失败: {e}")
        return False, f'SNMP GET失败: {str(e)}'


def snmp_walk(ip, oid, community='public', version='2c', port=161,
              security_username='', security_level='noAuthNoPriv',
              auth_protocol='MD5', auth_password='',
              priv_protocol='DES', priv_password=''):
    """
    SNMP WALK操作（获取OID子树的所有值）

    Args:
        参数同snmp_get

    Returns:
        tuple: (success: bool, result: list or error_message: str)
    """
    if not PYSNMP_AVAILABLE:
        return False, 'pysnmp未安装或导入失败，请检查pysnmp是否正确安装: pip install pysnmp'

    try:
        # 清理OID字符串：去除首尾空格，过滤空字符串
        oid_clean = oid.strip()
        if not oid_clean:
            return False, 'OID不能为空'
        # 分割并过滤空字符串（处理连续的点或多个点的情况）
        oid_parts = [p for p in oid_clean.split('.') if p.strip()]
        if not oid_parts:
            return False, 'OID格式无效'
        try:
            oid_tuple = tuple(map(int, oid_parts))
        except ValueError as e:
            return False, f'OID格式错误，包含非数字字符: {e}'

        # 检查加密库（用于v3 authPriv）
        if version == 'v3' and security_level == 'authPriv' and not CRYPTO_AVAILABLE:
            return False, '加密库未安装，无法使用SNMPv3加密功能。请安装: pip install pycryptodome'

        results = []

        # pysnmp 7.x的next_cmd是异步的，需要使用asyncio
        import asyncio

        async def async_snmp_walk():
            # 创建传输目标
            transport_target = await UdpTransportTarget.create((ip, port))

            # 准备next_cmd的参数（根据版本不同）
            if version == 'v1':
                # SNMPv1
                if V1ARCH_ASYNC and SnmpDispatcher is not None:
                    snmp_dispatcher = SnmpDispatcher()
                else:
                    snmp_dispatcher = SnmpEngine()
                next_cmd_func = v1_next_cmd
                auth_data = CommunityData(community, mpModel=0)
                context_data = None
            elif version == 'v2c':
                # SNMPv2c
                if V1ARCH_ASYNC and SnmpDispatcher is not None:
                    snmp_dispatcher = SnmpDispatcher()
                else:
                    snmp_dispatcher = SnmpEngine()
                next_cmd_func = v1_next_cmd
                auth_data = CommunityData(community, mpModel=1)
                context_data = None
            elif version == 'v3':
                # SNMPv3
                if security_level == 'noAuthNoPriv':
                    auth_proto = None
                    priv_proto = None
                elif security_level == 'authNoPriv':
                    auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                    priv_proto = None
                else:  # authPriv
                    if not CRYPTO_AVAILABLE:
                        return  # 提前返回，错误会在外层处理
                    auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                    # 尝试使用DES，如果失败再尝试AES
                    try:
                        priv_proto = usmDESPrivProtocol if priv_protocol == 'DES' else usmAESPrivProtocol
                    except Exception as e:
                        # 如果加密协议不可用，尝试只使用DES
                        logger.warning(f"加密协议 {priv_protocol} 不可用，尝试使用DES: {e}")
                        priv_proto = usmDESPrivProtocol

                snmp_dispatcher = SnmpEngine()
                next_cmd_func = v3_next_cmd
                auth_data = UsmUserData(security_username,
                                      authKey=auth_password if auth_proto else None,
                                      privKey=priv_password if priv_proto else None,
                                      authProtocol=auth_proto,
                                      privProtocol=priv_proto)
                context_data = v3_ContextData()
            else:
                return

            # pysnmp 7.x的next_cmd返回tuple，需要循环调用直到完成
            current_oid = oid_tuple
            max_iterations = 1000  # 防止无限循环
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                # 调用next_cmd获取下一个值
                if version == 'v3':
                    errorIndication, errorStatus, errorIndex, varBinds = await next_cmd_func(
                        snmp_dispatcher,
                        auth_data,
                        transport_target,
                        context_data,
                        ObjectType(ObjectIdentity(current_oid)),
                        lexicographicMode=False
                    )
                else:
                    errorIndication, errorStatus, errorIndex, varBinds = await next_cmd_func(
                        snmp_dispatcher,
                        auth_data,
                        transport_target,
                        ObjectType(ObjectIdentity(current_oid)),
                        lexicographicMode=False
                    )

                if errorIndication:
                    # 错误指示，停止遍历
                    break
                elif errorStatus:
                    # 错误状态，停止遍历
                    break
                else:
                    # 处理返回的变量绑定
                    for varBind in varBinds:
                        oid_str = '.'.join([str(x) for x in varBind[0]])
                        value = varBind[1]
                        value_str = value.prettyPrint() if hasattr(value, 'prettyPrint') else str(value)
                        results.append({
                            'oid': oid_str,
                            'value': value_str,
                            'type': value.__class__.__name__
                        })

                        # 更新当前OID为返回的OID，用于下一次迭代
                        current_oid = varBind[0]

                        # 检查是否已经遍历完（返回的OID不在子树范围内）
                        # 如果返回的OID不在原始OID的子树下，说明遍历完成
                        if len(current_oid) <= len(oid_tuple):
                            # OID长度不够，可能已经遍历完
                            return
                        # 检查前缀是否匹配
                        if current_oid[:len(oid_tuple)] != oid_tuple:
                            # OID前缀不匹配，遍历完成
                            return

        # 运行异步函数
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(async_snmp_walk())

        return True, results

    except Exception as e:
        logger.exception(f"SNMP WALK操作失败: {e}")
        return False, f'SNMP WALK失败: {str(e)}'


def trap_receiver_callback(snmpEngine, stateReference, contextEngineId, contextName, varBinds, cbCtx):
    """
    SNMPTRAP接收回调函数
    """
    global trap_receiver_state

    try:
        # 获取发送方信息
        # pysnmp 7.x 使用 message_dispatcher 和 get_transport_info (下划线命名)
        try:
            transportDomain, transportAddress = snmpEngine.message_dispatcher.get_transport_info(stateReference)
        except AttributeError:
            try:
                # 尝试旧的下划线命名
                transportDomain, transportAddress = snmpEngine.message_dispatcher.getTransportInfo(stateReference)
            except AttributeError:
                # 回退到旧API
                try:
                    transportDomain, transportAddress = snmpEngine.msgAndPduDsp.getTransportInfo(stateReference)
                except AttributeError:
                    # 如果都失败，尝试从 stateReference 获取
                    logger.warning("无法获取传输信息，使用默认值")
                    transportAddress = ('0.0.0.0', 0)
                    transportDomain = None
        source_ip = transportAddress[0] if transportAddress else '0.0.0.0'
        source_port = transportAddress[1] if len(transportAddress) > 1 else 0

        # 解析TRAP信息
        trap_info = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source_ip': source_ip,
            'source_port': source_port,
            'oid_values': []
        }

        # 解析varBinds
        for oid, val in varBinds:
            oid_str = '.'.join([str(x) for x in oid])
            value_str = val.prettyPrint() if hasattr(val, 'prettyPrint') else str(val)
            trap_info['oid_values'].append({
                'oid': oid_str,
                'value': value_str,
                'type': val.__class__.__name__
            })

        # 添加到TRAP列表
        with trap_receiver_state['lock']:
            trap_receiver_state['traps'].append(trap_info)

        logger.info(f'收到SNMPTRAP: {source_ip} - {len(trap_info["oid_values"])}个OID')

    except Exception as e:
        logger.error(f'处理SNMPTRAP时出错: {e}')
        import traceback
        logger.debug(traceback.format_exc())


def start_trap_receiver(port=162, security_username='', security_level='noAuthNoPriv',
                        auth_protocol='MD5', auth_password='',
                        priv_protocol='DES', priv_password=''):
    """
    启动SNMPTRAP接收器

    Args:
        port: 监听端口，默认162
        security_username: SNMPv3安全用户名（v3使用）
        security_level: SNMPv3安全级别 ('noAuthNoPriv', 'authNoPriv', 'authPriv')
        auth_protocol: SNMPv3认证协议 ('MD5', 'SHA')
        auth_password: SNMPv3认证密码
        priv_protocol: SNMPv3加密协议 ('DES', 'AES')
        priv_password: SNMPv3加密密码

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    logger.info(f"start_trap_receiver 被调用: port={port}, security_username={security_username}")

    if not PYSNMP_AVAILABLE:
        logger.error("pysnmp未安装")
        return False, 'pysnmp未安装或导入失败，请检查pysnmp是否正确安装: pip install pysnmp'

    with trap_receiver_state['lock']:
        if trap_receiver_state['running']:
            logger.warning("SNMPTRAP接收器已在运行中")
            return False, 'SNMPTRAP接收器已在运行中'

        # 先设置运行状态，确保状态正确（在锁外启动线程，避免死锁）
        trap_receiver_state['running'] = True
        trap_receiver_state['port'] = port

    try:
        # 启动异步接收（在事件循环中创建和配置 SnmpEngine）
        def trap_worker():
            loop = None
            try:
                # pysnmp 7.x使用asyncio，需要在新线程中运行事件循环
                import asyncio
                # 为新线程创建新的事件循环
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                # 更新状态
                with trap_receiver_state['lock']:
                    trap_receiver_state['loop'] = loop

                logger.info(f"trap_worker: 开始配置SNMP引擎，端口: {port}")

                # 创建SNMP引擎（不在异步函数中，直接创建）
                snmpEngine = SnmpEngine()
                logger.info("trap_worker: SnmpEngine 创建成功")

                # 配置传输层（pysnmp 7.x 使用标准 UDP 传输）
                logger.info(f"trap_worker: 开始配置UDP传输层，端口: {port}")
                # pysnmp 7.x 使用 UdpAsyncioTransport 和 open_server_mode (下划线命名)
                try:
                    if UDP_ASYNC_AVAILABLE and UdpAsyncioTransport:
                        transport = UdpAsyncioTransport().open_server_mode(('0.0.0.0', port))
                        logger.info("trap_worker: UDP传输层配置成功（使用UdpAsyncioTransport）")
                    else:
                        # 回退到旧版本
                        transport = udp.UdpTransport().openServerMode(('0.0.0.0', port))
                        logger.info("trap_worker: UDP传输层配置成功（使用UdpTransport旧版本）")
                except Exception as e:
                    logger.error(f"配置UDP传输层失败: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    raise

                config.addTransport(
                    snmpEngine,
                    udp.domainName + (1,),
                    transport
                )

                # 配置SNMPv1/v2c TRAP接收（接受任何community）
                # pysnmp 7.x 可能不需要显式配置 v1/v2c，或者使用不同的方法
                # 尝试配置，如果失败则跳过（TRAP接收器可能仍然可以工作）
                # 注意：pysnmp 7.x 的 config 模块可能没有 addV1System/addV2System
                # 这些配置可能不是必需的，因为 TRAP 接收器可以通过其他方式工作
                logger.info("跳过SNMPv1/v2c系统配置（pysnmp 7.x可能不需要）")

                # 配置SNMPv3 TRAP接收
                # 注意：对于TRAP接收器，如果未指定security_username，则只支持v1/v2c
                # 如果需要v3支持，必须提供security_username
                if security_username:
                    # 根据安全级别配置认证和加密
                    try:
                        if security_level == 'noAuthNoPriv':
                            # noAuthNoPriv: 使用 usmNoAuthProtocol 和 usmNoPrivProtocol
                            config.add_v3_user(
                                snmpEngine, security_username,
                                authProtocol=usmNoAuthProtocol, authKey=None,
                                privProtocol=usmNoPrivProtocol, privKey=None
                            )
                            logger.info(f"SNMPv3用户配置成功（noAuthNoPriv）: {security_username}")
                        elif security_level == 'authNoPriv':
                            auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                            auth_key = auth_password if auth_password else 'authkey1'
                            config.add_v3_user(
                                snmpEngine, security_username,
                                authProtocol=auth_proto, authKey=auth_key,
                                privProtocol=usmNoPrivProtocol, privKey=None
                            )
                            logger.info(f"SNMPv3用户配置成功（authNoPriv）: {security_username}")
                        else:  # authPriv
                            if not CRYPTO_AVAILABLE:
                                raise Exception('加密库未安装，无法使用SNMPv3加密功能。请安装: pip install pycryptodome')
                            auth_proto = usmHMACMD5AuthProtocol if auth_protocol == 'MD5' else usmHMACSHAAuthProtocol
                            priv_proto = usmDESPrivProtocol if priv_protocol == 'DES' else usmAESPrivProtocol
                            auth_key = auth_password if auth_password else 'authkey1'
                            priv_key = priv_password if priv_password else 'privkey1'
                            config.add_v3_user(
                                snmpEngine, security_username,
                                authProtocol=auth_proto, authKey=auth_key,
                                privProtocol=priv_proto, privKey=priv_key
                            )
                            logger.info(f"SNMPv3用户配置成功（authPriv）: {security_username}")

                        # 配置VACM（View-based Access Control Model）以允许TRAP接收
                        # 对于TRAP接收器，通常需要配置VACM来允许用户发送通知
                        try:
                            # 添加上下文
                            config.add_context(snmpEngine, '')
                            # 添加VACM用户，允许该用户发送通知
                            # add_vacm_user(snmpEngine, securityModel, securityName, securityLevel, readSubTree=(), writeSubTree=(), notifySubTree=(), contextName=b'')
                            # securityModel: 3 for USM (User-based Security Model)
                            # 对于TRAP接收，需要配置notifySubTree来允许通知
                            security_level_str = {
                                'noAuthNoPriv': 'noAuthNoPriv',
                                'authNoPriv': 'authNoPriv',
                                'authPriv': 'authPriv'
                            }.get(security_level, 'noAuthNoPriv')

                            config.add_vacm_user(
                                snmpEngine, 3,  # securityModel=3 for USM
                                security_username, security_level_str,
                                readSubTree=(1, 3, 6, 1),  # 允许读取整个MIB树
                                writeSubTree=(),  # 不允许写入
                                notifySubTree=(1, 3, 6, 1),  # 允许通知整个MIB树
                                contextName=b''  # 空上下文
                            )
                            logger.info(f"VACM用户配置成功: {security_username}")
                        except Exception as e:
                            logger.warning(f"配置VACM用户失败（可能不是必需的）: {e}")
                            import traceback
                            logger.debug(traceback.format_exc())
                    except Exception as e:
                        logger.error(f"配置SNMPv3用户失败: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                        raise
                else:
                    # 如果没有指定用户名，只配置v1/v2c支持（不配置v3用户）
                    logger.info("未指定SNMPv3用户名，仅支持v1/v2c TRAP接收")

                # 注册TRAP回调
                # pysnmp 7.x 使用 add_notification_target，需要先配置目标参数和地址
                try:
                    # 首先需要配置目标参数（用于v1/v2c）
                    # add_target_parameters(snmpEngine, name, securityName, securityLevel, mpModel=3)
                    try:
                        config.add_target_parameters(snmpEngine, 'my-area', 'public', 'noAuthNoPriv', mpModel=0)  # mpModel=0 for v1
                        logger.info("目标参数配置成功（v1）")
                    except Exception as e:
                        logger.warning(f"配置v1目标参数失败: {e}")

                    try:
                        config.add_target_parameters(snmpEngine, 'my-area-v2c', 'public', 'noAuthNoPriv', mpModel=1)  # mpModel=1 for v2c
                        logger.info("目标参数配置成功（v2c）")
                    except Exception as e:
                        logger.warning(f"配置v2c目标参数失败: {e}")

                    # 配置目标地址
                    # add_target_address(snmpEngine, addrName, transportDomain, transportAddress, params, timeout=None, retryCount=None, tagList=b'', sourceAddress=None)
                    try:
                        config.add_target_address(
                            snmpEngine, 'my-notif',
                            udp.domainName + (1,), ('0.0.0.0', port),
                            'my-area', timeout=1.5, retryCount=0, tagList=b'my-notif'
                        )
                        logger.info("目标地址配置成功")
                    except Exception as e:
                        logger.warning(f"配置目标地址失败: {e}，尝试其他方法")
                        try:
                            config.add_target_address(
                                snmpEngine, 'my-notif',
                                config.SNMP_UDP_DOMAIN, ('0.0.0.0', port),
                                'my-area', timeout=1.5, retryCount=0, tagList=b'my-notif'
                            )
                            logger.info("目标地址配置成功（方法2）")
                        except Exception as e2:
                            logger.warning(f"配置目标地址失败（方法2）: {e2}")

                    # 注册通知目标（TRAP回调）
                    # add_notification_target(snmpEngine, notificationName, paramsName, transportTag, notifyType=None, filterSubtree=None, filterMask=None, filterType=None)
                    # 注意：notifyType 应该是字符串 'trap'，而不是回调函数
                    # 回调函数需要通过 ntfrcv.NotificationReceiver 注册
                    try:
                        from pysnmp.entity.rfc3413 import ntfrcv
                        ntfrcv.NotificationReceiver(snmpEngine, trap_receiver_callback)
                        logger.info("TRAP回调函数注册成功（使用ntfrcv）")
                    except ImportError:
                        logger.warning("无法导入 ntfrcv，尝试使用 add_notification_target")
                        if hasattr(config, 'add_notification_target'):
                            config.add_notification_target(
                                snmpEngine, 'my-notif', 'my-area', 'my-notif',
                                notifyType='trap'
                            )
                            logger.info("通知目标注册成功（但回调可能无法工作）")
                        else:
                            logger.warning("无法注册通知目标")
                    except Exception as e:
                        logger.warning(f"注册TRAP回调函数失败: {e}，尝试使用 add_notification_target")
                        if hasattr(config, 'add_notification_target'):
                            try:
                                config.add_notification_target(
                                    snmpEngine, 'my-notif', 'my-area', 'my-notif',
                                    notifyType='trap'
                                )
                                logger.info("通知目标注册成功（但回调可能无法工作）")
                            except Exception as e2:
                                logger.error(f"注册通知目标失败: {e2}")
                except Exception as e:
                    logger.error(f"注册TRAP回调失败: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())

                # 保存引擎引用
                with trap_receiver_state['lock']:
                    trap_receiver_state['transport'] = snmpEngine

                logger.info(f'SNMPTRAP接收器已配置完成，监听端口: {port}')

                # 启动事件循环（非阻塞方式，在后台线程中运行）
                # 注意：transport_dispatcher 需要在事件循环中启动
                def run_loop():
                    try:
                        logger.info("run_loop: 开始运行事件循环")

                        # UDP transport 已经在事件循环中注册，事件循环会自动处理
                        # 不需要显式调用 run_dispatcher，只需要保持事件循环运行
                        async def keep_alive():
                            logger.info("keep_alive: 开始运行，保持事件循环活跃")
                            while trap_receiver_state['running']:
                                await asyncio.sleep(0.1)
                            logger.info("keep_alive: 停止运行")

                        # 启动 keep_alive 任务
                        asyncio.ensure_future(keep_alive(), loop=loop)
                        logger.info("run_loop: keep_alive 任务已添加，开始 run_forever")
                        loop.run_forever()
                    except Exception as e:
                        logger.error(f'事件循环运行错误: {e}')
                        import traceback
                        logger.debug(traceback.format_exc())
                    finally:
                        try:
                            # 关闭 transport_dispatcher
                            if hasattr(snmpEngine, 'transport_dispatcher'):
                                try:
                                    if hasattr(snmpEngine.transport_dispatcher, 'close_dispatcher'):
                                        loop.run_until_complete(snmpEngine.transport_dispatcher.close_dispatcher())
                                    elif hasattr(snmpEngine.transport_dispatcher, 'closeDispatcher'):
                                        snmpEngine.transport_dispatcher.closeDispatcher()
                                except:
                                    pass
                            elif hasattr(snmpEngine, 'transportDispatcher'):
                                try:
                                    snmpEngine.transportDispatcher.closeDispatcher()
                                except:
                                    pass
                            if loop and not loop.is_closed():
                                loop.close()
                        except:
                            pass

                # 在单独的线程中运行事件循环
                logger.info("准备启动事件循环线程")
                loop_thread = threading.Thread(target=run_loop, daemon=True)
                loop_thread.start()
                trap_receiver_state['loop_thread'] = loop_thread

                # 等待一下，让事件循环有时间启动
                import time
                time.sleep(0.5)

                logger.info(f'SNMPTRAP接收器事件循环已启动')
            except RuntimeError as e:
                # 如果事件循环已关闭，忽略错误
                error_str = str(e).lower()
                if 'event loop is closed' not in error_str and 'no current event loop' not in error_str:
                    logger.error(f'TRAP接收线程事件循环错误: {e}')
                    import traceback
                    logger.debug(traceback.format_exc())
                    with trap_receiver_state['lock']:
                        trap_receiver_state['running'] = False
            except Exception as e:
                logger.error(f'TRAP接收线程运行错误: {e}')
                import traceback
                logger.debug(traceback.format_exc())
                with trap_receiver_state['lock']:
                    trap_receiver_state['running'] = False
            finally:
                try:
                    if loop:
                        # 停止所有任务
                        try:
                            pending = asyncio.all_tasks(loop)
                            for task in pending:
                                task.cancel()
                            # 等待任务完成
                            if pending:
                                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                        except:
                            pass
                        finally:
                            try:
                                loop.close()
                            except:
                                pass
                except:
                    pass

        # 启动接收线程（状态已在锁外设置）
        logger.info("准备启动 trap_worker 线程")
        thread = threading.Thread(target=trap_worker, daemon=True)
        thread.start()
        trap_receiver_state['thread'] = thread
        logger.info("trap_worker 线程已启动")

        # 等待一下，确保线程启动并设置状态
        import time
        time.sleep(0.3)  # 减少等待时间，快速返回

        # 检查状态是否成功设置
        with trap_receiver_state['lock']:
            if trap_receiver_state['running']:
                logger.info(f"SNMPTRAP接收器启动成功，端口: {port}")
                return True, f'SNMPTRAP接收器已启动，监听端口: {port}'
            else:
                logger.error("SNMPTRAP接收器启动失败，状态未设置")
                return False, 'SNMPTRAP接收器启动失败，请查看日志'

    except Exception as e:
        logger.exception(f"启动SNMPTRAP接收器失败: {e}")
        with trap_receiver_state['lock']:
            trap_receiver_state['running'] = False
        return False, f'启动失败: {str(e)}'


def stop_trap_receiver():
    """
    停止SNMPTRAP接收器

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    with trap_receiver_state['lock']:
        if not trap_receiver_state['running']:
            return False, 'SNMPTRAP接收器未运行'

        trap_receiver_state['running'] = False

        # 关闭传输
        if trap_receiver_state['transport']:
            try:
                # 关闭所有传输
                transportDispatcher = trap_receiver_state['transport'].msgAndPduDsp.mibInstrumController.transportDispatcher
                if transportDispatcher:
                    for transport in list(transportDispatcher.transportTable.values()):
                        try:
                            transport.closeTransport()
                        except:
                            pass
            except Exception as e:
                logger.warning(f"关闭传输时出错: {e}")

        # 等待线程结束
        if trap_receiver_state['thread']:
            trap_receiver_state['thread'].join(timeout=2)

        trap_receiver_state['transport'] = None
        trap_receiver_state['thread'] = None

        return True, 'SNMPTRAP接收器已停止'


def get_trap_receiver_status():
    """
    获取SNMPTRAP接收器状态

    Returns:
        dict: 状态信息
    """
    global trap_receiver_state

    with trap_receiver_state['lock']:
        return {
            'running': trap_receiver_state['running'],
            'port': trap_receiver_state['port'],
            'trap_count': len(trap_receiver_state['traps'])
        }


def get_trap_receiver_traps(limit=1000):
    """
    获取接收到的TRAP列表

    Args:
        limit: 返回的TRAP条数

    Returns:
        list: TRAP列表
    """
    global trap_receiver_state

    with trap_receiver_state['lock']:
        traps = list(trap_receiver_state['traps'])
        return traps[-limit:]


def clear_trap_receiver_traps():
    """
    清空TRAP列表

    Returns:
        tuple: (success: bool, message: str)
    """
    global trap_receiver_state

    with trap_receiver_state['lock']:
        trap_receiver_state['traps'].clear()
        return True, 'TRAP列表已清空'