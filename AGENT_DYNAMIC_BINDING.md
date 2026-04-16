# 网卡-Agent 动态绑定方案设计

## 一、需求确认

| 需求项 | 确认内容 |
|--------|---------|
| Agent 功能 | 每个 Agent 都拥有全功能（报文发送、工控协议等） |
| Agent 数量 | 根据网卡数量动态决定 |
| 绑定方式 | 界面上可配置网卡与 Agent 的绑定关系 |

---

## 二、数据库模型设计

### 2.1 新增模型

```python
# main/models.py 新增

class NetworkInterface(models.Model):
    """网卡模型 - 存储系统网卡信息"""
    name = models.CharField(max_length=50, verbose_name="网卡名称")  # eth0, eth1
    ip_address = models.GenericIPAddressField(verbose_name="IP地址")
    mac_address = models.CharField(max_length=17, blank=True, verbose_name="MAC地址")
    speed = models.IntegerField(null=True, blank=True, verbose_name="速率(Mbps)")
    is_management = models.BooleanField(default=False, verbose_name="是否管理网卡")
    is_available = models.BooleanField(default=True, verbose_name="是否可用")
    detected_at = models.DateTimeField(auto_now_add=True, verbose_name="检测时间")
    
    class Meta:
        verbose_name = "网卡"
        verbose_name_plural = verbose_name
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class LocalAgent(models.Model):
    """本地 Agent 模型 - 存储网卡-Agent 绑定关系"""
    AGENT_STATUS = [
        ('running', '运行中'),
        ('stopped', '已停止'),
        ('error', '异常'),
        ('unknown', '未知'),
    ]
    
    agent_id = models.CharField(max_length=20, unique=True, verbose_name="Agent ID")
    interface = models.ForeignKey(NetworkInterface, on_delete=models.CASCADE, verbose_name="绑定网卡")
    port = models.IntegerField(default=8888, verbose_name="监听端口")
    status = models.CharField(max_length=20, choices=AGENT_STATUS, default='stopped', verbose_name="状态")
    auto_start = models.BooleanField(default=False, verbose_name="开机自启")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    
    class Meta:
        verbose_name = "本地 Agent"
        verbose_name_plural = verbose_name
        ordering = ['agent_id']
    
    def __str__(self):
        return f"Agent-{self.agent_id} ({self.interface.name})"
    
    def get_url(self):
        """获取 Agent HTTP URL"""
        return f"http://{self.interface.ip_address}:{self.port}"


class AgentConfig(models.Model):
    """Agent 全局配置"""
    config_key = models.CharField(max_length=50, unique=True, verbose_name="配置键")
    config_value = models.TextField(verbose_name="配置值")
    description = models.CharField(max_length=200, blank=True, verbose_name="描述")
    
    class Meta:
        verbose_name = "Agent 配置"
        verbose_name_plural = verbose_name
    
    @classmethod
    def get(cls, key, default=None):
        """获取配置值"""
        try:
            return cls.objects.get(config_key=key).config_value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set(cls, key, value, description=''):
        """设置配置值"""
        obj, created = cls.objects.update_or_create(
            config_key=key,
            defaults={'config_value': value, 'description': description}
        )
        return obj
```

### 2.2 数据库表关系

```
NetworkInterface (网卡)          LocalAgent (Agent)
┌─────────────────────┐         ┌─────────────────────┐
│ id                  │         │ id                  │
│ name (eth0)         │◄────────│ interface_id (FK)   │
│ ip_address          │         │ agent_id (A)        │
│ mac_address         │         │ port (8888)         │
│ speed               │         │ status (running)    │
│ is_management       │         │ auto_start          │
│ is_available        │         │ created_at          │
└─────────────────────┘         └─────────────────────┘
```

---

## 三、界面设计

### 3.1 网卡管理界面

