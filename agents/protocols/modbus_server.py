#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus TCP 服务端
使用 pymodbus 实现 Modbus TCP 服务端，参照 djangoProject 实现
支持 pymodbus 2.x 和 3.x 版本
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
PYMODBUS_VERSION = "0.0.0"

# pymodbus 类
ModbusTcpServer = None
AsyncModbusTcpServer = None
SyncModbusTcpServer = None
ModbusServerContext = None
ModbusSequentialDataBlock = None
ModbusDeviceContext = None
ModbusException = None

try:
    import pymodbus
    PYMODBUS_VERSION = getattr(pymodbus, '__version__', '0.0.0')
    logger.info(f"检测到 pymodbus 版本: {PYMODBUS_VERSION}")
    PYMODBUS_AVAILABLE = True
except ImportError:
    logger.warning("pymodbus 未安装，Modbus 服务端功能将不可用")

# 解析版本号
def get_pymodbus_version_major() -> int:
    """获取 pymodbus 主版本号"""
    try:
        return int(PYMODBUS_VERSION.split('.')[0])
    except (IndexError, ValueError):
        return 2

PYMODBUS_MAJOR_VERSION = get_pymodbus_version_major() if PYMODBUS_AVAILABLE else 2

# 导入具体类
if PYMODBUS_AVAILABLE:
    try:
        from pymodbus.client import ModbusTcpClient as ClientModbusTcpClient
        logger.info("ModbusTcpClient 导入成功")
    except ImportError:
        pass

    # 服务器导入
    if PYMODBUS_MAJOR_VERSION >= 3:
        try:
            from pymodbus.server import ModbusTcpServer
            logger.info("ModbusTcpServer 导入成功 (pymodbus 3.x)")
        except ImportError:
            try:
                from pymodbus.server.async_io import ModbusTcpServer
                logger.info("ModbusTcpServer 导入成功 (from async_io)")
            except ImportError as e:
                logger.warning(f"ModbusTcpServer 导入失败: {e}")
                PYMODBUS_AVAILABLE = False
    else:
        try:
            from pymodbus.server.sync import ModbusTcpServer as SyncModbusTcpServer
            ModbusTcpServer = SyncModbusTcpServer
            logger.info("SyncModbusTcpServer 导入成功 (pymodbus 2.x)")
        except ImportError as e:
            logger.warning(f"SyncModbusTcpServer 导入失败: {e}")
            PYMODBUS_AVAILABLE = False

    # 数据存储导入
    try:
        from pymodbus.datastore import (
            ModbusServerContext,
            ModbusSequentialDataBlock,
            ModbusDeviceContext
        )
        logger.info("ModbusDeviceContext, ModbusServerContext, ModbusSequentialDataBlock 导入成功")
    except ImportError:
        try:
            from pymodbus.datastore.context import (
                ModbusServerContext,
                ModbusDeviceContext
            )
            from pymodbus.datastore.store import ModbusSequentialDataBlock
            logger.info("数据存储导入成功 (备用路径)")
        except ImportError as e:
            logger.warning(f"数据存储导入失败: {e}")
            PYMODBUS_AVAILABLE = False

    # 异常类导入
    try:
        from pymodbus.exceptions import ModbusException
        logger.info("ModbusException 导入成功")
    except ImportError:
        ModbusException = Exception


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

    if level == 'INFO':
        logger.info(f"{message} - {details}")
    elif level == 'WARNING':
        logger.warning(f"{message} - {details}")
    elif level == 'ERROR':
        logger.error(f"{message} - {details}")
    else:
        logger.debug(f"{message} - {details}")


