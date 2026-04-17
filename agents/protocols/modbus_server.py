#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus TCP 服务端
使用 pymodbus 实现 Modbus TCP 服务端
"""

import logging
import threading
import asyncio
from typing import Dict, Tuple, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

# 尝试导入 pymodbus
PYMODBUS_AVAILABLE = False
try:
    from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
    PYMODBUS_AVAILABLE = True
    logger.info("pymodbus datastore 导入成功")
except ImportError:
    logger.warning("pymodbus 未安装，Modbus 服务端功能将不可用")


class ModbusServer:
    """Modbus TCP 服务端"""

    def __init__(self):
        self.servers: Dict[str, dict] = {}
        self.datastores: Dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, server_id: str = 'default', port: int = 502,
              interface: str = '0.0.0.0') -> Tuple[bool, str]:
        """启动 Modbus 服务端"""
        if not PYMODBUS_AVAILABLE:
            return False, "pymodbus 未安装"

        with self.lock:
            if server_id in self.servers and self.servers[server_id].get('running'):
                return False, "服务端已在运行"

            try:
                # 创建数据存储
                # 线圈 (地址 0-9999)
                coils = ModbusSequentialDataBlock(0, [0] * 10000)
                # 离散输入 (地址 0-9999)
                discrete_inputs = ModbusSequentialDataBlock(0, [0] * 10000)
                # 保持寄存器 (地址 0-9999)
                holding_registers = ModbusSequentialDataBlock(0, [0] * 10000)
                # 输入寄存器 (地址 0-9999)
                input_registers = ModbusSequentialDataBlock(0, [0] * 10000)

                # 创建从站上下文
                slave_context = ModbusDeviceContext(
                    coils=coils,
                    discrete_inputs=discrete_inputs,
                    holding_registers=holding_registers,
                    input_registers=input_registers
                )

                # 创建服务端上下文（单从站）
                server_context = ModbusServerContext(slaves=slave_context, single=True)

                # 保存数据存储
                self.datastores[server_id] = {
                    'coils': coils,
                    'discrete_inputs': discrete_inputs,
                    'holding_registers': holding_registers,
                    'input_registers': input_registers,
                    'context': server_context
                }

                # 启动服务端（异步）
                async def run_server():
                    from pymodbus.server import AsyncModbusTcpServer
                    server = AsyncModbusTcpServer(
                        context=server_context,
                        address=(interface, port)
                    )
                    await server.serve_forever()

                # 在后台线程中运行
                def run_async():
                    asyncio.run(run_server())

                thread = threading.Thread(target=run_async, daemon=True)
                thread.start()

                self.servers[server_id] = {
                    'running': True,
                    'port': port,
                    'interface': interface,
                    'thread': thread,
                    'start_time': datetime.now().isoformat()
                }

                logger.info(f"Modbus 服务端启动: {interface}:{port}")
                return True, "服务端启动成功"

            except Exception as e:
                logger.exception(f"Modbus 服务端启动异常: {e}")
                return False, str(e)

    def stop(self, server_id: str = 'default') -> Tuple[bool, str]:
        """停止服务端"""
        with self.lock:
            if server_id not in self.servers:
                return False, "服务端不存在"

            try:
                # 标记为停止
                self.servers[server_id]['running'] = False

                # 清理数据存储
                if server_id in self.datastores:
                    del self.datastores[server_id]

                logger.info(f"Modbus 服务端停止: {server_id}")
                return True, "服务端已停止"

            except Exception as e:
                logger.exception(f"Modbus 服务端停止异常: {e}")
                return False, str(e)

    def status(self, server_id: str = 'default') -> Dict:
        """获取服务端状态"""
        with self.lock:
            if server_id in self.servers:
                return {
                    'running': self.servers[server_id].get('running', False),
                    'port': self.servers[server_id].get('port'),
                    'interface': self.servers[server_id].get('interface'),
                    'start_time': self.servers[server_id].get('start_time')
                }
            return {'running': False}

    def get_data(self, server_id: str = 'default', function_code: int = 3,
                address: int = 0, count: int = 1) -> Tuple[bool, List]:
        """获取数据存储中的数据"""
        with self.lock:
            if server_id not in self.datastores:
                return False, []

            datastore = self.datastores[server_id]

            try:
                if function_code == 1:  # 线圈
                    values = datastore['coils'].getValues(address, count)
                    return True, [1 if v else 0 for v in values]

                elif function_code == 2:  # 离散输入
                    values = datastore['discrete_inputs'].getValues(address, count)
                    return True, [1 if v else 0 for v in values]

                elif function_code == 3:  # 保持寄存器
                    values = datastore['holding_registers'].getValues(address, count)
                    return True, list(values)

                elif function_code == 4:  # 输入寄存器
                    values = datastore['input_registers'].getValues(address, count)
                    return True, list(values)

                else:
                    return False, []

            except Exception as e:
                logger.exception(f"获取数据异常: {e}")
                return False, []

    def set_data(self, server_id: str = 'default', function_code: int = 3,
                address: int = 0, values: List = []) -> Tuple[bool, str]:
        """设置数据存储中的数据"""
        with self.lock:
            if server_id not in self.datastores:
                return False, "服务端不存在"

            if not values:
                return False, "值不能为空"

            datastore = self.datastores[server_id]

            try:
                if function_code == 1:  # 线圈
                    datastore['coils'].setValues(address, [bool(v) for v in values])

                elif function_code == 3:  # 保持寄存器
                    datastore['holding_registers'].setValues(address, [int(v) for v in values])

                else:
                    return False, f"不支持的功能码: {function_code}"

                logger.info(f"设置数据成功: 功能码={function_code}, 地址={address}")
                return True, "设置成功"

            except Exception as e:
                logger.exception(f"设置数据异常: {e}")
                return False, str(e)


# 全局服务端实例
modbus_server = ModbusServer()