# Ubuntu 部署项目功能模块讨论文档

## 一、核心决策确认

| 决策项 | 确认内容 |
|--------|---------|
| Agent 部署方式 | systemd 服务管理 |
| Agent 数量 | 4 个（3 个测试 Agent + 1 个管理网口） |
| 网卡绑定 | Agent 与网口一一对应 |
| Django 管理范围 | 仅本地 Agent |
| 测试环境管理 | 移除（不再需要远程 SSH 管理） |

---

## 二、网卡和 Agent 分配方案

### 2.1 推荐配置

```
Ubuntu 设备（4 网口工控机）
┌─────────────────────────────────────────────────────┐
│  eth0 (管理网口)                                      │
│  IP: 192.168.100.10                                  │
│  用途: Django Web 服务 + 用户访问                     │
│  端口: 8000 (Django)                                 │
├─────────────────────────────────────────────────────┤
│  eth1 (Agent-A)                                      │
│  IP: 192.168.1.10                                    │
│  用途: 报文发送/接收 Agent                            │
│  端口: 8888                                          │
│  绑定: 发送报文从 eth1 出，监听 192.168.1.10:8888    │
├─────────────────────────────────────────────────────┤
│  eth2 (Agent-B)                                      │
│  IP: 192.168.2.10                                    │
│  用途: 报文接收/监控 Agent                            │
│  端口: 8889                                          │
│  绑定: 监听 192.168.2.10:8889                        │
├─────────────────────────────────────────────────────┤
│  eth3 (Agent-C)                                      │
│  IP: 192.168.3.10                                    │
│  用途: 工控协议 Agent                                 │
│  端口: 8890                                          │
│  绑定: Modbus/S7/GOOSE 等协议                        │
└─────────────────────────────────────────────────────┘
```

### 2.2 systemd 服务配置

```ini
# /etc/systemd/system/django.service
[Unit]
Description=Django Web Service (Management Interface)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python manage.py runserver 192.168.100.10:8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/agent-a.service
[Unit]
Description=Packet Agent A (eth1 - 192.168.1.10)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=A"
Environment="BIND_IP=192.168.1.10"
Environment="BIND_INTERFACE=eth1"
Environment="AGENT_PORT=8888"
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/agent-b.service
[Unit]
Description=Packet Agent B (eth2 - 192.168.2.10)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=B"
Environment="BIND_IP=192.168.2.10"
Environment="BIND_INTERFACE=eth2"
Environment="AGENT_PORT=8889"
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

# /etc/systemd/system/agent-c.service
[Unit]
Description=Industrial Protocol Agent C (eth3 - 192.168.3.10)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=C"
Environment="BIND_IP=192.168.3.10"
Environment="BIND_INTERFACE=eth3"
Environment="AGENT_PORT=8890"
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python -m agents.industrial_protocol_agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 三、Agent 实现方式讨论

### 3.1 Agent 基类设计

```python
# agents/base.py
"""
Agent 基类 - 支持多实例、网卡绑定
"""
import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS

class BaseAgent:
    """Agent 基类"""
    
    def __init__(self):
        # 从环境变量读取配置（systemd 传入）
        self.agent_id = os.environ.get('AGENT_ID', 'A')
        self.bind_ip = os.environ.get('BIND_IP', '0.0.0.0')
        self.bind_interface = os.environ.get('BIND_INTERFACE', 'eth0')
        self.port = int(os.environ.get('AGENT_PORT', 8888))
        
        # Flask 应用
        self.app = Flask(__name__)
        CORS(self.app)
        
        # 日志
        self.logger = logging.getLogger(f'Agent-{self.agent_id}')
        
        # 注册通用 API
        self._register_common_api()
    
    def _register_common_api(self):
        """注册通用 API 接口"""
        
        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            """Agent 状态查询"""
            return jsonify({
                'agent_id': self.agent_id,
                'bind_ip': self.bind_ip,
                'bind_interface': self.bind_interface,
                'port': self.port,
                'status': 'running',
                'uptime': self._get_uptime()
            })
        
        @self.app.route('/api/health', methods=['GET'])
        def health_check():
            """健康检查"""
            return jsonify({'status': 'healthy'})
        
        @self.app.route('/api/shutdown', methods=['POST'])
        def shutdown():
            """优雅关闭（通过 systemd 管理）"""
            return jsonify({'status': 'shutdown_requested'})
    
    def _get_uptime(self):
        """获取运行时间"""
        import psutil
        process = psutil.Process(os.getpid())
        return process.create_time()
    
    def register_api(self, route, handler, methods=['GET']):
        """注册自定义 API"""
        self.app.route(route, methods=methods)(handler)
    
    def start(self):
        """启动 Agent"""
        self.logger.info(f"Agent-{self.agent_id} 启动")
        self.logger.info(f"绑定 IP: {self.bind_ip}, 网卡: {self.bind_interface}, 端口: {self.port}")
        
        # Flask 监听指定 IP 和端口
        self.app.run(
            host=self.bind_ip,
            port=self.port,
            threaded=True,
            use_reloader=False  # systemd 管理不需要自动重载
        )
```

### 3.2 报文发送 Agent 实现

```python
# agents/packet_agent.py
"""
报文发送 Agent - 绑定特定网卡
"""
from agents.base import BaseAgent
from scapy.all import sendp
import threading

class PacketAgent(BaseAgent):
    """报文发送 Agent"""
    
    def __init__(self):
        super().__init__()
        
        # 发送相关变量
        self.sending_thread = None
        self.stop_sending = threading.Event()
        self.statistics = {'total_sent': 0, 'rate': 0}
        
        # 注册报文发送 API
        self._register_packet_api()
    
    def _register_packet_api(self):
        """注册报文发送相关 API"""
        
        def get_interfaces():
            """返回当前 Agent 绑定的网卡信息"""
            return {
                'interface': self.bind_interface,
                'ip': self.bind_ip,
                'agent_id': self.agent_id
            }
        
        self.register_api('/api/interfaces', lambda: self._json_response(get_interfaces()))
        
        def send_packet():
            """发送报文"""
            data = request.get_json()
            
            # 注意：报文发送时使用 Agent 绑定的网卡
            # 不需要前端再指定网卡
            interface = self.bind_interface
            
            # 构造报文并发送
            packet = self._build_packet(data)
            sendp(packet, iface=interface, verbose=False)
            
            # 更新统计
            with threading.Lock():
                self.statistics['total_sent'] += 1
            
            return self._json_response({'status': 'sent', 'interface': interface})
        
        self.register_api('/api/send_packet', send_packet, methods=['POST'])
        
        def get_statistics():
            """获取发送统计"""
            return self._json_response(self.statistics)
        
        self.register_api('/api/statistics', get_statistics)
    
    def _build_packet(self, config):
        """构造报文"""
        from scapy.all import Ether, IP, TCP, UDP
        # ... 报文构造逻辑
        pass
    
    def _json_response(self, data):
        """返回 JSON 响应"""
        from flask import jsonify
        return jsonify(data)

if __name__ == '__main__':
    agent = PacketAgent()
    agent.start()
```

### 3.3 工控协议 Agent 实现

```python
# agents/industrial_protocol_agent.py
"""
工控协议 Agent - 支持 Modbus/S7/GOOSE 等
"""
from agents.base import BaseAgent

