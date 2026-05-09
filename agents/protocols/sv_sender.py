"""
SV 发送端模块 - 基于 scapy 实现
适配 Ubuntu namespace 环境
"""
try:
    from scapy.all import Ether, sendp, get_if_hwaddr, Raw
except ImportError:
    raise ImportError("请安装 scapy 库: pip install scapy")

import threading
import time
import logging
from asn1_encoder import IEC61850Encoder

logger = logging.getLogger(__name__)


class SVSenderService:
    """SV 发送服务类"""

    # SV 组播 MAC 地址范围: 01-0C-CD-04-00-00 到 01-0C-CD-04-01-FF
    SV_MULTICAST_MAC = "01:0C:CD:04:00:01"
    SV_ETHER_TYPE = 0x88BA

    def __init__(self, iface="eth0"):
        """初始化 SV 发送服务

        Args:
            iface: 网卡接口名称（直接使用，无需解析）
        """
        self.iface = iface
        self.is_running = False
        self.thread = None
        self.config = {
            "appid": 0x4019,  # APPID范围：0x4000~0x7FFF
            "svid": "SV_Line1",
            "confrev": 1,
            "smpcnt": 0,
            "smpsynch": True,
            "samples": {
                "Voltage_A": 220.1,
                "Voltage_B": 219.8,
                "Voltage_C": 220.3,
                "Current_A": 10.2,
                "Current_B": 10.5,
                "Current_C": 10.1
            }
        }
        self.callback = None
        self.src_mac = None
        self.packet_count = 0

    def _get_src_mac(self):
        """获取源 MAC 地址"""
        try:
            self.src_mac = get_if_hwaddr(self.iface)
            return self.src_mac
        except Exception as e:
            logger.warning(f"获取 MAC 地址失败: {e}, 使用默认 MAC")
            self.src_mac = "00:00:00:00:00:01"
            return self.src_mac

    def set_config(self, config):
        """设置配置"""
        self.config.update(config)

    def set_callback(self, callback):
        """设置发送回调函数"""
        self.callback = callback

    def start(self):
        """启动发送"""
        if self.is_running:
            return False

        try:
            if not self.iface:
                if self.callback:
                    self.callback({"error": "网卡名称未设置"})
                return False

            # 获取源 MAC 地址
            self._get_src_mac()
            self.is_running = True
            self.thread = threading.Thread(target=self._send_loop, daemon=True)
            self.thread.start()
            logger.info(f"SV 发送服务启动: iface={self.iface}")
            return True
        except Exception as e:
            logger.error(f"SV 启动失败: {e}")
            if self.callback:
                self.callback({"error": f"启动失败: {str(e)}"})
            return False

    def stop(self):
        """停止发送"""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        logger.info("SV 发送服务停止")

    def get_packet_count(self):
        """获取已发送报文数量"""
        return self.packet_count

    def reset_packet_count(self):
        """重置报文计数"""
        self.packet_count = 0

    def _send_loop(self):
        """发送循环"""
        while self.is_running:
            try:
                # 编码完整的 SV 报文（包含头部和 PDU）
                sv_packet = IEC61850Encoder.encode_sv_packet(self.config)

                if not sv_packet:
                    if self.callback:
                        self.callback({"error": "SV packet is None"})
                    break

                if len(sv_packet) == 0:
                    if self.callback:
                        self.callback({"error": "SV packet is empty (0 bytes)"})
                    break

                if len(sv_packet) < 8:
                    if self.callback:
                        self.callback({"error": f"SV packet too short: {len(sv_packet)} bytes (minimum 8)"})
                    break

                pdu_data = sv_packet[8:]
                if len(pdu_data) == 0:
                    if self.callback:
                        self.callback({"error": "SV PDU is empty (0 bytes after header)"})
                    break

                import struct
                appid, length, reserved1, reserved2 = struct.unpack('>HHHH', sv_packet[:8])
                expected_length = 4 + len(pdu_data)
                if length != expected_length:
                    if self.callback:
                        self.callback({"error": f"SV length field mismatch: header says {length}, expected {expected_length}"})
                    break

                frame = Ether(
                    dst=self.SV_MULTICAST_MAC,
                    src=self.src_mac,
                    type=self.SV_ETHER_TYPE
                ) / Raw(load=sv_packet)

                sendp(frame, iface=self.iface, verbose=0)

                self.packet_count += 1

                if self.callback:
                    self.callback(self.config.copy())

                self.config["smpcnt"] += 1
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"SV send error: {e}")
                if self.callback:
                    self.callback({"error": f"SV send error: {str(e)}"})
                import traceback
                traceback.print_exc()
                break