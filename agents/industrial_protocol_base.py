#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工控协议代理程序
运行在远程主机上，通过HTTP API接收Modbus等工控协议配置并执行
"""

import json
import threading
import time
import socket
import asyncio
import logging
import os
import sqlite3
import ctypes
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError:
    print("请安装 flask 和 flask-cors: pip install flask flask-cors")
    exit(1)

# 尝试导入pymodbus，使用更健壮的导入方式，兼容pymodbus 2.x和3.x版本（包括3.7.0）
PYMODBUS_AVAILABLE = False
PYMODBUS_VERSION = "0.0.0"  # 存储版本号
ModbusTcpClient = None
StartTcpServer = None  # 保留用于向后兼容
ModbusTcpServer = None  # 新增：用于优雅启停的服务器类
# 使用ModbusDeviceContext替代废弃的ModbusSlaveContext（3.x版本）
ModbusDeviceContext = None
ModbusServerContext = None
ModbusSequentialDataBlock = None
ModbusException = None

try:
    # 先检查pymodbus模块是否存在
    import pymodbus
    PYMODBUS_VERSION = getattr(pymodbus, '__version__', '0.0.0')
    print(f"检测到pymodbus模块，版本: {PYMODBUS_VERSION}")
    PYMODBUS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    print("警告: pymodbus模块未安装，Modbus功能将不可用。建议安装: pip install pymodbus")
    PYMODBUS_AVAILABLE = False
except Exception as e:
    print(f"警告: pymodbus模块导入异常 ({type(e).__name__}: {e})，Modbus功能将不可用")
    PYMODBUS_AVAILABLE = False

# 解析版本号，判断是2.x还是3.x
def get_pymodbus_version_major() -> int:
    """获取pymodbus主版本号"""
    try:
        return int(PYMODBUS_VERSION.split('.')[0])
    except (IndexError, ValueError):
        return 2  # 默认按2.x处理

PYMODBUS_MAJOR_VERSION = get_pymodbus_version_major()
print(f"[DEBUG] pymodbus版本检测: {PYMODBUS_VERSION} -> 主版本 {PYMODBUS_MAJOR_VERSION}")

# 如果pymodbus模块存在，尝试导入需要的类
if PYMODBUS_AVAILABLE:
    # 客户端导入
    try:
        from pymodbus.client import ModbusTcpClient
        print("[OK] ModbusTcpClient导入成功")
    except (ImportError, ModuleNotFoundError) as e:
        print(f"警告: ModbusTcpClient导入失败: {e}")
        PYMODBUS_AVAILABLE = False
    
    # 服务器导入 - pymodbus 3.x使用异步服务器，2.x使用同步服务器
    AsyncModbusTcpServer = None
    SyncModbusTcpServer = None
    ModbusTcpFramer = None
    if PYMODBUS_AVAILABLE:
        # 补充导入帧解析器（pymodbus 3.6.9+ 可能不需要显式导入，或路径不同）
        ModbusTcpFramer = None
        try:
            from pymodbus.transaction import ModbusTcpFramer
            print("[OK] ModbusTcpFramer导入成功 (from pymodbus.transaction)")
        except ImportError:
            try:
                # 尝试其他可能的导入路径
                from pymodbus.framer import ModbusTcpFramer
                print("[OK] ModbusTcpFramer导入成功 (from pymodbus.framer)")
            except ImportError:
                try:
                    from pymodbus.framer.socket_framer import ModbusSocketFramer as ModbusTcpFramer
                    print("[OK] ModbusTcpFramer导入成功 (from pymodbus.framer.socket_framer)")
                except ImportError as e:
                    print(f"警告: ModbusTcpFramer所有路径导入失败: {e}")
                    print("提示: pymodbus 3.6.9+ 可能不需要显式传递 framer，将使用默认值")
                    ModbusTcpFramer = None
        
        # 3.x 异步服务器导入
        if PYMODBUS_MAJOR_VERSION >= 3:
            try:
                from pymodbus.server import AsyncModbusTcpServer
                print("[OK] AsyncModbusTcpServer导入成功 (pymodbus 3.x)")
            except ImportError:
                try:
                    # 尝试从备用路径导入 (某些 3.x 版本)
                    from pymodbus.server.async_io import ModbusTcpServer as AsyncModbusTcpServer
                    print("[OK] AsyncModbusTcpServer (from async_io) 导入成功")
                except ImportError as e:
                    print(f"警告: AsyncModbusTcpServer所有路径导入失败: {e}")
                    # 尝试打印可用类方便调试
                    try:
                        import pymodbus.server as pms
                        print(f"pymodbus.server 可用属性: {[x for x in dir(pms) if not x.startswith('_')]}")
                    except:
                        pass
        else:
            # 2.x 同步服务器导入
            try:
                from pymodbus.server.sync import ModbusTcpServer as SyncModbusTcpServer
                print("[OK] SyncModbusTcpServer导入成功 (pymodbus 2.x)")
            except ImportError as e:
                print(f"警告: SyncModbusTcpServer导入失败: {e}")
    
    # 数据存储导入 - 适配3.11.4的结构（核心修复：使用ModbusDeviceContext替代ModbusSlaveContext）
    if PYMODBUS_AVAILABLE:
        try:
            # 3.x 最新版本（3.11.4）的导入路径
            from pymodbus.datastore import (
                ModbusServerContext,
                ModbusSequentialDataBlock,
                ModbusDeviceContext  # 替代 ModbusSlaveContext
            )
            print("[OK] ModbusDeviceContext, ModbusServerContext, ModbusSequentialDataBlock导入成功 (pymodbus 3.x 最新版)")
        except ImportError:
            try:
                # 3.x 早期版本的备用路径
                from pymodbus.datastore.context import (
                    ModbusServerContext,
                    ModbusDeviceContext
                )
                from pymodbus.datastore.store import ModbusSequentialDataBlock
                print("[OK] ModbusDeviceContext, ModbusServerContext, ModbusSequentialDataBlock导入成功 (3.x 早期路径)")
            except ImportError:
                try:
                    # 2.x 版本的导入路径（保留 ModbusSlaveContext 兼容）
                    from pymodbus.datastore.context import (
                        ModbusSlaveContext,
                        ModbusServerContext
                    )
                    from pymodbus.datastore.store import ModbusSequentialDataBlock
                    # 2.x 中用 ModbusSlaveContext 赋值给 ModbusDeviceContext，保持逻辑统一
                    ModbusDeviceContext = ModbusSlaveContext
                    print("[OK] ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock导入成功 (pymodbus 2.x)")
                except ImportError as e:
                    print(f"警告: 数据存储类导入失败: {e}")
                    try:
                        import pymodbus.datastore as ds
                        print(f"pymodbus.datastore可用属性: {[x for x in dir(ds) if not x.startswith('_')]}")
                    except Exception as ex:
                        print(f"检查模块结构时出错: {ex}")
                    PYMODBUS_AVAILABLE = False
    
    # 异常类导入
    if PYMODBUS_AVAILABLE:
        try:
            from pymodbus.exceptions import ModbusException
            print("[OK] ModbusException导入成功")
        except (ImportError, ModuleNotFoundError) as e:
            print(f"警告: ModbusException导入失败: {e}")
            PYMODBUS_AVAILABLE = False
    
    if PYMODBUS_AVAILABLE:
        print("pymodbus所有类导入成功")
    else:
        print("警告: pymodbus部分类导入失败，Modbus功能将不可用")

app = Flask(__name__)
# 配置CORS，允许所有来源、所有方法和所有头部
# 包括Blueprint路由
CORS(app, 
     resources={
         r"/api/*": {
             "origins": "*",
             "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
             "allow_headers": ["Content-Type", "Authorization", "X-Requested-With", "Accept"],
             "supports_credentials": True
         }
     }
)

# 在app级别添加CORS处理（在注册Blueprint之前）
@app.before_request
def handle_cors_preflight():
    """处理所有OPTIONS预检请求"""
    if request.method == 'OPTIONS':
        from flask import make_response
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response

@app.after_request
def add_cors_headers(response):
    """为所有响应添加CORS头"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    return response

# 注册GOOSE/SV Blueprint和路由
# 先添加备用路由（确保即使Blueprint导入失败也能工作）
from flask import make_response

@app.route('/api/industrial_protocol/goose-sv/interfaces', methods=['GET', 'POST', 'OPTIONS'])
def goose_sv_interfaces_fallback():
    """GOOSE/SV接口列表（备用路由）"""
    print(f"[GOOSE-SV] interfaces路由被调用: method={request.method}, path={request.path}")
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    
    # 直接实现接口列表获取逻辑
    try:
        import psutil
        import socket
    except ImportError:
        return jsonify({
            'success': False,
            'error': 'psutil未安装，无法获取网卡列表'
        }), 500
    
    interfaces = []
    seen_macs = set()  # 用于去重，避免重复的MAC地址
    
    try:
        print("[GOOSE-SV] 使用psutil获取网卡信息...")
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
                
                # 查找对应的Scapy接口名称（用于发送报文）
                scapy_name = None
                try:
                    # 尝试通过MAC地址匹配Scapy接口
                    from scapy.all import get_if_list, get_if_hwaddr
                    scapy_if_list = get_if_list()
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
                
                # 如果找不到Scapy接口，尝试使用网卡名称（Linux）或NPF设备（Windows）
                if not scapy_name:
                    # Windows系统：尝试查找NPF设备
                    try:
                        from scapy.all import get_if_list, get_if_hwaddr
                        scapy_if_list = get_if_list()
                        for scapy_if in scapy_if_list:
                            if scapy_if.startswith('\\Device\\NPF_'):
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
                
                # 如果还是找不到，使用网卡名称作为Scapy接口名（Linux系统通常可以）
                if not scapy_name:
                    scapy_name = ifname
                
                # 添加接口信息
                interfaces.append({
                    'name': scapy_name,  # Scapy使用的接口名称（用于发送报文）
                    'display_name': ifname,  # 友好显示名称（如"以太网"、"以太网 2"）
                    'ip': ip,  # IP地址
                    'mac': mac,  # MAC地址
                    'status': status,  # 状态（已启用/已禁用）
                    'mtu': stats.mtu,  # MTU
                    'speed': stats.speed if stats.speed > 0 else None  # 网卡速率（Mbps）
                })
                print(f"[GOOSE-SV] 添加接口: {ifname} ({scapy_name}) - IP: {ip}, MAC: {mac}, 状态: {status}")
            except Exception as e:
                print(f"[GOOSE-SV] 处理网卡 {ifname} 时出错: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        print(f"[GOOSE-SV] 使用psutil获取到 {len(interfaces)} 个网卡")
        response = jsonify({
            'success': True,
            'data': interfaces
        })
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({
            'success': False,
            'error': f'获取网卡列表异常: {str(e)}'
        })
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

# 注册其他GOOSE/SV路由（备用方案，确保即使Blueprint失败也能工作）
# 确保能导入goose_sv_api模块
import sys
import os
_goose_sv_api_imported = False
_goose_sv_manager = None

def _ensure_goose_sv_api():
    """确保goose_sv_api模块可以被导入"""
    global _goose_sv_api_imported, _goose_sv_manager
    if _goose_sv_api_imported:
        return _goose_sv_manager
    
    # 获取当前脚本所在目录（兼容exe环境）
    try:
        if hasattr(sys, 'frozen') and sys.frozen:
            # 如果是exe环境
            script_dir = os.path.dirname(sys.executable)
            print(f"[GOOSE-SV] 检测到exe环境，使用executable目录: {script_dir}")
        else:
            # 普通Python环境
            script_dir = os.path.dirname(os.path.abspath(__file__))
            print(f"[GOOSE-SV] 普通Python环境，脚本目录: {script_dir}")
    except:
        # 如果__file__不存在，尝试使用当前工作目录
        script_dir = os.getcwd()
        print(f"[GOOSE-SV] 使用当前工作目录: {script_dir}")
    
    print(f"[GOOSE-SV] 当前脚本目录: {script_dir}")
    print(f"[GOOSE-SV] sys.path前5个: {sys.path[:5]}")
    
    # 确保当前目录在sys.path中
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
        print(f"[GOOSE-SV] 已添加路径到sys.path: {script_dir}")
    
    # 检查goose_sv_api.py文件是否存在
    goose_sv_api_path = os.path.join(script_dir, 'goose_sv_api.py')
    print(f"[GOOSE-SV] 检查文件: {goose_sv_api_path}")
    if os.path.exists(goose_sv_api_path):
        print(f"[GOOSE-SV] [OK] 文件存在: {goose_sv_api_path}")
    else:
        print(f"[GOOSE-SV] [FAIL] 文件不存在: {goose_sv_api_path}")
        # 尝试查找其他可能的位置
        possible_paths = [
            os.path.join(script_dir, 'goose_sv_api.py'),
            os.path.join(os.getcwd(), 'goose_sv_api.py'),
            'C:\\packet_agent\\goose_sv_api.py',  # Agent运行时路径
        ]
        for path in possible_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                print(f"[GOOSE-SV] [OK] 找到文件: {abs_path}")
                parent_dir = os.path.dirname(abs_path)
                if parent_dir not in sys.path:
                    sys.path.insert(0, parent_dir)
                    print(f"[GOOSE-SV] 已添加父目录到sys.path: {parent_dir}")
                break
        else:
            print(f"[GOOSE-SV] [FAIL] 在所有可能位置都未找到goose_sv_api.py")
    
    try:
        # 尝试导入
        print("[GOOSE-SV] 开始导入goose_sv_api模块...")
        import goose_sv_api
        print(f"[GOOSE-SV] [OK] 模块导入成功: {goose_sv_api}")
        print(f"[GOOSE-SV] 模块文件路径: {getattr(goose_sv_api, '__file__', 'unknown')}")
        
        # 检查manager是否存在
        if not hasattr(goose_sv_api, 'manager'):
            raise AttributeError("goose_sv_api模块中没有manager属性")
        
        manager = goose_sv_api.manager
        _goose_sv_manager = manager
        _goose_sv_api_imported = True
        print("[GOOSE-SV] [OK] 成功获取manager实例")
        return manager
    except ImportError as e:
        error_msg = str(e)
        print(f"[GOOSE-SV] [FAIL] 导入失败 (ImportError): {error_msg}")
        print(f"[GOOSE-SV] 错误类型: {type(e).__name__}")
        import traceback
        print("[GOOSE-SV] 完整错误堆栈:")
        traceback.print_exc()
        raise
    except AttributeError as e:
        error_msg = str(e)
        print(f"[GOOSE-SV] [FAIL] 属性错误 (AttributeError): {error_msg}")
        import traceback
        traceback.print_exc()
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"[GOOSE-SV] [FAIL] 其他异常: {error_msg}")
        print(f"[GOOSE-SV] 异常类型: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        raise

@app.route('/api/industrial_protocol/goose-sv/goose/start', methods=['POST', 'OPTIONS'])
def goose_start_fallback():
    """启动GOOSE服务（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        data = request.json or {}
        success, message, result = manager.start_goose(data)
        response = jsonify({'success': True, 'message': message, 'data': result}) if success else jsonify({'success': False, 'error': message})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200 if success else 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'启动GOOSE服务异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

@app.route('/api/industrial_protocol/goose-sv/goose/stop', methods=['POST', 'OPTIONS'])
def goose_stop_fallback():
    """停止GOOSE服务（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        success, message = manager.stop_goose()
        response = jsonify({'success': True, 'message': message}) if success else jsonify({'success': False, 'error': message})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200 if success else 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'停止GOOSE服务异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

@app.route('/api/industrial_protocol/goose-sv/goose/status', methods=['GET', 'POST', 'OPTIONS'])
def goose_status_fallback():
    """获取GOOSE服务状态（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        status = manager.get_goose_status()
        response = jsonify({'success': True, 'data': status})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'获取GOOSE状态异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

@app.route('/api/industrial_protocol/goose-sv/sv/start', methods=['POST', 'OPTIONS'])
def sv_start_fallback():
    """启动SV服务（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        data = request.json or {}
        success, message, result = manager.start_sv(data)
        response = jsonify({'success': True, 'message': message, 'data': result}) if success else jsonify({'success': False, 'error': message})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200 if success else 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'启动SV服务异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

@app.route('/api/industrial_protocol/goose-sv/sv/stop', methods=['POST', 'OPTIONS'])
def sv_stop_fallback():
    """停止SV服务（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        success, message = manager.stop_sv()
        response = jsonify({'success': True, 'message': message}) if success else jsonify({'success': False, 'error': message})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200 if success else 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'停止SV服务异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

@app.route('/api/industrial_protocol/goose-sv/sv/status', methods=['GET', 'POST', 'OPTIONS'])
def sv_status_fallback():
    """获取SV服务状态（备用路由）"""
    if request.method == 'OPTIONS':
        response = make_response('', 200)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Requested-With, Accept'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
        return response
    try:
        manager = _ensure_goose_sv_api()
        status = manager.get_sv_status()
        response = jsonify({'success': True, 'data': status})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        response = jsonify({'success': False, 'error': f'获取SV状态异常: {str(e)}'})
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response, 500

print("[OK] GOOSE/SV备用路由注册成功")

# 注册GOOSE/SV Blueprint（用于其他路由）
try:
    print("[DEBUG] 开始导入goose_sv_api模块...")
    from goose_sv_api import goose_sv_bp
    print(f"[DEBUG] goose_sv_bp导入成功: {goose_sv_bp}")
    print(f"[DEBUG] Blueprint名称: {goose_sv_bp.name}")
    
    app.register_blueprint(goose_sv_bp, url_prefix='/api/industrial_protocol/goose-sv')
    print("[OK] GOOSE/SV Blueprint注册成功")
    
    # 打印所有注册的路由（用于调试）
    print("[DEBUG] 已注册的GOOSE/SV路由:")
    goose_sv_routes_found = False
    for rule in app.url_map.iter_rules():
        rule_str = rule.rule.lower()
        if 'goose' in rule_str or 'sv' in rule_str:
            print(f"  {rule.rule} -> {rule.endpoint} [{', '.join(rule.methods)}]")
            goose_sv_routes_found = True
    
    if not goose_sv_routes_found:
        print("[WARNING] 未找到GOOSE/SV相关路由，可能注册失败")
except ImportError as e:
    print(f"[WARNING] GOOSE/SV Blueprint注册失败: {e}")
    print("提示: 如果使用GOOSE/SV功能，请确保goose_sv_api模块可用")
    import traceback
    traceback.print_exc()
except Exception as e:
    print(f"[WARNING] GOOSE/SV Blueprint注册异常: {e}")
    import traceback
    traceback.print_exc()

# 尝试导入python-snap7，用于S7协议支持
# 兼容 python-snap7 1.3 和 2.0.2 版本
SNAP7_AVAILABLE = False
snap7 = None
snap7_server_module = None
snap7_area = None  # Area枚举（客户端用，值为0x84）
snap7_srv_area = None  # SrvArea枚举（服务端用，值为5）
snap7_util = None
Snap7Server = None  # Server类
Snap7Client = None  # Client类
snap7_version = None
snap7_type = None  # snap7.type模块（2.x版本）

try:
    # 先尝试导入基础模块
    import snap7

    # 检测版本
    try:
        snap7_version = getattr(snap7, '__version__', 'unknown')
        print(f"[DEBUG] snap7基础模块导入成功，版本: {snap7_version}")
    except Exception:
        print(f"[DEBUG] snap7基础模块导入成功（版本未知）")

    # ========== Server导入（兼容1.3和2.0.2）==========
    # 2.0.2: snap7.Server 或 snap7.server.Server
    # 1.3: snap7.server.Server
    server_imported = False

    # 方式1: snap7.Server (2.0.2新增)
    if hasattr(snap7, 'Server'):
        Snap7Server = snap7.Server
        snap7_server_module = snap7
        print("[OK] 使用snap7.Server（2.x方式）")
        server_imported = True

    # 方式2: snap7.server.Server (1.3和2.x都支持)
    if not server_imported:
        try:
            from snap7.server import Server as Snap7Server
            snap7_server_module = snap7.server
            print("[OK] 使用snap7.server.Server（1.3兼容方式）")
            server_imported = True
        except (ImportError, AttributeError):
            pass

    # 方式3: snap7的server模块
    if not server_imported:
        try:
            from snap7 import server as snap7_server_module
            Snap7Server = snap7_server_module.Server
            print("[OK] 使用snap7.server模块（备用方式）")
            server_imported = True
        except (ImportError, AttributeError):
            pass

    if not server_imported:
        raise ImportError("无法导入snap7 Server类")

    # ========== Client导入（兼容1.3和2.0.2）==========
    # 2.0.2: snap7.Client 或 snap7.client.Client
    # 1.3: snap7.client.Client
    client_imported = False

    # 方式1: snap7.Client (2.0.2新增)
    if hasattr(snap7, 'Client'):
        Snap7Client = snap7.Client
        print("[OK] 使用snap7.Client（2.x方式）")
        client_imported = True

    # 方式2: snap7.client.Client (1.3和2.x都支持)
    if not client_imported:
        try:
            from snap7.client import Client as Snap7Client
            print("[OK] 使用snap7.client.Client（1.3兼容方式）")
            client_imported = True
        except (ImportError, AttributeError):
            pass

    # 方式3: snap7的client模块
    if not client_imported:
        try:
            from snap7 import client as snap7_client_module
            Snap7Client = snap7_client_module.Client
            print("[OK] 使用snap7.client模块（备用方式）")
            client_imported = True
        except (ImportError, AttributeError):
            pass

    if not client_imported:
        print("[WARNING] 无法导入snap7 Client类，客户端功能可能不可用")

    # ========== Area枚举导入（兼容1.3和2.0.2）==========
    # 注意：客户端和服务端使用不同的区域枚举！
    # 客户端 Area.DB = 0x84（协议值）
    # 服务端 SrvArea.DB = 5（枚举值）
    area_imported = False
    srv_area_imported = False

    # 方式1: snap7.type.SrvArea (2.0.2) - 服务端用
    try:
        from snap7 import type as snap7_type
        if hasattr(snap7_type, 'SrvArea'):
            snap7_srv_area = snap7_type.SrvArea
            print("[OK] 使用snap7.type.SrvArea（服务端区域，2.x方式）")
            srv_area_imported = True
        if hasattr(snap7_type, 'Area'):
            snap7_area = snap7_type.Area
            print("[OK] 使用snap7.type.Area（客户端区域，2.x方式）")
            area_imported = True
    except ImportError:
        pass

    # 方式2: snap7.SrvArea / snap7.Area (部分版本)
    if not srv_area_imported and hasattr(snap7, 'SrvArea'):
        snap7_srv_area = snap7.SrvArea
        print("[OK] 使用snap7.SrvArea（服务端区域）")
        srv_area_imported = True
    if not area_imported and hasattr(snap7, 'Area'):
        snap7_area = snap7.Area
        print("[OK] 使用snap7.Area（客户端区域）")
        area_imported = True

    # 方式3: snap7.server 模块中的常量（1.3服务端）
    if not srv_area_imported:
        try:
            from snap7.server import srvArea as snap7_srv_area
            print("[OK] 使用snap7.server.srvArea（1.3服务端区域）")
            srv_area_imported = True
        except (ImportError, AttributeError):
            pass

    # 方式4: 硬编码常量（最后手段）
    if not srv_area_imported:
        # 服务端区域枚举值（注意：不是0x84！）
        # 参考 snap7 源码: SrvArea.DB = 5
        class SrvAreaFallback:
            DB = 5      # 数据块
            MK = 6      # 标志位
            PE = 7      # 输入区
            PA = 8      # 输出区
            CT = 9      # 计数器
            TM = 10     # 定时器
        snap7_srv_area = SrvAreaFallback
        print("[WARNING] 未找到SrvArea枚举，使用硬编码值（服务端DB=5）")
        srv_area_imported = True

    if not area_imported:
        # 客户端区域枚举值（协议值）
        class AreaFallback:
            DB = 0x84   # 数据块
            MK = 0x83   # 标志位
            PE = 0x81   # 输入区
            PA = 0x82   # 输出区
            CT = 0x1C   # 计数器
            TM = 0x1D   # 定时器
        snap7_area = AreaFallback
        print("[WARNING] 未找到Area枚举，使用硬编码值（客户端DB=0x84）")
        area_imported = True

    # ========== util模块导入（可选）==========
    try:
        from snap7 import util as snap7_util
        print("[OK] snap7.util导入成功")
    except ImportError:
        try:
            import snap7.util as snap7_util
            print("[OK] snap7.util导入成功（备用路径）")
        except ImportError:
            print("[WARNING] snap7.util导入失败，某些工具函数可能不可用")
            snap7_util = None

    SNAP7_AVAILABLE = True
    print(f"[OK] python-snap7初始化成功（版本: {snap7_version}），S7功能可用")

except (ImportError, ModuleNotFoundError) as e:
    print(f"[WARNING] python-snap7模块未安装或导入失败，S7功能将不可用")
    print(f"[WARNING] 详细错误: {e}")
    print(f"[WARNING] 建议: 1) 安装python-snap7: pip install python-snap7")
    print(f"[WARNING]       2) 确保底层snap7库已正确安装")
    print(f"[WARNING]       3) 支持版本: 1.3.x 或 2.0.2+")
    SNAP7_AVAILABLE = False
    snap7 = None
    snap7_server_module = None
    snap7_area = None
    snap7_srv_area = None
    snap7_util = None
    Snap7Server = None
    Snap7Client = None
    snap7_type = None
except Exception as e:
    print(f"[WARNING] python-snap7模块导入异常 ({type(e).__name__}: {e})，S7功能将不可用")
    import traceback
    print(f"[WARNING] 详细堆栈: {traceback.format_exc()}")
    SNAP7_AVAILABLE = False
    snap7 = None
    snap7_server_module = None
    snap7_area = None
    snap7_srv_area = None
    snap7_util = None
    Snap7Server = None
    Snap7Client = None
    snap7_type = None

# ========== ENIP协议支持（纯socket实现，无外部依赖）==========
ENIP_AVAILABLE = True
try:
    from enip_handler import EnipClient, EnipServer, build_enip_header
    print("[OK] ENIP handler imported successfully")
except ImportError as e:
    print(f"[WARNING] ENIP handler import failed: {e}")
    ENIP_AVAILABLE = False

# ========== DNP3协议支持（Windows-only，ctypes + subprocess隔离）==========
DNP3_AVAILABLE = False
DNP3_PLATFORM_OK = sys.platform == 'win32'
if DNP3_PLATFORM_OK:
    try:
        from dnp3_handler import Dnp3Client, Dnp3SubprocessHandler, DNP3_AVAILABLE as DNP3_LIB_AVAILABLE, get_function_codes_list
        DNP3_AVAILABLE = DNP3_LIB_AVAILABLE
        print(f"[OK] DNP3 handler imported (Windows, availability={DNP3_AVAILABLE})")
    except ImportError as e:
        print(f"[WARNING] DNP3 handler import failed: {e}")
else:
    print("[INFO] DNP3 not available - Windows-only protocol")

# ========== BACnet协议支持（bacpypes3异步库，独立线程）==========
BACNET_AVAILABLE = False
try:
    from bacnet_handler import BacnetHandler, BACNET_AVAILABLE as BACNET_LIB_AVAILABLE
    BACNET_AVAILABLE = BACNET_LIB_AVAILABLE
    bacnet_handler = BacnetHandler()
    print(f"[OK] BACnet handler imported (availability={BACNET_AVAILABLE})")
except ImportError as e:
    print(f"[WARNING] BACnet handler import failed: {e}")
    bacnet_handler = None

# ========== MMS/IEC 61850协议支持（pyiec61850编译绑定）==========
MMS_AVAILABLE = False
try:
    from mms_handler import MmsHandler, MMS_AVAILABLE as MMS_LIB_AVAILABLE
    MMS_AVAILABLE = MMS_LIB_AVAILABLE
    mms_handler = MmsHandler()
    print(f"[OK] MMS handler imported (availability={MMS_AVAILABLE})")
except ImportError as e:
    print(f"[WARNING] MMS handler import failed: {e}")
    mms_handler = None

# 全局变量
modbus_clients = {}  # 存储Modbus客户端连接
modbus_servers = {}  # 存储Modbus服务端实例
modbus_client_lock = threading.Lock()
modbus_server_lock = threading.Lock()

# S7服务端相关
s7_servers = {}  # 存储S7服务端实例
s7_server_lock = threading.Lock()
s7_data_storage = {}  # 存储S7服务端数据（DB块、M区等）

# S7 DB块大小限制
# 注意：snap7服务端支持的最大DB块大小是32768字节（32KB），不是64KB！
# 测试证明：65536字节会报 "Address out of range" 错误
S7_DB_MAX_SIZE = 32768  # 32KB，snap7服务端最大支持

# S7客户端相关
s7_clients = {}  # 存储S7客户端连接
s7_client_lock = threading.Lock()

# ENIP客户端和服务端
enip_clients = {}  # 存储ENIP客户端连接
enip_servers = {}  # 存储ENIP服务端实例
enip_client_lock = threading.Lock()
enip_server_lock = threading.Lock()

# DNP3客户端和服务端（子进程）
dnp3_clients = {}  # 存储DNP3客户端连接
dnp3_servers = {}  # 存储DNP3子进程信息
dnp3_client_lock = threading.Lock()
dnp3_server_lock = threading.Lock()
dnp3_handler = None
if DNP3_AVAILABLE:
    try:
        dnp3_handler = Dnp3SubprocessHandler()
    except Exception as e:
        print(f"[WARNING] Failed to create Dnp3SubprocessHandler: {e}")

# BACnet服务端配置存储
bacnet_server_config = {}  # 存储服务器配置
bacnet_server_lock = threading.Lock()

# MMS/IEC 61850服务端配置存储
mms_servers = {}  # 存储MMS服务端实例
mms_server_lock = threading.Lock()

# 日志存储
protocol_logs = []
protocol_logs_lock = threading.Lock()
MAX_LOGS = 1000

# S7数据库持久化
S7_DB_PATH = Path(__file__).parent / 's7_data.db'
s7_db_lock = threading.Lock()

def init_s7_database():
    """初始化S7数据库"""
    try:
        with sqlite3.connect(str(S7_DB_PATH)) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS s7_db_data (
                    server_id TEXT NOT NULL,
                    db_number INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (server_id, db_number)
                )
            ''')
            conn.commit()
            # 注意：此时add_log可能还未定义，使用print作为备选
            try:
                add_log('INFO', f'S7数据库初始化成功: {S7_DB_PATH}')
            except:
                print(f'[INFO] S7数据库初始化成功: {S7_DB_PATH}')
    except Exception as e:
        # 注意：此时add_log可能还未定义，使用print作为备选
        try:
            add_log('ERROR', f'S7数据库初始化失败: {e}')
        except:
            print(f'[ERROR] S7数据库初始化失败: {e}')

def load_s7_db_from_database(server_id, db_number):
    """从数据库加载S7 DB数据"""
    try:
        with s7_db_lock:
            with sqlite3.connect(str(S7_DB_PATH)) as conn:
                cursor = conn.execute(
                    'SELECT data FROM s7_db_data WHERE server_id = ? AND db_number = ?',
                    (server_id, db_number)
                )
                row = cursor.fetchone()
                if row:
                    data = bytearray(row[0])
                    # 截断到最大支持大小（snap7服务端最大32KB）
                    if len(data) > S7_DB_MAX_SIZE:
                        add_log('WARNING', f'DB{db_number}数据{len(data)}字节超过最大限制{S7_DB_MAX_SIZE}，已截断')
                        data = data[:S7_DB_MAX_SIZE]
                    add_log('INFO', f'从数据库加载DB{db_number}数据: {len(data)}字节 (server_id={server_id})')
                    return data
                else:
                    add_log('DEBUG', f'数据库中没有DB{db_number}数据 (server_id={server_id})，将使用默认值')
                    return None
    except Exception as e:
        add_log('ERROR', f'从数据库加载DB{db_number}失败: {e}')
        return None

def save_s7_db_to_database(server_id, db_number, data):
    """保存S7 DB数据到数据库"""
    try:
        if not isinstance(data, (bytearray, bytes)):
            data = bytearray(data)
        if isinstance(data, bytearray):
            data = bytes(data)
        
        with s7_db_lock:
            with sqlite3.connect(str(S7_DB_PATH)) as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO s7_db_data (server_id, db_number, data, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ''', (server_id, db_number, data))
                conn.commit()
                add_log('DEBUG', f'保存DB{db_number}数据到数据库: {len(data)}字节 (server_id={server_id})')
    except Exception as e:
        add_log('ERROR', f'保存DB{db_number}到数据库失败: {e}')

# 初始化数据库（延迟到add_log函数定义后）
# init_s7_database()  # 将在add_log函数定义后调用

# Modbus服务器专用日志文件
MODBUS_SERVER_LOG_FILE = 'modbus_server.log'
modbus_server_logger = None
modbus_server_logger_lock = threading.Lock()

def setup_modbus_server_logger():
    """设置Modbus服务器专用日志记录器"""
    global modbus_server_logger
    if modbus_server_logger is None:
        with modbus_server_logger_lock:
            if modbus_server_logger is None:
                # 创建日志记录器
                modbus_server_logger = logging.getLogger('modbus_server')
                modbus_server_logger.setLevel(logging.DEBUG)
                
                # 避免重复添加处理器
                if not modbus_server_logger.handlers:
                    # 创建文件处理器
                    file_handler = logging.FileHandler(MODBUS_SERVER_LOG_FILE, encoding='utf-8')
                    file_handler.setLevel(logging.DEBUG)
                    
                    # 创建格式器
                    formatter = logging.Formatter(
                        '%(asctime)s [%(levelname)s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S'
                    )
                    file_handler.setFormatter(formatter)
                    
                    # 添加处理器
                    modbus_server_logger.addHandler(file_handler)
                    
                    # 同时输出到控制台
                    console_handler = logging.StreamHandler()
                    console_handler.setLevel(logging.DEBUG)
                    console_handler.setFormatter(formatter)
                    modbus_server_logger.addHandler(console_handler)
                
                modbus_server_logger.info("=" * 80)
                modbus_server_logger.info("Modbus服务器日志系统初始化完成")
                modbus_server_logger.info("=" * 80)
    
    return modbus_server_logger

# 初始化日志记录器
setup_modbus_server_logger()


def add_log(level: str, message: str):
    """添加日志（同时写入内存和Modbus服务器日志文件）"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        'timestamp': timestamp,
        'level': level,
        'message': message
    }
    with protocol_logs_lock:
        protocol_logs.append(log_entry)
        if len(protocol_logs) > MAX_LOGS:
            protocol_logs.pop(0)
    # 只通过logger输出，避免重复打印
    if modbus_server_logger:
        log_level = getattr(logging, level.upper(), logging.INFO)
        modbus_server_logger.log(log_level, message)
    else:
        # 如果logger未初始化，使用print作为后备
        print(f"[{timestamp}] [{level}] {message}")


