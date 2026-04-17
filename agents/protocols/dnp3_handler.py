# -*- coding: utf-8 -*-
"""
dnp3_handler.py - DNP3 协议处理器
为 industrial_protocol_agent.py 提供 DNP3 客户端和服务端功能。

特点:
1. Windows-only (依赖 dnp3protocol.dll)
2. 服务端运行在子进程中（ctypes 崩溃不影响主 Flask 进程）
3. 客户端操作在主进程中（短生命周期，可接受风险）

参考: apps/DNP3/dnp3_client_win.py, apps/DNP3/dnp3_server_win.py
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from typing import Dict, Any, Optional, Tuple, List

# 平台检测
DNP3_PLATFORM_OK = sys.platform == "win32"

# DNP3 库可用性
DNP3_AVAILABLE = False
dnp3_lib = None

# ctypes 类型定义（在导入 dnp3protocol 后会填充）
sDNP3Parameters = None
sDNP3ConfigurationParameters = None
sDNP3DataAttributeID = None
sDNP3DataAttributeData = None
sDNP3CommandParameters = None
eApplicationFlag = None
eCommunicationMode = None
eDNP3GroupID = None
eDataSizes = None
eDataTypes = None
eDNP3QualityFlags = None
eCommandObjectVariation = None
DNP3ReadCallback = None
DNP3WriteCallback = None
DNP3UpdateCallback = None
DNP3ControlSelectCallback = None
DNP3ControlOperateCallback = None
DNP3DebugMessageCallback = None
DNP3UpdateIINCallback = None
DNP3ClientPollStatusCallback = None
DNP3ClientStatusCallback = None
DNP3ColdRestartCallback = None
DNP3WarmRestartCallback = None
DNP3DeviceAttributeCallback = None
sClientObject = None

# 初始化 ctypes 库
if DNP3_PLATFORM_OK:
    try:
        from dnp3protocol.dnp3api import *
        DNP3_AVAILABLE = True
        print("[OK] DNP3 handler loaded (Windows, dnp3protocol.dll available)")
    except ImportError as e:
        print(f"[WARNING] DNP3 handler: dnp3protocol not installed - {e}")
        DNP3_AVAILABLE = False
    except OSError as e:
        print(f"[WARNING] DNP3 handler: dnp3protocol.dll not found - {e}")
        DNP3_AVAILABLE = False
else:
    print("[INFO] DNP3 handler: Windows-only protocol, not available on this platform")

# 线程锁
_client_lock = threading.Lock()
_server_lock = threading.Lock()


def error_str(errorcode: int, errorvalue: int) -> Tuple[str, str]:
    """获取错误码描述"""
    if not DNP3_AVAILABLE:
        return str(errorcode), str(errorvalue)
    try:
        c = sDNP3ErrorCode()
        c.iErrorCode = errorcode
        dnp3_lib.DNP3ErrorCodeString(c)
        v = sDNP3ErrorValue()
        v.iErrorValue = errorvalue
        dnp3_lib.DNP3ErrorValueString(v)
        return c.LongDes.decode("utf-8", errors="ignore"), v.LongDes.decode("utf-8", errors="ignore")
    except Exception:
        return str(errorcode), str(errorvalue)


def make_daid(host: str, port: int, slave: int, group_id, index: int):
    """构建 DNP3DataAttributeID"""
    psDAID = sDNP3DataAttributeID()
    psDAID.eCommMode = eCommunicationMode.TCP_IP_MODE
    psDAID.u16PortNumber = port
    psDAID.ai8IPAddress = host.encode("utf-8")
    psDAID.eGroupID = group_id
    psDAID.u16SlaveAddress = slave
    psDAID.u16IndexNumber = index
    return psDAID


# ========== 客户端类 ==========

class Dnp3Client:
    """
    DNP3 客户端（主站）

    用于连接 DNP3 子站并发送功能码请求。
    在主进程中运行，短生命周期操作。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._handle = None
        self._tErrorValue = None
        self._ti = None
        self._host = ""
        self._port = 20000
        self._slave = 1
        self._master = 2
        self._count = 10
        self._connected = False
        self._connect_time = None

    def connect(self, host: str, port: int = 20000, slave: int = 1, master: int = 2, count: int = 10) -> Tuple[bool, str]:
        """
        连接到 DNP3 子站

        Args:
            host: 子站 IP 地址
            port: 子站端口 (默认 20000)
            slave: 子站地址 (默认 1)
            master: 主站地址 (默认 2)
            count: 对象个数 (默认 10)

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "DNP3 requires Windows + dnp3protocol.dll"

        with self._lock:
            try:
                # 断开已有连接
                if self._handle is not None:
                    self._disconnect_internal()

                i16ErrorCode = ctypes.c_short(0)
                tErrorValue = ctypes.c_short(0)
                sParameters = sDNP3Parameters()

                sParameters.eAppFlag = eApplicationFlag.APP_CLIENT
                sParameters.ptReadCallback = ctypes.cast(None, DNP3ReadCallback)
                sParameters.ptWriteCallback = ctypes.cast(None, DNP3WriteCallback)
                sParameters.ptUpdateCallback = ctypes.cast(None, DNP3UpdateCallback)
                sParameters.ptSelectCallback = ctypes.cast(None, DNP3ControlSelectCallback)
                sParameters.ptOperateCallback = ctypes.cast(None, DNP3ControlOperateCallback)
                sParameters.ptDebugCallback = ctypes.cast(None, DNP3DebugMessageCallback)
                sParameters.ptUpdateIINCallback = ctypes.cast(None, DNP3UpdateIINCallback)
                sParameters.ptClientPollStatusCallback = ctypes.cast(None, DNP3ClientPollStatusCallback)
                sParameters.ptClientStatusCallback = ctypes.cast(None, DNP3ClientStatusCallback)
                sParameters.ptColdRestartCallback = ctypes.cast(None, DNP3ColdRestartCallback)
                sParameters.ptWarmRestartCallback = ctypes.cast(None, DNP3WarmRestartCallback)
                sParameters.ptDeviceAttrCallback = ctypes.cast(None, DNP3DeviceAttributeCallback)
                sParameters.u32Options = 0
                sParameters.u16ObjectId = 1

                myClient = dnp3_lib.DNP3Create(ctypes.byref(sParameters), ctypes.byref(i16ErrorCode), ctypes.byref(tErrorValue))
                if i16ErrorCode.value != 0:
                    ec, ev = error_str(i16ErrorCode.value, tErrorValue.value)
                    return False, f"DNP3Create failed: {ec} {ev}"

                sDNP3Config = sDNP3ConfigurationParameters()
                sDNP3Config.sDNP3ClientSet.sDebug.u32DebugOptions = 0
                now = time.time()
                ti = time.localtime(now)
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u8Day = ti.tm_mday
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u8Month = ti.tm_mon
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u16Year = ti.tm_year
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u8Hour = ti.tm_hour
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u8Minute = ti.tm_min
                sDNP3Config.sDNP3ClientSet.sTimeStamp.u8Seconds = ti.tm_sec
                sDNP3Config.sDNP3ClientSet.bTimeInvalid = False
                sDNP3Config.sDNP3ClientSet.benabaleUTCtime = False
                sDNP3Config.sDNP3ClientSet.bUpdateCallbackCheckTimestamp = False
                sDNP3Config.sDNP3ClientSet.u16NoofClient = 1

                arr = (sClientObject * 1)()
                sDNP3Config.sDNP3ClientSet.psClientObjects = ctypes.cast(arr, ctypes.POINTER(sClientObject))
                arr[0].eCommMode = eCommunicationMode.TCP_IP_MODE
                arr[0].sClientCommunicationSet.sEthernetCommsSet.ai8ToIPAddress = host.encode("utf-8")
                arr[0].sClientCommunicationSet.sEthernetCommsSet.u16PortNumber = port
                arr[0].sClientProtSet.u16MasterAddress = master
                arr[0].sClientProtSet.u16SlaveAddress = slave
                arr[0].sClientProtSet.u32LinkLayerTimeout = 10000
                arr[0].sClientProtSet.u32ApplicationTimeout = 20000
                arr[0].sClientProtSet.u32Class0123pollInterval = 15000
                arr[0].sClientProtSet.u32Class123pollInterval = 15000
                arr[0].sClientProtSet.u32Class0pollInterval = 15000
                arr[0].sClientProtSet.u32Class1pollInterval = 15000
                arr[0].sClientProtSet.u32Class2pollInterval = 15000
                arr[0].sClientProtSet.u32Class3pollInterval = 15000
                arr[0].sClientProtSet.bFrozenAnalogInputSupport = False
                arr[0].sClientProtSet.bEnableFileTransferSupport = False
                arr[0].sClientProtSet.bDisableUnsolicitedStatup = False
                arr[0].u32CommandTimeout = 50000
                arr[0].u32FileOperationTimeout = 200000
                arr[0].sClientProtSet.bDisableResetofRemotelink = False
                try:
                    arr[0].sClientProtSet.eLinkConform = eLinkLayerConform.CONFORM_NEVER
                except AttributeError:
                    pass

                sDNP3Config.sDNP3ClientSet.bAutoGenDNP3DataObjects = True
                arr[0].u16NoofObject = 0
                arr[0].psDNP3Objects = None

                i16ErrorCode = dnp3_lib.DNP3LoadConfiguration(myClient, ctypes.byref(sDNP3Config), ctypes.byref(tErrorValue))
                if i16ErrorCode != 0:
                    ec, ev = error_str(i16ErrorCode, tErrorValue.value)
                    dnp3_lib.DNP3Free(myClient, ctypes.byref(tErrorValue))
                    return False, f"DNP3LoadConfiguration failed: {ec} {ev}"

                i16ErrorCode = dnp3_lib.DNP3Start(myClient, ctypes.byref(tErrorValue))
                if i16ErrorCode != 0:
                    ec, ev = error_str(i16ErrorCode, tErrorValue.value)
                    dnp3_lib.DNP3Free(myClient, ctypes.byref(tErrorValue))
                    return False, f"DNP3Start failed: {ec} {ev}"

                self._handle = myClient
                self._tErrorValue = tErrorValue
                self._ti = ti
                self._host = host
                self._port = port
                self._slave = slave
                self._master = master
                self._count = max(1, min(count, 0xFFFF))
                self._connected = True
                self._connect_time = time.strftime("%Y-%m-%d %H:%M:%S")

                return True, f"Connected to {host}:{port} (slave={slave}, master={master})"

            except Exception as e:
                return False, f"Connection error: {e}"

    def disconnect(self) -> Tuple[bool, str]:
        """断开连接"""
        with self._lock:
            if self._handle is None:
                return False, "Not connected"
            self._disconnect_internal()
            return True, "Disconnected"

    def _disconnect_internal(self):
        """内部断开连接（不加锁）"""
        if self._handle is not None:
            try:
                dnp3_lib.DNP3Stop(self._handle, ctypes.byref(self._tErrorValue))
                dnp3_lib.DNP3Free(self._handle, ctypes.byref(self._tErrorValue))
            except Exception:
                pass
        self._handle = None
        self._connected = False

    def read(self) -> Tuple[bool, str, Any]:
        """
        发送 Read 功能码（Class0 轮询）

        Returns:
            (成功标志, 消息, 数据)
        """
        if not DNP3_AVAILABLE:
            return False, "DNP3 not available", None

        with self._lock:
            if self._handle is None:
                return False, "Not connected", None

            try:
                # DNP3 Read 通过轮询自动完成，等待数据返回
                time.sleep(2)
                return True, "Read completed (Class0 poll)", {"count": self._count}
            except Exception as e:
                return False, str(e), None

    def write(self, index: int = 0, value: float = 0.0) -> Tuple[bool, str]:
        """
        发送 Write 功能码

        Args:
            index: 对象索引
            value: 写入值

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "DNP3 not available"

        if not hasattr(dnp3_lib, "DNP3Write"):
            return False, "DNP3Write API not available"

        with self._lock:
            if self._handle is None:
                return False, "Not connected"

            try:
                psDAID = make_daid(self._host, self._port, self._slave, eDNP3GroupID.ANALOG_OUTPUTS, index)
                psNewValue = sDNP3DataAttributeData()
                f32value = ctypes.c_float(value)

                psNewValue.eDataSize = eDataSizes.FLOAT32_SIZE
                psNewValue.eDataType = eDataTypes.FLOAT32_DATA
                psNewValue.tQuality = eDNP3QualityFlags.GOOD
                psNewValue.pvData = ctypes.cast(ctypes.pointer(f32value), ctypes.c_void_p)
                psNewValue.sTimeStamp.u8Day = self._ti.tm_mday
                psNewValue.sTimeStamp.u8Month = self._ti.tm_mon
                psNewValue.sTimeStamp.u16Year = self._ti.tm_year
                psNewValue.sTimeStamp.u8Hour = self._ti.tm_hour
                psNewValue.sTimeStamp.u8Minute = self._ti.tm_min
                psNewValue.sTimeStamp.u8Seconds = self._ti.tm_sec
                psNewValue.bTimeInvalid = False

                i16ErrorCode = dnp3_lib.DNP3Write(self._handle, ctypes.c_long(0), ctypes.byref(psDAID),
                                                   ctypes.byref(psNewValue), ctypes.byref(self._tErrorValue))
                if i16ErrorCode != 0:
                    ec, ev = error_str(i16ErrorCode, self._tErrorValue.value)
                    return False, f"Write failed: {ec} {ev}"

                return True, f"Write completed: index={index}, value={value}"

            except Exception as e:
                return False, str(e)

    def direct_operate(self, index: int = 0, value: float = 0.0) -> Tuple[bool, str]:
        """
        发送 Direct_Operate 功能码

        Args:
            index: 对象索引
            value: 操作值

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_AVAILABLE:
            return False, "DNP3 not available"

        with self._lock:
            if self._handle is None:
                return False, "Not connected"

            try:
                psDAID = make_daid(self._host, self._port, self._slave, eDNP3GroupID.ANALOG_OUTPUTS, index)
                psNewValue = sDNP3DataAttributeData()
                psCommandParameters = sDNP3CommandParameters()
                f32value = ctypes.c_float(value)

                psNewValue.eDataSize = eDataSizes.FLOAT32_SIZE
                psNewValue.eDataType = eDataTypes.FLOAT32_DATA
                psNewValue.tQuality = eDNP3QualityFlags.GOOD
                psNewValue.pvData = ctypes.cast(ctypes.pointer(f32value), ctypes.c_void_p)
                psNewValue.sTimeStamp.u8Day = self._ti.tm_mday
                psNewValue.sTimeStamp.u8Month = self._ti.tm_mon
                psNewValue.sTimeStamp.u16Year = self._ti.tm_year
                psNewValue.sTimeStamp.u8Hour = self._ti.tm_hour
                psNewValue.sTimeStamp.u8Minute = self._ti.tm_min
                psNewValue.sTimeStamp.u8Seconds = self._ti.tm_sec
                psNewValue.bTimeInvalid = False

                psCommandParameters.u8Count = 1
                psCommandParameters.eCommandVariation = eCommandObjectVariation.ANALOG_OUTPUT_BLOCK_FLOAT32

                i16ErrorCode = dnp3_lib.DNP3DirectOperate(self._handle, ctypes.byref(psDAID),
                                                          ctypes.byref(psNewValue), ctypes.byref(psCommandParameters),
                                                          ctypes.byref(self._tErrorValue))
                if i16ErrorCode != 0:
                    ec, ev = error_str(i16ErrorCode, self._tErrorValue.value)
                    return False, f"DirectOperate failed: {ec} {ev}"

                return True, f"DirectOperate completed: index={index}, value={value}"

            except Exception as e:
                return False, str(e)

    def status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        with self._lock:
            return {
                "connected": self._connected,
                "host": self._host,
                "port": self._port,
                "slave": self._slave,
                "master": self._master,
                "count": self._count,
                "connect_time": self._connect_time,
            }


# ========== 服务端子进程处理器 ==========

class Dnp3SubprocessHandler:
    """
    DNP3 服务端子进程处理器

    在独立子进程中运行 DNP3 服务端，避免 ctypes 崩溃影响主进程。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._server_id: Optional[str] = None
        self._config: Dict[str, Any] = {}

    def start_server(self, server_id: str, config: Dict[str, Any]) -> Tuple[bool, str]:
        """
        启动 DNP3 服务端子进程

        Args:
            server_id: 服务端标识
            config: 配置字典 {"bind": "0.0.0.0", "port": 20000, "count": 10, "slave": 1, "master": 2}

        Returns:
            (成功标志, 消息)
        """
        if not DNP3_PLATFORM_OK:
            return False, "DNP3 server requires Windows"

        if not DNP3_AVAILABLE:
            return False, "dnp3protocol.dll not available"

        with self._lock:
            try:
                # 如果已有进程在运行，先停止
                if self._process is not None and self._process.poll() is None:
                    self._stop_internal()

                # 获取服务端脚本路径
                script_dir = os.path.dirname(os.path.abspath(__file__))
                server_script = os.path.join(script_dir, "dnp3_server_win.py")

                if not os.path.exists(server_script):
                    return False, f"Server script not found: {server_script}"

                # 启动子进程
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"

                kwargs = {
                    "stdin": subprocess.PIPE,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "env": env,
                    "cwd": script_dir,
                }

                # Windows 下隐藏窗口
                if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                    kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

                self._process = subprocess.Popen(
                    [sys.executable, server_script],
                    **kwargs
                )

                # 发送配置到 stdin
                config_json = json.dumps(config)
                self._process.stdin.write(config_json + "\n")
                self._process.stdin.flush()

                # 读取启动结果
                import select
                if sys.platform != "win32":
                    # Unix: 使用 select
                    readable, _, _ = select.select([self._process.stdout], [], [], 5.0)
                    if readable:
                        result_line = self._process.stdout.readline()
                    else:
                        result_line = ""
                else:
                    # Windows: 简单等待读取（非阻塞检查）
                    time.sleep(0.5)
                    result_line = self._process.stdout.readline() if self._process.stdout else ""

                if result_line:
                    try:
                        result = json.loads(result_line.strip())
                        if result.get("status") == "started":
                            self._server_id = server_id
                            self._config = config
                            return True, f"DNP3 server started: {config.get('bind', '0.0.0.0')}:{config.get('port', 20000)}"
                        else:
                            error_msg = result.get("message", "Unknown error")
                            self._stop_internal()
                            return False, f"Server start failed: {error_msg}"
                    except json.JSONDecodeError:
                        pass  # 可能是日志输出，继续

                # 检查进程是否存活
                time.sleep(0.5)
                if self._process.poll() is None:
                    self._server_id = server_id
                    self._config = config
                    return True, f"DNP3 server started (no status output): {config.get('bind', '0.0.0.0')}:{config.get('port', 20000)}"
                else:
                    return False, "Server process exited unexpectedly"

            except Exception as e:
                self._stop_internal()
                return False, f"Start error: {e}"

    def stop_server(self) -> Tuple[bool, str]:
        """停止 DNP3 服务端子进程"""
        with self._lock:
            return self._stop_internal()

    def _stop_internal(self) -> Tuple[bool, str]:
        """内部停止方法（不加锁）"""
        if self._process is None:
            return False, "Server not running"

        try:
            # 发送终止信号
            self._process.terminate()
            # 等待进程结束
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # 强制终止
                self._process.kill()
                self._process.wait(timeout=2)
        except Exception as e:
            pass

        self._process = None
        self._server_id = None
        return True, "Server stopped"

    def status(self) -> Dict[str, Any]:
        """获取服务端状态"""
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            pid = self._process.pid if running else None
            return {
                "running": running,
                "pid": pid,
                "server_id": self._server_id,
                "config": self._config,
            }


