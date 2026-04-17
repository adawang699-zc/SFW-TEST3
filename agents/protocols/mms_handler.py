# -*- coding: utf-8 -*-
"""
mms_handler.py - MMS/IEC 61850 协议处理器
使用 pyiec61850 编译绑定（libiec61850 C 库）

提供客户端和服务端功能：
- 客户端: 读取/写入 MMS 变量（IEC 61850 数据模型）
- 服务端: 模拟 IED 设备（智能电子设备）

关键设计：
- pyiec61850 需要手动编译 libiec61850 C 库并启用 Python 绑定
- 如果库不可用，所有操作返回清晰的错误消息和构建指南
- 服务器运行在独立线程中，防止阻塞 Flask

参考: libiec61850-1.6, IEC 61850 标准
"""

import threading
import time
import logging
import sys
import os
from typing import Dict, Any, Optional, Tuple, Union

# MMS 协议端口（IEC 61850 默认）
MMS_PORT = 102

# MMS 变量路径格式说明
# 格式: "LogicalDevice/LogicalNode$FunctionalConstraint$DataAttribute"
# 示例: "MMS_SIMDevice1/GGIO1$ST$Ind1$stVal"
# LogicalNode 类型: GGIO (Generic I/O), MMXU (Measuring), XSWI (Switch), etc.
# FunctionalConstraint: ST (Status), MX (Measurable), CO (Control), SP (Setting)

# 搜索 pyiec61850 的可能路径
PYIEC61850_SEARCH_PATHS = [
    # 当前目录下的 libiec61850 构建
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "libiec61850-1.6", "build", "pyiec61850"),
    # apps/MMS 目录下的构建
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "MMS", "libiec61850-1.6", "build", "pyiec61850"),
    # 项目根目录下的构建
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "libiec61850-1.6", "build", "pyiec61850"),
    # 系统已安装（pip install pyiec61850）
    None,  # 使用默认 sys.path
]

# 检测 pyiec61850 是否可用
MMS_AVAILABLE = False
MMS_LIB_ERROR = ""
iec61850 = None

# 尝试导入 pyiec61850
for _search_path in PYIEC61850_SEARCH_PATHS:
    if _search_path is not None and os.path.isdir(_search_path):
        sys.path.insert(0, _search_path)
        print(f"[MMS] 添加搜索路径: {_search_path}")

try:
    import pyiec61850 as iec61850
    MMS_AVAILABLE = True
    print("[OK] pyiec61850 imported successfully - MMS/IEC 61850 available")
except ImportError as e:
    MMS_LIB_ERROR = str(e)
    print(f"[WARNING] pyiec61850 not available - MMS functionality disabled: {e}")
    print("[MMS] Build instructions:")
    print("  1. Download libiec61850-1.6 from: https://github.com/mz-automation/libiec61850")
    print("  2. Build with CMake: cmake -DBUILD_PYTHON_BINDINGS=ON ..")
    print("  3. Copy pyiec61850 to packet_agent/ or install via pip")
except Exception as e:
    MMS_LIB_ERROR = str(e)
    print(f"[WARNING] pyiec61850 import error - MMS functionality disabled: {e}")