def add_modbus_server_log(level: str, message: str, extra_info: dict = None):
    """添加Modbus服务器专用日志（带额外信息）"""
    if extra_info:
        info_str = " | ".join([f"{k}={v}" for k, v in extra_info.items()])
        full_message = f"{message} | {info_str}"
    else:
        full_message = message
    
    add_log(level, full_message)
    
    # 同时写入专用日志文件
    if modbus_server_logger:
        log_level = getattr(logging, level.upper(), logging.INFO)
        modbus_server_logger.log(log_level, full_message)


@app.route('/api/industrial_protocol/modbus_client/connect', methods=['POST'])
def modbus_client_connect():
    """连接Modbus客户端"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        ip = data.get('ip')
        port = data.get('port', 502)
        unit_id = data.get('unit_id', 1)
        timeout = data.get('timeout', 3)
        
        if not ip:
            return jsonify({'success': False, 'error': 'IP地址不能为空'}), 400
        
        with modbus_client_lock:
            # 如果已存在连接，先断开
            if client_id in modbus_clients:
                try:
                    modbus_clients[client_id].close()
                except:
                    pass
                del modbus_clients[client_id]
            
            # 创建新连接
            client = ModbusTcpClient(host=ip, port=port, timeout=timeout)
            result = client.connect()
            
            if result:
                modbus_clients[client_id] = {
                    'client': client,
                    'ip': ip,
                    'port': port,
                    'unit_id': unit_id,
                    'connected': True,
                    'connect_time': datetime.now().isoformat()
                }
                add_log('INFO', f'Modbus客户端连接成功: {ip}:{port} (从站地址: {unit_id})')
                return jsonify({'success': True, 'message': '连接成功'})
            else:
                add_log('ERROR', f'Modbus客户端连接失败: {ip}:{port}')
                return jsonify({'success': False, 'error': '连接失败'}), 500
                
    except Exception as e:
        add_log('ERROR', f'Modbus客户端连接异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_client/disconnect', methods=['POST'])
def modbus_client_disconnect():
    """断开Modbus客户端连接"""
    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        
        with modbus_client_lock:
            if client_id in modbus_clients:
                try:
                    modbus_clients[client_id]['client'].close()
                except:
                    pass
                del modbus_clients[client_id]
                add_log('INFO', f'Modbus客户端断开连接: {client_id}')
                return jsonify({'success': True, 'message': '断开成功'})
            else:
                return jsonify({'success': False, 'error': '连接不存在'}), 404
                
    except Exception as e:
        add_log('ERROR', f'Modbus客户端断开异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_client/status', methods=['GET'])
def modbus_client_status():
    """获取Modbus客户端连接状态"""
    try:
        client_id = request.args.get('client_id', 'default')
        
        with modbus_client_lock:
            if client_id in modbus_clients:
                client_info = modbus_clients[client_id]
                return jsonify({
                    'success': True,
                    'connected': client_info['connected'],
                    'ip': client_info['ip'],
                    'port': client_info['port'],
                    'unit_id': client_info['unit_id'],
                    'connect_time': client_info['connect_time']
                })
            else:
                return jsonify({'success': True, 'connected': False})
                
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_client/read', methods=['POST'])
def modbus_client_read():
    """读取Modbus数据"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        function_code = data.get('function_code', 3)  # 1=读线圈, 3=读保持寄存器
        address = data.get('address', 0)
        count = data.get('count', 1)
        
        with modbus_client_lock:
            if client_id not in modbus_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400
            
            client_info = modbus_clients[client_id]
            client = client_info['client']
            unit_id = client_info['unit_id']
            
            try:
                if function_code == 1:  # 读线圈
                    response = client.read_coils(address=address, count=count, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'读线圈失败: {response}'}), 500
                    # 确保返回0或1，而不是True/False
                    result = [1 if bit else 0 for bit in (response.bits[:count] if response.bits else [])]
                    add_log('INFO', f'读线圈成功: 地址={address}, 数量={count}, 结果={result}')
                    return jsonify({'success': True, 'data': result})
                
                elif function_code == 2:  # 读离散输入
                    response = client.read_discrete_inputs(address=address, count=count, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'读离散输入失败: {response}'}), 500
                    # 确保返回0或1，而不是True/False
                    result = [1 if bit else 0 for bit in (response.bits[:count] if response.bits else [])]
                    add_log('INFO', f'读离散输入成功: 地址={address}, 数量={count}, 结果={result}')
                    return jsonify({'success': True, 'data': result})
                
                elif function_code == 3:  # 读保持寄存器
                    response = client.read_holding_registers(address=address, count=count, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'读保持寄存器失败: {response}'}), 500
                    result = response.registers[:count] if response.registers else []
                    add_log('INFO', f'读保持寄存器成功: 地址={address}, 数量={count}, 结果={result}')
                    return jsonify({'success': True, 'data': result})
                
                elif function_code == 4:  # 读输入寄存器
                    response = client.read_input_registers(address=address, count=count, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'读输入寄存器失败: {response}'}), 500
                    result = response.registers[:count] if response.registers else []
                    add_log('INFO', f'读输入寄存器成功: 地址={address}, 数量={count}, 结果={result}')
                    return jsonify({'success': True, 'data': result})
                
                else:
                    return jsonify({'success': False, 'error': f'不支持的功能码: {function_code}'}), 400
                    
            except ModbusException as e:
                add_log('ERROR', f'Modbus读取异常: {str(e)}')
                return jsonify({'success': False, 'error': f'读取失败: {str(e)}'}), 500
                
    except Exception as e:
        add_log('ERROR', f'读取异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_client/write', methods=['POST'])
