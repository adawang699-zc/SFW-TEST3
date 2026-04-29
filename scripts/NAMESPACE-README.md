# Network Namespace 使用指南

## 概述

此方案通过 Network Namespace 将业务网卡隔离到独立的网络空间，
模拟多台独立机器，使流量必须经过物理网口传输（不走 lo）。

**已完成系统改造（2026-04-28）：**
- Django Agent 管理系统完全适配 namespace
- 网卡扫描自动检测 namespace 内网卡
- Agent 启动/停止/状态检查支持 namespace
- 新增 namespace 管理 API

## 文件清单

```
scripts/
├── network-namespace-setup.sh   # 动态 namespace 管理脚本（支持任意网卡）
├── network-namespace.service    # systemd 开机启动服务
├── agent-eth1-ns.service        # eth1 agent namespace 服务模板
├── agent-eth2-ns.service        # eth2 agent namespace 服务模板

main/
├── models.py                    # NetworkInterface 添加 namespace 字段
├── views.py                     # namespace 辅助函数 + Agent API 适配
├── urls.py                      # namespace 管理 API 路由
```

## 快速使用

### 1. 通过 Web 界面管理（推荐）

访问 Agent 管理页面：
- 网卡扫描：自动检测主 namespace 和子 namespace 网卡
- Agent 启动/停止：自动适配 namespace
- 状态显示：显示 namespace 标识

### 2. 通过 API 管理

```bash
# 获取 namespace 列表
curl 'http://192.168.81.140:8000/api/namespace/list/'

# 创建网卡 namespace
curl -X POST 'http://192.168.81.140:8000/api/namespace/setup-interface/' \
  -H 'Content-Type: application/json' \
  -d '{"interface_name":"eth1","ip_cidr":"192.168.11.100/16"}'

# 恢复网卡到主 namespace
curl -X POST 'http://192.168.81.140:8000/api/namespace/restore-interface/' \
  -H 'Content-Type: application/json' \
  -d '{"interface_name":"eth1"}'

# 扫描网卡（含 namespace）
curl -X POST 'http://192.168.81.140:8000/api/interfaces/scan/'

# Agent 列表（含 namespace 信息）
curl 'http://192.168.81.140:8000/api/agents/list/'
```

### 3. 通过命令行管理

```bash
# 设置单个网卡到 namespace
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh setup-interface eth1 192.168.11.100/16

# 启动 namespace 内 Agent
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh start-agent eth1

# 停止 namespace 内 Agent
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh stop-agent eth1

# 移除网卡 namespace
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh remove-interface eth1

# 查看所有 namespace 状态
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh status

# 设置所有业务网卡
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh setup-all

# 恢复所有网卡到主 namespace
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh restore-all
```
ssh zhangc@192.168.81.140

# 安装 systemd 服务
sudo cp /tmp/network-namespace.service /etc/systemd/system/
sudo cp /tmp/agent-eth1-ns.service /etc/systemd/system/
sudo cp /tmp/agent-eth2-ns.service /etc/systemd/system/

# 设置脚本权限
sudo chmod +x /opt/SFW-TEST3/scripts/network-namespace-setup.sh

# 禁用原有 agent 服务（避免冲突）
sudo systemctl disable agent-eth1.service
sudo systemctl disable agent-eth2.service

# 启用新的 namespace 服务
sudo systemctl daemon-reload
sudo systemctl enable network-namespace.service
sudo systemctl enable agent-eth1-ns.service
sudo systemctl enable agent-eth2-ns.service

# 启动服务
sudo systemctl start network-namespace.service
sudo systemctl start agent-eth1-ns.service
sudo systemctl start agent-eth2-ns.service

# 查看状态
sudo systemctl status network-namespace.service
sudo systemctl status agent-eth1-ns.service
sudo systemctl status agent-eth2-ns.service
```

### 3. 恢复原配置

```bash
# 停止 namespace 服务
sudo systemctl stop agent-eth1-ns.service
sudo systemctl stop agent-eth2-ns.service
sudo systemctl stop network-namespace.service

# 禁用 namespace 服务
sudo systemctl disable network-namespace.service
sudo systemctl disable agent-eth1-ns.service
sudo systemctl disable agent-eth2-ns.service

# 启用原有 agent 服务
sudo systemctl enable agent-eth1.service
sudo systemctl enable agent-eth2.service
sudo systemctl start agent-eth1.service
sudo systemctl start agent-eth2.service

# 验证
ping -I 192.168.11.100 192.168.12.100  # 应该走 lo，能通
```

## 工作原理

### 架构对比

**原配置（流量走 lo）：**
```
┌─────────────────────────────────────┐
│         Ubuntu 主 namespace         │
│                                     │
│  eth1 (192.168.11.100)              │
│       ↕ (内核本地路由)               │
│  eth2 (192.168.12.100)              │
│                                     │
│  流量直接在内核内转发，不经过物理网口  │
└─────────────────────────────────────┘
```

**Namespace 配置（流量走物理网口）：**
```
┌──────────────────┐    物理网线     ┌──────────────────┐
│   ns-eth1        │ ←──────────→ │   ns-eth2        │
│                  │               │                  │
│  eth1            │               │  eth2            │
│  192.168.11.100  │               │  192.168.12.100  │
│                  │               │                  │
│  agent-eth1      │               │  agent-eth2      │
│  (Modbus Client) │               │  (Modbus Server) │
└──────────────────┘               └──────────────────┘
     独立的网络栈                     独立的网络栈
     独立的 local 路由表              独立的 local 路由表
