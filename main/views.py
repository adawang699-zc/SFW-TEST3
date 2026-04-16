"""
Django Views - Ubuntu 多 Agent 一体化部署平台

主要功能:
- 网卡扫描和管理
- Agent 创建、启动、停止
- 功能页面（报文发送、工控协议、端口扫描等）
- Syslog 接收
- SNMP 管理
"""

import json
import subprocess
import logging
import socket
import os
import requests
import time
from datetime import datetime
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings

from main.models import NetworkInterface, LocalAgent, TestDevice, AgentStatistics
from main.syslog_server import (
    start_syslog_server, stop_syslog_server, get_syslog_status,
    get_syslog_logs, clear_syslog_logs, set_syslog_filter_ip
)
from main.snmp_utils import (
    snmp_get, snmp_walk, start_trap_receiver, stop_trap_receiver,
    get_trap_receiver_status, get_trap_receiver_traps, clear_trap_receiver_traps,
    PYSNMP_AVAILABLE
)

logger = logging.getLogger('main')


# ========== 页面视图 ==========

def home(request):
    """首页"""
    agents = LocalAgent.objects.all()
    devices = TestDevice.objects.all()

    context = {
        'agents': agents,
        'devices': devices,
        'management_interface': settings.MANAGEMENT_INTERFACE,
    }
    return render(request, 'home.html', context)


def agent_manage(request):
    """Agent 管理页面（网卡-Agent 绑定）"""
    interfaces = NetworkInterface.objects.all()
    agents = LocalAgent.objects.all()

    context = {
        'interfaces': interfaces,
        'agents': agents,
        'management_interface': settings.MANAGEMENT_INTERFACE,
    }
    return render(request, 'agent_manage.html', context)


def device_monitor(request):
    """测试设备管理页面"""
    devices = TestDevice.objects.all()
    context = {'devices': devices}
    return render(request, 'device_monitor.html', context)


def packet_send(request):
    """报文发送页面"""
    agents = LocalAgent.objects.filter(status='running')
    context = {'agents': agents}
    return render(request, 'packet_send.html', context)


def industrial_protocol(request):
    """工控协议页面"""
    agents = LocalAgent.objects.filter(status='running')
    context = {'agents': agents}
    return render(request, 'industrial_protocol.html', context)


def port_scan(request):
    """端口扫描页面"""
    agents = LocalAgent.objects.filter(status='running')
    context = {'agents': agents}
    return render(request, 'port_scan.html', context)


def packet_replay(request):
    """报文回放页面"""
    agents = LocalAgent.objects.filter(status='running')
    context = {'agents': agents}
    return render(request, 'packet_replay.html', context)


