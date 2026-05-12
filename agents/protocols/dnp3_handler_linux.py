# -*- coding: utf-8 -*-
"""
dnp3_handler_linux.py - DNP3 协议处理器 (pydnp3 跨平台版本)

基于 pydnp3 (OpenDNP3 Python 绑定) 实现，支持 Linux/macOS 平台。

特点:
1. 跨平台支持 (Linux, macOS)
2. 主站 (Master) 和子站 (Outstation) 实现
3. 支持基础功能码 (Read, Write, DirectOperate, SelectAndOperate)

依赖: pydnp3 (需要从源码编译安装)
安装:
    git clone --recursive https://github.com/Kisensum/pydnp3
    cd pydnp3 && python3 setup.py install
"""

import logging
import threading
import time
from typing import Dict, Any, Optional, Tuple, List

# 配置日志
logger = logging.getLogger(__name__)

# pydnp3 可用性检测
DNP3_AVAILABLE = False
PYDNP3_PLATFORM_OK = True  # pydnp3 支持 Linux/macOS

try:
    from pydnp3 import opendnp3, openpal, asiopal, asiodnp3
    DNP3_AVAILABLE = True
    logger.info("[OK] pydnp3 loaded successfully (Linux/macOS cross-platform)")
except ImportError as e:
    logger.warning(f"[WARNING] pydnp3 not installed: {e}")
    logger.warning("Install: git clone --recursive https://github.com/Kisensum/pydnp3 && cd pydnp3 && python3 setup.py install")
except Exception as e:
    logger.error(f"[ERROR] pydnp3 load failed: {e}")
    DNP3_AVAILABLE = False

# 线程锁
_client_lock = threading.Lock()
_server_lock = threading.Lock()


# ========== SOE Handler (Sequence of Events) ==========

class Dnp3SOEHandler(opendnp3.ISOEHandler if DNP3_AVAILABLE else object):
    """处理从子站返回的测量数据"""

    def __init__(self):
        if DNP3_AVAILABLE:
            super().__init__()
        self._data_cache: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def Process(self, info, values):
        """处理测量数据"""
        try:
            data_list = []
            # 使用 Visitor 模式遍历数据
            if hasattr(values, 'Foreach'):
                visitor = DataVisitor()
                values.Foreach(visitor)
                data_list = visitor.get_data()

            with self._lock:
                self._data_cache.extend(data_list)

            logger.debug(f"SOEHandler.Process: gv={info.gv}, headerIndex={info.headerIndex}, count={len(data_list)}")
        except Exception as e:
            logger.error(f"SOEHandler.Process error: {e}")

    def Start(self):
        logger.debug("SOEHandler.Start")

    def End(self):
        logger.debug("SOEHandler.End")

    def get_cached_data(self) -> List[Dict[str, Any]]:
        """获取缓存的数据"""
        with self._lock:
            return self._data_cache.copy()

    def clear_cache(self):
        """清空数据缓存"""
        with self._lock:
            self._data_cache.clear()


class DataVisitor:
    """数据访问器，用于遍历不同类型的测量数据"""

    def __init__(self):
        self._data: List[Dict[str, Any]] = []

    def visit(self, value, index):
        """访问单个数据点"""
        data_type = type(value).__name__
        self._data.append({
            'type': data_type,
            'index': index,
            'value': value.value if hasattr(value, 'value') else str(value),
            'quality': str(value.quality) if hasattr(value, 'quality') else 'GOOD',
            'timestamp': time.time()
        })

    def get_data(self) -> List[Dict[str, Any]]:
        return self._data


# ========== Command Callback ==========

class Dnp3CommandCallback:
    """命令执行回调处理"""

    def __init__(self):
        self._result: Optional[Dict[str, Any]] = None
        self._completed = threading.Event()

    def callback(self, result):
        """处理命令结果"""
        try:
            self._result = {
                'summary': str(result.summary) if hasattr(result, 'summary') else 'UNKNOWN',
                'success': result.summary == opendnp3.TaskCompletion.SUCCESS if DNP3_AVAILABLE else False,
                'details': []
            }

            # 遍历每个命令点的结果
            if hasattr(result, 'ForeachItem'):
                def item_callback(point_result):
                    self._result['details'].append({
                        'header_index': point_result.headerIndex,
                        'index': point_result.index,
                        'state': str(point_result.state),
                        'status': str(point_result.status)
                    })
                result.ForeachItem(item_callback)

            self._completed.set()
            logger.debug(f"CommandCallback: summary={self._result['summary']}")
        except Exception as e:
            logger.error(f"CommandCallback error: {e}")
            self._completed.set()

    def wait_for_result(self, timeout: float = 10.0) -> Dict[str, Any]:
        """等待命令完成并返回结果"""
        self._completed.wait(timeout)
        return self._result or {'summary': 'TIMEOUT', 'success': False, 'details': []}


