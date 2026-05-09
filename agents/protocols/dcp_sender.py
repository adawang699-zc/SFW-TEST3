# -*- coding: utf-8 -*-
"""
PROFINET DCP 发送端模块 - 基于 scapy/struct 实现
支持帧类型、服务码、服务类型、Option/Suboption 可配置
适配 Ubuntu namespace 环境
"""
import struct
import time
import threading
import logging

try:
    from scapy.all import Ether, Raw, sendp
except ImportError:
    raise ImportError("请安装 scapy 库: pip install scapy")

logger = logging.getLogger(__name__)


# ===================== 常量映射 =====================
FRAME_ID_MAP = {
    "HELLO": 0xFEFC,
    "GETORSET": 0xFEFD,
    "IDENT_REQ": 0xFEFF,
    "IDENT_RES": 0xFEFE,
}

SERVICE_ID_MAP = {
    "GET": 0x03,
    "SET": 0x04,
    "IDENTIFY": 0x05,
    "HELLO": 0x00,
    "RESERV": 0x06,
    "MANU": 0x07,
}

SERVICE_TYPE_MAP = {
    "request": 0x00,
    "response_success": 0x01,
}

OPTION_MAP = {
    "IP": 0x01,
    "DEVICE": 0x02,
    "DHCP": 0x03,
    "RESERVED": 0x04,
    "CONTROL": 0x05,
    "DEVICEINITIATIVE": 0x06,
    "NME_PARAMS": 0x07,
    "MANUF_X80": 0x80,
    "MANUF_X81": 0x81,
    "MANUF_X82": 0x82,
    "MANUF_X83": 0x83,
    "MANUF_X84": 0x84,
    "MANUF_X85": 0x85,
    "MANUF_X86": 0x86,
    "ALLSECECTOR": 0xFF,
}

OPTION_SUBOPTIONS = {
    0x01: [(0x01, "MAC address"), (0x02, "IP parameter"), (0x03, "Full IP suite")],
    0x02: [
        (0x01, "Type of Station"), (0x02, "Name of Station"), (0x03, "Device ID"),
        (0x04, "Device Role"), (0x05, "Device Options"), (0x06, "Alias Name"),
        (0x07, "Device Instance"), (0x08, "OEM Device ID"),
    ],
    0x03: [
        (0x0C, "Host name"), (0x2B, "Vendor specific"), (0x36, "Server identifier"),
        (0x37, "Parameter request list"), (0x3C, "Class identifier"),
        (0x3D, "DHCP client identifier"), (0x51, "FQDN"),
        (0x61, "UUID/GUID-based Client"), (0xFF, "Control DHCP"),
    ],
    0x04: [(0x01, "Reserved 0x01")],
    0x05: [
        (0x01, "Start Transaction"), (0x02, "End Transaction"), (0x03, "Signal"),
        (0x04, "Response"), (0x05, "Reset Factory Settings"), (0x06, "Reset to Factory"),
    ],
    0x06: [(0x01, "Device Initiative")],
    0x07: [
        (0x01, "NME Domain Name"), (0x02, "NME Manager"),
        (0x03, "NME Paramater UUID"), (0x04, "NME Agent"), (0x05, "CIM Interface"),
    ],
    0x80: [(0x01, "Manufacturer 0x80")],
    0x81: [(0x01, "Manufacturer 0x81")],
    0x82: [(0x01, "Manufacturer 0x82")],
    0x83: [(0x01, "Manufacturer 0x83")],
    0x84: [(0x01, "Manufacturer 0x84")],
    0x85: [(0x01, "Manufacturer 0x85")],
    0x86: [(0x01, "Manufacturer 0x86")],
    0xFF: [(0xFF, "ALL Selector")],
}


def get_suboptions_for_option(option_value):
    """根据 Option 数值返回 [(suboption_value, display_name), ...]"""
    return OPTION_SUBOPTIONS.get(option_value, [])