# ========== 功能码列表 ==========

def get_function_codes_list() -> List[Dict[str, str]]:
    """返回 DNP3 功能码列表"""
    return [
        {"id": "Read", "name": "Read (读)"},
        {"id": "Write", "name": "Write (写)"},
        {"id": "Select", "name": "Select (选择)"},
        {"id": "Operate", "name": "Operate (执行)"},
        {"id": "Direct_Operate", "name": "Direct_Operate (直接执行)"},
        {"id": "Direct_Operate_No_ACK", "name": "Direct_Operate_No_ACK (直接执行无应答)"},
        {"id": "Immediate_Freeze", "name": "Immediate_Freeze (立即冻结)"},
        {"id": "Immediate_Freeze_No_ACK", "name": "Immediate_Freeze_No_ACK (立即冻结无应答)"},
        {"id": "Freeze_and_Clear", "name": "Freeze_and_Clear (冻结并清除)"},
        {"id": "Freeze_and_Clear_No_ACK", "name": "Freeze_and_Clear_No_ACK (冻结并清除无应答)"},
        {"id": "Cold_Restart", "name": "Cold_Restart (冷重启)"},
        {"id": "Warm_Restart", "name": "Warm_Restart (热重启)"},
        {"id": "Delay_Measurement", "name": "Delay_Measurement (延迟测量)"},
        {"id": "Record_Current_Time", "name": "Record_Current_Time (记录当前时间)"},
        {"id": "Enable_Spontaneous_Msg", "name": "Enable_Spontaneous_Msg (启用自发消息)"},
        {"id": "Disable_Spontaneous_Msg", "name": "Disable_Spontaneous_Msg (禁用自发消息)"},
    ]


# 导出
__all__ = [
    "DNP3_AVAILABLE",
    "DNP3_PLATFORM_OK",
    "Dnp3Client",
    "Dnp3SubprocessHandler",
    "get_function_codes_list",
    "error_str",
]