# ========== 网卡管理 API ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_scan_interfaces(request):
    """扫描系统网卡"""
    try:
        import psutil

        interfaces = []
        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()

        for name, addrs in net_if_addrs.items():
            # 跳过回环接口
            if name.startswith('lo') or name.lower() == 'loopback':
                continue

            # 获取 IPv4 地址
            ipv4 = None
            mac = None
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                elif hasattr(psutil, 'PF_LINK') and addr.family == psutil.PF_LINK:
                    mac = addr.address

            if not ipv4:
                continue

            # 获取网卡速率
            stats = net_if_stats.get(name)
            speed = stats.speed if stats and stats.speed > 0 else None

            # 判断是否是管理网卡
            is_management = (name == settings.MANAGEMENT_INTERFACE)

            interfaces.append({
                'name': name,
                'ip_address': ipv4,
                'mac_address': mac or '',
                'speed': speed,
                'is_management': is_management,
                'is_up': stats.isup if stats else False
            })

        # 保存到数据库
        for iface in interfaces:
            NetworkInterface.objects.update_or_create(
                name=iface['name'],
                defaults={
                    'ip_address': iface['ip_address'],
                    'mac_address': iface.get('mac_address', ''),
                    'speed': iface.get('speed'),
                    'is_management': iface['is_management'],
                    'is_available': iface['is_up'],
                }
            )

        logger.info(f"扫描到 {len(interfaces)} 个网卡")

        return JsonResponse({
            'success': True,
            'interfaces': interfaces,
            'count': len(interfaces),
            'management_interface': settings.MANAGEMENT_INTERFACE
        })

    except Exception as e:
        logger.exception(f"扫描网卡失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_interface_list(request):
    """获取网卡列表"""
    interfaces = NetworkInterface.objects.all()

    data = []
    for iface in interfaces:
        agent = LocalAgent.objects.filter(interface=iface).first()
        data.append({
            'id': iface.id,
            'name': iface.name,
            'ip_address': iface.ip_address,
            'mac_address': iface.mac_address,
            'speed': iface.speed,
            'is_management': iface.is_management,
            'is_available': iface.is_available,
            'has_agent': agent is not None,
            'agent_id': agent.agent_id if agent else None,
            'agent_status': agent.status if agent else None,
        })

    return JsonResponse({
        'interfaces': data,
        'management_interface': settings.MANAGEMENT_INTERFACE
    })


# ========== Agent 管理 API ==========

@require_http_methods(["GET"])
def api_agent_list(request):
    """获取 Agent 列表"""
    agents = LocalAgent.objects.all()

    data = []
    for agent in agents:
        # 查询 Agent 实际状态（通过 HTTP）
        actual_status = agent.status
        try:
            resp = requests.get(
                f"http://{agent.interface.ip_address}:{agent.port}/api/status",
                timeout=2
            )
            if resp.status_code == 200:
                actual_status = 'running'
                agent.status = 'running'
                agent.save()
        except:
            actual_status = 'stopped'
            if agent.status != 'stopped':
                agent.status = 'stopped'
                agent.save()

        data.append({
            'id': agent.id,
            'agent_id': agent.agent_id,
            'interface_name': agent.interface.name,
            'ip_address': agent.interface.ip_address,
            'port': agent.port,
            'status': actual_status,
            'auto_start': agent.auto_start,
            'last_start_time': agent.last_start_time.isoformat() if agent.last_start_time else None,
            'created_at': agent.created_at.isoformat(),
        })

    return JsonResponse({'agents': data})


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_create(request):
    """创建 Agent（绑定网卡）"""
    try:
        data = json.loads(request.body)

        interface_name = data.get('interface_name')
        port = data.get('port', settings.AGENT_PORT_RANGE_START)

        # 检查网卡是否存在
        try:
            interface = NetworkInterface.objects.get(name=interface_name)
        except NetworkInterface.DoesNotExist:
            return JsonResponse({'success': False, 'error': '网卡不存在'})

        # 检查网卡是否是管理网卡
        if interface.is_management:
            return JsonResponse({'success': False, 'error': '管理网卡不能绑定 Agent'})

        # 检查网卡是否已绑定 Agent
        if LocalAgent.objects.filter(interface=interface).exists():
            return JsonResponse({'success': False, 'error': '网卡已绑定 Agent'})

        # 生成 Agent ID：agent_{网卡名}
        agent_id = f"agent_{interface_name}"

        # 自动分配端口（如果冲突）
        existing_ports = LocalAgent.objects.values_list('port', flat=True)
        while port in existing_ports:
            port += 1

        # 创建 Agent
        agent = LocalAgent.objects.create(
            agent_id=agent_id,
            interface=interface,
            port=port,
            status='stopped',
            auto_start=False
        )

        # 创建统计记录
        AgentStatistics.objects.create(agent=agent)

        logger.info(f"创建 Agent: {agent_id}, 网卡: {interface_name}, 端口: {port}")

        return JsonResponse({
            'success': True,
            'agent': {
                'agent_id': agent.agent_id,
                'interface_name': agent.interface.name,
                'ip_address': agent.interface.ip_address,
                'port': agent.port,
            }
        })

    except Exception as e:
        logger.exception(f"创建 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_delete(request):
    """删除 Agent"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 停止 systemd 服务
        service_name = agent.get_service_name()
        subprocess.run(['sudo', 'systemctl', 'stop', service_name], timeout=10, capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'disable', service_name], timeout=10, capture_output=True)

        # 删除 systemd 服务文件
        service_file = f'/etc/systemd/system/{service_name}.service'
        subprocess.run(['sudo', 'rm', '-f', service_file], timeout=5, capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10, capture_output=True)

        # 删除数据库记录
        interface_name = agent.interface.name
        agent.delete()

        logger.info(f"删除 Agent: {agent_id}")

        return JsonResponse({
            'success': True,
            'message': f'Agent {agent_id} 已删除',
            'interface_name': interface_name
        })

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"删除 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_start(request):
    """启动 Agent"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 创建 systemd 服务文件（如果不存在）
        service_file = f'/etc/systemd/system/{agent.get_service_name()}.service'

        service_content = f"""[Unit]
Description=Packet Agent {agent.agent_id} ({agent.interface.name})
After=network.target

[Service]
Type=simple
Environment="AGENT_ID={agent.agent_id}"
Environment="BIND_IP={agent.interface.ip_address}"
Environment="BIND_INTERFACE={agent.interface.name}"
Environment="AGENT_PORT={agent.port}"
WorkingDirectory={settings.AGENT_WORK_DIR}
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

        # 写入服务文件
        subprocess.run(
            ['sudo', 'tee', service_file],
            input=service_content,
            capture_output=True,
            text=True,
            timeout=10
        )

        # 重载 systemd
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10, capture_output=True)

        # 启动服务
        result = subprocess.run(
            ['sudo', 'systemctl', 'start', agent.get_service_name()],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            # 等待 Agent 启动
            time.sleep(3)

            # 检查 Agent 状态
            try:
                resp = requests.get(
                    f"http://{agent.interface.ip_address}:{agent.port}/api/status",
                    timeout=5
                )
                if resp.status_code == 200:
                    agent.status = 'running'
                    agent.last_start_time = datetime.now()
                    agent.save()
                    logger.info(f"Agent {agent_id} 启动成功")
                    return JsonResponse({'success': True, 'status': 'running'})
            except:
                agent.status = 'error'
                agent.save()
                return JsonResponse({'success': True, 'status': 'starting'})

        else:
            logger.error(f"启动 Agent 失败: {result.stderr}")
            return JsonResponse({'success': False, 'error': result.stderr})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"启动 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_stop(request):
    """停止 Agent"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        result = subprocess.run(
            ['sudo', 'systemctl', 'stop', agent.get_service_name()],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            agent.status = 'stopped'
            agent.last_stop_time = datetime.now()
            agent.save()
            logger.info(f"Agent {agent_id} 已停止")
            return JsonResponse({'success': True, 'status': 'stopped'})
        else:
            return JsonResponse({'success': False, 'error': result.stderr})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"停止 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_agent_status(request):
    """查询 Agent 状态"""
    agent_id = request.GET.get('agent_id')

    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 通过 HTTP 查询实际状态
        try:
            resp = requests.get(
                f"http://{agent.interface.ip_address}:{agent.port}/api/status",
                timeout=2
            )
            if resp.status_code == 200:
                status_data = resp.json()
                agent.status = 'running'
                agent.save()
                return JsonResponse({
                    'success': True,
                    'agent_id': agent_id,
                    'status': 'running',
                    'uptime': status_data.get('uptime'),
                    'interface': agent.interface.name,
                })
        except:
            agent.status = 'stopped'
            agent.save()
            return JsonResponse({
                'success': True,
                'agent_id': agent_id,
                'status': 'stopped',
            })

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})