class IndustrialProtocolAgent(BaseAgent):
    """工控协议 Agent"""
    
    def __init__(self):
        super().__init__()
        
        # 协议处理器
        self.protocol_handlers = {
            'modbus': self._handle_modbus,
            's7': self._handle_s7,
            'goose': self._handle_goose,
            'sv': self._handle_sv,
            'dnp3': self._handle_dnp3,
            'bacnet': self._handle_bacnet,
            'enip': self._handle_enip,
            'mms': self._handle_mms,
        }
        
        self._register_protocol_api()
    
    def _register_protocol_api(self):
        """注册工控协议 API"""
        
        def get_supported_protocols():
            """获取支持的协议列表"""
            return {
                'protocols': list(self.protocol_handlers.keys()),
                'agent_id': self.agent_id,
                'interface': self.bind_interface
            }
        
        self.register_api('/api/protocols', lambda: self._json_response(get_supported_protocols()))
        
        def send_protocol_packet():
            """发送工控协议报文"""
            data = request.get_json()
            protocol = data.get('protocol')
            
            if protocol not in self.protocol_handlers:
                return self._json_response({'error': f'不支持的协议: {protocol}'}, 400)
            
            # 使用绑定网卡发送
            result = self.protocol_handlers[protocol](data, self.bind_interface)
            return self._json_response(result)
        
        self.register_api('/api/send_protocol', send_protocol_packet, methods=['POST'])

if __name__ == '__main__':
    agent = IndustrialProtocolAgent()
    agent.start()
```

---

## 四、功能模块改动讨论

### 4.1 模块改动概览

| 模块 | 原功能 | 新功能 | 改动程度 |
|------|--------|--------|---------|
| **测试设备** | 管理防火墙设备 | 保持不变 | ✅ 无改动 |
| **测试环境** | SSH 远程管理 Agent | **移除** | ❌ 整模块移除 |
| **本地 Agent** | 无 | 管理本地 systemd Agent | ✨ 新增 |
| **防火墙策略** | 选择环境 → 发送策略 | 直接发送到设备 | ⚠️ 移除环境选择 |
| **服务下发** | 选择环境 → 下发服务 | 直接下发到设备 | ⚠️ 移除环境选择 |
| **工控协议** | 选择环境 → 发送报文 | 选择本地 Agent → 发送报文 | ⚠️ 改交互方式 |
| **报文发送** | 选择环境 → 选择网卡 → 发送 | 选择本地 Agent（即网卡）→ 发送 | ⚠️ 改交互方式 |
| **报文回放** | 选择环境 → 回放 | 选择本地 Agent → 回放 | ⚠️ 改交互方式 |
| **端口扫描** | 选择环境 → 扫描 | 选择本地 Agent → 扫描 | ⚠️ 改交互方式 |
| **Syslog 接收** | 无环境选择 | 保持不变（本地服务） | ✅ 无改动 |
| **SNMP** | 无环境选择 | 保持不变（本地服务） | ✅ 无改动 |
| **知识库** | 无环境选择 | 保持不变 | ✅ 无改动 |
| **授权管理** | 无环境选择 | 保持不变 | ✅ 无改动 |

### 4.2 详细改动讨论

#### 4.2.1 测试设备（device_monitor）

**改动**: 无改动

**理由**: 测试设备是防火墙被测设备，与 Agent 管理无关

**保留功能**:
- 设备列表管理
- 设备连接测试
- 设备监测（CPU、内存、磁盘）
- 设备告警
- 设备命令执行

---

#### 4.2.2 测试环境（test_env）→ 移除

**改动**: 整模块移除

**理由**: 本地 Agent 不需要远程 SSH 管理

**替代方案**: 新增"本地 Agent 管理"模块

---

#### 4.2.3 本地 Agent 管理（新增）

**界面设计**:
```
┌─────────────────────────────────────────────────────┐
│  本地 Agent 管理                                     │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Agent 状态卡片                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Agent-A  │ │ Agent-B  │ │ Agent-C  │            │
│  │ eth1     │ │ eth2     │ │ eth3     │            │
│  │192.168.1.│ │192.168.2.│ │192.168.3.│            │
│  │ 状态:运行 │ │ 状态:运行 │ │ 状态:停止 │            │
│  │ [停止]   │ │ [停止]   │ │ [启动]   │            │
│  └──────────┘ └──────────┘ └──────────┘            │
│                                                      │
│  操作按钮                                            │
│  [全部启动] [全部停止] [查看日志]                     │
│                                                      │
│  Agent 日志                                          │
│  ┌─────────────────────────────────────────────────┐│
│  │ [Agent-A] 2024-01-15 10:30:00 发送报文 1000 个   ││
│  │ [Agent-B] 2024-01-15 10:30:00 接收报文 950 个    ││
│  │ ...                                             ││
│  └─────────────────────────────────────────────────┘│
│                                                      │
└─────────────────────────────────────────────────────┘
```

**API 接口**:
```python
# main/views.py 新增

