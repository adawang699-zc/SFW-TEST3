#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC UA 客户端
使用 asyncua 库实现，支持：
- 连接管理
- 节点浏览
- 数据读写
- 历史数据查询
"""

import asyncio
import threading
import time
import logging
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime

from .opcua_common import (
    OPCUA_AVAILABLE,
    OPCUA_LIB_ERROR,
    get_install_instructions
)

logger = logging.getLogger(__name__)


class OpcUaClient:
    """OPC UA 客户端"""

    def __init__(self):
        self._lock = threading.Lock()
        self._client: Optional[Any] = None  # asyncua.Client
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._connected: bool = False

        # 配置
        self._endpoint: str = ""
        self._security_mode: str = "None"
        self._connect_time: Optional[str] = None

    def is_available(self) -> bool:
        """检查库是否可用"""
        return OPCUA_AVAILABLE

    def get_error(self) -> str:
        """获取错误信息"""
        return OPCUA_LIB_ERROR

    def get_install_instructions(self) -> str:
        """获取安装指南"""
        return get_install_instructions()

    async def _connect_async(self, endpoint: str, security_mode: str = "None"):
        """异步连接"""
        try:
            from asyncua import Client, ua

            self._client = Client(endpoint)

            # 安全模式配置（测试环境使用 None）
            # TODO: 生产环境需要证书配置

            await self._client.connect()
            self._connected = True
            self._endpoint = endpoint
            self._security_mode = security_mode
            self._connect_time = time.strftime("%Y-%m-%d %H:%M:%S")

            logger.info(f"OPC UA 客户端连接成功: {endpoint}")

            # 保持连接
            while self._connected:
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"连接异常: {e}")
            self._connected = False

    def connect(self, endpoint: str, security_mode: str = "None") -> Tuple[bool, str]:
        """连接服务器"""
        if not OPCUA_AVAILABLE:
            return (False, self.get_install_instructions())

        with self._lock:
            if self._connected:
                return (False, "已连接")

            try:
                self._loop = asyncio.new_event_loop()

                def run_loop():
                    asyncio.set_event_loop(self._loop)
                    self._loop.run_until_complete(self._connect_async(endpoint, security_mode))

                self._thread = threading.Thread(target=run_loop, daemon=True, name="opcua-client")
                self._thread.start()

                time.sleep(2)

                if self._connected:
                    return (True, f"连接成功: {endpoint}")
                return (False, "连接失败")

            except Exception as e:
                return (False, f"连接异常: {e}")

    async def _disconnect_async(self):
        """异步断开"""
        if self._client:
            try:
                await self._client.disconnect()
                logger.info(f"OPC UA 客户端断开: {self._endpoint}")
            except:
                pass

    def disconnect(self) -> Tuple[bool, str]:
        """断开连接"""
        with self._lock:
            if not self._connected:
                return (False, "未连接")

            self._connected = False

            if self._loop:
                try:
                    asyncio.run_coroutine_threadsafe(self._disconnect_async(), self._loop)
                except:
                    pass

            if self._thread:
                self._thread.join(timeout=3)

            self._thread = None
            self._loop = None
            self._client = None

            return (True, "已断开")

    async def _browse_async(self, node_id: str = "Objects") -> List[Dict]:
        """异步浏览节点"""
        if not self._client:
            return []

        try:
            from asyncua import ua

            if node_id == "Objects":
                node = self._client.get_objects_node()
            else:
                node = self._client.get_node(node_id)

            logger.info(f"浏览节点: {node_id}, node={node}, nodeid={node.nodeid}")
            children = await node.get_children()
            logger.info(f"子节点数量: {len(children)})")
            result = []

            for child in children:
                browse_name = await child.read_browse_name()
                node_class = await child.read_node_class()
                display_name = await child.read_display_name()

                # 将 NodeId 转换为标准 OPC UA 格式字符串
                # 格式: ns=<namespace>;i=<identifier> 或 ns=<namespace>;s=<identifier>
                nid = child.nodeid
                if nid.NamespaceIndex == 0:
                    # 默认命名空间
                    if hasattr(nid.Identifier, '__iter__') and not isinstance(nid.Identifier, str):
                        # 字节串类型，转换为字符串
                        node_id_str = f"i={nid.Identifier}"
                    elif isinstance(nid.Identifier, int):
                        node_id_str = f"i={nid.Identifier}"
                    elif isinstance(nid.Identifier, str):
                        node_id_str = f"s={nid.Identifier}"
                    else:
                        node_id_str = str(nid)
                else:
                    if isinstance(nid.Identifier, int):
                        node_id_str = f"ns={nid.NamespaceIndex};i={nid.Identifier}"
                    elif isinstance(nid.Identifier, str):
                        node_id_str = f"ns={nid.NamespaceIndex};s={nid.Identifier}"
                    else:
                        node_id_str = f"ns={nid.NamespaceIndex};{str(nid.Identifier)}"

                result.append({
                    "node_id": node_id_str,
                    "browse_name": browse_name.Name,
                    "node_class": str(node_class),
                    "display_name": display_name.Text if hasattr(display_name, 'Text') else str(display_name)
                })

            return result

        except Exception as e:
            logger.error(f"浏览节点异常: {e}", exc_info=True)
            return []

    def browse(self, node_id: str = "Objects") -> Tuple[bool, List, str]:
        """浏览节点"""
        if not self._connected:
            return (False, [], "未连接")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._browse_async(node_id), self._loop
            )
            result = future.result(timeout=10)
            return (True, result, "浏览成功")
        except Exception as e:
            return (False, [], f"浏览失败: {e}")

    async def _read_async(self, node_id: str) -> Tuple[bool, Any, str]:
        """异步读取"""
        if not self._client:
            return (False, None, "客户端未初始化")

        try:
            node = self._client.get_node(node_id)
            value = await node.read_value()
            return (True, value, "读取成功")
        except Exception as e:
            return (False, None, f"读取失败: {e}")

    def read(self, node_id: str) -> Tuple[bool, Any, str]:
        """读取节点值"""
        if not self._connected:
            return (False, None, "未连接")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._read_async(node_id), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, None, f"读取超时: {e}")

    async def _write_async(self, node_id: str, value: Any) -> Tuple[bool, str]:
        """异步写入"""
        if not self._client:
            return (False, "客户端未初始化")

        try:
            from asyncua import ua
            node = self._client.get_node(node_id)

            # 获取节点的数据类型
            try:
                data_type = await node.read_data_type()
                data_type_value = data_type.Value if hasattr(data_type, 'Value') else data_type

                # 根据数据类型转换值
                if data_type_value == ua.VariantType.Float or data_type_value == ua.VariantType.Double:
                    value = float(value)
                elif data_type_value == ua.VariantType.Int32 or data_type_value == ua.VariantType.Int16 or data_type_value == ua.VariantType.Int64:
                    value = int(float(value))  # 先转 float 再转 int，处理字符串数字
                elif data_type_value == ua.VariantType.Boolean:
                    if isinstance(value, str):
                        value = value.lower() in ('true', '1', 'yes')
                    else:
                        value = bool(value)
                elif data_type_value == ua.VariantType.String:
                    value = str(value)

            except Exception:
                # 如果读取类型失败，尝试智能转换
                if isinstance(value, str):
                    if value.lower() in ('true', 'false'):
                        value = value.lower() == 'true'
                    elif '.' in value:
                        value = float(value)
                    else:
                        try:
                            value = int(value)
                        except ValueError:
                            pass  # 保持字符串

            await node.write_value(value)
            return (True, "写入成功")
        except Exception as e:
            return (False, f"写入失败: {e}")

    def write(self, node_id: str, value: Any) -> Tuple[bool, str]:
        """写入节点值"""
        if not self._connected:
            return (False, "未连接")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._write_async(node_id, value), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, f"写入超时: {e}")

    async def _read_history_async(self, node_id: str, start_time: datetime,
                                    end_time: datetime) -> Tuple[bool, List, str]:
        """异步读取历史数据"""
        if not self._client:
            return (False, [], "客户端未初始化")

        try:
            node = self._client.get_node(node_id)
            history = await node.read_raw_history(start_time, end_time)

            result = []
            for hv in history:
                result.append({
                    "value": hv.Value.Value if hasattr(hv, 'Value') else hv,
                    "timestamp": str(hv.SourceTimestamp) if hasattr(hv, 'SourceTimestamp') else None,
                    "quality": str(hv.StatusCode) if hasattr(hv, 'StatusCode') else "Good"
                })

            return (True, result, "读取成功")
        except Exception as e:
            return (False, [], f"读取历史失败: {e}")

    def read_history(self, node_id: str, start_time: datetime,
                     end_time: datetime) -> Tuple[bool, List, str]:
        """查询历史数据"""
        if not self._connected:
            return (False, [], "未连接")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._read_history_async(node_id, start_time, end_time), self._loop
            )
            return future.result(timeout=30)
        except Exception as e:
            return (False, [], f"查询超时: {e}")

    async def _call_method_async(self, object_node: str, method_name: str,
                                  args: List = None) -> Tuple[bool, Any, str]:
        """异步调用方法"""
        if not self._client:
            return (False, None, "客户端未初始化")

        try:
            from asyncua import ua
            obj = self._client.get_node(object_node)

            # 方法节点通过 BrowsePath 获取: ["2:方法名"]
            browse_names = [ua.QualifiedName(method_name, 2)]
            method_node = await obj.get_child(browse_names)

            result = await obj.call_method(method_node, *(args or []))
            return (True, result, "调用成功")
        except Exception as e:
            logger.error(f"方法调用失败: {e}", exc_info=True)
            return (False, None, f"调用失败: {e}")

    def call_method(self, object_node: str, method_name: str,
                    args: List = None) -> Tuple[bool, Any, str]:
        """调用方法"""
        if not self._connected:
            return (False, None, "未连接")

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._call_method_async(object_node, method_name, args), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, None, f"调用超时: {e}")

    async def _find_servers_async(self, endpoint: str) -> Tuple[bool, List, str]:
        """异步发现服务器"""
        try:
            from asyncua import Client
            # 临时连接获取服务器列表
            client = Client(endpoint)
            await client.connect()
            servers = await client.find_servers()
            await client.disconnect()
            result_list = []
            for s in servers:
                # ApplicationDescription 有 ApplicationName 属性 (LocalizedText)
                name = s.ApplicationName.Text if hasattr(s.ApplicationName, 'Text') else str(s.ApplicationName)
                uri = s.ApplicationUri if hasattr(s, 'ApplicationUri') else ''
                result_list.append({'name': name, 'uri': uri})
            return (True, result_list, "发现成功")
        except Exception as e:
            return (False, [], f"发现失败: {e}")

    def find_servers(self, endpoint: str) -> Tuple[bool, List, str]:
        """发现服务器"""
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._find_servers_async(endpoint))
            loop.close()
            return result
        except Exception as e:
            return (False, [], f"发现失败: {e}")

    async def _get_endpoints_async(self, endpoint: str) -> Tuple[bool, List, str]:
        """异步获取端点"""
        try:
            from asyncua import Client
            client = Client(endpoint)
            await client.connect()
            endpoints = await client.get_endpoints()
            await client.disconnect()
            result = []
            for e in endpoints:
                result.append({
                    'endpoint_url': e.EndpointUrl if hasattr(e, 'EndpointUrl') else str(e),
                    'security_mode': str(e.SecurityMode) if hasattr(e, 'SecurityMode') else 'None'
                })
            return (True, result, "获取成功")
        except Exception as e:
            return (False, [], f"获取失败: {e}")

    def get_endpoints(self, endpoint: str) -> Tuple[bool, List, str]:
        """获取端点"""
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._get_endpoints_async(endpoint))
            loop.close()
            return result
        except Exception as e:
            return (False, [], f"获取失败: {e}")

    # 订阅管理
    _subscription: Optional[Any] = None
    _monitored_items: Dict[str, Any] = {}
    _subscription_handler: Optional[Any] = None  # 订阅数据处理器

    async def _create_subscription_async(self, interval: int) -> Tuple[bool, Any, str]:
        """异步创建订阅"""
        if not self._client:
            return (False, None, "未连接")
        try:
            # 创建简单的订阅处理器
            class SubHandler:
                """订阅数据变化处理器"""
                def datachange_notification(self, node, val, data):
                    logger.info(f"数据变化: {node} -> {val}")

            handler = SubHandler()
            self._subscription_handler = handler
            self._subscription = await self._client.create_subscription(interval, handler)
            return (True, str(self._subscription), "订阅创建成功")
        except Exception as e:
            return (False, None, f"创建失败: {e}")

    def create_subscription(self, interval: int) -> Tuple[bool, Any, str]:
        """创建订阅"""
        if not self._connected:
            return (False, None, "未连接")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._create_subscription_async(interval), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, None, f"创建超时: {e}")

    async def _create_monitored_item_async(self, node_id: str) -> Tuple[bool, Any, str]:
        """异步创建监控项"""
        if not self._client or not self._subscription:
            return (False, None, "未创建订阅")
        try:
            node = self._client.get_node(node_id)
            handle = await self._subscription.subscribe_data_change(node)
            self._monitored_items[node_id] = handle
            return (True, str(handle), "监控项创建成功")
        except Exception as e:
            return (False, None, f"创建失败: {e}")

    def create_monitored_item(self, node_id: str) -> Tuple[bool, Any, str]:
        """创建监控项"""
        if not self._connected:
            return (False, None, "未连接")
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._create_monitored_item_async(node_id), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, None, f"创建超时: {e}")

    async def _delete_subscription_async(self) -> Tuple[bool, str]:
        """异步删除订阅"""
        if not self._subscription:
            return (False, "没有订阅")
        try:
            await self._subscription.delete()
            self._subscription = None
            self._monitored_items = {}
            return (True, "订阅已删除")
        except Exception as e:
            return (False, f"删除失败: {e}")

    def delete_subscription(self) -> Tuple[bool, str]:
        """删除订阅"""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._delete_subscription_async(), self._loop
            )
            return future.result(timeout=10)
        except Exception as e:
            return (False, f"删除超时: {e}")

    def status(self) -> Dict[str, Any]:
        """获取状态"""
        with self._lock:
            return {
                "connected": self._connected,
                "endpoint": self._endpoint,
                "security_mode": self._security_mode,
                "connect_time": self._connect_time,
                "available": OPCUA_AVAILABLE,
                "error": OPCUA_LIB_ERROR if not OPCUA_AVAILABLE else None
            }


# 全局实例
opcua_client = OpcUaClient()

__all__ = ['OpcUaClient', 'opcua_client']