@require_http_methods(["GET"])
def api_agent_logs(request):
    """获取 Agent 日志"""
    agent_id = request.GET.get('agent_id')
    lines = int(request.GET.get('lines', 50))

    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)

        result = subprocess.run(
            ['sudo', 'journalctl', '-u', agent.get_service_name(), '-n', str(lines), '--no-pager'],
            capture_output=True,
            text=True,
            timeout=10
        )

        return JsonResponse({
            'success': True,
            'logs': result.stdout,
            'agent_id': agent_id,
        })

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 功能 API（代理到 Agent） ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_send_packet(request):
    """发送报文（代理到指定 Agent）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        if agent.status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        # 转发请求到 Agent
        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/send_packet",
            json=data,
            timeout=30
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_send_protocol(request):
    """发送工控协议报文"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        if agent.status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/send_protocol",
            json=data,
            timeout=30
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_start_scan(request):
    """启动端口扫描"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        if agent.status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/start_scan",
            json=data,
            timeout=10
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_start_replay(request):
    """启动报文回放"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        if agent.status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/start_replay",
            json=data,
            timeout=10
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_stop_scan(request):
    """停止端口扫描"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/stop_scan",
            json={},
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_scan_progress(request):
    """获取扫描进度"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.get(
            f"http://{agent.interface.ip_address}:{agent.port}/api/scan_progress",
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_scan_results(request):
    """获取扫描结果"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.get(
            f"http://{agent.interface.ip_address}:{agent.port}/api/scan_results",
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_list_pcap_files(request):
    """获取 PCAP 文件列表"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.get(
            f"http://{agent.interface.ip_address}:{agent.port}/api/list_pcap_files",
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_stop_replay(request):
    """停止报文回放"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.post(
            f"http://{agent.interface.ip_address}:{agent.port}/api/stop_replay",
            json={},
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_replay_stats(request):
    """获取回放统计"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        resp = requests.get(
            f"http://{agent.interface.ip_address}:{agent.port}/api/replay_stats",
            timeout=5
        )

        return JsonResponse(resp.json())

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 测试设备 API ==========

