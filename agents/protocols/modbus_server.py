#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modbus TCP 服务端
使用 pymodbus 实现 Modbus TCP 服务端
支持 pymodbus 2.x 和 3.x 版本（使用旧 API）
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
ModbusServerContext = None
ModbusSequentialDataBlock = None
ModbusSlaveContext = None
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
    # 服务器导入
    try:
        from pymodbus.server import ModbusTcpServer
        logger.info("ModbusTcpServer 导入成功")
    except ImportError:
        try:
            from pymodbus.server.async_io import ModbusTcpServer
            logger.info("ModbusTcpServer 导入成功 (from async_io)")
        except ImportError as e:
            logger.warning(f"ModbusTcpServer 导入失败: {e}")
            PYMODBUS_AVAILABLE = False

    # 数据存储导入（pymodbus 3.7.4 API）
    try:
        from pymodbus.datastore import (
            ModbusServerContext,
            ModbusSequentialDataBlock,
            ModbusSlaveContext
        )
        logger.info("ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock 导入成功")
    except ImportError:
        try:
            from pymodbus.datastore.context import (
                ModbusServerContext,
                ModbusSlaveContext
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

                # 1. 创建数据块（pymodbus 3.7.4 API）
                # ModbusSequentialDataBlock(address, values)
                # address=0 表示 zero mode，地址直接映射
                coils = ModbusSequentialDataBlock(0, [False] * 10000)
                discrete_inputs = ModbusSequentialDataBlock(0, [False] * 10000)
                holding_registers = ModbusSequentialDataBlock(0, [0] * 10000)
                input_registers = ModbusSequentialDataBlock(0, [0] * 10000)

                add_modbus_log('DEBUG', '数据块创建完成', {
                    'coils_size': 10000,
                    'holding_registers_size': 10000
                })

                # 2. 创建从站上下文（pymodbus 3.7.4 使用 ModbusSlaveContext）
                store = ModbusSlaveContext(
                    di=discrete_inputs,
                    co=coils,
                    hr=holding_registers,
                    ir=input_registers
                )
                add_modbus_log('INFO', 'ModbusSlaveContext 初始化成功', {})

                # 3. 创建服务器上下文
                # pymodbus 3.7.4: 使用 slaves 参数
                try:
                    context = ModbusServerContext(slaves={unit_id: store}, single=False)
                    add_modbus_log('INFO', 'ModbusServerContext 初始化成功', {'slaves': {unit_id: store}})
                except TypeError:
                    # pymodbus 3.x 使用 devices 参数
                    try:
                        context = ModbusServerContext(devices={unit_id: store}, single=False)
                        add_modbus_log('INFO', 'ModbusServerContext 使用 devices 参数', {})
                    except TypeError:
                        context = ModbusServerContext(slaves=store, single=True)
                        add_modbus_log('INFO', 'ModbusServerContext 使用 single=True', {})

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

                # 保存服务器信息和数据块引用
                self.servers[server_id] = {
                    'running': True,
                    'port': port,
                    'interface': interface,
                    'unit_id': unit_id,
                    'context': context,
                    'store': store,  # ModbusDeviceContext 引用
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
                # zero mode 下地址直接映射，不需要偏移
                actual_address = address
                if actual_address < 0:
                    actual_address = 0

                # 使用 getValues 方法获取数据
                values = store.getValues(function_code, actual_address, count)

                # 线圈/离散输入返回布尔值转为整数
                if function_code in [1, 2]:
                    values = [1 if v else 0 for v in values]

                add_modbus_log('INFO', '获取数据成功', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'frontend_address': address,
                    'actual_address': actual_address,
                    'count': count,
                    'result': list(values)[:10]
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
                # zero mode 下地址直接映射，不需要偏移
                actual_address = address
                if actual_address < 0:
                    actual_address = 0

                # 处理值类型
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
                    'values': processed_values[:10],
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