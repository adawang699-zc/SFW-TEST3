#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus TCP 客户端
使用 pymodbus 实现 Modbus TCP 通信
"""

import logging
import threading
from typing import Dict, Tuple, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

# 尝试导入 pymodbus
PYMODBUS_AVAILABLE = False
ModbusTcpClient = None
ModbusException = None

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ModbusException
    PYMODBUS_AVAILABLE = True
    logger.info("pymodbus 导入成功")
except ImportError:
    logger.warning("pymodbus 未安装，Modbus 功能将不可用。安装: pip install pymodbus")


class ModbusClient:
    """Modbus TCP 客户端"""

    def __init__(self):
        self.clients: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def connect(self, ip: str, port: int = 502, client_id: str = 'default',
                unit_id: int = 1, timeout: int = 3) -> Tuple[bool, str]:
        """连接 Modbus 服务端"""
        if not PYMODBUS_AVAILABLE:
            return False, "pymodbus 未安装"

        if not ip:
            return False, "IP 地址不能为空"

        with self.lock:
            # 断开旧连接
            if client_id in self.clients:
                try:
                    self.clients[client_id]['client'].close()
                except:
                    pass
                del self.clients[client_id]

            try:
                client = ModbusTcpClient(host=ip, port=port, timeout=timeout)
                result = client.connect()

                if result:
                    self.clients[client_id] = {
                        'client': client,
                        'ip': ip,
                        'port': port,
                        'unit_id': unit_id,
                        'connected': True,
                        'connect_time': datetime.now().isoformat()
                    }
                    logger.info(f"Modbus 连接成功: {ip}:{port}")
                    return True, "连接成功"
                else:
                    logger.error(f"Modbus 连接失败: {ip}:{port}")
                    return False, "连接失败"

            except Exception as e:
                logger.exception(f"Modbus 连接异常: {e}")
                return False, str(e)

    def disconnect(self, client_id: str = 'default') -> Tuple[bool, str]:
        """断开连接"""
        with self.lock:
            if client_id in self.clients:
                try:
                    self.clients[client_id]['client'].close()
                except:
                    pass
                del self.clients[client_id]
                logger.info(f"Modbus 断开连接: {client_id}")
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
                    'unit_id': client_info['unit_id'],
                    'connect_time': client_info['connect_time']
                }
            return {'connected': False}

    def read(self, client_id: str = 'default', function_code: int = 3,
             address: int = 0, count: int = 1) -> Tuple[bool, List]:
        """读取数据"""
        if not PYMODBUS_AVAILABLE:
            return False, []

        with self.lock:
            if client_id not in self.clients:
                return False, []

            client_info = self.clients[client_id]
            client = client_info['client']
            unit_id = client_info['unit_id']

            try:
                # pymodbus 3.7.4 使用 slave 参数
                slave_id = unit_id

                if function_code == 1:  # 读线圈
                    response = client.read_coils(address=address, count=count, slave=slave_id)
                    if response.isError():
                        return False, []
                    result = [1 if bit else 0 for bit in (response.bits[:count] if response.bits else [])]

                elif function_code == 2:  # 读离散输入
                    response = client.read_discrete_inputs(address=address, count=count, slave=slave_id)
                    if response.isError():
                        return False, []
                    result = [1 if bit else 0 for bit in (response.bits[:count] if response.bits else [])]

                elif function_code == 3:  # 读保持寄存器
                    response = client.read_holding_registers(address=address, count=count, slave=slave_id)
                    if response.isError():
                        return False, []
                    result = response.registers[:count] if response.registers else []

                elif function_code == 4:  # 读输入寄存器
                    response = client.read_input_registers(address=address, count=count, slave=slave_id)
                    if response.isError():
                        return False, []
                    result = response.registers[:count] if response.registers else []

                else:
                    return False, []

                logger.info(f"Modbus 读成功: 功能码={function_code}, 地址={address}, 结果={result}")
                return True, result

            except ModbusException as e:
                logger.error(f"Modbus 读异常: {e}")
                return False, []
            except Exception as e:
                logger.exception(f"Modbus 读异常: {e}")
                return False, []

    def write(self, client_id: str = 'default', function_code: int = 6,
              address: int = 0, values: List = []) -> Tuple[bool, str]:
        """写入数据"""
        if not PYMODBUS_AVAILABLE:
            return False, "pymodbus 未安装"

        if not values:
            return False, "写入值不能为空"

        with self.lock:
            if client_id not in self.clients:
                return False, "客户端未连接"

            client_info = self.clients[client_id]
            client = client_info['client']
            unit_id = client_info['unit_id']

            try:
                # pymodbus 3.7.4 使用 slave 参数
                slave_id = unit_id

                if function_code == 5:  # 写单个线圈
                    value = bool(values[0])
                    response = client.write_coil(address=address, value=value, slave=slave_id)

                elif function_code == 6:  # 写单个寄存器
                    value = int(values[0])
                    response = client.write_register(address=address, value=value, slave=slave_id)

                elif function_code == 15:  # 写多个线圈
                    values_bool = [bool(v) for v in values]
                    response = client.write_coils(address=address, values=values_bool, slave=slave_id)

                elif function_code == 16:  # 写多个寄存器
                    values_int = [int(v) for v in values]
                    response = client.write_registers(address=address, values=values_int, slave=slave_id)

                else:
                    return False, f"不支持的功能码: {function_code}"

                if response.isError():
                    return False, f"写入失败: {response}"

                logger.info(f"Modbus 写成功: 功能码={function_code}, 地址={address}")
                return True, "写入成功"

            except ModbusException as e:
                logger.error(f"Modbus 写异常: {e}")
                return False, str(e)
            except Exception as e:
                logger.exception(f"Modbus 写异常: {e}")
                return False, str(e)


# 全局客户端实例
modbus_client = ModbusClient()