```

### 关键点

1. **每个 namespace 有独立的 local 路由表**
   - ns-eth1 的 local 表：只包含 192.168.11.100
   - ns-eth2 的 local 表：只包含 192.168.12.100

2. **跨 namespace 通信**
   - ns-eth1 发送包到 192.168.12.100 → 查路由表 → 目标不在 local → 发到 eth1
   - 包经过物理网线 → 到达 eth2
   - ns-eth2 接收包 → 查 local 表 → 目标是本机 → 处理并回复

3. **必须物理连接**
   - eth1 和 eth2 必须有网线连接
   - 否则跨 namespace 通信失败

## 验证方法

### 1. 检查 namespace 是否创建

```bash
ip netns list
# 应显示：
# ns-eth1
# ns-eth2
```

### 2. 检查接口是否在 namespace 内

```bash
# 主 namespace 不应看到 eth1/eth2
ip link show | grep -E "eth1|eth2"
# 应无输出或只看到其他接口

# namespace 内应看到接口
ip netns exec ns-eth1 ip link show eth1
ip netns exec ns-eth2 ip link show eth2
```

### 3. 检查 IP 配置

```bash
ip netns exec ns-eth1 ip addr show eth1
# 应显示 192.168.11.100

ip netns exec ns-eth2 ip addr show eth2
# 应显示 192.168.12.100
```

### 4. 测试连通性（关键！）

```bash
# 从 ns-eth1 ping ns-eth2
ip netns exec ns-eth1 ping 192.168.12.100

# 同时抓包验证流量经过物理网口
# 终端1：
ip netns exec ns-eth1 tcpdump -i eth1 -nn icmp

# 终端2：
ip netns exec ns-eth2 tcpdump -i eth2 -nn icmp

# 应能看到 ICMP 包
```

### 5. 测试 Modbus

```bash
# 方法1: 通过 Agent API（推荐）
# 启动 Modbus Server (in ns-eth2)
sudo ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{"config_id":"test","interface":"192.168.12.100","port":502}'

# 连接 Modbus Client (in ns-eth1)
sudo ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{"ip":"192.168.12.100","port":502,"client_id":"test"}'

# 读取数据
sudo ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{"client_id":"test","address":0,"count":5}'

# 抓包验证流量走物理网口
sudo ip netns exec ns-eth1 tcpdump -i eth1 -nn port 502 -c 10
sudo ip netns exec ns-eth2 tcpdump -i eth2 -nn port 502 -c 10
# 应看到 Modbus TCP 流量（192.168.11.100 > 192.168.12.100:502）
```

## 验证结果

**已验证成功（2026-04-28）：**
- ICMP Ping 流量：eth1 和 eth2 均抓到 5 packets
- Modbus TCP 流量：eth1 和 eth2 均抓到 20 packets
- 流量路径：192.168.11.100.端口 > 192.168.12.100.502 (请求) → 192.168.12.100.502 > 192.168.11.100.端口 (响应)
- **结论：流量走物理网口，不走 loopback！**

## 故障排查

### 问题0：本地连接不可达

**现象：**
```
curl: (7) Couldn't connect to server - 网络不可达
```

**原因：** namespace 内 loopback 接口未启用

**解决：**
```bash
sudo ip netns exec ns-eth1 ip link set lo up
sudo ip netns exec ns-eth2 ip link set lo up
```

**注意：** network-namespace-setup.sh 已添加 loopback 启用配置

**检查：**
```bash
# 检查接口状态
ip netns exec ns-eth1 ip link show eth1 | grep "state UP"
ip netns exec ns-eth2 ip link show eth2 | grep "state UP"

# 检查物理连接（接口 carrier）
ip netns exec ns-eth1 ip link show eth1 | grep "LOWER_UP"
```

**原因：** eth1 和 eth2 没有网线连接

### 问题2：服务启动失败

**检查：**
```bash
# 查看服务日志
journalctl -u agent-eth1-ns.service -n 50
journalctl -u agent-eth2-ns.service -n 50

# 检查 namespace 是否存在
ip netns list
```

### 问题3：恢复后 ping 失败

**检查：**
```bash
# 检查接口是否恢复到主 namespace
ip link show | grep eth1
ip link show | grep eth2

# 检查 IP 配置
ip addr show eth1
ip addr show eth2
```

**恢复：**
```bash
# 手动添加 IP
sudo ip addr add 192.168.11.100/16 dev eth1
sudo ip addr add 192.168.12.100/16 dev eth2
sudo ip link set eth1 up
sudo ip link set eth2 up
```