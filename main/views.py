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

from main.models import NetworkInterface, LocalAgent, TestDevice, AgentStatistics, DeviceAlertStatus
from main.syslog_server import (
    start_syslog_server, stop_syslog_server, get_syslog_status,
    get_syslog_logs, clear_syslog_logs, set_syslog_filter_ip
)
from main.snmp_utils import (
    snmp_get, snmp_walk, start_trap_receiver, stop_trap_receiver,
    get_trap_receiver_status, get_trap_receiver_traps, clear_trap_receiver_traps,
    check_snmp_tools
)

# 尝试导入设备监控模块
try:
    from main.device_utils import (
        get_cpu_info, get_memory_info, get_network_info, get_disk_info,
        get_coredump_files, execute_in_vtysh, execute_in_backend, test_ssh_connection
    )
    from main.device_monitor_task import (
        start_device_monitoring, stop_device_monitoring, is_device_monitoring,
        get_monitoring_status, get_alert_config, update_alert_config
    )
    from main.email_utils import send_alert_email, format_alert_email_content
    DEVICE_MONITORING_AVAILABLE = True
except ImportError as e:
    logger.warning(f"设备监控模块导入失败: {e}")
    DEVICE_MONITORING_AVAILABLE = False
    # 提供空函数作为后备
    def start_device_monitoring(*args, **kwargs): pass
    def stop_device_monitoring(*args, **kwargs): pass
    def is_device_monitoring(*args, **kwargs): return False
    def get_monitoring_status(*args, **kwargs): return {}
    def get_alert_config(*args, **kwargs): return {}
    def update_alert_config(*args, **kwargs): return False

logger = logging.getLogger('main')

# ========== Network Namespace 辅助函数 ==========

