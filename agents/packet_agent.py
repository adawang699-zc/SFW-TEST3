"""
全功能 Agent - 支持：
- 报文发送（TCP/UDP/ICMP/自定义）
- 报文接收/监控
- 工控协议（Modbus/S7/GOOSE/SV/DNP3/BACnet/ENIP/MMS）
- 端口扫描
- 报文回放

每个 Agent 都拥有全部功能
"""

import threading
import time
import socket
import logging
import json
import os
import subprocess
from datetime import datetime
from flask import request, jsonify

from agents.base import BaseAgent

logger = logging.getLogger('agents')

# 尝试导入 Scapy
try:
    from scapy.all import sendp, sniff, Ether, IP, TCP, UDP, ICMP, Raw
    from scapy.volatile import RandShort
    SCAPY_AVAILABLE = True
except ImportError:
    logger.warning("Scapy 未安装，报文发送功能将不可用")
    SCAPY_AVAILABLE = False

# 尝试导入 psutil
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


class FullFeatureAgent(BaseAgent):
    """
    全功能 Agent

    继承 BaseAgent，添加:
    - 报文发送/接收功能
    - 工控协议支持
    - 端口扫描
    - 报文回放
    """

    def __init__(self):
        super().__init__()

        # 功能状态
        self.features = {
            'packet_send': {
                'running': False,
                'thread': None,
                'stats': {'sent': 0, 'rate': 0}
            },
            'packet_receive': {
                'running': False,
                'thread': None,
                'stats': {'received': 0, 'filtered': 0}
            },
            'port_scan': {
                'running': False,
                'thread': None,
                'progress': 0,
                'results': [],
                'target': None
            },
            'packet_replay': {
                'running': False,
                'thread': None,
                'current_file': None,
                'sent': 0,
                'total': 0
            }
        }

        # 停止信号
        self.stop_events = {
            'packet_send': threading.Event(),
            'packet_receive': threading.Event(),
            'port_scan': threading.Event(),
            'packet_replay': threading.Event(),
        }

        # 统计锁
        self.stats_lock = threading.Lock()

        # 注册所有功能 API
        self._register_feature_api()

        logger.info(f"全功能 Agent 初始化完成: {self.agent_id}")

    def _register_feature_api(self):
        """注册所有功能 API"""

        # ========== 报文发送功能 ==========
        self.register_api('/api/send_packet', self._api_send_packet, ['POST'])
        self.register_api('/api/stop_send', self._api_stop_send, ['POST'])
        self.register_api('/api/send_stats', self._api_send_stats, ['GET'])

        # ========== 报文接收功能 ==========
        self.register_api('/api/start_receive', self._api_start_receive, ['POST'])
        self.register_api('/api/stop_receive', self._api_stop_receive, ['POST'])
        self.register_api('/api/receive_stats', self._api_receive_stats, ['GET'])
        self.register_api('/api/received_packets', self._api_received_packets, ['GET'])

        # ========== 工控协议功能 ==========
        self.register_api('/api/protocols', self._api_get_protocols, ['GET'])
        self.register_api('/api/send_protocol', self._api_send_protocol, ['POST'])

        # ========== 端口扫描功能 ==========
        self.register_api('/api/start_scan', self._api_start_scan, ['POST'])
        self.register_api('/api/stop_scan', self._api_stop_scan, ['POST'])
        self.register_api('/api/scan_progress', self._api_scan_progress, ['GET'])
        self.register_api('/api/scan_results', self._api_scan_results, ['GET'])

        # ========== 报文回放功能 ==========
        self.register_api('/api/list_pcap_files', self._api_list_pcap_files, ['GET'])
        self.register_api('/api/start_replay', self._api_start_replay, ['POST'])
        self.register_api('/api/stop_replay', self._api_stop_replay, ['POST'])
        self.register_api('/api/replay_stats', self._api_replay_stats, ['GET'])

        # ========== 综合统计 ==========
        self.register_api('/api/full_stats', self._api_full_stats, ['GET'])

    # ========== 报文发送功能实现 ==========

    def _api_send_packet(self):
        """发送报文 API"""
        if not SCAPY_AVAILABLE:
            return self.json_response({'error': 'Scapy 未安装'}, 500)

        data = request.get_json()

        # 报文配置
        protocol = data.get('protocol', 'tcp')
        src_ip = data.get('src_ip', self.bind_ip)
        dst_ip = data.get('dst_ip')
        src_port = data.get('src_port', 0)
        dst_port = data.get('dst_port', 80)
        payload = data.get('payload', '')

        # 发送配置
        count = data.get('count', 1)
        interval = data.get('interval', 0)
        continuous = data.get('continuous', False)

        if not dst_ip:
            return self.json_response({'error': '缺少目标 IP'}, 400)

        # 构造报文
        try:
            packet = self._build_packet(protocol, src_ip, dst_ip, src_port, dst_port, payload)
        except Exception as e:
            logger.error(f"构造报文失败: {e}")
            return self.json_response({'error': f'构造报文失败: {str(e)}'}, 500)

        # 连续发送模式
        if continuous:
            self.features['packet_send']['running'] = True
            self.stop_events['packet_send'].clear()

            def send_continuous():
                while not self.stop_events['packet_send'].is_set():
                    try:
                        sendp(packet, iface=self.bind_interface, verbose=False)
                        with self.stats_lock:
                            self.features['packet_send']['stats']['sent'] += 1
                        if interval > 0:
                            time.sleep(interval)
                    except Exception as e:
                        logger.error(f"发送报文失败: {e}")
                        break
                self.features['packet_send']['running'] = False
                logger.info(f"连续发送停止: {self.agent_id}")

            self.features['packet_send']['thread'] = threading.Thread(target=send_continuous)
            self.features['packet_send']['thread'].start()

            return self.json_response({
                'status': 'continuous_sending',
                'interface': self.bind_interface,
                'agent_id': self.agent_id
            })

        # 固定次数发送
        else:
            sent_count = 0
            for i in range(count):
                try:
                    sendp(packet, iface=self.bind_interface, verbose=False)
                    sent_count += 1
                    with self.stats_lock:
                        self.features['packet_send']['stats']['sent'] += 1
                    if interval > 0:
                        time.sleep(interval)
                except Exception as e:
                    logger.error(f"发送报文失败: {e}")

            return self.json_response({
                'status': 'sent',
                'sent_count': sent_count,
                'interface': self.bind_interface,
                'agent_id': self.agent_id,
                'total_sent': self.features['packet_send']['stats']['sent']
            })

    def _api_stop_send(self):
        """停止发送 API"""
        self.stop_events['packet_send'].set()
        self.features['packet_send']['running'] = False
        return self.json_response({'status': 'stopped', 'agent_id': self.agent_id})

    def _api_send_stats(self):
        """发送统计 API"""
        with self.stats_lock:
            return self.json_response({
                'running': self.features['packet_send']['running'],
                'total_sent': self.features['packet_send']['stats']['sent'],
                'interface': self.bind_interface,
                'agent_id': self.agent_id
            })

    def _build_packet(self, protocol, src_ip, dst_ip, src_port, dst_port, payload):
        """构造报文"""
        # 以太网层
        ether = Ether()

        # IP 层
        ip = IP(src=src_ip, dst=dst_ip)

        # 根据协议构造不同层
        if protocol == 'tcp':
            sport = RandShort() if src_port == 0 else src_port
            tcp = TCP(sport=sport, dport=dst_port)
            packet = ether / ip / tcp
        elif protocol == 'udp':
            sport = RandShort() if src_port == 0 else src_port
            udp = UDP(sport=sport, dport=dst_port)
            packet = ether / ip / udp
        elif protocol == 'icmp':
            icmp = ICMP()
            packet = ether / ip / icmp
        else:
            packet = ether / ip

        # 添加 payload
        if payload:
            packet = packet / Raw(load=payload.encode() if isinstance(payload, str) else payload)

        return packet

    # ========== 报文接收功能实现 ==========

    def _api_start_receive(self):
        """启动报文接收 API"""
        if not SCAPY_AVAILABLE:
            return self.json_response({'error': 'Scapy 未安装'}, 500)

        data = request.get_json()
        filter_expr = data.get('filter', '')

        self.features['packet_receive']['running'] = True
        self.stop_events['packet_receive'].clear()

        def receive_packets():
            logger.info(f"启动报文接收: {self.agent_id}, filter={filter_expr}")

            def packet_callback(packet):
                if self.stop_events['packet_receive'].is_set():
                    return False

                with self.stats_lock:
                    self.features['packet_receive']['stats']['received'] += 1

                # 这里可以添加报文处理逻辑

            try:
                sniff(iface=self.bind_interface, filter=filter_expr,
                      prn=packet_callback, stop_filter=lambda x: self.stop_events['packet_receive'].is_set())
            except Exception as e:
                logger.error(f"报文接收错误: {e}")

            self.features['packet_receive']['running'] = False
            logger.info(f"报文接收停止: {self.agent_id}")

        self.features['packet_receive']['thread'] = threading.Thread(target=receive_packets)
        self.features['packet_receive']['thread'].start()

        return self.json_response({
            'status': 'receiving',
            'interface': self.bind_interface,
            'filter': filter_expr,
            'agent_id': self.agent_id
        })

    def _api_stop_receive(self):
        """停止报文接收 API"""
        self.stop_events['packet_receive'].set()
        self.features['packet_receive']['running'] = False
        return self.json_response({'status': 'stopped', 'agent_id': self.agent_id})

    def _api_receive_stats(self):
        """接收统计 API"""
        with self.stats_lock:
            return self.json_response({
                'running': self.features['packet_receive']['running'],
                'received': self.features['packet_receive']['stats']['received'],
                'interface': self.bind_interface,
                'agent_id': self.agent_id
            })

    def _api_received_packets(self):
        """获取接收的报文列表（简化版）"""
        return self.json_response({
            'packets': [],
            'agent_id': self.agent_id
        })

    # ========== 工控协议功能实现 ==========

    def _api_get_protocols(self):
        """获取支持的工控协议列表"""
        protocols = [
            {'name': 'modbus-tcp', 'description': 'Modbus TCP 协议', 'default_port': 502},
            {'name': 's7', 'description': '西门子 S7 协议', 'default_port': 102},
            {'name': 'goose', 'description': 'IEC61850 GOOSE', 'default_port': 0},
            {'name': 'sv', 'description': 'IEC61850 SV', 'default_port': 0},
            {'name': 'dnp3', 'description': 'DNP3 协议', 'default_port': 20000},
            {'name': 'bacnet', 'description': 'BACnet 协议', 'default_port': 47808},
            {'name': 'enip', 'description': 'Ethernet/IP 协议', 'default_port': 44818},
            {'name': 'mms', 'description': 'MMS 协议', 'default_port': 102},
        ]
        return self.json_response({
            'protocols': protocols,
            'interface': self.bind_interface,
            'agent_id': self.agent_id
        })

    def _api_send_protocol(self):
        """发送工控协议报文 API"""
        if not SCAPY_AVAILABLE:
            return self.json_response({'error': 'Scapy 未安装'}, 500)

        data = request.get_json()
        protocol = data.get('protocol')
        dst_ip = data.get('dst_ip')
        dst_port = data.get('dst_port')
        payload = data.get('payload', {})

        if not protocol or not dst_ip:
            return self.json_response({'error': '缺少协议或目标 IP'}, 400)

        # TODO: 实现各协议的报文构造
        # 这里暂时使用通用报文发送
        logger.info(f"发送工控协议报文: {protocol} -> {dst_ip}:{dst_port}")

        return self.json_response({
            'status': 'sent',
            'protocol': protocol,
            'interface': self.bind_interface,
            'agent_id': self.agent_id
        })

    # ========== 端口扫描功能实现 ==========

    def _api_start_scan(self):
        """启动端口扫描 API"""
        data = request.get_json()
        target_ip = data.get('target_ip')
        port_range = data.get('port_range', '1-1000')
        scan_type = data.get('scan_type', 'tcp')

        if not target_ip:
            return self.json_response({'error': '缺少目标 IP'}, 400)

        self.features['port_scan']['running'] = True
        self.features['port_scan']['progress'] = 0
        self.features['port_scan']['results'] = []
        self.features['port_scan']['target'] = target_ip
        self.stop_events['port_scan'].clear()

        def scan_ports():
            ports = self._parse_port_range(port_range)
            total = len(ports)
            open_ports = []

            logger.info(f"开始端口扫描: {target_ip}, 范围: {port_range}")

            for i, port in enumerate(ports):
                if self.stop_events['port_scan'].is_set():
                    logger.info("端口扫描被中断")
                    break

                try:
                    if scan_type == 'tcp':
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(0.5)
                        result = sock.connect_ex((target_ip, port))
                        if result == 0:
                            open_ports.append({'port': port, 'status': 'open', 'type': 'tcp'})
                        sock.close()
                except Exception as e:
                    pass

                self.features['port_scan']['progress'] = int((i + 1) / total * 100)

            self.features['port_scan']['results'] = open_ports
            self.features['port_scan']['running'] = False
            logger.info(f"端口扫描完成: 发现 {len(open_ports)} 个开放端口")

        self.features['port_scan']['thread'] = threading.Thread(target=scan_ports)
        self.features['port_scan']['thread'].start()

        return self.json_response({
            'status': 'scanning',
            'target': target_ip,
            'range': port_range,
            'agent_id': self.agent_id
        })

    def _api_stop_scan(self):
        """停止端口扫描 API"""
        self.stop_events['port_scan'].set()
        self.features['port_scan']['running'] = False
        return self.json_response({'status': 'stopped', 'agent_id': self.agent_id})

    def _api_scan_progress(self):
        """获取扫描进度 API"""
        return self.json_response({
            'running': self.features['port_scan']['running'],
            'progress': self.features['port_scan']['progress'],
            'target': self.features['port_scan']['target'],
            'results_count': len(self.features['port_scan']['results']),
            'agent_id': self.agent_id
        })

    def _api_scan_results(self):
        """获取扫描结果 API"""
        return self.json_response({
            'running': self.features['port_scan']['running'],
            'results': self.features['port_scan']['results'],
            'agent_id': self.agent_id
        })

    def _parse_port_range(self, port_range):
        """解析端口范围字符串"""
        ports = []
        for part in port_range.split(','):
            if '-' in part:
                start, end = part.split('-')
                ports.extend(range(int(start), int(end) + 1))
            else:
                ports.append(int(part))
        return sorted(set(ports))

    # ========== 报文回放功能实现 ==========

    def _api_list_pcap_files(self):
        """获取可回放的 PCAP 文件列表"""
        # PCAP 文件目录
        pcap_dir = os.environ.get('PCAP_DIR', '/opt/sfw_deploy/packets')

        files = []
        if os.path.exists(pcap_dir):
            for f in os.listdir(pcap_dir):
                if f.endswith('.pcap') or f.endswith('.pcapng'):
                    filepath = os.path.join(pcap_dir, f)
                    files.append({
                        'name': f,
                        'path': filepath,
                        'size': os.path.getsize(filepath)
                    })

        return self.json_response({
            'files': files,
            'directory': pcap_dir,
            'agent_id': self.agent_id
        })

    def _api_start_replay(self):
        """启动报文回放 API"""
        if not SCAPY_AVAILABLE:
            return self.json_response({'error': 'Scapy 未安装'}, 500)

        data = request.get_json()
        pcap_file = data.get('pcap_file')
        speed = data.get('speed', 1.0)  # 回放速度倍数

        if not pcap_file or not os.path.exists(pcap_file):
            return self.json_response({'error': 'PCAP 文件不存在'}, 400)

        self.features['packet_replay']['running'] = True
        self.features['packet_replay']['current_file'] = pcap_file
        self.features['packet_replay']['sent'] = 0
        self.stop_events['packet_replay'].clear()

        def replay_packets():
            logger.info(f"开始报文回放: {pcap_file}")

            try:
                from scapy.all import PcapReader

                packets = list(PcapReader(pcap_file))
                self.features['packet_replay']['total'] = len(packets)

                for packet in packets:
                    if self.stop_events['packet_replay'].is_set():
                        break

                    try:
                        sendp(packet, iface=self.bind_interface, verbose=False)
                        self.features['packet_replay']['sent'] += 1

                        # 速度控制
                        if speed < 1.0:
                            time.sleep(1.0 / speed - 1.0)
                    except Exception as e:
                        logger.error(f"回放报文失败: {e}")

            except Exception as e:
                logger.error(f"读取 PCAP 文件失败: {e}")

            self.features['packet_replay']['running'] = False
            logger.info(f"报文回放完成: {self.agent_id}")

        self.features['packet_replay']['thread'] = threading.Thread(target=replay_packets)
        self.features['packet_replay']['thread'].start()

        return self.json_response({
            'status': 'replaying',
            'file': pcap_file,
            'agent_id': self.agent_id
        })

    def _api_stop_replay(self):
        """停止报文回放 API"""
        self.stop_events['packet_replay'].set()
        self.features['packet_replay']['running'] = False
        return self.json_response({'status': 'stopped', 'agent_id': self.agent_id})

    def _api_replay_stats(self):
        """获取回放统计 API"""
        return self.json_response({
            'running': self.features['packet_replay']['running'],
            'current_file': self.features['packet_replay']['current_file'],
            'sent': self.features['packet_replay']['sent'],
            'total': self.features['packet_replay']['total'],
            'agent_id': self.agent_id
        })

    # ========== 综合统计 ==========

    def _api_full_stats(self):
        """获取综合统计 API"""
        with self.stats_lock:
            return self.json_response({
                'agent_id': self.agent_id,
                'interface': self.bind_interface,
                'ip': self.bind_ip,
                'port': self.port,
                'uptime': self._get_uptime(),
                'features': {
                    'packet_send': {
                        'running': self.features['packet_send']['running'],
                        'sent': self.features['packet_send']['stats']['sent']
                    },
                    'packet_receive': {
                        'running': self.features['packet_receive']['running'],
                        'received': self.features['packet_receive']['stats']['received']
                    },
                    'port_scan': {
                        'running': self.features['port_scan']['running'],
                        'progress': self.features['port_scan']['progress']
                    },
                    'packet_replay': {
                        'running': self.features['packet_replay']['running'],
                        'sent': self.features['packet_replay']['sent']
                    }
                }
            })


def main():
    """Agent 主入口"""
    agent = FullFeatureAgent()
    agent.start()


if __name__ == '__main__':
    main()