@require_http_methods(["GET"])
def api_device_list(request):
    """获取测试设备列表"""
    devices = TestDevice.objects.all()

    data = [{
        'id': d.id,
        'name': d.name,
        'type': d.type,
        'ip': d.ip,
        'port': d.port,
        'description': d.description,
        'created_at': d.created_at.isoformat(),
    } for d in devices]

    return JsonResponse({'devices': data})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_add(request):
    """添加测试设备"""
    try:
        data = json.loads(request.body)

        device = TestDevice.objects.create(
            name=data.get('name'),
            type=data.get('type', 'ic_firewall'),
            ip=data.get('ip'),
            port=data.get('port', 22),
            user=data.get('user', 'admin'),
            password=data.get('password', ''),
            description=data.get('description', ''),
        )

        return JsonResponse({
            'success': True,
            'device': {
                'id': device.id,
                'name': device.name,
                'ip': device.ip,
            }
        })

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_delete(request):
    """删除测试设备"""
    try:
        data = json.loads(request.body)
        device_id = data.get('id')

        device = TestDevice.objects.get(id=device_id)
        device.delete()

        return JsonResponse({'success': True})

    except TestDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': '设备不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ========== Syslog 接收 ==========

def syslog_receiver(request):
    """Syslog 接收页面"""
    status = get_syslog_status()
    devices = TestDevice.objects.all()
    context = {
        'status': status,
        'devices': devices,
    }
    return render(request, 'syslog_receiver.html', context)


@require_http_methods(["GET"])
def api_syslog_status(request):
    """获取 Syslog 服务器状态"""
    status = get_syslog_status()
    return JsonResponse({'success': True, 'status': status})


@require_http_methods(["POST"])
@csrf_exempt
def api_syslog_control(request):
    """控制 Syslog 服务器（启动/停止）"""
    try:
        data = json.loads(request.body)
        action = data.get('action')
        port = data.get('port', 514)

        if action == 'start':
            success, message = start_syslog_server(port)
            return JsonResponse({'success': success, 'message': message})
        elif action == 'stop':
            success, message = stop_syslog_server()
            return JsonResponse({'success': success, 'message': message})
        else:
            return JsonResponse({'success': False, 'error': '无效操作'})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_syslog_logs(request):
    """获取 Syslog 日志"""
    limit = int(request.GET.get('limit', 1000))
    filter_ip = request.GET.get('filter_ip', '')

    logs = get_syslog_logs(limit, filter_ip)
    return JsonResponse({'success': True, 'logs': logs})


