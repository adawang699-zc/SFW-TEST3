#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC UA 服务端模拟器
使用 asyncua 库实现，支持：
- 数据访问 (DA)
- 历史数据访问 (HDA)
- 报警与事件 (A&E)
"""

import asyncio
import threading
import time
import logging
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime

from .opcua_common import (
    OPCUA_PORT,
    OPCUA_NAMESPACE,
    OPCUA_AVAILABLE,
    OPCUA_LIB_ERROR,
    DEFAULT_VARIABLES,
    generate_simulated_value,
    check_alarm_trigger,
    HistoryBuffer,
    get_install_instructions
)

logger = logging.getLogger(__name__)


class OpcUaServer:
    """OPC UA 服务端模拟器"""

    def __init__(self):
        self._lock = threading.Lock()
        self._server: Optional[Any] = None  # asyncua.Server
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._update_task: Optional[asyncio.Task] = None

        # 配置
        self._host: str = "0.0.0.0"
        self._port: int = OPCUA_PORT
        self._server_name: str = "OPC UA Simulator"
        self._namespace: str = OPCUA_NAMESPACE
        self._idx: int = 2
        self._start_time: Optional[str] = None

        # 数据更新间隔
        self._update_interval: float = 1.0

        # 数据存储
        self._datastore: Dict[str, Dict] = {}  # 变量配置
        self._nodes: Dict[str, Any] = {}  # 节点引用
        self._history_buffers: Dict[str, HistoryBuffer] = {}
        self._history_max_size: int = 10000

        # 报警状态
        self._alarm_active: bool = False

        # 启动时间戳（用于计算 elapsed_time）
        self._start_timestamp: float = 0

    def is_available(self) -> bool:
        """检查库是否可用"""
        return OPCUA_AVAILABLE

    def get_error(self) -> str:
        """获取错误信息"""
        return OPCUA_LIB_ERROR

    def get_install_instructions(self) -> str:
        """获取安装指南"""
        return get_install_instructions()

    async def _create_address_space(self):
        """创建地址空间"""
        # 获取对象节点
        objects = self._server.get_objects_node()

        # 创建模拟设备对象
        sim_device = await objects.add_object(self._idx, "SimulationDevice")

        # 初始化数据存储
        self._datastore = {}
        self._nodes = {}
        self._history_buffers = {}

        # 创建变量节点
        for var_config in DEFAULT_VARIABLES:
            name = var_config['name']
            var_type = var_config['type']
            mode = var_config['mode']
            params = var_config['params']

            # 根据类型确定初始值
            if var_type == 'Float':
                initial_value = params.get('base', 0.0)
            elif var_type == 'Boolean':
                initial_value = False
            elif var_type == 'Int32':
                initial_value = params.get('value', 0)
            else:
                initial_value = 0

            # 创建变量节点
            node = await sim_device.add_variable(self._idx, name, initial_value)
            await node.set_writable()

            # 存储节点引用和配置
            self._nodes[name] = node
            self._datastore[name] = {
                'type': var_type,
                'mode': mode,
                'params': params,
                'node': node
            }

            # 创建历史数据缓冲（仅 Float 和 Int32 类型）
            if var_type in ('Float', 'Int32'):
                self._history_buffers[name] = HistoryBuffer(self._history_max_size)

        # 创建方法 - ResetCounter
        from asyncua.common.methods import uamethod

        @uamethod
        def reset_counter(parent):
            """重置计数器"""
            self._datastore['Counter']['params']['value'] = 0
            return [0]

        await sim_device.add_method(self._idx, "ResetCounter", reset_counter)

        # 创建方法 - SetMode
        @uamethod
        def set_mode(parent, mode_value: int):
            """设置运行模式"""
            self._datastore['Mode']['params']['value'] = mode_value
            return [mode_value]

        await sim_device.add_method(self._idx, "SetMode", set_mode,
            [asyncua.ua.Argument("ModeValue", asyncua.ua.VariantType.Int32)],
            [asyncua.ua.Argument("Result", asyncua.ua.VariantType.Int32)])

        logger.info(f"地址空间创建完成，包含 {len(self._nodes)} 个变量")

    async def _update_data_loop(self):
        """数据更新循环"""
        self._start_timestamp = time.time()

        while self._running:
            try:
                elapsed = time.time() - self._start_timestamp

                # 更新每个变量
                for name, config in self._datastore.items():
                    if config['mode'] == 'alarm':
                        continue  # 报警状态由温度逻辑控制

                    # 生成模拟值
                    value = generate_simulated_value(config, elapsed)

                    # 写入节点
                    node = self._nodes.get(name)
                    if node:
                        await node.write_value(value)

                    # 存储历史数据
                    if name in self._history_buffers:
                        self._history_buffers[name].append(value)

                # 特殊处理：报警状态
                temperature_config = self._datastore.get('Temperature')
                if temperature_config:
                    temp_node = self._nodes.get('Temperature')
                    if temp_node:
                        temp_value = await temp_node.read_value()

                        alarm_config = self._datastore.get('AlarmActive', {}).get('params', {})
                        alarm_action = check_alarm_trigger(temp_value, alarm_config)

                        if alarm_action == 'trigger' and not self._alarm_active:
                            self._alarm_active = True
                            alarm_node = self._nodes.get('AlarmActive')
                            if alarm_node:
                                await alarm_node.write_value(True)
                            logger.warning(f"报警触发: 温度 {temp_value:.2f} > {alarm_config.get('trigger_threshold')}")

                        elif alarm_action == 'clear' and self._alarm_active:
                            self._alarm_active = False
                            alarm_node = self._nodes.get('AlarmActive')
                            if alarm_node:
                                await alarm_node.write_value(False)
                            logger.info(f"报警清除: 温度 {temp_value:.2f} < {alarm_config.get('clear_threshold')}")

                await asyncio.sleep(self._update_interval)

            except Exception as e:
                logger.error(f"数据更新异常: {e}")
                await asyncio.sleep(1)

    async def _run_server(self):
        """运行服务器"""
        try:
            from asyncua import Server, ua

            # 创建服务器实例
            self._server = Server()
            await self._server.init()

            # 配置服务器
            self._server.set_endpoint(f"opc.tcp://{self._host}:{self._port}/")
            self._server.set_server_name(self._server_name)

            # 注册命名空间
            self._idx = await self._server.register_namespace(self._namespace)

            # 创建地址空间
            await self._create_address_space()

            # 启动服务器
            await self._server.start()

            self._running = True
            self._start_time = time.strftime("%Y-%m-%d %H:%M:%S")

            logger.info(f"OPC UA 服务端启动: opc.tcp://{self._host}:{self._port}/")

            # 启动数据更新任务
            self._update_task = asyncio.create_task(self._update_data_loop())

            # 保持运行
            while self._running:
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"服务器运行异常: {e}")
            self._running = False
        finally:
            if self._server:
                try:
                    await self._server.stop()
                    logger.info("OPC UA 服务端已停止")
                except:
                    pass
            self._server = None

    def start(self, host: str = "0.0.0.0", port: int = OPCUA_PORT,
              server_name: str = "OPC UA Simulator",
              update_interval: float = 1.0,
              history_size: int = 10000) -> Tuple[bool, str]:
        """启动服务端"""
        if not OPCUA_AVAILABLE:
            return (False, self.get_install_instructions())

        with self._lock:
            if self._running:
                return (False, "服务端已在运行")

            self._host = host
            self._port = port
            self._server_name = server_name
            self._update_interval = update_interval
            self._history_max_size = history_size

            try:
                # 创建事件循环和线程
                self._loop = asyncio.new_event_loop()

                def run_loop():
                    asyncio.set_event_loop(self._loop)
                    self._loop.run_until_complete(self._run_server())

                self._thread = threading.Thread(target=run_loop, daemon=True, name="opcua-server")
                self._thread.start()

                # 等待启动
                time.sleep(2)

                if self._running:
                    return (True, f"OPC UA 服务端启动成功: opc.tcp://{host}:{port}/")
                else:
                    return (False, "服务端启动失败")

            except Exception as e:
                logger.error(f"启动异常: {e}")
                return (False, f"启动异常: {e}")

    def stop(self) -> Tuple[bool, str]:
        """停止服务端"""
        with self._lock:
            if not self._running:
                return (False, "服务端未运行")

            self._running = False

            # 取消更新任务
            if self._update_task and self._loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self._update_task.cancel(), self._loop
                    )
                except:
                    pass

            # 等待线程结束
            if self._thread:
                self._thread.join(timeout=5)

            self._thread = None
            self._loop = None
            self._server = None

            return (True, "OPC UA 服务端已停止")

    def status(self) -> Dict[str, Any]:
        """获取状态"""
        with self._lock:
            return {
                "running": self._running,
                "available": OPCUA_AVAILABLE,
                "host": self._host,
                "port": self._port,
                "endpoint": f"opc.tcp://{self._host}:{self._port}/" if self._running else None,
                "server_name": self._server_name,
                "start_time": self._start_time,
                "variables": list(self._nodes.keys()),
                "history_size": self._history_max_size,
                "error": OPCUA_LIB_ERROR if not OPCUA_AVAILABLE else None
            }

    def get_variables(self) -> List[Dict]:
        """获取变量列表"""
        result = []
        for name, config in self._datastore.items():
            result.append({
                "name": name,
                "type": config.get('type'),
                "mode": config.get('mode'),
                "description": config.get('params', {}).get('description', name)
            })
        return result

    def get_history(self, variable: str, count: int = 100) -> List[Dict]:
        """获取历史数据"""
        if variable in self._history_buffers:
            return self._history_buffers[variable].get_records(count)
        return []


# 全局实例
opcua_server = OpcUaServer()

__all__ = ['OpcUaServer', 'opcua_server']