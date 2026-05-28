# OPC 协议族仿真模拟设计文档

**日期**: 2026-05-28
**作者**: Claude (Brainstorming Session)
**状态**: 待审核

---

## 1. 概述

### 1.1 项目目标

为 Agent 工控协议管理系统实现 OPC 协议族的仿真模拟功能，支持：
- **OPC UA Server**: 完整的数据访问、历史数据、报警事件功能
- **OPC UA Client**: 测试验证用途的客户端功能
- **OPC Classic**: 通过 Windows UaGateway 网关间接支持，提供辅助管理功能

### 1.2 需求总结

| 需求项 | 决定 |
|--------|------|
| 核心优先级 | OPC UA 为主，Classic 通过网关 |
| UA 功能范围 | 数据访问 + 历史数据 + 报警事件 |
| 模拟数据类型 | 通用工业数据（温度/压力/流量等） |
| Client 用途 | 测试验证为主 |
| Agent 集成 | 与现有 Agent 架构集成 |
| Classic 方案 | 提供辅助管理功能（状态监控、配置模板、前端显示） |
| 技术方案 | asyncua (Python 异步 OPC UA 库) |

---

## 2. 架构设计

### 2.1 文件结构

```
agents/protocols/
├── opcua_server.py      # OPC UA 服务端模拟器
├── opcua_client.py      # OPC UA 客户端
├── opcua_common.py      # 公共定义（数据类型、模拟模式）
├── opcua_gateway.py     # Classic 网关辅助管理
└── __init__.py          # 更新导出
```