@require_http_methods(["POST"])
@csrf_exempt
def api_syslog_clear(request):
    """清空 Syslog 日志"""
    success, message = clear_syslog_logs()
    return JsonResponse({'success': success, 'message': message})


@require_http_methods(["POST"])
@csrf_exempt
def api_syslog_filter(request):
    """设置 Syslog IP 过滤"""
    try:
        data = json.loads(request.body)
        filter_ip = data.get('filter_ip', '')

        success, message = set_syslog_filter_ip(filter_ip)
        return JsonResponse({'success': success, 'message': message})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ========== SNMP 管理 ==========

def snmp(request):
    """SNMP 管理页面"""
    trap_status = get_trap_receiver_status()
    devices = TestDevice.objects.all()
    context = {
        'trap_status': trap_status,
        'devices': devices,
        'pysnmp_available': PYSNMP_AVAILABLE,
    }
    return render(request, 'snmp.html', context)


@require_http_methods(["POST"])
@csrf_exempt
def api_snmp_get(request):
    """SNMP GET/WALK 操作"""
    try:
        data = json.loads(request.body)

        ip = data.get('ip')
        oid = data.get('oid')
        version = data.get('version', 'v2c')
        port = data.get('port', 161)
        walk = data.get('walk', False)

        # V1/V2C 参数
        community = data.get('community', 'public')

        # V3 参数
        security_username = data.get('security_username', '')
        security_level = data.get('security_level', 'noAuthNoPriv')
        auth_protocol = data.get('auth_protocol', 'MD5')
        auth_password = data.get('auth_password', '')
        priv_protocol = data.get('priv_protocol', 'DES')
        priv_password = data.get('priv_password', '')

        if walk:
            success, result = snmp_walk(
                ip, oid, community, version, port,
                security_username, security_level,
                auth_protocol, auth_password,
                priv_protocol, priv_password
            )
        else:
            success, result = snmp_get(
                ip, oid, community, version, port,
                security_username, security_level,
                auth_protocol, auth_password,
                priv_protocol, priv_password
            )

        if success:
            return JsonResponse({'success': True, 'data': result})
        else:
            return JsonResponse({'success': False, 'error': result})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_snmp_trap_control(request):
    """控制 SNMPTRAP 接收器"""
    try:
        data = json.loads(request.body)
        action = data.get('action')

        if action == 'start':
            port = data.get('port', 162)
            security_username = data.get('security_username', '')
            security_level = data.get('security_level', 'noAuthNoPriv')
            auth_protocol = data.get('auth_protocol', 'MD5')
            auth_password = data.get('auth_password', '')
            priv_protocol = data.get('priv_protocol', 'DES')
            priv_password = data.get('priv_password', '')

            success, message = start_trap_receiver(
                port, security_username, security_level,
                auth_protocol, auth_password,
                priv_protocol, priv_password
            )
            return JsonResponse({'success': success, 'message': message})

        elif action == 'stop':
            success, message = stop_trap_receiver()
            return JsonResponse({'success': success, 'message': message})

        else:
            return JsonResponse({'success': False, 'error': '无效操作'})

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_snmp_trap_status(request):
    """获取 SNMPTRAP 接收器状态"""
    status = get_trap_receiver_status()
    return JsonResponse({'success': True, 'status': status})


@require_http_methods(["GET"])
def api_snmp_trap_traps(request):
    """获取接收到的 TRAP 列表"""
    limit = int(request.GET.get('limit', 1000))
    traps = get_trap_receiver_traps(limit)
    return JsonResponse({'success': True, 'traps': traps})


@require_http_methods(["POST"])
@csrf_exempt
def api_snmp_trap_clear(request):
    """清空 TRAP 列表"""
    success, message = clear_trap_receiver_traps()
    return JsonResponse({'success': success, 'message': message})


# ========== DHCP 客户端 ==========