```
┌─────────────────────────────────────────────────────────────┐
│  网卡管理                                                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  [扫描网卡] [刷新状态]                                        │
│                                                              │
│  已检测到的网卡                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 网卡名称 │ IP地址      │ MAC地址        │ 速率 │ 角色   │ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ eth0     │ 192.168.100.│ 00:11:22:33:44 │ 1000 │ 管理   │ │
│  │ eth1     │ 192.168.1.10│ 00:11:22:33:45 │ 1000 │ 可用   │ │
│  │ eth2     │ 192.168.2.10│ 00:11:22:33:46 │ 1000 │ 可用   │ │
│  │ eth3     │ 192.168.3.10│ 00:11:22:33:47 │ 1000 │ 可用   │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  设置管理网卡: [eth0 ▼]  (管理网卡不参与 Agent 绑定)          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Agent 配置界面

```
┌─────────────────────────────────────────────────────────────┐
│  Agent 配置                                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  可用网卡（未绑定 Agent）                                     │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ eth1 (192.168.1.10)  [创建 Agent]                       │ │
│  │ eth2 (192.168.2.10)  [创建 Agent]                       │ │
│  │ eth3 (192.168.3.10)  [创建 Agent]                       │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  已配置的 Agent                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Agent ID │ 绑定网卡 │ IP地址      │ 端口 │ 状态 │ 操作  │ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ A        │ eth1     │ 192.168.1.10│ 8888 │ 运行 │ [停止]│ │
│  │          │          │             │      │      │ [删除]│ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ B        │ eth2     │ 192.168.2.10│ 8889 │ 运行 │ [停止]│ │
│  │          │          │             │      │      │ [删除]│ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ C        │ eth3     │ 192.168.3.10│ 8890 │ 停止 │ [启动]│ │
│  │          │          │             │      │      │ [删除]│ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [全部启动] [全部停止]                                        │
│                                                              │
│  创建新 Agent                                                 │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 选择网卡: [eth1 ▼]                                       │ │
│  │ Agent ID: [D        ] (自动生成或手动输入)               │ │
│  │ 端口:    [8888      ] (自动分配可用端口)                 │ │
│  │ 开机自启: [✓]                                            │ │
│  │                               [创建并启动] [仅创建]      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 报文发送界面（使用 Agent 选择）

```
┌─────────────────────────────────────────────────────────────┐
│  报文发送                                                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  选择发送 Agent                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ ● Agent-A (eth1 - 192.168.1.10:8888) 运行中             │ │
│  │ ○ Agent-B (eth2 - 192.168.2.10:8889) 运行中             │ │
│  │ ○ Agent-C (eth3 - 192.168.3.10:8890) 已停止             │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  报文配置                                                    │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 协议类型: [TCP ▼]                                        │ │
│  │ 源端口:   [随机  ] 目的端口: [80    ]                    │ │
│  │ ...                                                      │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [发送报文] [批量发送]                                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 四、API 接口设计

### 4.1 网卡管理 API

```python
# main/urls.py 新增

# 网卡管理
path('api/interfaces/scan/', views.scan_interfaces, name='scan_interfaces'),
path('api/interfaces/list/', views.interface_list, name='interface_list'),
path('api/interfaces/set_management/', views.set_management_interface, name='set_management_interface'),

# Agent 管理
path('api/agents/list/', views.local_agent_list, name='local_agent_list'),
path('api/agents/create/', views.local_agent_create, name='local_agent_create'),
path('api/agents/delete/', views.local_agent_delete, name='local_agent_delete'),
path('api/agents/start/', views.local_agent_start, name='local_agent_start'),
path('api/agents/stop/', views.local_agent_stop, name='local_agent_stop'),
path('api/agents/status/', views.local_agent_status, name='local_agent_status'),
path('api/agents/logs/', views.local_agent_logs, name='local_agent_logs'),
```

### 4.2 网卡扫描实现

```python
# main/views.py

import psutil
import subprocess

def scan_interfaces(request):
    """扫描系统网卡"""
    interfaces = []
    
    # 使用 psutil 获取网卡信息
    net_if_addrs = psutil.net_if_addrs()
    net_if_stats = psutil.net_if_stats()
    
    for name, addrs in net_if_addrs.items():
        # 跳过回环接口
        if name.startswith('lo') or name.startswith('Loopback'):
            continue
        
        # 获取 IPv4 地址
        ipv4 = None
        mac = None
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ipv4 = addr.address
            elif addr.family == psutil.PF_LINK:
                mac = addr.address
        
        if not ipv4:
            continue
        
        # 获取网卡速率
        stats = net_if_stats.get(name)
        speed = stats.speed if stats and stats.speed > 0 else None
        
        interfaces.append({
            'name': name,
            'ip_address': ipv4,
            'mac_address': mac,
            'speed': speed,
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
                'is_available': iface['is_up']
            }
        )
    
    return JsonResponse({
        'success': True,
        'interfaces': interfaces,
        'count': len(interfaces)
    })

