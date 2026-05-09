# -*- coding: utf-8 -*-
"""
EtherCAT 发送端模块 - 基于 scapy 实现
支持数据单元类型 1 (Ethercat DLPDU) 及多种命令码
适配 Ubuntu namespace 环境
"""
import struct
import time
import threading
import logging

try:
    from scapy.all import (
        Ether, sendp, Raw,
        BitField, ByteField, ShortField, StrLenField, Packet,
    )
except ImportError:
    raise ImportError("请安装 scapy 库: pip install scapy")

logger = logging.getLogger(__name__)


# ===================== ECAT 帧头 =====================
class ECAT_Header(Packet):
    name = "EtherCAT Header"
    fields_desc = [
        BitField("type", 0x01, 4),
        BitField("reserved", 0, 1),
        BitField("len", 0, 11)
    ]

    def post_build(self, pkt, pay):
        val = (self.type << 12) | (self.reserved << 11) | self.len
        hex_16bit = struct.pack(">H", val)
        hex_reversed = hex_16bit[::-1]
        return hex_reversed + pay


# ===================== ECAT 数据报 =====================
class ECAT_Datagram(Packet):
    name = "EtherCAT Datagram"
    fields_desc = [
        ByteField("cmd", 0x01),
        ByteField("index", 0x00),
        ShortField("dlen", 0x00),
        ShortField("adp", 0x0001),
        ShortField("ado", 0x0130),
        ShortField("cnt", 0x0000),
        StrLenField("data", "", length_from=lambda pkt: pkt.dlen),
        ShortField("wkc", 0x0000)
    ]


# ===================== 协议绑定 =====================
try:
    from scapy.all import bind_layers
    bind_layers(Ether, ECAT_Header, type=0x88A4)
    bind_layers(ECAT_Header, ECAT_Datagram)
except Exception:
    pass


# 命令码名称映射
ECAT_CMD_NAMES = {
    0x00: "NOP - 空操作",
    0x01: "APRD - 物理读",
    0x02: "APWR - 物理写",
    0x03: "APRW - 物理读写",
    0x04: "FPRD - 配置读",
    0x05: "FPWR - 配置写",
    0x06: "FPRW - 配置读写",
    0x07: "保留命令",
    0x08: "BRD - 广播读",
    0x09: "BWR - 广播写",
    0x0A: "LRD - 逻辑读",
    0x0B: "LWR - 逻辑写",
    0x0C: "LRW - 逻辑读写",
    0x0D: "LRMW - 逻辑读改写",
    0x0E: "ARMW - 物理读改写",
    0x0F: "FRMW - 配置读改写"
}


def get_ecat_cmd_name(cmd_code):
    return ECAT_CMD_NAMES.get(cmd_code, f"未知命令0x{cmd_code:02X}")


def _default_params(cmd_code, read_len=2):
    """每个命令的默认参数"""
    if cmd_code == 0x00:
        return 0, b"", 0x0001, 0x0130
    if cmd_code in (0x01, 0x04, 0x08, 0x0A):
        dlen = read_len
        data = b""
    elif cmd_code in (0x0D, 0x0E, 0x0F):
        dlen = 4
        data = b"\xFF\x00\x00\x55"
    elif cmd_code in (0x02, 0x09, 0x0B):
        dlen = 2
        data = b"\x11\x22" if cmd_code == 0x02 else (b"\x55\xAA" if cmd_code == 0x09 else b"\x99\x88")
    elif cmd_code in (0x03, 0x06, 0x0C):
        dlen = 2
        data = b"\x33\x44" if cmd_code == 0x03 else (b"\x02\x03" if cmd_code == 0x06 else b"\x77\x66")
    elif cmd_code == 0x05:
        dlen = 2
        data = b"\x00\x01"
    elif cmd_code == 0x07:
        dlen = 0
        data = b""
    else:
        dlen = read_len
        data = b""
    if dlen == 0:
        ado = 0x0030
    elif dlen == 1:
        ado = 0x0130
    elif dlen == 2:
        ado = 0x0230
    elif dlen == 4:
        ado = 0x0430
    else:
        ado = (dlen << 8) | 0x30
    adp = 0x0001
    if cmd_code in (0x04, 0x05, 0x06, 0x0F):
        adp = 0x0000
        if cmd_code != 0x0F:
            ado = 0x0000 if dlen == 0 else ado
    if cmd_code in (0x08, 0x09):
        adp = 0x0000
    if cmd_code in (0x0A, 0x0B, 0x0C, 0x0D):
        adp = 0x1000
        if cmd_code in (0x0A, 0x0B, 0x0C):
            ado = 0x0000
    return dlen, data, adp, ado