def build_and_send_dcp_request(iface, frame_id, service_id, service_type, option, suboption,
                               dst_mac="01:0e:cf:00:00:00", src_mac="00:11:22:33:44:55", xid=0x00010203):
    """构造并发送单条 DCP 请求报文"""
    multi_blocks = struct.pack("!BB", option, suboption)
    dcp_data_length = len(multi_blocks)
    dcp_core = struct.pack(
        "!H BB I H H",
        frame_id & 0xFFFF,
        service_id & 0xFF,
        service_type & 0xFF,
        xid & 0xFFFFFFFF,
        0x0000,
        dcp_data_length
    ) + multi_blocks
    pkt = Ether(dst=dst_mac, src=src_mac, type=0x8892) / Raw(dcp_core)
    sendp(pkt, iface=iface, verbose=0)
    time.sleep(0.05)


class DcpSenderService:
    """DCP 发送服务"""

    def __init__(self, iface="eth0"):
        self.iface = iface
        self.is_running = False
        self.thread = None
        self.frame_type = "GETORSET"
        self.service_code = "GET"
        self.service_type = "request"
        self.option = "IP"
        self.suboptions = [0x01]
        self.dst_mac = "01:0e:cf:00:00:00"
        self.src_mac = "00:11:22:33:44:55"
        self.packet_count = 0
        self.callback = None

    def set_config(self, config):
        self.frame_type = config.get("frame_type", "GETORSET")
        self.service_code = config.get("service_code", "GET")
        self.service_type = config.get("service_type", "request")
        self.option = config.get("option", "IP")
        subs = config.get("suboptions")
        if isinstance(subs, (list, tuple)) and len(subs) > 0:
            self.suboptions = list(int(s) & 0xFF for s in subs if s is not None)
        elif subs is not None:
            self.suboptions = [int(subs) & 0xFF]
        else:
            sub = config.get("suboption")
            self.suboptions = [int(sub) & 0xFF] if sub is not None else [0x01]
        if not self.suboptions:
            self.suboptions = [0x01]
        self.dst_mac = config.get("dst_mac", "01:0e:cf:00:00:00")
        self.src_mac = config.get("src_mac", "00:11:22:33:44:55")

    def set_callback(self, callback):
        self.callback = callback

    def start(self):
        if self.is_running:
            return False
        try:
            if not self.iface:
                if self.callback:
                    self.callback({"error": "网卡名称未设置"})
                return False
            frame_id = FRAME_ID_MAP.get(self.frame_type, 0xFEFD)
            service_id = SERVICE_ID_MAP.get(self.service_code, 0x03)
            service_type = SERVICE_TYPE_MAP.get(self.service_type, 0x00)
            option_val = OPTION_MAP.get(self.option, 0x01)
            if not self.suboptions:
                if self.callback:
                    self.callback({"error": "请至少选择一个子选项"})
                return False
            self.is_running = True
            self.thread = threading.Thread(target=self._send_loop, daemon=True,
                                          args=(frame_id, service_id, service_type, option_val))
            self.thread.start()
            logger.info(f"DCP 发送服务启动: iface={self.iface}")
            return True
        except Exception as e:
            logger.error(f"DCP 启动失败: {e}")
            if self.callback:
                self.callback({"error": str(e)})
            return False

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("DCP 发送服务停止")

    def get_packet_count(self):
        return self.packet_count

    def reset_packet_count(self):
        self.packet_count = 0

    def _send_loop(self, frame_id, service_id, service_type, option_val):
        idx = 0
        subopts = list(self.suboptions)
        n = len(subopts)
        while self.is_running and n:
            suboption_val = subopts[idx % n]
            idx += 1
            try:
                build_and_send_dcp_request(
                    self.iface,
                    frame_id,
                    service_id,
                    service_type,
                    option_val,
                    suboption_val,
                    dst_mac=self.dst_mac,
                    src_mac=self.src_mac,
                )
                self.packet_count += 1
                if self.callback:
                    self.callback({"count": self.packet_count})
            except Exception as e:
                logger.error(f"DCP send error: {e}")
                if self.callback:
                    self.callback({"error": str(e)})
                break
            time.sleep(0.1)