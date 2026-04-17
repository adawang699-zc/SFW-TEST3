# -*- coding: utf-8 -*-
"""
enip_handler.py - EtherNet/IP 协议处理器（完整版）

功能：
- TCP 客户端/服务端 (44818)
- UDP 封装客户端/服务端 (44818)
- I/O 通信 (UDP 2222)
- 支持 21 种 CIP 服务
- 支持所有 ENIP 命令码
- 连接式和非连接式通信

参考: apps/ENIP/enip_client_entry.py 和 apps/ENIP/enip_server_entry.py
"""

import socket
import struct
import threading
import time
import logging
from typing import Dict, Any, Optional, Tuple, List, Callable

# 配置日志
logger = logging.getLogger("ENIP")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ----- ENIP 常量 -----
ENIP_PORT = 44818
ENIP_IO_PORT = 2222
SENDER_CONTEXT = b"_pycomm_"

# ENIP 命令码
ENIP_COMMANDS = {
    'NOP': 0x0000,
    'ListServices': 0x0004,
    'ListIdentity': 0x0063,
    'ListInterfaces': 0x0064,
    'RegisterSession': 0x0065,
    'UnregisterSession': 0x0066,
    'SendRRData': 0x006F,
    'SendUnitData': 0x0070,
    'IndicateStatus': 0x0072,
    'Cancel': 0x0073,
}

ENIP_CMD_NAMES = {
    0x0000: "NOP",
    0x0004: "ListServices",
    0x0063: "ListIdentity",
    0x0064: "ListInterfaces",
    0x0065: "RegisterSession",
    0x0066: "UnregisterSession",
    0x006F: "SendRRData",
    0x0070: "SendUnitData",
    0x0072: "IndicateStatus",
    0x0073: "Cancel",
}

# ENIP 状态码
ENIP_STATUS_SUCCESS = 0x00000000
ENIP_STATUS_UNSUPPORTED_CMD = 0x00000001
ENIP_STATUS_INVALID_SESSION = 0x00000064
ENIP_STATUS_INVALID_LENGTH = 0x00000065

# CPF 项类型
CPF_ITEM_NULL = 0x0000
CPF_ITEM_COMM_CAP = 0x0001
CPF_ITEM_INTERFACE_LIST = 0x0002
CPF_ITEM_CONNECTION_BASED = 0x00A1
CPF_ITEM_CONNECTED_DATA = 0x00B1
CPF_ITEM_UNCONNECTED_MESSAGE = 0x00B2
CPF_ITEM_SEQUENCED_ADDR = 0x8002
CPF_ITEM_IDENTITY = 0x000C

CPF_ITEM_NAMES = {
    CPF_ITEM_NULL: "Null",
    CPF_ITEM_COMM_CAP: "Communications_Capability",
    CPF_ITEM_INTERFACE_LIST: "Interface_List",
    CPF_ITEM_CONNECTION_BASED: "Connection_Based",
    CPF_ITEM_CONNECTED_DATA: "Connected_Data",
    CPF_ITEM_UNCONNECTED_MESSAGE: "Unconnected_Message",
    CPF_ITEM_SEQUENCED_ADDR: "Sequenced_Address",
    CPF_ITEM_IDENTITY: "Identity",
}

# CIP 服务码
CIP_SERVICES = {
    'No_Operation': 0x00,
    'Get_Attribute_All': 0x01,
    'Set_Attribute_All': 0x02,
    'Get_Attribute_List': 0x03,
    'Set_Attribute_List': 0x04,
    'Reset': 0x05,
    'Start': 0x06,
    'Stop': 0x07,
    'Create': 0x08,
    'Delete': 0x09,
    'Multiple_Service_Packet': 0x0A,
    'Apply_Attributes': 0x0D,
    'Get_Attribute_Single': 0x0E,
    'Set_Attribute_Single': 0x10,
    'Find_Next_Object': 0x11,
    'Restore': 0x15,
    'Save': 0x16,
    'Get_Member': 0x18,
    'Set_Member': 0x19,
    'Insert_Member': 0x1A,
}

CIP_SERVICE_NAMES = {v: k for k, v in CIP_SERVICES.items()}

# CIP 路径段类型
CIP_PATH_CLASS = 0x20
CIP_PATH_INSTANCE = 0x24
CIP_PATH_ATTRIBUTE = 0x30
CIP_PATH_MEMBER = 0x28
CIP_PATH_CONNECTION_POINT = 0x2C

# CIP 标准类
CIP_CLASS_IDENTITY = 0x01
CIP_CLASS_MESSAGE_ROUTER = 0x02
CIP_CLASS_CONNECTION_MANAGER = 0x06

# 逻辑段类型名称
LOGICAL_NAMES = {
    0: "class_id", 1: "instance_id", 2: "member_id",
    3: "connection_point", 4: "attribute_id", 5: "special",
    6: "service_id"
}

# 端口名称
PORT_NAMES = {1: "backplane", 2: "ethernet", 3: "dhrio-b"}


# ----- ENIP 封装头 -----
def build_enip_header(command: int, length: int, session_handle: int = 0,
                      status: int = 0, context: bytes = None, options: int = 0) -> bytes:
    """构建 24 字节 ENIP 封装头"""
    if context is None:
        context = SENDER_CONTEXT
    context = context.ljust(8, b"\x00")[:8]

    header = struct.pack("<H", command)
    header += struct.pack("<H", length)
    header += struct.pack("<I", session_handle)
    header += struct.pack("<I", status)
    header += context
    header += struct.pack("<I", options)
    return header


def parse_enip_header(header: bytes) -> Optional[Dict[str, Any]]:
    """解析 ENIP 封装头"""
    if not header or len(header) < 24:
        return None

    cmd, length, session, status = struct.unpack("<HHII", header[:12])
    sender_context = header[12:20]
    options = struct.unpack("<I", header[20:24])[0]

    return {
        'command': cmd,
        'command_name': ENIP_CMD_NAMES.get(cmd, f"0x{cmd:04X}"),
        'length': length,
        'session_handle': session,
        'status': status,
        'sender_context': sender_context,
        'options': options,
    }


# ----- CPF 载荷构建 -----
def build_ucmm_cpf(cip_data: bytes) -> bytes:
    """构建非连接消息 CPF (Null + Unconnected_Message)"""
    cpf = struct.pack("<IHH", 0, 0, 2)
    cpf += struct.pack("<HH", CPF_ITEM_NULL, 0)
    cpf += struct.pack("<HH", CPF_ITEM_UNCONNECTED_MESSAGE, len(cip_data))
    cpf += cip_data
    return cpf


def build_connection_address_item(conn_id: int) -> bytes:
    """构建连接地址项 (0x00A1)"""
    return struct.pack("<HHI", CPF_ITEM_CONNECTION_BASED, 4, conn_id & 0xFFFFFFFF)


def build_connection_based_cpf(conn_id: int, data: bytes) -> bytes:
    """构建连接式 CPF (0x00A1 + 0x00B1)"""
    cpf = struct.pack("<IHH", 0, 0, 2)
    cpf += build_connection_address_item(conn_id)
    cpf += struct.pack("<HH", CPF_ITEM_CONNECTED_DATA, len(data))
    cpf += data
    return cpf