class ModbusServer:
    """Modbus TCP 服务端 - 完整实现"""

    def __init__(self):
        self.servers: Dict[str, dict] = {}
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
                    'unit_id': unit_id,
                    'pymodbus_version': PYMODBUS_VERSION
                })

                # 1. 创建数据块（pymodbus 3.13.0 要求地址 >= 1）
                # 使用地址 1 作为起始地址
                coils = ModbusSequentialDataBlock(1, [0] * 65536)
                discrete_inputs = ModbusSequentialDataBlock(1, [0] * 65536)
                holding_registers = ModbusSequentialDataBlock(1, [0] * 65536)
                input_registers = ModbusSequentialDataBlock(1, [0] * 65536)

                add_modbus_log('DEBUG', '数据块创建完成', {
                    'coils_size': 65536,
                    'holding_registers_size': 65536
                })

                # 2. 创建从站上下文 - 关键：使用 zero_mode=True
                try:
                    store = ModbusDeviceContext(
                        di=discrete_inputs,
                        co=coils,
                        hr=holding_registers,
                        ir=input_registers,
                        zero_mode=True  # 启用零地址模式，确保地址映射正确
                    )
                    add_modbus_log('INFO', 'ModbusDeviceContext 初始化成功', {'zero_mode': True})
                except TypeError:
                    # 如果不支持 zero_mode 参数
                    store = ModbusDeviceContext(
                        di=discrete_inputs,
                        co=coils,
                        hr=holding_registers,
                        ir=input_registers
                    )
                    store.zero_mode = False  # 标记实际模式
                    add_modbus_log('WARNING', 'ModbusDeviceContext 不支持 zero_mode 参数', {})

                # 3. 创建服务器上下文
                if PYMODBUS_MAJOR_VERSION >= 3:
                    try:
                        context = ModbusServerContext(slaves={unit_id: store}, single=False)
                        add_modbus_log('INFO', 'ModbusServerContext 初始化成功', {'slaves': {unit_id: store}})
                    except TypeError:
                        try:
                            context = ModbusServerContext(devices=store, single=True)
                            add_modbus_log('INFO', 'ModbusServerContext 使用 devices 参数', {})
                        except TypeError:
                            context = ModbusServerContext(slaves=store, single=True)
                            add_modbus_log('INFO', 'ModbusServerContext 使用 single=True', {})
                else:
                    context = ModbusServerContext(slaves=store, single=True)

                # 用于传递 server 实例的事件
                server_ready = threading.Event()
                server_error = [None]
                server_loop = [None]
                server_instance = [None]

                # 启动服务端（异步）
                async def create_and_run_server():
                    try:
                        server = ModbusTcpServer(
                            context=context,
                            address=(interface, port)
                        )

                        server_instance[0] = server
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
                    finally:
                        if server_instance[0]:
                            add_modbus_log('INFO', '服务端停止', {'server_id': server_id})

                # 在后台线程中运行异步服务器
                def run_async_thread():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    server_loop[0] = loop
                    try:
                        loop.run_until_complete(create_and_run_server())
                    except Exception as e:
                        server_error[0] = str(e)
                        server_ready.set()
                        add_modbus_log('ERROR', '事件循环异常', {'error': str(e)})
                    finally:
                        loop.close()

                thread = threading.Thread(target=run_async_thread, daemon=True, name=f'modbus-server-{server_id}')
                thread.start()

                # 等待服务器准备好（最多10秒）
                if not server_ready.wait(timeout=10):
                    add_modbus_log('ERROR', '服务端启动超时', {})
                    return False, "服务端启动超时"

                if server_error[0]:
                    return False, server_error[0]

                # 保存服务器信息
                self.servers[server_id] = {
                    'running': True,
                    'port': port,
                    'interface': interface,
                    'unit_id': unit_id,
                    'context': context,
                    'store': store,  # 保存 store 引用以便直接访问数据
                    'loop': server_loop[0],
                    'server': server_instance[0],
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

                server_info = self.servers[server_id]
                server_info['running'] = False

                server = server_info.get('server')
                loop = server_info.get('loop')

                if loop and loop.is_running() and server:
                    async def do_shutdown():
                        try:
                            if hasattr(server, 'shutdown'):
                                await server.shutdown()
                        except Exception as e:
                            add_modbus_log('WARNING', 'shutdown 异常', {'error': str(e)})

                    asyncio.run_coroutine_threadsafe(do_shutdown(), loop)

                # 等待线程结束
                thread = server_info.get('thread')
                if thread and thread.is_alive():
                    thread.join(timeout=3)

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
                    'start_time': self.servers[server_id].get('start_time'),
                    'success': True
                }
            return {'running': False, 'success': True}

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
        """获取数据存储中的数据 - 使用 getValues 方法"""
        with self.lock:
            if server_id not in self.servers:
                add_modbus_log('WARNING', '获取数据失败: 服务端不存在', {'server_id': server_id})
                return False, []

            server_info = self.servers[server_id]
            store = server_info['store']

            try:
                # 地址映射：前端地址转 Modbus 内部地址
                # 数据块起始地址为 1，所以需要减 1
                # FC1: 地址 1-9999 -> 内部地址 0-9998
                # FC2: 地址 10001-19999 -> 内部地址 0-9998
                # FC3: 地址 40001-49999 -> 内部地址 0-9998
                # FC4: 地址 30001-39999 -> 内部地址 0-9998

                actual_address = address
                if function_code == 1:
                    # 线圈地址从 1 开始，映射到内部地址 0
                    actual_address = address - 1
                elif function_code == 2:
                    # 离散输入地址从 10001 开始
                    if address >= 10001:
                        actual_address = address - 10001
                elif function_code == 3:
                    # 保持寄存器地址从 40001 开始
                    if address >= 40001:
                        actual_address = address - 40001
                elif function_code == 4:
                    # 输入寄存器地址从 30001 开始
                    if address >= 30001:
                        actual_address = address - 30001

                # 确保 address 参数符合 pymodbus 要求（从 1 开始）
                # getValues/setValues 内部使用 simdata，地址需要加 1
                internal_address = actual_address + 1

                # 使用 getValues 方法获取数据
                values = store.getValues(function_code, actual_address, count)

                add_modbus_log('INFO', '获取数据成功', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'frontend_address': address,
                    'actual_address': actual_address,
                    'count': count,
                    'result': values[:10] if len(values) > 10 else values
                })
                return True, list(values)

            except Exception as e:
                add_modbus_log('ERROR', '获取数据异常', {'error': str(e)})
                logger.exception(f"获取数据异常: {e}")
                return False, []

    def set_data(self, server_id: str = 'default', function_code: int = 3,
                address: int = 0, values: List = []) -> Tuple[bool, str]:
        """设置数据存储中的数据 - 使用 setValues 方法"""
        with self.lock:
            if server_id not in self.servers:
                add_modbus_log('WARNING', '设置数据失败: 服务端不存在', {'server_id': server_id})
                return False, "服务端不存在"

            if not values:
                add_modbus_log('WARNING', '设置数据失败: 值不能为空', {})
                return False, "值不能为空"

            server_info = self.servers[server_id]
            store = server_info['store']

            try:
                # 地址映射
                actual_address = address
                if function_code == 1:
                    if address >= 1:
                        actual_address = address - 1
                elif function_code == 2:
                    if address >= 10001:
                        actual_address = address - 10001
                elif function_code == 3:
                    if address >= 40001:
                        actual_address = address - 40001
                elif function_code == 4:
                    if address >= 30001:
                        actual_address = address - 30001

                if actual_address < 0:
                    actual_address = 0

                # 处理值类型
                processed_values = []
                if function_code in [1, 2]:
                    processed_values = [bool(int(v)) for v in values]
                else:
                    processed_values = [int(v) for v in values]

                # 使用 setValues 方法设置数据
                store.setValues(function_code, actual_address, processed_values)

                # 验证设置是否成功
                verify_values = store.getValues(function_code, actual_address, min(5, len(processed_values)))

                add_modbus_log('INFO', '设置数据成功', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'frontend_address': address,
                    'actual_address': actual_address,
                    'count': len(values),
                    'values': processed_values[:10] if len(processed_values) > 10 else processed_values,
                    'verify_values': list(verify_values)
                })
                return True, "设置成功"

            except Exception as e:
                add_modbus_log('ERROR', '设置数据异常', {'error': str(e)})
                logger.exception(f"设置数据异常: {e}")
                return False, str(e)

    def bulk_set_data(self, server_id: str = 'default', function_code: int = 3,
                     address: int = 0, values: List = []) -> Tuple[bool, str]:
        """批量设置数据（用于随机和重置）"""
        return self.set_data(server_id, function_code, address, values)


# 全局服务端实例
modbus_server = ModbusServer()