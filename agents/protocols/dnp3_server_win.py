# -*- coding: utf-8 -*-
"""
dnp3_server_win.py - DNP3 子站（服务端）子进程脚本
运行在独立子进程中，通过 stdin/stdout 与主进程通信。
ctypes 调用 dnp3protocol.dll，崩溃不影响主 Flask 进程。

输入: stdin JSON 配置 {"bind": "0.0.0.0", "port": 20000, "count": 10, "slave": 1, "master": 2}
输出: stdout JSON 状态 {"status": "started", "bind": "...", "port": ...}
错误: stderr 错误信息

运行: 由 dnp3_handler.py 通过 subprocess.Popen() 调用
"""

import ctypes
import json
import logging
import signal
import struct
import sys
import time
import os

# Windows 平台检测
if sys.platform != "win32":
    print(json.dumps({"status": "error", "message": "DNP3 requires Windows (dnp3protocol.dll)"}))
    sys.exit(1)

# 强制 UTF-8 输出
if hasattr(sys.stdout, "buffer"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 配置日志（输出到 stderr，避免与 stdout JSON 混淆）
logging.basicConfig(level=logging.INFO, format="%(asctime)s\t%(levelname)s\t%(message)s", stream=sys.stderr)
_log = logging.getLogger(__name__)

# 尝试加载 dnp3protocol.dll
DNP3_LIB_AVAILABLE = False
dnp3_lib = None

try:
    from dnp3protocol.dnp3api import *
    DNP3_LIB_AVAILABLE = True
    _log.info("dnp3protocol.dll loaded successfully")
except ImportError as e:
    _log.error("Failed to load dnp3protocol: %s", e)
except OSError as e:
    _log.error("Failed to load dnp3protocol.dll: %s", e)

# 全局状态
_server_handle = None
_running = False


def error_str(errorcode, errorvalue):
    """获取错误码描述"""
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


def cb_write(u16ObjectId, eFunctionID, ptWriteID, ptWriteValue, ptWriteParams, ptErrorValue):
    """Write 功能码回调"""
    if not ptWriteID:
        return 0
    try:
        gid = getattr(ptWriteID.contents.eGroupID, 'value', ptWriteID.contents.eGroupID)
        idx = ptWriteID.contents.u16IndexNumber
        gid = int(gid) if gid is not None else 0
        idx = int(idx) if idx is not None else 0
        _log.info("Write callback ServerID=%s FunctionID=%s Group=%s Index=%s", int(u16ObjectId), eFunctionID, gid, idx)
    except Exception:
        _log.debug("Write callback ServerID=%s", u16ObjectId)
    return 0


def cb_select(u16ObjectId, psSelectID, psSelectValue, psSelectParams, ptErrorValue):
    """Select 功能码回调"""
    if not psSelectID:
        return 0
    try:
        gid = getattr(psSelectID.contents.eGroupID, 'value', psSelectID.contents.eGroupID)
        idx = psSelectID.contents.u16IndexNumber
        gid = int(gid) if gid is not None else 0
        idx = int(idx) if idx is not None else 0
        _log.info("Select callback ServerID=%s Group=%s Index=%s", int(u16ObjectId), gid, idx)
    except Exception:
        _log.debug("Select callback ServerID=%s", u16ObjectId)
    return 0


def cb_operate(u16ObjectId, psOperateID, psOperateValue, psOperateParams, ptErrorValue):
    """Operate/Direct_Operate 功能码回调"""
    if not psOperateID:
        return 0
    try:
        gid = getattr(psOperateID.contents.eGroupID, 'value', psOperateID.contents.eGroupID)
        idx = psOperateID.contents.u16IndexNumber
        gid = int(gid) if gid is not None else 0
        idx = int(idx) if idx is not None else 0
        _log.info("Operate callback ServerID=%s Group=%s Index=%s", int(u16ObjectId), gid, idx)
    except Exception:
        _log.debug("Operate callback ServerID=%s", u16ObjectId)
    return 0


def cb_debug(u16ObjectId, ptDebugData, ptErrorValue):
    """调试消息回调"""
    return 0


def cb_cold_restart(u16ObjectId, ptWriteID, ptErrorValue):
    """Cold_Restart 回调"""
    _log.info("Cold_Restart callback ServerID=%s", int(u16ObjectId))
    return 0


def cb_warm_restart(u16ObjectId, ptWriteID, ptErrorValue):
    """Warm_Restart 回调"""
    _log.info("Warm_Restart callback ServerID=%s", int(u16ObjectId))
    return 0


def start_server(config):
    """启动 DNP3 服务端"""
    global _server_handle, _running

    if not DNP3_LIB_AVAILABLE:
        return False, "dnp3protocol.dll not available"

    # 解析配置
    bind = config.get("bind", "0.0.0.0")
    port = config.get("port", 20000)
    count = config.get("count", 10)
    slave = config.get("slave", 1)
    master = config.get("master", 2)

    n = max(1, min(count, 0xFFFF))

    try:
        ver = dnp3_lib.DNP3GetLibraryVersion()
        _log.info("Library version: %s", ver.decode("utf-8", errors="ignore") if ver else "?")
    except Exception:
        pass

    i16ErrorCode = ctypes.c_short(0)
    tErrorValue = ctypes.c_short(0)
    sParameters = sDNP3Parameters()

    sParameters.eAppFlag = eApplicationFlag.APP_SERVER
    sParameters.ptReadCallback = ctypes.cast(None, DNP3ReadCallback)
    sParameters.ptWriteCallback = DNP3WriteCallback(cb_write)
    sParameters.ptUpdateCallback = ctypes.cast(None, DNP3UpdateCallback)
    sParameters.ptSelectCallback = DNP3ControlSelectCallback(cb_select)
    sParameters.ptOperateCallback = DNP3ControlOperateCallback(cb_operate)
    sParameters.ptDebugCallback = DNP3DebugMessageCallback(cb_debug)
    sParameters.ptUpdateIINCallback = ctypes.cast(None, DNP3UpdateIINCallback)
    sParameters.ptClientPollStatusCallback = ctypes.cast(None, DNP3ClientPollStatusCallback)
    sParameters.ptClientStatusCallback = ctypes.cast(None, DNP3ClientStatusCallback)
    sParameters.ptColdRestartCallback = DNP3ColdRestartCallback(cb_cold_restart)
    sParameters.ptWarmRestartCallback = DNP3WarmRestartCallback(cb_warm_restart)
    sParameters.ptDeviceAttrCallback = ctypes.cast(None, DNP3DeviceAttributeCallback)
    sParameters.u32Options = 0
    sParameters.u16ObjectId = 1

    _server_handle = dnp3_lib.DNP3Create(ctypes.byref(sParameters), ctypes.byref(i16ErrorCode), ctypes.byref(tErrorValue))
    if i16ErrorCode.value != 0:
        ec, ev = error_str(i16ErrorCode.value, tErrorValue.value)
        return False, f"DNP3Create failed: {ec} {ev}"

    sDNP3Config = sDNP3ConfigurationParameters()
    sDNP3Config.sDNP3ServerSet.sServerCommunicationSet.eCommMode = eCommunicationMode.TCP_IP_MODE
    sDNP3Config.sDNP3ServerSet.sServerCommunicationSet.sEthernetCommsSet.sEthernetportSet.ai8FromIPAddress = bind.encode("utf-8")
    sDNP3Config.sDNP3ServerSet.sServerCommunicationSet.sEthernetCommsSet.sEthernetportSet.u16PortNumber = port

    sDNP3Config.sDNP3ServerSet.sServerProtSet.u16SlaveAddress = slave
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u16MasterAddress = master
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u32LinkLayerTimeout = 10000
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u32ApplicationLayerTimeout = 20000
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u32TimeSyncIntervalSeconds = 90

    # 设置默认变体
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarBI = eDefaultStaticVariationBinaryInput.BI_WITH_FLAGS
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarAI = eDefaultStaticVariationAnalogInput.AI_SINGLEPREC_FLOATWITHFLAG
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarBO = eDefaultStaticVariationBinaryOutput.BO_WITH_FLAGS
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarAO = eDefaultStaticVariationAnalogOutput.AO_SINGLEPRECFLOAT_WITHFLAG
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarCI = eDefaultStaticVariationCounterInput.CI_32BIT_WITHFLAG
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarFzCI = eDefaultStaticVariationFrozenCounterInput.FCI_32BIT_WITHFLAGANDTIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarDBI = eDefaultStaticVariationDoubleBitBinaryInput.DBBI_WITH_FLAGS
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarFzAI = eDefaultStaticVariationFrozenAnalogInput.FAI_SINGLEPRECFLOATWITHFLAG
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sStaticVariation.eDeStVarAID = eDefaultStaticVariationAnalogInputDeadBand.DAI_SINGLEPRECFLOAT

    # 事件变体
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarBI = eDefaultEventVariationBinaryInput.BIE_WITH_ABSOLUTETIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarAI = eDefaultEventVariationAnalogInput.AIE_SINGLEPREC_WITHTIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarBO = eDefaultEventVariationBinaryOutput.BOE_WITH_TIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarAO = eDefaultEventVariationAnalogOutput.AOE_SINGLEPREC_WITHTIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarCI = eDefaultEventVariationCounterInput.CIE_32BIT_WITHFLAG_WITHTIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarFzCI = eDefaultEventVariationFrozenCounterInput.FCIE_32BIT_WITHFLAG_WITHTIME
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sEventVariation.eDeEvVarFzAI = eDefaultEventVariationFrozenAnalogInput.FAIE_SINGLEPREC_WITHTIME

    # 事件缓冲区
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u16Class1EventBufferSize = 50
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u8Class1EventBufferOverFlowPercentage = 90
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u16Class2EventBufferSize = 50
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u8Class2EventBufferOverFlowPercentage = 90
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u16Class3EventBufferSize = 50
    sDNP3Config.sDNP3ServerSet.sServerProtSet.u8Class3EventBufferOverFlowPercentage = 90

    # Class0 设置
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bAddBIinClass0 = True
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bAddAIinClass0 = True
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bAddBOinClass0 = True
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bAddCIinClass0 = True
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bAddAOinClass0 = True

    # 非请求响应设置
    ur = sDNP3Config.sDNP3ServerSet.sServerProtSet.sUnsolicitedResponseSet
    ur.bEnableUnsolicited = False
    ur.bEnableResponsesonStartup = False
    ur.u32Timeout = 5000
    ur.u8Retries = 5

    # 时间戳
    now = time.time()
    ti = time.localtime(now)
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u8Day = ti.tm_mday
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u8Month = ti.tm_mon
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u16Year = ti.tm_year
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u8Hour = ti.tm_hour
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u8Minute = ti.tm_min
    sDNP3Config.sDNP3ServerSet.sServerProtSet.sTimeStamp.u8Seconds = ti.tm_sec
    sDNP3Config.sDNP3ServerSet.sServerProtSet.bTimeInvalid = False

    # DNP3 对象配置
    sDNP3Config.sDNP3ServerSet.u16NoofObject = 4
    sDNP3Config.sDNP3ServerSet.psDNP3Objects = (sDNP3Object * 4)()

    for i, (name, gid, ctrl) in enumerate([
        ("binary input 0-%s" % (n - 1), eDNP3GroupID.BINARY_INPUT, eDNP3ControlModelConfig.INPUT_STATUS_ONLY),
        ("analog input 0-%s" % (n - 1), eDNP3GroupID.ANALOG_INPUT, eDNP3ControlModelConfig.INPUT_STATUS_ONLY),
        ("binary output 0-%s" % (n - 1), eDNP3GroupID.BINARY_OUTPUT, eDNP3ControlModelConfig.SELECT_BEFORE_OPERATION),
        ("analog output 0-%s" % (n - 1), eDNP3GroupID.ANALOG_OUTPUTS, eDNP3ControlModelConfig.SELECT_BEFORE_OPERATION),
    ]):
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].ai8Name = name.encode("utf-8")
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].eGroupID = gid
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].u16NoofPoints = n
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].eClassID = eDNP3ClassID.CLASS_ONE
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].eControlModel = ctrl
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].u32SBOTimeOut = (
            5000 if ctrl == eDNP3ControlModelConfig.SELECT_BEFORE_OPERATION else 0
        )
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].f32AnalogInputDeadband = 0
        sDNP3Config.sDNP3ServerSet.psDNP3Objects[i].eAnalogStoreType = eAnalogStorageType.AS_FLOAT

    i16ErrorCode = dnp3_lib.DNP3LoadConfiguration(_server_handle, ctypes.byref(sDNP3Config), ctypes.byref(tErrorValue))
    if i16ErrorCode != 0:
        ec, ev = error_str(i16ErrorCode, tErrorValue.value)
        dnp3_lib.DNP3Free(_server_handle, ctypes.byref(tErrorValue))
        _server_handle = None
        return False, f"DNP3LoadConfiguration failed: {ec} {ev}"

    i16ErrorCode = dnp3_lib.DNP3Start(_server_handle, ctypes.byref(tErrorValue))
    if i16ErrorCode != 0:
        ec, ev = error_str(i16ErrorCode, tErrorValue.value)
        dnp3_lib.DNP3Free(_server_handle, ctypes.byref(tErrorValue))
        _server_handle = None
        return False, f"DNP3Start failed: {ec} {ev}"

    _running = True
    _log.info("DNP3 server started on %s:%s (count=%s)", bind, port, n)
    return True, f"Server started on {bind}:{port}"