# ========== Master (主站) ==========

class Dnp3Master:
    """
    DNP3 主站 (Master)

    用于连接 DNP3 子站并发送功能码请求。
    """

    def __init__(self, client_id: str = 'default'):
        self._lock = threading.Lock()
        self._client_id = client_id
        self._manager = None
        self._channel = None
        self._master = None
        self._soe_handler = None
        self._connected = False
        self._host = ""
        self._port = 20000
        self._remote_addr = 10
        self._local_addr = 1
        self._connect_time = None

    def connect(self, host: str, port: int = 20000,
                remote_addr: int = 10, local_addr: int = 1) -> Tuple[bool, str]:
        """
        连接到 DNP3 子站

        Args:
            host: 子站 IP 地址
            port: 子站端口 (默认 20000)
            remote_addr: 子站地址 (默认 10)
            local_addr: 主站地址 (默认 1)

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not installed. Install: pip install pydnp3 (requires compilation)"

        with self._lock:
            try:
                # 断开已有连接
                if self._connected:
                    self._disconnect_internal()

                logger.info(f"Connecting to DNP3 outstation: {host}:{port}")

                # 创建日志处理器
                log_handler = asiodnp3.ConsoleLogger().Create()

                # 创建 DNP3 Manager
                self._manager = asiodnp3.DNP3Manager(1, log_handler)

                # 配置重连参数
                retry_params = asiopal.ChannelRetry().Default()

                # 创建 TCP 客户端通道
                listener = ChannelListener()
                self._channel = self._manager.AddTCPClient(
                    "tcpclient",
                    opendnp3.levels.NORMAL,
                    retry_params,
                    host,
                    "0.0.0.0",
                    port,
                    listener
                )

                # 配置栈
                stack_config = asiodnp3.MasterStackConfig()
                stack_config.master.responseTimeout = openpal.TimeDuration().Seconds(5)
                stack_config.link.RemoteAddr = remote_addr
                stack_config.link.LocalAddr = local_addr

                # 创建 SOE Handler
                self._soe_handler = Dnp3SOEHandler()

                # 创建 Master 应用
                master_app = MasterApplication()

                # 添加 Master 到通道
                self._master = self._channel.AddMaster(
                    "master",
                    self._soe_handler,
                    master_app,
                    stack_config
                )

                # 启用 Master
                self._master.Enable()

                # 等待连接建立
                time.sleep(2)

                self._host = host
                self._port = port
                self._remote_addr = remote_addr
                self._local_addr = local_addr
                self._connected = True
                self._connect_time = time.strftime("%Y-%m-%d %H:%M:%S")

                logger.info(f"DNP3 Master connected: {host}:{port}")
                return True, f"Connected to {host}:{port} (remote_addr={remote_addr}, local_addr={local_addr})"

            except Exception as e:
                logger.error(f"Connection error: {e}")
                self._disconnect_internal()
                return False, f"Connection error: {e}"

    def disconnect(self) -> Tuple[bool, str]:
        """断开连接"""
        with self._lock:
            if not self._connected:
                return False, "Not connected"
            self._disconnect_internal()
            return True, "Disconnected"

    def _disconnect_internal(self):
        """内部断开连接（不加锁）"""
        try:
            if self._master:
                self._master = None
            if self._channel:
                self._channel = None
            if self._manager:
                self._manager.Shutdown()
                self._manager = None
        except Exception as e:
            logger.error(f"Disconnect error: {e}")
        self._connected = False

    def read(self, class_field: int = 0) -> Tuple[bool, str, Any]:
        """
        发送 Read 功能码 (Class0 轮询)

        Args:
            class_field: 类别字段 (默认 0 表示所有类别)

        Returns:
            (成功标志, 消息, 数据)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not available", None

        with self._lock:
            if not self._connected or self._master is None:
                return False, "Not connected", None

            try:
                # 清空缓存
                self._soe_handler.clear_cache()

                # 执行 Class Scan
                if class_field == 0:
                    class_field_obj = opendnp3.ClassField().AllClasses()
                else:
                    class_field_obj = opendnp3.ClassField(class_field)

                # 等待数据返回
                time.sleep(3)

                # 获取缓存数据
                data = self._soe_handler.get_cached_data()

                return True, f"Read completed, received {len(data)} data points", data

            except Exception as e:
                logger.error(f"Read error: {e}")
                return False, str(e), None

    def write(self, value: float, index: int = 0) -> Tuple[bool, str]:
        """
        发送 Write 功能码 (Analog Output)

        Args:
            value: 写入值
            index: 对象索引

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not available"

        with self._lock:
            if not self._connected or self._master is None:
                return False, "Not connected"

            try:
                # 创建 AnalogOutputFloat32 命令
                command = opendnp3.AnalogOutputFloat32(value)

                # 创建回调
                callback = Dnp3CommandCallback()

                # 执行写操作 (使用 SelectAndOperate)
                self._master.SelectAndOperate(
                    command,
                    index,
                    lambda result: callback.callback(result),
                    opendnp3.TaskConfig().Default()
                )

                # 等待结果
                result = callback.wait_for_result()

                if result.get('success'):
                    return True, f"Write completed: index={index}, value={value}"
                else:
                    return False, f"Write failed: {result.get('summary')}"

            except Exception as e:
                logger.error(f"Write error: {e}")
                return False, str(e)

    def direct_operate(self, value: float, index: int = 0) -> Tuple[bool, str]:
        """
        发送 Direct_Operate 功能码

        Args:
            value: 操作值
            index: 对象索引

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not available"

        with self._lock:
            if not self._connected or self._master is None:
                return False, "Not connected"

            try:
                # 创建 AnalogOutputFloat32 命令
                command = opendnp3.AnalogOutputFloat32(value)

                # 创建回调
                callback = Dnp3CommandCallback()

                # 执行 DirectOperate
                self._master.DirectOperate(
                    command,
                    index,
                    lambda result: callback.callback(result),
                    opendnp3.TaskConfig().Default()
                )

                # 等待结果
                result = callback.wait_for_result()

                if result.get('success'):
                    return True, f"DirectOperate completed: index={index}, value={value}"
                else:
                    return False, f"DirectOperate failed: {result.get('summary')}"

            except Exception as e:
                logger.error(f"DirectOperate error: {e}")
                return False, str(e)

    def select_and_operate(self, value: float, index: int = 0) -> Tuple[bool, str]:
        """
        发送 SelectAndOperate 功能码 (先选择再执行)

        Args:
            value: 操作值
            index: 对象索引

        Returns:
            (成功标志, 消息)
        """
        return self.write(value, index)

    def status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        with self._lock:
            return {
                "connected": self._connected,
                "host": self._host,
                "port": self._port,
                "remote_addr": self._remote_addr,
                "local_addr": self._local_addr,
                "client_id": self._client_id,
                "connect_time": self._connect_time,
            }


