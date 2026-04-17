#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GOOSE/SV API Blueprint
提供GOOSE和SV协议的Flask Blueprint模块，可集成到现有Flask应用
"""

import threading
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

try:
    from flask import Blueprint, request, jsonify, make_response
except ImportError:
    print("请安装 flask: pip install flask")
    exit(1)

# 创建Blueprint
goose_sv_bp = Blueprint('goose_sv', __name__)

# 为Blueprint添加CORS支持
@goose_sv_bp.after_request
def after_request(response):
    """添加CORS响应头"""
    print(f"[GOOSE-SV CORS] after_request: {request.method} {request.path}, status={response.status_code}")
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    print(f"[GOOSE-SV CORS] 添加CORS响应头完成")
    return response

@goose_sv_bp.before_request
def handle_preflight():
    """处理CORS预检请求"""
    print(f"[GOOSE-SV CORS] {request.method} {request.path} from {request.remote_addr}")
    if request.method == 'OPTIONS':
        print(f"[GOOSE-SV CORS] 处理OPTIONS预检请求: {request.path}")
        response = make_response('', 200)  # 明确设置200状态码
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        print(f"[GOOSE-SV CORS] OPTIONS响应头: {dict(response.headers)}")
        return response

# 服务管理器（单例）
class ServiceManager:
    """GOOSE/SV服务管理器"""
    
    def __init__(self):
        self.goose_sender = None
        self.sv_sender = None
        self.goose_config = None
        self.sv_config = None
        self.goose_running = False
        self.sv_running = False
        self.goose_packet_count = 0
        self.sv_packet_count = 0
        self.lock = threading.Lock()
        
        # 尝试导入GOOSE/SV服务类
        self.goose_sender_class = None
        self.sv_sender_class = None
        
        # 尝试从多个路径导入
        import sys
        import os
        
        # 尝试添加goose_sv目录到路径（如果存在）
        # 1. 开发环境路径
        # 2. Agent运行时的相对路径（与industrial_protocol_agent.py同目录下的goose_sv）
        # 3. 绝对路径（开发环境）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        possible_paths = [
            os.path.join(script_dir, 'goose_sv'),  # Agent运行时路径
            os.path.join(script_dir, '..', 'apps', 'goose_sv'),  # 开发环境相对路径
            os.path.join(os.path.dirname(script_dir), 'apps', 'goose_sv'),  # 另一种相对路径
            'D:\\自动化测试\\SFW_CONFIG\\apps\\goose_sv',  # 绝对路径（开发环境）
        ]
        
        for path in possible_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path) and abs_path not in sys.path:
                sys.path.insert(0, abs_path)
                print(f"[DEBUG] 添加路径到sys.path: {abs_path}")
        
        try:
            from goose_sender import GooseSenderService
            self.goose_sender_class = GooseSenderService
            print("[OK] GooseSenderService导入成功")
        except ImportError as e:
            print(f"[WARNING] GooseSenderService导入失败: {e}")
            print("提示: 如果使用GOOSE功能，请确保goose_sender模块可用")
            import traceback
            traceback.print_exc()
        
        try:
            from sv_sender import SVSenderService
            self.sv_sender_class = SVSenderService
            print("[OK] SVSenderService导入成功")
        except ImportError as e:
            print(f"[WARNING] SVSenderService导入失败: {e}")
            print("提示: 如果使用SV功能，请确保sv_sender模块可用")
            import traceback
            traceback.print_exc()
        
        self.ethercat_sender = None
        self.ethercat_config = None
        self.ethercat_running = False
        self.ethercat_packet_count = 0
        self.ethercat_sender_class = None
        try:
            from ethercat_sender import EthercatSenderService
            self.ethercat_sender_class = EthercatSenderService
            print("[OK] EthercatSenderService导入成功")
        except ImportError as e:
            print(f"[WARNING] EthercatSenderService导入失败: {e}")
            import traceback
            traceback.print_exc()

        self.powerlink_sender = None
        self.powerlink_config = None
        self.powerlink_running = False
        self.powerlink_packet_count = 0
        self.powerlink_sender_class = None
        try:
            from powerlink_sender import PowerlinkSenderService
            self.powerlink_sender_class = PowerlinkSenderService
            print("[OK] PowerlinkSenderService导入成功")
        except ImportError as e:
            print(f"[WARNING] PowerlinkSenderService导入失败: {e}")
            import traceback
            traceback.print_exc()

        self.dcp_sender = None
        self.dcp_config = None
        self.dcp_running = False
        self.dcp_packet_count = 0
        self.dcp_sender_class = None
        try:
            from dcp_sender import DcpSenderService, get_suboptions_for_option, OPTION_MAP
            self.dcp_sender_class = DcpSenderService
            self.dcp_suboptions_fn = get_suboptions_for_option
            self.dcp_option_map = OPTION_MAP
            print("[OK] DcpSenderService导入成功")
        except ImportError as e:
            print(f"[WARNING] DcpSenderService导入失败: {e}")
            import traceback
            traceback.print_exc()
    
    def start_goose(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict]]:
        """启动GOOSE服务"""
        with self.lock:
            if self.goose_running:
                return False, 'GOOSE服务已在运行', None
            
            if self.goose_sender_class is None:
                return False, 'GooseSenderService未导入，请检查goose_sender模块', None
            
            try:
                # 从config中提取参数
                interface = config.get('interface', '以太网')
                appid = config.get('appid', 256)
                gocb_ref = config.get('gocb_ref', "IED1/LLN0$GO$GSE1")
                dataset = config.get('dataset', "IED1/LLN0$DataSet1")
                data_content = config.get('data_content', {"Switch_1": True, "Switch_2": False})
                
                # 将友好名称解析为 Scapy 可用的接口名（兼容 Win7/Win10 差异）
                try:
                    from network_utils import find_interface_by_name, validate_interface
                    resolved = find_interface_by_name(interface)
                    if resolved:
                        interface = resolved
                    is_valid, msg = validate_interface(interface)
                    if not is_valid:
                        return False, f'GOOSE服务启动失败（网卡验证失败: {msg}）', None
                except ImportError:
                    pass  # 无 network_utils 时沿用原逻辑
                
                # 创建GOOSE发送服务实例（传入网卡名称，已解析为 Scapy 名）
                self.goose_sender = self.goose_sender_class(iface=interface)
                
                # 重置报文计数
                if hasattr(self.goose_sender, 'reset_packet_count'):
                    self.goose_sender.reset_packet_count()
                
                # 设置配置
                goose_config = {
                    "appid": appid,
                    "gocb_ref": gocb_ref,
                    "datset": dataset,  # 注意：goose_sender中使用的是"datset"而不是"dataset"
                    "data": data_content
                }
                self.goose_sender.set_config(goose_config)
                
                # 启动服务
                if not self.goose_sender.start():
                    return False, 'GOOSE服务启动失败（请检查网卡配置；若为 Win10 请确认 Npcap 已安装且网卡名称与列表一致）', None
                
                self.goose_config = config
                self.goose_running = True
                self.goose_packet_count = 0  # 启动时重置计数
                return True, 'GOOSE服务启动成功', {
                    'status': 'running',
                    'start_time': datetime.now().isoformat(),
                    'config': config
                }
            except Exception as e:
                import traceback
                traceback.print_exc()
                return False, f'启动GOOSE服务失败: {str(e)}', None
    
    def stop_goose(self) -> Tuple[bool, str]:
        """停止GOOSE服务"""
        with self.lock:
            if not self.goose_running:
                return False, 'GOOSE服务未运行'
            
            try:
                if self.goose_sender:
                    # 停止服务（假设有stop方法）
                    if hasattr(self.goose_sender, 'stop'):
                        self.goose_sender.stop()
                    elif hasattr(self.goose_sender, 'close'):
                        self.goose_sender.close()
                
                self.goose_sender = None
                self.goose_config = None
                self.goose_running = False
                self.goose_packet_count = 0  # 停止时重置计数
                return True, 'GOOSE服务已停止'
            except Exception as e:
                return False, f'停止GOOSE服务失败: {str(e)}'
    
    def get_goose_status(self) -> Dict[str, Any]:
        """获取GOOSE服务状态"""
        with self.lock:
            # 尝试从goose_sender获取实际发送的报文数量
            packet_count = self.goose_packet_count
            if self.goose_sender and hasattr(self.goose_sender, 'get_packet_count'):
                try:
                    packet_count = self.goose_sender.get_packet_count()
                except:
                    pass
            
            return {
                'running': self.goose_running,
                'config': self.goose_config,
                'start_time': self.goose_config.get('start_time') if self.goose_config else None,
                'packet_count': packet_count
            }
    
    def start_sv(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict]]:
        """启动SV服务"""
        with self.lock:
            if self.sv_running:
                return False, 'SV服务已在运行', None
            
            if self.sv_sender_class is None:
                return False, 'SVSenderService未导入，请检查sv_sender模块', None
            
            try:
                # 从config中提取参数
                interface = config.get('interface', '以太网')
                appid = config.get('appid', 16409)
                svid = config.get('svid', 'SV_Line1')
                smp_rate = config.get('smp_rate', 50)
                sampled_values = config.get('sampled_values', {
                    "Voltage_A": 22010,
                    "Voltage_B": 21980,
                    "Voltage_C": 22030,
                    "Current_A": 1020,
                    "Current_B": 1050,
                    "Current_C": 1010
                })
                
                # 将友好名称解析为 Scapy 可用的接口名（兼容 Win7/Win10 差异）
                try:
                    from network_utils import find_interface_by_name, validate_interface
                    resolved = find_interface_by_name(interface)
                    if resolved:
                        interface = resolved
                    is_valid, msg = validate_interface(interface)
                    if not is_valid:
                        return False, f'SV服务启动失败（网卡验证失败: {msg}）', None
                except ImportError:
                    pass
                
                # 创建SV发送服务实例（传入网卡名称，已解析为 Scapy 名）
                self.sv_sender = self.sv_sender_class(iface=interface)
                
                # 重置报文计数
                if hasattr(self.sv_sender, 'reset_packet_count'):
                    self.sv_sender.reset_packet_count()
                
                # 设置配置：ASDU 含 svID(80)、smpCnt(82)、confRev(83)、smpSynch(85)、seqData(87)；svID 前端传入，confRev 默认 1，smpSynch 默认 1
                sv_config = {
                    "appid": appid,
                    "svid": svid,
                    "confrev": config.get('confrev', 1),
                    "smpsynch": config.get('smpsynch', True),
                    "samples": sampled_values
                }
                self.sv_sender.set_config(sv_config)
                
                # 启动服务
                if not self.sv_sender.start():
                    return False, 'SV服务启动失败（请检查网卡配置；若为 Win10 请确认 Npcap 已安装且网卡名称与列表一致）', None
                
                self.sv_config = config
                self.sv_running = True
                self.sv_packet_count = 0  # 启动时重置计数
                return True, 'SV服务启动成功', {
                    'status': 'running',
                    'start_time': datetime.now().isoformat(),
                    'config': config
                }
            except Exception as e:
                import traceback
                traceback.print_exc()
                return False, f'启动SV服务失败: {str(e)}', None
    
    def stop_sv(self) -> Tuple[bool, str]:
        """停止SV服务"""
        with self.lock:
            if not self.sv_running:
                return False, 'SV服务未运行'
            
            try:
                if self.sv_sender:
                    if hasattr(self.sv_sender, 'stop'):
                        self.sv_sender.stop()
                    elif hasattr(self.sv_sender, 'close'):
                        self.sv_sender.close()
                
                self.sv_sender = None
                self.sv_config = None
                self.sv_running = False
                self.sv_packet_count = 0  # 停止时重置计数
                return True, 'SV服务已停止'
            except Exception as e:
                return False, f'停止SV服务失败: {str(e)}'
    
    def get_sv_status(self) -> Dict[str, Any]:
        """获取SV服务状态"""
        with self.lock:
            # 尝试从sv_sender获取实际发送的报文数量
            packet_count = self.sv_packet_count
            if self.sv_sender and hasattr(self.sv_sender, 'get_packet_count'):
                try:
                    packet_count = self.sv_sender.get_packet_count()
                except:
                    pass
            
            return {
                'running': self.sv_running,
                'config': self.sv_config,
                'start_time': self.sv_config.get('start_time') if self.sv_config else None,
                'packet_count': packet_count
            }
    
    def start_ethercat(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict]]:
        """启动 EtherCAT 发送服务"""
        with self.lock:
            if self.ethercat_running:
                return False, 'EtherCAT 服务已在运行', None
            if self.ethercat_sender_class is None:
                return False, 'EthercatSenderService 未导入，请检查 ethercat_sender 模块', None
            try:
                interface = config.get('interface', '以太网')
                data_unit_type = int(config.get('data_unit_type', 1))
                command_codes = config.get('command_codes', [0x00])
                if not command_codes:
                    return False, '请至少选择一种命令码', None
                if data_unit_type not in (1,):
                    return False, '当前仅支持数据单元类型 1 (Ethercat DLPDU)', None
                try:
                    from network_utils import find_interface_by_name, validate_interface
                    resolved = find_interface_by_name(interface)
                    if resolved:
                        interface = resolved
                    is_valid, msg = validate_interface(interface)
                    if not is_valid:
                        return False, f'EtherCAT 启动失败（网卡验证失败: {msg}）', None
                except ImportError:
                    pass
                self.ethercat_sender = self.ethercat_sender_class(iface=interface)
                if hasattr(self.ethercat_sender, 'reset_packet_count'):
                    self.ethercat_sender.reset_packet_count()
                self.ethercat_sender.set_config({
                    'data_unit_type': data_unit_type,
                    'command_codes': command_codes,
                    'read_len': config.get('read_len', 2),
                })
                if not self.ethercat_sender.start():
                    return False, 'EtherCAT 服务启动失败（请检查网卡配置）', None
                self.ethercat_config = config
                self.ethercat_running = True
                self.ethercat_packet_count = 0
                return True, 'EtherCAT 服务启动成功', {
                    'status': 'running',
                    'start_time': datetime.now().isoformat(),
                    'config': config,
                }
            except Exception as e:
                import traceback
                traceback.print_exc()
                return False, f'启动 EtherCAT 服务失败: {str(e)}', None
    
    def stop_ethercat(self) -> Tuple[bool, str]:
        """停止 EtherCAT 服务"""
        with self.lock:
            if not self.ethercat_running:
                return False, 'EtherCAT 服务未运行'
            try:
                if self.ethercat_sender:
                    if hasattr(self.ethercat_sender, 'stop'):
                        self.ethercat_sender.stop()
                self.ethercat_sender = None
                self.ethercat_config = None
                self.ethercat_running = False
                self.ethercat_packet_count = 0
                return True, 'EtherCAT 服务已停止'
            except Exception as e:
                return False, f'停止 EtherCAT 服务失败: {str(e)}'
    
    def get_ethercat_status(self) -> Dict[str, Any]:
        """获取 EtherCAT 服务状态"""
        with self.lock:
            packet_count = self.ethercat_packet_count
            if self.ethercat_sender and hasattr(self.ethercat_sender, 'get_packet_count'):
                try:
                    packet_count = self.ethercat_sender.get_packet_count()
                except Exception:
                    pass
            return {
                'running': self.ethercat_running,
                'config': self.ethercat_config,
                'start_time': self.ethercat_config.get('start_time') if self.ethercat_config else None,
                'packet_count': packet_count,
            }

    def start_powerlink(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict]]:
        """启动 POWERLINK 发送服务"""
        with self.lock:
            if self.powerlink_running:
                return False, 'POWERLINK 服务已在运行', None
            if self.powerlink_sender_class is None:
                return False, 'PowerlinkSenderService 未导入，请检查 powerlink_sender 模块', None
            try:
                interface = config.get('interface', '以太网')
                service_types = config.get('service_types', ['SoC'])
                sa = config.get('sa', 240)
                da = config.get('da', 17)
                dst_mac = config.get('dst_mac', '01:11:1e:00:00:01')
                src_mac = config.get('src_mac', '00:50:c2:31:3f:dd')
                sa = max(0, min(255, int(sa))) if sa is not None else 240
                da = max(0, min(255, int(da))) if da is not None else 17
                if not service_types:
                    return False, '请至少选择一种服务类型（SoC/Preq/Pres/SoA/ASnd/AMNI）', None
                self.powerlink_sender = self.powerlink_sender_class(iface=interface)
                if hasattr(self.powerlink_sender, 'reset_packet_count'):
                    self.powerlink_sender.reset_packet_count()
                self.powerlink_sender.set_config({
                    'service_types': service_types,
                    'sa': sa,
                    'da': da,
                    'dst_mac': dst_mac,
                    'src_mac': src_mac,
                })
                if not self.powerlink_sender.start():
                    return False, 'POWERLINK 服务启动失败（请检查网卡配置）', None
                self.powerlink_config = {**config, 'start_time': datetime.now().isoformat()}
                self.powerlink_running = True
                self.powerlink_packet_count = 0
                return True, 'POWERLINK 服务启动成功', {
                    'status': 'running',
                    'start_time': self.powerlink_config.get('start_time'),
                    'packet_count': 0,
                }
            except Exception as e:
                return False, f'启动 POWERLINK 服务失败: {str(e)}', None

    def stop_powerlink(self) -> Tuple[bool, str]:
        """停止 POWERLINK 服务"""
        with self.lock:
            if not self.powerlink_running:
                return False, 'POWERLINK 服务未运行'
            try:
                if self.powerlink_sender:
                    if hasattr(self.powerlink_sender, 'stop'):
                        self.powerlink_sender.stop()
                self.powerlink_sender = None
                self.powerlink_config = None
                self.powerlink_running = False
                self.powerlink_packet_count = 0
                return True, 'POWERLINK 服务已停止'
            except Exception as e:
                return False, f'停止 POWERLINK 服务失败: {str(e)}'

    def get_powerlink_status(self) -> Dict[str, Any]:
        """获取 POWERLINK 服务状态"""
        with self.lock:
            packet_count = self.powerlink_packet_count
            if self.powerlink_sender and hasattr(self.powerlink_sender, 'get_packet_count'):
                try:
                    packet_count = self.powerlink_sender.get_packet_count()
                except Exception:
                    pass
            return {
                'running': self.powerlink_running,
                'config': self.powerlink_config,
                'start_time': self.powerlink_config.get('start_time') if self.powerlink_config else None,
                'packet_count': packet_count,
            }

    def start_dcp(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict]]:
        """启动 DCP 发送服务"""
        with self.lock:
            if self.dcp_running:
                return False, 'DCP 服务已在运行', None
            if self.dcp_sender_class is None:
                return False, 'DcpSenderService 未导入，请检查 dcp_sender 模块', None
            try:
                interface = config.get('interface', '以太网')
                self.dcp_sender = self.dcp_sender_class(iface=interface)
                if hasattr(self.dcp_sender, 'reset_packet_count'):
                    self.dcp_sender.reset_packet_count()
                # 显式传递 suboptions 列表副本，确保多选生效
                config_for_sender = dict(config)
                raw_subs = config_for_sender.get('suboptions')
                if isinstance(raw_subs, (list, tuple)) and len(raw_subs) > 0:
                    config_for_sender['suboptions'] = [int(x) & 0xFF for x in raw_subs if x is not None]
                self.dcp_sender.set_config(config_for_sender)
                if not self.dcp_sender.start():
                    return False, 'DCP 服务启动失败（请检查网卡配置）', None
                self.dcp_config = {**config, 'start_time': datetime.now().isoformat()}
                self.dcp_running = True
                self.dcp_packet_count = 0
                return True, 'DCP 服务启动成功', {
                    'status': 'running',
                    'start_time': self.dcp_config.get('start_time'),
                    'packet_count': 0,
                }
            except Exception as e:
                return False, f'启动 DCP 服务失败: {str(e)}', None

    def stop_dcp(self) -> Tuple[bool, str]:
        """停止 DCP 服务"""
        with self.lock:
            if not self.dcp_running:
                return False, 'DCP 服务未运行'
            try:
                if self.dcp_sender:
                    if hasattr(self.dcp_sender, 'stop'):
                        self.dcp_sender.stop()
                self.dcp_sender = None
                self.dcp_config = None
                self.dcp_running = False
                self.dcp_packet_count = 0
                return True, 'DCP 服务已停止'
            except Exception as e:
                return False, f'停止 DCP 服务失败: {str(e)}'

    def get_dcp_status(self) -> Dict[str, Any]:
        """获取 DCP 服务状态"""
        with self.lock:
            packet_count = self.dcp_packet_count
            if self.dcp_sender and hasattr(self.dcp_sender, 'get_packet_count'):
                try:
                    packet_count = self.dcp_sender.get_packet_count()
                except Exception:
                    pass
            return {
                'running': self.dcp_running,
                'config': self.dcp_config,
                'start_time': self.dcp_config.get('start_time') if self.dcp_config else None,
                'packet_count': packet_count,
            }

# 创建全局服务管理器实例
manager = ServiceManager()

# ==================== GOOSE API ====================

@goose_sv_bp.route('/goose/start', methods=['POST', 'OPTIONS'])
def start_goose():
    """启动GOOSE服务"""
    try:
        data = request.json or {}
        
        # 验证必需参数
        required_fields = ['interface', 'dataset']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'error': f'缺少必需参数: {field}'
                }), 400
        
        # 设置默认值（如果未提供）
        if 'appid' not in data:
            data['appid'] = 256
        if 'gocb_ref' not in data:
            data['gocb_ref'] = "IED1/LLN0$GO$GSE1"
        if 'data_content' not in data:
            data['data_content'] = {"Switch_1": True, "Switch_2": False}
        
        # 启动服务
        success, message, result = manager.start_goose(data)
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'data': result
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'启动GOOSE服务异常: {str(e)}'
        }), 500

@goose_sv_bp.route('/goose/stop', methods=['POST', 'OPTIONS'])
def stop_goose():
    """停止GOOSE服务"""
    try:
        success, message = manager.stop_goose()
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'停止GOOSE服务异常: {str(e)}'
        }), 500

@goose_sv_bp.route('/goose/status', methods=['GET', 'POST', 'OPTIONS'])
def goose_status():
    """获取GOOSE服务状态"""
    try:
        status = manager.get_goose_status()
        return jsonify({
            'success': True,
            'data': status
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取GOOSE状态异常: {str(e)}'
        }), 500

# ==================== EtherCAT API ====================

@goose_sv_bp.route('/ethercat/start', methods=['POST', 'OPTIONS'])
def start_ethercat():
    """启动 EtherCAT 发送服务"""
    try:
        data = request.json or {}
        if 'interface' not in data or not data['interface']:
            return jsonify({'success': False, 'error': '缺少必需参数: interface'}), 400
        if 'command_codes' not in data or not data['command_codes']:
            return jsonify({'success': False, 'error': '请至少选择一种命令码'}), 400
        data.setdefault('data_unit_type', 1)
        success, message, result = manager.start_ethercat(data)
        if success:
            return jsonify({'success': True, 'message': message, 'data': result}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'启动 EtherCAT 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/ethercat/stop', methods=['POST', 'OPTIONS'])
def stop_ethercat():
    """停止 EtherCAT 服务"""
    try:
        success, message = manager.stop_ethercat()
        if success:
            return jsonify({'success': True, 'message': message}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'停止 EtherCAT 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/ethercat/status', methods=['GET', 'POST', 'OPTIONS'])
def ethercat_status():
    """获取 EtherCAT 服务状态"""
    try:
        status = manager.get_ethercat_status()
        return jsonify({'success': True, 'data': status}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取 EtherCAT 状态异常: {str(e)}'}), 500

# ==================== POWERLINK API ====================

@goose_sv_bp.route('/powerlink/start', methods=['POST', 'OPTIONS'])
def start_powerlink():
    """启动 POWERLINK 发送服务"""
    try:
        data = request.json or {}
        if 'interface' not in data or not data['interface']:
            return jsonify({'success': False, 'error': '缺少必需参数: interface'}), 400
        service_types = data.get('service_types', ['SoC'])
        if not service_types:
            return jsonify({'success': False, 'error': '请至少选择一种服务类型'}), 400
        data.setdefault('sa', 240)
        data.setdefault('da', 17)
        success, message, result = manager.start_powerlink(data)
        if success:
            return jsonify({'success': True, 'message': message, 'data': result}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'启动 POWERLINK 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/powerlink/stop', methods=['POST', 'OPTIONS'])
def stop_powerlink():
    """停止 POWERLINK 服务"""
    try:
        success, message = manager.stop_powerlink()
        if success:
            return jsonify({'success': True, 'message': message}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'停止 POWERLINK 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/powerlink/status', methods=['GET', 'POST', 'OPTIONS'])
def powerlink_status():
    """获取 POWERLINK 服务状态"""
    try:
        status = manager.get_powerlink_status()
        return jsonify({'success': True, 'data': status}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取 POWERLINK 状态异常: {str(e)}'}), 500

# ==================== DCP API ====================

@goose_sv_bp.route('/dcp/start', methods=['POST', 'OPTIONS'])
def start_dcp():
    """启动 DCP 发送服务"""
    try:
        data = request.json or {}
        if 'interface' not in data or not data['interface']:
            return jsonify({'success': False, 'error': '缺少必需参数: interface'}), 400
        data.setdefault('frame_type', 'GETORSET')
        data.setdefault('service_code', 'GET')
        data.setdefault('service_type', 'request')
        data.setdefault('option', 'IP')
        # 明确规范 suboptions 为整数列表，避免被覆盖或单值
        raw = data.get('suboptions')
        if isinstance(raw, (list, tuple)) and len(raw) > 0:
            data['suboptions'] = [int(x) & 0xFF for x in raw if x is not None]
        elif isinstance(raw, (int, float, str)) and raw is not None:
            data['suboptions'] = [int(raw) & 0xFF]
        else:
            data['suboptions'] = [int(data.get('suboption', 1)) & 0xFF]
        success, message, result = manager.start_dcp(data)
        if success:
            return jsonify({'success': True, 'message': message, 'data': result}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'启动 DCP 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/dcp/stop', methods=['POST', 'OPTIONS'])
def stop_dcp():
    """停止 DCP 服务"""
    try:
        success, message = manager.stop_dcp()
        if success:
            return jsonify({'success': True, 'message': message}), 200
        return jsonify({'success': False, 'error': message}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'停止 DCP 服务异常: {str(e)}'}), 500

@goose_sv_bp.route('/dcp/status', methods=['GET', 'POST', 'OPTIONS'])
def dcp_status():
    """获取 DCP 服务状态"""
    try:
        status = manager.get_dcp_status()
        return jsonify({'success': True, 'data': status}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取 DCP 状态异常: {str(e)}'}), 500

@goose_sv_bp.route('/dcp/suboptions', methods=['GET', 'OPTIONS'])
def dcp_suboptions():
    """根据 Option 返回子选项列表（option 参数为选项名如 IP、DEVICE）"""
    try:
        from dcp_sender import get_suboptions_for_option, OPTION_MAP
        option_name = request.args.get('option', 'IP')
        option_val = OPTION_MAP.get(option_name.upper(), 0x01)
        subopts = get_suboptions_for_option(option_val)
        items = [{'value': v, 'label': label} for v, label in subopts]
        return jsonify({'success': True, 'data': items}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== SV API ====================

@goose_sv_bp.route('/sv/start', methods=['POST', 'OPTIONS'])
def start_sv():
    """启动SV服务"""
    try:
        data = request.json or {}
        
        # 验证必需参数
        required_fields = ['interface', 'svid']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({
                    'success': False,
                    'error': f'缺少必需参数: {field}'
                }), 400
        
        # 设置默认值（如果未提供）
        if 'appid' not in data:
            data['appid'] = 16409
        if 'smp_rate' not in data:
            data['smp_rate'] = 50
        if 'sampled_values' not in data:
            data['sampled_values'] = {
                "Voltage_A": 22010,
                "Voltage_B": 21980,
                "Voltage_C": 22030,
                "Current_A": 1020,
                "Current_B": 1050,
                "Current_C": 1010
            }
        
        # 启动服务
        success, message, result = manager.start_sv(data)
        
        if success:
            return jsonify({
                'success': True,
                'message': message,
                'data': result
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'启动SV服务异常: {str(e)}'
        }), 500

@goose_sv_bp.route('/sv/stop', methods=['POST', 'OPTIONS'])
def stop_sv():
    """停止SV服务"""
    try:
        success, message = manager.stop_sv()
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 400
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'停止SV服务异常: {str(e)}'
        }), 500

@goose_sv_bp.route('/sv/status', methods=['GET', 'POST', 'OPTIONS'])
def sv_status():
    """获取SV服务状态"""
    try:
        status = manager.get_sv_status()
        return jsonify({
            'success': True,
            'data': status
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取SV状态异常: {str(e)}'
        }), 500

# ==================== 通用API ====================

@goose_sv_bp.route('/test', methods=['GET', 'OPTIONS'])
def test_route():
    """测试路由，用于验证Blueprint是否正常工作"""
    print(f"[GOOSE-SV] test_route被调用: method={request.method}, path={request.path}")
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
        return response
    return jsonify({
        'success': True,
        'message': 'GOOSE/SV Blueprint is working',
        'path': request.path
    }), 200

@goose_sv_bp.route('/interfaces', methods=['GET', 'POST', 'OPTIONS'])
def get_interfaces():
    """获取可用网卡列表（使用与packet_agent相同的逻辑）"""
    print(f"[GOOSE-SV] get_interfaces被调用: method={request.method}, path={request.path}")
    
    # 处理OPTIONS预检请求（双重保险）
    if request.method == 'OPTIONS':
        print(f"[GOOSE-SV] 处理OPTIONS预检请求")
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    try:
        import psutil
        import socket
        
        PSUTIL_AVAILABLE = True
    except ImportError:
        return jsonify({
            'success': False,
            'error': 'psutil未安装，无法获取网卡列表'
        }), 500
    
    interfaces = []
    seen_macs = set()  # 用于去重，避免重复的MAC地址
    
    try:
        # 获取所有网卡的统计信息
        if_stats = psutil.net_if_stats()
        # 获取所有网卡的地址信息
        if_addrs = psutil.net_if_addrs()
        
        # 遍历所有网卡
        for ifname, stats in if_stats.items():
            try:
                # 跳过回环网卡
                if ifname == 'Loopback Pseudo-Interface 1' or 'Loopback' in ifname:
                    continue
                
                # 获取该网卡的地址信息
                addrs = if_addrs.get(ifname, [])
                ip = None
                mac = None
                
                for addr in addrs:
                    # 提取 IPv4 地址
                    if addr.family == socket.AF_INET:
                        # 跳过回环地址和0.0.0.0
                        if addr.address not in ('127.0.0.1', '0.0.0.0'):
                            ip = addr.address
                    # 提取 MAC 地址
                    elif addr.family == psutil.AF_LINK:
                        mac = addr.address
                
                # 如果没有MAC地址，跳过（虚拟网卡可能没有MAC）
                if not mac:
                    continue
                
                # 如果IP为0.0.0.0或None，不显示该网卡
                if not ip or ip == '0.0.0.0':
                    continue
                
                # 标准化MAC地址格式（用于去重）
                mac_normalized = mac.replace('-', ':').upper()
                
                # 去重：如果MAC地址已存在，跳过
                if mac_normalized in seen_macs:
                    continue
                seen_macs.add(mac_normalized)
                
                # 获取网卡状态
                status = '已启用' if stats.isup else '已禁用'
                
                # 尝试查找Scapy接口名称
                scapy_name = ifname
                try:
                    from scapy.all import get_if_list, get_if_hwaddr
                    scapy_if_list = get_if_list()
                    # 通过MAC地址匹配
                    for scapy_if in scapy_if_list:
                        try:
                            scapy_mac = get_if_hwaddr(scapy_if)
                            if scapy_mac:
                                scapy_mac_normalized = scapy_mac.replace('-', ':').upper()
                                if scapy_mac_normalized == mac_normalized:
                                    scapy_name = scapy_if
                                    break
                        except:
                            continue
                except:
                    pass
                
                # 添加接口信息
                interfaces.append({
                    'name': scapy_name,  # Scapy接口名称（用于发送报文）
                    'display_name': ifname,  # 友好显示名称（如"以太网"、"以太网 2"）
                    'ip': ip,  # IP地址
                    'mac': mac,  # MAC地址
                    'status': status,  # 状态（已启用/已禁用）
                    'mtu': stats.mtu,  # MTU
                    'speed': stats.speed if stats.speed > 0 else None  # 网卡速率（Mbps）
                })
            except Exception as e:
                continue
        
        return jsonify({
            'success': True,
            'data': interfaces
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': f'获取网卡列表异常: {str(e)}'
        }), 500

