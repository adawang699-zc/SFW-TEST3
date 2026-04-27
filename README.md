# 防火墙测试平台

## 项目概述

基于 Ubuntu 的多 Agent 一体化防火墙自动化测试平台，支持：
- **多网卡 Agent 绑定**：每个 Agent 绑定特定网卡
- **全功能 Agent**：报文发送、工控协议、端口扫描、报文回放
- **systemd 服务管理**：稳定可靠的服务管理
- **深色工业风 UI**：专业美观的用户界面

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│  Ubuntu 设备（4 网口工控机）                          │
├─────────────────────────────────────────────────────┤
│  eth0 (管理网口)                                      │
│  IP: 192.168.100.10                                  │
│  Django Web 服务 (8000)                              │
├─────────────────────────────────────────────────────┤
│  eth1 → Agent_eth1 (端口 8888)                       │
│  eth2 → Agent_eth2 (端口 8889)                       │
│  eth3 → Agent_eth3 (端口 8890)                       │
└─────────────────────────────────────────────────────┘
```

## 安装步骤

### 1. Ubuntu 系统安装

- Ubuntu Server 22.04 LTS
- 最小化安装（无 GUI）

### 2. 网卡配置

编辑 `/etc/netplan/00-installer-config.yaml`:

```yaml
network:
  version: 2
  renderer: networkd
  ethernets:
    eth0:  # 管理网卡
      addresses: [192.168.100.10/24]
    eth1:  # Agent 网卡 1
      addresses: [192.168.1.10/24]
    eth2:  # Agent 网卡 2
      addresses: [192.168.2.10/24]
    eth3:  # Agent 网卡 3
      addresses: [192.168.3.10/24]
```

应用配置: `sudo netplan apply`

### 3. 运行初始化脚本

```bash
sudo chmod +x deploy/setup.sh
sudo ./deploy/setup.sh
```

### 4. 启动 Django

```bash
sudo systemctl start django
```

### 5. Web 界面配置

访问 `http://192.168.100.10:8000`

1. 进入"网卡 & Agent"页面
2. 点击"扫描网卡"获取网卡列表
3. 选择网卡创建 Agent
4. 点击"启动"运行 Agent

## 功能说明

### 网卡 & Agent 管理

- 自动扫描系统网卡
- 网卡-Agent 一一绑定
- Agent ID 格式: `agent_{网卡名}`
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
- **Agent 选择**：仅显示已租用的 Agent

### 报文回放

使用 tcpreplay 进行 PCAP 文件回放：
- **文件浏览**：支持目录导航、文件搜索、多文件选择
- **速率控制**：
  - 倍数模式：相对于原始速率的倍数
  - PPS 模式：每秒发送报文数
  - Mbps 模式：带宽速率控制
- **回放次数**：支持循环回放
- **实时统计**：显示已发送报文数、速率 (pps/Mbps)、进度
- **默认目录**：`/opt/pcap/`

## 开发环境

### Windows 开发 → Ubuntu 部署

```bash
# Windows 开发
git add .
git commit -m "feat: xxx"
git push

# Ubuntu 部署
cd /opt/sfw_deploy
git pull
sudo systemctl restart django
sudo systemctl restart agent-eth1
sudo systemctl restart agent-eth2
sudo systemctl restart agent-eth3
```

## 文件结构

```
ubuntu_deploy/
├── djangoProject/      # Django 配置
├── main/               # 主应用
│   ├── models.py       # 数据库模型
│   ├── views.py        # API 接口
│   └── urls.py         # URL 配置
├── agents/             # Agent 程序
│   ├── base.py         # Agent 基类
│   └── packet_agent.py # 全功能 Agent
├── templates/          # 前端模板
├── deploy/             # 部署配置
│   ├── django.service
│   ├── sudoers.django
│   └── setup.sh
└── static/             # 静态资源
```

## 技术栈

- Django 5.1
- Flask + Scapy (Agent)
- systemd (服务管理)
- SQLite3 (数据库)

## 许可证

MIT License