def interface_list(request):
    """获取网卡列表"""
    interfaces = NetworkInterface.objects.all()
    
    data = [{
        'id': iface.id,
        'name': iface.name,
        'ip_address': iface.ip_address,
        'mac_address': iface.mac_address,
        'speed': iface.speed,
        'is_management': iface.is_management,
        'is_available': iface.is_available,
        'has_agent': LocalAgent.objects.filter(interface=iface).exists()
    } for iface in interfaces]
    
    return JsonResponse({'interfaces': data})
```

### 4.3 Agent 管理实现

```python
# main/views.py

import subprocess
import requests
import time

def local_agent_list(request):
    """获取本地 Agent 列表"""
    agents = LocalAgent.objects.all()
    
    data = []
    for agent in agents:
        # 查询 Agent 实际状态（通过 HTTP）
        actual_status = 'unknown'
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
            agent.status = 'stopped'
            agent.save()
        
        data.append({
            'id': agent.id,
            'agent_id': agent.agent_id,
            'interface': agent.interface.name,
            'ip_address': agent.interface.ip_address,
            'port': agent.port,
            'status': actual_status,
            'auto_start': agent.auto_start,
            'created_at': agent.created_at.isoformat()
        })
    
    return JsonResponse({'agents': data})

def local_agent_create(request):
    """创建新的 Agent"""
    data = json.loads(request.body)
    
    interface_id = data.get('interface_id')
    agent_id = data.get('agent_id')
    port = data.get('port', 8888)
    auto_start = data.get('auto_start', False)
    
    # 检查网卡是否存在
    try:
        interface = NetworkInterface.objects.get(id=interface_id)
    except NetworkInterface.DoesNotExist:
        return JsonResponse({'success': False, 'error': '网卡不存在'})
    
    # 检查网卡是否是管理网卡
    if interface.is_management:
        return JsonResponse({'success': False, 'error': '管理网卡不能绑定 Agent'})
    
    # 检查网卡是否已绑定 Agent
    if LocalAgent.objects.filter(interface=interface).exists():
        return JsonResponse({'success': False, 'error': '网卡已绑定 Agent'})
    
    # 自动生成 Agent ID（如果未提供）
    if not agent_id:
        existing_ids = LocalAgent.objects.values_list('agent_id', flat=True)
        # 找到下一个可用 ID (A, B, C, D...)
        for char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            if char not in existing_ids:
                agent_id = char
                break
    
    # 自动分配端口（如果冲突）
    existing_ports = LocalAgent.objects.values_list('port', flat=True)
    while port in existing_ports:
        port += 1
    
    # 创建 Agent 配置
    agent = LocalAgent.objects.create(
        agent_id=agent_id,
        interface=interface,
        port=port,
        auto_start=auto_start,
        status='stopped'
    )
    
    # 创建 systemd 服务文件
    create_systemd_service(agent)
    
    return JsonResponse({
        'success': True,
        'agent': {
            'agent_id': agent.agent_id,
            'interface': agent.interface.name,
            'ip_address': agent.interface.ip_address,
            'port': agent.port
        }
    })

def local_agent_delete(request):
    """删除 Agent"""
    data = json.loads(request.body)
    agent_id = data.get('agent_id')
    
    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)
        
        # 先停止 Agent
        subprocess.run(['sudo', 'systemctl', 'stop', f'agent-{agent_id.lower()}'], timeout=10)
        
        # 禁用开机自启
        subprocess.run(['sudo', 'systemctl', 'disable', f'agent-{agent_id.lower()}'], timeout=10)
        
        # 删除 systemd 服务文件
        service_file = f'/etc/systemd/system/agent-{agent_id.lower()}.service'
        subprocess.run(['sudo', 'rm', '-f', service_file], timeout=5)
        
        # 重载 systemd
        subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10)
        
        # 删除数据库记录
        agent.delete()
        
        return JsonResponse({'success': True})
        
    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})

def local_agent_start(request):
    """启动 Agent"""
    data = json.loads(request.body)
    agent_id = data.get('agent_id')
    
    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)
        
        # 启动 systemd 服务
        result = subprocess.run(
            ['sudo', 'systemctl', 'start', f'agent-{agent_id.lower()}'],
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
                    agent.save()
                    return JsonResponse({'success': True, 'status': 'running'})
                else:
                    return JsonResponse({'success': False, 'error': 'Agent 启动失败'})
            except:
                return JsonResponse({'success': True, 'status': 'starting'})
        else:
            return JsonResponse({'success': False, 'error': result.stderr})
            
    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})