def modbus_client_write():
    """写入Modbus数据"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        function_code = data.get('function_code', 6)  # 5=写单个线圈, 6=写单个寄存器, 15=写多个线圈, 16=写多个寄存器
        address = data.get('address', 0)
        values = data.get('values', [])
        
        if not values:
            return jsonify({'success': False, 'error': '写入值不能为空'}), 400
        
        with modbus_client_lock:
            if client_id not in modbus_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400
            
            client_info = modbus_clients[client_id]
            client = client_info['client']
            unit_id = client_info['unit_id']
            
            try:
                if function_code == 5:  # 写单个线圈
                    if len(values) != 1:
                        return jsonify({'success': False, 'error': '写单个线圈只能写入一个值'}), 400
                    value = bool(values[0])
                    response = client.write_coil(address=address, value=value, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'写单个线圈失败: {response}'}), 500
                    add_log('INFO', f'写单个线圈成功: 地址={address}, 值={value}')
                    return jsonify({'success': True, 'message': '写入成功'})
                
                elif function_code == 6:  # 写单个寄存器
                    if len(values) != 1:
                        return jsonify({'success': False, 'error': '写单个寄存器只能写入一个值'}), 400
                    value = int(values[0])
                    if value < 0 or value > 65535:
                        return jsonify({'success': False, 'error': f'寄存器值超出范围: {value} (应在0-65535之间)'}), 400
                    response = client.write_register(address=address, value=value, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'写单个寄存器失败: {response}'}), 500
                    add_log('INFO', f'写单个寄存器成功: 地址={address}, 值={value}')
                    return jsonify({'success': True, 'message': '写入成功'})
                
                elif function_code == 15:  # 写多个线圈
                    coil_values = [bool(v) for v in values]
                    response = client.write_coils(address=address, values=coil_values, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'写多个线圈失败: {response}'}), 500
                    add_log('INFO', f'写多个线圈成功: 地址={address}, 数量={len(coil_values)}, 值={coil_values}')
                    return jsonify({'success': True, 'message': '写入成功'})
                
                elif function_code == 16:  # 写多个寄存器
                    register_values = [int(v) for v in values]
                    for val in register_values:
                        if val < 0 or val > 65535:
                            return jsonify({'success': False, 'error': f'寄存器值超出范围: {val} (应在0-65535之间)'}), 400
                    response = client.write_registers(address=address, values=register_values, slave=unit_id)
                    if response.isError():
                        return jsonify({'success': False, 'error': f'写多个寄存器失败: {response}'}), 500
                    add_log('INFO', f'写多个寄存器成功: 地址={address}, 数量={len(register_values)}, 值={register_values}')
                    return jsonify({'success': True, 'message': '写入成功'})
                
                else:
                    return jsonify({'success': False, 'error': f'不支持的功能码: {function_code}'}), 400
                    
            except ModbusException as e:
                add_log('ERROR', f'Modbus写入异常: {str(e)}')
                return jsonify({'success': False, 'error': f'写入失败: {str(e)}'}), 500
                
    except Exception as e:
        add_log('ERROR', f'写入异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_server/start', methods=['POST'])
def modbus_server_start():
    """启动Modbus服务端"""
    if not PYMODBUS_AVAILABLE:
        error_msg = 'pymodbus未安装或导入失败'
        add_log('ERROR', error_msg)
        return jsonify({'success': False, 'error': error_msg}), 500
    
    # 检查必要的类
    missing_classes = []
    if ModbusSequentialDataBlock is None:
        missing_classes.append('ModbusSequentialDataBlock')
    if ModbusDeviceContext is None:
        missing_classes.append('ModbusDeviceContext')
    if ModbusServerContext is None:
        missing_classes.append('ModbusServerContext')
    
    # 检查服务器类（3.x需要AsyncModbusTcpServer，2.x需要SyncModbusTcpServer）
    if PYMODBUS_MAJOR_VERSION >= 3:
        if AsyncModbusTcpServer is None:
            missing_classes.append('AsyncModbusTcpServer')
    else:
        if SyncModbusTcpServer is None:
            missing_classes.append('SyncModbusTcpServer')
    
    if missing_classes:
        error_msg = f'pymodbus类导入失败，缺失的类: {", ".join(missing_classes)}'
        add_log('ERROR', error_msg)
        return jsonify({'success': False, 'error': error_msg}), 500
    
    try:
        
        data = request.json
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = data.get('port', 502)
        unit_id = data.get('unit_id', 1)
        
        with modbus_server_lock:
            # 停止旧服务端
            if server_id in modbus_servers:
                old_server_info = modbus_servers[server_id]
                old_server_info['running'] = False
                
                # 优雅停止旧服务器
                if PYMODBUS_MAJOR_VERSION >= 3:
                    import asyncio
                    loop = old_server_info.get('loop')
                    server = old_server_info.get('server')
                    if loop and server and not loop.is_closed():
                        try:
                            # 3.x 异步服务器 shutdown() 是异步方法
                            async def stop_old_server():
                                try:
                                    if hasattr(server, 'shutdown'):
                                        await server.shutdown()
                                except:
                                    pass
                                finally:
                                    if loop.is_running():
                                        loop.stop()
                            
                            asyncio.run_coroutine_threadsafe(stop_old_server(), loop)
                            add_log('INFO', f'优雅停止旧异步服务器')
                        except Exception as e:
                            add_log('WARNING', f'停止旧异步服务器时出错: {e}')
                else:
                    server = old_server_info.get('server')
                    if server:
                        try:
                            server.shutdown()
                            add_log('INFO', f'优雅停止旧同步服务器')
                        except Exception as e:
                            add_log('WARNING', f'停止旧同步服务器时出错: {e}')
                
                # 等待旧线程退出
                if 'thread' in old_server_info:
                    old_thread = old_server_info['thread']
                    if old_thread.is_alive():
                        old_thread.join(timeout=2)
                
                del modbus_servers[server_id]
                add_log('INFO', f'旧Modbus服务端已优雅停止: {server_id}')
            
            # 记录服务器启动请求
            add_modbus_server_log('INFO', '=== Modbus服务器启动请求 ===', {
                'server_id': server_id,
                'host': host,
                'port': port,
                'unit_id': unit_id,
                'pymodbus_version': PYMODBUS_VERSION,
                'pymodbus_major_version': PYMODBUS_MAJOR_VERSION
            })
            
            # 1. 创建数据块（标准Modbus地址空间）
            coils = ModbusSequentialDataBlock(0, [0] * 65536)
            discrete_inputs = ModbusSequentialDataBlock(0, [0] * 65536)
            holding_registers = ModbusSequentialDataBlock(0, [0] * 65536)
            input_registers = ModbusSequentialDataBlock(0, [0] * 65536)
            
            add_modbus_server_log('DEBUG', '数据块创建完成', {
                'coils_size': len(coils.values),
                'discrete_inputs_size': len(discrete_inputs.values),
                'holding_registers_size': len(holding_registers.values),
                'input_registers_size': len(input_registers.values)
            })
            
            # 2. 创建从站上下文
            # 关键修复：必须设置 zero_mode=True，否则地址会加1导致数据错位
            # zero_mode=False 时，getValues(0x01, 0, 5) 会变成 getValues(0x01, 1, 5)，跳过第一个值
            # zero_mode=True 时，getValues(0x01, 0, 5) 正确对应数据块的地址 0
            try:
                # 尝试使用zero_mode参数（如果支持）
                store = ModbusDeviceContext(
                    di=discrete_inputs,
                    co=coils,
                    hr=holding_registers,
                    ir=input_registers,
                    zero_mode=True  # 启用零地址模式（Modbus标准），确保地址映射正确
                )
                print(f"[OK] ModbusDeviceContext初始化成功（zero_mode=True）")
                add_log('INFO', 'ModbusDeviceContext初始化成功（zero_mode=True）')
                add_modbus_server_log('INFO', 'ModbusDeviceContext初始化成功', {
                    'zero_mode': True,
                    'has_zero_mode_param': True
                })
            except TypeError:
                # 如果不支持zero_mode参数，使用默认方式（zero_mode=False）
                # 注意：这种情况下需要在调用 getValues/setValues 时手动调整地址
                store = ModbusDeviceContext(
                    di=discrete_inputs,
                    co=coils,
                    hr=holding_registers,
                    ir=input_registers
                )
                print(f"[WARNING] ModbusDeviceContext不支持zero_mode参数，使用默认方式（zero_mode=False）")
                add_log('WARNING', 'ModbusDeviceContext不支持zero_mode参数，使用默认方式（zero_mode=False）')
                # 检查实际的 zero_mode 值
                actual_zero_mode = getattr(store, 'zero_mode', None)
                if actual_zero_mode is not None:
                    print(f"[DEBUG] 实际 zero_mode 值: {actual_zero_mode}")
                    add_log('DEBUG', f'实际 zero_mode 值: {actual_zero_mode}')
                add_modbus_server_log('WARNING', 'ModbusDeviceContext不支持zero_mode参数', {
                    'zero_mode': actual_zero_mode,
                    'has_zero_mode_param': False,
                    'note': '地址映射可能不正确，需要手动调整'
                })
            
            # 3. 创建服务器上下文（修复从站映射，适配3.x不同子版本）
            context = None
            if PYMODBUS_MAJOR_VERSION >= 3:
                try:
                    # 优先尝试使用 slaves 参数并关闭 single 模式，以支持特定的 unit_id
                    context = ModbusServerContext(slaves={unit_id: store}, single=False)
                    print(f"[OK] ModbusServerContext初始化成功（3.x，从站ID={unit_id}）")
                    add_log('INFO', f'ModbusServerContext初始化成功（3.x，从站ID={unit_id}）')
                except TypeError:
                    try:
                        # 如果不支持 slaves 参数，尝试 single=True 模式下的 store 参数
                        context = ModbusServerContext(store=store, single=True)
                        print(f"[OK] ModbusServerContext初始化成功（3.x，单从站模式，使用store参数）")
                        add_log('INFO', 'ModbusServerContext初始化成功（3.x，单从站模式，使用store参数）')
                    except Exception as e:
                        print(f"[ERROR] 3.x ModbusServerContext初始化失败: {e}")
                        raise
            else:
                try:
                    # 2.x 固定使用 slaves 参数
                    context = ModbusServerContext(slaves={unit_id: store}, single=True)
                    print(f"[OK] ModbusServerContext初始化成功（2.x，unit_id={unit_id}）")
                    add_log('INFO', f'ModbusServerContext初始化成功（2.x，unit_id={unit_id}）')
                except Exception as e:
                    print(f"[ERROR] 2.x ModbusServerContext初始化失败: {e}")
                    raise
            
            # 4. 初始化服务器（分3.x和2.x）
            server = None
            server_loop = None
            server_thread = None
            
            if PYMODBUS_MAJOR_VERSION >= 3:
                # 3.x 使用异步服务器（推荐）
                import asyncio
                
                # 用于同步等待服务器实例创建的事件
                server_created_event = threading.Event()
                server_error = [None]
                
                # 后台线程运行事件循环
                def run_async_server():
                    nonlocal loop, server
                    try:
                        # 在新线程中创建 loop
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        loop = new_loop
                        
                        async def start_async_server_internal():
                            nonlocal server
                            try:
                                # 在运行中的循环内实例化服务器
                                # 注意：pymodbus 3.6.9+ 中，如果 ModbusTcpFramer 为 None，不传递 framer 参数
                                # 让服务器使用默认的 framer
                                server_kwargs = {
                                    'context': context,
                                    'address': (host, port),
                                }
                                # 只有在 ModbusTcpFramer 成功导入时才传递 framer 参数
                                if ModbusTcpFramer is not None:
                                    server_kwargs['framer'] = ModbusTcpFramer
                                
                                add_modbus_server_log('DEBUG', '创建 AsyncModbusTcpServer', {
                                    'host': host,
                                    'port': port,
                                    'has_framer': ModbusTcpFramer is not None,
                                    'framer_type': type(ModbusTcpFramer).__name__ if ModbusTcpFramer else None
                                })
                                
                                server = AsyncModbusTcpServer(**server_kwargs)
                                print(f"[OK] AsyncModbusTcpServer在异步循环中实例化成功")
                                add_log('INFO', f'AsyncModbusTcpServer在异步循环中实例化成功')
                                
                                # 通知主线程实例已创建
                                server_created_event.set()
                                
                                # 3.x 正确启动逻辑：使用 serve_forever() 启动服务器并持续监听
                                # serve_forever() 会一直运行直到服务器被关闭
                                add_log('INFO', f'异步Modbus服务器开始监听: {host}:{port}')
                                await server.serve_forever()
                                
                            except Exception as e:
                                server_error[0] = e
                                server_created_event.set()
                                raise
                            finally:
                                try:
                                    if server:
                                        # 使用 shutdown 释放端口
                                        await server.shutdown()
                                    add_log('INFO', f'异步Modbus服务器已停止')
                                except Exception as e:
                                    add_log('WARNING', f'服务器shutdown时出错: {e}')
                        
                        new_loop.run_until_complete(start_async_server_internal())
                    except Exception as e:
                        add_log('ERROR', f'异步服务器运行异常: {e}')
                    finally:
                        try:
                            new_loop.close()
                        except:
                            pass
                
                server_thread = threading.Thread(target=run_async_server, daemon=True)
                server_thread.start()
                
                # 等待服务器在异步线程中创建完成
                if not server_created_event.wait(timeout=10.0):
                    raise Exception("等待异步服务器启动超时")
                
                if server_error[0]:
                    raise server_error[0]
                
                server_loop = loop
                
            else:
                # 2.x 使用同步服务器
                try:
                    # 2.x 同步服务器
                    server_kwargs = {
                        'context': context,
                        'address': (host, port),
                    }
                    # 只有在 ModbusTcpFramer 成功导入时才传递 framer 参数
                    if ModbusTcpFramer is not None:
                        server_kwargs['framer'] = ModbusTcpFramer
                    
                    server = SyncModbusTcpServer(**server_kwargs)
                    print(f"[OK] SyncModbusTcpServer实例化成功")
                    add_log('INFO', f'SyncModbusTcpServer实例化成功')
                except Exception as e:
                    print(f"[ERROR] SyncModbusTcpServer实例化失败: {e}")
                    add_log('ERROR', f'SyncModbusTcpServer实例化失败: {e}')
                    raise
                
                # 后台线程运行服务器
                def run_sync_server():
                    try:
                        add_log('INFO', f'同步Modbus服务器开始运行: {host}:{port}')
                        server.serve_forever()
                    except Exception as e:
                        add_log('ERROR', f'同步服务器运行异常: {e}')
                
                server_thread = threading.Thread(target=run_sync_server, daemon=True)
                server_thread.start()
            
            # 等待服务器启动
            time.sleep(0.5)
            
            # 检查线程是否还在运行
            if not server_thread.is_alive():
                error_msg = '服务器线程启动后立即退出'
                print(f"[ERROR] {error_msg}")
                add_log('ERROR', error_msg)
                return jsonify({'success': False, 'error': error_msg}), 500
            
            print(f"[DEBUG] 服务器线程已启动，线程ID: {server_thread.ident}, 是否存活: {server_thread.is_alive()}")
            add_log('INFO', f'服务器线程已启动，线程ID: {server_thread.ident}, 是否存活: {server_thread.is_alive()}')
            
            # 存储服务器信息
            modbus_servers[server_id] = {
                'server': server,
                'thread': server_thread,
                'context': context,
                'host': host,
                'port': port,
                'unit_id': unit_id,
                'running': True,
                'start_time': datetime.now().isoformat(),
                'loop': server_loop if PYMODBUS_MAJOR_VERSION >= 3 else None
            }
            
            print(f"[OK] Modbus服务端启动成功: {host}:{port} (从站地址: {unit_id}), server_id={server_id}")
            add_log('INFO', f'Modbus服务端启动成功: {host}:{port} (从站地址: {unit_id}), server_id={server_id}')
            
            # 记录服务器启动成功
            add_modbus_server_log('INFO', '=== Modbus服务器启动成功 ===', {
                'server_id': server_id,
                'host': host,
                'port': port,
                'unit_id': unit_id,
                'thread_id': server_thread.ident if server_thread else None,
                'thread_alive': server_thread.is_alive() if server_thread else False,
                'server_type': 'AsyncModbusTcpServer' if PYMODBUS_MAJOR_VERSION >= 3 else 'SyncModbusTcpServer',
                'zero_mode': getattr(store, 'zero_mode', None)
            })
            
            # 返回成功响应，包含数据重置标志，通知前端需要刷新数据
            return jsonify({
                'success': True, 
                'message': '服务端启动成功',
                'data_reset': True,  # 标志：数据已重置为0
                'host': host,
                'port': port,
                'unit_id': unit_id
            })
            
    except NameError as e:
        add_log('ERROR', f'Modbus类未定义: {str(e)}')
        return jsonify({'success': False, 'error': f'pymodbus类未定义: {str(e)}，请检查pymodbus安装和版本'}), 500
    except Exception as e:
        add_log('ERROR', f'Modbus服务端启动异常: {str(e)}')
        import traceback
        error_detail = traceback.format_exc()
        add_log('ERROR', f'详细错误信息: {error_detail}')
        return jsonify({'success': False, 'error': f'启动失败: {str(e)}'}), 500




@app.route('/api/industrial_protocol/modbus_server/stop', methods=['POST'])
def modbus_server_stop():
    """停止Modbus服务端（保证优雅停止且端口释放）"""
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        
        with modbus_server_lock:
            if server_id not in modbus_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            server_info = modbus_servers[server_id]
            server_info['running'] = False
            server = server_info.get('server')
            server_thread = server_info.get('thread')
            
            # 分版本优雅停止
            if PYMODBUS_MAJOR_VERSION >= 3:
                loop = server_info.get('loop')
                if loop and server and not loop.is_closed() and loop.is_running():
                    try:
                        print(f"[DEBUG] 优雅停止异步服务器")
                        add_log('INFO', f'优雅停止异步服务器')
                        
                        # 定义异步停止函数
                        async def stop_server_async():
                            try:
                                if hasattr(server, 'shutdown'):
                                    await server.shutdown()
                                    print(f"[OK] 异步服务器shutdown完成")
                            except Exception as e:
                                print(f"[WARNING] 服务器shutdown失败: {e}")
                            finally:
                                # 停止事件循环（这会中断 serve_forever()）
                                if loop.is_running():
                                    loop.stop()
                        
                        # 使用 run_coroutine_threadsafe 线程安全地提交停止任务
                        import asyncio
                        future = asyncio.run_coroutine_threadsafe(stop_server_async(), loop)
                        # 等待停止完成（最多等待2秒）
                        try:
                            future.result(timeout=2.0)
                        except Exception as e:
                            print(f"[WARNING] 等待停止完成时出错: {e}")
                        
                        print(f"[OK] 异步服务器停止命令已发送")
                        add_log('INFO', f'异步服务器停止命令已发送')
                    except Exception as e:
                        print(f"[WARNING] 停止异步服务器时出错: {str(e)}")
                        add_log('WARNING', f'停止异步服务器时出错: {str(e)}')
            else:
                # 2.x 同步停止
                if server:
                    try:
                        print(f"[DEBUG] 优雅停止同步服务器")
                        add_log('INFO', f'优雅停止同步服务器')
                        server.shutdown()
                        print(f"[OK] 同步服务器已停止")
                        add_log('INFO', f'同步服务器已停止')
                    except Exception as e:
                        print(f"[WARNING] 停止同步服务器时出错: {str(e)}")
                        add_log('WARNING', f'停止同步服务器时出错: {str(e)}')
            
            # 等待线程退出（超时2秒）
            if server_thread and server_thread.is_alive():
                print(f"[DEBUG] 等待服务器线程退出，线程ID: {server_thread.ident}")
                add_log('INFO', f'等待服务器线程退出，线程ID: {server_thread.ident}')
                server_thread.join(timeout=2.0)
                if server_thread.is_alive():
                    print(f"[WARNING] 服务器线程未在超时时间内退出，但已停止服务")
                    add_log('WARNING', f'服务器线程未在超时时间内退出，但已停止服务')
                else:
                    print(f"[DEBUG] 服务器线程已正常退出")
                    add_log('INFO', f'服务器线程已正常退出')
            
            # 清理资源
            del modbus_servers[server_id]
            add_log('INFO', f'Modbus服务端已优雅停止: {server_id}')
            
            # 记录服务器停止成功
            add_modbus_server_log('INFO', '=== Modbus服务器停止成功 ===', {
                'server_id': server_id,
                'thread_id': server_thread.ident if server_thread else None,
                'thread_alive': server_thread.is_alive() if server_thread else False
            })
            
            return jsonify({'success': True, 'message': '服务端已停止'})
                
    except Exception as e:
        add_log('ERROR', f'Modbus服务端停止异常: {str(e)}')
        import traceback
        add_log('ERROR', f'停止异常详情: {traceback.format_exc()}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_server/status', methods=['GET'])
def modbus_server_status():
    """获取Modbus服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')
        
        with modbus_server_lock:
            if server_id in modbus_servers:
                server_info = modbus_servers[server_id]
                return jsonify({
                    'success': True,
                    'running': server_info['running'],
                    'host': server_info['host'],
                    'port': server_info['port'],
                    'unit_id': server_info['unit_id'],
                    'start_time': server_info['start_time']
                })
            else:
                return jsonify({'success': True, 'running': False})
                
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_server/get_data', methods=['POST'])
def modbus_server_get_data():
    """读取Modbus服务端数据"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        function_code = int(data.get('function_code', 1))
        address = int(data.get('address', 0))
        count = int(data.get('count', 1))
        
        # 统一处理地址映射：
        # 前端传来的地址如 40001，内部实际地址是 0
        # 1-9999 -> FC 1 (Coils), 内部地址 = address - 1
        # 10001-19999 -> FC 2 (Discrete Inputs), 内部地址 = address - 10001
        # 40001-49999 -> FC 3 (Holding Registers), 内部地址 = address - 40001
        # 30001-39999 -> FC 4 (Input Registers), 内部地址 = address - 30001
        
        actual_modbus_address = 0
        if function_code == 1:
            actual_modbus_address = address - 1
        elif function_code == 2:
            actual_modbus_address = address - 10001
        elif function_code == 3:
            actual_modbus_address = address - 40001
        elif function_code == 4:
            actual_modbus_address = address - 30001
            
        # 确保地址不小于0
        if actual_modbus_address < 0:
            actual_modbus_address = 0

        with modbus_server_lock:
            if server_id not in modbus_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            server_info = modbus_servers[server_id]
            context = server_info['context']
            unit_id = server_info['unit_id']
            store = context[unit_id]
            
            try:
                # 记录读取请求
                zero_mode = getattr(store, 'zero_mode', None)
                add_modbus_server_log('DEBUG', '=== 读取数据请求 ===', {
                    'server_id': server_id,
                    'function_code': function_code,
                    'frontend_address': address,
                    'actual_modbus_address': actual_modbus_address,
                    'count': count,
                    'unit_id': unit_id,
                    'zero_mode': zero_mode
                })
                
                # pymodbus getValues(fc_as_hex, address, count)
                # fc_as_hex: 功能码的十六进制形式（0x01=读线圈, 0x02=读离散输入, 0x03=读保持寄存器, 0x04=读输入寄存器）
                # 注意：如果 zero_mode=False，getValues 内部会将地址加1，所以需要确保 zero_mode=True
                # 或者手动调整地址（但当前代码已设置 zero_mode=True，所以不需要调整）
                fc_as_hex = function_code  # pymodbus 3.7.0 中，十进制和十六进制都可以
                
                # 记录调用前的状态
                add_modbus_server_log('DEBUG', '调用 getValues 前', {
                    'fc_as_hex': fc_as_hex,
                    'address': actual_modbus_address,
                    'count': count,
                    'store_type': type(store).__name__
                })
                
                values_raw = store.getValues(fc_as_hex, actual_modbus_address, count)
                
                # 记录 getValues 返回的原始数据
                add_modbus_server_log('DEBUG', 'getValues 返回原始数据', {
                    'values_raw': values_raw,
                    'values_raw_type': [type(v).__name__ for v in values_raw] if values_raw else [],
                    'values_raw_length': len(values_raw) if values_raw else 0
                })
                
                # 修复响应数据格式：线圈和离散输入返回 0/1，寄存器返回整数值
                values = []
                for i, v in enumerate(values_raw):
                    if function_code in [1, 2]:  # 线圈和离散输入
                        # 确保返回 0 或 1（而不是 True/False）
                        converted_value = 1 if v else 0
                        values.append(converted_value)
                        add_modbus_server_log('DEBUG', f'数据转换 [{i}]', {
                            'raw_value': v,
                            'raw_type': type(v).__name__,
                            'converted_value': converted_value
                        })
                    else:  # 寄存器
                        converted_value = int(v)
                        values.append(converted_value)
                
                # 记录最终响应
                add_modbus_server_log('INFO', '=== 读取数据响应 ===', {
                    'function_code': function_code,
                    'frontend_address': address,
                    'actual_modbus_address': actual_modbus_address,
                    'count': count,
                    'values': values,
                    'values_length': len(values)
                })
                
                add_log('DEBUG', f'读取数据: function_code={function_code}, address={address}->{actual_modbus_address}, count={count}, values={values}')
                return jsonify({'success': True, 'data': values})
                
            except Exception as e:
                add_log('ERROR', f'读取数据内部异常: {str(e)}')
                return jsonify({'success': False, 'error': str(e)}), 500
                
    except Exception as e:
        add_log('ERROR', f'读取数据请求异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_server/set_data', methods=['POST'])
def modbus_server_set_data():
    """单个设置Modbus服务端数据"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        function_code = int(data.get('function_code', 1))
        address = int(data.get('address', 0))
        value = data.get('value', 0)
        
        actual_modbus_address = 0
        if function_code == 1: actual_modbus_address = address - 1
        elif function_code == 2: actual_modbus_address = address - 10001
        elif function_code == 3: actual_modbus_address = address - 40001
        elif function_code == 4: actual_modbus_address = address - 30001
        
        if actual_modbus_address < 0: actual_modbus_address = 0

        with modbus_server_lock:
            if server_id not in modbus_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            server_info = modbus_servers[server_id]
            context = server_info['context']
            unit_id = server_info['unit_id']
            store = context[unit_id]
            
            # 记录设置请求
            zero_mode = getattr(store, 'zero_mode', None)
            add_modbus_server_log('DEBUG', '=== 设置数据请求 ===', {
                'server_id': server_id,
                'function_code': function_code,
                'frontend_address': address,
                'actual_modbus_address': actual_modbus_address,
                'value': value,
                'unit_id': unit_id,
                'zero_mode': zero_mode
            })
            
            # 准备设置的值
            if function_code in [1, 2]:
                set_value = bool(int(value))
                values_to_set = [set_value]
            else:
                set_value = int(value)
                values_to_set = [set_value]
            
            add_modbus_server_log('DEBUG', '调用 setValues 前', {
                'function_code': function_code,
                'address': actual_modbus_address,
                'values_to_set': values_to_set,
                'value_types': [type(v).__name__ for v in values_to_set]
            })
            
            store.setValues(function_code, actual_modbus_address, values_to_set)
            
            # 验证设置是否成功
            verify_values = store.getValues(function_code, actual_modbus_address, 1)
            verify_success = (verify_values[0] == set_value) if verify_values else False
            
            add_modbus_server_log('INFO', '=== 设置数据完成 ===', {
                'function_code': function_code,
                'frontend_address': address,
                'actual_modbus_address': actual_modbus_address,
                'set_value': set_value,
                'verify_values': verify_values,
                'verify_success': verify_success
            })
            
            # 返回成功响应，包含更新的数据，让前端同步更新
            return jsonify({
                'success': True, 
                'message': '设置成功',
                'data_updated': True,  # 标志：数据已更新
                'function_code': function_code,
                'address': address,
                'value': int(set_value) if function_code in [1, 2] else int(set_value),
                'verify_value': int(verify_values[0]) if verify_values and verify_success else None
            })
                
    except Exception as e:
        add_log('ERROR', f'设置数据异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/modbus_server/bulk_set_data', methods=['POST'])