# ========== Outstation (子站) ==========

class Dnp3Outstation:
    """
    DNP3 子站 (Outstation)

    作为 TCP Server 监听主站连接并响应请求。
    """

    def __init__(self, server_id: str = 'default'):
        self._lock = threading.Lock()
        self._server_id = server_id
        self._manager = None
        self._channel = None
        self._outstation = None
        self._running = False
        self._port = 20000
        self._bind = "0.0.0.0"
        self._database = {}
        self._start_time = None

    def start(self, bind: str = "0.0.0.0", port: int = 20000,
              remote_addr: int = 1, local_addr: int = 10,
              point_count: int = 10) -> Tuple[bool, str]:
        """
        启动 DNP3 子站

        Args:
            bind: 监听地址 (默认 0.0.0.0)
            port: 监听端口 (默认 20000)
            remote_addr: 主站地址 (默认 1)
            local_addr: 子站地址 (默认 10)
            point_count: 数据点数量 (默认 10)

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not installed. Install: pip install pydnp3 (requires compilation)"

        with self._lock:
            try:
                # 停止已有服务
                if self._running:
                    self._stop_internal()

                logger.info(f"Starting DNP3 Outstation: {bind}:{port}")

                # 创建日志处理器
                log_handler = asiodnp3.ConsoleLogger().Create()

                # 创建 DNP3 Manager
                self._manager = asiodnp3.DNP3Manager(1, log_handler)

                # 配置重连参数
                retry_params = asiopal.ChannelRetry().Default()

                # 创建 TCP Server 通道
                listener = ChannelListener()
                self._channel = self._manager.AddTCPServer(
                    "server",
                    opendnp3.levels.NORMAL,
                    retry_params,
                    bind,
                    port,
                    listener
                )

                # 配置栈
                stack_config = asiodnp3.OutstationStackConfig(
                    opendnp3.DatabaseSizes.AllTypes(point_count)
                )
                stack_config.outstation.eventBufferConfig = opendnp3.EventBufferConfig().AllTypes(point_count)
                stack_config.outstation.params.allowUnsolicited = True
                stack_config.link.LocalAddr = local_addr
                stack_config.link.RemoteAddr = remote_addr
                stack_config.link.KeepAliveTimeout = openpal.TimeDuration().Max()

                # 配置数据库
                self._configure_database(stack_config.dbConfig, point_count)

                # 创建命令处理器
                command_handler = OutstationCommandHandler(self)

                # 创建 Outstation 应用
                outstation_app = OutstationApplication(self)

                # 添加 Outstation 到通道
                self._outstation = self._channel.AddOutstation(
                    "outstation",
                    command_handler,
                    outstation_app,
                    stack_config
                )

                # 启用 Outstation
                self._outstation.Enable()

                self._bind = bind
                self._port = port
                self._running = True
                self._start_time = time.strftime("%Y-%m-%d %H:%M:%S")

                logger.info(f"DNP3 Outstation started: {bind}:{port}")
                return True, f"Outstation started on {bind}:{port}"

            except Exception as e:
                logger.error(f"Start error: {e}")
                self._stop_internal()
                return False, f"Start error: {e}"

    def _configure_database(self, db_config, point_count: int):
        """配置数据点"""
        try:
            # 配置 Analog 输入点
            for i in range(min(point_count, 10)):
                db_config.analog[i].clazz = opendnp3.PointClass.Class2
                db_config.analog[i].svariation = opendnp3.StaticAnalogVariation.Group30Var1
                db_config.analog[i].evariation = opendnp3.EventAnalogVariation.Group32Var7

            # 配置 Binary 输入点
            for i in range(min(point_count, 10)):
                db_config.binary[i].clazz = opendnp3.PointClass.Class2
                db_config.binary[i].svariation = opendnp3.StaticBinaryVariation.Group1Var2
                db_config.binary[i].evariation = opendnp3.EventBinaryVariation.Group2Var2

        except Exception as e:
            logger.error(f"Database config error: {e}")

    def stop(self) -> Tuple[bool, str]:
        """停止子站"""
        with self._lock:
            if not self._running:
                return False, "Not running"
            self._stop_internal()
            return True, "Outstation stopped"

    def _stop_internal(self):
        """内部停止方法（不加锁）"""
        try:
            if self._outstation:
                self._outstation = None
            if self._channel:
                self._channel = None
            if self._manager:
                self._manager.Shutdown()
                self._manager = None
        except Exception as e:
            logger.error(f"Stop error: {e}")
        self._running = False

    def update_point(self, point_type: str, index: int, value: Any) -> Tuple[bool, str]:
        """
        更新数据点值

        Args:
            point_type: 点类型 (analog, binary)
            index: 点索引
            value: 点值

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "pydnp3 not available"

        with self._lock:
            if not self._running or self._outstation is None:
                return False, "Outstation not running"

            try:
                builder = asiodnp3.UpdateBuilder()

                if point_type == 'analog':
                    # 创建 Analog 值
                    analog_value = opendnp3.Analog(value)
                    analog_value.quality = opendnp3.Quality.GOOD
                    builder.Update(analog_value, index)
                elif point_type == 'binary':
                    # 创建 Binary 值
                    binary_value = opendnp3.Binary(value)
                    binary_value.quality = opendnp3.Quality.GOOD
                    builder.Update(binary_value, index)
                else:
                    return False, f"Unsupported point type: {point_type}"

                # 应用更新
                update = builder.Build()
                self._outstation.Apply(update)

                # 存储到本地数据库
                self._database[f"{point_type}_{index}"] = value

                return True, f"Point updated: {point_type}[{index}] = {value}"

            except Exception as e:
                logger.error(f"Update point error: {e}")
                return False, str(e)

    def status(self) -> Dict[str, Any]:
        """获取子站状态"""
        with self._lock:
            return {
                "running": self._running,
                "bind": self._bind,
                "port": self._port,
                "server_id": self._server_id,
                "start_time": self._start_time,
                "database_size": len(self._database),
            }


