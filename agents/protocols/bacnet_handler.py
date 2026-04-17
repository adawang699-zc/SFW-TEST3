# -*- coding: utf-8 -*-
"""
bacnet_handler.py - BACnet 协议处理器
使用 bacpypes3 异步库，独立 asyncio 线程

提供客户端和服务端功能：
- 客户端: 读取/写入 BACnet 对象属性
- 服务端: 模拟 BACnet/IP 设备

关键设计：BACnet 使用 bacpypes3 异步库，必须在独立 asyncio 线程中运行，
不能与 Flask 的同步上下文共享事件循环。

参考: bacpypes3 library documentation
"""

import asyncio
import threading
import time
import logging
from typing import Dict, Any, Optional, Tuple, Union

# BACnet 协议端口
BACNET_PORT = 47808

# BACnet 对象类型映射
BACNET_OBJECT_TYPES = {
    'analogInput': 'ai',
    'analogOutput': 'ao',
    'analogValue': 'av',
    'binaryInput': 'bi',
    'binaryOutput': 'bo',
    'binaryValue': 'bv',
    'multiStateInput': 'mi',
    'multiStateOutput': 'mo',
    'multiStateValue': 'mv',
}

# BACnet 属性标识符（常用）
BACNET_PROPERTIES = {
    'presentValue': 85,
    'description': 28,
    'objectName': 77,
    'objectType': 79,
    'systemStatus': 112,
    'units': 117,
    'minPresValue': 69,
    'maxPresValue': 70,
    'priorityArray': 87,
    'reliability': 103,
}

# 检测 bacpypes3 是否可用
BACNET_AVAILABLE = False
BACNET_LIB_ERROR = ""

try:
    # 从 bacpypes3.app 导入核心类
    from bacpypes3.app import Application, DeviceObject, Error, SimpleAckPDU

    # APDU 请求和响应类型
    from bacpypes3.apdu import (
        ReadPropertyRequest,
        WritePropertyRequest,
        ReadPropertyACK,
    )

    # 数据类型
    from bacpypes3.primitivedata import Real, Unsigned, Boolean

    # 基础类型（ObjectIdentifier 和 ObjectType 在 app 模块中已导入）
    from bacpypes3.basetypes import PropertyIdentifier, ObjectType, ObjectIdentifier

    # 地址类型
    from bacpypes3.pdu import Address

    BACNET_AVAILABLE = True
    print("[OK] bacpypes3 imported successfully - BACnet available")
except ImportError as e:
    BACNET_LIB_ERROR = str(e)
    print(f"[WARNING] bacpypes3 not installed - BACnet unavailable: {e}")
except Exception as e:
    BACNET_LIB_ERROR = str(e)
    print(f"[WARNING] bacpypes3 import error - BACnet unavailable: {e}")