def stop_server():
    """停止 DNP3 服务端"""
    global _server_handle, _running

    if _server_handle is not None:
        try:
            dnp3_lib.DNP3Stop(_server_handle, ctypes.c_short())
            dnp3_lib.DNP3Free(_server_handle, ctypes.c_short())
        except Exception as e:
            _log.error("Error stopping server: %s", e)
        _server_handle = None
    _running = False
    _log.info("DNP3 server stopped")


def main():
    """主函数：读取 stdin 配置，启动服务端，等待信号"""
    global _running

    if not DNP3_LIB_AVAILABLE:
        # 输出错误 JSON 到 stdout
        print(json.dumps({"status": "error", "message": "dnp3protocol.dll not available on this system"}))
        sys.exit(1)

    # 从 stdin 读取配置
    try:
        config_line = sys.stdin.readline()
        if not config_line:
            config_line = "{}"
        config = json.loads(config_line.strip())
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "message": f"Invalid JSON config: {e}"}))
        sys.exit(1)

    # 启动服务端
    success, message = start_server(config)
    if not success:
        print(json.dumps({"status": "error", "message": message}))
        sys.exit(1)

    # 输出成功状态到 stdout
    print(json.dumps({
        "status": "started",
        "bind": config.get("bind", "0.0.0.0"),
        "port": config.get("port", 20000),
        "slave": config.get("slave", 1),
        "master": config.get("master", 2),
        "count": config.get("count", 10)
    }))
    sys.stdout.flush()

    # 注册信号处理
    def handle_signal(signum, frame):
        _log.info("Received signal %s, shutting down...", signum)
        stop_server()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, handle_signal)
    except AttributeError:
        pass  # Windows 没有 SIGINT

    try:
        signal.signal(signal.SIGTERM, handle_signal)
    except AttributeError:
        pass  # Windows 没有 SIGTERM

    # 主循环
    try:
        while _running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop_server()


if __name__ == "__main__":
    main()