# ========== Helper Classes ==========

class ChannelListener(asiodnp3.IChannelListener if DNP3_AVAILABLE else object):
    """通道状态监听器"""

    def __init__(self):
        if DNP3_AVAILABLE:
            super().__init__()
        self._state = "CLOSED"

    def OnStateChange(self, state):
        self._state = str(state)
        logger.debug(f"Channel state changed: {state}")


class MasterApplication(opendnp3.IMasterApplication if DNP3_AVAILABLE else object):
    """主站应用"""

    def __init__(self):
        if DNP3_AVAILABLE:
            super().__init__()

    def AssignClassDuringStartup(self):
        return False

    def OnClose(self):
        pass

    def OnOpen(self):
        pass

    def OnReceiveIIN(self, iin):
        pass

    def OnTaskComplete(self, info):
        pass

    def OnTaskStart(self, type, id):
        pass


class OutstationApplication(opendnp3.IOutstationApplication if DNP3_AVAILABLE else object):
    """子站应用"""

    def __init__(self, outstation: Dnp3Outstation):
        if DNP3_AVAILABLE:
            super().__init__()
        self._outstation = outstation

    def ColdRestartSupport(self):
        return opendnp3.RestartMode.UNSUPPORTED

    def WarmRestartSupport(self):
        return opendnp3.RestartMode.UNSUPPORTED

    def GetApplicationIIN(self):
        return opendnp3.ApplicationIIN()

    def SupportsAssignClass(self):
        return False

    def SupportsWriteAbsoluteTime(self):
        return False

    def SupportsWriteTimeAndInterval(self):
        return False