def build_io_packet(connection_id: int, io_data: bytes, sequence: int = 0) -> bytes:
    """构建 I/O 报文 (0x8002 + 0x00B1)"""
    buf = struct.pack("<H", 2)  # item_count
    buf += struct.pack("<HH", CPF_ITEM_SEQUENCED_ADDR, 8)
    buf += struct.pack("<II", connection_id, sequence & 0xFFFFFFFF)
    buf += struct.pack("<HH", CPF_ITEM_CONNECTED_DATA, len(io_data))
    buf += io_data
    return buf


# ----- CIP 请求构建 -----
def build_cip_path(class_id: int, instance: int, attribute: int = None) -> bytes:
    """构建 CIP 路径"""
    path = bytes([CIP_PATH_CLASS, class_id, CIP_PATH_INSTANCE, instance])
    if attribute is not None:
        path += bytes([CIP_PATH_ATTRIBUTE, attribute])
    return path


def build_cip_request(service: int, class_id: int, instance: int,
                      attribute: int = None, data: bytes = None) -> bytes:
    """构建 CIP 请求"""
    path = build_cip_path(class_id, instance, attribute)
    path_size = len(path) // 2

    cip = bytes([service, path_size])
    cip += path
    if data:
        cip += data
    return cip


def build_cip_read_request(class_id: int, instance: int, attribute: int) -> bytes:
    """构建 Get_Attribute_Single 请求"""
    return build_cip_request(CIP_SERVICES['Get_Attribute_Single'],
                             class_id, instance, attribute)


def build_cip_write_request(class_id: int, instance: int, attribute: int,
                            value: bytes) -> bytes:
    """构建 Set_Attribute_Single 请求"""
    return build_cip_request(CIP_SERVICES['Set_Attribute_Single'],
                             class_id, instance, attribute, value)


# ----- CPF 解析 -----
def parse_cpf_items(data: bytes) -> Dict[str, Any]:
    """解析 CPF 项"""
    if len(data) < 8:
        return {'items': []}

    interface_handle, timeout, count = struct.unpack("<IHH", data[:8])
    result = {
        'interface_handle': interface_handle,
        'timeout': timeout,
        'item_count': count,
        'items': []
    }

    off = 8
    for i in range(count):
        if off + 4 > len(data):
            break
        item_type, item_len = struct.unpack("<HH", data[off:off+4])
        off += 4
        item_data = data[off:off+item_len] if off + item_len <= len(data) else b''
        off += item_len

        item_info = {
            'type_id': item_type,
            'type_name': CPF_ITEM_NAMES.get(item_type, f"0x{item_type:04X}"),
            'length': item_len,
            'data_hex': item_data.hex() if item_data else ''  # 转为hex字符串
        }

        # 解析 CIP 服务
        if item_type in (CPF_ITEM_CONNECTED_DATA, CPF_ITEM_UNCONNECTED_MESSAGE) and len(item_data) >= 2:
            service = item_data[0] & 0x3F
            item_info['service'] = service
            item_info['service_name'] = CIP_SERVICE_NAMES.get(service, f"0x{service:02X}")
            if len(item_data) > 2:
                item_info['path_segments'] = parse_cip_path(item_data[2:])

        result['items'].append(item_info)

    return result


def parse_cip_path(path_data: bytes) -> List[Dict[str, Any]]:
    """解析 CIP 路径段"""
    segments = []
    if not path_data:
        return segments

    off = 0
    seg_num = 0
    while off < len(path_data):
        if off + 1 > len(path_data):
            break
        seg_type = path_data[off] & 0xE0
        seg_format = path_data[off] & 0x03

        if seg_type == 0x00:  # 端口段
            if off + 2 > len(path_data):
                break
            port_id = path_data[off] & 0x1F
            link_addr_len = 1 if (path_data[off] & 0x10) == 0 else path_data[off + 1]
            if off + 2 + link_addr_len > len(path_data):
                break
            link_addr = path_data[off + 2:off + 2 + link_addr_len]
            segments.append({
                'kind': 'port',
                'index': seg_num,
                'port_id': port_id,
                'port_name': PORT_NAMES.get(port_id, f"port{port_id}"),
                'link_addr': link_addr.hex()
            })
            off += 2 + link_addr_len
        elif seg_type == 0x20:  # 逻辑段
            logical_type = (path_data[off] >> 2) & 0x1F
            logical_name = LOGICAL_NAMES.get(logical_type, f"logical{logical_type}")
            value = None
            if seg_format == 0:
                if off + 2 > len(path_data):
                    break
                value = path_data[off + 1]
                off += 2
            elif seg_format == 1:
                if off + 3 > len(path_data):
                    break
                value = struct.unpack("<H", path_data[off + 1:off + 3])[0]
                off += 3
            elif seg_format == 3:
                if off + 5 > len(path_data):
                    break
                value = struct.unpack("<I", path_data[off + 1:off + 5])[0]
                off += 5
            if value is not None:
                segments.append({
                    'kind': 'logical',
                    'index': seg_num,
                    'logical_type': logical_name,
                    'value': value
                })
            seg_num += 1
        else:
            off += 1
    return segments


def parse_io_packet(data: bytes) -> Tuple[Optional[int], Optional[int], Optional[bytes]]:
    """解析 I/O 报文"""
    if len(data) < 2 + 4 + 4 + 4:
        return None, None, None

    item_count = struct.unpack("<H", data[0:2])[0]
    off = 2
    connection_id, sequence, payload = None, None, None

    for _ in range(min(item_count, 2)):
        if off + 4 > len(data):
            break
        item_type, item_len = struct.unpack("<HH", data[off:off+4])
        off += 4
        if off + item_len > len(data):
            break
        if item_type == CPF_ITEM_SEQUENCED_ADDR and item_len >= 8:
            connection_id = struct.unpack("<I", data[off:off+4])[0]
            sequence = struct.unpack("<I", data[off+4:off+8])[0]
        elif item_type == CPF_ITEM_CONNECTED_DATA:
            payload = data[off:off+item_len]
        off += item_len

    return connection_id, sequence, payload