class MmsHandler:
    """
    MMS/IEC 61850 协议处理器

    使用 pyiec61850 编译绑定。如果库不可用，所有方法返回清晰的错误消息。
    服务器运行在独立线程中，使用锁保护服务器实例管理。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._server_thread: Optional[threading.Thread] = None
        self._ied_server: Optional[Any] = None  # pyiec61850.IedServer
        self._ied_model: Optional[Any] = None  # pyiec61850.IedModel
        self._running: bool = False
        self._host: str = "0.0.0.0"
        self._port: int = MMS_PORT
        self._ied_name: str = "MMS_SIM"
        self._start_time: Optional[str] = None

        # 配置日志
        self._logger = logging.getLogger('mms_handler')
        self._logger.setLevel(logging.INFO)

    def is_available(self) -> bool:
        """检查 MMS 库是否可用"""
        return MMS_AVAILABLE

    def get_error(self) -> str:
        """获取库不可用时的错误信息"""
        return MMS_LIB_ERROR

    def get_build_instructions(self) -> str:
        """获取构建指南"""
        return (
            "pyiec61850 not available. Build libiec61850 with Python bindings:\n"
            "  1. Download: https://github.com/mz-automation/libiec61850\n"
            "  2. Build: cmake -DBUILD_PYTHON_BINDINGS=ON .. && cmake --build .\n"
            "  3. Copy pyiec61850 module to packet_agent/ or pip install"
        )

    def start_server(self, host: str = "0.0.0.0", port: int = MMS_PORT,
                     ied_name: str = "MMS_SIM",
                     config: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        启动 MMS/IEC 61850 服务端（IED 模拟器）

        Args:
            host: 绑定地址
            port: MMS 端口 (默认 102)
            ied_name: IED 设备名称
            config: 数据模型配置（可选）

        Returns:
            (成功标志, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, self.get_build_instructions())

        with self._lock:
            if self._running:
                return (False, "MMS 服务端已在运行")

            try:
                self._host = host
                self._port = port
                self._ied_name = ied_name
                self._start_time = time.strftime("%Y-%m-%d %H:%M:%S")

                # 创建 IED 数据模型
                self._ied_model = iec61850.IedModel_create(ied_name)

                # 创建逻辑设备（LogicalDevice）
                ld_name = f"{ied_name}Device1"
                logical_device = iec61850.LogicalDevice_create(ld_name, self._ied_model)

                # 创建逻辑节点（根据配置或默认）
                if config and 'logical_nodes' in config:
                    for ln_config in config['logical_nodes']:
                        ln_type = ln_config.get('type', 'GGIO')
                        ln_name = ln_config.get('name', 'GGIO1')
                        # 创建逻辑节点
                        ln = iec61850.LogicalNode_create(ln_name, logical_device)
                        # 创建数据对象（根据类型）
                        if ln_type == 'GGIO':
                            # Generic I/O - 创建指示器
                            for i in range(1, 5):
                                iec61850.DataObject_create(f"Ind{i}", ln, False)
                else:
                    # 默认配置: GGIO (Generic I/O)
                    ln_ggio = iec61850.LogicalNode_create("GGIO1", logical_device)
                    # 创建数据对象
                    for i in range(1, 5):
                        iec61850.DataObject_create(f"Ind{i}", ln_ggio, False)

                # 创建 IedServer
                self._ied_server = iec61850.IedServer_create(self._ied_model)

                # 启动服务器线程
                self._running = True
                self._server_thread = threading.Thread(
                    target=self._run_server_loop,
                    daemon=True
                )
                self._server_thread.start()

                # 等待服务器启动
                time.sleep(0.5)

                # 检查是否成功启动
                if iec61850.IedServer_isRunning(self._ied_server):
                    self._logger.info(f"MMS server started: {host}:{port}, IED: {ied_name}")
                    return (True, f"MMS/IEC 61850 服务端启动成功: {host}:{port}, IED: {ied_name}")
                else:
                    self._running = False
                    return (False, "MMS 服务端启动失败（IedServer 未运行）")

            except Exception as e:
                self._running = False
                self._logger.error(f"MMS server start error: {e}")
                return (False, f"启动失败: {e}")

    def _run_server_loop(self):
        """
        运行 MMS 服务端循环（后台线程）
        """
        try:
            # 启动 IedServer
            iec61850.IedServer_start(self._ied_server, self._host, self._port)

            self._logger.info(f"MMS server listening on {self._host}:{self._port}")

            # 保持运行直到停止信号
            while self._running and iec61850.IedServer_isRunning(self._ied_server):
                time.sleep(0.1)

        except Exception as e:
            self._logger.error(f"MMS server loop error: {e}")
            import traceback
            traceback.print_exc()
            self._running = False
        finally:
            # 清理
            if self._ied_server:
                try:
                    iec61850.IedServer_stop(self._ied_server)
                    iec61850.IedServer_destroy(self._ied_server)
                except:
                    pass
            if self._ied_model:
                try:
                    iec61850.IedModel_destroy(self._ied_model)
                except:
                    pass
            self._ied_server = None
            self._ied_model = None
            self._running = False

    def stop_server(self) -> Tuple[bool, str]:
        """
        停止 MMS 服务端

        Returns:
            (成功标志, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, self.get_build_instructions())

        with self._lock:
            if not self._running:
                return (False, "MMS 服务端未运行")

            self._running = False

            # 等待服务器线程结束
            if self._server_thread:
                self._server_thread.join(timeout=5)

            self._server_thread = None
            self._logger.info("MMS server stopped")

            return (True, "MMS/IEC 61850 服务端已停止")

    def status(self) -> Dict[str, Any]:
        """
        获取服务端状态

        Returns:
            状态字典
        """
        with self._lock:
            is_running = False
            if MMS_AVAILABLE and self._ied_server:
                try:
                    is_running = iec61850.IedServer_isRunning(self._ied_server)
                except:
                    is_running = self._running

            return {
                'running': is_running,
                'available': MMS_AVAILABLE,
                'host': self._host,
                'port': self._port,
                'ied_name': self._ied_name,
                'start_time': self._start_time,
                'error': MMS_LIB_ERROR if not MMS_AVAILABLE else None,
            }

    def client_read(self, host: str, port: int = MMS_PORT,
                    domain: str = None, item: str = None) -> Tuple[bool, Any, str]:
        """
        MMS 客户端读取变量

        Args:
            host: 目标主机
            port: 目标端口
            domain: 域名（LogicalDevice 名称）
            item: 变量项（格式: LogicalNode$FC$DataAttribute）

        Returns:
            (成功标志, 数据, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, None, self.get_build_instructions())

        try:
            # 创建连接
            conn = iec61850.MmsConnection_create()
            iec61850.MmsConnection_setRemoteAddress(conn, host, port)

            # 连接服务器
            if not iec61850.MmsConnection_connect(conn):
                iec61850.MmsConnection_destroy(conn)
                return (False, None, f"连接失败: {host}:{port}")

            # 设置默认值
            if domain is None:
                domain = f"{self._ied_name}Device1"
            if item is None:
                item = "GGIO1$ST$Ind1$stVal"

            # 读取变量
            value = iec61850.MmsConnection_readVariable(conn, domain, item)

            # 关闭连接
            iec61850.MmsConnection_destroy(conn)

            self._logger.info(f"MMS read success: {host}:{port}, {domain}/{item}")

            # 转换值为可序列化格式
            if value is None:
                return (True, None, "读取成功（值为空）")
            elif hasattr(value, 'value'):
                return (True, value.value, "读取成功")
            else:
                return (True, str(value), "读取成功")

        except Exception as e:
            self._logger.error(f"MMS read error: {e}")
            return (False, None, f"读取失败: {e}")

    def client_write(self, host: str, port: int = MMS_PORT,
                     domain: str = None, item: str = None,
                     value: Any = None) -> Tuple[bool, str]:
        """
        MMS 客户端写入变量

        Args:
            host: 目标主机
            port: 目标端口
            domain: 域名（LogicalDevice 名称）
            item: 变量项（格式: LogicalNode$FC$DataAttribute）
            value: 要写入的值

        Returns:
            (成功标志, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, self.get_build_instructions())

        try:
            # 创建连接
            conn = iec61850.MmsConnection_create()
            iec61850.MmsConnection_setRemoteAddress(conn, host, port)

            # 连接服务器
            if not iec61850.MmsConnection_connect(conn):
                iec61850.MmsConnection_destroy(conn)
                return (False, f"连接失败: {host}:{port}")

            # 设置默认值
            if domain is None:
                domain = f"{self._ied_name}Device1"
            if item is None:
                item = "GGIO1$ST$Ind1$stVal"

            # 写入变量
            # 注意：pyiec61850 的 writeVariable API 可能因版本不同而变化
            # 这里使用通用方法
            if isinstance(value, bool):
                # 布尔值
                mms_value = iec61850.MmsValue_newBoolean(value)
            elif isinstance(value, int):
                # 整数值
                mms_value = iec61850.MmsValue_newInteger(value)
            elif isinstance(value, float):
                # 浮点值
                mms_value = iec61850.MmsValue_newFloat(value)
            else:
                # 默认为字符串
                mms_value = iec61850.MmsValue_newVisibleString(str(value))

            # 执行写入
            result = iec61850.MmsConnection_writeVariable(conn, domain, item, mms_value)

            # 清理
            iec61850.MmsValue_delete(mms_value)
            iec61850.MmsConnection_destroy(conn)

            if result:
                self._logger.info(f"MMS write success: {host}:{port}, {domain}/{item}, value={value}")
                return (True, "写入成功")
            else:
                return (False, "写入失败（服务器拒绝或变量不存在）")

        except Exception as e:
            self._logger.error(f"MMS write error: {e}")
            return (False, f"写入失败: {e}")

    def client_connect(self, host: str, port: int = MMS_PORT) -> Tuple[bool, str]:
        """
        MMS 客户端测试连接

        Args:
            host: 目标主机
            port: 目标端口

        Returns:
            (成功标志, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, self.get_build_instructions())

        try:
            conn = iec61850.MmsConnection_create()
            iec61850.MmsConnection_setRemoteAddress(conn, host, port)

            if iec61850.MmsConnection_connect(conn):
                iec61850.MmsConnection_destroy(conn)
                return (True, f"连接成功: {host}:{port}")
            else:
                iec61850.MmsConnection_destroy(conn)
                return (False, f"连接失败: {host}:{port}")

        except Exception as e:
            return (False, f"连接异常: {e}")

    def get_domain_list(self, host: str, port: int = MMS_PORT) -> Tuple[bool, Any, str]:
        """
        获取服务器域名列表（LogicalDevice）

        Args:
            host: 目标主机
            port: 目标端口

        Returns:
            (成功标志, 域名列表, 消息)
        """
        if not MMS_AVAILABLE:
            return (False, None, self.get_build_instructions())

        try:
            conn = iec61850.MmsConnection_create()
            iec61850.MmsConnection_setRemoteAddress(conn, host, port)

            if not iec61850.MmsConnection_connect(conn):
                iec61850.MmsConnection_destroy(conn)
                return (False, None, f"连接失败: {host}:{port}")

            # 获取域名列表
            domains = iec61850.MmsConnection_getDomainNames(conn)

            iec61850.MmsConnection_destroy(conn)

            # 转换为列表
            domain_list = []
            if domains:
                # pyiec61850 返回的是 LinkedList，需要遍历
                # 具体 API 可能因版本不同
                try:
                    iterator = iec61850.LinkedList_getNext(domains)
                    while iterator:
                        element = iec61850.LinkedList_getData(iterator)
                        if element:
                            domain_list.append(str(element))
                        iterator = iec61850.LinkedList_getNext(iterator)
                except:
                    # 简化处理
                    domain_list = [str(domains)]

            return (True, domain_list, "获取域名列表成功")

        except Exception as e:
            return (False, None, f"获取域名列表失败: {e}")


# 导出的类和常量
__all__ = [
    'MmsHandler',
    'MMS_AVAILABLE',
    'MMS_LIB_ERROR',
    'MMS_PORT',
    'iec61850',  # 直接导出 pyiec61850 模块供高级用法
]