def send_ethercat_packet(data_unit_type, cmd_code, iface, read_len=2,
                         send_data=None, adp=None, ado=None, dst_mac="01:01:01:01:01:01"):
    """发送单条 EtherCAT 报文"""
    dlen_val, default_data, default_adp, default_ado = _default_params(cmd_code, read_len)
    if send_data is not None:
        dlen_val = len(send_data)
        default_data = send_data
    if adp is not None:
        default_adp = adp
    if ado is not None:
        default_ado = ado
    datagram_total_len = 10 + dlen_val + 2
    if dlen_val == 1:
        default_ado = 0x0130
    elif dlen_val == 2:
        default_ado = 0x0230
    elif dlen_val == 0:
        default_ado = 0x0030
    elif dlen_val == 4:
        default_ado = 0x0430

    ethercat_pkt = (
        Ether(dst=dst_mac, type=0x88A4)
        / ECAT_Header(type=data_unit_type, reserved=0, len=datagram_total_len)
        / ECAT_Datagram(
            cmd=cmd_code,
            dlen=dlen_val,
            adp=default_adp,
            ado=default_ado,
            data=default_data,
        )
    )
    sendp(ethercat_pkt, iface=iface, verbose=0)
    time.sleep(0.1)
    return ethercat_pkt


class EthercatSenderService:
    """EtherCAT 发送服务类"""

    def __init__(self, iface="eth0"):
        self.iface = iface
        self.is_running = False
        self.thread = None
        self.data_unit_type = 1
        self.command_codes = [0x00]
        self.dst_mac = "01:01:01:01:01:01"
        self.read_len = 2
        self.packet_count = 0
        self.callback = None

    def set_config(self, config):
        self.data_unit_type = config.get("data_unit_type", 1)
        self.command_codes = config.get("command_codes", [0x00])
        self.dst_mac = config.get("dst_mac", "01:01:01:01:01:01")
        self.read_len = config.get("read_len", 2)

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
            if not self.command_codes:
                if self.callback:
                    self.callback({"error": "请至少选择一种命令码"})
                return False
            self.is_running = True
            self.thread = threading.Thread(target=self._send_loop, daemon=True)
            self.thread.start()
            logger.info(f"EtherCAT 发送服务启动: iface={self.iface}")
            return True
        except Exception as e:
            logger.error(f"EtherCAT 启动失败: {e}")
            if self.callback:
                self.callback({"error": str(e)})
            return False

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("EtherCAT 发送服务停止")

    def get_packet_count(self):
        return self.packet_count

    def reset_packet_count(self):
        self.packet_count = 0

    def _send_loop(self):
        idx = 0
        cmd_list = self.command_codes
        n = len(cmd_list)
        while self.is_running and n:
            cmd_code = cmd_list[idx % n]
            idx += 1
            try:
                send_ethercat_packet(
                    self.data_unit_type,
                    cmd_code,
                    self.iface,
                    read_len=self.read_len,
                    dst_mac=self.dst_mac,
                )
                self.packet_count += 1
                if self.callback:
                    self.callback({"cmd": cmd_code, "count": self.packet_count})
            except Exception as e:
                logger.error(f"EtherCAT send error: {e}")
                if self.callback:
                    self.callback({"error": str(e)})
                break