def dhcp_client(request):
    """DHCP 客户端模拟页面"""
    devices = TestDevice.objects.all()
    context = {'devices': devices}
    return render(request, 'dhcp_client.html', context)


# ========== 知识库管理 ==========

import os
import base64
from pathlib import Path
from djangoProject.config import ADMIN_PASSWORD

# 知识库模板目录
KNOWLEDGE_TEMPLATE_DIR = Path(settings.BASE_DIR) / 'knowledge_templates'
KNOWLEDGE_TEMPLATE_DIR.mkdir(exist_ok=True)

# 知识库子目录
SERVICE_TEMPLATE_DIR = KNOWLEDGE_TEMPLATE_DIR / 'service'
VUL_TEMPLATE_DIR = KNOWLEDGE_TEMPLATE_DIR / 'vul'
VIRUS_TEMPLATE_DIR = KNOWLEDGE_TEMPLATE_DIR / 'virus'

for d in [SERVICE_TEMPLATE_DIR, VUL_TEMPLATE_DIR, VIRUS_TEMPLATE_DIR]:
    d.mkdir(exist_ok=True)


def knowledge_base(request):
    """知识库管理页面"""
    return render(request, 'knowledge_base.html')


# ========== 知识库 API ==========

@require_http_methods(["GET"])
def api_knowledge_templates(request):
    """获取预定义服务模板列表"""
    templates = []
    for f in SERVICE_TEMPLATE_DIR.glob('*.json'):
        stat = f.stat()
        templates.append({
            'name': f.stem,
            'time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'size': stat.st_size
        })
    return JsonResponse({'success': True, 'templates': templates})


