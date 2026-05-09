# -*- coding: utf-8 -*-
"""
POWERLINK 发送端模块 - 基于 scapy 实现
支持 SoC、Preq、Pres、SoA、ASnd、AMNI 报文发送
适配 Ubuntu namespace 环境
"""
import time
import threading
import logging
import warnings
warnings.filterwarnings("ignore", category=Warning)

try:
    from scapy.all import (
        Ether, sendp, Packet,
        ByteField, ShortField, IntField, StrField,
        bind_layers
    )
except ImportError:
    raise ImportError("请安装 scapy 库: pip install scapy")

logger = logging.getLogger(__name__)


# ===================== 1. SoC 协议层 =====================
class POWERLINK_SoC(Packet):
    name = "POWERLINK SoC Protocol"
    fields_desc = [
        ByteField("SID", 0x01),
        ByteField("DA", 0xFF),
        ByteField("SA", 0xF0),
        ShortField("SyncCounter", 0x6000),
        ByteField("Reserved1", 0x00),
        ByteField("Reserved2", 0x00),
        ByteField("Reserved3", 0x00),
        ByteField("Reserved4", 0x00),
        IntField("Timestamp", 0x0000bebc),
        IntField("CycleControl", 0x200400d0),
        StrField("Reserved5", b"\x30\x00" + b"\x00" * 28)
    ]


# ===================== 2. PReq 协议层 =====================
class POWERLINK_PReq(Packet):
    name = "POWERLINK PReq Protocol"
    fields_desc = [
        ByteField("SID", 0x03),
        ByteField("DA", 0x11),
        ByteField("SA", 0xF0),
        ByteField("Reserved", 0x00),
        ByteField("Flags", 0x00),
        ByteField("FLS_SLS", 0x00),
        ByteField("PDOVersion", 0x02),
        ByteField("Unparsed", 0x00),
        ShortField("Size", 0x0000),
        StrField("PDOData", b"\xaa" * 8 + b"\x00" * 16 + b"\xaa" * 1 + b"\x00" * 12)
    ]


# ===================== 3. PRes 协议层 =====================
class POWERLINK_PRes(Packet):
    name = "POWERLINK PRes Protocol"
    fields_desc = [
        ByteField("SID", 0x04),
        ByteField("DA", 0x11),
        ByteField("SA", 0xF0),
        ByteField("NMTStatus", 0x6d),
        ByteField("Flags", 0x00),
        ByteField("FLS_SLS_PR_RS", 0x00),
        ByteField("Version", 0x02),
        ByteField("Reserved", 0x00),
        ShortField("Size", 0x2000),
        StrField("PDOData", b"\xbb" * 8 + b"\x00" * 16 + b"\xbb" * 1 + b"\x00" * 12)
    ]


# ===================== 4. SoA 协议层 =====================
class POWERLINK_SoA(Packet):
    name = "POWERLINK SoA Protocol"
    fields_desc = [
        ByteField("SID", 0x05),
        ByteField("DA", 0xFF),
        ByteField("SA", 0xF0),
        ByteField("NMTStatus", 0x6d),
        ByteField("Flags", 0x00),
        ByteField("FLS_SLS_PR_RS", 0x00),
        ByteField("Version", 0x02),
        ByteField("Reserved", 0x00),
        ShortField("Size", 0x2000),
        StrField("Data", b"\xcc" * 8 + b"\x00" * 24)
    ]


# ===================== 5. ASnd 协议层 =====================
class POWERLINK_ASnd(Packet):
    name = "POWERLINK ASnd Protocol"
    fields_desc = [
        ByteField("SID", 0x06),
        ByteField("DA", 0xFF),
        ByteField("SA", 0xF0),
        ByteField("NMTStatus", 0x6d),
        ByteField("Flags", 0x00),
        ByteField("Type", 0x01),
        ByteField("Version", 0x02),
        ByteField("Reserved", 0x00),
        ShortField("Size", 0x1000),
        StrField("Data", b"\xdd" * 8 + b"\x00" * 24)
    ]