def get_namespace_list():
    """获取所有 namespace 列表"""
    try:
        result = subprocess.run(
            ['ip', 'netns', 'list'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            # 输出格式: ns-eth1 (id: 1) ns-eth2 (id: 2)
            namespaces = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    ns_name = line.split()[0]
                    namespaces.append(ns_name)
            return namespaces
    except Exception as e:
        logger.warning(f"获取namespace列表失败: {e}")
    return []


def scan_namespace_interfaces(namespace):
    """扫描指定 namespace 内的网卡"""
    interfaces = []
    try:
        # 获取 namespace 内所有网卡
        result = subprocess.run(
            ['sudo', 'ip', 'netns', 'exec', namespace, 'ip', 'link', 'show'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return interfaces

        # 解析 ip link show 输出
        lines = result.stdout.split('\n')
        for i, line in enumerate(lines):
            # 匹配网卡行: "3: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP>..."
            if ':' in line and 'link/ether' in lines[i+1] if i+1 < len(lines) else False:
                parts = line.split(':')
                if len(parts) >= 2:
                    # 提取网卡名（去掉后面的状态）
                    iface_name = parts[1].strip().split('@')[0].strip()

                    # 获取 MAC 地址
                    mac_line = lines[i+1] if i+1 < len(lines) else ''
                    mac_match = mac_line.split('link/ether')[1].strip().split()[0] if 'link/ether' in mac_line else ''

                    # 获取 IP 地址
                    ip_result = subprocess.run(
                        ['sudo', 'ip', 'netns', 'exec', namespace, 'ip', 'addr', 'show', iface_name],
                        capture_output=True, text=True, timeout=5
                    )
                    ipv4 = None
                    if ip_result.returncode == 0:
                        for ip_line in ip_result.stdout.split('\n'):
                            if 'inet ' in ip_line:
                                ipv4 = ip_line.split('inet ')[1].split()[0].split('/')[0]

                    # 判断是否有网线连接（carrier 状态）
                    # LOWER_UP 表示有物理连接，NO-CARRIER 表示无连接
                    has_carrier = 'LOWER_UP' in line and 'NO-CARRIER' not in line
                    is_up = has_carrier  # 根据网线连接判断 UP/DOWN

                    interfaces.append({
                        'name': iface_name,
                        'namespace': namespace,
                        'ip_address': ipv4,
                        'mac_address': mac_match,
                        'is_up': is_up,
                        'status': 'UP' if is_up else 'DOWN',
                        'is_management': False,  # namespace 内网卡都不是管理网卡
                    })
    except Exception as e:
        logger.warning(f"扫描namespace {namespace} 失败: {e}")

    return interfaces


def interface_in_namespace(interface_name):
    """检查网卡是否在某个 namespace 内"""
    try:
        result = subprocess.run(
            ['ip', 'link', 'show', interface_name],
            capture_output=True, timeout=5
        )
        # 如果在主 namespace 能看到网卡，返回 False
        # 如果看不到（returncode != 0），说明网卡在 namespace 内
        return result.returncode != 0
    except:
        return False


def exec_in_namespace(namespace, command):
    """在指定 namespace 内执行命令"""
    if namespace:
        full_cmd = ['ip', 'netns', 'exec', namespace] + command
        return subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
    else:
        return subprocess.run(command, capture_output=True, text=True, timeout=30)


def check_namespace_agent_status(namespace, ip_address, port, service_name=None):
    """检查 namespace 内 Agent 状态

    优先使用 systemd 服务状态检查（更可靠），其次用 HTTP 健康检查
    """
    try:
        # 方法1: 使用 systemctl 检查服务状态（需要 sudo，已配置免密码）
        if service_name:
            result = subprocess.run(
                ['sudo', 'systemctl', 'is-active', service_name],
                capture_output=True, text=True, timeout=10
            )
            if result.stdout.strip() == 'active':
                return 'running'

        # 方法2: 使用 ip netns exec curl 健康检查（备用）
        result = subprocess.run(
            ['sudo', 'ip', 'netns', 'exec', namespace, 'curl', '-s', '--max-time', '3',
             f'http://{ip_address}:{port}/api/health'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and 'success' in result.stdout:
            return 'running'
    except:
        pass
    return 'stopped'


def namespace_http_request(namespace, ip_address, port, method='GET', endpoint='', data=None, timeout=10):
    """在 namespace 内执行 HTTP 请求

    Args:
        namespace: namespace 名称（如 ns-eth1）
        ip_address: Agent IP 地址
        port: Agent 端口
        method: HTTP 方法（GET/POST）
        endpoint: API 路径（如 /api/status）
        data: POST 数据（JSON dict）
        timeout: 超时时间（秒）

    Returns:
        tuple: (success, response_data, error_message)
    """
    import json

    url = f'http://{ip_address}:{port}{endpoint}'
    cmd = ['sudo', 'ip', 'netns', 'exec', namespace, 'curl', '-s', '--max-time', str(timeout)]

    if method == 'POST':
        cmd.extend(['-X', 'POST', '-H', 'Content-Type: application/json'])
        if data:
            cmd.extend(['-d', json.dumps(data)])

    cmd.append(url)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if result.returncode == 0 and result.stdout:
            return True, json.loads(result.stdout), None
        return False, None, result.stderr or 'HTTP request failed'
    except subprocess.TimeoutExpired:
        return False, None, 'Timeout'
    except json.JSONDecodeError:
        return False, None, 'Invalid JSON response'
    except Exception as e:
        return False, None, str(e)


def forward_to_agent(agent, method='GET', endpoint='', data=None, timeout=10):
    """转发请求到 Agent（支持 namespace）

    Args:
        agent: LocalAgent 对象
        method: HTTP 方法
        endpoint: API 路径
        data: POST 数据
        timeout: 超时时间

    Returns:
        tuple: (success, response_data, error_message)
    """
    ns = agent.interface.namespace
    ip = agent.interface.ip_address
    port = agent.port

    if ns:
        # namespace 内 Agent
        return namespace_http_request(ns, ip, port, method, endpoint, data, timeout)
    else:
        # 主 namespace Agent
        try:
            url = f'http://{ip}:{port}{endpoint}'
            if method == 'GET':
                resp = requests.get(url, timeout=timeout)
            else:
                resp = requests.post(url, json=data, timeout=timeout)

            if resp.status_code == 200:
                return True, resp.json(), None
            return False, None, f'HTTP {resp.status_code}'
        except requests.exceptions.Timeout:
            return False, None, 'Timeout'
        except Exception as e:
            return False, None, str(e)

# ========== 系统信息 API ==========

@require_http_methods(["GET"])
def api_system_info(request):
    """获取当前服务器系统信息（CPU、内存、磁盘）"""
    try:
        import psutil

        # CPU 信息
        cpu_usage = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()

        # 内存信息
        memory = psutil.virtual_memory()
        memory_usage = memory.percent
        memory_used = memory.used // (1024 * 1024)  # MB
        memory_total = memory.total // (1024 * 1024)  # MB

        # 磁盘信息
        disk = psutil.disk_usage('/')
        disk_usage = disk.percent
        disk_used = disk.used // (1024 * 1024 * 1024)  # GB
        disk_total = disk.total // (1024 * 1024 * 1024)  # GB

        # 网络信息
        net_io = psutil.net_io_counters()
        net_rx = net_io.bytes_recv
        net_tx = net_io.bytes_sent

        return JsonResponse({
            'success': True,
            'cpu': {
                'usage': cpu_usage,
                'count': cpu_count,
                'freq': cpu_freq.current if cpu_freq else None,
            },
            'memory': {
                'usage': memory_usage,
                'used': memory_used,
                'total': memory_total,
            },
            'disk': {
                'usage': disk_usage,
                'used': disk_used,
                'total': disk_total,
            },
            'network': {
                'rx_bytes': net_rx,
                'tx_bytes': net_tx,
            }
        })
    except Exception as e:
        logger.exception(f"获取系统信息失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 页面视图 ==========

def home(request):
    """首页"""
    agents = LocalAgent.objects.all()
    devices = TestDevice.objects.all()
    interfaces = NetworkInterface.objects.all()

    context = {
        'agents': agents,
        'devices': devices,
        'interfaces': interfaces,
        'interface_count': interfaces.count(),
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


def service_deploy(request):
    """服务下发页面"""
    agents = LocalAgent.objects.filter(status='running')
    context = {'agents': agents}
    return render(request, 'service_deploy.html', context)


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
    """扫描系统网卡（包括主namespace和子namespace）"""
    try:
        import psutil
        import socket

        interfaces = []

        # ========== 1. 扫描主 namespace 网卡（psutil）==========
        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()

        for name, addrs in net_if_addrs.items():
            # 跳过回环接口
            if name.startswith('lo') or name.lower() == 'loopback':
                continue

            # 获取 IPv4 地址（可能为空）
            ipv4 = None
            mac = None
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ipv4 = addr.address
                elif hasattr(psutil, 'PF_LINK') and addr.family == psutil.PF_LINK:
                    mac = addr.address
                elif addr.family == socket.AF_PACKET:
                    mac = addr.address

            # 获取网卡状态
            stats = net_if_stats.get(name)
            # admin_up: 管理状态（接口是否被启用）
            admin_up = stats.isup if stats else False
            speed = stats.speed if stats and stats.speed > 0 else None

            # 获取 carrier 状态（物理连接）- 通过 ip link show
            has_carrier = False
            try:
                carrier_result = subprocess.run(
                    ['ip', 'link', 'show', name],
                    capture_output=True, text=True, timeout=2
                )
                if carrier_result.returncode == 0:
                    # LOWER_UP 表示有物理连接，NO-CARRIER 表示无连接
                    has_carrier = 'LOWER_UP' in carrier_result.stdout and 'NO-CARRIER' not in carrier_result.stdout
            except:
                has_carrier = admin_up  # fallback 到管理状态

            is_up = has_carrier  # 根据网线连接判断 UP/DOWN

            # 判断是否是管理网卡
            is_management = (name == settings.MANAGEMENT_INTERFACE)

            interfaces.append({
                'name': name,
                'ip_address': ipv4 or '',
                'mac_address': mac or '',
                'speed': speed,
                'is_management': is_management,
                'is_up': is_up,
                'status': 'UP' if is_up else 'DOWN',
                'namespace': None,  # 主 namespace
            })

        # ========== 2. 扫描子 namespace 网卡（ip netns exec）==========
        namespaces = get_namespace_list()
        for ns in namespaces:
            ns_interfaces = scan_namespace_interfaces(ns)
            for iface in ns_interfaces:
                interfaces.append(iface)

        # ========== 3. 保存到数据库（更新 namespace 字段）==========
        for iface in interfaces:
            NetworkInterface.objects.update_or_create(
                name=iface['name'],
                defaults={
                    'ip_address': iface['ip_address'] or None,
                    'mac_address': iface.get('mac_address', ''),
                    'speed': iface.get('speed'),
                    'is_management': iface['is_management'],
                    'is_available': iface['is_up'],
                    'is_up': iface['is_up'],
                    'status': iface['status'],
                    'namespace': iface.get('namespace'),  # 新增：保存 namespace
                }
            )

        # 按 eth0, eth1, eth2... 顺序排序，namespace 内网卡排在后面
        def interface_sort_key(iface):
            name = iface['name']
            # 管理网卡 (eth0) 排在最前面
            if iface['is_management']:
                return (0, 0, 0)
            # namespace 内网卡排后面
            ns_order = 2 if iface.get('namespace') else 1
            # 其他网卡按数字排序
            if name.startswith('eth') and len(name) > 3:
                try:
                    num = int(name[3:])
                    return (ns_order, num, iface.get('namespace') or '')
                except:
                    return (ns_order, 999, iface.get('namespace') or '')
            # 非 eth 开头的网卡排最后
            return (3, 0, name)

        interfaces.sort(key=interface_sort_key)

        logger.info(f"扫描到 {len(interfaces)} 个网卡 (主namespace: {len([i for i in interfaces if not i.get('namespace')])}, 子namespace: {len([i for i in interfaces if i.get('namespace')])})")

        return JsonResponse({
            'success': True,
            'interfaces': interfaces,
            'count': len(interfaces),
            'management_interface': settings.MANAGEMENT_INTERFACE,
            'namespaces': namespaces,
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
            'ip_address': iface.ip_address or '',
            'mac_address': iface.mac_address,
            'speed': iface.speed,
            'is_management': iface.is_management,
            'is_available': iface.is_available,
            'is_up': iface.is_up,
            'status': iface.status,
            'namespace': iface.namespace,
            'has_agent': agent is not None,
            'agent_id': agent.agent_id if agent else None,
            'agent_status': agent.status if agent else None,
        })

    # 按 eth0, eth1, eth2... 顺序排序
    def interface_sort_key(iface):
        name = iface['name']
        # 管理网卡 (eth0) 排在最前面
        if iface['is_management']:
            return (0, 0)
        # 其他网卡按数字排序
        if name.startswith('eth') and len(name) > 3:
            try:
                num = int(name[3:])
                return (1, num)
            except:
                return (1, 999)
        # 非 eth 开头的网卡排最后
        return (2, name)

    data.sort(key=interface_sort_key)

    return JsonResponse({
        'interfaces': data,
        'management_interface': settings.MANAGEMENT_INTERFACE
    })


# ========== Agent 管理 API ==========

@require_http_methods(["GET"])
def api_agent_list(request):
    """获取 Agent 列表（支持 namespace）"""
    import psutil
    import socket
    import concurrent.futures

    agents = LocalAgent.objects.all()

    # 先获取主 namespace 网卡实际状态
    net_if_addrs = psutil.net_if_addrs()
    net_if_stats = psutil.net_if_stats()

    # 获取 namespace 内网卡状态
    namespace_interfaces = {}
    namespaces = get_namespace_list()
    for ns in namespaces:
        namespace_interfaces[ns] = {}
        ns_ifaces = scan_namespace_interfaces(ns)
        for iface in ns_ifaces:
            namespace_interfaces[ns][iface['name']] = iface

    # 并行查询 Agent 状态
    def query_agent_status(agent):
        """查询单个 Agent 状态"""
        interface_name = agent.interface.name
        ns = agent.interface.namespace

        actual_ip = None
        actual_is_up = False
        actual_status = 'DOWN'

        # 根据是否在 namespace 内，选择不同的检查方式
        if ns:
            # ========== namespace 内网卡状态 ==========
            if ns in namespace_interfaces and interface_name in namespace_interfaces[ns]:
                ns_iface = namespace_interfaces[ns][interface_name]
                actual_ip = ns_iface.get('ip_address')
                actual_is_up = ns_iface.get('is_up', False)
                actual_status = ns_iface.get('status', 'DOWN')
        else:
            # ========== 主 namespace 网卡状态 ==========
            if interface_name in net_if_addrs:
                addrs = net_if_addrs[interface_name]
                for addr in addrs:
                    if addr.family == socket.AF_INET:
                        actual_ip = addr.address

            if interface_name in net_if_stats:
                stats = net_if_stats[interface_name]
                actual_is_up = stats.isup
                actual_status = 'UP' if stats.isup else 'DOWN'

        # 同步网卡状态到数据库
        if actual_ip and actual_ip != agent.interface.ip_address:
            agent.interface.ip_address = actual_ip
            agent.interface.save()

        if agent.interface.is_up != actual_is_up or agent.interface.status != actual_status:
            agent.interface.is_up = actual_is_up
            agent.interface.status = actual_status
            agent.interface.save()

        # 查询 Agent HTTP 状态
        actual_agent_status = agent.status
        if agent.interface.ip_address:
            if ns:
                # ========== namespace 内 Agent 状态检查 ==========
                service_name = agent.get_namespace_service_name()
                actual_agent_status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
                agent.status = actual_agent_status
                agent.save()
            else:
                # ========== 主 namespace Agent 状态检查 ==========
                try:
                    resp = requests.get(
                        f"http://{agent.interface.ip_address}:{agent.port}/api/status",
                        timeout=1
                    )
                    if resp.status_code == 200:
                        actual_agent_status = 'running'
                        agent.status = 'running'
                        agent.save()
                except:
                    actual_agent_status = 'stopped'
                    if agent.status != 'stopped':
                        agent.status = 'stopped'
                        agent.save()

        return {
            'id': agent.id,
            'agent_id': agent.agent_id,
            'interface_name': agent.interface.name,
            'ip_address': agent.interface.ip_address,
            'mac_address': agent.interface.mac_address,
            'port': agent.port,
            'status': actual_agent_status,
            'interface_status': agent.interface.status,
            'interface_is_up': agent.interface.is_up,
            'namespace': ns,
            'auto_start': agent.auto_start,
            'last_start_time': agent.last_start_time.isoformat() if agent.last_start_time else None,
            'created_at': agent.created_at.isoformat(),
        }

    data = []
    # 使用线程池并行查询
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(query_agent_status, agent): agent for agent in agents}
        for future in concurrent.futures.as_completed(futures, timeout=15):
            try:
                result = future.result(timeout=3)
                data.append(result)
            except:
                agent = futures[future]
                # 超时时使用数据库状态
                data.append({
                    'id': agent.id,
                    'agent_id': agent.agent_id,
                    'interface_name': agent.interface.name,
                    'ip_address': agent.interface.ip_address,
                    'mac_address': agent.interface.mac_address,
                    'port': agent.port,
                    'status': agent.status,
                    'interface_status': agent.interface.status,
                    'interface_is_up': agent.interface.is_up,
                    'namespace': agent.interface.namespace,
                    'auto_start': agent.auto_start,
                    'last_start_time': agent.last_start_time.isoformat() if agent.last_start_time else None,
                    'created_at': agent.created_at.isoformat(),
                })

    return JsonResponse({'agents': data, 'namespaces': namespaces})


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

        # 创建 Agent（允许没有 IP 的网卡创建 Agent，状态为 stopped）
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
    """删除 Agent（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查是否在 namespace 内
        ns = agent.interface.namespace

        if ns:
            service_name = agent.get_namespace_service_name()
        else:
            service_name = agent.get_service_name()

        # 停止 systemd 服务
        subprocess.run(['sudo', 'systemctl', 'stop', service_name], timeout=10, capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'disable', service_name], timeout=10, capture_output=True)

        # 删除 systemd 服务文件
        service_file = f'/etc/systemd/system/{service_name}.service'
        subprocess.run(['sudo', 'rm', '-f', service_file], timeout=5, capture_output=True)
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10, capture_output=True)

        # 删除数据库记录
        interface_name = agent.interface.name
        agent.delete()

        logger.info(f"删除 Agent: {agent_id} (namespace: {ns or 'main'})")

        return JsonResponse({
            'success': True,
            'message': f'Agent {agent_id} 已删除',
            'interface_name': interface_name,
            'namespace': ns
        })

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"删除 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_start(request):
    """启动 Agent（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查网卡是否有 IP 地址
        if not agent.interface.ip_address:
            return JsonResponse({
                'success': False,
                'error': '网卡未配置 IP 地址，请先配置 IP',
                'need_config_ip': True
            })

        # 检查是否在 namespace 内
        ns = agent.interface.namespace

        if ns:
            # ========== namespace 内 Agent 启动 ==========
            service_name = agent.get_namespace_service_name()
            service_file = f'/etc/systemd/system/{service_name}.service'

            # 创建 namespace 服务文件
            service_content = f"""[Unit]
Description=Packet Agent {agent.agent_id} (in namespace {ns})
After=network.target
Requires=network.target

[Service]
Type=simple
WorkingDirectory={settings.AGENT_WORK_DIR}
ExecStart=/usr/bin/ip netns exec {ns} {settings.AGENT_VENV_PYTHON} -m gunicorn -w 1 -b {agent.interface.ip_address}:{agent.port} --preload --timeout 30 agents.full_agent:app
ExecStop=/usr/bin/ip netns exec {ns} {settings.AGENT_VENV_PYTHON} -c "import sys; sys.exit(0)"
Restart=always
RestartSec=5
StandardOutput=append:{settings.AGENT_WORK_DIR}/logs/agent_{agent.interface.name}_ns.log
StandardError=append:{settings.AGENT_WORK_DIR}/logs/agent_{agent.interface.name}_ns.log

LimitNOFILE=65535
TimeoutStartSec=30
TimeoutStopSec=10

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
                ['sudo', 'systemctl', 'start', service_name],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                time.sleep(3)
                # 通过 namespace 检查状态
                service_name = agent.get_namespace_service_name()
                status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
                if status == 'running':
                    agent.status = 'running'
                    agent.last_start_time = datetime.now()
                    agent.save()
                    logger.info(f"Agent {agent_id} (namespace {ns}) 启动成功")
                    return JsonResponse({'success': True, 'status': 'running', 'namespace': ns})
                else:
                    agent.status = 'starting'
                    agent.save()
                    return JsonResponse({'success': True, 'status': 'starting', 'namespace': ns})
            else:
                logger.error(f"启动 Agent {agent_id} 失败: {result.stderr}")
                return JsonResponse({'success': False, 'error': result.stderr})

        else:
            # ========== 主 namespace Agent 启动（原有逻辑）==========
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
ExecStart={settings.AGENT_VENV_PYTHON} -m gunicorn -w 1 -b {agent.interface.ip_address}:{agent.port} --preload --timeout 30 agents.full_agent:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""

            subprocess.run(
                ['sudo', 'tee', service_file],
                input=service_content,
                capture_output=True,
                text=True,
                timeout=10
            )

            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10, capture_output=True)

            result = subprocess.run(
                ['sudo', 'systemctl', 'start', agent.get_service_name()],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                time.sleep(3)
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
    """停止 Agent（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查是否在 namespace 内
        ns = agent.interface.namespace

        if ns:
            # namespace 内 Agent 使用 namespace 服务名
            service_name = agent.get_namespace_service_name()
        else:
            # 主 namespace Agent 使用普通服务名
            service_name = agent.get_service_name()

        result = subprocess.run(
            ['sudo', 'systemctl', 'stop', service_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            agent.status = 'stopped'
            agent.last_stop_time = datetime.now()
            agent.save()
            logger.info(f"Agent {agent_id} 已停止 (namespace: {ns or 'main'})")
            return JsonResponse({'success': True, 'status': 'stopped', 'namespace': ns})
        else:
            return JsonResponse({'success': False, 'error': result.stderr})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"停止 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_agent_status(request):
    """查询 Agent 状态（支持 namespace）"""
    agent_id = request.GET.get('agent_id')

    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 通过 HTTP 查询实际状态（支持 namespace）
        # 增加超时时间，避免发送报文时误判为停止
        success, status_data, err = forward_to_agent(agent, 'GET', '/api/status', timeout=5)

        if success:
            agent.status = 'running'
            agent.save()

            # 获取统计信息
            statistics = {}
            try:
                success2, stats_data, err2 = forward_to_agent(agent, 'GET', '/api/statistics', timeout=5)
                if success2:
                    statistics = stats_data.get('statistics', {})
            except:
                pass

            return JsonResponse({
                'success': True,
                'agent_id': agent_id,
                'status': 'running',
                'uptime': status_data.get('uptime'),
                'interface': agent.interface.name,
                'statistics': statistics,
            })

        # 查询失败时，保持数据库原有状态，不强制更新为 stopped
        return JsonResponse({
            'success': True,
            'agent_id': agent_id,
            'status': agent.status,
            'interface': agent.interface.name,
            'statistics': {},
            'query_failed': True,
        })

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})


@require_http_methods(["GET"])
def api_agent_logs(request):
    """获取 Agent 日志（支持 namespace）"""
    agent_id = request.GET.get('agent_id')
    lines = int(request.GET.get('lines', 50))

    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)

        # namespace Agent 使用对应的服务名
        if agent.is_in_namespace():
            service_name = agent.get_namespace_service_name()
        else:
            service_name = agent.get_service_name()

        result = subprocess.run(
            ['sudo', 'journalctl', '-u', service_name, '-n', str(lines), '--no-pager'],
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


@require_http_methods(["POST"])
@csrf_exempt
def api_agent_config_ip(request):
    """配置 Agent 网卡的 IP 地址（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        ip_address = data.get('ip_address')
        netmask = data.get('netmask', '24')

        if not agent_id or not ip_address:
            return JsonResponse({'success': False, 'error': '缺少 Agent ID 或 IP 地址'})

        agent = LocalAgent.objects.get(agent_id=agent_id)
        interface_name = agent.interface.name
        namespace = agent.interface.namespace

        # 检查 Agent 是否正在运行
        if agent.status == 'running':
            return JsonResponse({'success': False, 'error': 'Agent 正在运行，请先停止 Agent'})

        # 获取命令执行函数（考虑 namespace）
        def run_ip_cmd(args):
            if namespace:
                return exec_in_namespace(namespace, ['sudo'] + args)
            else:
                return subprocess.run(['sudo'] + args, capture_output=True, text=True, timeout=10)

        # 先启动网卡
        run_ip_cmd(['ip', 'link', 'set', interface_name, 'up'])

        # 清空网卡上所有旧的 IP 地址
        run_ip_cmd(['ip', 'addr', 'flush', 'dev', interface_name])

        # 配置新的 IP 地址（CIDR 格式）
        cidr = f"{ip_address}/{netmask}"
        result = run_ip_cmd(['ip', 'addr', 'add', cidr, 'dev', interface_name])

        if namespace or result.returncode == 0 or 'File exists' in result.stderr:
            # 更新数据库
            agent.interface.ip_address = ip_address
            agent.interface.is_up = True
            agent.interface.status = 'UP'
            agent.interface.save()

            # 更新 systemd 服务配置文件（namespace 时使用 -ns 后缀）
            if namespace:
                service_name = agent.get_namespace_service_name()
                exec_prefix = f'/usr/bin/ip netns exec {namespace}'
            else:
                service_name = agent.get_service_name()
                exec_prefix = ''

            service_file = f'/etc/systemd/system/{service_name}.service'
            # 使用 Gunicorn 启动（单 worker + preload）
            if exec_prefix:
                exec_start = f'{exec_prefix} {settings.AGENT_VENV_PYTHON} -m gunicorn -w 1 -b {ip_address}:{agent.port} --preload --timeout 30 agents.full_agent:app'
            else:
                exec_start = f'{settings.AGENT_VENV_PYTHON} -m gunicorn -w 1 -b {ip_address}:{agent.port} --preload --timeout 30 agents.full_agent:app'
            service_content = f"""[Unit]
Description=Packet Agent {agent.agent_id} ({interface_name})
After=network.target

[Service]
Type=simple
Environment="AGENT_ID={agent.agent_id}"
Environment="BIND_IP={ip_address}"
Environment="BIND_INTERFACE={interface_name}"
Environment="AGENT_PORT={agent.port}"
WorkingDirectory={settings.AGENT_WORK_DIR}
ExecStart={exec_start}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
            subprocess.run(
                ['sudo', 'tee', service_file],
                input=service_content,
                capture_output=True,
                text=True,
                timeout=10
            )
            subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10, capture_output=True)

            logger.info(f"Agent {agent_id} 网卡 {interface_name} 配置 IP: {cidr}, 已更新服务配置")
            return JsonResponse({
                'success': True,
                'message': f'网卡 {interface_name} 已配置 IP: {cidr}，服务配置已同步更新',
                'ip_address': ip_address
            })
        else:
            logger.error(f"配置 IP 失败: {result.stderr}")
            return JsonResponse({'success': False, 'error': result.stderr})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"配置 Agent IP 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== Agent 租用管理 API ==========

INACTIVITY_TIMEOUT_HOURS = 2  # 无活动 2 小时后自动释放


def check_and_release_expired_locks():
    """检查并释放过期的租用（基于活跃时间）"""
    from django.utils import timezone
    from .models import AgentLock

    # 查找所有活跃租用
    active_locks = AgentLock.objects.filter(status='active')

    for lock in active_locks:
        if lock.is_expired():  # 使用模型的 is_expired 方法（基于 last_activity_at）
            lock.status = 'expired'
            lock.released_at = timezone.now()
            lock.save()
            logger.info(f"租用过期自动释放: {lock.user_identifier} ({lock.client_ip}), 无活动超过 {INACTIVITY_TIMEOUT_HOURS} 小时")


def update_lock_activity(user_identifier):
    """更新用户租用的活跃时间"""
    from .models import AgentLock

    try:
        lock = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        ).first()
        if lock:
            lock.update_activity()
            logger.debug(f"更新租用活跃时间: {user_identifier}")
    except Exception as e:
        logger.warning(f"更新租用活跃时间失败: {e}")


@require_http_methods(["POST"])
@csrf_exempt
def api_agents_lock(request):
    """租用 Agent 组"""
    from django.utils import timezone
    from .models import AgentLock, LocalAgent

    try:
        # 先检查并释放过期的租用
        check_and_release_expired_locks()

        data = json.loads(request.body)
        user_identifier = data.get('user_identifier', '').strip()
        agent_ids = data.get('agent_ids', [])  # 要租用的 agent_id 列表
        client_ip = request.META.get('REMOTE_ADDR', '')  # 获取客户端 IP

        if not user_identifier:
            return JsonResponse({'success': False, 'error': '请输入用户标识符'})

        if not agent_ids or len(agent_ids) == 0:
            return JsonResponse({'success': False, 'error': '请选择要租用的 Agent'})

        # 检查该用户是否已有活跃租用
        existing_lock = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        ).first()

        if existing_lock:
            # 如果已有租用，更新活跃时间并返回提示
            existing_lock.update_activity()
            return JsonResponse({
                'success': False,
                'error': f'您已有租用记录，使用中会自动续期',
                'existing_lock': {
                    'lock_id': existing_lock.id,
                    'locked_agents': [a.agent_id for a in existing_lock.agents.all()],
                    'last_activity_at': existing_lock.last_activity_at.isoformat(),
                    'remaining_seconds': existing_lock.get_remaining_time()
                }
            })

        # 检查要租用的 Agent 是否已被其他人租用
        locked_agent_ids = []
        for agent_id in agent_ids:
            try:
                agent = LocalAgent.objects.get(agent_id=agent_id)
                # 检查是否被活跃租用锁定
                is_locked = AgentLock.objects.filter(
                    status='active',
                    agents__id=agent.id
                ).exists()
                if is_locked:
                    locked_agent_ids.append(agent_id)
            except LocalAgent.DoesNotExist:
                return JsonResponse({'success': False, 'error': f'Agent {agent_id} 不存在'})

        if locked_agent_ids:
            return JsonResponse({
                'success': False,
                'error': f'以下 Agent 已被租用: {", ".join(locked_agent_ids)}'
            })

        # 创建租用记录（基于活跃时间，无固定过期时间）
        from django.utils import timezone
        lock = AgentLock.objects.create(
            user_identifier=user_identifier,
            client_ip=client_ip,
            status='active',
            last_activity_at=timezone.now()
        )

        # 关联 Agent
        for agent_id in agent_ids:
            agent = LocalAgent.objects.get(agent_id=agent_id)
            lock.agents.add(agent)

        logger.info(f"租用成功: {user_identifier} ({client_ip}) 租用 Agent: {agent_ids}, 无活动 {INACTIVITY_TIMEOUT_HOURS} 小时后自动释放")

        return JsonResponse({
            'success': True,
            'message': f'租用成功，使用中自动续期，无活动 {INACTIVITY_TIMEOUT_HOURS} 小时后自动释放',
            'lock': {
                'lock_id': lock.id,
                'user_identifier': lock.user_identifier,
                'client_ip': lock.client_ip,
                'locked_agents': agent_ids,
                'locked_at': lock.locked_at.isoformat(),
                'last_activity_at': lock.last_activity_at.isoformat(),
                'remaining_seconds': lock.get_remaining_time()
            }
        })

    except Exception as e:
        logger.exception(f"租用 Agent 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agents_unlock(request):
    """释放租用（租用管理页面使用，需要 user_identifier）"""
    from django.utils import timezone
    from .models import AgentLock

    try:
        # 先检查并释放过期的租用
        check_and_release_expired_locks()

        data = json.loads(request.body)
        user_identifier = data.get('user_identifier', '').strip()

        if not user_identifier:
            return JsonResponse({'success': False, 'error': '请输入用户标识符'})

        # 查找该用户的活跃租用
        locks = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        )

        if not locks.exists():
            # 检查是否有已过期但未标记的租用
            expired_locks = AgentLock.objects.filter(
                user_identifier=user_identifier,
                status='expired'
            )
            if expired_locks.exists():
                return JsonResponse({'success': False, 'error': '您的租用已过期，无需手动释放'})
            return JsonResponse({'success': False, 'error': '没有找到您的租用记录'})

        # 释放所有租用
        released_agents = []
        for lock in locks:
            released_agents.extend([a.agent_id for a in lock.agents.all()])
            lock.status = 'released'
            lock.released_at = timezone.now()
            lock.save()

        logger.info(f"手动释放租用: {user_identifier}, Agent: {released_agents}")

        return JsonResponse({
            'success': True,
            'message': f'已释放租用的 Agent: {", ".join(released_agents)}'
        })

    except Exception as e:
        logger.exception(f"释放租用失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_agents_locks(request):
    """获取所有租用记录"""
    from .models import AgentLock

    try:
        # 先检查并释放过期的租用
        check_and_release_expired_locks()

        locks = AgentLock.objects.filter(status='active').order_by('-locked_at')

        data = []
        for lock in locks:
            data.append({
                'lock_id': lock.id,
                'user_identifier': lock.user_identifier,
                'client_ip': lock.client_ip,
                'locked_agents': [a.agent_id for a in lock.agents.all()],
                'locked_at': lock.locked_at.isoformat(),
                'last_activity_at': lock.last_activity_at.isoformat() if lock.last_activity_at else lock.locked_at.isoformat(),
                'remaining_seconds': lock.get_remaining_time(),
                'status': lock.status
            })

        return JsonResponse({
            'success': True,
            'locks': data,
            'total': len(data)
        })

    except Exception as e:
        logger.exception(f"获取租用记录失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_agents_my_lock(request):
    """获取指定用户的租用信息（租用管理页面使用）"""
    from .models import AgentLock

    try:
        # 先检查并释放过期的租用
        check_and_release_expired_locks()

        user_identifier = request.GET.get('user_identifier', '').strip()

        if not user_identifier:
            return JsonResponse({'success': True, 'lock': None})

        lock = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        ).first()

        if not lock:
            return JsonResponse({'success': True, 'lock': None})

        return JsonResponse({
            'success': True,
            'lock': {
                'lock_id': lock.id,
                'user_identifier': lock.user_identifier,
                'client_ip': lock.client_ip,
                'locked_agents': [a.agent_id for a in lock.agents.all()],
                'locked_at': lock.locked_at.isoformat(),
                'last_activity_at': lock.last_activity_at.isoformat() if lock.last_activity_at else lock.locked_at.isoformat(),
                'remaining_seconds': lock.get_remaining_time()
            }
        })

    except Exception as e:
        logger.exception(f"获取用户租用信息失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_agents_keepalive(request):
    """更新租用活跃时间（心跳，租用管理页面使用）"""
    try:
        data = json.loads(request.body)
        user_identifier = data.get('user_identifier', '').strip()

        if not user_identifier:
            return JsonResponse({'success': False, 'error': '请输入用户标识符'})

        # 检查并释放过期租用
        check_and_release_expired_locks()

        # 更新活跃时间
        update_lock_activity(user_identifier)

        # 返回更新后的状态
        from .models import AgentLock
        lock = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        ).first()

        if lock:
            return JsonResponse({
                'success': True,
                'message': '活跃时间已更新',
                'remaining_seconds': lock.get_remaining_time()
            })
        else:
            return JsonResponse({'success': False, 'error': '没有活跃的租用记录'})

    except Exception as e:
        logger.exception(f"更新活跃时间失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_agents_my_rented(request):
    """获取当前 IP 租用的 Agent 列表（用于其他页面选择 Agent）

    返回所有租用的 Agent，实时查询状态，包括发送状态
    """
    from .models import AgentLock, LocalAgent
    import concurrent.futures

    try:
        # 先检查并释放过期的租用
        check_and_release_expired_locks()

        # 自动获取客户端 IP
        client_ip = request.META.get('REMOTE_ADDR', '')

        if not client_ip:
            return JsonResponse({
                'success': True,
                'agents': [],
                'client_ip': '',
                'has_rental': False
            })

        # 按 IP 查找活跃租用
        lock = AgentLock.objects.filter(
            client_ip=client_ip,
            status='active'
        ).first()

        if not lock:
            return JsonResponse({
                'success': True,
                'agents': [],
                'client_ip': client_ip,
                'has_rental': False,
                'message': f'当前 IP ({client_ip}) 无租用记录，请在 Agent 管理页面租用 Agent'
            })

        # 并行查询所有 Agent 状态
        def query_agent_status(agent):
            actual_status = agent.status
            is_sending = False
            send_rate = 0
            send_total = 0
            ns = agent.interface.namespace

            if agent.interface.ip_address:
                if ns:
                    # ========== namespace 内 Agent 状态检查 ==========
                    service_name = agent.get_namespace_service_name()
                    actual_status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)

                    # 使用 namespace 内 curl 查询统计数据
                    if actual_status == 'running':
                        try:
                            result = subprocess.run(
                                ['sudo', 'ip', 'netns', 'exec', ns, 'curl', '-s', '--max-time', '3',
                                 f'http://{agent.interface.ip_address}:{agent.port}/api/statistics'],
                                capture_output=True, text=True, timeout=10
                            )
                            if result.returncode == 0:
                                stats = json.loads(result.stdout).get('statistics', {})
                                send_rate = stats.get('rate', 0)
                                send_total = stats.get('total_sent', 0)
                                is_sending = stats.get('sending', False)
                        except:
                            pass
                else:
                    # ========== 主 namespace Agent 状态检查 ==========
                    try:
                        resp = requests.get(
                            f"http://{agent.interface.ip_address}:{agent.port}/api/status",
                            timeout=3
                        )
                        if resp.status_code == 200:
                            actual_status = 'running'

                            stats_resp = requests.get(
                                f"http://{agent.interface.ip_address}:{agent.port}/api/statistics",
                                timeout=3
                            )
                            if stats_resp.status_code == 200:
                                stats = stats_resp.json().get('statistics', {})
                                send_rate = stats.get('rate', 0)
                                send_total = stats.get('total_sent', 0)
                                is_sending = stats.get('sending', False)
                    except:
                        actual_status = 'stopped'

            return {
                'agent_id': agent.agent_id,
                'interface_name': agent.interface.name,
                'ip_address': agent.interface.ip_address or '',
                'mac_address': agent.interface.mac_address,
                'port': agent.port,
                'status': actual_status,
                'is_sending': is_sending,
                'send_rate': send_rate,
                'send_total': send_total,
                'has_ip': bool(agent.interface.ip_address),
                'namespace': ns,
            }

        rented_agents = []
        agents_list = list(lock.agents.all())

        # 使用线程池并行查询（最多5个并行）
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(query_agent_status, agent): agent for agent in agents_list}
            for future in concurrent.futures.as_completed(futures, timeout=15):
                try:
                    result = future.result(timeout=5)
                    rented_agents.append(result)
                    # 同步数据库状态
                    agent = futures[future]
                    if agent.status != result['status']:
                        agent.status = result['status']
                        agent.save(update_fields=['status'])
                except:
                    agent = futures[future]
                    rented_agents.append({
                        'agent_id': agent.agent_id,
                        'interface_name': agent.interface.name,
                        'ip_address': agent.interface.ip_address or '',
                        'mac_address': agent.interface.mac_address,
                        'port': agent.port,
                        'status': 'stopped',
                        'is_sending': False,
                        'send_rate': 0,
                        'send_total': 0,
                        'has_ip': bool(agent.interface.ip_address),
                    })

        # 更新活跃时间
        lock.update_activity()

        # 按 interface_name 排序（eth1, eth2, eth3...）
        def sort_by_interface(agent):
            name = agent.get('interface_name', '')
            # 提取数字部分进行排序
            import re
            match = re.search(r'eth(\d+)', name)
            if match:
                return int(match.group(1))
            return 999  # 没有 eth 的排在后面

        rented_agents.sort(key=sort_by_interface)

        return JsonResponse({
            'success': True,
            'agents': rented_agents,
            'client_ip': client_ip,
            'has_rental': True,
            'user_identifier': lock.user_identifier,
            'remaining_seconds': lock.get_remaining_time()
        })

    except Exception as e:
        logger.exception(f"获取租用 Agent 列表失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 功能 API（代理到 Agent） ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_send_packet(request):
    """发送报文（代理到指定 Agent，支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查 Agent 状态（支持 namespace）
        ns = agent.interface.namespace
        if ns:
            service_name = agent.get_namespace_service_name()
            status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
        else:
            status = agent.status

        if status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        packet_config = data.get('packet_config', {})
        send_config = data.get('send_config', {})

        # 转换前端格式到 Agent 格式
        if 'protocol' not in packet_config:
            if 'tcp_flags' in packet_config:
                packet_config['protocol'] = 'tcp'
            elif 'icmp_type' in packet_config:
                packet_config['protocol'] = 'icmp'
            elif 'arp_type' in packet_config:
                packet_config['protocol'] = 'arp'
            elif 'udp_type' in packet_config:
                packet_config['protocol'] = 'udp'
            else:
                packet_config['protocol'] = 'tcp'

        if 'tcp_flags' in packet_config:
            tcp_flags = packet_config['tcp_flags']
            flags = []
            if tcp_flags.get('syn'):
                flags.append('SYN')
            if tcp_flags.get('ack'):
                flags.append('ACK')
            if tcp_flags.get('fin'):
                flags.append('FIN')
            if tcp_flags.get('rst'):
                flags.append('RST')
            if tcp_flags.get('psh'):
                flags.append('PSH')
            if tcp_flags.get('urg'):
                flags.append('URG')
            packet_config['flags'] = flags
            del packet_config['tcp_flags']

        if 'udp_type' in packet_config:
            udp_type = packet_config['udp_type']
            if udp_type == 'udp_normal':
                packet_config['udp_type'] = 'udp'
            elif udp_type == 'teardrop':
                packet_config['udp_type'] = 'teardrop'

        forward_data = {
            'interface': agent.interface.name,
            'packet_config': packet_config,
            'send_config': send_config
        }

        # 使用统一的转发函数（支持 namespace）
        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/send_packet', forward_data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"发送报文失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_stop_send(request):
    """停止发送报文（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 使用统一的转发函数（支持 namespace）
        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/stop', {}, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        logger.exception(f"停止发送失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_send_protocol(request):
    """发送工控协议报文（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # namespace Agent 使用实时状态检查
        if agent.is_in_namespace():
            ns_status = check_namespace_agent_status(
                agent.interface.namespace, agent.interface.ip_address, agent.port
            )
            if ns_status != 'running':
                return JsonResponse({'success': False, 'error': 'Agent 未运行'})
        elif agent.status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        # 使用 forward_to_agent 转发（支持 namespace）
        success, resp_data, err = forward_to_agent(
            agent, 'POST', '/api/send_protocol', data=data, timeout=30
        )

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': err or '请求失败'})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_start_scan(request):
    """启动端口扫描（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查状态（支持 namespace）
        ns = agent.interface.namespace
        if ns:
            service_name = agent.get_namespace_service_name()
            status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
        else:
            status = agent.status

        if status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/start_scan', data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_start_replay(request):
    """启动报文回放（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查状态（支持 namespace）
        ns = agent.interface.namespace
        if ns:
            service_name = agent.get_namespace_service_name()
            status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
        else:
            status = agent.status

        if status != 'running':
            return JsonResponse({'success': False, 'error': 'Agent 未运行'})

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/start_replay', data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_stop_scan(request):
    """停止端口扫描（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/stop_scan', {}, timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
@csrf_exempt
def api_scan_progress(request):
    """获取扫描进度（支持 namespace）"""
    try:
        agent_id = request.GET.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'GET', '/api/scan_progress', timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_scan_results(request):
    """获取扫描结果（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/scan_results', {}, timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_list_pcap_files(request):
    """获取 PCAP 文件列表（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'GET', '/api/list_pcap_files', timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_pcap_files(request):
    """获取 PCAP 文件列表（新版，支持目录浏览，支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        directory = data.get('directory', '/opt/pcap')
        search = data.get('search', '')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/pcap_files/', {'directory': directory, 'search': search}, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_packet_replay_start(request):
    """启动报文回放（新版 tcpreplay，支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        pcap_files = data.get('pcap_files', [])
        loop = data.get('loop', 1)
        multiplier = data.get('multiplier')
        rate_pps = data.get('rate_pps')
        rate_mbps = data.get('rate_mbps')
        topspeed = data.get('topspeed', False)

        agent = LocalAgent.objects.get(agent_id=agent_id)

        forward_data = {
            'pcap_files': pcap_files,
            'loop': loop,
            'multiplier': multiplier,
            'rate_pps': rate_pps,
            'rate_mbps': rate_mbps,
            'topspeed': topspeed
        }

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/packet_replay/start', forward_data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_packet_replay_stop(request):
    """停止报文回放（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/packet_replay/stop', {}, timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})
    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
@csrf_exempt
def api_packet_replay_status(request):
    """获取回放状态（支持 namespace）"""
    try:
        agent_id = request.GET.get('agent_id')
        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'GET', '/api/packet_replay/status', timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_stop_replay(request):
    """停止报文回放（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/stop_replay', {}, timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_replay_stats(request):
    """获取回放统计（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        agent = LocalAgent.objects.get(agent_id=agent_id)

        success, resp_data, error = forward_to_agent(agent, 'GET', '/api/replay_stats', timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

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
        'user': d.user,
        'password': d.password,
        'backend_password': d.backend_password,
        'has_backend_password': bool(d.backend_password),  # 是否有自定义后台密码
        'default_backend_password': True if not d.backend_password else False,  # 是否使用默认密码
        'is_long_running': d.is_long_running,
        'description': d.description,
        'created_at': d.created_at.isoformat(),
        'cpu_model': d.cpu_model,
        'cpu_cores': d.cpu_cores,
        'hardware_model': d.hardware_model,
    } for d in devices]

    return JsonResponse({'devices': data, 'success': True})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_add(request):
    """添加测试设备"""
    try:
        from main.device_monitor_task import start_device_monitoring

        data = json.loads(request.body)

        device = TestDevice.objects.create(
            name=data.get('name'),
            type=data.get('type', 'ic_firewall'),
            ip=data.get('ip'),
            port=data.get('port', 22),
            user=data.get('user', 'admin'),
            password=data.get('password', ''),
            backend_password=data.get('backend_password', ''),
            is_long_running=data.get('is_long_running', False),
            description=data.get('description', ''),
        )

        # 如果是长跑环境，自动启动监测
        if device.is_long_running:
            try:
                device_info = {
                    'name': device.name,
                    'ip': device.ip,
                    'type': device.type,
                    'user': device.user,
                    'password': device.password,
                    'backend_password': device.backend_password,
                    'port': device.port
                }
                start_device_monitoring(str(device.id), device_info)
                logger.info(f"长跑环境设备 {device.name}({device.ip}) 已自动启动监测")
            except Exception as e:
                logger.warning(f"自动启动监测失败: {e}")

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
    snmp_available, _ = check_snmp_tools()
    context = {
        'trap_status': trap_status,
        'devices': devices,
        'snmp_available': snmp_available,
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
            version = data.get('version', 'v2c')
            community = data.get('community', 'public')
            security_username = data.get('security_username', '')
            security_level = data.get('security_level', 'noAuthNoPriv')
            auth_protocol = data.get('auth_protocol', 'MD5')
            auth_password = data.get('auth_password', '')
            priv_protocol = data.get('priv_protocol', 'DES')
            priv_password = data.get('priv_password', '')

            success, message = start_trap_receiver(
                port, version, community, security_username, security_level,
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


@require_http_methods(["POST"])
@csrf_exempt
def api_dhcp_client_start(request):
    """启动 DHCP 客户端（代理转发到 Agent，支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')
        count = data.get('count', 1)
        start_mac = data.get('start_mac', '00:11:22:33:44:01')
        timeout = data.get('timeout', 30)
        max_workers = data.get('max_workers', 10)

        agent = LocalAgent.objects.get(agent_id=agent_id)
        interface_name = agent.interface.name

        forward_data = {
            'count': count,
            'start_mac': start_mac,
            'interface': interface_name,
            'timeout': timeout,
            'max_workers': max_workers
        }

        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/dhcp_client/start', forward_data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
@csrf_exempt
def api_dhcp_client_status(request):
    """获取 DHCP 客户端状态（代理转发到 Agent，支持 namespace）"""
    try:
        agent_id = request.GET.get('agent_id')
        session_id = request.GET.get('session_id')

        if not agent_id or not session_id:
            return JsonResponse({'success': False, 'error': '缺少 agent_id 或 session_id'})

        agent = LocalAgent.objects.get(agent_id=agent_id)

        endpoint = f'/api/dhcp_client/status?session_id={session_id}'
        success, resp_data, error = forward_to_agent(agent, 'GET', endpoint, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


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
    """生成设备授权 - 支持 DR 和 dev-Code 两种方式"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': '仅支持POST请求'})

    try:
        data = json.loads(request.body)
        auth_name = data.get('name', '').strip()
        machine_code = data.get('machine_code', '').strip()
        method = data.get('method', 'DR')  # 默认 DR 方式

        if not machine_code:
            return JsonResponse({'success': False, 'error': '缺少设备机器码'})

        if method == 'dev-Code':
            # dev-Code 方式：SSH 执行 licgen 命令
            success, result = _generate_device_license_dev_code(machine_code)
        else:
            # DR 方式：SSH 执行 lic_gen 命令
            if not auth_name:
                return JsonResponse({'success': False, 'error': 'DR 方式需要授权机构名称'})

            from main.license_utils import generate_device_license as gen_device_license
            success, result = gen_device_license(auth_name, machine_code)

        if success:
            # 将二进制内容转为 base64 返回（前端处理）
            import base64
            content_b64 = base64.b64encode(result.get('content', b'')).decode('utf-8')
            return JsonResponse({
                'success': True,
                'filename': result.get('filename'),
                'content': content_b64,
                'message': result.get('message', '生成成功')
            })
        else:
            return JsonResponse({'success': False, 'error': result})

    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': '无效的JSON格式'})
    except Exception as e:
        logger.exception(f"生成设备授权异常: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


def _generate_device_license_dev_code(machine_code: str) -> tuple:
    """
    dev-Code 方式：SSH 到授权服务器执行 licgen 命令

    Args:
        machine_code: 设备机器码

    Returns:
        (success, result): 成功返回(True, {'filename': ..., 'content': ...})，失败返回(False, error)
    """
    import paramiko

    # dev-Code 授权服务器配置（与 DR 服务器相同，但工具路径不同）
    LICENSE_SERVER = '10.40.24.17'
    LICENSE_USER = 'tdhx'
    LICENSE_PASSWORD = 'tdhx@2017'
    LICENSE_PORT = 22
    LICGEN_PATH = '/home/tdhx/license/x64/licgen'  # 注意：dev-Code 使用 licgen，DR 使用 lic_gen

    filename = f'{machine_code}.lic'

    try:
        logger.info(f"[dev-Code] 连接授权服务器: {LICENSE_SERVER}")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(LICENSE_SERVER, port=LICENSE_PORT, username=LICENSE_USER, password=LICENSE_PASSWORD, timeout=30)

        # 执行 licgen 命令（dev-Code 方式）
        command = f'{LICGEN_PATH} -m {machine_code} -p {filename}'
        logger.info(f"[dev-Code] 执行命令: {command}")

        stdin, stdout, stderr = ssh.exec_command(command, timeout=30)
        exit_code = stdout.channel.recv_exit_status()

        output = stdout.read().decode('utf-8', errors='ignore')
        error = stderr.read().decode('utf-8', errors='ignore')

        logger.info(f"[dev-Code] 命令输出: {output}")
        if error:
            logger.warning(f"[dev-Code] 命令错误输出: {error}")

        if exit_code != 0 and 'successful' not in output.lower():
            ssh.close()
            return False, f"授权生成失败: {output or error}"

        # 下载生成的 .lic 文件
        sftp = ssh.open_sftp()
        remote_path = f'/home/tdhx/{filename}'

        try:
            with sftp.file(remote_path, 'rb') as remote_file:
                file_content = remote_file.read()
        except FileNotFoundError:
            ssh.close()
            return False, f"授权文件未生成: {remote_path}"

        sftp.close()
        ssh.close()

        logger.info(f"[dev-Code] 设备授权生成成功: {filename}, 文件大小: {len(file_content)} 字节")

        return True, {
            'filename': filename,
            'content': file_content,
            'message': f'{filename} generate successful.',
            'output': output.strip()
        }

    except paramiko.AuthenticationException:
        logger.error("[dev-Code] 授权服务器认证失败")
        return False, "授权服务器认证失败"
    except paramiko.SSHException as e:
        logger.error(f"[dev-Code] SSH连接错误: {e}")
        return False, f"SSH连接错误: {str(e)}"
    except Exception as e:
        logger.exception(f"[dev-Code] 生成设备授权失败: {e}")
        return False, str(e)


# ========== 网卡配置 API ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_interface_config_ip(request):
    """配置网卡 IP 地址（支持 namespace）"""
    try:
        data = json.loads(request.body)
        interface_name = data.get('interface_name')
        ip_address = data.get('ip_address')
        netmask = data.get('netmask', '24')

        if not interface_name or not ip_address:
            return JsonResponse({'success': False, 'error': '缺少网卡名或 IP 地址'})

        # 检查网卡是否在 namespace 内
        iface = NetworkInterface.objects.filter(name=interface_name).first()
        namespace = iface.namespace if iface else None

        def run_ip_cmd(args):
            if namespace:
                return exec_in_namespace(namespace, ['sudo'] + args)
            else:
                return subprocess.run(['sudo'] + args, capture_output=True, text=True, timeout=10)

        # 先启动网卡
        run_ip_cmd(['ip', 'link', 'set', interface_name, 'up'])

        # 配置 IP 地址（CIDR 格式）
        cidr = f"{ip_address}/{netmask}"
        result = run_ip_cmd(['ip', 'addr', 'add', cidr, 'dev', interface_name])

        if result.returncode == 0 or 'File exists' in result.stderr:
            # 更新数据库
            NetworkInterface.objects.update_or_create(
                name=interface_name,
                defaults={
                    'ip_address': ip_address,
                    'is_up': True,
                    'status': 'UP',
                    'is_available': True,
                }
            )

            logger.info(f"网卡 {interface_name} 配置 IP: {cidr}")
            return JsonResponse({
                'success': True,
                'message': f'网卡 {interface_name} 已配置 IP: {cidr}',
                'ip_address': ip_address
            })
        else:
            logger.error(f"配置 IP 失败: {result.stderr}")
            return JsonResponse({'success': False, 'error': result.stderr})

    except Exception as e:
        logger.exception(f"配置网卡 IP 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_interface_startup(request):
    """启动网卡（支持 namespace）"""
    try:
        data = json.loads(request.body)
        interface_name = data.get('interface_name')

        if not interface_name:
            return JsonResponse({'success': False, 'error': '缺少网卡名'})

        # 检查网卡是否在 namespace 内
        iface = NetworkInterface.objects.filter(name=interface_name).first()
        namespace = iface.namespace if iface else None

        if namespace:
            result = exec_in_namespace(namespace, ['sudo', 'ip', 'link', 'set', interface_name, 'up'])
        else:
            result = subprocess.run(
                ['sudo', 'ip', 'link', 'set', interface_name, 'up'],
                capture_output=True,
                text=True,
                timeout=10
            )

        if result.returncode == 0:
            # 更新数据库
            NetworkInterface.objects.update_or_create(
                name=interface_name,
                defaults={
                    'is_up': True,
                    'status': 'UP',
                    'is_available': True,
                }
            )

            logger.info(f"网卡 {interface_name} 已启动")
            return JsonResponse({
                'success': True,
                'message': f'网卡 {interface_name} 已启动'
            })
        else:
            logger.error(f"启动网卡失败: {result.stderr}")
            return JsonResponse({'success': False, 'error': result.stderr})

    except Exception as e:
        logger.exception(f"启动网卡失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 设备监控 API ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_device_test_connection(request):
    """测试设备 SSH 连接（包含 SSH、vtysh、后台 root 三层测试）"""
    try:
        import paramiko
        import time
        data = json.loads(request.body)

        ip = data.get('ip')
        port = int(data.get('port', 22))
        user = data.get('user', 'admin')
        password = data.get('password', '')
        device_type = data.get('device_type', 'ic_firewall')
        backend_password = data.get('backend_password', '')

        if not ip:
            return JsonResponse({'success': False, 'error': '缺少 IP 地址'})

        results = {
            'ssh': {'success': False, 'message': ''},
            'vtysh': {'success': False, 'message': ''},
            'backend': {'success': False, 'message': ''},
        }

        # 1. 测试 SSH 基础连接
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            ssh.connect(ip, port=port, username=user, password=password, timeout=10)
            results['ssh'] = {'success': True, 'message': 'SSH 连接成功'}
            logger.info(f"测试连接 {ip}: SSH 连接成功")
        except paramiko.AuthenticationException as e:
            results['ssh'] = {'success': False, 'message': f'SSH 认证失败: {e}'}
            logger.warning(f"测试连接 {ip}: SSH 认证失败")
            return JsonResponse({'success': False, 'results': results, 'error': 'SSH 认证失败'})
        except Exception as e:
            results['ssh'] = {'success': False, 'message': f'SSH 连接失败: {e}'}
            logger.warning(f"测试连接 {ip}: SSH 连接失败: {e}")
            return JsonResponse({'success': False, 'results': results, 'error': str(e)})

        # 2. 测试 vtysh 连接
        try:
            chan = ssh.invoke_shell()
            chan.settimeout(15)

            # 清空初始输出
            time.sleep(0.5)
            if chan.recv_ready():
                chan.recv(4096)

            # 进入 vtysh
            chan.send('vtysh\n')
            time.sleep(1)

            if chan.recv_ready():
                output = chan.recv(4096).decode('utf-8', errors='ignore')
                # 检查是否进入 vtysh（输出中有 # 提示符）
                if '#' in output or 'vtysh' in output.lower():
                    # 测试执行命令
                    chan.send('show version\n')
                    time.sleep(1)
                    if chan.recv_ready():
                        output += chan.recv(4096).decode('utf-8', errors='ignore')
                        results['vtysh'] = {'success': True, 'message': 'vtysh 连接成功'}
                        logger.info(f"测试连接 {ip}: vtysh 连接成功")
                    else:
                        results['vtysh'] = {'success': False, 'message': 'vtysh 响应超时'}
                else:
                    results['vtysh'] = {'success': False, 'message': '无法进入 vtysh'}

            # 退出 vtysh
            chan.send('exit\n')
            time.sleep(0.3)
            chan.close()
        except Exception as e:
            results['vtysh'] = {'success': False, 'message': f'vtysh 测试失败: {e}'}
            logger.warning(f"测试连接 {ip}: vtysh 测试失败: {e}")

        # 3. 测试后台 root 连接
        from main.device_utils import get_backend_password
        actual_backend_pwd = get_backend_password(device_type, backend_password)

        if actual_backend_pwd:
            try:
                chan = ssh.invoke_shell()
                chan.settimeout(15)

                # 清空初始输出
                time.sleep(0.3)
                if chan.recv_ready():
                    chan.recv(4096)

                # 输入 enter 进入后台
                chan.send('enter\n')
                time.sleep(0.5)
                if chan.recv_ready():
                    chan.recv(4096)

                # 输入后台密码
                chan.send(actual_backend_pwd + '\n')
                time.sleep(0.5)
                if chan.recv_ready():
                    output = chan.recv(4096).decode('utf-8', errors='ignore')
                    # 检查是否成功进入后台（出现 root@ 提示符）
                    if 'root@' in output or '#' in output and 'Password:' not in output:
                        # 测试执行命令
                        chan.send('whoami\n')
                        time.sleep(0.5)
                        if chan.recv_ready():
                            output += chan.recv(4096).decode('utf-8', errors='ignore')
                            if 'root' in output:
                                results['backend'] = {'success': True, 'message': '后台 root 连接成功'}
                                logger.info(f"测试连接 {ip}: 后台 root 连接成功")
                            else:
                                results['backend'] = {'success': False, 'message': '后台权限验证失败'}
                    else:
                        results['backend'] = {'success': False, 'message': '后台密码错误或无法进入'}

                chan.close()
            except Exception as e:
                results['backend'] = {'success': False, 'message': f'后台测试失败: {e}'}
                logger.warning(f"测试连接 {ip}: 后台测试失败: {e}")
        else:
            results['backend'] = {'success': False, 'message': '未配置后台密码'}

        ssh.close()

        # 判断整体是否成功（SSH 必须成功，vtysh 和 backend 至少一个成功）
        overall_success = results['ssh']['success'] and (results['vtysh']['success'] or results['backend']['success'])

        return JsonResponse({
            'success': overall_success,
            'results': results,
            'message': f'SSH: {results["ssh"]["message"]}, vtysh: {results["vtysh"]["message"]}, 后台: {results["backend"]["message"]}'
        })

    except Exception as e:
        logger.exception(f"测试连接失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_monitor_data(request):
    """获取设备 CPU/内存/网络监控数据（从数据库返回最新值）"""
    try:
        from .models import DeviceMonitorData
        data = json.loads(request.body)

        device_id = data.get('device_id')
        ip = data.get('ip')

        if not device_id and not ip:
            return JsonResponse({'success': False, 'error': '缺少设备 ID 或 IP'})

        # 尝试从数据库获取最新数据
        monitor_data = None
        if device_id:
            try:
                monitor_data = DeviceMonitorData.objects.filter(device_id=device_id).first()
            except:
                pass

        if monitor_data:
            # 返回数据库中的最新数据
            return JsonResponse({
                'success': True,
                'from_cache': True,
                'cpu': {
                    'usage': monitor_data.cpu_usage,
                    'name': monitor_data.cpu_name,
                },
                'memory': {
                    'usage': monitor_data.memory_usage,
                    'used': monitor_data.memory_used,
                    'total': monitor_data.memory_total,
                },
                'network': {
                    'rx_rate': monitor_data.rx_rate,
                    'tx_rate': monitor_data.tx_rate,
                },
                'is_online': monitor_data.is_online,
                'updated_at': monitor_data.updated_at.isoformat() if monitor_data.updated_at else None,
            })

        # 如果数据库没有数据，返回默认值
        return JsonResponse({
            'success': True,
            'from_cache': False,
            'cpu': {
                'usage': 0,
                'name': 'ARM/x86 Processor',
            },
            'memory': {
                'usage': 0,
                'used': 0,
                'total': 0,
            },
            'network': {
                'rx_rate': 0,
                'tx_rate': 0,
            },
            'is_online': False,
        })

    except Exception as e:
        logger.exception(f"获取设备监控数据失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_disk_data(request):
    """获取设备磁盘数据（从数据库返回最新值）"""
    try:
        from .models import DeviceMonitorData
        data = json.loads(request.body)

        device_id = data.get('device_id')

        if not device_id:
            return JsonResponse({'success': False, 'error': '缺少设备 ID'})

        # 从数据库获取最新数据
        monitor_data = None
        try:
            monitor_data = DeviceMonitorData.objects.filter(device_id=device_id).first()
        except:
            pass

        if monitor_data:
            return JsonResponse({
                'success': True,
                'from_cache': True,
                'disk': {
                    'total': monitor_data.disk_total,
                    'used': monitor_data.disk_used,
                    'usage': monitor_data.disk_usage,
                }
            })

        return JsonResponse({
            'success': True,
            'from_cache': False,
            'disk': {
                'total': 0,
                'used': 0,
                'usage': 0,
            }
        })

    except Exception as e:
        logger.exception(f"获取设备磁盘数据失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_execute(request):
    """在设备上执行命令"""
    try:
        data = json.loads(request.body)

        ip = data.get('ip')
        port = int(data.get('port', 22))
        user = data.get('user', 'admin')
        password = data.get('password', '')
        command = data.get('command', '')
        command_type = data.get('command_type', 'backend')  # 'vtysh' 或 'backend'
        device_type = data.get('device_type', 'ic_firewall')
        backend_password = data.get('backend_password', '')

        if not ip or not command:
            return JsonResponse({'success': False, 'error': '缺少 IP 或命令'})

        if command_type == 'vtysh':
            # 使用 vtysh 执行命令
            from .device_utils import execute_in_vtysh
            result = execute_in_vtysh(
                command, ip, user, password, port
            )
            if result:
                return JsonResponse({'success': True, 'output': result})
            else:
                return JsonResponse({'success': False, 'error': 'vtysh 命令执行失败'})
        else:
            # 使用后台执行命令
            from .device_utils import execute_in_backend
            result = execute_in_backend(
                command, ip, user, password,
                backend_password, device_type, port
            )
            if result:
                return JsonResponse({'success': True, 'output': result})
            else:
                return JsonResponse({'success': False, 'error': '后台命令执行失败，请检查后台密码是否正确'})

    except Exception as e:
        logger.exception(f"执行命令失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 设备监测扩展 API ==========

@require_http_methods(["POST"])
@csrf_exempt
def api_device_update(request):
    """更新设备信息"""
    try:
        data = json.loads(request.body)
        device_id = data.get('id')

        if not device_id:
            return JsonResponse({'success': False, 'error': '缺少设备 ID'})

        device = TestDevice.objects.get(id=device_id)
        device.name = data.get('name', device.name)
        device.type = data.get('type', device.type)
        device.ip = data.get('ip', device.ip)
        device.port = int(data.get('port', device.port))
        device.user = data.get('user', device.user)

        # 如果提供了新密码则更新
        new_password = data.get('password')
        if new_password is not None and new_password != '':
            device.password = new_password

        # 后台密码
        new_backend_password = data.get('backend_password')
        if new_backend_password is not None and new_backend_password != '':
            device.backend_password = new_backend_password

        # 长跑环境
        device.is_long_running = data.get('is_long_running', device.is_long_running)
        device.description = data.get('description', device.description)
        device.save()

        return JsonResponse({'success': True, 'message': '设备信息已更新'})

    except TestDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': '设备不存在'})
    except Exception as e:
        logger.exception(f"更新设备失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_monitoring_toggle(request):
    """开启/关闭设备监测"""
    try:
        data = json.loads(request.body)
        device_id = data.get('device_id', '').strip()
        enabled = data.get('enabled', False)
        device_info = data.get('device_info', {})

        if not device_id:
            return JsonResponse({'success': False, 'error': '缺少设备 ID'})

        if not device_info:
            return JsonResponse({'success': False, 'error': '缺少设备信息'})

        if enabled:
            start_device_monitoring(device_id, device_info)
        else:
            stop_device_monitoring(device_id)

        return JsonResponse({'success': True, 'message': '监测状态已更新'})

    except Exception as e:
        logger.exception(f"切换监测状态失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_device_monitoring_status(request):
    """获取所有设备的监测状态"""
    try:
        status = get_monitoring_status()
        return JsonResponse({'success': True, 'status': status})
    except Exception as e:
        logger.exception(f"获取监测状态失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_device_alert_config(request):
    """获取或保存告警配置"""
    if request.method == 'GET':
        try:
            config = get_alert_config()
            return JsonResponse({'success': True, 'config': config})
        except Exception as e:
            logger.exception(f"获取告警配置失败: {e}")
            return JsonResponse({'success': False, 'error': str(e)})

    elif request.method == 'POST':
        try:
            data = json.loads(request.body)
            success = update_alert_config(data)
            if success:
                return JsonResponse({'success': True, 'message': '配置保存成功'})
            else:
                return JsonResponse({'success': False, 'error': '配置保存失败'})
        except Exception as e:
            logger.exception(f"保存告警配置失败: {e}")
            return JsonResponse({'success': False, 'error': str(e)})

    return JsonResponse({'success': False, 'error': '不支持的请求方法'})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_alert_config_test(request):
    """测试邮件发送"""
    try:
        data = json.loads(request.body)

        # 创建测试邮件内容
        test_content = format_alert_email_content(
            {'name': '测试设备', 'ip': '192.168.1.100', 'type': 'ic_firewall'},
            'resource',
            {
                'cpu_usage': 85.5,
                'memory_usage': 82.3,
                'memory_total': 8192,
                'memory_used': 6734,
                'memory_free': 1458,
                'resource_info': {
                    'cpu_usage': 85.5,
                    'memory_usage': 82.3,
                    'memory_total': 8192,
                    'memory_used': 6734,
                    'memory_free': 1458
                }
            }
        )

        try:
            success = send_alert_email(
                data,
                '[测试邮件] 设备监控系统告警测试',
                test_content,
                data.get('recipients', [])
            )

            if success:
                return JsonResponse({'success': True, 'message': '测试邮件发送成功'})
            else:
                return JsonResponse({'success': False, 'error': '测试邮件发送失败'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': f'邮件发送失败: {str(e)}'})

    except Exception as e:
        logger.exception(f"测试邮件发送失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_device_alert_status(request):
    """获取所有设备的告警状态"""
    try:
        from django.utils import timezone

        alerts = DeviceAlertStatus.objects.filter(
            has_alert=True,
            is_ignored=False
        )

        status_dict = {}
        for alert in alerts:
            if alert.is_ignore_active():
                continue

            device_id = str(alert.device_id)
            if device_id not in status_dict:
                status_dict[device_id] = {
                    'has_alert': True,
                    'alert_type': alert.alert_type,
                    'alert_value': alert.alert_value,
                    'alert_time': alert.alert_time.isoformat() if alert.alert_time else None
                }

        return JsonResponse({'success': True, 'status': status_dict})

    except Exception as e:
        logger.exception(f"获取告警状态失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_alert_ignore(request):
    """忽略设备告警"""
    try:
        from django.utils import timezone
        from datetime import timedelta

        data = json.loads(request.body)
        device_id = data.get('device_id')

        if not device_id:
            return JsonResponse({'success': False, 'error': '缺少设备 ID'})

        # 设置忽略时间为一周后
        ignore_until = timezone.now() + timedelta(days=7)

        updated_count = DeviceAlertStatus.objects.filter(
            device_id=device_id,
            has_alert=True,
            is_ignored=False
        ).update(
            is_ignored=True,
            ignore_until=ignore_until
        )

        return JsonResponse({
            'success': True,
            'message': f'已忽略设备 {device_id} 的 {updated_count} 个告警',
            'ignore_until': ignore_until.isoformat()
        })

    except Exception as e:
        logger.exception(f"忽略告警失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_device_coredump_list(request):
    """获取 coredump 文件列表"""
    try:
        data = json.loads(request.body)

        ip = data.get('ip', '').strip()
        port = int(data.get('port', 22))
        user = data.get('user', 'admin')
        password = data.get('password', '')
        device_type = data.get('device_type', '')
        backend_password = data.get('backend_password', '')
        coredump_dir = data.get('coredump_dir', '/data/coredump')

        if not ip:
            return JsonResponse({'success': False, 'error': '缺少 IP 地址'})

        files = get_coredump_files(
            ip, user, password,
            coredump_dir=coredump_dir,
            device_type=device_type,
            backend_password=backend_password
        )

        return JsonResponse({'success': True, 'files': files})

    except Exception as e:
        logger.exception(f"获取 coredump 文件列表失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ==================== 服务下发功能 ====================

@require_http_methods(["POST"])
@csrf_exempt
def api_services_listener(request):
    """监听服务下发API - 转发到指定Agent（支持 namespace）"""
    try:
        data = json.loads(request.body)
        logger.info(f"收到服务下发请求 - 完整: {data}")
        logger.info(f"  - agent_id: {data.get('agent_id')}")
        logger.info(f"  - protocol: {data.get('protocol')}")
        logger.info(f"  - type: {data.get('type')}")
        logger.info(f"  - action: {data.get('action')}")
        agent_id = data.get('agent_id')

        if not agent_id:
            return JsonResponse({'success': False, 'error': '缺少 agent_id'})

        # 获取Agent信息
        from .models import LocalAgent
        agent = LocalAgent.objects.filter(agent_id=agent_id).first()
        if not agent:
            return JsonResponse({'success': False, 'error': 'Agent不存在'})

        if not agent.interface.ip_address:
            return JsonResponse({'success': False, 'error': 'Agent未配置IP'})

        # 使用统一的转发函数（支持 namespace）
        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/services/listener', data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except Exception as e:
        logger.exception(f"监听服务下发失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_services_client(request):
    """客户端服务下发API - 转发到指定Agent（支持 namespace）"""
    try:
        data = json.loads(request.body)
        agent_id = data.get('agent_id')

        logger.info(f'客户端服务请求: agent_id={agent_id}, protocol={data.get("protocol")}, action={data.get("action")}')

        if not agent_id:
            return JsonResponse({'success': False, 'error': '缺少 agent_id'})

        from .models import LocalAgent
        agent = LocalAgent.objects.filter(agent_id=agent_id).first()
        if not agent:
            return JsonResponse({'success': False, 'error': 'Agent不存在'})

        if not agent.interface.ip_address:
            return JsonResponse({'success': False, 'error': 'Agent未配置IP'})

        # 使用统一的转发函数（支持 namespace）
        success, resp_data, error = forward_to_agent(agent, 'POST', '/api/services/client', data, timeout=10)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except Exception as e:
        logger.exception(f"客户端服务下发失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_services_status(request):
    """获取Agent服务状态（支持 namespace）"""
    try:
        agent_id = request.GET.get('agent_id')
        detail = request.GET.get('detail', '')

        if not agent_id:
            return JsonResponse({'success': False, 'error': '缺少 agent_id'})

        from .models import LocalAgent
        agent = LocalAgent.objects.filter(agent_id=agent_id).first()
        if not agent:
            return JsonResponse({'success': False, 'error': 'Agent不存在'})

        if not agent.interface.ip_address:
            return JsonResponse({'success': False, 'error': 'Agent未配置IP'})

        # 使用统一的转发函数（支持 namespace）
        success, result, error = forward_to_agent(agent, 'GET', '/api/services/status', timeout=5)

        if not success:
            return JsonResponse({'success': False, 'error': error})

        # 如果请求邮件用户详情，额外获取用户列表
        if detail == 'mail_users' and result.get('success'):
            success2, users_result, _ = forward_to_agent(agent, 'POST', '/api/services/listener', {
                'protocol': 'mail',
                'action': 'list_users'
            }, timeout=5)
            if success2 and users_result.get('success'):
                result['mail_users'] = users_result.get('mail_users', [])

        return JsonResponse(result)

    except Exception as e:
        logger.exception(f"获取服务状态失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_services_logs(request):
    """获取Agent服务日志（支持 namespace）"""
    try:
        agent_id = request.GET.get('agent_id')
        protocol = request.GET.get('protocol', '')
        limit = int(request.GET.get('limit', 100))

        if not agent_id:
            return JsonResponse({'success': False, 'error': '缺少 agent_id'})

        from .models import LocalAgent
        agent = LocalAgent.objects.filter(agent_id=agent_id).first()
        if not agent:
            return JsonResponse({'success': False, 'error': 'Agent不存在'})

        if not agent.interface.ip_address:
            return JsonResponse({'success': False, 'error': 'Agent未配置IP'})

        # 构建 endpoint 带参数
        endpoint = f'/api/services/logs?limit={limit}'
        if protocol:
            endpoint += f'&protocol={protocol}'

        success, resp_data, error = forward_to_agent(agent, 'GET', endpoint, timeout=5)

        if success:
            return JsonResponse(resp_data)
        return JsonResponse({'success': False, 'error': error})

    except Exception as e:
        logger.exception(f"获取服务日志失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


# ========== 工控协议代理 API ==========

def _merge_get_params(request, data):
    """合并 GET 参数到 data dict"""
    if request.GET:
        for key in request.GET:
            if key not in data:
                value = request.GET[key]
                # 转换数字参数
                numeric_keys = ['function_code', 'address', 'count', 'port', 'unit_id',
                               'timeout', 'rack', 'slot', 'db_number', 'start_address',
                               'data_type', 'value', 'interface_index', 'goose_id',
                               'sv_id', 'app_id', 'object_type', 'object_instance',
                               'property_id', 'outstation_address', 'master_address',
                               'association_id', 'domain_id', 'vmd_name']
                if key in numeric_keys:
                    try:
                        data[key] = int(value)
                    except ValueError:
                        data[key] = value
                else:
                    data[key] = value
    return data


def _proxy_industrial_request(agent_id, protocol_path, method='GET', data=None, params=None, timeout=30):
    """通用工控协议代理请求函数（支持 namespace）"""
    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)

        # 检查 Agent 状态（支持 namespace）
        ns = agent.interface.namespace
        if ns:
            service_name = agent.get_namespace_service_name()
            status = check_namespace_agent_status(ns, agent.interface.ip_address, agent.port, service_name)
        else:
            status = agent.status

        if status != 'running':
            return {'success': False, 'error': 'Agent 未运行'}

        # 构建 endpoint（带 params）
        endpoint = f'/api/industrial_protocol/{protocol_path}'
        if params:
            param_str = '&'.join(f'{k}={v}' for k, v in params.items())
            endpoint += f'?{param_str}'

        # 使用统一的转发函数（支持 namespace）
        success, resp_data, error = forward_to_agent(agent, method, endpoint, data, timeout=timeout)

        if success:
            return resp_data
        return {'success': False, 'error': error}

    except LocalAgent.DoesNotExist:
        return {'success': False, 'error': 'Agent 不存在'}
    except Exception as e:
        return {'success': False, 'error': str(e)}


@csrf_exempt
def api_industrial_modbus_client(request, action):
    """Modbus Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"modbus_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_modbus_server(request, action):
    """Modbus Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"modbus_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_s7_client(request, action):
    """S7 Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"s7_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_s7_server(request, action):
    """S7 Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"s7_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_goose_sv(request, action):
    """GOOSE/SV 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"goose-sv/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_enip_client(request, action):
    """Ethernet/IP Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"enip_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_enip_server(request, action):
    """Ethernet/IP Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"enip_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_bacnet_client(request, action):
    """BACnet Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"bacnet_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_bacnet_server(request, action):
    """BACnet Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"bacnet_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_dnp3_client(request, action):
    """DNP3 Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"dnp3_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_dnp3_server(request, action):
    """DNP3 Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"dnp3_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_mms_client(request, action):
    """MMS Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"mms_client/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_mms_server(request, action):
    """MMS Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"mms_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_http_client(request, action):
    """HTTP Client 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"http_client/{action}", method='POST', data=data, timeout=60)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_http_server(request, action):
    """HTTP Server 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')
        result = _proxy_industrial_request(agent_id, f"http_server/{action}", method='POST', data=data)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
def api_industrial_http_files(request, action, filename=None):
    """HTTP Files 代理 API"""
    try:
        data = json.loads(request.body) if request.body else {}
        data = _merge_get_params(request, data)
        agent_id = data.get('agent_id')

        if action == 'download' and filename:
            result = _proxy_industrial_request(agent_id, f"http_files/download/{filename}", method='GET')
        elif action == 'analyze' and filename:
            result = _proxy_industrial_request(agent_id, f"http_files/analyze/{filename}", method='GET')
        elif action == 'delete' and filename:
            result = _proxy_industrial_request(agent_id, f"http_files/delete/{filename}", method='POST', data=data)
        else:
            result = _proxy_industrial_request(agent_id, f"http_files/{action}", method='POST', data=data)

        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ========== Network Namespace 管理 API ==========

@require_http_methods(["GET"])
def api_namespace_list(request):
    """获取所有 namespace 列表"""
    namespaces = get_namespace_list()
    return JsonResponse({
        'success': True,
        'namespaces': namespaces,
        'count': len(namespaces)
    })


@require_http_methods(["POST"])
@csrf_exempt
def api_namespace_setup_interface(request):
    """创建网卡 namespace"""
    try:
        data = json.loads(request.body)
        interface_name = data.get('interface_name')
        ip_cidr = data.get('ip_cidr')  # 如 192.168.11.100/16

        if not interface_name:
            return JsonResponse({'success': False, 'error': '网卡名称不能为空'})

        if not ip_cidr:
            return JsonResponse({'success': False, 'error': 'IP地址不能为空'})

        # 执行 setup-interface
        result = subprocess.run(
            ['sudo', '/opt/SFW-TEST3/scripts/network-namespace-setup.sh', 'setup-interface', interface_name, ip_cidr],
            capture_output=True, text=True, timeout=60
        )

        if result.returncode == 0:
            # 更新数据库
            try:
                iface = NetworkInterface.objects.get(name=interface_name)
                iface.namespace = f'ns-{interface_name}'
                iface.ip_address = ip_cidr.split('/')[0]
                iface.save()
                logger.info(f"网卡 {interface_name} namespace 创建成功")
            except NetworkInterface.DoesNotExist:
                pass

            return JsonResponse({
                'success': True,
                'message': f'网卡 {interface_name} namespace 创建成功',
                'namespace': f'ns-{interface_name}',
                'output': result.stdout
            })
        else:
            return JsonResponse({'success': False, 'error': result.stderr, 'output': result.stdout})

    except Exception as e:
        logger.exception(f"创建 namespace 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@csrf_exempt
def api_namespace_restore_interface(request):
    """恢复网卡到主 namespace"""
    try:
        data = json.loads(request.body)
        interface_name = data.get('interface_name')

        if not interface_name:
            return JsonResponse({'success': False, 'error': '网卡名称不能为空'})

        # 执行 remove-interface
        result = subprocess.run(
            ['sudo', '/opt/SFW-TEST3/scripts/network-namespace-setup.sh', 'remove-interface', interface_name],
            capture_output=True, text=True, timeout=60
        )

        if result.returncode == 0:
            # 更新数据库
            try:
                iface = NetworkInterface.objects.get(name=interface_name)
                iface.namespace = None
                iface.save()
                logger.info(f"网卡 {interface_name} 已恢复到主 namespace")
            except NetworkInterface.DoesNotExist:
                pass

            return JsonResponse({
                'success': True,
                'message': f'网卡 {interface_name} 已恢复到主 namespace',
                'output': result.stdout
            })
        else:
            return JsonResponse({'success': False, 'error': result.stderr, 'output': result.stdout})

    except Exception as e:
        logger.exception(f"恢复 namespace 失败: {e}")
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["GET"])
def api_namespace_status(request):
    """获取 namespace 状态"""
    result = subprocess.run(
        ['sudo', '/opt/SFW-TEST3/scripts/network-namespace-setup.sh', 'status'],
        capture_output=True, text=True, timeout=30
    )
    return JsonResponse({
        'success': True,
        'status': result.stdout,
        'error': result.stderr if result.returncode != 0 else None
    })