def local_agent_stop(request):
    """停止 Agent"""
    data = json.loads(request.body)
    agent_id = data.get('agent_id')
    
    try:
        agent = LocalAgent.objects.get(agent_id=agent_id)
        
        result = subprocess.run(
            ['sudo', 'systemctl', 'stop', f'agent-{agent_id.lower()}'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            agent.status = 'stopped'
            agent.save()
            return JsonResponse({'success': True, 'status': 'stopped'})
        else:
            return JsonResponse({'success': False, 'error': result.stderr})
            
    except LocalAgent.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Agent 不存在'})


def create_systemd_service(agent):
    """创建 systemd 服务文件"""
    
    service_content = f"""[Unit]
Description=Packet Agent {agent.agent_id} ({agent.interface.name})
After=network.target

[Service]
Type=simple
Environment="AGENT_ID={agent.agent_id}"
Environment="BIND_IP={agent.interface.ip_address}"
Environment="BIND_INTERFACE={agent.interface.name}"
Environment="AGENT_PORT={agent.port}"
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    
    # 写入服务文件（需要 sudo）
    service_file = f'/etc/systemd/system/agent-{agent.agent_id.lower()}.service'
    
    subprocess.run(
        ['sudo', 'tee', service_file],
        input=service_content,
        capture_output=True,
        text=True,
        timeout=10
    )
    
    # 重载 systemd
    subprocess.run(['sudo', 'systemctl', 'daemon-reload'], timeout=10)
    
    # 设置开机自启（如果配置了）
    if agent.auto_start:
        subprocess.run(['sudo', 'systemctl', 'enable', f'agent-{agent.agent_id.lower()}'], timeout=10)
```

---

## 五、Agent 程序设计（全功能）

### 5.1 Agent 全功能架构

```python
# agents/packet_agent.py

"""
全功能 Agent - 支持：
- 报文发送（TCP/UDP/ICMP/自定义）
- 报文接收/监控
- 工控协议（Modbus/S7/GOOSE/SV/DNP3/BACnet/ENIP/MMS）
- 端口扫描
- 报文回放
"""

from agents.base import BaseAgent
from flask import request, jsonify
import threading
import subprocess
import logging

class FullFeatureAgent(BaseAgent):
    """全功能 Agent"""
    
    def __init__(self):
        super().__init__()
        
        # 各功能模块的状态
        self.features = {
            'packet_send': {'running': False, 'thread': None, 'stats': {'sent': 0}},
            'packet_receive': {'running': False, 'thread': None, 'stats': {'received': 0}},
            'industrial_protocol': {'running': False, 'current_protocol': None},
            'port_scan': {'running': False, 'progress': 0, 'results': []},
            'packet_replay': {'running': False, 'current_file': None, 'sent': 0},
        }
        
        self.stop_events = {
            'packet_send': threading.Event(),
            'packet_receive': threading.Event(),
            'port_scan': threading.Event(),
            'packet_replay': threading.Event(),
        }
        
        # 注册所有功能 API
        self._register_all_api()
    
    def _register_all_api(self):
        """注册所有功能 API"""
        
        # ========== 报文发送功能 ==========
        self.register_api('/api/send_packet', self.api_send_packet, ['POST'])
        self.register_api('/api/stop_send', self.api_stop_send, ['POST'])
        self.register_api('/api/send_stats', self.api_send_stats, ['GET'])
        
        # ========== 报文接收功能 ==========
        self.register_api('/api/start_receive', self.api_start_receive, ['POST'])
        self.register_api('/api/stop_receive', self.api_stop_receive, ['POST'])
        self.register_api('/api/receive_stats', self.api_receive_stats, ['GET'])
        
        # ========== 工控协议功能 ==========
        self.register_api('/api/protocols', self.api_get_protocols, ['GET'])
        self.register_api('/api/send_protocol', self.api_send_protocol, ['POST'])
        self.register_api('/api/start_protocol_server', self.api_start_protocol_server, ['POST'])
        self.register_api('/api/stop_protocol_server', self.api_stop_protocol_server, ['POST'])
        
        # ========== 端口扫描功能 ==========
        self.register_api('/api/start_scan', self.api_start_scan, ['POST'])
        self.register_api('/api/stop_scan', self.api_stop_scan, ['POST'])
        self.register_api('/api/scan_progress', self.api_scan_progress, ['GET'])
        self.register_api('/api/scan_results', self.api_scan_results, ['GET'])
        
        # ========== 报文回放功能 ==========
        self.register_api('/api/replay_files', self.api_replay_files, ['GET'])
        self.register_api('/api/start_replay', self.api_start_replay, ['POST'])
        self.register_api('/api/stop_replay', self.api_stop_replay, ['POST'])
        self.register_api('/api/replay_stats', self.api_replay_stats, ['GET'])
        
        # ========== 网卡信息 ==========
        self.register_api('/api/interface_info', self.api_interface_info, ['GET'])
    
    # ========== 报文发送功能实现 ==========
    
    def api_send_packet(self):
        """发送报文"""
        from scapy.all import sendp, Ether, IP, TCP, UDP, ICMP
        
        data = request.get_json()
        
        # 构造报文
        packet = self._build_packet(data)
        
        # 发送报文（使用绑定的网卡）
        count = data.get('count', 1)
        interval = data.get('interval', 0)
        continuous = data.get('continuous', False)
        
        if continuous:
            # 连续发送模式
            self.features['packet_send']['running'] = True
            self.stop_events['packet_send'].clear()
            
            def send_continuous():
                while not self.stop_events['packet_send'].is_set():
                    sendp(packet, iface=self.bind_interface, verbose=False)
                    self.features['packet_send']['stats']['sent'] += 1
                    if interval > 0:
                        time.sleep(interval)
                self.features['packet_send']['running'] = False
            
            self.features['packet_send']['thread'] = threading.Thread(target=send_continuous)
            self.features['packet_send']['thread'].start()
            
            return jsonify({'status': 'continuous_sending', 'interface': self.bind_interface})
        
        else:
            # 固定次数发送
            for i in range(count):
                sendp(packet, iface=self.bind_interface, verbose=False)
                self.features['packet_send']['stats']['sent'] += 1
                if interval > 0:
                    time.sleep(interval)
            
            return jsonify({
                'status': 'sent',
                'count': count,
                'interface': self.bind_interface,
                'total_sent': self.features['packet_send']['stats']['sent']
            })
    
    def api_stop_send(self):
        """停止发送"""
        self.stop_events['packet_send'].set()
        return jsonify({'status': 'stopped'})
    
    def api_send_stats(self):
        """发送统计"""
        return jsonify({
            'running': self.features['packet_send']['running'],
            'total_sent': self.features['packet_send']['stats']['sent'],
            'interface': self.bind_interface
        })
    
    # ========== 工控协议功能实现 ==========
    
    def api_get_protocols(self):
        """获取支持的工控协议"""
        protocols = [
            'modbus-tcp', 's7', 'goose', 'sv', 
            'dnp3', 'bacnet', 'enip', 'mms',
            'ethercat', 'profinet', 'dcp'
        ]
        return jsonify({
            'protocols': protocols,
            'interface': self.bind_interface,
            'agent_id': self.agent_id
        })
    
    def api_send_protocol(self):
        """发送工控协议报文"""
        data = request.get_json()
        protocol = data.get('protocol')
        
        # 根据协议类型构造报文
        packet = self._build_protocol_packet(protocol, data)
        
        # 发送报文
        from scapy.all import sendp
        sendp(packet, iface=self.bind_interface, verbose=False)
        
        return jsonify({
            'status': 'sent',
            'protocol': protocol,
            'interface': self.bind_interface
        })
    
    def _build_protocol_packet(self, protocol, config):
        """构造工控协议报文"""
        # 这里实现各协议的报文构造
        # 从原有 industrial_protocol_agent.py 中迁移
        pass
    
    # ========== 端口扫描功能实现 ==========
    
    def api_start_scan(self):
        """启动端口扫描"""
        data = request.get_json()
        target_ip = data.get('target_ip')
        port_range = data.get('port_range', '1-1000')
        
        self.features['port_scan']['running'] = True
        self.features['port_scan']['progress'] = 0
        self.features['port_scan']['results'] = []
        self.stop_events['port_scan'].clear()
        
        def scan_ports():
            import socket
            ports = self._parse_port_range(port_range)
            total = len(ports)
            
            for i, port in enumerate(ports):
                if self.stop_events['port_scan'].is_set():
                    break
                
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1)
                    result = sock.connect_ex((target_ip, port))
                    if result == 0:
                        self.features['port_scan']['results'].append({
                            'port': port,
                            'status': 'open'
                        })
                    sock.close()
                except:
                    pass
                
                self.features['port_scan']['progress'] = (i + 1) / total * 100
            
            self.features['port_scan']['running'] = False
        
        self.features['port_scan']['thread'] = threading.Thread(target=scan_ports)
        self.features['port_scan']['thread'].start()
        
        return jsonify({'status': 'scanning', 'target': target_ip, 'range': port_range})
    
    def api_interface_info(self):
        """返回网卡信息"""
        return jsonify({
            'agent_id': self.agent_id,
            'interface': self.bind_interface,
            'ip': self.bind_ip,
            'port': self.port
        })


if __name__ == '__main__':
    agent = FullFeatureAgent()
    agent.start()
```

---

## 六、systemd 动态管理方案

### 6.1 Django sudo 权限配置

```bash
# /etc/sudoers.d/django-agent

# Django 用户可以管理 Agent 服务
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl start agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl enable agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl disable agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl status agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl daemon-reload

# Django 用户可以创建/删除服务文件
django ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/systemd/system/agent-*.service
django ALL=(ALL) NOPASSWD: /usr/bin/rm /etc/systemd/system/agent-*.service

# Django 用户可以查看日志
django ALL=(ALL) NOPASSWD: /usr/bin/journalctl -u agent-*
```

### 6.2 systemd 服务模板

```ini
# 自动生成的服务文件格式

[Unit]
Description=Packet Agent {AGENT_ID} ({INTERFACE})
After=network.target

[Service]
Type=simple
Environment="AGENT_ID={AGENT_ID}"
Environment="BIND_IP={BIND_IP}"
Environment="BIND_INTERFACE={INTERFACE}"
Environment="AGENT_PORT={PORT}"
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 七、项目目录结构

```
ubuntu_deploy/
├── djangoProject/          # Django 核心配置
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── main/                   # Django 主应用
│   ├── models.py           # 数据库模型（含 NetworkInterface, LocalAgent）
│   ├── views.py            # 视图函数
│   ├── urls.py             # 子路由
│   ├── agent_utils.py      # Agent 管理工具函数
│   └── interface_utils.py  # 网卡扫描工具函数
├── agents/                 # Agent 模块
│   ├── __init__.py
│   ├── base.py             # Agent 基类
│   ├── packet_agent.py     # 全功能 Agent
│   └── protocol_handlers/  # 工控协议处理器
│       ├── modbus.py
│       ├── s7.py
│       ├── goose.py
│       ├── dnp3.py
│       └── ...
├── templates/              # 前端模板
│   ├── base.html
│   ├── home.html
│   ├── interface_manage.html  # 网卡管理页面（新增）
│   ├── agent_config.html      # Agent 配置页面（新增）
│   ├── device_monitor.html
│   ├── firewall_policy.html   # 移除环境选择
│   ├── packet_send.html       # Agent 选择方式
│   ├── industrial_protocol.html
│   └── ...
├── static/
│   ├── css/
│   │   ├── custom.css      # 深色工业风主题
│   │   └── bootstrap.min.css
│   └── js/
├── deploy/                 # 部署配置
│   ├── django.service      # Django systemd 服务
│   ├── sudoers.django      # sudo 权限配置
│   └── setup.sh            # Ubuntu 初始化脚本
├── requirements.txt
├── manage.py
└── README.md
```

---

## 八、需要确认的问题

### 8.1 网卡管理方式

**问题**: 网卡信息是否需要自动扫描，还是手动添加？

- **方案 A**: 自动扫描（推荐）- Django 启动时自动扫描网卡
- **方案 B**: 手动添加 - 用户在界面手动输入网卡信息

### 8.2 Agent ID 生成方式

**问题**: Agent ID 如何生成？

- **方案 A**: 自动生成（A, B, C, D...）
- **方案 B**: 用户手动命名
- **方案 C**: 基于网卡名（如 eth1-agent）

### 8.3 管理网卡配置

**问题**: 如何指定管理网卡？

- **方案 A**: 在界面选择（推荐）
- **方案 B**: 在配置文件中指定
- **方案 C**: 自动选择第一个网卡

### 8.4 Agent 开机自启

**问题**: 新创建的 Agent 默认是否开机自启？

- **方案 A**: 默认不自启，用户手动配置
- **方案 B**: 默认自启

### 8.5 功能页面位置

**问题**: 网卡管理和 Agent 配置放在哪个位置？

- **方案 A**: 新增两个独立页面（网卡管理 + Agent 配置）
- **方案 B**: 合并为一个页面"Agent 管理"
- **方案 C**: 放在"测试设备"菜单下