class OutstationCommandHandler(opendnp3.ICommandHandler if DNP3_AVAILABLE else object):
    """子站命令处理器"""

    def __init__(self, outstation: Dnp3Outstation):
        if DNP3_AVAILABLE:
            super().__init__()
        self._outstation = outstation

    def Start(self):
        logger.debug("CommandHandler.Start")

    def End(self):
        logger.debug("CommandHandler.End")

    def Select(self, command, index):
        logger.info(f"CommandHandler.Select: index={index}")
        return opendnp3.CommandStatus.SUCCESS

    def Operate(self, command, index, op_type):
        logger.info(f"CommandHandler.Operate: index={index}, op_type={op_type}")
        return opendnp3.CommandStatus.SUCCESS


# ========== 功能码列表 ==========

def get_function_codes_list() -> List[Dict[str, str]]:
    """返回 DNP3 功能码列表"""
    return [
        {"id": "Read", "name": "Read (读)"},
        {"id": "Write", "name": "Write (写)"},
        {"id": "Select", "name": "Select (选择)"},
        {"id": "Operate", "name": "Operate (执行)"},
        {"id": "Direct_Operate", "name": "Direct_Operate (直接执行)"},
        {"id": "Select_And_Operate", "name": "Select_And_Operate (选择并执行)"},
        {"id": "Cold_Restart", "name": "Cold_Restart (冷重启)"},
        {"id": "Warm_Restart", "name": "Warm_Restart (热重启)"},
    ]


# ========== 全局实例管理 ==========

# 存储客户端和服务端实例
dnp3_clients: Dict[str, Dnp3Master] = {}
dnp3_servers: Dict[str, Dnp3Outstation] = {}
dnp3_client_lock = threading.Lock()
dnp3_server_lock = threading.Lock()


def get_client(client_id: str = 'default') -> Optional[Dnp3Master]:
    """获取客户端实例"""
    with dnp3_client_lock:
        return dnp3_clients.get(client_id)


def create_client(client_id: str = 'default') -> Dnp3Master:
    """创建客户端实例"""
    with dnp3_client_lock:
        if client_id not in dnp3_clients:
            dnp3_clients[client_id] = Dnp3Master(client_id)
        return dnp3_clients[client_id]


def remove_client(client_id: str = 'default'):
    """移除客户端实例"""
    with dnp3_client_lock:
        if client_id in dnp3_clients:
            dnp3_clients[client_id].disconnect()
            del dnp3_clients[client_id]


def get_server(server_id: str = 'default') -> Optional[Dnp3Outstation]:
    """获取服务端实例"""
    with dnp3_server_lock:
        return dnp3_servers.get(server_id)


def create_server(server_id: str = 'default') -> Dnp3Outstation:
    """创建服务端实例"""
    with dnp3_server_lock:
        if server_id not in dnp3_servers:
            dnp3_servers[server_id] = Dnp3Outstation(server_id)
        return dnp3_servers[server_id]


def remove_server(server_id: str = 'default'):
    """移除服务端实例"""
    with dnp3_server_lock:
        if server_id in dnp3_servers:
            dnp3_servers[server_id].stop()
            del dnp3_servers[server_id]


# 导出
__all__ = [
    "DNP3_AVAILABLE",
    "PYDNP3_PLATFORM_OK",
    "Dnp3Master",
    "Dnp3Outstation",
    "get_function_codes_list",
    "get_client",
    "create_client",
    "remove_client",
    "get_server",
    "create_server",
    "remove_server",
    "dnp3_clients",
    "dnp3_servers",
]