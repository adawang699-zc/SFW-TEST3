# 防火墙测试平台

## 项目概述

基于 Ubuntu 的多 Agent 一体化防火墙自动化测试平台，支持：
- **多网卡 Agent 绑定**：每个 Agent 绑定特定网卡，使用 Network Namespace 隔离
- **全功能 Agent**：报文发送、工控协议、端口扫描、报文回放
- **systemd 服务管理**：稳定可靠的服务管理，支持 namespace 模式
- **深色工业风 UI**：专业美观的用户界面

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│  Ubuntu 设备（多网口工控机）                          │
├─────────────────────────────────────────────────────┤
│  eth0 (管理网口)                                      │
│  IP: 192.168.81.140                                  │
│  Django Web 服务 (8000)                              │
├─────────────────────────────────────────────────────┤
│  eth1 → ns-eth1 → Agent_eth1 (11.11.11.11:8888)      │
│  eth2 → ns-eth2 → Agent_eth2 (11.11.11.12:8889)      │
│  eth3 → ns-eth3 → Agent_eth3 (11.11.11.13:8890)      │
│  eth4 → ns-eth4 → Agent_eth4 (11.11.11.14:8891)      │
│  eth5 → ns-eth5 → Agent_eth5 (11.11.11.15:8892)      │
│  eth6 → ns-eth6 → Agent_eth6 (11.11.11.16:8893)      │
│  eth7 → ns-eth7 → Agent_eth7 (11.11.11.17:8894)      │
└─────────────────────────────────────────────────────┘
```

## 网络架构

- **管理网络**: 192.168.81.140（SSH、Django、Windows 可访问）
- **业务网络**: 11.11.11.x 网段（Agent 监听，通过 Network Namespace 隔离）

| 服务 | 地址 | 说明 |
|------|------|------|
| SSH | 192.168.81.140:22 | 管理网络 SSH |
| Django | 192.168.81.140:8000 | 管理网络 Web 服务 |
| Agent | 11.11.11.x:port | 业务网络，通过 Django 代理访问 |

## 快速开始

### 首次部署（新环境）

在 Ubuntu 服务器上以 root 身份执行:

```bash
sudo python3 setup.py
```

此脚本会自动完成:
1. 安装系统依赖（Python、pip、git、nmap 等）
2. 创建 Python 虚拟环境 `/opt/venv`
3. 安装 Python 包依赖
4. 创建必要的工作目录
5. 交互式部署授权工具（从 10.40.24.17 SCP 拷贝）
6. 配置 sudo 权限
7. 安装 Django systemd 服务
8. 执行数据库迁移
9. 验证环境

### 同步代码到 Ubuntu

```bash
python sync_to_ubuntu.py
```

此脚本会自动完成：
1. 本地 git push
2. Ubuntu git pull
3. 重启 Django 服务
4. 重启所有 Agent 服务

### 重启 Ubuntu 服务

```bash
python restart_ubuntu.py
```

单独重启 Ubuntu 上的 Django 和 Agent 服务。

## 功能说明

### 网卡 & Agent 管理

- 自动扫描系统网卡
- 网卡-Agent 一一绑定
- Agent ID 格式: `agent_eth{网卡号}`
- 使用 Network Namespace 隔离各 Agent 网络
- 支持 systemd 服务管理（启动、停止、日志）

### 报文发送

- TCP/UDP/ICMP 报文构造
- 连续发送模式
- 发送统计

### 工控协议

支持协议:
- Modbus TCP (端口 502)
- Siemens S7 (端口 102)
- IEC61850 GOOSE/SV
- DNP3 (端口 20000)
- BACnet (端口 47808)
- Ethernet/IP (端口 44818)
- MMS (端口 102)

### 端口扫描

使用 Nmap 进行端口扫描，支持多种扫描类型：
- **扫描类型**：SYN (-sS)、TCP Connect (-sT)、UDP (-sU)、FIN (-sF)、Null (-sN)、Xmas (-sX)、ACK (-sA)
- **端口范围**：支持范围 (1-10000) 和列表 (22,80,443)
- **进度显示**：实时显示扫描进度和已发现的开放端口
- **服务识别**：自动识别开放端口的服务类型

### 报文回放

使用 tcpreplay 进行 PCAP 文件回放：
- **文件浏览**：支持目录导航、文件搜索、多文件选择
- **速率控制**：倍数模式、PPS 模式、Mbps 模式
- **回放次数**：支持循环回放
- **实时统计**：显示已发送报文数、速率、进度

### 网口管理

防火墙网口自协商、速率、双工模式测试功能：
- **设备选择**：选择防火墙设备，显示所有网口信息（名称、LINK状态、速率、双工）
- **拓扑检测**：自动检测 Agent 网口与防火墙网口的连接关系（通过 UP/DOWN 检测）
- **测试配置**：支持自协商、速率、双工模式组合测试
- **结果展示**：表格形式对比测试结果（PASS/FAIL）

## 文件结构

```
ubuntu_deploy/
├── manage.py            # Django 管理入口
├── sync_to_ubuntu.py    # 同步代码到 Ubuntu
├── restart_ubuntu.py    # 重启 Ubuntu 服务
├── coredump_monitor.py  # Coredump 监控模块
├── djangoProject/       # Django 配置
├── main/                # 主应用
│   ├── models.py        # 数据库模型
│   ├── views.py         # API 接口
│   └── urls.py          # URL 配置
├── agents/              # Agent 程序
│   ├── full_agent.py    # 全功能 Agent
│   ├── modules/         # 功能模块
│   └── protocols/       # 工控协议
├── templates/           # 前端模板
├── static/              # 静态资源
├── deploy/              # 部署配置
│   ├── agent-eth*.service
│   └── setup.sh
├── scripts/             # 辅助脚本
└── logs/                # 日志目录
```

## 技术栈

- Django 5.1
- Flask + Scapy (Agent)
- systemd + Network Namespace (服务管理)
- SQLite3 (数据库)

## 许可证

MIT License