# ----- ENIP 客户端 -----
class EnipClient:
    """
    完整的 EtherNet/IP 客户端

    支持:
    - TCP 连接 (44818)
    - UDP 封装 (44818)
    - I/O 通信 (2222)
    - 所有 ENIP 命令码
    - 21 种 CIP 服务
    - 连接式和非连接式通信
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._socket: Optional[socket.socket] = None
        self._session_handle: int = 0
        self._connected: bool = False
        self._host: str = ""
        self._port: int = ENIP_PORT
        self._timeout: float = 5.0
        self._connect_time: Optional[str] = None
        self._connection_id: int = 0x02730a85  # 默认连接 ID
        self._heartbeat_interval: float = 10.0  # 心跳间隔（秒）
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_running: bool = False

    # ----- TCP 连接 -----
    def connect(self, host: str, port: int = ENIP_PORT, timeout: float = 5.0) -> Tuple[bool, str]:
        """通过 TCP 连接到 ENIP 设备"""
        with self._lock:
            try:
                if self._socket:
                    self._disconnect_internal()

                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(timeout)
                self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                self._socket.connect((host, port))
                self._host = host
                self._port = port
                self._timeout = timeout

                success, message, session = self._register_session_internal()
                if success:
                    self._session_handle = session
                    self._connected = True
                    self._connect_time = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._start_heartbeat()  # 启动心跳
                    return (True, f"连接成功，会话句柄: 0x{session:08X}")
                else:
                    self._disconnect_internal()
                    return (False, f"会话注册失败: {message}")

            except socket.timeout:
                return (False, f"连接超时: {host}:{port}")
            except socket.error as e:
                return (False, f"连接错误: {e}")
            except Exception as e:
                return (False, f"连接异常: {e}")

    def disconnect(self) -> Tuple[bool, str]:
        """断开 TCP 连接"""
        with self._lock:
            if not self._socket:
                return (False, "未连接")
            try:
                self._unregister_session_internal()
            except:
                pass
            self._disconnect_internal()
            return (True, "断开连接成功")

    def _disconnect_internal(self):
        """内部断开连接"""
        self._stop_heartbeat()
        if self._socket:
            try:
                self._socket.close()
            except:
                pass
        self._socket = None
        self._connected = False
        self._session_handle = 0

    def _start_heartbeat(self):
        """启动心跳线程（每 10 秒发送 CIP 0x00 No_Operation）"""
        if self._heartbeat_running:
            return
        
        def heartbeat_loop():
            logger.info('心跳线程启动，间隔 10 秒')
            while self._heartbeat_running and self._connected:
                try:
                    time.sleep(self._heartbeat_interval)
                    if self._connected and self._heartbeat_running:
                        success, msg = self.no_operation()
                        if success:
                            logger.debug('心跳成功 (CIP 0x00 No_Operation)')
                        else:
                            logger.warning(f'心跳失败：{msg}')
                            # 心跳失败不一定断开连接，继续尝试
                except Exception as e:
                    logger.error(f'心跳异常：{e}')
                    self._connected = False
                    break
            logger.info('心跳线程停止')
        
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self):
        """停止心跳线程"""
        self._heartbeat_running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def _register_session_internal(self) -> Tuple[bool, str, int]:
        """注册会话"""
        try:
            payload = struct.pack("<HH", 0x0100, 0x0000)
            header = build_enip_header(ENIP_COMMANDS['RegisterSession'], len(payload))
            self._socket.sendall(header + payload)

            response = self._recv_response()
            if response:
                parsed = parse_enip_header(response)
                if parsed and parsed['status'] == 0:
                    if len(response) >= 28:
                        session = struct.unpack("<I", response[24:28])[0]
                        return (True, "会话注册成功", session)
                    return (True, "会话注册成功", parsed['session_handle'])
                return (False, f"注册失败，状态码: {parsed['status'] if parsed else -1}", 0)
            return (False, "无响应", 0)
        except Exception as e:
            return (False, str(e), 0)

    def _unregister_session_internal(self):
        """注销会话"""
        try:
            header = build_enip_header(ENIP_COMMANDS['UnregisterSession'], 0, self._session_handle)
            self._socket.sendall(header)
        except:
            pass

    def _recv_response(self, timeout: float = None) -> Optional[bytes]:
        """接收响应"""
        if timeout is None:
            timeout = self._timeout

        try:
            # 设置超时
            original_timeout = self._socket.gettimeout()
            self._socket.settimeout(timeout)

            header_buf = b""
            while len(header_buf) < 24:
                chunk = self._socket.recv(24 - len(header_buf))
                if not chunk:
                    logger.warning("连接已关闭 (接收header时)")
                    self._connected = False
                    return None
                header_buf += chunk

            parsed = parse_enip_header(header_buf)
            if not parsed:
                self._socket.settimeout(original_timeout)
                return header_buf

            data_len = parsed['length']
            data_buf = b""
            if data_len > 0:
                while len(data_buf) < data_len:
                    chunk = self._socket.recv(min(data_len - len(data_buf), 4096))
                    if not chunk:
                        break
                    data_buf += chunk

            # 恢复原始超时
            self._socket.settimeout(original_timeout)
            return header_buf + data_buf
        except socket.timeout:
            logger.warning("接收响应超时")
            # 超时不一定断开连接，可以继续使用
            return None
        except (OSError, socket.error) as e:
            logger.error(f"Socket错误: {e}")
            self._connected = False
            return None
        except Exception as e:
            logger.error(f"接收响应异常: {e}")
            self._connected = False
            return None

    # ----- TCP 命令 -----
    def list_services(self) -> Tuple[bool, Any, str]:
        """获取设备支持的服务列表"""
        with self._lock:
            if not self._socket or not self._connected:
                return (False, None, "未连接")
            try:
                header = build_enip_header(ENIP_COMMANDS['ListServices'], 0)
                self._socket.sendall(header)
                response = self._recv_response()
                if response:
                    parsed = parse_enip_header(response)
                    if parsed and parsed['status'] == 0:
                        services = self._parse_list_services(response[24:])
                        return (True, services, "获取成功")
                    else:
                        status = parsed['status'] if parsed else -1
                        return (False, None, f"服务器返回状态码: {status}")
                return (False, None, "无响应")
            except Exception as e:
                self._connected = False
                return (False, None, str(e))

    def list_identity(self) -> Tuple[bool, Any, str]:
        """获取设备标识信息"""
        with self._lock:
            if not self._socket or not self._connected:
                return (False, None, "未连接")
            try:
                header = build_enip_header(ENIP_COMMANDS['ListIdentity'], 0, self._session_handle)
                self._socket.sendall(header)
                response = self._recv_response()
                if response:
                    parsed = parse_enip_header(response)
                    if parsed and parsed['status'] == 0:
                        identity = self._parse_identity(response[24:])
                        return (True, identity, "获取成功")
                    else:
                        status = parsed['status'] if parsed else -1
                        return (False, None, f"服务器返回状态码: {status}")
                return (False, None, "无响应")
            except Exception as e:
                self._connected = False
                return (False, None, str(e))

    def list_interfaces(self) -> Tuple[bool, Any, str]:
        """获取网络接口列表"""
        with self._lock:
            if not self._socket or not self._connected:
                return (False, None, "未连接")
            try:
                header = build_enip_header(ENIP_COMMANDS['ListInterfaces'], 0, self._session_handle)
                self._socket.sendall(header)
                response = self._recv_response()
                if response:
                    parsed = parse_enip_header(response)
                    if parsed and parsed['status'] == 0:
                        interfaces = self._parse_list_interfaces(response[24:])
                        return (True, interfaces, "获取成功")
                    else:
                        status = parsed['status'] if parsed else -1
                        return (False, None, f"服务器返回状态码: {status}")
                return (False, None, "无响应")
            except Exception as e:
                self._connected = False
                return (False, None, str(e))

    # ----- CIP 操作 -----
    def send_rr_data(self, cip_data: bytes) -> Tuple[bool, Any, str]:
        """发送请求-响应数据"""
        with self._lock:
            if not self._socket or not self._connected:
                return (False, None, "未连接")
            try:
                cpf = build_ucmm_cpf(cip_data)
                header = build_enip_header(ENIP_COMMANDS['SendRRData'], len(cpf), self._session_handle)
                self._socket.sendall(header + cpf)

                response = self._recv_response()
                if response:
                    parsed = parse_enip_header(response)
                    if parsed and parsed['status'] == 0:
                        cpf_result = parse_cpf_items(response[24:])
                        return (True, cpf_result, "发送成功")
                    return (False, None, f"发送失败，状态码: {parsed['status'] if parsed else -1}")
                return (False, None, "无响应")
            except Exception as e:
                self._connected = False
                return (False, None, str(e))

    def send_unit_data(self, cip_data: bytes, conn_id: int = None) -> Tuple[bool, Any, str]:
        """发送单元数据（连接式或非连接式）"""
        with self._lock:
            if not self._socket or not self._connected:
                return (False, None, "未连接")
            try:
                if conn_id:
                    cpf = build_connection_based_cpf(conn_id, cip_data)
                else:
                    cpf = build_ucmm_cpf(cip_data)

                header = build_enip_header(ENIP_COMMANDS['SendUnitData'], len(cpf), self._session_handle)
                self._socket.sendall(header + cpf)

                response = self._recv_response()
                if response:
                    parsed = parse_enip_header(response)
                    if parsed and parsed['status'] == 0:
                        cpf_result = parse_cpf_items(response[24:])
                        return (True, cpf_result, "发送成功")
                    return (False, None, f"发送失败，状态码: {parsed['status'] if parsed else -1}")
                return (False, None, "无响应")
            except Exception as e:
                self._connected = False
                return (False, None, str(e))

    # ----- CIP 服务 -----
    def get_attribute_single(self, class_id: int, instance: int, attribute: int) -> Tuple[bool, Any, str]:
        """获取单个属性"""
        cip = build_cip_read_request(class_id, instance, attribute)
        success, result, msg = self.send_rr_data(cip)
        logger.info(f'get_attribute_single: success={success}, result={result}, msg={msg}')
        if success and result:
            for item in result.get('items', []):
                # 使用 data_hex 字段
                data_hex = item.get('data_hex', '')
                logger.info(f'get_attribute_single item: type={item.get("type_name")}, data_hex={data_hex}')
                if data_hex:
                    data = bytes.fromhex(data_hex)
                    # CIP响应格式: service(1) + reserved(1) + status(1) + ext_status_size(1) + data
                    if len(data) >= 4:
                        cip_status = data[2]
                        if cip_status == 0:  # 成功
                            return (True, data[4:].hex(), "读取成功")
                        else:
                            return (False, None, f"CIP错误，状态码: {cip_status}")
                    elif len(data) > 0:
                        # 数据太短，返回原始数据
                        return (True, data.hex(), "读取成功(原始)")
            # 发送成功但无法解析数据
            return (False, None, "响应数据格式错误，无法解析属性值")
        return (False, None, msg if not success else "发送失败")

    def set_attribute_single(self, class_id: int, instance: int, attribute: int,
                             value: bytes) -> Tuple[bool, str]:
        """设置单个属性"""
        cip = build_cip_write_request(class_id, instance, attribute, value)
        success, result, msg = self.send_rr_data(cip)
        return (success, msg)

    def get_attribute_all(self, class_id: int, instance: int) -> Tuple[bool, Any, str]:
        """获取所有属性"""
        cip = build_cip_request(CIP_SERVICES['Get_Attribute_All'], class_id, instance)
        return self.send_rr_data(cip)

    def reset_device(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """复位设备"""
        cip = build_cip_request(CIP_SERVICES['Reset'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def start_device(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """启动设备"""
        cip = build_cip_request(CIP_SERVICES['Start'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def stop_device(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """停止设备"""
        cip = build_cip_request(CIP_SERVICES['Stop'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def create_object(self, class_id: int, instance: int = 1) -> Tuple[bool, Any, str]:
        """创建对象 (Create 0x08)"""
        cip = build_cip_request(CIP_SERVICES['Create'], class_id, instance)
        return self.send_rr_data(cip)

    def delete_object(self, class_id: int, instance: int = 1) -> Tuple[bool, str]:
        """删除对象 (Delete 0x09)"""
        cip = build_cip_request(CIP_SERVICES['Delete'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def multiple_service_packet(self, services: list) -> Tuple[bool, Any, str]:
        """多服务请求 (Multiple_Service_Packet 0x0A)"""
        # services 是一个列表，每个元素是一个 CIP 请求
        # 格式: service_count(2) + offsets + services
        if not services:
            return (False, None, "服务列表为空")

        service_count = len(services)
        offsets = []
        services_data = b""
        current_offset = 2 + service_count * 2  # service_count + offsets

        for svc in services:
            offsets.append(current_offset)
            services_data += svc
            current_offset += len(svc)

        cip = struct.pack("<H", service_count)
        for off in offsets:
            cip += struct.pack("<H", off)
        cip += services_data

        return self.send_rr_data(cip)

    def apply_attributes(self, class_id: int, instance: int = 1, attributes: bytes = b"") -> Tuple[bool, str]:
        """应用属性 (Apply_Attributes 0x0D)"""
        cip = build_cip_request(CIP_SERVICES['Apply_Attributes'], class_id, instance)
        cip += attributes
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def find_next_object(self, class_id: int, instance: int = 1) -> Tuple[bool, Any, str]:
        """查找下一个对象 (Find_Next_Object 0x11)"""
        cip = build_cip_request(CIP_SERVICES['Find_Next_Object'], class_id, instance)
        return self.send_rr_data(cip)

    def restore_device(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """恢复设备 (Restore 0x15)"""
        cip = build_cip_request(CIP_SERVICES['Restore'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def save_device(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """保存设备 (Save 0x16)"""
        cip = build_cip_request(CIP_SERVICES['Save'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def get_member(self, class_id: int, instance: int, member: int) -> Tuple[bool, Any, str]:
        """获取成员 (Get_Member 0x18)"""
        cip = build_cip_request(CIP_SERVICES['Get_Member'], class_id, instance)
        cip += struct.pack("<B", 0x28) + struct.pack("<B", member)  # 成员路径
        return self.send_rr_data(cip)

    def set_member(self, class_id: int, instance: int, member: int, value: bytes) -> Tuple[bool, str]:
        """设置成员 (Set_Member 0x19)"""
        cip = build_cip_request(CIP_SERVICES['Set_Member'], class_id, instance)
        cip += struct.pack("<B", 0x28) + struct.pack("<B", member)  # 成员路径
        cip += value
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def insert_member(self, class_id: int, instance: int, member: int, value: bytes) -> Tuple[bool, str]:
        """插入成员 (Insert_Member 0x1A)"""
        cip = build_cip_request(CIP_SERVICES['Insert_Member'], class_id, instance)
        cip += struct.pack("<B", 0x28) + struct.pack("<B", member)  # 成员路径
        cip += value
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def no_operation(self, class_id: int = CIP_CLASS_IDENTITY, instance: int = 1) -> Tuple[bool, str]:
        """空操作 (No_Operation 0x00)"""
        cip = build_cip_request(CIP_SERVICES['No_Operation'], class_id, instance)
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def get_attribute_list(self, class_id: int, instance: int, attributes: list = None) -> Tuple[bool, Any, str]:
        """获取属性列表 (Get_Attribute_List 0x03)"""
        cip = build_cip_request(CIP_SERVICES['Get_Attribute_List'], class_id, instance)
        if attributes:
            cip += struct.pack("<H", len(attributes))
            for attr in attributes:
                cip += struct.pack("<H", attr)
        return self.send_rr_data(cip)

    def set_attribute_list(self, class_id: int, instance: int, attributes: dict = None) -> Tuple[bool, str]:
        """设置属性列表 (Set_Attribute_List 0x04)"""
        cip = build_cip_request(CIP_SERVICES['Set_Attribute_List'], class_id, instance)
        if attributes:
            cip += struct.pack("<H", len(attributes))
            for attr, value in attributes.items():
                cip += struct.pack("<H", attr)
                cip += value
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    def set_attribute_all(self, class_id: int, instance: int, data: bytes = b"") -> Tuple[bool, str]:
        """设置所有属性 (Set_Attribute_All 0x02)"""
        cip = build_cip_request(CIP_SERVICES['Set_Attribute_All'], class_id, instance)
        cip += data
        success, _, msg = self.send_rr_data(cip)
        return (success, msg)

    # ----- UDP 操作 -----
    def discover_devices(self, broadcast_addr: str = "255.255.255.255",
                        port: int = ENIP_PORT, timeout: float = 2.0) -> Tuple[bool, List[Dict], str]:
        """UDP 广播发现设备 - 使用 ListIdentity (0x0063) 命令"""
        devices = []
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)

        try:
            header = build_enip_header(ENIP_COMMANDS['ListIdentity'], 0)
            sock.sendto(header, (broadcast_addr, port))

            while True:
                try:
                    data, addr = sock.recvfrom(4096)
                    if len(data) >= 24:
                        identity = self._parse_identity(data[24:])
                        if identity:
                            identity['ip'] = addr[0]
                            identity['port'] = addr[1]
                            devices.append(identity)
                except socket.timeout:
                    break
            return (True, devices, f"发现 {len(devices)} 个设备")
        except Exception as e:
            return (False, [], str(e))
        finally:
            sock.close()

    def send_udp_command(self, host: str, command: int, port: int = ENIP_PORT,
                         payload: bytes = b"", timeout: float = 5.0) -> Tuple[bool, Any, str]:
        """发送 UDP 命令"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            header = build_enip_header(command, len(payload))
            sock.sendto(header + payload, (host, port))
            data, _ = sock.recvfrom(4096)
            if len(data) >= 24:
                parsed = parse_enip_header(data)
                return (True, {
                    'header': parsed,
                    'data_hex': data[24:].hex()
                }, "发送成功")
            return (False, None, "响应不完整")
        except socket.timeout:
            return (False, None, "UDP 超时无响应")
        except Exception as e:
            return (False, None, str(e))
        finally:
            sock.close()

    # ----- I/O 操作 -----
    def send_io_data(self, host: str, io_data: bytes, connection_id: int = None,
                     port: int = ENIP_IO_PORT, timeout: float = 3.0) -> Tuple[bool, Any, str]:
        """发送 I/O 数据 (UDP 2222)"""
        if connection_id is None:
            connection_id = self._connection_id

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            packet = build_io_packet(connection_id, io_data, sequence=int(time.time() * 1000) % 0xFFFFFFFF)
            sock.sendto(packet, (host, port))
            data, _ = sock.recvfrom(2048)
            cid, seq, payload = parse_io_packet(data)
            return (True, {
                'connection_id': cid,
                'sequence': seq,
                'data_hex': payload.hex() if payload else ''
            }, "I/O 发送成功")
        except socket.timeout:
            return (False, None, "I/O 超时无响应")
        except Exception as e:
            return (False, None, str(e))
        finally:
            sock.close()

    # ----- 解析方法 -----
    def _parse_list_services(self, data: bytes) -> List[Dict]:
        """解析服务列表"""
        services = []
        if len(data) < 6:
            return services
        count = struct.unpack("<H", data[0:2])[0]
        off = 2
        for _ in range(count):
            if off + 4 > len(data):
                break
            item_type, item_len = struct.unpack("<HH", data[off:off+4])
            off += 4
            if item_type == 0x0100 and off + item_len <= len(data):
                service_data = data[off:off+item_len]
                # 解析服务信息
                service_info = {
                    'type': item_type,
                    'length': item_len,
                    'data_hex': service_data.hex()  # 转为hex字符串
                }
                # 尝试解析服务名称
                if len(service_data) >= 6:
                    service_info['service_name'] = service_data[5:].decode('ascii', errors='ignore').rstrip('\x00')
                services.append(service_info)
            off += item_len
        return services

    def _parse_identity(self, data: bytes) -> Optional[Dict]:
        """解析设备标识"""
        if len(data) < 8:
            return None
        try:
            # CPF 格式
            iface, timeout, count = struct.unpack("<IHH", data[:8])
            off = 8
            for _ in range(count):
                if off + 4 > len(data):
                    break
                item_type, item_len = struct.unpack("<HH", data[off:off+4])
                off += 4
                if item_type == CPF_ITEM_IDENTITY and off + item_len <= len(data):
                    id_data = data[off:off+item_len]
                    if len(id_data) >= 36:
                        return {
                            'vendor_id': struct.unpack("<H", id_data[0:2])[0],
                            'device_type': struct.unpack("<H", id_data[2:4])[0],
                            'product_code': struct.unpack("<H", id_data[4:6])[0],
                            'revision': f"{id_data[6]}.{id_data[7]}",
                            'status': struct.unpack("<H", id_data[8:10])[0],
                            'serial_number': struct.unpack("<I", id_data[10:14])[0],
                            'product_name': id_data[15:15+id_data[14]].decode('ascii', errors='ignore') if len(id_data) > 14 else ''
                        }
                off += item_len
        except:
            pass
        return None

    def _parse_list_interfaces(self, data: bytes) -> List[Dict]:
        """解析接口列表"""
        interfaces = []
        if len(data) < 6:
            return interfaces
        count = struct.unpack("<H", data[0:2])[0]
        # 简化解析
        interfaces.append({'count': count, 'data': data.hex()})
        return interfaces

    def status(self) -> Dict[str, Any]:
        """获取客户端状态"""
        with self._lock:
            return {
                'connected': self._connected,
                'host': self._host,
                'port': self._port,
                'session_handle': self._session_handle,
                'connect_time': self._connect_time,
                'connection_id': self._connection_id
            }