def local_agent_list(request):
    """获取本地 Agent 列表"""
    agents_config = [
        {'id': 'A', 'interface': 'eth1', 'ip': '192.168.1.10', 'port': 8888, 'role': 'packet_sender'},
        {'id': 'B', 'interface': 'eth2', 'ip': '192.168.2.10', 'port': 8889, 'role': 'packet_receiver'},
        {'id': 'C', 'interface': 'eth3', 'ip': '192.168.3.10', 'port': 8890, 'role': 'industrial_protocol'},
    ]
    
    # 查询每个 Agent 的实际状态
    for agent in agents_config:
        try:
            resp = requests.get(f"http://{agent['ip']}:{agent['port']}/api/status", timeout=2)
            agent['status'] = resp.json().get('status', 'unknown')
            agent['uptime'] = resp.json().get('uptime')
        except:
            agent['status'] = 'offline'
    
    return JsonResponse({'agents': agents_config})

def local_agent_control(request):
    """控制本地 Agent（systemctl）"""
    agent_id = request.POST.get('agent_id')  # A, B, C
    action = request.POST.get('action')      # start, stop, restart
    
    # systemctl 控制
    result = subprocess.run(
        ['systemctl', action, f'agent-{agent_id.lower()}'],
        capture_output=True,
        text=True
    )
    
    return JsonResponse({
        'success': result.returncode == 0,
        'output': result.stdout,
        'error': result.stderr
    })

def local_agent_logs(request):
    """获取 Agent 日志"""
    agent_id = request.GET.get('agent_id')
    lines = int(request.GET.get('lines', 50))
    
    # 使用 journalctl 读取 systemd 日志
    result = subprocess.run(
        ['journalctl', '-u', f'agent-{agent_id.lower()}', '-n', str(lines), '--no-pager'],
        capture_output=True,
        text=True
    )
    
    return JsonResponse({'logs': result.stdout})
```

---

#### 4.2.4 防火墙策略（firewall_policy）

**改动**: 移除"测试环境"选择，直接发送到测试设备

**界面变化**:
```
原界面:
┌─────────────────────────────────┐
│ 选择测试环境: [下拉选择]         │  ← 移除
│ 选择测试设备: [下拉选择]         │
│ 策略配置...                     │
│ [发送策略]                      │
└─────────────────────────────────┘

新界面:
┌─────────────────────────────────┐
│ 选择测试设备: [下拉选择]         │
│ 策略配置...                     │
│ [发送策略]                      │
└─────────────────────────────────┘
```

**代码改动**:
```python
# main/views_with_cache.py 改动

# 原代码
def firewall_policy(request):
    test_envs = TestEnvironment.objects.all()  # ← 移除
    devices = TestDevice.objects.all()
    return render(request, 'firewall_policy.html', {
        'test_envs': test_envs,  # ← 移除
        'devices': devices
    })

# 新代码
def firewall_policy(request):
    devices = TestDevice.objects.all()
    return render(request, 'firewall_policy.html', {
        'devices': devices
    })
