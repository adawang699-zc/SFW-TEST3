#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S7 客户端
使用 python-snap7 实现西门子 S7 协议通信
"""

import logging
import threading
from typing import Dict, Tuple, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

# 尝试导入 snap7
SNAP7_AVAILABLE = False
try:
    import snap7
    from snap7.util import *
    from snap7.snap7types import *
    SNAP7_AVAILABLE = True
    logger.info("snap7 导入成功")
except ImportError:
    logger.warning("snap7 未安装，S7 功能将不可用。安装: pip install python-snap7")


class S7Client:
    """西门子 S7 客户端"""

    def __init__(self):
        self.clients: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def connect(self, ip: str, port: int = 102, client_id: str = 'default',
                rack: int = 0, slot: int = 1) -> Tuple[bool, str]:
        """连接 S7 PLC"""
        if not SNAP7_AVAILABLE:
            return False, "snap7 未安装"

        if not ip:
            return False, "IP 地址不能为空"

        with self.lock:
            # 断开旧连接
            if client_id in self.clients:
                try:
                    self.clients[client_id]['client'].disconnect()
                except:
                    pass
                del self.clients[client_id]

            try:
                client = snap7.client.Client()
                client.connect(ip, rack, slot, port)

                if client.get_connected():
                    self.clients[client_id] = {
                        'client': client,
                        'ip': ip,
                        'port': port,
                        'rack': rack,
                        'slot': slot,
                        'connected': True,
                        'connect_time': datetime.now().isoformat()
                    }
                    logger.info(f"S7 连接成功: {ip}:{port} (rack={rack}, slot={slot})")
                    return True, "连接成功"
                else:
                    logger.error(f"S7 连接失败: {ip}:{port}")
                    return False, "连接失败"

            except Exception as e:
                logger.exception(f"S7 连接异常: {e}")
                return False, str(e)

    def disconnect(self, client_id: str = 'default') -> Tuple[bool, str]:
        """断开连接"""
        with self.lock:
            if client_id in self.clients:
                try:
                    self.clients[client_id]['client'].disconnect()
                except:
                    pass
                del self.clients[client_id]
                logger.info(f"S7 断开连接: {client_id}")
                return True, "断开成功"
            return False, "连接不存在"

    def status(self, client_id: str = 'default') -> Dict:
        """获取连接状态"""
        with self.lock:
            if client_id in self.clients:
                client_info = self.clients[client_id]
                return {
                    'connected': client_info['connected'],
                    'ip': client_info['ip'],
                    'port': client_info['port'],
                    'rack': client_info['rack'],
                    'slot': client_info['slot'],
                    'connect_time': client_info['connect_time']
                }
            return {'connected': False}

    def read(self, client_id: str = 'default', area: int = 0x84,  # DB
            db_number: int = 1, start: int = 0, size: int = 1) -> Tuple[bool, bytes]:
        """读取数据"""
        if not SNAP7_AVAILABLE:
            return False, b''

        with self.lock:
            if client_id not in self.clients:
                return False, b''

            client = self.clients[client_id]['client']

            try:
                data = client.read_area(area, db_number, start, size)
                logger.info(f"S7 读成功: 区域={area}, DB={db_number}, 起始={start}, 大小={size}")
                return True, data

            except Exception as e:
                logger.exception(f"S7 读异常: {e}")
                return False, b''

    def write(self, data: bytes, area: int = 0x84,  # DB
                 db_number: int = 1, start: int = 0, client_id: str = 'default') -> Tuple[bool, str]:
        """写入数据"""
        if not SNAP7_AVAILABLE:
            return False, "snap7 未安装"

        with self.lock:
            if client_id not in self.clients:
                return False, "客户端未连接"

            client = self.clients[client_id]['client']

            try:
                client.write_area(area, db_number, start, data)
                logger.info(f"S7 写成功: 区域={area}, DB={db_number}, 起始={start}")
                return True, "写入成功"

            except Exception as e:
                logger.exception(f"S7 写异常: {e}")
                return False, str(e)

    def get_cpu_info(self, client_id: str = 'default') -> Tuple[bool, Dict]:
        """获取 CPU 信息"""
        if not SNAP7_AVAILABLE:
            return False, {}

        with self.lock:
            if client_id not in self.clients:
                return False, {}

            client = self.clients[client_id]['client']

            try:
                info = client.get_cpu_info()
                return True, {
                    'ModuleType': info.ModuleTypeName.decode() if hasattr(info, 'ModuleTypeName') else '',
                    'SerialNumber': info.SerialNumber.decode() if hasattr(info, 'SerialNumber') else '',
                    'ASName': info.ASName.decode() if hasattr(info, 'ASName') else '',
                    'Copyright': info.Copyright.decode() if hasattr(info, 'Copyright') else '',
                    'ModuleName': info.ModuleName.decode() if hasattr(info, 'ModuleName') else ''
                }

            except Exception as e:
                logger.exception(f"获取 CPU 信息异常: {e}")
                return False, {}

    def get_cpu_state(self, client_id: str = 'default') -> Tuple[bool, str]:
        """获取 CPU 状态"""
        if not SNAP7_AVAILABLE:
            return False, "unknown"

        with self.lock:
            if client_id not in self.clients:
                return False, "unknown"

            client = self.clients[client_id]['client']

            try:
                state = client.get_cpu_state()
                if state == 0x00:
                    return True, "RUN"
                elif state == 0x02:
                    return True, "STOP"
                elif state == 0x04:
                    return True, "HALT"
                else:
                    return True, f"Unknown ({state})"

            except Exception as e:
                logger.exception(f"获取 CPU 状态异常: {e}")
                return False, "unknown"

    def plc_cold_start(self, client_id: str = 'default') -> Tuple[bool, str]:
        """PLC 冷启动"""
        if not SNAP7_AVAILABLE:
            return False, "snap7 未安装"

        with self.lock:
            if client_id not in self.clients:
                return False, "客户端未连接"

            client = self.clients[client_id]['client']

            try:
                client.plc_cold_start()
                logger.info(f"PLC 冷启动: {client_id}")
                return True, "冷启动成功"

            except Exception as e:
                logger.exception(f"PLC 冷启动异常: {e}")
                return False, str(e)

    def plc_hot_start(self, client_id: str = 'default') -> Tuple[bool, str]:
        """PLC 热启动"""
        if not SNAP7_AVAILABLE:
            return False, "snap7 未安装"

        with self.lock:
            if client_id not in self.clients:
                return False, "客户端未连接"

            client = self.clients[client_id]['client']

            try:
                client.plc_hot_start()
                logger.info(f"PLC 热启动: {client_id}")
                return True, "热启动成功"

            except Exception as e:
                logger.exception(f"PLC 热启动异常: {e}")
                return False, str(e)

    def plc_stop(self, client_id: str = 'default') -> Tuple[bool, str]:
        """PLC 停止"""
        if not SNAP7_AVAILABLE:
            return False, "snap7 未安装"

        with self.lock:
            if client_id not in self.clients:
                return False, "客户端未连接"

            client = self.clients[client_id]['client']

            try:
                client.plc_stop()
                logger.info(f"PLC 停止: {client_id}")
                return True, "停止成功"

            except Exception as e:
                logger.exception(f"PLC 停止异常: {e}")
                return False, str(e)

    def list_blocks(self, client_id: str = 'default') -> Tuple[bool, List]:
        """列出 PLC 中的块"""
        if not SNAP7_AVAILABLE:
            return False, []

        with self.lock:
            if client_id not in self.clients:
                return False, []

            client = self.clients[client_id]['client']

            try:
                blocks = client.list_blocks()
                result = []
                for block in blocks:
                    result.append({
                        'type': block[0],
                        'number': block[1]
                    })
                logger.info(f"列出块: {len(result)} 个")
                return True, result

            except Exception as e:
                logger.exception(f"列出块异常: {e}")
                return False, []


# 全局客户端实例
s7_client = S7Client()