# ===================== 6. AMNI 协议层 =====================
class POWERLINK_AMNI(Packet):
    name = "POWERLINK AMNI Protocol"
    fields_desc = [
        ByteField("SID", 0x07),
        ByteField("DA", 0xFF),
        ByteField("SA", 0xF0),
        ByteField("NodeID", 0x11),
        ByteField("Flags", 0x00),
        ByteField("Code", 0x00),
        ByteField("Version", 0x02),
        ByteField("Reserved", 0x00),
        ShortField("Size", 0x0800),
        StrField("Data", b"\xee" * 8 + b"\x00" * 24)
    ]


bind_layers(Ether, POWERLINK_SoC, type=0x88AB)
bind_layers(Ether, POWERLINK_PReq, type=0x88AB)
bind_layers(Ether, POWERLINK_PRes, type=0x88AB)
bind_layers(Ether, POWERLINK_SoA, type=0x88AB)
bind_layers(Ether, POWERLINK_ASnd, type=0x88AB)
bind_layers(Ether, POWERLINK_AMNI, type=0x88AB)


# 服务类型名称 -> 报文类
SERVICE_TYPE_MAP = {
    "SoC": POWERLINK_SoC,
    "Preq": POWERLINK_PReq,
    "Pres": POWERLINK_PRes,
    "SoA": POWERLINK_SoA,
    "ASnd": POWERLINK_ASnd,
    "AMNI": POWERLINK_AMNI,
}


def _clamp_node(v):
    """将节点号限制在 0-255"""
    try:
        n = int(v)
        return max(0, min(255, n))
    except (TypeError, ValueError):
        return 0


def send_powerlink_packet(iface, pkt_class, dst_mac, src_mac, sa, da):
    """发送单条 POWERLINK 报文"""
    sa_byte = _clamp_node(sa)
    da_byte = _clamp_node(da)
    frame = Ether(dst=dst_mac, src=src_mac, type=0x88AB) / pkt_class()
    pl = frame.getlayer(1)
    if pl is not None:
        pl.SA = sa_byte
        pl.DA = da_byte
    sendp(frame, iface=iface, verbose=0)
    time.sleep(0.05)
    return frame


class PowerlinkSenderService:
    """POWERLINK 发送服务"""

    def __init__(self, iface="eth0"):
        self.iface = iface
        self.is_running = False
        self.thread = None
        self.service_types = ["SoC"]
        self.sa = 240
        self.da = 17
        self.dst_mac = "01:11:1e:00:00:01"
        self.src_mac = "00:50:c2:31:3f:dd"
        self.packet_count = 0
        self.callback = None

    def set_config(self, config):
        self.service_types = config.get("service_types", ["SoC"])
        self.sa = _clamp_node(config.get("sa", 240))
        self.da = _clamp_node(config.get("da", 17))
        self.dst_mac = config.get("dst_mac", "01:11:1e:00:00:01")
        self.src_mac = config.get("src_mac", "00:50:c2:31:3f:dd")

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
            allowed = [t for t in self.service_types if t in SERVICE_TYPE_MAP]
            if not allowed:
                if self.callback:
                    self.callback({"error": "请至少选择一种服务类型（SoC/Preq/Pres/SoA/ASnd/AMNI）"})
                return False
            self.service_types = allowed
            self.is_running = True
            self.thread = threading.Thread(target=self._send_loop, daemon=True)
            self.thread.start()
            logger.info(f"POWERLINK 发送服务启动: iface={self.iface}")
            return True
        except Exception as e:
            logger.error(f"POWERLINK 启动失败: {e}")
            if self.callback:
                self.callback({"error": str(e)})
            return False

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("POWERLINK 发送服务停止")

    def get_packet_count(self):
        return self.packet_count

    def reset_packet_count(self):
        self.packet_count = 0

    def _send_loop(self):
        idx = 0
        types = self.service_types
        n = len(types)
        while self.is_running and n:
            name = types[idx % n]
            idx += 1
            pkt_class = SERVICE_TYPE_MAP.get(name)
            if pkt_class is None:
                continue
            try:
                send_powerlink_packet(
                    self.iface,
                    pkt_class,
                    self.dst_mac,
                    self.src_mac,
                    self.sa,
                    self.da,
                )
                self.packet_count += 1
                if self.callback:
                    self.callback({"service_type": name, "count": self.packet_count})
            except Exception as e:
                logger.error(f"POWERLINK send error: {e}")
                if self.callback:
                    self.callback({"error": str(e)})
                break