@require_http_methods(["GET"])
def api_knowledge_template_get(request, name):
    """获取单个模板内容"""
    try:
        file_path = SERVICE_TEMPLATE_DIR / f'{name}.json'
        if not file_path.exists():
            return JsonResponse({'success': False, 'error': '模板不存在'})

        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return JsonResponse({'success': True, 'content': content})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_knowledge_template_save(request):
    """保存模板"""
    try:
        data = json.loads(request.body)
        name = data.get('name')
        content = data.get('content')

        if not name or not content:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_path = SERVICE_TEMPLATE_DIR / f'{name}.json'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return JsonResponse({'success': True, 'message': '保存成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_knowledge_template_delete(request):
    """删除模板"""
    try:
        data = json.loads(request.body)
        name = data.get('name')

        file_path = SERVICE_TEMPLATE_DIR / f'{name}.json'
        if file_path.exists():
            file_path.unlink()

        return JsonResponse({'success': True, 'message': '删除成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_knowledge_create(request):
    """创建知识库升级包"""
    try:
        data = json.loads(request.body)
        template_content = data.get('template_content')
        version = data.get('version', '1.0.0')
        time_str = data.get('time', '')

        if not template_content:
            return JsonResponse({'success': False, 'error': '缺少模板内容'})

        from main.knowledge_utils import create_knowledge_package

        success, result = create_knowledge_package(template_content, version, time_str)

        if success:
            content_b64 = base64.b64encode(result).decode('utf-8')
            filename = f'service_{version}.bin'
            return JsonResponse({
                'success': True,
                'content': content_b64,
                'filename': filename
            })
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"创建知识库包失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_knowledge_upgrade(request):
    """升级知识库到设备"""
    try:
        data = json.loads(request.body)
        ip = data.get('ip')
        content_b64 = data.get('content')
        auto_get_cookie = data.get('auto_get_cookie', False)

        if not ip or not content_b64:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_content = base64.b64decode(content_b64)

        from main.knowledge_utils import upgrade_knowledge_to_device

        success, result = upgrade_knowledge_to_device(ip, file_content, auto_get_cookie=auto_get_cookie)

        if success:
            return JsonResponse({'success': True, 'response': result})
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"升级知识库失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 漏洞库 API ==========

@require_http_methods(["GET"])
def api_vul_templates(request):
    """获取漏洞库模板列表"""
    templates = []
    for f in VUL_TEMPLATE_DIR.glob('*.zip'):
        stat = f.stat()
        templates.append({
            'name': f.stem,
            'time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'size': stat.st_size
        })
    return JsonResponse({'success': True, 'templates': templates})


@require_http_methods(["GET"])
def api_vul_template_get(request, name):
    """获取漏洞库模板文件"""
    try:
        file_path = VUL_TEMPLATE_DIR / f'{name}.zip'
        if not file_path.exists():
            return JsonResponse({'success': False, 'error': '模板不存在'})

        with open(file_path, 'rb') as f:
            response = JsonResponse(f.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{name}.zip"'
            return response
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_vul_template_save(request):
    """保存漏洞库模板"""
    try:
        name = request.POST.get('name')
        file = request.FILES.get('file')

        if not name or not file:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_path = VUL_TEMPLATE_DIR / f'{name}.zip'
        with open(file_path, 'wb') as f:
            for chunk in file.chunks():
                f.write(chunk)

        return JsonResponse({'success': True, 'message': '保存成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_vul_template_delete(request):
    """删除漏洞库模板"""
    try:
        data = json.loads(request.body)
        name = data.get('name')

        file_path = VUL_TEMPLATE_DIR / f'{name}.zip'
        if file_path.exists():
            file_path.unlink()

        return JsonResponse({'success': True, 'message': '删除成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_vul_create(request):
    """创建漏洞库升级包"""
    try:
        file = request.FILES.get('file')
        build_time = request.POST.get('build_time')
        version = request.POST.get('version')

        if not file or not build_time or not version:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        zip_content = file.read()

        from main.knowledge_utils import create_vul_package

        success, result = create_vul_package(zip_content, build_time, version)

        if success:
            content_b64 = base64.b64encode(result).decode('utf-8')
            filename = 'vul.lib'
            return JsonResponse({
                'success': True,
                'content': content_b64,
                'filename': filename
            })
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"创建漏洞库包失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_vul_upgrade(request):
    """升级漏洞库到设备"""
    try:
        file = request.FILES.get('file')
        ip = request.POST.get('ip')
        auto_get_cookie = request.POST.get('auto_get_cookie', 'false') == 'true'

        if not file or not ip:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_content = file.read()

        from main.knowledge_utils import upgrade_vul_to_device

        success, result = upgrade_vul_to_device(ip, file_content, auto_get_cookie=auto_get_cookie)

        if success:
            return JsonResponse({'success': True, 'response': result})
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"升级漏洞库失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 病毒库 API ==========

@require_http_methods(["GET"])
def api_virus_templates(request):
    """获取病毒库模板列表"""
    templates = []
    for f in VIRUS_TEMPLATE_DIR.glob('*.zip'):
        stat = f.stat()
        templates.append({
            'name': f.stem,
            'time': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
            'size': stat.st_size
        })
    return JsonResponse({'success': True, 'templates': templates})


@require_http_methods(["GET"])
def api_virus_template_get(request, name):
    """获取病毒库模板文件"""
    try:
        file_path = VIRUS_TEMPLATE_DIR / f'{name}.zip'
        if not file_path.exists():
            return JsonResponse({'success': False, 'error': '模板不存在'})

        with open(file_path, 'rb') as f:
            response = JsonResponse(f.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{name}.zip"'
            return response
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_virus_template_save(request):
    """保存病毒库模板"""
    try:
        name = request.POST.get('name')
        file = request.FILES.get('file')

        if not name or not file:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_path = VIRUS_TEMPLATE_DIR / f'{name}.zip'
        with open(file_path, 'wb') as f:
            for chunk in file.chunks():
                f.write(chunk)

        return JsonResponse({'success': True, 'message': '保存成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_virus_template_delete(request):
    """删除病毒库模板"""
    try:
        data = json.loads(request.body)
        name = data.get('name')

        file_path = VIRUS_TEMPLATE_DIR / f'{name}.zip'
        if file_path.exists():
            file_path.unlink()

        return JsonResponse({'success': True, 'message': '删除成功'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_virus_create(request):
    """创建病毒库升级包"""
    try:
        file = request.FILES.get('file')
        vul_time = request.POST.get('vul_time')
        version = request.POST.get('version')

        if not file or not vul_time or not version:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        zip_content = file.read()

        from main.knowledge_utils import create_virus_package

        success, result = create_virus_package(zip_content, vul_time, version)

        if success:
            content_b64 = base64.b64encode(result).decode('utf-8')
            filename = 'virus.lib'
            return JsonResponse({
                'success': True,
                'content': content_b64,
                'filename': filename
            })
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"创建病毒库包失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_virus_upgrade(request):
    """升级病毒库到设备"""
    try:
        file = request.FILES.get('file')
        ip = request.POST.get('ip')
        auto_get_cookie = request.POST.get('auto_get_cookie', 'false') == 'true'

        if not file or not ip:
            return JsonResponse({'success': False, 'error': '缺少参数'})

        file_content = file.read()

        from main.knowledge_utils import upgrade_virus_to_device

        success, result = upgrade_virus_to_device(ip, file_content, auto_get_cookie=auto_get_cookie)

        if success:
            return JsonResponse({'success': True, 'response': result})
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"升级病毒库失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 授权管理 ==========

def license_management(request):
    """授权管理页面"""
    return render(request, 'license_management.html')


@require_http_methods(["POST"])
@csrf_exempt
def api_license_verify_password(request):
    """验证授权管理密码"""
    try:
        data = json.loads(request.body)
        password = data.get('password')

        if password == ADMIN_PASSWORD:
            return JsonResponse({'success': True, 'message': '验证成功'})
        else:
            return JsonResponse({'success': False, 'error': '密码错误'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_license_knowledge_generate(request):
    """生成知识库授权"""
    try:
        data = json.loads(request.body)
        machine_code = data.get('machine_code')
        vul_expire = data.get('vul_expire', 1)
        virus_expire = data.get('virus_expire', 1)
        rules_expire = data.get('rules_expire', 1)

        if not machine_code:
            return JsonResponse({'success': False, 'error': '缺少机器码'})

        from main.license_utils import generate_knowledge_license

        success, result = generate_knowledge_license(
            machine_code, vul_expire, virus_expire, rules_expire
        )

        if success:
            return JsonResponse({
                'success': True,
                'filename': result.get('filename'),
                'content': result.get('content'),
                'message': result.get('message', '生成成功')
            })
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"生成知识库授权失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_license_knowledge_decrypt(request):
    """解密知识库授权"""
    try:
        data = json.loads(request.body)
        file_path = data.get('file_path')

        if not file_path:
            return JsonResponse({'success': False, 'error': '缺少文件路径'})

        from main.license_utils import decrypt_knowledge_license

        success, result = decrypt_knowledge_license(file_path)

        if success:
            return JsonResponse({'success': True, 'content': result})
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"解密授权失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_license_device_test_connection(request):
    """测试设备授权服务器连接"""
    try:
        from main.license_utils import test_device_license_connection

        success, result = test_device_license_connection()

        return JsonResponse({'success': success, 'message': result})
    except Exception as e:
        logger.exception(f"测试连接失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_license_device_generate(request):
    """生成设备授权"""
    try:
        data = json.loads(request.body)
        auth_name = data.get('name', '')
        machine_code = data.get('machine_code')
        method = data.get('method', 'DR')

        if not machine_code:
            return JsonResponse({'success': False, 'error': '缺少机器码'})

        from main.license_utils import generate_device_license

        success, result = generate_device_license(auth_name, machine_code)

        if success:
            # 将二进制内容转为数组返回（前端处理）
            content_bytes = list(result.get('content', b''))
            return JsonResponse({
                'success': True,
                'filename': result.get('filename'),
                'content': content_bytes,
                'message': result.get('message', '生成成功')
            })
        else:
            return JsonResponse({'success': False, 'error': result})
    except Exception as e:
        logger.exception(f"生成设备授权失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})