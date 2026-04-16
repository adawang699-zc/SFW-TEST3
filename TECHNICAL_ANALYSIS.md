# 现有项目技术分析报告

## 一、项目概述

**项目名称**: 防火墙自动化测试系统 (djangoProject)

**技术栈**:
- Django 5.2.8 + SQLite3
- Flask + Scapy (Agent 程序)
- Bootstrap 5 (前端)
- Paramiko (SSH 远程管理)

**核心功能**: 防火墙设备自动化测试，包括报文发送、工控协议测试、设备监控等

---

## 二、数据库模型分析

### 2.1 模型列表

| 模型名 | 功能 | 主要字段 |
|--------|------|---------|
| **S7ServerDBData** | S7 服务器 DB 块数据持久化 | server_id, db_number, data(Binary) |
| **TestEnvironment** | 测试环境配置 | name, ip, type(linux/windows), ssh_user, ssh_password, ssh_port |
| **ServiceTestCase** | 服务测试用例 | service_type, operation_type, name, desc, content, expected_success |
| **TestDevice** | 测试设备信息 | name, type, ip, port, user, password, backend_password, is_long_running |
| **DeviceAlertStatus** | 设备告警状态 | device_id, alert_type(cpu/memory/coredump), alert_value, is_ignored |
| **AlertConfig** | 告警配置 | smtp_server, smtp_port, sender_email, recipients, cpu_threshold |

### 2.2 数据库特点

- 使用 SQLite3（轻量级，适合单机部署）
- 支持设备监测和邮件告警功能
- 服务测试用例支持批量测试和期望结果验证

---

## 三、API 接口分析

### 3.1 API 路由统计

| 功能模块 | API 数量 | 主要接口 |
|---------|---------|---------|
| **设备管理** | 12 | /api/device/list/, /api/device/add/, /api/device/monitor_data/ |
| **测试环境** | 10 | /api/test_env/list/, /api/test_env/agent_control/, /api/test_env/agent_status/ |
| **防火墙策略** | 15 | /api/get_cookie/, /api/send_custom_service/, /api/send_packet_filter/ |
| **Agent 管理** | 15 | /api/agent/connect/, /api/agent/start/, /api/agent/stop/, /api/agent/status/ |
| **工控协议** | 5 | /api/get_protocol_files/, /api/send_custom_protocol/ |
| **端口扫描** | 4 | /api/port_scan/, /api/port_scan/progress/ |
| **Syslog** | 5 | /api/syslog/control/, /api/syslog/status/, /api/syslog/logs/ |
| **SNMP** | 5 | /api/snmp/get/, /api/snmp/trap/control/ |
| **知识库** | 12 | /api/knowledge/create/, /api/knowledge/templates/ |
| **授权管理** | 5 | /api/license/knowledge/generate/, /api/license/device/generate/ |
| **报文回放** | 5 | /api/packet_replay/start/, /api/packet_replay/status/ |

**总计**: 约 88 个 API 接口

### 3.2 Agent 管理方式（核心改动点）

**当前方式**: 远程 SSH 管理
- 通过 Paramiko 建立 SSH 连接
- 上传 Agent 文件到远程主机
- 通过 SSH 命令启动/停止 Agent
- 支持 Windows 和 Linux 双平台

**关键 API**:
```
/api/agent/connect/      - 建立 SSH 连接
/api/agent/upload/       - 上传 Agent 文件
/api/agent/start/        - 启动远程 Agent
/api/agent/stop/         - 停止远程 Agent
/api/agent/status/       - 查询 Agent 状态
/api/agent/logs/         - 获取 Agent 日志
```

---

## 四、Agent 程序分析

### 4.1 packet_agent.py 核心结构

```python
# Flask API 服务器
app = Flask(__name__)
CORS(app)

# 主要 API 接口
/api/interfaces          - 获取网卡列表
/api/send_packet         - 发送报文
/api/statistics          - 获取统计信息
/api/stop                - 停止发送
/api/shutdown            - 关闭 Agent
/api/health              - 健康检查
```

### 4.2 报文发送流程

```
Django 后端 → HTTP API → packet_agent.py → Scapy → 网卡
```

**关键函数**:
- `get_interfaces()` - 获取网卡列表（使用 psutil）
- `send_packets_worker()` - 报文发送工作线程
- `sendp(packet, iface=interface)` - Scapy 发送报文

### 4.3 Windows 特有代码（需改为 Ubuntu）

| 位置 | Windows 代码 | Ubuntu 替代方案 |
|------|-------------|----------------|
| **网卡获取** | `psutil.net_if_addrs()` + NPF 设备名 | `psutil.net_if_addrs()` + eth0/eth1 |
| **后台启动** | `pythonw ... > log.txt 2>&1` | `nohup python3 ... &` |
| **进程管理** | `taskkill /F /PID` | `kill -9 PID` 或 `systemctl` |
| **端口检查** | `netstat -ano | findstr "LISTENING"` | `ss -tlnp | grep :8888` |
| **文件权限** | `icacls ... /grant tdhx:F` | `chmod 755 ...` |
| **系统检测** | `ver` 命令检测 Windows | `uname -s` 检测 Linux |
| **日志读取** | `powershell Get-Content` | `tail -n 20` |