### 2.2 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     industrial_protocol_base.py                  │
│                      (Flask API + Blueprint)                     │
│                                                                  │
│   /api/industrial/opcua_server/*    OPC UA Server API           │
│   /api/industrial/opcua_client/*    OPC UA Client API           │
│   /api/industrial/opcua_gateway/*   Gateway 辅助 API            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP API 调用
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                     Agent (远程主机运行)                          │
│                                                                  │
│   opcua_server.py                                                 │
│     ├─ asyncua Server 实例                                        │
│     ├─ 地址空间（SimulationDevice 对象）                          │
│     ├─ 变量节点（Temperature/Pressure/Flow/...）                  │
│     ├─ 历史数据缓冲                                               │
│     ├─ 报警事件生成器                                             │
│     └─ 数据更新循环（后台线程）                                    │
│                                                                  │
│   opcua_client.py                                                 │
│     ├─ asyncua Client 实例                                        │
│     ├─ 连接管理                                                   │
│     ├─ 节点浏览                                                   │
│     ├─ 数据读写                                                   │
│     └─ 历史数据查询                                               │
│                                                                  │
│   opcua_gateway.py                                                │
│     ├─ UA Server 连通性检测                                       │
│     ├─ 网关状态显示                                               │
│     └─ 配置模板生成                                               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ OPC UA 协议 (opc.tcp://xxx:4840)
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              Windows 虚拟机 - UaGateway 网关                      │
│               (用户自行部署，提供辅助管理)                         │
│                                                                  │
│   UaGateway.DA    → OPC Classic 数据访问                         │
│   UaGateway.HDA   → OPC Classic 历史数据访问                     │
│   UaGateway.AE    → OPC Classic 报警与事件                       │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 与现有协议对比

| 协议 | 库 | 线程模式 | API 路径前缀 |
|------|------|------|------|
| Modbus | pymodbus | asyncio + thread | `/api/industrial/modbus_*` |
| S7 | snap7 | thread | `/api/industrial/s7_*` |
| ENIP | 原生实现 | thread | `/api/industrial/enip_*` |
| BACnet | bacpypes3 | asyncio + thread | `/api/industrial/bacnet_*` |
| **OPC UA** | **asyncua** | **asyncio + thread** | `/api/industrial/opcua_*` |

---

## 3. OPC UA Server 设计

### 3.1 类结构

```python
class OpcUaServer:
    """OPC UA 服务端模拟器"""
    
    # 状态管理
    _running: bool
    _server: Server        # asyncua Server 实例
    _loop: asyncio.AbstractEventLoop
    _thread: threading.Thread
    
    # 配置
    _host: str             # 绑定地址（默认 0.0.0.0）
    _port: int             # 端口（默认 4840）
    _server_name: str      # 服务器名称
    _namespace: str        # 命名空间 URI
    _idx: int              # 命名空间索引
    
    # 数据存储
    _datastore: Dict       # 变量配置（类型、参数、节点引用）
    _history: Dict         # 历史数据缓冲（每个变量的 deque）
    _event_generators: Dict  # 报警事件生成器
    
    # 核心方法
    start(host, port, server_name, update_interval, history_size) -> Tuple[bool, str]
    stop() -> Tuple[bool, str]
    status() -> Dict
    get_variables() -> List[str]
    get_history(variable, count) -> List[Dict]
```

### 3.2 地址空间设计

```
Objects
└── SimulationDevice (对象)
    ├── Temperature       (Float, 正弦波模拟, 周期60s)
    ├── Pressure          (Float, 随机波动)
    ├── Flow              (Float, 阶梯波)
    ├── Speed             (Float, 正弦波)
    ├── Level             (Float, 随机波动)
    ├── SwitchState       (Boolean, 定时切换)
    ├── AlarmActive       (Boolean, 报警状态)
    ├── Counter           (Int32, 计数器)
    ├── Mode              (Int32, 运行模式)
    ├── ResetCounter      (方法, 重置计数器)
    └── SetMode           (方法, 设置运行模式)
```

### 3.3 数据模拟模式

| 变量 | 模式 | 参数 | 说明 |
|------|------|------|------|
| Temperature | sine | base=25, amp=5, period=60s | 正弦波动，25±5°C |
| Pressure | random | base=100, range=10 | 随机波动，100±10 |
| Flow | step | steps=[40,50,60,70] | 阶梯波，周期切换 |
| Speed | sine | base=1500, amp=200, period=30s | 转速波动 |
| Level | random | base=50, range=15 | 液位波动 |
| SwitchState | toggle | interval=120s | 每2分钟切换 |
| Counter | counter | max=1000 | 循环计数 |

### 3.4 报警事件机制

- **触发条件**: Temperature > 30°C 触发高温报警
- **清除条件**: Temperature < 28°C 清除报警
- **事件类型**: BaseEventType，携带 Severity、Message、SourceNode、Time
- **事件记录**: 保存到历史缓冲，供 HDA 查询

### 3.5 历史数据存储

- **存储方式**: 每个变量独立的 `deque` 缓冲
- **默认容量**: 10000 条记录
- **记录内容**: timestamp、value、quality
- **查询接口**: 按时间范围或按数量返回

---

## 4. OPC UA Client 设计

### 4.1 类结构

```python
class OpcUaClient:
    """OPC UA 客户端"""
    
    # 状态管理
    _connected: bool
    _client: Client        # asyncua Client 实例
    _loop: asyncio.AbstractEventLoop
    _thread: threading.Thread
    
    # 配置
    _endpoint: str         # 目标端点 URL
    _security_mode: str    # 安全模式（None/Sign/SignAndEncrypt）
    _connect_time: str     # 连接时间
    
    # 核心方法
    connect(endpoint, security_mode) -> Tuple[bool, str]
    disconnect() -> Tuple[bool, str]
    status() -> Dict
    
    # 数据访问
    browse(node_id) -> Tuple[bool, List, str]
    read(node_id) -> Tuple[bool, Any, str]
    write(node_id, value) -> Tuple[bool, str]
    
    # 历史数据
    read_history(node_id, start_time, end_time) -> Tuple[bool, List, str]
    
    # 方法调用
    call_method(object_node, method_name, args) -> Tuple[bool, Any, str]
```

### 4.2 支持的操作

| 操作 | 说明 | 参数 |
|------|------|------|
| **连接** | 连接到 UA Server | endpoint, security_mode |
| **浏览** | 获取节点子节点列表 | node_id (默认 "Objects") |
| **读取** | 读取单个节点值 | node_id |
| **写入** | 写入单个节点值 | node_id, value |
| **历史查询** | 查询历史数据 | node_id, start_time, end_time |
| **方法调用** | 调用服务器方法 | object_node, method_name, args |

### 4.3 安全模式支持

| 模式 | 说明 | 使用场景 |
|------|------|------|
| None | 无安全（测试环境） | 开发测试 |
| Sign | 签名模式 | 需要验证 |
| SignAndEncrypt | 签名加密 | 生产环境 |

### 4.4 错误处理

- 连接失败: 返回错误信息，不抛异常
- 节点不存在: 返回 `(False, None, "节点不存在")`
- 权限不足: 返回错误状态码
- 超时处理: 默认 10 秒超时，历史查询 30 秒

---

## 5. Gateway 辅助功能设计

### 5.1 类结构

```python
class OpcUaGatewayHelper:
    """OPC Classic 网关辅助管理"""
    
    # 状态检测
    check_uaserver_reachable(host, port) -> Dict
    get_gateway_status() -> Dict
    
    # 配置辅助
    generate_config_template() -> str    # 返回 Markdown
    generate_dcom_checklist() -> str     # 返回 Markdown
    
    # 信息展示
    get_deployment_guide() -> str        # 返回 Markdown
    get_connection_diagram() -> str      # 返回 HTML
```

### 5.2 辅助功能列表

| 功能 | 说明 | 输出 |
|------|------|------|
| **连通性检测** | Ping UA Server 端口 | reachable: bool |
| **配置模板** | 生成 UaGateway 配置步骤 | Markdown 文档 |
| **DCOM 清单** | DCOM 配置检查步骤 | Markdown 文档 |
| **部署指南** | Windows VM + UaGateway 部署步骤 | Markdown 文档 |
| **架构图** | UA → Gateway → Classic 拓扑 | 文本/HTML 图 |

---

## 6. API 路由设计

### 6.1 Server API

| 路由 | 方法 | 说明 | 参数 |
|------|------|------|------|
| `/api/industrial/opcua_server/start` | POST | 启动服务端 | host, port, server_name |
| `/api/industrial/opcua_server/stop` | POST | 停止服务端 | - |
| `/api/industrial/opcua_server/status` | GET | 获取状态 | - |
| `/api/industrial/opcua_server/variables` | GET | 获取变量列表 | - |
| `/api/industrial/opcua_server/history/<var>` | GET | 获取历史数据 | count |

### 6.2 Client API

| 路由 | 方法 | 说明 | 参数 |
|------|------|------|------|
| `/api/industrial/opcua_client/connect` | POST | 连接服务器 | endpoint, security_mode |
| `/api/industrial/opcua_client/disconnect` | POST | 断开连接 | - |
| `/api/industrial/opcua_client/status` | GET | 获取状态 | - |
| `/api/industrial/opcua_client/browse` | POST | 浏览节点 | node_id |
| `/api/industrial/opcua_client/read` | POST | 读取值 | node_id |
| `/api/industrial/opcua_client/write` | POST | 写入值 | node_id, value |
| `/api/industrial/opcua_client/history` | POST | 查询历史 | node_id, start_time, end_time |

### 6.3 Gateway API

| 路由 | 方法 | 说明 | 参数 |
|------|------|------|------|
| `/api/industrial/opcua_gateway/check` | POST | 检测连通性 | host, port |
| `/api/industrial/opcua_gateway/guide` | GET | 获取部署指南 | - |
| `/api/industrial/opcua_gateway/dcom` | GET | 获取 DCOM 清单 | - |

---

## 7. 前端设计

### 7.1 Tab 结构

在 `industrial_protocol.html` 中新增 OPC UA Tab:

```
OPC UA Tab
├── OPC UA Server（左侧）
│   ├── Agent 选择器
│   ├── 启动/停止按钮
│   ├── 状态显示
│   ├── 变量列表表格
│   └── 历史数据查看
│
├── OPC UA Client（右侧）
│   ├── Agent 选择器
│   ├── 连接配置（endpoint, security_mode）
│   ├── 节点浏览树
│   ├── 数据读写表单
│   └── 历史查询表单
│
└── OPC Classic 网关说明（底部）
    ├── 连通性检测按钮
    ├── 部署指南链接
    ├── DCOM 配置清单链接
    └── 架构示意图
```

### 7.2 JavaScript 函数

```javascript
// Server 函数
async function opcuaServerStart() { ... }
async function opcuaServerStop() { ... }
async function opcuaServerStatus() { ... }
async function opcuaGetVariables() { ... }
async function opcuaGetHistory(variable) { ... }

// Client 函数
async function opcuaClientConnect() { ... }
async function opcuaClientDisconnect() { ... }
async function opcuaBrowse() { ... }
async function opcuaRead() { ... }
async function opcuaWrite() { ... }
async function opcuaReadHistory() { ... }

// Gateway 函数
async function checkGateway() { ... }
function showGatewayGuide() { ... }
function showDcomChecklist() { ... }
```

---

## 8. 依赖与部署

### 8.1 Python 依赖

```bash
pip install asyncua>=1.0.0
```

### 8.2 OPC Classic 部署指南（用户自行部署）

**Windows 虚拟机配置**:
- 系统: Windows 10 LTSC 或 Windows Server 2019 Core
- 资源: 1C2G, 15GB 磁盘
- 网络: 桥接模式，与 Ubuntu 同网段

**UaGateway 安装**:
1. 下载: https://www.unified-automation.com/downloads/opc-ua-gateway.html
2. 安装免费版（支持 1000 标签）
3. 配置 UA Server 连接（输入 Ubuntu 的 opc.tcp://IP:4840）
4. 启用 DA/HDA/AE 三个 Classic Server

**DCOM 配置要点**:
1. 运行 `dcomcnfg`，配置 UaGateway 组件权限
2. 添加 "Everyone" 用户，赋予启动/访问权限
3. 开放端口 135 和动态端口范围 1024-65535
4. 选择 "交互式用户" 作为运行身份

---

## 9. 实现计划

### 9.1 实现顺序

1. 创建 `opcua_common.py` - 公共定义和模拟数据生成函数
2. 创建 `opcua_server.py` - OPC UA 服务端实现
3. 创建 `opcua_client.py` - OPC UA 客户端实现
4. 创建 `opcua_gateway.py` - Gateway 辅助功能
5. 更新 `agents/protocols/__init__.py` - 导出新模块
6. 在 `industrial_protocol_base.py` 中添加 API 路由
7. 在 `industrial_protocol.html` 中添加 OPC UA Tab
8. 测试验证

### 9.2 预估工作量

| 模块 | 预估时间 |
|------|------|
| opcua_common.py | 1 小时 |
| opcua_server.py | 3 小时 |
| opcua_client.py | 2 小时 |
| opcua_gateway.py | 1 小时 |
| API 路由 | 1 小时 |
| 前端页面 | 2 小时 |
| 测试验证 | 1 小时 |
| **总计** | **约 10 小时** |

---

## 10. 验收标准

- [ ] OPC UA Server 可启动/停止，支持 Agent 选择
- [ ] Server 自动生成模拟数据（温度/压力/流量等）
- [ ] Server 支持历史数据存储和查询
- [ ] Server 支持报警事件触发
- [ ] OPC UA Client 可连接/断开 UA Server
- [ ] Client 可浏览节点树
- [ ] Client 可读写节点值
- [ ] Client 可查询历史数据
- [ ] Gateway 辅助功能（连通性检测、部署指南）
- [ ] 前端页面完整，与现有协议风格一致
- [ ] API 返回格式统一（success/message/data）

---

## 附录：节点 ID 格式说明

OPC UA 节点 ID 格式示例:
- `ns=2;s=SimulationDevice.Temperature` - 字符串标识符
- `ns=2;i=1001` - 整数标识符
- `i=84` - Objects 节点（标准命名空间）

变量节点 ID:
```
Temperature:  ns=2;s=SimulationDevice.Temperature
Pressure:     ns=2;s=SimulationDevice.Pressure
Flow:         ns=2;s=SimulationDevice.Flow
SwitchState:  ns=2;s=SimulationDevice.SwitchState
AlarmActive:  ns=2;s=SimulationDevice.AlarmActive
Counter:      ns=2;s=SimulationDevice.Counter
```