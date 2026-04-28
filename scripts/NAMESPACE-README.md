# Network Namespace 使用指南

## 概述

此方案通过 Network Namespace 将 eth1 和 eth2 隔离到独立的网络空间，
模拟两台独立的机器，使流量必须经过物理网口传输（不走 lo）。

## 文件清单

```
scripts/
├── network-namespace-setup.sh   # 主要配置脚本
├── network-namespace.service    # systemd 开机启动服务
├── agent-eth1-ns.service        # eth1 agent namespace 服务
├── agent-eth2-ns.service        # eth2 agent namespace 服务
```

## 快速使用

### 1. 手动测试（一次性）

```bash
# 上传脚本到 Ubuntu
scp scripts/network-namespace-setup.sh zhangc@192.168.81.105:/opt/SFW-TEST3/scripts/

# SSH 到 Ubuntu
ssh zhangc@192.168.81.105

# 设置 namespace（需要 sudo）
sudo chmod +x /opt/SFW-TEST3/scripts/network-namespace-setup.sh
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh setup

# 查看状态
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh status

# 测试连通性（eth1 -> eth2，流量走物理网口）
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh test

# 恢复原配置
sudo /opt/SFW-TEST3/scripts/network-namespace-setup.sh restore
```

### 2. 开机启动（持久化）

```bash
# 上传所有服务文件
scp scripts/*.service zhangc@192.168.81.105:/tmp/
scp scripts/network-namespace-setup.sh zhangc@192.168.81.105:/opt/SFW-TEST3/scripts/

# SSH 到 Ubuntu
ssh zhangc@192.168.81.105

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
# 在 ns-eth2 启动 Modbus Server
ip netns exec ns-eth2 python -c "
import sys
sys.path.insert(0, '/opt/SFW-TEST3')
from agents.industrial_protocol_base import modbus_server
modbus_server.start('test', port=502, interface='192.168.12.100')
"

# 在 ns-eth1 连接并读取
ip netns exec ns-eth1 python -c "
import sys
sys.path.insert(0, '/opt/SFW-TEST3')
from agents.protocols.modbus_client import modbus_client
modbus_client.connect('192.168.12.100', 502, 'test')
result = modbus_client.read('test', 3, 0, 5)
print(f'Read result: {result}')
"

# 验证：抓包应显示 Modbus TCP 流量
```

## 故障排查

### 问题1：ping 不通

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