### 4.4 工控协议 Agent

**支持协议**:
- Modbus TCP
- S7 (Siemens)
- GOOSE/SV (IEC61850)
- DNP3
- BACnet
- ENIP (Ethernet/IP)
- MMS (Manufacturing Message Specification)
- EtherCAT
- PROFINET
- DCP (Discovery and Configuration Protocol)

**端口分配**:
- packet_agent.py: 8888
- industrial_protocol_agent.py: 8889

---

## 五、前端模板分析

### 5.1 页面列表

| 页面 | 模板文件 | 功能 |
|------|---------|------|
| 设备管理 | device_monitor.html | 测试设备管理、监测、告警 |
| 测试环境 | test_env.html | Agent 管理、远程控制 |
| 防火墙策略 | firewall_policy.html | 策略测试、用例管理 |
| 服务下发 | service_deploy.html | 服务配置下发 |
| 工控协议 | industrial_protocol.html | 工控协议测试 |
| 报文发送 | packet_send.html | 报文构造和发送 |
| 报文回放 | packet_replay.html | PCAP 文件回放 |
| 端口扫描 | port_scan.html | 端口扫描工具 |
| DHCP 客户端 | dhcp_client.html | DHCP 测试 |
| Syslog 接收 | syslog_receiver.html | 日志接收和显示 |
| SNMP | snmp.html | SNMP 监控 |
| 知识库 | knowledge_base.html | 知识库管理 |
| 授权管理 | license_management.html | 许可证生成 |
| Agent 同步 | agent_sync.html | Agent 自动同步 |
| 数据恢复 | restore_data.html | 数据恢复工具 |

**总计**: 15 个功能页面 + base.html + home.html

### 5.2 当前 UI 风格

**主题**: 清新科技蓝（浅色）
```css
:root {
    --bg-dark: #f1f5f9;       /* 浅灰背景 */
    --bg-card: #ffffff;       /* 白色卡片 */
    --primary-color: #3b82f6; /* 蓝色主色 */
    --text-primary: #1e293b;  /* 深灰文字 */
}
```

**特点**:
- Bootstrap 5 框架
- 固定顶部导航栏（56px）
- 固定左侧边栏（250px）
- 卡片式布局
- 表格和表单样式定制

### 5.3 深色工业风参考设计

**目标主题**: 深色工业风（training_presentation.html）
```css
:root {
    --bg-deep: #0f0f23;       /* 最深背景 */
    --bg-dark: #1a1a2e;       /* 主背景 */
    --bg-card: #16213e;       /* 卡片背景 */
    --primary: #ff6b35;       /* 橙红主色 */
    --secondary: #00a896;     /* 青色辅色 */
    --text-primary: #ffffff;  /* 白色文字 */
    --glow: 0 0 40px rgba(255, 107, 53, 0.3); /* 发光效果 */
}
```

**视觉元素**:
- 网格纹理背景
- 渐变光晕效果
- 发光边框卡片
- 橙红渐变按钮
- 状态指示灯动画

---

## 六、Windows → Ubuntu 迁移改动清单

### 6.1 Agent 程序改动

| 改动项 | 说明 | 优先级 |
|--------|------|--------|
| **后台启动方式** | `pythonw` → `nohup python3 &` | ⭐⭐⭐ 高 |
| **进程管理** | `taskkill` → `kill` 或 `systemctl` | ⭐⭐⭐ 高 |
| **网卡绑定** | NPF 设备名 → eth0/eth1 | ⭐⭐⭐ 高 |
| **端口检查** | `netstat -ano` → `ss -tlnp` | ⭐⭐ 中 |
| **文件权限** | `icacls` → `chmod` | ⭐⭐ 中 |
| **日志读取** | `powershell Get-Content` → `tail` | ⭐ 低 |
| **系统检测** | 移除 Windows 检测分支 | ⭐ 低 |

### 6.2 Django 后端改动

| 改动项 | 说明 | 优先级 |
|--------|------|--------|
| **Agent 管理方式** | SSH 远程 → 本地 systemd | ⭐⭐⭐ 高 |
| **Agent 状态查询** | SSH 命令 → HTTP API | ⭐⭐⭐ 高 |
| **网卡配置** | Windows 风格路径 → Linux 路径 | ⭐⭐ 中 |
| **日志文件路径** | Windows 路径 → Linux 路径 | ⭐⭐ 中 |

### 6.3 新增模块