def modbus_server_bulk_set_data():
    """批量设置Modbus服务端数据（用于随机和重置）"""
    if not PYMODBUS_AVAILABLE:
        return jsonify({'success': False, 'error': 'pymodbus未安装'}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        function_code = int(data.get('function_code', 1))
        address_offset = int(data.get('address', 0)) # 这里是相对于该功能码起始位置的偏移，通常从0开始
        values = data.get('values', [])
        
        with modbus_server_lock:
            if server_id not in modbus_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            server_info = modbus_servers[server_id]
            context = server_info['context']
            unit_id = server_info['unit_id']
            store = context[unit_id]
            
            # 记录批量设置请求
            zero_mode = getattr(store, 'zero_mode', None)
            add_modbus_server_log('DEBUG', '=== 批量设置数据请求 ===', {
                'server_id': server_id,
                'function_code': function_code,
                'address_offset': address_offset,
                'values_count': len(values),
                'values_preview': values[:10] if len(values) > 10 else values,
                'unit_id': unit_id,
                'zero_mode': zero_mode
            })
            
            processed_values = []
            if function_code in [1, 2]:
                processed_values = [bool(int(v)) for v in values]
            else:
                processed_values = [int(v) for v in values]
            
            add_modbus_server_log('DEBUG', '调用 setValues 前（批量）', {
                'function_code': function_code,
                'address_offset': address_offset,
                'processed_values_count': len(processed_values),
                'processed_values_preview': processed_values[:10] if len(processed_values) > 10 else processed_values,
                'value_types': [type(v).__name__ for v in processed_values[:5]]
            })
                
            store.setValues(function_code, address_offset, processed_values)
            
            # 验证设置是否成功（只验证前几个值）
            verify_count = min(5, len(processed_values))
            verify_success = False
            verify_values = []
            if verify_count > 0:
                verify_values = store.getValues(function_code, address_offset, verify_count)
                verify_success = verify_values == processed_values[:verify_count] if verify_values else False
                add_modbus_server_log('INFO', '=== 批量设置数据完成 ===', {
                    'function_code': function_code,
                    'address_offset': address_offset,
                    'values_count': len(processed_values),
                    'verify_count': verify_count,
                    'verify_values': verify_values,
                    'verify_success': verify_success
                })
            
            # 返回成功响应，包含更新的数据范围，让前端同步更新
            return jsonify({
                'success': True, 
                'message': f'批量设置{len(values)}条数据成功',
                'data_updated': True,  # 标志：数据已更新
                'function_code': function_code,
                'address': address_offset,
                'count': len(processed_values),
                'values': [int(v) if function_code not in [1, 2] else (1 if v else 0) for v in processed_values[:100]]  # 返回前100个值供前端更新
            })
                
    except Exception as e:
        add_log('ERROR', f'批量设置数据异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/logs', methods=['GET'])
def get_logs():
    """获取日志"""
    try:
        limit = int(request.args.get('limit', 100))
        with protocol_logs_lock:
            logs = protocol_logs[-limit:] if len(protocol_logs) > limit else protocol_logs
            return jsonify({'success': True, 'logs': logs})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'success': True,
        'message': 'Industrial Protocol Agent is running',
        'pymodbus_available': PYMODBUS_AVAILABLE,
        'snap7_available': SNAP7_AVAILABLE,
        'snap7_version': snap7_version
    })


# ==================== S7 服务端 API ====================

def register_s7_areas(server_id, db_list=None):
    """
    注册S7服务端区域（兼容snap7 1.3和2.0.2）

    重要：服务端register_area使用的是SrvArea枚举（DB=5），不是Area枚举（DB=0x84）！
    - snap7 2.0.2: snap7.type.SrvArea.DB = 5
    - snap7 1.3: snap7.server.srvArea.DB = 5 或硬编码值5
    """
    if not SNAP7_AVAILABLE:
        return
    try:
        with s7_server_lock:
            if server_id not in s7_servers:
                return
            server_info = s7_servers.get(server_id)
            if not server_info or 'server' not in server_info:
                return
            server = server_info['server']
            storage = s7_data_storage.get(server_id)
            if not storage:
                return

            # 需要注册的DB列表
            if db_list is None:
                db_list = [1, 2, 3]

            # 获取服务端DB区域常量
            # 重要：必须使用SrvArea（值=5），不是Area（值=0x84）！
            SRV_AREA_DB = None
            area_source = None

            # 方式1: 使用全局变量snap7_srv_area（已在初始化时正确设置）
            if snap7_srv_area is not None and hasattr(snap7_srv_area, 'DB'):
                SRV_AREA_DB = snap7_srv_area.DB
                area_source = f"snap7_srv_area.DB（版本: {snap7_version}）"
                add_log('INFO', f'使用全局snap7_srv_area.DB')
            # 方式2: snap7_type.SrvArea (2.0.2)
            elif snap7_type is not None and hasattr(snap7_type, 'SrvArea') and hasattr(snap7_type.SrvArea, 'DB'):
                SRV_AREA_DB = snap7_type.SrvArea.DB
                area_source = "snap7.type.SrvArea.DB（2.x方式）"
            # 方式3: snap7.server.srvArea (1.3)
            elif hasattr(snap7, 'server') and hasattr(snap7.server, 'srvArea') and hasattr(snap7.server.srvArea, 'DB'):
                SRV_AREA_DB = snap7.server.srvArea.DB
                area_source = "snap7.server.srvArea.DB（1.3方式）"
            # 方式4: 硬编码值（最后手段）
            else:
                # 服务端DB区域枚举值是5（不是0x84！）
                SRV_AREA_DB = 5
                area_source = "硬编码值5（兼容模式）"
                add_log('WARNING', '未找到SrvArea枚举，使用硬编码值5')

            if SRV_AREA_DB is None:
                add_log('ERROR', '无法获取服务端DB区域常量，区域注册失败')
                return

            # 获取枚举值（用于日志）
            if hasattr(SRV_AREA_DB, 'value'):
                enum_value = SRV_AREA_DB.value
            else:
                enum_value = int(SRV_AREA_DB)

            add_log('INFO', f'=== S7区域注册（版本兼容模式） ===')
            add_log('INFO', f'区域来源: {area_source}')
            add_log('INFO', f'使用DB区域值: {SRV_AREA_DB} (0x{enum_value:02X})')

            # 参考工作代码：初始化ctypes缓冲区并创建DB映射
            import ctypes
            if 'ctypes_buffers' not in server_info:
                server_info['ctypes_buffers'] = {}
            
            # DB块映射（参考工作代码的db_mapping模式）
            db_mapping = {}
            
            for db_num in db_list:
                if db_num not in storage['db']:
                    continue
                
                db_data = storage['db'][db_num]
                db_size = len(db_data)
                
                # 创建或获取ctypes缓冲区（参考工作代码：ctypes.c_ubyte * DB_SIZE）
                if db_num not in server_info['ctypes_buffers']:
                    c_buffer_type = ctypes.c_ubyte * db_size
                    c_buffer = c_buffer_type()  # 正确：直接实例化数组类型
                    # 初始化数据
                    for i in range(min(db_size, len(db_data))):
                        c_buffer[i] = db_data[i]
                    server_info['ctypes_buffers'][db_num] = c_buffer
                    if 'bytearray_refs' not in server_info:
                        server_info['bytearray_refs'] = {}
                    server_info['bytearray_refs'][db_num] = db_data
                    add_log('INFO', f'创建DB{db_num}的ctypes缓冲区: {db_size}字节')
                else:
                    c_buffer = server_info['ctypes_buffers'][db_num]
                    # 同步数据
                    for i in range(min(len(c_buffer), len(db_data))):
                        c_buffer[i] = db_data[i]
                    add_log('DEBUG', f'同步DB{db_num}数据到ctypes缓冲区')
                
                # 添加到映射字典（参考工作代码的db_mapping）
                db_mapping[db_num] = c_buffer
            
            # 参考工作代码：注册DB块（for db_num, buf in db_mapping.items()）
            try:
                for db_num, buf in db_mapping.items():
                    try:
                        server.register_area(SRV_AREA_DB, db_num, buf)
                        add_log('INFO', f'DB{db_num}区域注册成功（枚举类型：{SRV_AREA_DB}）')
                    except Exception as e:
                        err_msg = str(getattr(e, 'args', [''])[0]).lower()
                        if "cannot register area since already exists" in err_msg or "already exists" in err_msg:
                            add_log('INFO', f'DB{db_num}已注册，跳过重复注册')
                        else:
                            add_log('WARNING', f'DB{db_num}注册警告: {e}（服务继续运行）')
            except Exception as e:
                add_log('ERROR', f'S7区域注册异常: {e}')
                import traceback
                add_log('DEBUG', f'详细错误: {traceback.format_exc()}')
                
    except Exception as e:
        add_log('ERROR', f'S7区域注册异常: {e}')
        import traceback
        add_log('DEBUG', f'详细错误: {traceback.format_exc()}')


def sync_s7_data_to_server(server_id, db_number=None):
    """
    将s7_data_storage的数据同步到服务器
    优先使用server.db（如果支持自动注册），否则使用ctypes缓冲区（手动注册方式）
    """
    if not SNAP7_AVAILABLE:
        return
    
    try:
        with s7_server_lock:
            if server_id not in s7_servers:
                return
            server_info = s7_servers.get(server_id)
            if not server_info:
                return
            
            server = server_info['server']
            storage = s7_data_storage.get(server_id)
            if not storage:
                return

            # 确定需要同步的DB列表
            if db_number is not None:
                db_list = [db_number] if db_number in [1, 2, 3] else []
            else:
                db_list = [1, 2, 3]

            # 优先尝试使用server.db（自动注册方式）
            if hasattr(server, 'db'):
                try:
                    if isinstance(server.db, dict):
                        # 直接通过server.db设置数据
                        for db_num in db_list:
                            if db_num in storage['db']:
                                db_data = storage['db'][db_num]
                                server.db[db_num] = db_data
                                add_log('DEBUG', f'通过server.db同步DB{db_num}数据 ({len(db_data)}字节)')
                        return
                    elif hasattr(server.db, '__setitem__'):
                        # 支持索引赋值
                        for db_num in db_list:
                            if db_num in storage['db']:
                                db_data = storage['db'][db_num]
                                server.db[db_num] = db_data
                                add_log('DEBUG', f'通过server.db[索引]同步DB{db_num}数据 ({len(db_data)}字节)')
                        return
                except Exception as db_e:
                    add_log('DEBUG', f'使用server.db同步失败: {db_e}，尝试ctypes缓冲区方式')
            
            # 使用ctypes缓冲区同步（手动注册方式）
            if 'ctypes_buffers' not in server_info:
                add_log('DEBUG', '未找到ctypes_buffers，跳过数据同步')
                return

            for db_num in db_list:
                if db_num not in storage['db']:
                    continue
                if db_num not in server_info['ctypes_buffers']:
                    continue
                
                db_data = storage['db'][db_num]
                c_buffer = server_info['ctypes_buffers'][db_num]
                
                # 同步数据：将bytearray复制到ctypes缓冲区
                sync_size = min(len(c_buffer), len(db_data))
                for i in range(sync_size):
                    c_buffer[i] = db_data[i]
                
                add_log('DEBUG', f'S7数据同步: DB{db_num}已同步到ctypes缓冲区 ({sync_size}字节)')
                
    except Exception as e:
        add_log('WARNING', f'S7数据同步异常: {e}')

# ==================== S7 客户端 API ====================

