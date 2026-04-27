#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus TCP 服务端
使用 pymodbus 实现 Modbus TCP 服务端，支持日志记录
"""

import logging
import threading
import asyncio
import socket
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)

# 尝试导入 pymodbus
PYMODBUS_AVAILABLE = False
try:
    from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusDeviceContext
    PYMODBUS_AVAILABLE = True
    logger.info("pymodbus datastore 导入成功")
except ImportError:
    logger.warning("pymodbus 未安装，Modbus 服务端功能将不可用")


# Modbus 操作日志存储
modbus_server_logs = deque(maxlen=1000)
modbus_server_log_lock = threading.Lock()


def add_modbus_log(level: str, message: str, details: dict = None):
    """添加 Modbus 操作日志"""
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'level': level,
        'message': message,
        'details': details or {}
    }
    with modbus_server_log_lock:
        modbus_server_logs.append(log_entry)

    # 同时写入 logger
    if level == 'INFO':
        logger.info(f"{message} - {details}")
    elif level == 'WARNING':
        logger.warning(f"{message} - {details}")
    elif level == 'ERROR':
        logger.error(f"{message} - {details}")
    else:
        logger.debug(f"{message} - {details}")


class LoggingDataBlock(ModbusSequentialDataBlock):
    """带日志记录的数据块"""

    def __init__(self, address, values, block_name='unknown'):
        # pymodbus 3.x: address 是起始地址，values 是数据列表
        # 使用 address=1 避免 0 地址问题（某些版本可能不支持）
        super().__init__(address, values)
        self.block_name = block_name
        self._start_address = address

    def getValues(self, address, count=1):
        """读取数据时记录日志"""
        # 调整地址到内部索引
        idx = address - self._start_address
        if idx < 0 or idx + count > len(self.values):
            return [0] * count
        values = self.values[idx:idx + count]
        add_modbus_log('INFO', f'读取 {self.block_name}', {
            'address': address,
            'count': count,
            'values': list(values)[:10] if len(values) > 10 else list(values)
        })
        return values

    def setValues(self, address, values):
        """写入数据时记录日志"""
        idx = address - self._start_address
        if idx < 0 or idx + len(values) > len(self.values):
            return
        for i, v in enumerate(values):
            self.values[idx + i] = v
        add_modbus_log('INFO', f'写入 {self.block_name}', {
            'address': address,
            'count': len(values),
            'values': list(values)[:10] if len(values) > 10 else list(values)
        })


class ModbusServer:
    """Modbus TCP 服务端"""

    def __init__(self):
        self.servers: Dict[str, dict] = {}
        self.datastores: Dict[str, dict] = {}
        self.loops: Dict[str, asyncio.AbstractEventLoop] = {}
        self.server_instances: Dict[str, object] = {}
        self.lock = threading.Lock()

    def _check_port_available(self, interface: str, port: int) -> bool:
        """检查端口是否可用"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((interface, port))
            sock.close()
            return True
        except OSError:
            return False

    def start(self, server_id: str = 'default', port: int = 502,
              interface: str = '0.0.0.0', unit_id: int = 1) -> Tuple[bool, str]:
        """启动 Modbus 服务端"""
        if not PYMODBUS_AVAILABLE:
            add_modbus_log('ERROR', 'pymodbus 未安装', {})
            return False, "pymodbus 未安装"

        with self.lock:
            if server_id in self.servers and self.servers[server_id].get('running'):
                add_modbus_log('WARNING', '服务端已在运行', {'server_id': server_id})
                return False, "服务端已在运行"

            # 检查端口
            if not self._check_port_available(interface, port):
                add_modbus_log('ERROR', '端口不可用', {'port': port, 'interface': interface})
                return False, f"端口 {port} 不可用或已被占用"

            try:
                add_modbus_log('INFO', '开始启动 Modbus 服务端', {
                    'server_id': server_id,
                    'interface': interface,
                    'port': port,
                    'unit_id': unit_id
                })

                # 创建带日志的数据存储（使用 address=1 避免 pymodbus 3.x 的地址验证问题）
                coils = LoggingDataBlock(1, [0] * 10000, '线圈')
                discrete_inputs = LoggingDataBlock(1, [0] * 10000, '离散输入')
                holding_registers = LoggingDataBlock(1, [0] * 10000, '保持寄存器')
                input_registers = LoggingDataBlock(1, [0] * 10000, '输入寄存器')

                # 创建从站上下文
                slave_context = ModbusDeviceContext(
                    coils=coils,
                    discrete_inputs=discrete_inputs,
                    holding_registers=holding_registers,
                    input_registers=input_registers
                )

                # 创建服务端上下文
                server_context = ModbusServerContext(slaves=slave_context, single=True)

                # 保存数据存储
                self.datastores[server_id] = {
                    'coils': coils,
                    'discrete_inputs': discrete_inputs,
                    'holding_registers': holding_registers,
                    'input_registers': input_registers,
                    'context': server_context
                }

                # 用于传递 server 实例的事件
                server_ready = threading.Event()
                server_error = [None]

                # 启动服务端（异步）
                async def run_server():
                    from pymodbus.server import AsyncModbusTcpServer
                    try:
                        server = AsyncModbusTcpServer(
                            context=server_context,
                            address=(interface, port)
                        )

                        # 保存 server 实例
                        self.server_instances[server_id] = server

                        # 通知主线程服务器已准备好
                        server_ready.set()

                        add_modbus_log('INFO', 'Modbus 服务端开始监听', {
                            'interface': interface,
                            'port': port
                        })

                        await server.serve_forever()

                    except Exception as e:
                        server_error[0] = str(e)
                        server_ready.set()
                        add_modbus_log('ERROR', '服务端启动异常', {'error': str(e)})

                # 在后台线程中运行
                def run_async():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self.loops[server_id] = loop
                    try:
                        loop.run_until_complete(run_server())
                    except Exception as e:
                        add_modbus_log('ERROR', '事件循环异常', {'error': str(e)})
                    finally:
                        loop.close()

                thread = threading.Thread(target=run_async, daemon=True, name=f'modbus-server-{server_id}')
                thread.start()

                # 等待服务器准备好（最多5秒）
                if not server_ready.wait(timeout=5):
                    add_modbus_log('ERROR', '服务端启动超时', {})
                    return False, "服务端启动超时"

                if server_error[0]:
                    return False, server_error[0]

                # 验证端口是否真正监听
                import time
                time.sleep(0.5)
                try:
                    test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    test_sock.settimeout(1)
                    result = test_sock.connect_ex((interface if interface != '0.0.0.0' else '127.0.0.1', port))
                    test_sock.close()
                    if result != 0:
                        add_modbus_log('WARNING', '端口监听验证失败', {'port': port, 'result': result})
                except:
                    pass

                self.servers[server_id] = {
                    'running': True,
                    'port': port,
                    'interface': interface,
                    'unit_id': unit_id,
                    'thread': thread,
                    'start_time': datetime.now().isoformat()
                }

                add_modbus_log('INFO', 'Modbus 服务端启动成功', {
                    'server_id': server_id,
                    'port': port,
                    'interface': interface
                })
                return True, "服务端启动成功"

            except Exception as e:
                add_modbus_log('ERROR', '服务端启动异常', {'error': str(e)})
                logger.exception(f"Modbus 服务端启动异常: {e}")
                return False, str(e)

    def stop(self, server_id: str = 'default') -> Tuple[bool, str]:
        """停止服务端"""
        with self.lock:
            if server_id not in self.servers:
                add_modbus_log('WARNING', '服务端不存在', {'server_id': server_id})
                return False, "服务端不存在"

            try:
                add_modbus_log('INFO', '开始停止 Modbus 服务端', {'server_id': server_id})

                # 标记为停止
                self.servers[server_id]['running'] = False

                # 停止异步服务器
                if server_id in self.server_instances:
                    server = self.server_instances[server_id]
                    loop = self.loops.get(server_id)

                    if loop and loop.is_running():
                        # 在事件循环中调用 shutdown
                        async def do_shutdown():
                            try:
                                if hasattr(server, 'shutdown'):
                                    await server.shutdown()
                            except Exception as e:
                                add_modbus_log('WARNING', 'shutdown 异常', {'error': str(e)})

                        asyncio.run_coroutine_threadsafe(do_shutdown(), loop)

                    del self.server_instances[server_id]

                if server_id in self.loops:
                    loop = self.loops[server_id]
                    if loop.is_running():
                        loop.call_soon_threadsafe(loop.stop)
                    del self.loops[server_id]

                # 清理数据存储
                if server_id in self.datastores:
                    del self.datastores[server_id]

                del self.servers[server_id]

                add_modbus_log('INFO', 'Modbus 服务端已停止', {'server_id': server_id})
                return True, "服务端已停止"

            except Exception as e:
                add_modbus_log('ERROR', '服务端停止异常', {'error': str(e)})
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
                    'unit_id': self.servers[server_id].get('unit_id'),
                    'start_time': self.servers[server_id].get('start_time')
                }
            return {'running': False}

    def get_logs(self, limit: int = 100) -> List[Dict]:
        """获取操作日志"""
        with modbus_server_log_lock:
            return list(modbus_server_logs)[-limit:]

    def clear_logs(self):
        """清空日志"""
        with modbus_server_log_lock:
            modbus_server_logs.clear()

    def get_data(self, server_id: str = 'default', function_code: int = 3,
                address: int = 0, count: int = 1) -> Tuple[bool, List]:
        """获取数据存储中的数据"""
        with self.lock:
            if server_id not in self.datastores:
                add_modbus_log('WARNING', '获取数据失败: 服务端不存在', {'server_id': server_id})
                return False, []

            datastore = self.datastores[server_id]

            try:
                if function_code == 1:  # 线圈
                    values = datastore['coils'].getValues(address, count)
                    result = [1 if v else 0 for v in values]

                elif function_code == 2:  # 离散输入
                    values = datastore['discrete_inputs'].getValues(address, count)
                    result = [1 if v else 0 for v in values]

                elif function_code == 3:  # 保持寄存器
                    values = datastore['holding_registers'].getValues(address, count)
                    result = list(values)

                elif function_code == 4:  # 输入寄存器
                    values = datastore['input_registers'].getValues(address, count)
                    result = list(values)

                else:
                    add_modbus_log('WARNING', '不支持的功能码', {'function_code': function_code})
                    return False, []

                add_modbus_log('INFO', '获取数据', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'address': address,
                    'count': count,
                    'result_count': len(result)
                })
                return True, result

            except Exception as e:
                add_modbus_log('ERROR', '获取数据异常', {'error': str(e)})
                logger.exception(f"获取数据异常: {e}")
                return False, []

    def set_data(self, server_id: str = 'default', function_code: int = 3,
                address: int = 0, values: List = []) -> Tuple[bool, str]:
        """设置数据存储中的数据"""
        with self.lock:
            if server_id not in self.datastores:
                add_modbus_log('WARNING', '设置数据失败: 服务端不存在', {'server_id': server_id})
                return False, "服务端不存在"

            if not values:
                add_modbus_log('WARNING', '设置数据失败: 值不能为空', {})
                return False, "值不能为空"

            datastore = self.datastores[server_id]

            try:
                if function_code == 1:  # 线圈
                    datastore['coils'].setValues(address, [bool(v) for v in values])

                elif function_code == 3:  # 保持寄存器
                    datastore['holding_registers'].setValues(address, [int(v) for v in values])

                else:
                    add_modbus_log('WARNING', '不支持的功能码', {'function_code': function_code})
                    return False, f"不支持的功能码: {function_code}"

                add_modbus_log('INFO', '设置数据成功', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'address': address,
                    'count': len(values)
                })
                return True, "设置成功"

            except Exception as e:
                add_modbus_log('ERROR', '设置数据异常', {'error': str(e)})
                logger.exception(f"设置数据异常: {e}")
                return False, str(e)


# 全局服务端实例
modbus_server = ModbusServer()