| 模块 | 说明 | 优先级 |
|------|------|--------|
| **agents/base.py** | Agent 基类，支持多实例 | ⭐⭐⭐ 高 |
| **agents/services.py** | systemd 服务管理 | ⭐⭐⭐ 高 |
| **deploy/systemd/*.service** | systemd 服务配置文件 | ⭐⭐⭐ 高 |
| **deploy/setup.sh** | Ubuntu 初始化脚本 | ⭐⭐ 中 |
| **static/css/custom.css** | 深色工业风 CSS | ⭐⭐ 中 |

---

## 七、多 Agent 架构设计

### 7.1 当前架构问题

```
问题1: 单实例硬编码
- packet_agent.py 只支持单实例（端口固定 8888）
- 无法在同一主机运行多个 Agent 实例

问题2: 无网卡绑定参数
- 报文发送时通过 interface 参数指定网卡
- 但 Agent 启动时未绑定特定网卡 IP

问题3: 远程 SSH 管理依赖
- 需要建立 SSH 连接才能管理 Agent
- Ubuntu 本地部署不需要 SSH
```

### 7.2 新架构设计

```python
# agents/base.py - Agent 基类
class BaseAgent:
    def __init__(self, agent_id: str, bind_ip: str, bind_interface: str, port: int):
        self.agent_id = agent_id
        self.bind_ip = bind_ip          # 绑定的网卡 IP
        self.bind_interface = bind_interface  # 发送报文使用的网卡名
        self.port = port

    def start(self):
        app.run(host=self.bind_ip, port=self.port)
```

```ini
# deploy/systemd/agent-a.service
[Service]
Environment="AGENT_ID=A" "BIND_IP=192.168.1.10" "BIND_INTERFACE=eth0"
ExecStart=/opt/venv/bin/python -m agents.packet_agent
```

### 7.3 多实例启动方式

```bash
# 方式1: systemd 服务（推荐）
systemctl start agent-a   # eth0, 192.168.1.10
systemctl start agent-b   # eth1, 192.168.2.10
systemctl start agent-c   # eth2, 192.168.3.10

# 方式2: 命令行启动（开发测试）
python -m agents.packet_agent --id A --bind 192.168.1.10 --iface eth0 --port 8888
python -m agents.packet_agent --id B --bind 192.168.2.10 --iface eth1 --port 8889
```

---

## 八、部署工作流设计

### 8.1 开发流程

```
Windows 开发环境                      Ubuntu 测试环境
    │                                     │
    │ 1. 编写/修改代码                      │
    │ 2. 本地测试                           │
    │ 3. git commit                         │
    │ 4. git push                           │
    └──────────────────────────────────────►│
    │                                      │ 5. git pull
    │                                      │ 6. systemctl restart
    │                                      │ 7. 功能测试
    │◄──────────────────────────────────────│
    │                                      │ 8. 反馈问题
    │ 9. 修复问题                           │
```

### 8.2 systemd 服务配置

```ini
# Django 服务
[Unit]
Description=Django Web Service
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/sfw_deploy
ExecStart=/opt/venv/bin/python manage.py runserver 192.168.100.10:8000
Restart=always

[Install]
WantedBy=multi-user.target

# Agent-A 服务
[Unit]
Description=Packet Agent A (eth0)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=A" "BIND_IP=192.168.1.10" "BIND_INTERFACE=eth0"
ExecStart=/opt/venv/bin/python -m agents.packet_agent
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 九、下一步建议

### 9.1 第一阶段任务

1. **创建新项目结构**
   - 在 `ubuntu_deploy/` 创建 Django 项目
   - 复制并精简 models.py、urls.py

2. **Agent 模块化改造**
   - 创建 `agents/` 目录
   - 实现 Agent 基类和多实例支持
   - 创建 systemd 服务配置

3. **本地 Agent 管理改造**
   - 替换 SSH 管理为 systemd + HTTP API
   - 新增本地 Agent 状态查询接口

### 9.2 技术选型确认

| 选择项 | 推荐方案 | 说明 |
|--------|---------|------|
| **数据库** | SQLite3 | 继续使用，轻量级适合单机 |
| **Agent 进程管理** | systemd | Ubuntu 标准服务管理 |
| **网卡绑定** | IP + 网卡名 | 支持多网口隔离 |
| **前端框架** | Bootstrap 5 | 继续使用，深色化改造 |
| **Python 版本** | 3.10+ | Ubuntu 22.04 默认版本 |

---

## 十、风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Scapy 网卡绑定差异 | Ubuntu 网卡名 eth0 vs Windows NPF | 测试验证网卡绑定 |
| systemd 服务权限 | 需要 root 权限启动服务 | 使用 sudo 或 root 用户 |
| 多实例端口冲突 | 多 Agent 需要不同端口 | 配置不同端口 |
| UI 改造影响功能 | CSS 改动可能影响交互 | 分步改造，逐步验证 |