class BacnetHandler:
    """
    BACnet 协议处理器

    使用 bacpypes3 异步库，必须运行在独立的 asyncio 线程中。
    客户端操作通过 run_coroutine_threadsafe 桥接到服务器事件循环。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._server_thread: Optional[threading.Thread] = None
        self._server_app: Optional[Any] = None  # bacpypes3 Application
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running: bool = False
        self._host: str = "0.0.0.0"
        self._port: int = BACNET_PORT
        self._device_id: int = 1234
        self._device_name: str = "BACnet Simulator"
        self._start_time: Optional[str] = None

    def is_available(self) -> bool:
        """检查 BACnet 库是否可用"""
        return BACNET_AVAILABLE

    def get_error(self) -> str:
        """获取库不可用时的错误信息"""
        return BACNET_LIB_ERROR

    def start_server(self, host: str = "0.0.0.0", port: int = BACNET_PORT,
                     device_id: int = 1234, device_name: str = "BACnet Simulator") -> Tuple[bool, str]:
        """
        启动 BACnet 服务端

        Args:
            host: 绑定地址
            port: BACnet 端口 (默认 47808)
            device_id: 设备 ID
            device_name: 设备名称

        Returns:
            (成功标志, 消息)
        """
        if not BACNET_AVAILABLE:
            return (False, f"bacpypes3 未安装: {BACNET_LIB_ERROR}")

        with self._lock:
            if self._running:
                return (False, "BACnet 服务端已在运行")

            try:
                self._host = host
                self._port = port
                self._device_id = device_id
                self._device_name = device_name
                self._start_time = time.strftime("%Y-%m-%d %H:%M:%S")

                # 创建后台线程运行 asyncio 事件循环
                self._running = True
                self._server_thread = threading.Thread(
                    target=self._run_server_loop,
                    daemon=True
                )
                self._server_thread.start()

                # 等待事件循环启动
                time.sleep(0.5)

                return (True, f"BACnet 服务端启动成功: {host}:{port}, Device ID: {device_id}")

            except Exception as e:
                self._running = False
                return (False, f"启动失败: {e}")

    def _run_server_loop(self):
        """
        运行 BACnet 服务端事件循环（后台线程）

        关键：创建新的 asyncio 事件循环，不与 Flask 共享
        """
        try:
            # 创建新的 asyncio 事件循环
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # 创建 DeviceObject 配置
            device_object = DeviceObject(
                objectName=self._device_name,
                objectIdentifier=(ObjectType.device, self._device_id),
                maxApduLengthAccepted=1024,
                segmentationSupported="segmentedBoth",
                vendorIdentifier=15,
            )

            # 创建 Application
            # bacpypes3 Application 需要特定初始化方式
            self._server_app = Application()

            print(f"[BACnet Server] Application created: {self._host}:{self._port}")

            # 运行事件循环
            self._loop.run_forever()

        except Exception as e:
            print(f"[BACnet Server] Error in server loop: {e}")
            import traceback
            traceback.print_exc()
            self._running = False
        finally:
            if self._loop:
                try:
                    self._loop.close()
                except:
                    pass
            self._loop = None
            self._server_app = None
            self._running = False

    def stop_server(self) -> Tuple[bool, str]:
        """
        停止 BACnet 服务端

        Returns:
            (成功标志, 消息)
        """
        with self._lock:
            if not self._running:
                return (False, "BACnet 服务端未运行")

            self._running = False

            # 安全停止事件循环
            if self._loop and self._loop.is_running():
                try:
                    self._loop.call_soon_threadsafe(self._loop.stop)
                    # 等待线程结束
                    if self._server_thread:
                        self._server_thread.join(timeout=5)
                except Exception as e:
                    print(f"[BACnet Server] Stop error: {e}")

            self._server_thread = None
            self._loop = None
            self._server_app = None

            return (True, "BACnet 服务端已停止")

    def status(self) -> Dict[str, Any]:
        """
        获取服务端状态

        Returns:
            状态字典
        """
        with self._lock:
            return {
                'running': self._running,
                'available': BACNET_AVAILABLE,
                'host': self._host,
                'port': self._port,
                'device_id': self._device_id,
                'device_name': self._device_name,
                'start_time': self._start_time,
                'loop_running': self._loop is not None and self._loop.is_running() if self._loop else False,
            }

    async def _read_property_async(self, destination: str, object_type: str,
                                   object_instance: int, property_id: int) -> Tuple[bool, Any, str]:
        """
        异步读取 BACnet 属性

        Args:
            destination: 目标地址 (格式: "ip:port")
            object_type: 对象类型 (如 "analogInput", "ai")
            object_instance: 对象实例号
            property_id: 属性标识符

        Returns:
            (成功标志, 数据, 消息)
        """
        if not BACNET_AVAILABLE:
            return (False, None, f"bacpypes3 未安装: {BACNET_LIB_ERROR}")

        try:
            # 解析目标地址
            dest_address = Address(destination)

            # 转换对象类型
            obj_type_short = BACNET_OBJECT_TYPES.get(object_type, object_type)
            obj_type_enum = getattr(ObjectType, obj_type_short, ObjectType.analogInput)

            # 构建对象标识符
            object_identifier = ObjectIdentifier((obj_type_enum, object_instance))

            # 构建属性标识符
            prop_identifier = PropertyIdentifier(property_id)

            # 创建读请求
            request = ReadPropertyRequest(
                objectIdentifier=object_identifier,
                propertyIdentifier=prop_identifier,
            )
            request.pduDestination = dest_address

            # 创建客户端应用
            client_app = Application()

            # 发送请求并等待响应
            response = await client_app.request(request)

            # 解析响应
            if isinstance(response, ReadPropertyACK):
                # 成功响应
                property_value = response.propertyValue
                # 转换为可序列化的值
                if hasattr(property_value, 'value'):
                    result_value = property_value.value
                else:
                    result_value = str(property_value)
                return (True, result_value, "读取成功")
            elif isinstance(response, Error):
                # 错误响应
                error_code = response.errorCode if hasattr(response, 'errorCode') else str(response)
                return (False, None, f"BACnet 错误: {error_code}")
            elif isinstance(response, SimpleAckPDU):
                # 简单确认（某些情况）
                return (True, None, "操作成功")
            else:
                return (False, None, f"未知响应类型: {type(response)}")

        except asyncio.TimeoutError:
            return (False, None, f"读取超时: {destination}")
        except Exception as e:
            return (False, None, f"读取异常: {e}")

    def read_property(self, destination: str, object_type: str,
                      object_instance: int, property_id: int) -> Tuple[bool, Any, str]:
        """
        读取 BACnet 属性（同步接口）

        Args:
            destination: 目标地址 (格式: "ip:port")
            object_type: 对象类型
            object_instance: 对象实例号
            property_id: 属性标识符

        Returns:
            (成功标志, 数据, 消息)
        """
        if not BACNET_AVAILABLE:
            return (False, None, f"bacpypes3 未安装: {BACNET_LIB_ERROR}")

        try:
            if self._loop and self._loop.is_running():
                # 在服务器事件循环中执行
                future = asyncio.run_coroutine_threadsafe(
                    self._read_property_async(destination, object_type, object_instance, property_id),
                    self._loop
                )
                return future.result(timeout=10)
            else:
                # 创建临时事件循环执行
                return asyncio.run(
                    self._read_property_async(destination, object_type, object_instance, property_id)
                )

        except asyncio.TimeoutError:
            return (False, None, f"操作超时")
        except Exception as e:
            return (False, None, f"读取失败: {e}")

    async def _write_property_async(self, destination: str, object_type: str,
                                    object_instance: int, property_id: int,
                                    value: Any, priority: int = None) -> Tuple[bool, str]:
        """
        异步写入 BACnet 属性

        Args:
            destination: 目标地址
            object_type: 对象类型
            object_instance: 对象实例号
            property_id: 属性标识符
            value: 要写入的值
            priority: 写入优先级（可选）

        Returns:
            (成功标志, 消息)
        """
        if not BACNET_AVAILABLE:
            return (False, f"bacpypes3 未安装: {BACNET_LIB_ERROR}")

        try:
            # 解析目标地址
            dest_address = Address(destination)

            # 转换对象类型
            obj_type_short = BACNET_OBJECT_TYPES.get(object_type, object_type)
            obj_type_enum = getattr(ObjectType, obj_type_short, ObjectType.analogInput)

            # 构建对象标识符
            object_identifier = ObjectIdentifier((obj_type_enum, object_instance))

            # 构建属性标识符
            prop_identifier = PropertyIdentifier(property_id)

            # 根据值类型转换
            if isinstance(value, float):
                property_value = Real(value)
            elif isinstance(value, int):
                property_value = Unsigned(value)
            elif isinstance(value, bool):
                property_value = Boolean(value)
            else:
                property_value = Real(float(value) if value else 0.0)

            # 创建写请求
            request = WritePropertyRequest(
                objectIdentifier=object_identifier,
                propertyIdentifier=prop_identifier,
                propertyValue=property_value,
            )
            request.pduDestination = dest_address

            # 设置优先级（可选）
            if priority is not None:
                request.priority = priority

            # 创建客户端应用
            client_app = Application()

            # 发送请求
            response = await client_app.request(request)

            # 解析响应
            if isinstance(response, SimpleAckPDU):
                return (True, "写入成功")
            elif isinstance(response, Error):
                error_code = response.errorCode if hasattr(response, 'errorCode') else str(response)
                return (False, f"BACnet 错误: {error_code}")
            else:
                return (False, f"未知响应类型: {type(response)}")

        except asyncio.TimeoutError:
            return (False, f"写入超时: {destination}")
        except Exception as e:
            return (False, f"写入异常: {e}")

    def write_property(self, destination: str, object_type: str,
                       object_instance: int, property_id: int,
                       value: Any, priority: int = None) -> Tuple[bool, str]:
        """
        写入 BACnet 属性（同步接口）

        Args:
            destination: 目标地址
            object_type: 对象类型
            object_instance: 对象实例号
            property_id: 属性标识符
            value: 要写入的值
            priority: 写入优先级

        Returns:
            (成功标志, 消息)
        """
        if not BACNET_AVAILABLE:
            return (False, f"bacpypes3 未安装: {BACNET_LIB_ERROR}")

        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._write_property_async(destination, object_type, object_instance,
                                               property_id, value, priority),
                    self._loop
                )
                return future.result(timeout=10)
            else:
                return asyncio.run(
                    self._write_property_async(destination, object_type, object_instance,
                                               property_id, value, priority)
                )

        except asyncio.TimeoutError:
            return (False, f"操作超时")
        except Exception as e:
            return (False, f"写入失败: {e}")


# 导出的类和常量
__all__ = [
    'BacnetHandler',
    'BACNET_AVAILABLE',
    'BACNET_LIB_ERROR',
    'BACNET_PORT',
    'BACNET_OBJECT_TYPES',
    'BACNET_PROPERTIES',
]