```

---

#### 4.2.5 报文发送（packet_send）

**改动**: 移除"测试环境"和"网卡"选择，改为选择"本地 Agent"

**界面变化**:
```
原界面:
┌─────────────────────────────────┐
│ 选择测试环境: [下拉选择]         │  ← 移除
│ 选择网卡: [下拉选择]             │  ← 移除（改为 Agent）
│ 报文配置...                     │
│ [发送报文]                      │
└─────────────────────────────────┘

新界面:
┌─────────────────────────────────┐
│ 选择发送 Agent:                  │
│ ┌──────────────────────────────┐│
│ │ ○ Agent-A (eth1 - 192.168.1.││
│ │ ○ Agent-B (eth2 - 192.168.2.││
│ │ ○ Agent-C (eth3 - 192.168.3.││
│ └──────────────────────────────┘│
│ 报文配置...                     │
│ [发送报文]                      │
└─────────────────────────────────┘
```

**代码改动**:
```python
# main/views_with_cache.py 改动

def packet_send(request):
    # 获取本地 Agent 列表
    agents = get_local_agents()  # 新函数
    return render(request, 'packet_send.html', {'agents': agents})

def send_packet_via_agent(request):
    """发送报文到本地 Agent"""
    data = json.loads(request.body)
    agent_id = data.get('agent_id')  # A, B, C
    
    # 获取 Agent 配置
    agent_config = get_agent_config(agent_id)
    
    # 发送 HTTP 请求到 Agent
    resp = requests.post(
        f"http://{agent_config['ip']}:{agent_config['port']}/api/send_packet",
        json=data
    )
    
    return JsonResponse(resp.json())
```

---

#### 4.2.6 工控协议（industrial_protocol）

**改动**: 选择"本地 Agent"而非"测试环境"

**界面变化**:
```
原界面:
┌─────────────────────────────────┐
│ 选择测试环境: [下拉选择]         │  ← 移除
│ 选择协议: [Modbus/S7/GOOSE...]  │
│ 协议配置...                     │
│ [发送报文]                      │
└─────────────────────────────────┘

新界面:
┌─────────────────────────────────┐
│ 选择协议 Agent:                  │
│ ┌──────────────────────────────┐│
│ │ ● Agent-C (eth3 - 工控协议)  ││  ← 工控协议固定用 Agent-C
│ └──────────────────────────────┘│
│ 选择协议: [Modbus/S7/GOOSE...]  │
│ 协议配置...                     │
│ [发送报文]                      │
└─────────────────────────────────┘
```

---

#### 4.2.7 报文回放（packet_replay）

**改动**: 选择"本地 Agent"而非"测试环境"

**界面变化**:
```
新界面:
┌─────────────────────────────────┐
│ 选择回放 Agent:                  │
│ ┌──────────────────────────────┐│
│ │ ○ Agent-A (eth1 - 发送)     ││
│ │ ○ Agent-B (eth2 - 接收)     ││
│ └──────────────────────────────┘│
│ 选择 PCAP 文件: [文件列表]      │
│ 回放配置...                     │
│ [开始回放]                      │
└─────────────────────────────────┘
```

---

#### 4.2.8 端口扫描（port_scan）

**改动**: 选择"本地 Agent"而非"测试环境"

**界面变化**:
```
新界面:
┌─────────────────────────────────┐
│ 选择扫描 Agent:                  │
│ ┌──────────────────────────────┐│
│ │ ○ Agent-A (eth1)            ││
│ │ ○ Agent-B (eth2)            ││
│ └──────────────────────────────┘│
│ 扫描目标 IP: [输入框]           │
│ 扫描端口范围: [输入框]          │
│ [开始扫描]                      │
└─────────────────────────────────┘
```

---

#### 4.2.9 Syslog 接收（syslog_receiver）

**改动**: 无改动（本地服务）

**理由**: Syslog 接收服务运行在 Django 主机上，与 Agent 无关

---

#### 4.2.10 SNMP（snmp）

**改动**: 无改动（本地服务）

**理由**: SNMP Trap 接收运行在 Django 主机上，与 Agent 无关

---

## 五、界面交互方式变化

### 5.1 原交互流程

```
用户 → 选择测试环境 → 选择网卡 → 配置参数 → 发送
           ↓
    SSH 连接到远程主机
           ↓
    获取远程网卡列表
           ↓
    发送 HTTP 请求到远程 Agent
           ↓
    Agent 发送报文
```

### 5.2 新交互流程

```
用户 → 选择本地 Agent（即网卡）→ 配置参数 → 发送
           ↓
    直接发送 HTTP 请求到本地 Agent
           ↓
    Agent 发送报文（使用绑定的网卡）
```

### 5.3 Agent 选择界面设计

**方案 A**: 下拉选择
```
选择 Agent: [Agent-A (eth1 - 192.168.1.10) ▼]
```

**方案 B**: 卡片选择（推荐）
```
┌────────────────────────────────────────────────────┐
│ 选择发送 Agent                                       │
│                                                      │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│ │ ● Agent-A    │ │ ○ Agent-B    │ │ ○ Agent-C    │ │
│ │ eth1         │ │ eth2         │ │ eth3         │ │
│ │ 192.168.1.10 │ │ 192.168.2.10 │ │ 192.168.3.10 │ │
│ │ 报文发送     │ │ 报文接收     │ │ 工控协议     │ │
│ │ 状态: 运行   │ │ 状态: 运行   │ │ 状态: 停止   │ │
│ └──────────────┘ └──────────────┘ └──────────────┘ │
│                                                      │
└────────────────────────────────────────────────────┘
```

---

## 六、需要进一步讨论的问题

### 6.1 Agent 角色分配

| Agent | 建议角色 | 说明 |
|-------|---------|------|
| Agent-A | 报文发送 | eth1，主动发送测试报文 |
| Agent-B | 报文接收/监控 | eth2，接收响应报文，统计丢包 |
| Agent-C | 工控协议 | eth3，专门处理工控协议 |

**问题**: 
- Agent-B 是否需要"接收统计"功能？
- Agent-C 是否需要支持所有工控协议，还是只支持特定协议？

### 6.2 Agent 配置来源

**方案 A**: 硬编码在 Django settings.py
```python
LOCAL_AGENTS = [
    {'id': 'A', 'interface': 'eth1', 'ip': '192.168.1.10', 'port': 8888},
    {'id': 'B', 'interface': 'eth2', 'ip': '192.168.2.10', 'port': 8889},
    {'id': 'C', 'interface': 'eth3', 'ip': '192.168.3.10', 'port': 8890},
]
```

**方案 B**: 存储在数据库（可配置）
```python
class LocalAgent(models.Model):
    agent_id = models.CharField(max_length=10)
    interface = models.CharField(max_length=20)
    ip = models.GenericIPAddressField()
    port = models.IntegerField()
    role = models.CharField(max_length=50)
```

**推荐**: 方案 A（硬编码），因为网卡配置相对固定

### 6.3 systemd 权限问题

**问题**: Django 需要 systemctl 权限来控制 Agent

**解决方案**:
- 方案 A: Django 以 root 用户运行（不推荐）
- 方案 B: 配置 sudo 免密（推荐）
```bash
# /etc/sudoers.d/django
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl start agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart agent-*
django ALL=(ALL) NOPASSWD: /usr/bin/journalctl -u agent-*
```

---

## 七、下一步行动

请确认以下问题，我将开始编写代码：

1. **Agent 配置方式**: 硬编码还是数据库？

2. **Agent 选择界面**: 下拉选择还是卡片选择？

3. **Agent 角色分配**: 是否按照建议的 A-发送、B-接收、C-工控协议？

4. **systemd 权限**: 是否使用 sudo 免密方案？

5. **是否先开始某个模块的改造**:
   - 先改造 Agent 模块？
   - 先改造 Django 后端 API？
   - 先改造 UI 界面？