# ----- ENIP 服务端 -----
class EnipServer:
    """
    完整的 EtherNet/IP 服务端

    支持:
    - TCP 显式消息服务 (44818)
    - UDP 封装服务 (44818)
    - I/O 服务 (2222)
    - 所有 ENIP 命令响应
    - CIP 对象模拟
    """

    def __init__(self):
        self._lock = threading.Lock()

        # TCP 服务
        self._tcp_socket: Optional[socket.socket] = None
        self._tcp_thread: Optional[threading.Thread] = None
        self._tcp_running: bool = False

        # UDP 封装服务
        self._udp_socket: Optional[socket.socket] = None
        self._udp_thread: Optional[threading.Thread] = None
        self._udp_running: bool = False

        # I/O 服务
        self._io_socket: Optional[socket.socket] = None
        self._io_thread: Optional[threading.Thread] = None
        self._io_running: bool = False

        # 配置
        self._host: str = "0.0.0.0"
        self._port: int = ENIP_PORT
        self._io_port: int = ENIP_IO_PORT

        # 会话管理
        self._sessions: Dict[int, Dict] = {}
        self._session_counter: int = 0x00000001

        # Identity 数据
        self._identity_data: Dict[int, bytes] = {
            1: struct.pack("<H", 1),      # Vendor ID
            2: struct.pack("<H", 1),      # Device Type
            3: struct.pack("<H", 1),      # Product Code
            4: struct.pack("<H", 0x0100), # Revision
            5: struct.pack("<H", 0),      # Status
            6: struct.pack("<I", 0x12345678),  # Serial Number
            7: b"ENIP Simulator",         # Product Name
        }

        # 模拟标签
        self._tags: Dict[str, Any] = {
            'Motor_Speed': 0,
            'Valve_Open': False,
            'Temperature': 25.5
        }

        # 回调
        self._message_callback: Optional[Callable] = None

    def start(self, host: str = "0.0.0.0", port: int = ENIP_PORT,
              enable_udp: bool = True, enable_io: bool = True) -> Tuple[bool, str]:
        """启动服务端"""
        with self._lock:
            if self._tcp_running:
                return (False, "服务端已在运行")

            self._host = host
            self._port = port

            try:
                # 启动 TCP 服务
                self._tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._tcp_socket.settimeout(0.5)
                self._tcp_socket.bind((host, port))
                self._tcp_socket.listen(5)

                self._tcp_running = True
                self._tcp_thread = threading.Thread(target=self._tcp_server_loop, daemon=True)
                self._tcp_thread.start()

                # 启动 UDP 封装服务
                if enable_udp:
                    self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self._udp_socket.settimeout(0.5)
                    self._udp_socket.bind((host, port))

                    self._udp_running = True
                    self._udp_thread = threading.Thread(target=self._udp_server_loop, daemon=True)
                    self._udp_thread.start()

                # 启动 I/O 服务
                if enable_io:
                    self._io_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    self._io_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    self._io_socket.settimeout(0.5)
                    self._io_socket.bind((host, self._io_port))

                    self._io_running = True
                    self._io_thread = threading.Thread(target=self._io_server_loop, daemon=True)
                    self._io_thread.start()

                services = []
                services.append(f"TCP:{port}")
                if enable_udp:
                    services.append(f"UDP:{port}")
                if enable_io:
                    services.append(f"I/O:{self._io_port}")

                return (True, f"服务端启动成功 ({', '.join(services)})")

            except socket.error as e:
                self._cleanup()
                return (False, f"启动失败: {e}")
            except Exception as e:
                self._cleanup()
                return (False, f"启动异常: {e}")

    def stop(self) -> Tuple[bool, str]:
        """停止服务端"""
        with self._lock:
            if not self._tcp_running:
                return (False, "服务端未运行")

            self._cleanup()
            return (True, "服务端已停止")

    def _cleanup(self):
        """清理资源"""
        self._tcp_running = False
        self._udp_running = False
        self._io_running = False

        for sock in [self._tcp_socket, self._udp_socket, self._io_socket]:
            if sock:
                try:
                    sock.close()
                except:
                    pass

        self._tcp_socket = None
        self._udp_socket = None
        self._io_socket = None
        self._sessions.clear()

    def set_message_callback(self, callback: Callable):
        """设置消息回调"""
        self._message_callback = callback

    def update_tag(self, name: str, value: Any):
        """更新模拟标签"""
        with self._lock:
            self._tags[name] = value

    # ----- TCP 服务循环 -----
    def _tcp_server_loop(self):
        """TCP 服务主循环"""
        logger.info(f"ENIP TCP 服务监听 {self._host}:{self._port}")
        while self._tcp_running:
            try:
                try:
                    client_sock, client_addr = self._tcp_socket.accept()
                    client_sock.settimeout(30)
                    client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    threading.Thread(target=self._handle_tcp_client, args=(client_sock, client_addr), daemon=True).start()
                except socket.timeout:
                    continue
            except Exception as e:
                if self._tcp_running:
                    logger.error(f"TCP Accept 错误: {e}")

    def _handle_tcp_client(self, client_sock: socket.socket, client_addr: tuple):
        """处理 TCP 客户端"""
        logger.info(f"客户端连接: {client_addr}")
        current_session = None

        try:
            while self._tcp_running:
                # 接收 ENIP 头
                header_buf = self._recv_all(client_sock, 24, 30)
                if not header_buf or len(header_buf) < 24:
                    break

                parsed = parse_enip_header(header_buf)
                if not parsed:
                    continue

                command = parsed['command']
                data_len = parsed['length']
                sender_context = parsed['sender_context']

                # 接收数据
                payload = b""
                if data_len > 0:
                    payload = self._recv_all(client_sock, data_len, 30)
                    if len(payload) < data_len:
                        continue

                # 消息回调
                if self._message_callback:
                    try:
                        self._message_callback('tcp', client_addr, parsed, payload)
                    except:
                        pass

                # 处理命令
                response = self._handle_tcp_command(command, parsed['session_handle'],
                                                    payload, sender_context, current_session)

                if response:
                    client_sock.sendall(response)

                if command == ENIP_COMMANDS['UnregisterSession']:
                    break

        except Exception as e:
            logger.error(f"客户端处理错误: {e}")
        finally:
            if current_session and current_session in self._sessions:
                del self._sessions[current_session]
            client_sock.close()
            logger.info(f"客户端断开: {client_addr}")

    def _handle_tcp_command(self, command: int, session_handle: int, payload: bytes,
                            sender_context: bytes, current_session: int) -> Optional[bytes]:
        """处理 TCP 命令"""

        # NOP
        if command == ENIP_COMMANDS['NOP']:
            return build_enip_header(command, 0, session_handle, 0, sender_context)

        # ListServices
        elif command == ENIP_COMMANDS['ListServices']:
            resp_data = self._build_list_services()
            return build_enip_header(command, len(resp_data), session_handle, 0, sender_context) + resp_data

        # ListIdentity
        elif command == ENIP_COMMANDS['ListIdentity']:
            resp_data = self._build_list_identity()
            return build_enip_header(command, len(resp_data), session_handle, 0, sender_context) + resp_data

        # ListInterfaces
        elif command == ENIP_COMMANDS['ListInterfaces']:
            resp_data = self._build_list_interfaces()
            return build_enip_header(command, len(resp_data), session_handle, 0, sender_context) + resp_data

        # RegisterSession
        elif command == ENIP_COMMANDS['RegisterSession']:
            self._session_counter = (self._session_counter + 1) % 0xFFFFFFFF
            session = self._session_counter
            self._sessions[session] = {'created': time.time()}
            resp_data = struct.pack("<IHH", session, 0x0100, 0x0001)
            return build_enip_header(command, len(resp_data), session, 0, sender_context) + resp_data

        # UnregisterSession
        elif command == ENIP_COMMANDS['UnregisterSession']:
            if session_handle in self._sessions:
                del self._sessions[session_handle]
            return build_enip_header(command, 0, session_handle, 0, sender_context)

        # SendRRData
        elif command == ENIP_COMMANDS['SendRRData']:
            if session_handle not in self._sessions:
                return build_enip_header(command, 0, session_handle, ENIP_STATUS_INVALID_SESSION, sender_context)
            resp_data = self._handle_cip_request(payload)
            return build_enip_header(command, len(resp_data), session_handle, 0, sender_context) + resp_data

        # SendUnitData
        elif command == ENIP_COMMANDS['SendUnitData']:
            if session_handle not in self._sessions:
                return build_enip_header(command, 0, session_handle, ENIP_STATUS_INVALID_SESSION, sender_context)
            resp_data = self._handle_cip_request(payload)
            return build_enip_header(command, len(resp_data), session_handle, 0, sender_context) + resp_data

        # IndicateStatus
        elif command == ENIP_COMMANDS['IndicateStatus']:
            return build_enip_header(command, 0, session_handle, 0, sender_context)

        # Cancel
        elif command == ENIP_COMMANDS['Cancel']:
            return build_enip_header(command, 0, session_handle, 0, sender_context)

        # 未知命令
        else:
            return build_enip_header(command, 0, session_handle, ENIP_STATUS_UNSUPPORTED_CMD, sender_context)

    # ----- UDP 服务循环 -----
    def _udp_server_loop(self):
        """UDP 封装服务主循环"""
        logger.info(f"ENIP UDP 服务监听 {self._host}:{self._port}")
        while self._udp_running:
            try:
                try:
                    data, addr = self._udp_socket.recvfrom(4096)
                    response = self._handle_udp_command(data, addr)
                    if response:
                        self._udp_socket.sendto(response, addr)
                except socket.timeout:
                    continue
            except Exception as e:
                if self._udp_running:
                    logger.error(f"UDP 处理错误: {e}")

    def _handle_udp_command(self, data: bytes, addr: tuple) -> Optional[bytes]:
        """处理 UDP 命令"""
        if len(data) < 24:
            return None

        parsed = parse_enip_header(data)
        if not parsed:
            return None

        command = parsed['command']
        sender_context = parsed['sender_context']

        # 消息回调
        if self._message_callback:
            try:
                self._message_callback('udp', addr, parsed, data[24:])
            except:
                pass

        if command == ENIP_COMMANDS['ListIdentity']:
            resp_data = self._build_list_identity()
            return build_enip_header(command, len(resp_data), 0, 0, sender_context) + resp_data

        elif command == ENIP_COMMANDS['ListServices']:
            resp_data = self._build_list_services()
            return build_enip_header(command, len(resp_data), 0, 0, sender_context) + resp_data

        elif command == ENIP_COMMANDS['ListInterfaces']:
            resp_data = self._build_list_interfaces()
            return build_enip_header(command, len(resp_data), 0, 0, sender_context) + resp_data

        elif command == ENIP_COMMANDS['RegisterSession']:
            session = self._session_counter
            self._sessions[session] = {'created': time.time()}
            resp_data = struct.pack("<IHH", session, 0x0100, 0x0001)
            return build_enip_header(command, len(resp_data), session, 0, sender_context) + resp_data

        elif command == ENIP_COMMANDS['SendUnitData']:
            resp_data = self._handle_cip_request(data[24:])
            return build_enip_header(command, len(resp_data), 0, 0, sender_context) + resp_data

        elif command in (ENIP_COMMANDS['NOP'], ENIP_COMMANDS['IndicateStatus'],
                         ENIP_COMMANDS['Cancel'], ENIP_COMMANDS['UnregisterSession']):
            return build_enip_header(command, 0, 0, 0, sender_context)

        return build_enip_header(command, 0, 0, ENIP_STATUS_UNSUPPORTED_CMD, sender_context)

    # ----- I/O 服务循环 -----
    def _io_server_loop(self):
        """I/O 服务主循环"""
        logger.info(f"ENIP I/O 服务监听 {self._host}:{self._io_port}")
        sequence = 0
        while self._io_running:
            try:
                try:
                    data, addr = self._io_socket.recvfrom(2048)
                    cid, rx_seq, payload = parse_io_packet(data)
                    if cid is not None:
                        sequence = (sequence + 1) % 0xFFFFFFFF

                        # 消息回调
                        if self._message_callback:
                            try:
                                self._message_callback('io', addr, {'connection_id': cid, 'sequence': rx_seq}, payload)
                            except:
                                pass

                        # 回显数据
                        reply_data = payload if payload else b"\x00" * 6
                        reply = build_io_packet(cid, reply_data, sequence)
                        self._io_socket.sendto(reply, addr)
                except socket.timeout:
                    continue
            except Exception as e:
                if self._io_running:
                    logger.error(f"I/O 处理错误: {e}")

    # ----- CIP 请求处理 -----
    def _handle_cip_request(self, cpf_data: bytes) -> bytes:
        """处理 CIP 请求"""
        if len(cpf_data) < 8:
            return self._build_cip_error_response(0x01)

        cpf = parse_cpf_items(cpf_data)
        cip_response = None

        for item in cpf.get('items', []):
            if item.get('type_id') in (CPF_ITEM_UNCONNECTED_MESSAGE, CPF_ITEM_CONNECTED_DATA):
                cip_data = item.get('data', b'')
                if len(cip_data) >= 2:
                    cip_response = self._process_cip_service(cip_data)
                    break

        if cip_response:
            # 构建响应 CPF
            resp_cpf = struct.pack("<IHH", 0, 0, 2)
            resp_cpf += struct.pack("<HH", CPF_ITEM_NULL, 0)
            resp_cpf += struct.pack("<HH", CPF_ITEM_UNCONNECTED_MESSAGE, len(cip_response))
            resp_cpf += cip_response
            return resp_cpf

        return self._build_cip_error_response(0x01)

    def _process_cip_service(self, cip_data: bytes) -> bytes:
        """处理 CIP 服务"""
        if len(cip_data) < 2:
            return self._build_cip_error_response(0x01)

        service = cip_data[0] & 0x3F
        path_size = cip_data[1] if len(cip_data) > 1 else 0

        # 解析路径
        class_id, instance, attribute = None, None, None
        if len(cip_data) >= 2 + path_size * 2:
            path = cip_data[2:2 + path_size * 2]
            for i in range(0, len(path) - 1, 2):
                seg_type = path[i]
                seg_value = path[i + 1]
                if seg_type == CIP_PATH_CLASS:
                    class_id = seg_value
                elif seg_type == CIP_PATH_INSTANCE:
                    instance = seg_value
                elif seg_type == CIP_PATH_ATTRIBUTE:
                    attribute = seg_value

        # 处理服务
        if service == CIP_SERVICES['Get_Attribute_Single']:
            return self._cip_get_attribute_single(class_id, instance, attribute)

        elif service == CIP_SERVICES['Set_Attribute_Single']:
            data = cip_data[2 + path_size * 2:] if len(cip_data) > 2 + path_size * 2 else b""
            return self._cip_set_attribute_single(class_id, instance, attribute, data)

        elif service == CIP_SERVICES['Get_Attribute_All']:
            return self._cip_get_attribute_all(class_id, instance)

        elif service == CIP_SERVICES['Reset']:
            return self._cip_reset(class_id, instance)

        elif service == CIP_SERVICES['Start']:
            return self._cip_start(class_id, instance)

        elif service == CIP_SERVICES['Stop']:
            return self._cip_stop(class_id, instance)

        else:
            # 服务不支持
            return bytes([service | 0x80, 0x08])

    def _cip_get_attribute_single(self, class_id: int, instance: int, attribute: int) -> bytes:
        """Get_Attribute_Single 响应"""
        if class_id == CIP_CLASS_IDENTITY and attribute in self._identity_data:
            value = self._identity_data[attribute]
            return bytes([CIP_SERVICES['Get_Attribute_Single'] | 0x80, 0x00]) + value
        return bytes([CIP_SERVICES['Get_Attribute_Single'] | 0x80, 0x09])

    def _cip_set_attribute_single(self, class_id: int, instance: int, attribute: int, value: bytes) -> bytes:
        """Set_Attribute_Single 响应"""
        if class_id == CIP_CLASS_IDENTITY and attribute in self._identity_data:
            self._identity_data[attribute] = value[:len(self._identity_data[attribute])]
            return bytes([CIP_SERVICES['Set_Attribute_Single'] | 0x80, 0x00])
        return bytes([CIP_SERVICES['Set_Attribute_Single'] | 0x80, 0x09])

    def _cip_get_attribute_all(self, class_id: int, instance: int) -> bytes:
        """Get_Attribute_All 响应"""
        if class_id == CIP_CLASS_IDENTITY:
            all_data = b""
            for attr in [1, 2, 3, 4, 5, 6, 7]:
                if attr in self._identity_data:
                    val = self._identity_data[attr]
                    if isinstance(val, str):
                        val = val.encode('utf-8')
                    all_data += val
            return bytes([CIP_SERVICES['Get_Attribute_All'] | 0x80, 0x00]) + all_data
        return bytes([CIP_SERVICES['Get_Attribute_All'] | 0x80, 0x08])

    def _cip_reset(self, class_id: int, instance: int) -> bytes:
        """Reset 响应"""
        return bytes([CIP_SERVICES['Reset'] | 0x80, 0x00])

    def _cip_start(self, class_id: int, instance: int) -> bytes:
        """Start 响应"""
        return bytes([CIP_SERVICES['Start'] | 0x80, 0x00])

    def _cip_stop(self, class_id: int, instance: int) -> bytes:
        """Stop 响应"""
        return bytes([CIP_SERVICES['Stop'] | 0x80, 0x00])

    def _build_cip_error_response(self, error_code: int) -> bytes:
        """构建 CIP 错误响应"""
        cpf = struct.pack("<IHH", 0, 0, 2)
        cpf += struct.pack("<HH", CPF_ITEM_NULL, 0)
        cip_error = bytes([0x80, error_code])
        cpf += struct.pack("<HH", CPF_ITEM_UNCONNECTED_MESSAGE, len(cip_error)) + cip_error
        return cpf

    # ----- 响应构建 -----
    def _build_list_identity(self) -> bytes:
        """构建 ListIdentity 响应"""
        identity = b""
        identity += struct.pack("<H", 1)       # Vendor ID
        identity += struct.pack("<H", 1)       # Device Type
        identity += struct.pack("<H", 1)       # Product Code
        identity += struct.pack("<BB", 1, 0)   # Revision
        identity += struct.pack("<H", 0)       # Status
        identity += struct.pack("<I", 0x12345678)  # Serial Number
        product_name = b"ENIP Simulator"
        identity += struct.pack("<B", len(product_name)) + product_name
        identity += struct.pack("<B", 0x01)    # State

        # CPF 包装
        cpf = struct.pack("<H", 1)  # Item count
        cpf += struct.pack("<HH", CPF_ITEM_IDENTITY, len(identity))
        cpf += identity
        return cpf

    def _build_list_services(self) -> bytes:
        """构建 ListServices 响应"""
        name = b"Communications"
        item_data = struct.pack("<HH", 0x0100, 0x0001)  # Protocol version, capability flags
        item_data += struct.pack("<B", len(name)) + name

        resp = struct.pack("<H", 1)  # Item count
        resp += struct.pack("<HH", 0x0100, len(item_data))  # Item type, length
        resp += item_data
        return resp

    def _build_list_interfaces(self) -> bytes:
        """构建 ListInterfaces 响应"""
        item_data = struct.pack("<HHH", 0x0100, 0x0001, 0x0001)
        resp = struct.pack("<H", 1)  # Item count
        resp += struct.pack("<HH", 0x0002, len(item_data))
        resp += item_data
        return resp

    # ----- 工具方法 -----
    def _recv_all(self, sock: socket.socket, length: int, timeout: float) -> bytes:
        """接收指定长度数据"""
        data = b""
        sock.settimeout(timeout)
        while len(data) < length:
            try:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                break
        return data

    def status(self) -> Dict[str, Any]:
        """获取服务端状态"""
        with self._lock:
            return {
                'running': self._tcp_running,
                'host': self._host,
                'port': self._port,
                'io_port': self._io_port,
                'tcp_running': self._tcp_running,
                'udp_running': self._udp_running,
                'io_running': self._io_running,
                'active_sessions': len(self._sessions)
            }


# ----- 导出 -----
__all__ = [
    'EnipClient',
    'EnipServer',
    'build_enip_header',
    'parse_enip_header',
    'build_ucmm_cpf',
    'build_connection_based_cpf',
    'build_io_packet',
    'build_cip_request',
    'build_cip_read_request',
    'build_cip_write_request',
    'parse_cpf_items',
    'parse_cip_path',
    'parse_io_packet',
    'ENIP_COMMANDS',
    'ENIP_CMD_NAMES',
    'CIP_SERVICES',
    'CIP_SERVICE_NAMES',
    'ENIP_PORT',
    'ENIP_IO_PORT',
    'CPF_ITEM_NAMES',
    'CPF_ITEM_NULL',
    'CPF_ITEM_UNCONNECTED_MESSAGE',
    'CPF_ITEM_CONNECTED_DATA',
    'CPF_ITEM_CONNECTION_BASED',
    'CPF_ITEM_SEQUENCED_ADDR',
]