@app.route('/api/industrial_protocol/s7_client/connect', methods=['POST'])
def s7_client_connect():
    """连接S7服务器"""
    if not SNAP7_AVAILABLE:
        error_msg = 'python-snap7未安装或导入失败'
        add_log('ERROR', error_msg)
        return jsonify({'success': False, 'error': error_msg}), 500

    if Snap7Client is None:
        error_msg = 'snap7.Client类不可用'
        add_log('ERROR', error_msg)
        return jsonify({'success': False, 'error': error_msg}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        server_ip = data.get('server_ip')
        port = data.get('port', 102)
        rack = data.get('rack', 0)
        slot = data.get('slot', 2)

        if not server_ip:
            return jsonify({'success': False, 'error': '缺少服务器IP地址'}), 400

        add_log('INFO', f'S7客户端连接请求: {server_ip}:{port}, Rack={rack}, Slot={slot}')

        with s7_client_lock:
            # 如果已存在连接，先断开
            if client_id in s7_clients:
                old_client = s7_clients[client_id].get('client')
                if old_client:
                    try:
                        old_client.disconnect()
                    except Exception:
                        pass
                del s7_clients[client_id]

            # 创建新的客户端连接
            client = Snap7Client()
            client.connect(server_ip, rack, slot, port)

            # 存储连接信息
            s7_clients[client_id] = {
                'client': client,
                'server_ip': server_ip,
                'port': port,
                'rack': rack,
                'slot': slot,
                'connected': True
            }

            add_log('INFO', f'S7客户端连接成功: {server_ip}:{port}')
            return jsonify({
                'success': True,
                'message': f'已连接到 {server_ip}:{port}',
                'client_id': client_id
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7客户端连接失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/disconnect', methods=['POST'])
def s7_client_disconnect():
    """断开S7客户端连接"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': True, 'message': '客户端未连接'})

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if client:
                try:
                    client.disconnect()
                except Exception as e:
                    add_log('WARNING', f'S7客户端断开连接时出错: {e}')

            del s7_clients[client_id]
            add_log('INFO', f'S7客户端已断开: {client_id}')

        return jsonify({'success': True, 'message': '已断开连接'})

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7客户端断开连接失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/read', methods=['POST'])
def s7_client_read():
    """读取S7服务器数据

    数据类型与协议传输格式:
    - BYTE: WordLen.Byte (2), 1字节/个
    - WORD: WordLen.Word (4), 2字节/个
    - DWORD: WordLen.DWord (6), 4字节/个
    - REAL: WordLen.Real (8), 4字节/个
    - INT: WordLen.Int (5), 2字节/个
    - DINT: WordLen.DInt (7), 4字节/个
    - BOOL: WordLen.Bit (1), 1位/个
    """
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        area = data.get('area', 'DB')
        db_number = data.get('db_number', 1)
        start = data.get('start', 0)
        amount = data.get('size', 10)  # 数据个数
        data_type = data.get('data_type', 'BYTE')  # 数据类型

        # 数据类型到 WordLen 的映射
        word_len_map = {
            'BOOL': 1,    # WordLen.Bit
            'BYTE': 2,    # WordLen.Byte
            'CHAR': 3,    # WordLen.Char
            'WORD': 4,    # WordLen.Word
            'INT': 5,     # WordLen.Int
            'DWORD': 6,   # WordLen.DWord
            'DINT': 7,    # WordLen.DInt
            'REAL': 8,    # WordLen.Real
        }

        # 每种数据类型的字节数
        bytes_per_element = {
            'BOOL': 1, 'BYTE': 1, 'CHAR': 1,
            'WORD': 2, 'INT': 2,
            'DWORD': 4, 'DINT': 4, 'REAL': 4,
        }

        word_len = word_len_map.get(data_type.upper(), 2)
        byte_size = amount * bytes_per_element.get(data_type.upper(), 1)

        # 如果提供了新的连接参数，尝试连接
        server_ip = data.get('server_ip')
        port = data.get('port', 102)
        rack = data.get('rack', 0)
        slot = data.get('slot', 2)

        with s7_client_lock:
            # 如果客户端不存在但有连接参数，创建新连接
            if client_id not in s7_clients and server_ip:
                if Snap7Client is None:
                    return jsonify({'success': False, 'error': 'snap7.Client类不可用'}), 500

                client = Snap7Client()
                client.connect(server_ip, rack, slot, port)
                s7_clients[client_id] = {
                    'client': client,
                    'server_ip': server_ip,
                    'port': port,
                    'rack': rack,
                    'slot': slot,
                    'connected': True
                }
                add_log('INFO', f'S7客户端自动连接: {server_ip}:{port}')

            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接，请先连接服务器'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            # 区域类型映射
            area_map = {
                'DB': 0x84,      # 数据块
                'I': 0x81,       # 输入区
                'Q': 0x82,       # 输出区
                'M': 0x83,       # 标志位
                'C': 0x1C,       # 计数器
                'T': 0x1D,       # 定时器
            }
            area_code = area_map.get(area.upper(), 0x84)

            # 使用 read_multi_vars 正确传递数据类型
            try:
                from snap7.type import S7DataItem

                # 创建数据缓冲区
                buffer = (ctypes.c_ubyte * byte_size)()

                # 创建 S7DataItem
                item = S7DataItem()
                item.Area = area_code
                item.WordLen = word_len
                item.DBNumber = db_number if area.upper() == 'DB' else 0
                item.Start = start
                item.Amount = amount  # 数据个数，不是字节数
                item.pData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))

                # 创建 item 数组
                items = (S7DataItem * 1)(item)

                # 执行读取
                result_code, result_items = client.read_multi_vars(items)

                # 检查结果
                if result_code == 0 and result_items[0].Result == 0:
                    # 成功读取
                    data_list = list(buffer)
                    add_log('INFO', f'S7客户端读取成功: 区域={area}, DB={db_number}, 起始={start}, 数量={amount}, 类型={data_type}, 字节={byte_size}')
                else:
                    error_code = result_items[0].Result if result_items else result_code
                    add_log('ERROR', f'S7客户端读取失败: 结果码={result_code}, 项目结果={result_items[0].Result if result_items else "N/A"}')
                    # 回退到 db_read
                    if area.upper() == 'DB':
                        result = client.db_read(db_number, start, byte_size)
                        data_list = list(result) if result else []
                    else:
                        result = client.read_area(area_code, db_number, start, byte_size)
                        data_list = list(result) if result else []
            except Exception as e:
                add_log('WARNING', f'read_multi_vars失败，回退到db_read: {e}')
                # 回退到 db_read
                if area.upper() == 'DB':
                    result = client.db_read(db_number, start, byte_size)
                else:
                    result = client.read_area(area_code, db_number, start, byte_size)
                data_list = list(result) if result else []

            # 根据数据类型转换数据
            interpreted_data = []
            type_info = {'type': data_type, 'size_per_element': bytes_per_element.get(data_type.upper(), 1), 'element_count': amount}

            if snap7_util is not None and data_list:
                try:
                    result_bytes = bytearray(data_list)
                    if data_type == 'BYTE':
                        interpreted_data = data_list
                    elif data_type == 'WORD':
                        for i in range(0, len(result_bytes) - 1, 2):
                            interpreted_data.append(snap7_util.get_word(result_bytes, i))
                    elif data_type == 'DWORD':
                        for i in range(0, len(result_bytes) - 3, 4):
                            interpreted_data.append(snap7_util.get_dword(result_bytes, i))
                    elif data_type == 'REAL':
                        for i in range(0, len(result_bytes) - 3, 4):
                            interpreted_data.append(snap7_util.get_real(result_bytes, i))
                    elif data_type == 'INT':
                        for i in range(0, len(result_bytes) - 1, 2):
                            interpreted_data.append(snap7_util.get_int(result_bytes, i))
                    elif data_type == 'DINT':
                        for i in range(0, len(result_bytes) - 3, 4):
                            interpreted_data.append(snap7_util.get_dint(result_bytes, i))
                    else:
                        interpreted_data = data_list
                except Exception as conv_err:
                    add_log('WARNING', f'数据类型转换失败: {conv_err}')
                    interpreted_data = data_list
            else:
                interpreted_data = data_list

            return jsonify({
                'success': True,
                'data': data_list,  # 原始字节
                'interpreted': interpreted_data,  # 按数据类型解释的值
                'type_info': type_info,
                'size': len(data_list),
                'area': area,
                'db_number': db_number,
                'start': start,
                'data_type': data_type
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7客户端读取失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/write', methods=['POST'])
def s7_client_write():
    """写入S7服务器数据

    数据类型与协议传输格式:
    - BYTE: WordLen.Byte (2), 1字节/个
    - WORD: WordLen.Word (4), 2字节/个
    - DWORD: WordLen.DWord (6), 4字节/个
    - REAL: WordLen.Real (8), 4字节/个
    - INT: WordLen.Int (5), 2字节/个
    - DINT: WordLen.DInt (7), 4字节/个

    请求参数:
    - data: 要写入的数据列表（字节数组）
    - data_type: 数据类型
    - start: 起始地址（字节地址）
    - size: 数据个数
    """
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        area = data.get('area', 'DB')
        db_number = data.get('db_number', 1)
        start = data.get('start', 0)
        write_data = data.get('data', [])
        data_type = data.get('data_type', 'BYTE')

        if not write_data:
            return jsonify({'success': False, 'error': '缺少写入数据'}), 400

        # 数据类型到 WordLen 的映射
        word_len_map = {
            'BOOL': 1,    # WordLen.Bit
            'BYTE': 2,    # WordLen.Byte
            'CHAR': 3,    # WordLen.Char
            'WORD': 4,    # WordLen.Word
            'INT': 5,     # WordLen.Int
            'DWORD': 6,   # WordLen.DWord
            'DINT': 7,    # WordLen.DInt
            'REAL': 8,    # WordLen.Real
        }

        # 每种数据类型的字节数
        bytes_per_element = {
            'BOOL': 1, 'BYTE': 1, 'CHAR': 1,
            'WORD': 2, 'INT': 2,
            'DWORD': 4, 'DINT': 4, 'REAL': 4,
        }

        word_len = word_len_map.get(data_type.upper(), 2)
        byte_per_elem = bytes_per_element.get(data_type.upper(), 1)

        # 计算数据个数（字节数 / 每个数据的字节数）
        amount = len(write_data) // byte_per_elem

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            # 区域类型映射
            area_map = {
                'DB': 0x84,
                'I': 0x81,
                'Q': 0x82,
                'M': 0x83,
            }
            area_code = area_map.get(area.upper(), 0x84)

            # 使用 write_multi_vars 正确传递数据类型
            try:
                from snap7.type import S7DataItem

                # 创建数据缓冲区
                buffer = (ctypes.c_ubyte * len(write_data))(*write_data)

                # 创建 S7DataItem
                item = S7DataItem()
                item.Area = area_code
                item.WordLen = word_len
                item.DBNumber = db_number if area.upper() == 'DB' else 0
                item.Start = start
                item.Amount = amount  # 数据个数
                item.pData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))

                # 创建 item 列表
                items = [item]

                # 执行写入
                result_code = client.write_multi_vars(items)

                if result_code == 0:
                    add_log('INFO', f'S7客户端写入成功: 区域={area}, DB={db_number}, 起始={start}, 数量={amount}, 类型={data_type}')
                    return jsonify({
                        'success': True,
                        'message': f'已写入 {amount} 个{data_type}数据',
                        'element_count': amount,
                        'byte_count': len(write_data),
                        'data_type': data_type
                    })
                else:
                    add_log('ERROR', f'S7客户端写入失败: 结果码={result_code}')
                    # 回退到 db_write
                    if area.upper() == 'DB':
                        client.db_write(db_number, start, bytearray(write_data))
                    else:
                        client.write_area(area_code, db_number, start, bytearray(write_data))
                    return jsonify({
                        'success': True,
                        'message': f'已写入 {len(write_data)} 字节',
                        'element_count': amount,
                        'byte_count': len(write_data),
                        'data_type': data_type
                    })
            except Exception as e:
                add_log('WARNING', f'write_multi_vars失败，回退到db_write: {e}')
                # 回退到 db_write
                data_bytes = bytearray(write_data)
                if area.upper() == 'DB':
                    client.db_write(db_number, start, data_bytes)
                else:
                    client.write_area(area_code, db_number, start, data_bytes)

                add_log('INFO', f'S7客户端写入: 区域={area}, DB={db_number}, 起始={start}, 字节数={len(write_data)}')

                return jsonify({
                    'success': True,
                    'message': f'已写入 {amount} 个{data_type}数据（{len(write_data)}字节）',
                    'element_count': amount,
                    'byte_count': len(write_data),
                    'data_type': data_type
                })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7客户端写入失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/status', methods=['GET'])
def s7_client_status():
    """获取S7客户端状态"""
    try:
        client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({
                    'success': True,
                    'connected': False,
                    'message': '客户端未连接'
                })

            client_info = s7_clients[client_id]
            return jsonify({
                'success': True,
                'connected': client_info.get('connected', False),
                'server_ip': client_info.get('server_ip'),
                'port': client_info.get('port'),
                'rack': client_info.get('rack'),
                'slot': client_info.get('slot')
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== S7 客户端控制 API ====================

@app.route('/api/industrial_protocol/s7_client/plc_cold_start', methods=['POST'])
def s7_client_plc_cold_start():
    """S7 PLC冷启动"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.json.get('client_id', 'default') if request.json else 'default'

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_cold_start()
            add_log('INFO', f'S7 PLC冷启动: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC冷启动成功',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC冷启动失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/plc_hot_start', methods=['POST'])
def s7_client_plc_hot_start():
    """S7 PLC热启动"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.json.get('client_id', 'default') if request.json else 'default'

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_hot_start()
            add_log('INFO', f'S7 PLC热启动: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC热启动成功',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC热启动失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/plc_stop', methods=['POST'])
def s7_client_plc_stop():
    """停止S7 PLC"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.json.get('client_id', 'default') if request.json else 'default'

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            result = client.plc_stop()
            add_log('INFO', f'S7 PLC停止: client_id={client_id}, result={result}')

            return jsonify({
                'success': True,
                'message': 'PLC已停止',
                'result': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 PLC停止失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/get_cpu_info', methods=['GET'])
def s7_client_get_cpu_info():
    """获取S7 CPU信息"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            cpu_info = client.get_cpu_info()

            # 转换为可序列化的字典（bytes需要decode）
            def safe_decode(val):
                """安全解码bytes或字符串"""
                if isinstance(val, bytes):
                    try:
                        return val.decode('utf-8').rstrip('\x00')
                    except:
                        return val.hex()
                return str(val) if val else ''

            result = {
                'module_type': safe_decode(getattr(cpu_info, 'ModuleType', '')),
                'serial_number': safe_decode(getattr(cpu_info, 'SerialNumber', '')),
                'as_name': safe_decode(getattr(cpu_info, 'ASName', '')),
                'module_name': safe_decode(getattr(cpu_info, 'ModuleName', '')),
                'copyright': safe_decode(getattr(cpu_info, 'Copyright', '')),
            }

            add_log('INFO', f'S7 CPU信息: client_id={client_id}, module={result["module_name"]}')

            return jsonify({
                'success': True,
                **result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'获取S7 CPU信息失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/get_cpu_state', methods=['GET'])
def s7_client_get_cpu_state():
    """获取S7 CPU运行状态"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            state = client.get_cpu_state()

            # 处理bytes类型的返回值
            if isinstance(state, bytes):
                state = state.decode('utf-8').rstrip('\x00')

            # 标准化状态字符串
            # snap7返回的状态可能是: S7CPUSTATUSRUN, S7CPUSTATUSSTOP, S7CPUSTATUSUNKNOWN
            state_str = str(state).upper() if state else 'UNKNOWN'

            # 判断是否运行中
            is_running = 'RUN' in state_str and 'STOP' not in state_str

            add_log('DEBUG', f'S7 CPU状态: client_id={client_id}, state={state_str}, running={is_running}')

            return jsonify({
                'success': True,
                'state': state_str,
                'running': is_running
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'获取S7 CPU状态失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/list_blocks', methods=['GET'])
def s7_client_list_blocks():
    """列出S7 PLC中的所有块"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            blocks_list = client.list_blocks()

            # 兼容不同snap7版本的BlocksList属性名
            # snap7 1.3: 使用小写属性名 (ob_count, db_count等)
            # snap7 2.0.2: 可能使用不同属性名
            def get_block_count(blocks_list, attr_names):
                """尝试多个属性名获取块计数"""
                for attr in attr_names:
                    val = getattr(blocks_list, attr, None)
                    if val is not None:
                        return val
                return 0

            result = {
                'DB': get_block_count(blocks_list, ['db_count', 'DBCount', 'db']),
                'OB': get_block_count(blocks_list, ['ob_count', 'OBCount', 'ob']),
                'FC': get_block_count(blocks_list, ['fc_count', 'FCCount', 'fc']),
                'FB': get_block_count(blocks_list, ['fb_count', 'FBCount', 'fb']),
                'SDB': get_block_count(blocks_list, ['sdb_count', 'SDBCount', 'sdb']),
                'SFC': get_block_count(blocks_list, ['sfc_count', 'SFCCount', 'sfc']),
                'SFB': get_block_count(blocks_list, ['sfb_count', 'SFBCount', 'sfb']),
            }

            add_log('INFO', f'S7 块列表: client_id={client_id}, DB={result["DB"]}, FC={result["FC"]}')

            return jsonify({
                'success': True,
                'blocks': result
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'获取S7块列表失败: {error_msg}')
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/upload_block', methods=['POST'])
def s7_client_upload_block():
    """上传块到S7 PLC (PC → PLC)

    注意：在snap7中，download()是将块写入PLC

    警告：上传块需要有效的S7块格式数据，普通字节数据无法写入。
    建议先使用下载块功能获取有效格式的块数据，修改后再上传。
    """
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        block_type = data.get('block_type', 'db')  # db, ob, fc, fb, sdb, sfc, sfb
        block_number = data.get('block_number', 1)
        # 兼容两个字段名：block_data(base64编码) 或 data(字节数组)
        block_data_b64 = data.get('block_data', '')
        block_data_array = data.get('data', [])

        # 处理数据：支持base64或字节数组
        if block_data_b64:
            import base64
            block_bytes = base64.b64decode(block_data_b64)
            block_bytearray = bytearray(block_bytes)
        elif block_data_array:
            # 前端发送的是字节数组
            block_bytearray = bytearray(block_data_array)
        else:
            return jsonify({
                'success': False,
                'error': '缺少块数据。注意：上传块需要有效的S7块格式数据，建议先下载块获取有效格式。'
            }), 400

        # 检查数据长度（S7块最小需要几十字节的头部）
        if len(block_bytearray) < 32:
            return jsonify({
                'success': False,
                'error': f'块数据太小({len(block_bytearray)}字节)，有效的S7块需要包含头部信息。建议先下载块获取有效格式。'
            }), 400

        # 块类型映射（兼容snap7 1.3和2.0.2）
        # 2.0.2: Block.DB, Block.FB 等（大写）
        # 1.3: 可能使用不同的导入方式
        try:
            from snap7.type import Block
            block_type_map = {
                'db': Block.DB,
                'ob': Block.OB,
                'fc': Block.FC,
                'fb': Block.FB,
                'sdb': Block.SDB,
                'sfc': Block.SFC,
                'sfb': Block.SFB
            }
        except ImportError:
            # 1.3 兼容：使用数值常量
            block_type_map = {
                'db': 0x41,   # 'A'
                'ob': 0x38,   # '8'
                'fc': 0x43,   # 'C'
                'fb': 0x45,   # 'E'
                'sdb': 0x42,  # 'B'
                'sfc': 0x44,  # 'D'
                'sfb': 0x46   # 'F'
            }

        block_type_enum = block_type_map.get(block_type.lower(), block_type_map['db'])

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            # 下载块到PLC（snap7术语：download = 写入PLC）
            # 参数顺序：data在前，block_num在后
            client.download(block_bytearray, block_number)

            add_log('INFO', f'S7 上传块: client_id={client_id}, type={block_type}, number={block_number}, size={len(block_bytearray)}')

            return jsonify({
                'success': True,
                'message': f'块{block_number}上传成功',
                'block_type': block_type,
                'block_number': block_number,
                'size': len(block_bytearray)
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 上传块失败: {error_msg}')
        # 提供更友好的错误信息
        if 'Invalid block size' in error_msg:
            return jsonify({'success': False, 'error': '块格式无效：数据大小或格式不符合S7块规范。建议先下载块获取有效格式。'}), 500
        elif 'protection' in error_msg.lower() or 'not authorized' in error_msg.lower():
            return jsonify({'success': False, 'error': 'S7模拟器不支持块上传功能，需连接真实PLC。'}), 500
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_client/download_block', methods=['POST'])
def s7_client_download_block():
    """从S7 PLC下载块 (PLC → PC)

    注意：在snap7中，upload()是从PLC读取块
    """
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7不可用'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        block_number = data.get('block_number', 1)

        with s7_client_lock:
            if client_id not in s7_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = s7_clients[client_id]
            client = client_info.get('client')

            if not client or not client_info.get('connected'):
                return jsonify({'success': False, 'error': '客户端连接已断开'}), 400

            # 从PLC上传块（snap7术语：upload = 从PLC读取）
            block_data = client.upload(block_number)

            # 编码为base64
            import base64
            block_base64 = base64.b64encode(bytes(block_data)).decode('utf-8')

            add_log('INFO', f'S7 下载块: client_id={client_id}, number={block_number}, size={len(block_data)}')

            return jsonify({
                'success': True,
                'message': f'块{block_number}下载成功',
                'block_number': block_number,
                'size': len(block_data),
                'data': block_base64
            })

    except Exception as e:
        error_msg = str(e)
        add_log('ERROR', f'S7 下载块失败: {error_msg}')
        # 提供更友好的错误信息
        if 'protection' in error_msg.lower() or 'not authorized' in error_msg.lower():
            return jsonify({'success': False, 'error': 'S7模拟器不支持块下载功能，需连接真实PLC。'}), 500
        return jsonify({'success': False, 'error': error_msg}), 500


@app.route('/api/industrial_protocol/s7_server/start', methods=['POST'])
def s7_server_start():
    """启动S7服务端"""
    if not SNAP7_AVAILABLE:
        error_msg = 'python-snap7未安装或导入失败'
        add_log('ERROR', error_msg)
        return jsonify({'success': False, 'error': error_msg}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = data.get('port', 102)  # S7默认端口102
        
        with s7_server_lock:
            # 停止旧服务端
            if server_id in s7_servers:
                old_server_info = s7_servers[server_id]
                old_server_info['running'] = False
                
                server = old_server_info.get('server')
                if server:
                    try:
                        server.stop()
                        server.destroy()
                        add_log('INFO', f'停止旧S7服务器')
                    except Exception as e:
                        add_log('WARNING', f'停止旧S7服务器时出错: {e}')
                
                # 等待旧线程退出
                if 'thread' in old_server_info:
                    old_thread = old_server_info['thread']
                    if old_thread.is_alive():
                        old_thread.join(timeout=2)
                
                del s7_servers[server_id]
            
            # 创建新的S7服务器
            try:
                server = Snap7Server()
                
                # 初始化数据存储（DB块、M区等）
                if server_id not in s7_data_storage:
                    s7_data_storage[server_id] = {
                        'db': {},  # 数据块存储，格式: {db_number: bytearray}
                        'm': bytearray(S7_DB_MAX_SIZE),  # M区存储
                        'i': bytearray(S7_DB_MAX_SIZE),  # 输入区存储
                        'q': bytearray(S7_DB_MAX_SIZE),  # 输出区存储
                    }
                
                # 确保DB1、DB2、DB3已初始化（维护三组独立数据）
                # 优先从数据库加载，如果不存在则使用默认值
                storage = s7_data_storage[server_id]
                for db_num in [1, 2, 3]:
                    if db_num not in storage['db']:
                        # 尝试从数据库加载
                        db_data = load_s7_db_from_database(server_id, db_num)
                        if db_data is not None:
                            storage['db'][db_num] = db_data
                            add_log('INFO', f'S7服务端从数据库加载DB{db_num}数据存储（{len(db_data)}字节）')
                        else:
                            # 初始化DB为不同的值：DB1全1，DB2全2，DB3全3
                            storage['db'][db_num] = bytearray([db_num] * S7_DB_MAX_SIZE)
                            add_log('INFO', f'S7服务端初始化DB{db_num}数据存储（{S7_DB_MAX_SIZE}字节，初始值={db_num}）')
                            # 保存默认值到数据库
                            save_s7_db_to_database(server_id, db_num, storage['db'][db_num])
                    else:
                        add_log('DEBUG', f'S7服务端DB{db_num}数据存储已存在（{len(storage["db"][db_num])}字节）')
                
                # 打印所有DB的初始值（前160字节）
                add_log('INFO', '=' * 70)
                add_log('INFO', 'S7服务端DB块初始化完成，数据预览（前160字节）：')
                for db_num in [1, 2, 3]:
                    if db_num in storage['db']:
                        db_data = storage['db'][db_num]
                        preview_data = db_data[:160]  # 前160字节
                        # 格式化为十六进制，每行16字节
                        hex_lines = []
                        for i in range(0, len(preview_data), 16):
                            hex_bytes = ' '.join([f'{b:02X}' for b in preview_data[i:i+16]])
                            hex_lines.append(f'  {i:04X}-{min(i+15, len(preview_data)-1):04X}: {hex_bytes}')
                        add_log('INFO', f'DB{db_num}（前160字节）：')
                        for line in hex_lines:
                            add_log('INFO', line)
                add_log('INFO', '=' * 70)

                # 设置保护级别为0（无保护，允许上传/下载块）
                # 必须在服务器启动前设置
                try:
                    if hasattr(server, 'set_protection_level'):
                        server.set_protection_level(0)
                        add_log('INFO', 'S7服务端保护级别设置为0（无保护，允许上传/下载块）')
                    else:
                        add_log('WARNING', 'S7服务端不支持set_protection_level方法')
                except Exception as e:
                    add_log('WARNING', f'设置保护级别失败: {e}')

                # 设置数据回调函数
                # 获取SrvArea枚举（用于区域标识）- 服务端使用
                # 注意：服务端回调收到的area值可能是协议值(0x84)或枚举值(5)，需要都支持
                area_enum = snap7_srv_area if snap7_srv_area is not None else (snap7.SrvArea if hasattr(snap7, 'SrvArea') else None)

                def read_callback(area, db_number, start, size):
                    """读取数据回调 - 打印请求和响应，并同步数据到ctypes缓冲区"""
                    add_log('DEBUG', f'[read_callback] 函数被调用: area=0x{area:02X}({area}), db_number={db_number}, start={start}, size={size}')
                    storage = s7_data_storage[server_id]
                    try:
                        # 打印请求信息（包括区域码的十六进制和十进制）
                        area_name = "DB" if (area_enum and area == area_enum.DB) else f"area=0x{area:02X}({area})"
                        add_log('INFO', f'[S7读取请求] {area_name}, DB块号:{db_number}, 起始地址:{start}, 长度:{size}')

                        # 关键修复：area可能是协议值132(0x84)或枚举值5，都需要匹配DB区域
                        # 服务端注册时使用枚举值5，但客户端发送的可能是协议值0x84
                        is_db_area = False
                        if area_enum:
                            # 检查是否是DB区域（枚举值5或协议值132/0x84）
                            enum_db_value = area_enum.DB.value if hasattr(area_enum.DB, 'value') else int(area_enum.DB)
                            if area == area_enum.DB or area == enum_db_value or area == 132 or area == 0x84 or area == 5:
                                is_db_area = True
                        else:
                            # 降级：直接使用整数比较（支持枚举值5和协议值0x84）
                            if area == 5 or area == 132 or area == 0x84:
                                is_db_area = True
                        
                        if is_db_area:
                            if db_number not in storage['db']:
                                storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)  # 默认32KB
                                add_log('WARNING', f'[S7读取] DB{db_number}不存在，已创建{S7_DB_MAX_SIZE}字节缓冲区')
                            db_data = storage['db'][db_number]
                            
                            # 检查地址范围
                            if start + size > len(db_data):
                                add_log('WARNING', f'[S7读取] 地址越界: start={start}, size={size}, 缓冲区大小={len(db_data)}')
                                return bytearray(size)
                            
                            result = db_data[start:start+size]
                            
                            # 打印响应数据（格式化显示）
                            hex_data = ' '.join([f'{b:02X}' for b in result])
                            dec_data = list(result)
                            add_log('INFO', f'[S7读取响应] DB{db_number}, 返回{len(result)}字节')
                            add_log('INFO', f'  十六进制: {hex_data}')
                            add_log('INFO', f'  十进制: {dec_data}')
                            
                            # 关键：确保ctypes缓冲区中的数据是最新的（snap7会从缓冲区读取）
                            with s7_server_lock:
                                server_info = s7_servers.get(server_id)
                                if server_info and 'ctypes_buffers' in server_info and db_number in server_info['ctypes_buffers']:
                                    c_buffer = server_info['ctypes_buffers'][db_number]
                                    if start + size <= len(c_buffer):
                                        for i in range(size):
                                            c_buffer[start + i] = db_data[start + i]
                                        add_log('DEBUG', f'已同步DB{db_number}数据到ctypes缓冲区（地址{start}，长度{size}）')
                            
                            return result
                        
                        # 处理其他区域类型（MK, PE, PA）
                        if area_enum:
                            if area == area_enum.MK:
                                if start + size <= len(storage['m']):
                                    return storage['m'][start:start+size]
                                else:
                                    return bytearray(size)
                            elif area == area_enum.PE:
                                if start + size <= len(storage['i']):
                                    return storage['i'][start:start+size]
                                else:
                                    return bytearray(size)
                            elif area == area_enum.PA:
                                if start + size <= len(storage['q']):
                                    return storage['q'][start:start+size]
                                else:
                                    return bytearray(size)
                        else:
                            # 降级：使用整数比较（SrvArea枚举值）
                            # DB=132(0x84), MK=131(0x83), PE=129(0x81), PA=130(0x82)
                            if area == 132 or area == 0x84:  # DB
                                if db_number not in storage['db']:
                                    storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                                db_data = storage['db'][db_number]
                                if start + size <= len(db_data):
                                    return db_data[start:start+size]
                                else:
                                    return bytearray(size)
                            elif area == 131 or area == 0x83:  # MK
                                if start + size <= len(storage['m']):
                                    return storage['m'][start:start+size]
                                else:
                                    return bytearray(size)
                            elif area == 129 or area == 0x81:  # PE
                                if start + size <= len(storage['i']):
                                    return storage['i'][start:start+size]
                                else:
                                    return bytearray(size)
                            elif area == 130 or area == 0x82:  # PA
                                if start + size <= len(storage['q']):
                                    return storage['q'][start:start+size]
                                else:
                                    return bytearray(size)
                        return bytearray(size)
                    except Exception as e:
                        add_log('ERROR', f'[S7读取回调异常] area=0x{area:02X}({area}), db_number={db_number}, start={start}, size={size}: {e}')
                        import traceback
                        add_log('DEBUG', f'[S7读取回调异常详情] {traceback.format_exc()}')
                        return bytearray(size) if size > 0 else bytearray()
                
                def write_callback(area, db_number, start, size, data):
                    """写入数据回调 - 打印请求和响应"""
                    storage = s7_data_storage[server_id]
                    try:
                        # 打印请求信息
                        area_name = "DB" if (area_enum and area == area_enum.DB) else f"area={area}"
                        data_hex = ' '.join([f'{b:02X}' for b in data])
                        data_dec = list(data)
                        add_log('INFO', f'[S7写入请求] {area_name}, DB块号:{db_number}, 起始地址:{start}, 长度:{size}')
                        add_log('INFO', f'  写入数据(hex): {data_hex}')
                        add_log('INFO', f'  写入数据(dec): {data_dec}')
                        
                        if area_enum:
                            if area == area_enum.DB:
                                if db_number not in storage['db']:
                                    storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                                    add_log('DEBUG', f'[S7写入] DB{db_number}不存在，已创建{S7_DB_MAX_SIZE}字节缓冲区')
                                db_data = storage['db'][db_number]
                                
                                # 检查地址范围
                                if start + size > len(db_data):
                                    add_log('WARNING', f'[S7写入] 地址越界: start={start}, size={size}, 缓冲区大小={len(db_data)}')
                                    return
                                
                                db_data[start:start+size] = data
                                
                                # 保存到数据库
                                save_s7_db_to_database(server_id, db_number, db_data)
                                
                                # 同步数据到ctypes缓冲区（使用独立缓冲区，需要手动同步）
                                with s7_server_lock:
                                    sync_s7_data_to_server(server_id, db_number)
                                
                                # 验证写入结果
                                verify_data = db_data[start:start+size]
                                verify_hex = ' '.join([f'{b:02X}' for b in verify_data])
                                add_log('INFO', f'[S7写入响应] DB{db_number}, 写入成功, 地址范围: {start}~{start+size-1}')
                                add_log('INFO', f'  验证数据(hex): {verify_hex}')
                            elif area == area_enum.MK:
                                if start + size <= len(storage['m']):
                                    storage['m'][start:start+size] = data
                            elif area == area_enum.PE:
                                if start + size <= len(storage['i']):
                                    storage['i'][start:start+size] = data
                            elif area == area_enum.PA:
                                if start + size <= len(storage['q']):
                                    storage['q'][start:start+size] = data
                        else:
                            # 降级：使用整数比较
                            if area == 132 or area == 0x84:  # DB
                                if db_number not in storage['db']:
                                    storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                                db_data = storage['db'][db_number]
                                if start + size <= len(db_data):
                                    db_data[start:start+size] = data
                                    # 保存到数据库
                                    save_s7_db_to_database(server_id, db_number, db_data)
                                    # 同步数据到ctypes缓冲区
                                    with s7_server_lock:
                                        sync_s7_data_to_server(server_id, db_number)
                            elif area == 131 or area == 0x83:  # MK
                                if start + size <= len(storage['m']):
                                    storage['m'][start:start+size] = data
                            elif area == 129 or area == 0x81:  # PE
                                if start + size <= len(storage['i']):
                                    storage['i'][start:start+size] = data
                            elif area == 130 or area == 0x82:  # PA
                                if start + size <= len(storage['q']):
                                    storage['q'][start:start+size] = data
                    except Exception as e:
                        add_log('ERROR', f'S7写入回调异常: {e}')
                
                # 注册回调函数（python-snap7 2.0.2可能不支持回调，使用事件机制）
                callback_registered = False
                try:
                    # 尝试多种回调注册方法
                    if hasattr(server, 'set_read_callback') and hasattr(server, 'set_write_callback'):
                        server.set_read_callback(read_callback)
                        server.set_write_callback(write_callback)
                        callback_registered = True
                        add_log('INFO', 'S7服务器回调函数注册成功 (set_read_callback/set_write_callback)')
                    elif hasattr(server, 'set_events_callback'):
                        # python-snap7 2.0.2的set_events_callback接收一个回调函数，回调函数接收event对象
                        try:
                            # 修复：回调函数应该接收event对象，而不是多个参数
                            def events_callback(event):
                                """统一事件回调函数，接收event对象"""
                                try:
                                    evt_code = getattr(event, 'EvtCode', 0)
                                    evt_param1 = getattr(event, 'EvtParam1', 0)  # area
                                    evt_param2 = getattr(event, 'EvtParam2', 0)  # DB块号
                                    evt_param3 = getattr(event, 'EvtParam3', 0)  # start
                                    evt_param4 = getattr(event, 'EvtParam4', 0)  # size
                                    
                                    # 解析区域码
                                    area = evt_param1
                                    db_number = evt_param2
                                    start = evt_param3
                                    size = evt_param4
                                    
                                    # 0x20000 = 读请求, 0x40000 = 写请求
                                    if evt_code == 0x20000:  # 读请求
                                        add_log('INFO', f'[events_callback] 收到读请求: area=0x{area:02X}({area}), db_number={db_number}, start={start}, size={size}')
                                        # 关键：在回调函数中，先同步数据到ctypes缓冲区
                                        # 因为snap7会直接从缓冲区读取，而不是使用返回值
                                        # 注意：sync_s7_data_to_server内部已经持有锁，这里直接调用即可
                                        add_log('DEBUG', f'[events_callback] 开始同步数据到ctypes缓冲区...')
                                        try:
                                            # 直接调用sync_s7_data_to_server（它内部会处理锁）
                                            sync_s7_data_to_server(server_id, db_number)
                                            add_log('DEBUG', f'[events_callback] 数据同步完成')
                                        except Exception as sync_err:
                                            add_log('ERROR', f'[events_callback] 数据同步异常: {sync_err}')
                                            import traceback
                                            add_log('DEBUG', f'[events_callback] 数据同步异常详情: {traceback.format_exc()}')
                                        
                                        # 调用read_callback处理并打印日志
                                        add_log('DEBUG', f'[events_callback] 准备调用read_callback...')
                                        try:
                                            result = read_callback(area, db_number, start, size)
                                            # 注意：返回值可能不被使用，数据必须从缓冲区读取
                                            add_log('DEBUG', f'[events_callback] read_callback返回: {len(result) if result else 0}字节')
                                            return result
                                        except Exception as read_err:
                                            add_log('ERROR', f'[events_callback] read_callback异常: {read_err}')
                                            import traceback
                                            add_log('DEBUG', f'[events_callback] read_callback异常详情: {traceback.format_exc()}')
                                            # 返回空数据，让snap7从缓冲区读取
                                            return bytearray(size) if size > 0 else bytearray()
                                    elif evt_code == 0x40000:  # 写请求
                                        # 写请求需要读取数据
                                        data = None
                                        if hasattr(event, 'pdata') and event.pdata:
                                            try:
                                                import ctypes
                                                data_ptr = ctypes.cast(event.pdata, ctypes.POINTER(ctypes.c_ubyte * size))
                                                data = bytearray(data_ptr.contents)
                                            except:
                                                pass
                                        write_callback(area, db_number, start, size, data)
                                except Exception as e:
                                    add_log('ERROR', f'事件回调处理异常: {e}')
                            
                            server.set_events_callback(events_callback)
                            callback_registered = True
                            add_log('INFO', 'S7服务器回调函数注册成功 (使用set_events_callback)')
                        except Exception as e:
                            add_log('DEBUG', f'set_events_callback注册失败: {e}，将使用pick_event事件机制')
                    elif hasattr(server, 'set_read_events_callback'):
                        # 某些版本可能使用set_read_events_callback
                        try:
                            server.set_read_events_callback(read_callback)
                            if hasattr(server, 'set_write_events_callback'):
                                server.set_write_events_callback(write_callback)
                            callback_registered = True
                            add_log('INFO', 'S7服务器回调函数注册成功 (使用set_read_events_callback)')
                        except Exception as e:
                            add_log('DEBUG', f'set_read_events_callback注册失败: {e}，将使用pick_event事件机制')
                    elif hasattr(server, 'set_callback'):
                        # 某些版本可能使用 set_callback
                        server.set_callback(read_callback, write_callback)
                        callback_registered = True
                        add_log('INFO', 'S7服务器回调函数注册成功 (使用set_callback)')
                    elif hasattr(server, 'set_area_callback'):
                        # 某些版本可能使用 set_area_callback
                        server.set_area_callback(read_callback, write_callback)
                        callback_registered = True
                        add_log('INFO', 'S7服务器回调函数注册成功 (使用set_area_callback)')
                    else:
                        # 检查是否有其他回调相关方法
                        callback_methods = [m for m in dir(server) if 'callback' in m.lower() and not m.startswith('_')]
                        add_log('DEBUG', f'Server回调相关方法: {callback_methods}')
                        add_log('INFO', 'S7服务器将使用pick_event事件机制捕获请求（回调未注册）')
                except AttributeError as e:
                    add_log('INFO', f'S7服务器回调注册失败: {e}，将使用pick_event事件机制')
                except Exception as e:
                    add_log('INFO', f'S7服务器回调注册异常: {e}，将使用pick_event事件机制')
                    import traceback
                    add_log('DEBUG', f'回调注册详细错误: {traceback.format_exc()}')
                
                # 在后台线程中启动服务器
                server_running = {'value': True}
                server_error = [None]
                
                def run_s7_server():
                    try:
                        # 修正Server启动流程（先设置端口，再启动）
                        try:
                            # 方法1: 先设置端口，再无参数启动（新版本推荐）
                            if hasattr(server, 'set_socket_params'):
                                server.set_socket_params(port=port)
                                server.start()  # 无参数启动
                                add_log('INFO', f'S7服务端启动成功: 0.0.0.0:{port}, server_id={server_id} [set_socket_params+start]')
                            # 方法2: start(tcp_port=...) (某些版本)
                            elif hasattr(server, 'start'):
                                try:
                                    server.start(tcp_port=port)
                                    add_log('INFO', f'S7服务端启动成功: 0.0.0.0:{port}, server_id={server_id} [start(tcp_port)]')
                                except (AttributeError, TypeError):
                                    # 方法3: start() 无参数（新版本）
                                    server.start()
                                    add_log('INFO', f'S7服务端启动成功: 0.0.0.0:{port}, server_id={server_id} [start()]')
                            # 方法4: start(host, port) (旧版本)
                            elif hasattr(server, 'start'):
                                server.start(host, port)
                                add_log('INFO', f'S7服务端启动成功: {host}:{port}, server_id={server_id} [start(host,port)]')
                            # 方法5: start_to (某些版本)
                            elif hasattr(server, 'start_to'):
                                server.start_to(host, port)
                                add_log('INFO', f'S7服务端启动成功: {host}:{port}, server_id={server_id} [start_to]')
                            # 方法6: listen (某些版本)
                            elif hasattr(server, 'listen'):
                                server.listen(host, port)
                                add_log('INFO', f'S7服务端启动成功: {host}:{port}, server_id={server_id} [listen]')
                            else:
                                raise AttributeError('无法找到S7服务器启动方法')
                        except Exception as e:
                            raise Exception(f'S7服务器启动失败: {e}')
                        
                        time.sleep(0.5)

                        # 关键：用正确的区域码0x84注册DB（必须在服务器启动后注册）
                        add_log('INFO', '=== 开始注册DB区域（snap7 2.0.2） ===')
                        register_s7_areas(server_id, db_list=[1, 2, 3])
                        sync_s7_data_to_server(server_id)
                        
                        # 验证区域注册是否成功
                        with s7_server_lock:
                            server_info = s7_servers.get(server_id)
                            if server_info and 'ctypes_buffers' in server_info:
                                registered_dbs = list(server_info['ctypes_buffers'].keys())
                                add_log('INFO', f'已注册的DB块: {registered_dbs}')
                                if not registered_dbs:
                                    add_log('WARNING', '警告：没有成功注册任何DB块，客户端可能无法读取数据')
                        add_log('INFO', '=== DB区域注册完成 ===')
                        
                        # 修复事件循环：确保捕获所有客户端请求
                        add_log('INFO', '开始事件监听（snap7 2.0.2），将打印所有客户端请求')
                        last_sync_time = time.time()  # 初始化last_sync_time变量
                        while True:
                            with s7_server_lock:
                                if not server_running['value']:
                                    break
                            
                            # 关键修复：循环读取所有待处理事件
                            try:
                                # 连续读取事件，直到没有新事件（避免遗漏）
                                while True:
                                    event = server.pick_event()
                                    if not event:
                                        break
                                    
                                    # 解析事件（snap7 2.0.2事件结构）
                                    evt_code = getattr(event, 'EvtCode', 0)
                                    evt_param1 = getattr(event, 'EvtParam1', 0)
                                    evt_param2 = getattr(event, 'EvtParam2', 0)  # DB块号
                                    evt_param3 = getattr(event, 'EvtParam3', 0)  # 起始地址
                                    evt_param4 = getattr(event, 'EvtParam4', 0)  # 长度
                                    evt_retcode = getattr(event, 'EvtRetCode', 0)
                                    
                                    # 打印所有事件（包括客户端连接/请求）
                                    event_type_desc = {
                                        0x80000: "客户端连接",
                                        0x08: "客户端断开",
                                        0x20000: "读请求",
                                        0x40000: "写请求",
                                        0x100000: "参数协商",
                                        0x01: "旧版读请求"
                                    }
                                    evt_desc = event_type_desc.get(evt_code, f"未知事件(0x{evt_code:05X})")
                                    
                                    # 打印核心事件
                                    add_log('INFO', '=' * 80)
                                    add_log('INFO', f'捕获S7事件 (snap7 2.0.2)')
                                    add_log('INFO', f'   事件类型: {evt_desc} (0x{evt_code:05X})')
                                    add_log('INFO', f'   参数: DB块={evt_param2}, 地址={evt_param3}, 长度={evt_param4}')
                                    add_log('INFO', f'   错误码: 0x{evt_retcode:02X} ({evt_retcode})')
                                    
                                    # 处理读请求（0x20000）- 参考工作代码
                                    if evt_code == 0x20000:
                                        db_num = evt_param2  # 实际=DB块号
                                        start_addr = evt_param3  # 实际=起始地址
                                        length = evt_param4  # 实际=长度
                                        area_code = evt_param1  # 区域码
                                        
                                        add_log('INFO', f'[pick_event] 客户端读取请求: area=0x{area_code:02X}({area_code}), DB{db_num}, 地址{start_addr}, 长度{length}, 错误码=0x{evt_retcode:02X}')
                                        
                                        # 参考工作代码：仅处理读请求 + 合法DB块 + 无错误
                                        is_valid_db = (db_num in [1, 2, 3])
                                        is_success = (evt_retcode == 0)
                                        
                                        if is_valid_db and is_success:
                                            # 确保数据已同步到ctypes缓冲区
                                            with s7_server_lock:
                                                server_info = s7_servers.get(server_id)
                                                storage = s7_data_storage.get(server_id)
                                                if storage and db_num in storage.get('db', {}):
                                                    db_data = storage['db'][db_num]
                                                    # 同步数据到ctypes缓冲区
                                                    if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                        c_buffer = server_info['ctypes_buffers'][db_num]
                                                        sync_size = min(len(c_buffer), len(db_data))
                                                        for i in range(sync_size):
                                                            c_buffer[i] = db_data[i]
                                            
                                            # 校验地址范围
                                            if start_addr + length > S7_DB_MAX_SIZE:
                                                add_log('WARNING', f'读取越界: DB{db_num}, 地址:{start_addr}, 长度:{length}（最大:{S7_DB_MAX_SIZE}）')
                                            else:
                                                # 提取返回数据
                                                with s7_server_lock:
                                                    server_info = s7_servers.get(server_id)
                                                    if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                        c_buffer = server_info['ctypes_buffers'][db_num]
                                                        if start_addr + length <= len(c_buffer):
                                                            response_data = bytes(c_buffer[start_addr:start_addr+length])
                                                            hex_data = ' '.join([f'{b:02X}' for b in response_data])
                                                            dec_data = list(response_data)
                                                            
                                                            # 打印读取请求详情（参考工作代码格式）
                                                            add_log('INFO', '=' * 70)
                                                            add_log('INFO', f'📢 捕获客户端读取请求（python-snap7 2.0.2）')
                                                            add_log('INFO', f'   ├─ DB块号     : DB{db_num}')
                                                            add_log('INFO', f'   ├─ 起始地址    : {start_addr} 字节')
                                                            add_log('INFO', f'   ├─ 读取长度    : {length} 字节')
                                                            add_log('INFO', f'   ├─ 返回数据(16进制) : {hex_data}')
                                                            add_log('INFO', f'   └─ 返回数据(10进制) : {dec_data}')
                                                            add_log('INFO', '=' * 70)
                                        elif is_valid_db and evt_retcode != 0:
                                            error_name = "address out of range" if evt_retcode == 0x08 else ("Invalid address" if evt_retcode == 0x05 else f"错误码0x{evt_retcode:02X}")
                                            add_log('ERROR', f'读取失败: {error_name} (错误码0x{evt_retcode:02X})')
                                            add_log('ERROR', f'  原因：DB{db_num}未正确注册或地址超出范围')
                                            add_log('ERROR', f'  区域码: 客户端发送0x{area_code:02X}，服务器注册的是枚举值0x05')
                                            
                                            # 尝试手动同步数据到缓冲区
                                            with s7_server_lock:
                                                server_info = s7_servers.get(server_id)
                                                storage = s7_data_storage.get(server_id)
                                                if storage and db_num in storage.get('db', {}):
                                                    db_data = storage['db'][db_num]
                                                    if start_addr + length <= len(db_data):
                                                        if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                            c_buffer = server_info['ctypes_buffers'][db_num]
                                                            for i in range(length):
                                                                if start_addr + i < len(db_data) and start_addr + i < len(c_buffer):
                                                                    c_buffer[start_addr + i] = db_data[start_addr + i]
                                                            add_log('INFO', f'已手动同步DB{db_num}数据到缓冲区（地址{start_addr}，长度{length}）')
                                    
                                    # 处理写请求（0x40000）
                                    elif evt_code == 0x40000:
                                        db_num = evt_param2
                                        start_addr = evt_param3
                                        length = evt_param4
                                        add_log('INFO', f'客户端写入请求: DB{db_num}, 地址{start_addr}, 长度{length}')

                                        # 从缓冲区获取写入的数据并持久化到存储
                                        with s7_server_lock:
                                            server_info = s7_servers.get(server_id)
                                            storage = s7_data_storage.get(server_id)
                                            if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                c_buffer = server_info['ctypes_buffers'][db_num]
                                                if start_addr + length <= len(c_buffer):
                                                    written_data = bytes(c_buffer[start_addr:start_addr+length])
                                                    hex_data = ' '.join([f'{b:02X}' for b in written_data])
                                                    add_log('INFO', f'写入数据: {hex_data}')
                                                    add_log('INFO', f'  十进制: {list(written_data)}')

                                            # 关键修复：将ctypes缓冲区的数据同步到s7_data_storage
                                            if storage and db_num in storage.get('db', {}):
                                                db_data = storage['db'][db_num]
                                                if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                    c_buffer = server_info['ctypes_buffers'][db_num]
                                                    # 从ctypes缓冲区复制到存储
                                                    if start_addr + length <= len(db_data) and start_addr + length <= len(c_buffer):
                                                        for i in range(length):
                                                            db_data[start_addr + i] = c_buffer[start_addr + i]
                                                        add_log('INFO', f'已将写入数据同步到存储: DB{db_num}, 地址{start_addr}, 长度{length}')
                                                        # 保存到数据库
                                                        save_s7_db_to_database(server_id, db_num, db_data)
                                                        add_log('INFO', f'已保存DB{db_num}数据到数据库')
                                            elif storage and db_num not in storage.get('db', {}):
                                                # 如果DB不存在，创建并从ctypes缓冲区复制数据
                                                storage['db'][db_num] = bytearray(S7_DB_MAX_SIZE)
                                                if server_info and 'ctypes_buffers' in server_info and db_num in server_info['ctypes_buffers']:
                                                    c_buffer = server_info['ctypes_buffers'][db_num]
                                                    sync_size = min(len(c_buffer), len(storage['db'][db_num]))
                                                    for i in range(sync_size):
                                                        storage['db'][db_num][i] = c_buffer[i]
                                                    save_s7_db_to_database(server_id, db_num, storage['db'][db_num])
                                                    add_log('INFO', f'已创建DB{db_num}并同步数据到存储和数据库')
                                    
                                    # 处理连接事件
                                    elif evt_code == 0x80000:
                                        add_log('INFO', f'客户端已连接 (参数1: {evt_param1})')
                                    elif evt_code == 0x08:
                                        add_log('INFO', f'客户端已断开 (参数1: {evt_param1})')
                                    
                                    add_log('INFO', '=' * 80)
                            except Exception as e:
                                # 记录事件处理错误，但不中断服务
                                add_log('WARNING', f'S7事件处理异常: {e}')
                                import traceback
                                add_log('DEBUG', f'详细错误: {traceback.format_exc()}')
                            
                            # 参考工作代码：事件扫描间隔
                            time.sleep(0.1)  # SCAN_INTERVAL = 0.1秒
                            
                            # 定期同步数据（如果回调未注册）
                            if not callback_registered and (time.time() - last_sync_time) >= 0.5:
                                with s7_server_lock:
                                    sync_s7_data_to_server(server_id)
                                last_sync_time = time.time()
                        
                        # 尝试不同的停止方法
                        try:
                            server.stop()
                        except AttributeError:
                            try:
                                server.shutdown()
                            except AttributeError:
                                pass
                        
                        try:
                            server.destroy()
                        except AttributeError:
                            pass
                        
                        add_log('INFO', f'S7服务端已停止: {host}:{port}, server_id={server_id}')
                    except Exception as e:
                        server_error[0] = str(e)
                        add_log('ERROR', f'S7服务端运行异常: {e}')
                        server_running['value'] = False
                
                server_thread = threading.Thread(target=run_s7_server, daemon=True)
                server_thread.start()
                
                # 等待服务器启动
                time.sleep(0.5)
                
                if server_error[0]:
                    return jsonify({'success': False, 'error': f'启动失败: {server_error[0]}'}), 500
                
                # 保存服务器信息
                s7_servers[server_id] = {
                    'server': server,
                    'thread': server_thread,
                    'host': host,
                    'port': port,
                    'running': server_running,  # 使用共享的字典引用
                    'start_time': datetime.now().isoformat(),
                    'callback_registered': callback_registered,
                    'area_enum': area_enum
                }
                
                add_log('INFO', f'S7服务端启动成功: {host}:{port} (server_id={server_id})')
                return jsonify({
                    'success': True,
                    'message': 'S7服务端启动成功',
                    'data_reset': True,  # 标志：数据已重置
                    'host': host,
                    'port': port
                })
                
            except Exception as e:
                add_log('ERROR', f'S7服务端创建异常: {str(e)}')
                import traceback
                error_detail = traceback.format_exc()
                add_log('ERROR', f'详细错误信息: {error_detail}')
                return jsonify({'success': False, 'error': f'启动失败: {str(e)}'}), 500
                
    except Exception as e:
        add_log('ERROR', f'S7服务端启动异常: {str(e)}')
        import traceback
        error_detail = traceback.format_exc()
        add_log('ERROR', f'详细错误信息: {error_detail}')
        return jsonify({'success': False, 'error': f'启动失败: {str(e)}'}), 500


@app.route('/api/industrial_protocol/s7_server/stop', methods=['POST'])
def s7_server_stop():
    """停止S7服务端"""
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        
        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            server_info = s7_servers[server_id]
            server = server_info.get('server')
            server_thread = server_info.get('thread')
            server_running = server_info.get('running')
            
            # 设置停止标志（使用共享的字典引用）
            if server_running and isinstance(server_running, dict):
                server_running['value'] = False
            
            if server:
                try:
                    # 尝试不同的停止方法
                    try:
                        server.stop()
                    except AttributeError:
                        try:
                            server.shutdown()
                        except AttributeError:
                            pass
                    
                    try:
                        server.destroy()
                    except AttributeError:
                        pass
                    
                    add_log('INFO', f'S7服务端已停止: server_id={server_id}')
                except Exception as e:
                    add_log('WARNING', f'停止S7服务器时出错: {e}')
            
            # 等待线程退出（使用较短的超时，避免卡住）
            if server_thread and server_thread.is_alive():
                server_thread.join(timeout=1)
                if server_thread.is_alive():
                    add_log('WARNING', f'S7服务器线程未在1秒内退出，但继续执行清理')
            
            del s7_servers[server_id]
            
            return jsonify({'success': True, 'message': 'S7服务端已停止'})
            
    except Exception as e:
        add_log('ERROR', f'S7服务端停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_server/status', methods=['GET'])
def s7_server_status():
    """获取S7服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')
        
        with s7_server_lock:
            if server_id in s7_servers:
                server_info = s7_servers[server_id]
                server_thread = server_info.get('thread')
                
                return jsonify({
                    'success': True,
                    'running': server_info.get('running', False),
                    'host': server_info.get('host', ''),
                    'port': server_info.get('port', 0),
                    'thread_alive': server_thread.is_alive() if server_thread else False,
                    'start_time': server_info.get('start_time', '')
                })
            else:
                return jsonify({'success': True, 'running': False})
                
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_server/get_data', methods=['POST'])
def s7_server_get_data():
    """读取S7服务端数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装'}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        area = data.get('area', 'DB')  # DB, M, I, Q
        db_number = data.get('db_number')
        if db_number is None:
            db_number = 1
        else:
            db_number = int(db_number)
        
        start = data.get('start')
        if start is None:
            start = 0
        else:
            start = int(start)
        
        size = data.get('size')
        if size is None:
            size = 1
        else:
            size = int(size)
        
        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            if server_id not in s7_data_storage:
                return jsonify({'success': False, 'error': '数据存储不存在'}), 404
            
            storage = s7_data_storage[server_id]
            
            try:
                # 打印读取请求
                add_log('INFO', f'[API读取请求] area={area}, db_number={db_number}, start={start}, size={size}')

                if area == 'DB':
                    if db_number not in storage['db']:
                        storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                        add_log('WARNING', f'S7读取: 创建新DB{db_number}数据块')
                    db_data = storage['db'][db_number]
                    if start + size <= len(db_data):
                        data_bytes = db_data[start:start+size]
                    else:
                        data_bytes = bytearray(size)
                        add_log('WARNING', f'S7读取DB{db_number}: 地址范围超出（start={start}, size={size}, 数据长度={len(db_data)}）')
                    # 转换为整数列表
                    values = [int(b) for b in data_bytes]

                    # 打印响应数据（前160字节）
                    preview_len = min(len(values), 160)
                    preview_values = values[:preview_len]
                    preview_hex = ' '.join([f'{b:02X}' for b in data_bytes[:preview_len]])
                    add_log('INFO', f'[API读取响应] DB{db_number}, 返回{len(values)}字节（显示前{preview_len}字节）')
                    add_log('INFO', f'  十六进制: {preview_hex}')
                    add_log('INFO', f'  十进制: {preview_values}')
                elif area == 'M':
                    if start + size <= len(storage['m']):
                        data_bytes = storage['m'][start:start+size]
                    else:
                        data_bytes = bytearray(size)
                    values = [int(b) for b in data_bytes]
                elif area == 'I':
                    if start + size <= len(storage['i']):
                        data_bytes = storage['i'][start:start+size]
                    else:
                        data_bytes = bytearray(size)
                    values = [int(b) for b in data_bytes]
                elif area == 'Q':
                    if start + size <= len(storage['q']):
                        data_bytes = storage['q'][start:start+size]
                    else:
                        data_bytes = bytearray(size)
                    values = [int(b) for b in data_bytes]
                else:
                    return jsonify({'success': False, 'error': f'不支持的区域: {area}'}), 400
                
                return jsonify({
                    'success': True,
                    'data': values,
                    'area': area,
                    'db_number': db_number if area == 'DB' else None,
                    'start': start,
                    'size': size
                })
                
            except Exception as e:
                add_log('ERROR', f'读取S7数据异常: {str(e)}')
                return jsonify({'success': False, 'error': str(e)}), 500
                
    except Exception as e:
        add_log('ERROR', f'读取S7数据请求异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/s7_server/set_data', methods=['POST'])
def s7_server_set_data():
    """设置S7服务端数据"""
    if not SNAP7_AVAILABLE:
        return jsonify({'success': False, 'error': 'python-snap7未安装'}), 500
    
    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        area = data.get('area', 'DB')
        db_number = data.get('db_number')
        if db_number is None:
            db_number = 1
        else:
            db_number = int(db_number)
        
        start = data.get('start')
        if start is None:
            start = 0
        else:
            start = int(start)
        
        values = data.get('values', [])  # 整数列表，每个值0-255
        if not isinstance(values, list):
            return jsonify({'success': False, 'error': 'values必须是列表'}), 400
        
        with s7_server_lock:
            if server_id not in s7_servers:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404
            
            if server_id not in s7_data_storage:
                return jsonify({'success': False, 'error': '数据存储不存在'}), 404
            
            storage = s7_data_storage[server_id]
            
            try:
                # 转换为bytearray
                data_bytes = bytearray([min(255, max(0, int(v))) for v in values])
                size = len(data_bytes)
                
                if area == 'DB':
                    if db_number not in storage['db']:
                        storage['db'][db_number] = bytearray(S7_DB_MAX_SIZE)
                        add_log('WARNING', f'S7写入: 创建新DB{db_number}数据块')
                    db_data = storage['db'][db_number]
                    if start + size <= len(db_data):
                        db_data[start:start+size] = data_bytes
                        # 保存到数据库
                        save_s7_db_to_database(server_id, db_number, db_data)
                        # 打印写入请求和响应
                        data_hex = ' '.join([f'{b:02X}' for b in data_bytes])
                        data_dec = list(data_bytes)
                        add_log('INFO', f'[API写入请求] area={area}, db_number={db_number}, start={start}, size={size}')
                        add_log('INFO', f'  写入数据(hex): {data_hex}')
                        add_log('INFO', f'  写入数据(dec): {data_dec}')
                        # 验证写入结果
                        verify_data = db_data[start:start+size]
                        verify_hex = ' '.join([f'{b:02X}' for b in verify_data])
                        verify_dec = list(verify_data)
                        add_log('INFO', f'[API写入响应] DB{db_number}, 写入成功, 地址范围: {start}~{start+size-1}')
                        add_log('INFO', f'  验证数据(hex): {verify_hex}')
                        add_log('INFO', f'  验证数据(dec): {verify_dec}')
                    else:
                        add_log('WARNING', f'S7写入DB{db_number}: 地址范围超出（start={start}, size={size}, 数据长度={len(db_data)}）')
                elif area == 'M':
                    if start + size <= len(storage['m']):
                        storage['m'][start:start+size] = data_bytes
                elif area == 'I':
                    if start + size <= len(storage['i']):
                        storage['i'][start:start+size] = data_bytes
                elif area == 'Q':
                    if start + size <= len(storage['q']):
                        storage['q'][start:start+size] = data_bytes
                else:
                    return jsonify({'success': False, 'error': f'不支持的区域: {area}'}), 400
                
                # 验证数据是否已正确写入（用于调试）
                verify_success = False
                try:
                    if area == 'DB':
                        if db_number in storage['db']:
                            verify_data = storage['db'][db_number][start:start+size]
                            verify_success = verify_data == data_bytes
                    elif area == 'M':
                        verify_data = storage['m'][start:start+size]
                        verify_success = verify_data == data_bytes
                    elif area == 'I':
                        verify_data = storage['i'][start:start+size]
                        verify_success = verify_data == data_bytes
                    elif area == 'Q':
                        verify_data = storage['q'][start:start+size]
                        verify_success = verify_data == data_bytes
                except Exception as e:
                    add_log('WARNING', f'验证S7数据写入失败: {e}')
                
                # 简化数据同步逻辑（写入后仅同步，不重复注册区域）
                # 注意：使用from_buffer时，数据会自动同步，不需要重新注册区域
                try:
                    server_info = s7_servers.get(server_id)
                    if server_info:
                        if area == 'DB' and db_number is not None:
                            # 仅同步，不重新注册区域（避免破坏已有映射）
                            if not server_info.get('callback_registered', False):
                                sync_s7_data_to_server(server_id, db_number)
                            add_log('DEBUG', f'S7数据写入后已同步DB{db_number}（from_buffer自动同步）')
                        else:
                            # 其他区域同步所有数据
                            if not server_info.get('callback_registered', False):
                                sync_s7_data_to_server(server_id)
                except Exception as e:
                    add_log('WARNING', f'S7数据同步失败: {e}')
                
                return jsonify({
                    'success': True,
                    'message': '设置成功',
                    'data_updated': True,
                    'area': area,
                    'db_number': db_number if area == 'DB' else None,
                    'start': start,
                    'size': size,
                    'values': values,
                    'verify_success': verify_success
                })
                
            except Exception as e:
                add_log('ERROR', f'设置S7数据异常: {str(e)}')
                return jsonify({'success': False, 'error': str(e)}), 500
                
    except Exception as e:
        add_log('ERROR', f'设置S7数据请求异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== ENIP客户端路由 ==========

@app.route('/api/industrial_protocol/enip_client/connect', methods=['POST'])
def enip_client_connect():
    """连接ENIP客户端"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        ip = data.get('ip')
        port = data.get('port', 44818)
        timeout = data.get('timeout', 5)

        if not ip:
            return jsonify({'success': False, 'error': 'IP地址不能为空'}), 400

        with enip_client_lock:
            # 如果已存在连接，先断开
            if client_id in enip_clients:
                try:
                    enip_clients[client_id]['client'].disconnect()
                except:
                    pass
                del enip_clients[client_id]

            # 创建新客户端
            client = EnipClient()
            success, message = client.connect(ip, port, timeout)

            if success:
                enip_clients[client_id] = {
                    'client': client,
                    'ip': ip,
                    'port': port,
                    'connected': True,
                    'connect_time': time.strftime("%Y-%m-%d %H:%M:%S")
                }
                add_log('INFO', f'ENIP客户端连接成功: {ip}:{port}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP客户端连接失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP客户端连接异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/disconnect', methods=['POST'])
def enip_client_disconnect():
    """断开ENIP客户端连接"""
    try:
        data = request.json
        client_id = data.get('client_id', 'default')

        with enip_client_lock:
            if client_id in enip_clients:
                try:
                    enip_clients[client_id]['client'].disconnect()
                except:
                    pass
                del enip_clients[client_id]
                add_log('INFO', f'ENIP客户端断开连接: {client_id}')
                return jsonify({'success': True, 'message': '断开成功'})
            else:
                return jsonify({'success': False, 'error': '连接不存在'}), 404

    except Exception as e:
        add_log('ERROR', f'ENIP客户端断开异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/status', methods=['GET'])
def enip_client_status():
    """获取ENIP客户端连接状态"""
    try:
        client_id = request.args.get('client_id', 'default')

        with enip_client_lock:
            if client_id in enip_clients:
                client_info = enip_clients[client_id]
                status = client_info['client'].status()
                return jsonify({
                    'success': True,
                    'connected': client_info['connected'],
                    'ip': client_info['ip'],
                    'port': client_info['port'],
                    'session_handle': status.get('session_handle', 0),
                    'connect_time': client_info['connect_time']
                })
            else:
                return jsonify({'success': True, 'connected': False})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/read', methods=['POST'])
def enip_client_read():
    """读取ENIP设备属性"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        class_id = data.get('class_id', 0x01)  # 默认Identity对象
        instance = data.get('instance', 1)
        attribute = data.get('attribute', 1)

        add_log('INFO', f'ENIP读取属性请求: client_id={client_id}, class={class_id}, instance={instance}, attribute={attribute}')

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, value, message = client.get_attribute_single(class_id, instance, attribute)
            add_log('INFO', f'ENIP读取属性结果: success={success}, value={value}, message={message}')

            if success:
                # 将bytes转换为hex字符串以便JSON传输
                if isinstance(value, bytes):
                    value_hex = value.hex()
                else:
                    value_hex = str(value)
                add_log('INFO', f'ENIP读取属性成功: class={class_id}, instance={instance}, attribute={attribute}, value={value_hex}')
                decoded = decode_enip_attribute_value(value_hex)
                return jsonify({'success': True, 'data': value_hex, 'decoded': decoded['decoded'], 'message': message})
            else:
                add_log('ERROR', f'ENIP读取属性失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP读取属性异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


def decode_enip_attribute_value(value_hex: str) -> Dict[str, Any]:
    """解码 ENIP 属性值为可读格式"""
    result = {'hex': value_hex, 'decoded': {}}
    if not value_hex:
        return result

    try:
        data = bytes.fromhex(value_hex.replace(' ', ''))
        if len(data) >= 2:
            # 尝试多种类型解码
            result['decoded']['uint16_le'] = struct.unpack('<H', data[:2])[0] if len(data) >= 2 else None
            result['decoded']['uint16_be'] = struct.unpack('>H', data[:2])[0] if len(data) >= 2 else None
            result['decoded']['uint32_le'] = struct.unpack('<I', data[:4])[0] if len(data) >= 4 else None
            result['decoded']['uint32_be'] = struct.unpack('>I', data[:4])[0] if len(data) >= 4 else None
            result['decoded']['int16_le'] = struct.unpack('<h', data[:2])[0] if len(data) >= 2 else None
            result['decoded']['int32_le'] = struct.unpack('<i', data[:4])[0] if len(data) >= 4 else None
            result['decoded']['float_le'] = struct.unpack('<f', data[:4])[0] if len(data) >= 4 else None
            # 尝试 ASCII 解码
            try:
                ascii_str = data.decode('ascii', errors='ignore').rstrip('\x00')
                if ascii_str.isprintable():
                    result['decoded']['ascii'] = ascii_str
            except:
                pass
    except Exception as e:
        result['decoded']['error'] = f'解码失败：{e}'
    return result


@app.route('/api/industrial_protocol/enip_client/write', methods=['POST'])
def enip_client_write():
    """写入ENIP设备属性"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        class_id = data.get('class_id', 0x01)
        instance = data.get('instance', 1)
        attribute = data.get('attribute', 1)
        value_hex = data.get('value', '')

        # 将hex字符串转换为bytes
        try:
            value = bytes.fromhex(value_hex) if value_hex else b''
        except ValueError:
            return jsonify({'success': False, 'error': '无效的hex值'}), 400

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, message = client.set_attribute_single(class_id, instance, attribute, value)

            if success:
                add_log('INFO', f'ENIP写入属性成功: class={class_id}, instance={instance}, attribute={attribute}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP写入属性失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP写入属性异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/get_identity', methods=['GET'])
def enip_client_get_identity():
    """获取设备标识信息"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, identity, message = client.list_identity()

            if success:
                add_log('INFO', f'ENIP获取设备标识成功')
                return jsonify({'success': True, 'identity': identity, 'message': message})
            else:
                add_log('ERROR', f'ENIP获取设备标识失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP获取设备标识异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/reset_device', methods=['POST'])
def enip_client_reset_device():
    """复位设备"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        class_id = data.get('class_id', 0x01)
        instance = data.get('instance', 1)

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, message = client.reset_device(class_id, instance)

            if success:
                add_log('INFO', f'ENIP复位设备成功')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP复位设备失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP复位设备异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/start_device', methods=['POST'])
def enip_client_start_device():
    """启动设备"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        class_id = data.get('class_id', 0x01)
        instance = data.get('instance', 1)

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, message = client.start_device(class_id, instance)

            if success:
                add_log('INFO', f'ENIP启动设备成功')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP启动设备失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP启动设备异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/stop_device', methods=['POST'])
def enip_client_stop_device():
    """停止设备"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        class_id = data.get('class_id', 0x01)
        instance = data.get('instance', 1)

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, message = client.stop_device(class_id, instance)

            if success:
                add_log('INFO', f'ENIP停止设备成功')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP停止设备失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP停止设备异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/list_services', methods=['GET', 'POST'])
def enip_client_list_services():
    """获取服务列表"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, services, message = client.list_services()

            if success:
                add_log('INFO', f'ENIP获取服务列表成功')
                return jsonify({'success': True, 'services': services, 'message': message})
            else:
                add_log('ERROR', f'ENIP获取服务列表失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP获取服务列表异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/list_identity', methods=['GET', 'POST'])
def enip_client_list_identity():
    """获取设备标识 (ListIdentity命令)"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, identity, message = client.list_identity()

            if success:
                add_log('INFO', f'ENIP获取设备标识成功')
                return jsonify({'success': True, 'identity': identity, 'message': message})
            else:
                add_log('ERROR', f'ENIP获取设备标识失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP获取设备标识异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/list_interfaces', methods=['GET', 'POST'])
def enip_client_list_interfaces():
    """获取网络接口列表"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        client_id = request.args.get('client_id', 'default')

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, interfaces, message = client.list_interfaces()

            if success:
                add_log('INFO', f'ENIP获取网络接口列表成功')
                return jsonify({'success': True, 'interfaces': interfaces, 'message': message})
            else:
                add_log('ERROR', f'ENIP获取网络接口列表失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP获取网络接口列表异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/discover_devices', methods=['POST'])
def enip_client_discover_devices():
    """UDP广播发现设备"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        broadcast_addr = data.get('broadcast_addr', '255.255.255.255')
        timeout = data.get('timeout', 2.0)

        # discover_devices不需要已连接的客户端，创建临时客户端
        temp_client = EnipClient()
        success, devices, message = temp_client.discover_devices(broadcast_addr, timeout=timeout)

        if success:
            add_log('INFO', f'ENIP发现设备成功: 发现 {len(devices)} 个设备')
            return jsonify({'success': True, 'devices': devices, 'message': message})
        else:
            add_log('ERROR', f'ENIP发现设备失败: {message}')
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP发现设备异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/send_rr_data', methods=['POST'])
def enip_client_send_rr_data():
    """发送请求-响应数据 (SendRRData 0x006F)"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        cip_data_hex = data.get('cip_data', '')

        # 将hex字符串转换为bytes
        try:
            cip_data = bytes.fromhex(cip_data_hex.replace(' ', '')) if cip_data_hex else b''
        except ValueError:
            return jsonify({'success': False, 'error': '无效的CIP数据格式'}), 400

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, result, message = client.send_rr_data(cip_data)

            if success:
                add_log('INFO', f'ENIP SendRRData成功')
                return jsonify({'success': True, 'response': result, 'message': message})
            else:
                add_log('ERROR', f'ENIP SendRRData失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP SendRRData异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/send_unit_data', methods=['POST'])
def enip_client_send_unit_data():
    """发送单元数据 (SendUnitData 0x0070)"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        cip_data_hex = data.get('cip_data', '')
        conn_id = data.get('connection_id', None)

        # 将hex字符串转换为bytes
        try:
            cip_data = bytes.fromhex(cip_data_hex.replace(' ', '')) if cip_data_hex else b''
        except ValueError:
            return jsonify({'success': False, 'error': '无效的CIP数据格式'}), 400

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, result, message = client.send_unit_data(cip_data, conn_id)

            if success:
                add_log('INFO', f'ENIP SendUnitData成功')
                return jsonify({'success': True, 'response': result, 'message': message})
            else:
                add_log('ERROR', f'ENIP SendUnitData失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP SendUnitData异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_client/send_io_data', methods=['POST'])
def enip_client_send_io_data():
    """发送I/O数据 (UDP 2222端口)"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        client_id = data.get('client_id', 'default')
        host = data.get('host', '')
        io_data_hex = data.get('io_data', '')
        connection_id = data.get('connection_id', None)

        if not host:
            return jsonify({'success': False, 'error': '缺少目标IP'}), 400

        # 将hex字符串转换为bytes
        try:
            io_data = bytes.fromhex(io_data_hex.replace(' ', '')) if io_data_hex else b''
        except ValueError:
            return jsonify({'success': False, 'error': '无效的I/O数据格式'}), 400

        with enip_client_lock:
            if client_id not in enip_clients:
                return jsonify({'success': False, 'error': '客户端未连接'}), 400

            client_info = enip_clients[client_id]
            client = client_info['client']

            success, result, message = client.send_io_data(host, io_data, connection_id)

            if success:
                add_log('INFO', f'ENIP发送I/O数据成功')
                return jsonify({'success': True, 'response': result, 'message': message})
            else:
                add_log('ERROR', f'ENIP发送I/O数据失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP发送I/O数据异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== ENIP服务端路由 ==========

@app.route('/api/industrial_protocol/enip_server/start', methods=['POST'])
def enip_server_start():
    """启动ENIP服务端"""
    if not ENIP_AVAILABLE:
        return jsonify({'success': False, 'error': 'ENIP模块未加载'}), 500

    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = data.get('port', 44818)

        with enip_server_lock:
            # 如果已存在服务端，先停止
            if server_id in enip_servers:
                try:
                    enip_servers[server_id]['server'].stop()
                except:
                    pass
                del enip_servers[server_id]

            # 创建新服务端
            server = EnipServer()
            success, message = server.start(host, port)

            if success:
                enip_servers[server_id] = {
                    'server': server,
                    'host': host,
                    'port': port,
                    'running': True,
                    'start_time': time.strftime("%Y-%m-%d %H:%M:%S")
                }
                add_log('INFO', f'ENIP服务端启动成功: {host}:{port}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'ENIP服务端启动失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'ENIP服务端启动异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_server/stop', methods=['POST'])
def enip_server_stop():
    """停止ENIP服务端"""
    try:
        data = request.json
        server_id = data.get('server_id', 'default')

        with enip_server_lock:
            if server_id in enip_servers:
                try:
                    enip_servers[server_id]['server'].stop()
                except:
                    pass
                del enip_servers[server_id]
                add_log('INFO', f'ENIP服务端停止: {server_id}')
                return jsonify({'success': True, 'message': '服务端已停止'})
            else:
                return jsonify({'success': False, 'error': '服务端不存在'}), 404

    except Exception as e:
        add_log('ERROR', f'ENIP服务端停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/enip_server/status', methods=['GET'])
def enip_server_status():
    """获取ENIP服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')

        with enip_server_lock:
            if server_id in enip_servers:
                server_info = enip_servers[server_id]
                status = server_info['server'].status()
                return jsonify({
                    'success': True,
                    'running': status.get('running', False),
                    'host': server_info['host'],
                    'port': server_info['port'],
                    'active_sessions': status.get('active_sessions', 0),
                    'start_time': server_info.get('start_time', '')
                })
            else:
                return jsonify({'success': True, 'running': False})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== BACnet客户端路由 ==========

@app.route('/api/industrial_protocol/bacnet_client/read', methods=['POST'])
def bacnet_client_read():
    """读取BACnet设备属性"""
    if not BACNET_AVAILABLE or bacnet_handler is None:
        return jsonify({'success': False, 'error': 'BACnet模块未加载'}), 500

    try:
        data = request.json
        destination = data.get('destination')  # 格式: "ip:port"
        object_type = data.get('object_type', 'analogInput')
        object_instance = data.get('object_instance', 1)
        property_id = data.get('property_id', 85)  # 默认presentValue

        if not destination:
            return jsonify({'success': False, 'error': 'destination不能为空'}), 400

        success, value, message = bacnet_handler.read_property(
            destination, object_type, object_instance, property_id
        )

        if success:
            add_log('INFO', f'BACnet读取成功: {destination}, {object_type}:{object_instance}, prop={property_id}')
            return jsonify({
                'success': True,
                'value': value,
                'message': message,
                'destination': destination,
                'object_type': object_type,
                'object_instance': object_instance,
                'property_id': property_id
            })
        else:
            add_log('ERROR', f'BACnet读取失败: {message}')
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'BACnet读取异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/bacnet_client/write', methods=['POST'])
def bacnet_client_write():
    """写入BACnet设备属性"""
    if not BACNET_AVAILABLE or bacnet_handler is None:
        return jsonify({'success': False, 'error': 'BACnet模块未加载'}), 500

    try:
        data = request.json
        destination = data.get('destination')  # 格式: "ip:port"
        object_type = data.get('object_type', 'analogOutput')
        object_instance = data.get('object_instance', 1)
        property_id = data.get('property_id', 85)  # 默认presentValue
        value = data.get('value')
        priority = data.get('priority')  # 可选

        if not destination:
            return jsonify({'success': False, 'error': 'destination不能为空'}), 400
        if value is None:
            return jsonify({'success': False, 'error': 'value不能为空'}), 400

        success, message = bacnet_handler.write_property(
            destination, object_type, object_instance, property_id, value, priority
        )

        if success:
            add_log('INFO', f'BACnet写入成功: {destination}, {object_type}:{object_instance}, prop={property_id}, value={value}')
            return jsonify({
                'success': True,
                'message': message,
                'destination': destination,
                'object_type': object_type,
                'object_instance': object_instance,
                'property_id': property_id,
                'value': value
            })
        else:
            add_log('ERROR', f'BACnet写入失败: {message}')
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'BACnet写入异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== BACnet服务端路由 ==========

@app.route('/api/industrial_protocol/bacnet_server/start', methods=['POST'])
def bacnet_server_start():
    """启动BACnet服务端"""
    if not BACNET_AVAILABLE or bacnet_handler is None:
        return jsonify({'success': False, 'error': 'BACnet模块未加载'}), 500

    try:
        data = request.json
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = data.get('port', 47808)
        device_id = data.get('device_id', 1234)
        device_name = data.get('device_name', 'BACnet Simulator')

        with bacnet_server_lock:
            # 检查是否已在运行
            status = bacnet_handler.status()
            if status.get('running'):
                return jsonify({'success': False, 'error': 'BACnet服务端已在运行'}), 400

            # 启动服务端
            success, message = bacnet_handler.start_server(
                host, port, device_id, device_name
            )

            if success:
                bacnet_server_config[server_id] = {
                    'host': host,
                    'port': port,
                    'device_id': device_id,
                    'device_name': device_name,
                    'start_time': time.strftime("%Y-%m-%d %H:%M:%S")
                }
                add_log('INFO', f'BACnet服务端启动成功: {host}:{port}, device_id={device_id}')
                return jsonify({
                    'success': True,
                    'message': message,
                    'server_id': server_id,
                    'host': host,
                    'port': port,
                    'device_id': device_id
                })
            else:
                add_log('ERROR', f'BACnet服务端启动失败: {message}')
                return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'BACnet服务端启动异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/bacnet_server/stop', methods=['POST'])
def bacnet_server_stop():
    """停止BACnet服务端"""
    if not BACNET_AVAILABLE or bacnet_handler is None:
        return jsonify({'success': False, 'error': 'BACnet模块未加载'}), 500

    try:
        data = request.json
        server_id = data.get('server_id', 'default')

        with bacnet_server_lock:
            success, message = bacnet_handler.stop_server()

            if success:
                if server_id in bacnet_server_config:
                    del bacnet_server_config[server_id]
                add_log('INFO', f'BACnet服务端停止成功: {server_id}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('WARNING', f'BACnet服务端停止失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'BACnet服务端停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/bacnet_server/status', methods=['GET'])
def bacnet_server_status():
    """获取BACnet服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')

        if bacnet_handler is None:
            return jsonify({
                'success': True,
                'running': False,
                'available': False,
                'error': 'BACnet handler not loaded'
            })

        status = bacnet_handler.status()

        return jsonify({
            'success': True,
            'running': status.get('running', False),
            'available': status.get('available', False),
            'host': status.get('host', ''),
            'port': status.get('port', 47808),
            'device_id': status.get('device_id', 1234),
            'device_name': status.get('device_name', ''),
            'start_time': status.get('start_time', ''),
            'loop_running': status.get('loop_running', False),
            'server_id': server_id
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== DNP3客户端路由 ==========

@app.route('/api/industrial_protocol/dnp3_client/connect', methods=['POST'])
def dnp3_client_connect():
    """连接DNP3客户端"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')
        host = data.get('host', '127.0.0.1')
        port = data.get('port', 20000)
        slave = data.get('slave', 1)
        master = data.get('master', 2)
        count = data.get('count', 10)

        with dnp3_client_lock:
            # 断开已有连接
            if client_id in dnp3_clients:
                try:
                    dnp3_clients[client_id]['client'].disconnect()
                except Exception:
                    pass
                del dnp3_clients[client_id]

            client = Dnp3Client()
            success, message = client.connect(host, port, slave, master, count)

            if success:
                dnp3_clients[client_id] = {
                    'client': client,
                    'host': host,
                    'port': port,
                    'slave': slave,
                    'master': master,
                    'count': count,
                    'connect_time': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                add_log('INFO', f'DNP3客户端连接成功: {host}:{port}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'DNP3客户端连接失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3客户端连接异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/disconnect', methods=['POST'])
def dnp3_client_disconnect():
    """断开DNP3客户端连接"""
    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')

        with dnp3_client_lock:
            if client_id in dnp3_clients:
                try:
                    dnp3_clients[client_id]['client'].disconnect()
                except Exception:
                    pass
                del dnp3_clients[client_id]
                add_log('INFO', f'DNP3客户端断开连接: {client_id}')
                return jsonify({'success': True, 'message': 'Disconnected'})
            else:
                return jsonify({'success': True, 'message': 'Not connected'})

    except Exception as e:
        add_log('ERROR', f'DNP3客户端断开异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/status', methods=['GET'])
def dnp3_client_status():
    """获取DNP3客户端连接状态"""
    try:
        client_id = request.args.get('client_id', 'default')

        with dnp3_client_lock:
            if client_id in dnp3_clients:
                client_info = dnp3_clients[client_id]
                status = client_info['client'].status()
                return jsonify({
                    'success': True,
                    'connected': status.get('connected', False),
                    'host': status.get('host', ''),
                    'port': status.get('port', 20000),
                    'slave': status.get('slave', 1),
                    'master': status.get('master', 2),
                    'count': status.get('count', 10),
                    'connect_time': status.get('connect_time', '')
                })
            else:
                return jsonify({'success': True, 'connected': False})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/read', methods=['POST'])
def dnp3_client_read():
    """DNP3客户端读取操作（Class0轮询）"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')

        with dnp3_client_lock:
            if client_id not in dnp3_clients:
                return jsonify({'success': False, 'error': 'Client not connected'}), 400

            client_info = dnp3_clients[client_id]
            success, message, result = client_info['client'].read()

            if success:
                add_log('INFO', f'DNP3读取成功: {message}')
                return jsonify({
                    'success': True,
                    'message': message,
                    'data': result
                })
            else:
                add_log('ERROR', f'DNP3读取失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3读取异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/write', methods=['POST'])
def dnp3_client_write():
    """DNP3客户端写入操作"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')
        index = data.get('index', 0)
        value = data.get('value', 0.0)

        with dnp3_client_lock:
            if client_id not in dnp3_clients:
                return jsonify({'success': False, 'error': 'Client not connected'}), 400

            client_info = dnp3_clients[client_id]
            success, message = client_info['client'].write(index, value)

            if success:
                add_log('INFO', f'DNP3写入成功: index={index}, value={value}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'DNP3写入失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3写入异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/direct_operate', methods=['POST'])
def dnp3_client_direct_operate():
    """DNP3客户端直接执行操作"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        data = request.json or {}
        client_id = data.get('client_id', 'default')
        index = data.get('index', 0)
        value = data.get('value', 0.0)

        with dnp3_client_lock:
            if client_id not in dnp3_clients:
                return jsonify({'success': False, 'error': 'Client not connected'}), 400

            client_info = dnp3_clients[client_id]
            success, message = client_info['client'].direct_operate(index, value)

            if success:
                add_log('INFO', f'DNP3 DirectOperate成功: index={index}, value={value}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('ERROR', f'DNP3 DirectOperate失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3 DirectOperate异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_client/function_codes', methods=['GET'])
def dnp3_client_function_codes():
    """获取DNP3支持的功能码列表"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        codes = get_function_codes_list()
        return jsonify({
            'success': True,
            'function_codes': codes,
            'count': len(codes)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== DNP3服务端路由 ==========

@app.route('/api/industrial_protocol/dnp3_server/start', methods=['POST'])
def dnp3_server_start():
    """启动DNP3服务端子进程"""
    if not DNP3_AVAILABLE:
        return jsonify({'success': False, 'error': 'DNP3 requires Windows + dnp3protocol.dll'}), 500

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        bind = data.get('bind', '0.0.0.0')
        port = data.get('port', 20000)
        count = data.get('count', 10)
        slave = data.get('slave', 1)
        master = data.get('master', 2)

        with dnp3_server_lock:
            # 如果已有服务端在运行，先停止
            if server_id in dnp3_servers:
                try:
                    dnp3_handler.stop_server()
                except Exception:
                    pass
                del dnp3_servers[server_id]

            config = {
                'bind': bind,
                'port': port,
                'count': count,
                'slave': slave,
                'master': master
            }

            success, message = dnp3_handler.start_server(server_id, config)

            if success:
                dnp3_servers[server_id] = {
                    'config': config,
                    'start_time': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                add_log('INFO', f'DNP3服务端启动成功: {bind}:{port}')
                return jsonify({
                    'success': True,
                    'message': message,
                    'server_id': server_id,
                    'config': config
                })
            else:
                add_log('ERROR', f'DNP3服务端启动失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3服务端启动异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_server/stop', methods=['POST'])
def dnp3_server_stop():
    """停止DNP3服务端子进程"""
    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')

        with dnp3_server_lock:
            success, message = dnp3_handler.stop_server()

            if success:
                if server_id in dnp3_servers:
                    del dnp3_servers[server_id]
                add_log('INFO', f'DNP3服务端停止: {server_id}')
                return jsonify({'success': True, 'message': message})
            else:
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'DNP3服务端停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/dnp3_server/status', methods=['GET'])
def dnp3_server_status():
    """获取DNP3服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')

        if not DNP3_AVAILABLE:
            return jsonify({
                'success': True,
                'running': False,
                'available': False,
                'error': 'DNP3 requires Windows + dnp3protocol.dll'
            })

        with dnp3_server_lock:
            status = dnp3_handler.status()

            if server_id in dnp3_servers:
                server_info = dnp3_servers[server_id]
                return jsonify({
                    'success': True,
                    'running': status.get('running', False),
                    'available': DNP3_AVAILABLE,
                    'pid': status.get('pid'),
                    'server_id': server_id,
                    'config': server_info.get('config', {}),
                    'start_time': server_info.get('start_time', '')
                })
            else:
                return jsonify({
                    'success': True,
                    'running': status.get('running', False),
                    'available': DNP3_AVAILABLE
                })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== MMS/IEC 61850客户端路由 ==========

@app.route('/api/industrial_protocol/mms_client/read', methods=['POST'])
def mms_client_read():
    """读取MMS变量"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        host = data.get('host', '127.0.0.1')
        port = data.get('port', 102)
        domain = data.get('domain')  # LogicalDevice 名称
        item = data.get('item')  # 变量项: LogicalNode$FC$DataAttribute

        success, value, message = mms_handler.client_read(host, port, domain, item)

        if success:
            add_log('INFO', f'MMS读取成功: {host}:{port}, domain={domain}, item={item}')
            return jsonify({'success': True, 'value': value, 'message': message})
        else:
            add_log('ERROR', f'MMS读取失败: {message}')
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS读取异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/mms_client/write', methods=['POST'])
def mms_client_write():
    """写入MMS变量"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        host = data.get('host', '127.0.0.1')
        port = data.get('port', 102)
        domain = data.get('domain')
        item = data.get('item')
        value = data.get('value')

        success, message = mms_handler.client_write(host, port, domain, item, value)

        if success:
            add_log('INFO', f'MMS写入成功: {host}:{port}, domain={domain}, item={item}, value={value}')
            return jsonify({'success': True, 'message': message})
        else:
            add_log('ERROR', f'MMS写入失败: {message}')
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS写入异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/mms_client/connect', methods=['POST'])
def mms_client_connect():
    """测试MMS连接"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        host = data.get('host', '127.0.0.1')
        port = data.get('port', 102)

        success, message = mms_handler.client_connect(host, port)

        if success:
            add_log('INFO', f'MMS连接成功: {host}:{port}')
            return jsonify({'success': True, 'message': message})
        else:
            add_log('ERROR', f'MMS连接失败: {message}')
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS连接异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/mms_client/get_domains', methods=['POST'])
def mms_client_get_domains():
    """获取MMS服务器域名列表"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        host = data.get('host', '127.0.0.1')
        port = data.get('port', 102)

        success, domains, message = mms_handler.get_domain_list(host, port)

        if success:
            add_log('INFO', f'MMS获取域名列表成功: {host}:{port}')
            return jsonify({'success': True, 'domains': domains, 'message': message})
        else:
            add_log('ERROR', f'MMS获取域名列表失败: {message}')
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS获取域名列表异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== MMS/IEC 61850服务端路由 ==========

@app.route('/api/industrial_protocol/mms_server/start', methods=['POST'])
def mms_server_start():
    """启动MMS/IEC 61850服务端"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')
        host = data.get('host', '0.0.0.0')
        port = data.get('port', 102)
        ied_name = data.get('ied_name', 'MMS_SIM')
        config = data.get('config')  # 可选: 数据模型配置

        with mms_server_lock:
            # 检查是否已运行
            status = mms_handler.status()
            if status.get('running'):
                return jsonify({'success': False, 'error': 'MMS服务端已在运行'}), 400

            # 启动服务端
            success, message = mms_handler.start_server(
                host=host,
                port=port,
                ied_name=ied_name,
                config=config
            )

            if success:
                mms_servers[server_id] = {
                    'host': host,
                    'port': port,
                    'ied_name': ied_name,
                    'start_time': time.strftime('%Y-%m-%d %H:%M:%S')
                }
                add_log('INFO', f'MMS服务端启动成功: {host}:{port}, IED: {ied_name}')
                return jsonify({'success': True, 'message': message, 'server_id': server_id})
            else:
                add_log('ERROR', f'MMS服务端启动失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS服务端启动异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/mms_server/stop', methods=['POST'])
def mms_server_stop():
    """停止MMS/IEC 61850服务端"""
    if not MMS_AVAILABLE or mms_handler is None:
        return jsonify({'success': False, 'error': 'pyiec61850 not available. Build libiec61850 with -DBUILD_PYTHON_BINDINGS=ON'}), 500

    try:
        data = request.json or {}
        server_id = data.get('server_id', 'default')

        with mms_server_lock:
            success, message = mms_handler.stop_server()

            if success:
                if server_id in mms_servers:
                    del mms_servers[server_id]
                add_log('INFO', f'MMS服务端停止成功: {server_id}')
                return jsonify({'success': True, 'message': message})
            else:
                add_log('WARNING', f'MMS服务端停止失败: {message}')
                return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        add_log('ERROR', f'MMS服务端停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/mms_server/status', methods=['GET'])
def mms_server_status():
    """获取MMS/IEC 61850服务端状态"""
    try:
        server_id = request.args.get('server_id', 'default')

        if mms_handler is None:
            return jsonify({
                'success': True,
                'running': False,
                'available': False,
                'error': 'MMS handler not loaded'
            })

        status = mms_handler.status()

        return jsonify({
            'success': True,
            'running': status.get('running', False),
            'available': status.get('available', False),
            'host': status.get('host', ''),
            'port': status.get('port', 102),
            'ied_name': status.get('ied_name', ''),
            'start_time': status.get('start_time', ''),
            'error': status.get('error'),
            'server_id': server_id
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== HTTP 协议路由 ====================

# HTTP 模块可用性检查
try:
    from http_handler import HTTPClient, HTTPServerWrapper, HTTPAnalyzer, HTTP_METHODS, detect_file_type_by_content, file_manager
    HTTP_AVAILABLE = True
    http_clients = {}  # 存储多个客户端实例
    http_client_lock = threading.Lock()
    http_server = HTTPServerWrapper()
except ImportError as e:
    HTTP_AVAILABLE = False
    logger.warning(f'HTTP 模块加载失败: {e}')


@app.route('/api/industrial_protocol/http_client/test_connection', methods=['POST'])
def http_client_test_connection():
    """测试与目标服务器的连接"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        data = request.json
        host = data.get('host', '')
        port = int(data.get('port', 80))
        timeout = float(data.get('timeout', 5))

        if not host:
            return jsonify({'success': False, 'error': '请输入主机地址'}), 400

        # 尝试建立 TCP 连接测试
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            sock.close()
            add_log('INFO', f'HTTP连接测试成功: {host}:{port}')
            return jsonify({'success': True, 'message': f'连接成功: {host}:{port}'})
        except socket.timeout:
            return jsonify({'success': False, 'error': '连接超时'}), 200
        except socket.error as e:
            return jsonify({'success': False, 'error': f'连接失败: {e}'}), 200

    except Exception as e:
        add_log('ERROR', f'HTTP连接测试异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_client/send', methods=['POST'])
def http_client_send():
    """发送 HTTP 请求"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        data = request.json
        host = data.get('host', '')
        port = int(data.get('port', 80))
        method = data.get('method', 'GET').upper()
        path = data.get('path', '/')
        headers = data.get('headers', {})
        body = data.get('body', '')
        timeout = float(data.get('timeout', 10.0))

        if not host:
            return jsonify({'success': False, 'error': '请输入主机地址'}), 400

        if method not in HTTP_METHODS:
            return jsonify({'success': False, 'error': f'不支持的请求方法: {method}'}), 400

        client = HTTPClient()
        success, result, message = client.send_request(host, port, method, path, headers, body, timeout)

        add_log('INFO', f'HTTP请求: {method} {host}:{port}{path} - {message}')

        return jsonify({
            'success': success,
            'result': result,
            'message': message
        })

    except Exception as e:
        add_log('ERROR', f'HTTP请求异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_client/download', methods=['POST'])
def http_client_download():
    """下载文件到客户端 Agent 所在的测试环境"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        data = request.json
        host = data.get('host', '')
        port = int(data.get('port', 80))
        filename = data.get('filename', '')
        timeout = float(data.get('timeout', 30))

        if not host:
            return jsonify({'success': False, 'error': '请输入主机地址'}), 400
        if not filename:
            return jsonify({'success': False, 'error': '请输入文件名'}), 400

        # 构建 HTTP GET 请求
        path = f'/files/{filename}'
        request_lines = [f"GET {path} HTTP/1.1"]
        request_lines.append(f"Host: {host}")
        request_lines.append("Connection: close")
        request_lines.append("")
        request_lines.append("")
        raw_request = "\r\n".join(request_lines).encode('utf-8')

        # 发送请求
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(raw_request)

        # 接收响应
        response_data = b''
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            response_data += chunk
        sock.close()

        # 解析响应
        if b'\r\n\r\n' in response_data:
            header_part, body = response_data.split(b'\r\n\r\n', 1)
            header_text = header_part.decode('utf-8', errors='ignore')

            # 检查状态码
            status_line = header_text.split('\r\n')[0]
            if '200' not in status_line:
                return jsonify({
                    'success': False,
                    'error': f'服务器返回错误: {status_line}'
                })

            # 保存文件到客户端 Agent 的文件目录
            success, type_info, message = file_manager.save_file(filename, body)

            if success:
                add_log('INFO', f'HTTP下载文件: {filename} ({len(body)} bytes) -> {file_manager.base_dir}')
                return jsonify({
                    'success': True,
                    'filename': filename,
                    'size': len(body),
                    'file_type': type_info.get('detected_type', '未知'),
                    'saved_to': file_manager.base_dir,
                    'message': f'文件已下载到客户端: {file_manager.base_dir}\\{filename}'
                })
            else:
                return jsonify({'success': False, 'error': f'保存文件失败: {message}'})
        else:
            return jsonify({'success': False, 'error': '无效的HTTP响应'}), 500

    except socket.timeout:
        return jsonify({'success': False, 'error': '连接超时'}), 200
    except socket.error as e:
        return jsonify({'success': False, 'error': f'连接错误: {e}'}), 200
    except Exception as e:
        add_log('ERROR', f'HTTP下载异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_client/methods', methods=['GET'])
def http_client_methods():
    """获取支持的 HTTP 方法列表"""
    return jsonify({
        'success': True,
        'methods': HTTP_METHODS
    })


@app.route('/api/industrial_protocol/http_server/start', methods=['POST'])
def http_server_start():
    """启动 HTTP 服务器"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        data = request.json or {}
        port = int(data.get('port', 8080))
        host = data.get('host', '0.0.0.0')

        success, message = http_server.start(port, host)

        if success:
            add_log('INFO', f'HTTP服务器启动成功: {host}:{port}')
        else:
            add_log('ERROR', f'HTTP服务器启动失败: {message}')

        return jsonify({'success': success, 'message': message})

    except Exception as e:
        add_log('ERROR', f'HTTP服务器启动异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_server/stop', methods=['POST'])
def http_server_stop():
    """停止 HTTP 服务器"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        success, message = http_server.stop()
        add_log('INFO', f'HTTP服务器停止: {message}')
        return jsonify({'success': success, 'message': message})

    except Exception as e:
        add_log('ERROR', f'HTTP服务器停止异常: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_server/status', methods=['GET'])
def http_server_status():
    """获取 HTTP 服务器状态"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        status = http_server.get_status()
        # 添加文件管理目录
        status['base_dir'] = file_manager.base_dir
        return jsonify({'success': True, 'status': status, 'base_dir': file_manager.base_dir})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_server/logs', methods=['GET'])
def http_server_logs():
    """获取 HTTP 服务器请求日志"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        limit = int(request.args.get('limit', 100))
        logs = http_server.get_logs(limit)
        return jsonify({'success': True, 'logs': logs, 'count': len(logs)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_server/clear_logs', methods=['POST'])
def http_server_clear_logs():
    """清除 HTTP 服务器请求日志"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        http_server.clear_logs()
        return jsonify({'success': True, 'message': '日志已清除'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== HTTP 文件管理路由 ====================

@app.route('/api/industrial_protocol/http_files/list', methods=['GET'])
def http_files_list():
    """列出 HTTP 文件目录中的所有文件"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        files = file_manager.list_files()
        return jsonify({
            'success': True,
            'files': files,
            'count': len(files),
            'base_dir': file_manager.base_dir
        })

    except Exception as e:
        add_log('ERROR', f'列出HTTP文件失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_files/download/<filename>', methods=['GET'])
def http_files_download(filename):
    """下载文件"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        success, content, message = file_manager.get_file(filename)

        if success:
            from flask import Response
            # 检测文件类型
            type_info = detect_file_type_by_content(os.path.join(file_manager.base_dir, filename))

            response = Response(
                content,
                mimetype='application/octet-stream',
                headers={
                    'Content-Disposition': f'attachment; filename="{filename}"',
                    'X-File-Type': type_info.get('detected_type', 'unknown'),
                    'X-File-Size': len(content),
                }
            )
            add_log('INFO', f'HTTP文件下载: {filename}, 类型: {type_info.get("detected_type")}')
            return response
        else:
            return jsonify({'success': False, 'error': message}), 404

    except Exception as e:
        add_log('ERROR', f'HTTP文件下载失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_files/upload', methods=['POST'])
def http_files_upload():
    """上传文件"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '没有上传文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '没有选择文件'}), 400

        filename = file.filename
        content = file.read()

        success, type_info, message = file_manager.save_file(filename, content)

        if success:
            add_log('INFO', f'HTTP文件上传: {filename}, 类型: {type_info.get("detected_type")}, 大小: {len(content)} bytes')
            return jsonify({
                'success': True,
                'message': message,
                'filename': filename,
                'size': len(content),
                'type_info': type_info
            })
        else:
            return jsonify({'success': False, 'error': message}), 500

    except Exception as e:
        add_log('ERROR', f'HTTP文件上传失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_files/analyze/<filename>', methods=['GET'])
def http_files_analyze(filename):
    """分析文件（检测真实类型）"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        result = file_manager.analyze_file(filename)
        add_log('INFO', f'HTTP文件分析: {filename}, 类型: {result.get("type", {}).get("detected_type")}')
        return jsonify(result)

    except Exception as e:
        add_log('ERROR', f'HTTP文件分析失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_files/delete/<filename>', methods=['DELETE'])
def http_files_delete(filename):
    """删除文件"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        success, message = file_manager.delete_file(filename)

        if success:
            add_log('INFO', f'HTTP文件删除: {filename}')
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 404

    except Exception as e:
        add_log('ERROR', f'HTTP文件删除失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/industrial_protocol/http_files/set_dir', methods=['POST'])
def http_files_set_dir():
    """设置文件目录"""
    if not HTTP_AVAILABLE:
        return jsonify({'success': False, 'error': 'HTTP模块未加载'}), 500

    try:
        data = request.json or {}
        new_dir = data.get('directory', '')

        if not new_dir:
            return jsonify({'success': False, 'error': '请指定目录路径'}), 400

        file_manager.set_base_dir(new_dir)
        add_log('INFO', f'HTTP文件目录设置: {new_dir}')

        return jsonify({
            'success': True,
            'message': f'目录已设置: {new_dir}',
            'base_dir': file_manager.base_dir
        })

    except Exception as e:
        add_log('ERROR', f'设置HTTP文件目录失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    import sys
    import os
    from datetime import datetime
    
    # 初始化数据库（在add_log函数定义后）
    try:
        init_s7_database()
    except Exception as e:
        print(f'[ERROR] S7数据库初始化失败: {e}')
        import traceback
        traceback.print_exc()
    
    # 支持通过命令行参数指定端口，默认8889（避免与packet_agent.py的8888端口冲突）
    port = 8889
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"警告: 无效的端口参数 {sys.argv[1]}，使用默认端口8889")
    
    # 获取文件信息
    script_path = os.path.abspath(__file__)
    script_size = os.path.getsize(script_path)
    script_mtime = datetime.fromtimestamp(os.path.getmtime(script_path)).strftime('%Y-%m-%d %H:%M:%S')
    
    print("=" * 60)
    print("工控协议代理程序")
    print("=" * 60)
    print(f"*** [文件下发] 工控协议Agent文件已部署 ***")
    print(f"*** [文件信息] 文件路径: {script_path} ***")
    print(f"*** [文件信息] 文件大小: {script_size} 字节 ***")
    print(f"*** [文件信息] 修改时间: {script_mtime} ***")
    print(f"监听地址: 0.0.0.0:{port}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)

