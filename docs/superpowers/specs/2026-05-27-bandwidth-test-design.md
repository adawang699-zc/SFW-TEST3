# 带宽测试功能设计文档

**日期：** 2026-05-27  
**作者：** Claude  
**版本：** 1.0

---

## 概述

本文档设计一个带宽测试功能，用于测试防火墙的带宽管理策略是否生效。通过iperf工具在两个Agent网口之间生成流量，流量经过防火墙，实时显示带宽数据，验证防火墙带宽限制效果。

### 核心需求

| 需求项 | 详情 |
|--------|------|
| **测试场景** | 两个Agent网口串接防火墙，测试带宽管理 |
| **Agent选择** | 必须是当前用户租用的Agent |
| **IP显示** | 只显示当前IP，不提供配置功能 |
| **iperf参数** | 协议类型、测试时间、MTU、带宽目标、端口 |
| **实时显示** | 瞬时速度、平均速度、峰值速度、传输总量 |
| **动画效果** | 数据流动动画 + 数值仪表盘 |
| **测试结果** | 简单摘要（平均带宽、峰值、总传输量） |
| **测试控制** | 手动停止 + 异常检测 |

---

## 第一章：整体架构设计

### 1.1 页面结构

创建独立页面 `bandwidth_test.html`，导航栏新增入口。

页面分为四个区域：

```
┌─────────────────────────────────────────────────────┐
│  带宽测试                                    [导航栏] │
├─────────────────────────────────────────────────────┤
│  Agent选择区                                         │
│  - 用户标识符输入                                    │
│  - 显示当前用户租用的Agent列表                       │
│  - 选择Agent A（Server端）                           │
│  - 选择Agent B（Client端）                           │
│  - 显示两个Agent的IP地址                             │
├─────────────────────────────────────────────────────┤
│  参数配置区                                          │
│  - 协议类型（TCP/UDP）                               │
│  - 测试时间                                          │
│  - MTU大小                                           │
│  - 带宽目标（UDP专用）                               │
│  - 端口                                              │
│  - [开始测试] [停止测试] 按钮                        │
├─────────────────────────────────────────────────────┤
│  动画展示区                                          │
│  - Canvas画布：数据流动动画                          │
│  - Canvas画布：仪表盘动画                            │
│  - 实时数据显示：瞬时/平均/峰值/总量                 │
├─────────────────────────────────────────────────────┤
│  结果摘要区                                          │
│  - 测试完成后显示：平均带宽、峰值、总传输量          │
└─────────────────────────────────────────────────────┘
```

### 1.2 技术架构

采用Canvas动画 + WebSocket实时通信方案。

```
┌──────────────────┐      WebSocket      ┌──────────────────┐
│   浏览器前端     │ ←────────────────→ │  Django后端      │
│                  │                     │                  │
│  - Canvas动画    │    实时数据推送      │  - WebSocket     │
│  - 参数配置      │                     │    Handler       │
│  - Agent选择     │    API请求          │  - iperf控制     │
└──────────────────┘ ─────────────────→ └──────────────────┘
                                                │
                                                ↓
                                         ┌──────────────────┐
                                         │   Agent进程      │
                                         │                  │
                                         │  - iperf server  │
                                         │  - iperf client  │
                                         └──────────────────┘
```

### 1.3 WebSocket方案

使用Django Channels + Daphne实现WebSocket支持。

- WebSocket Handler处理iperf实时数据推送
- 每秒推送一次带宽数据
- 支持异常情况通知

---

## 第二章：后端API和WebSocket设计

### 2.1 新增API接口

| 接口 | 方法 | 功能 |
|------|------|------|
| `/api/bandwidth/my-agents/` | GET | 获取当前用户租用的Agent列表 |
| `/api/bandwidth/start/` | POST | 启动带宽测试 |
| `/api/bandwidth/stop/` | POST | 带宽测试停止 |
| `/api/bandwidth/status/` | GET | 查询测试状态 |

**启动测试请求参数：**
```json
{
  "server_agent_id": "agent_eth1",
  "client_agent_id": "agent_eth2",
  "protocol": "tcp",
  "duration": 10,
  "mtu": 1400,
  "bandwidth": null,
  "port": 5201
}
```

**启动测试响应：**
```json
{
  "success": true,
  "test_id": "bw_test_20260527_001",
  "websocket_url": "ws://192.168.81.105:8000/ws/bandwidth/bw_test_20260527_001/"
}
```

### 2.2 WebSocket连接

**WebSocket URL格式：**
```
ws://<host>/ws/bandwidth/<test_id>/
```

**消息格式（服务器推送）：**

**实时数据：**
```json
{
  "type": "iperf_data",
  "timestamp": "2026-05-27 17:30:45",
  "data": {
    "instant_speed": 125.6,
    "avg_speed": 98.3,
    "peak_speed": 150.2,
    "total_bytes": 125000000,
    "interval": 1.0,
    "transfer": 12.5
  }
}
```

**测试完成：**
```json
{
  "type": "test_complete",
  "summary": {
    "avg_bandwidth": 95.2,
    "peak_bandwidth": 150.2,
    "total_transfer": 95.0,
    "duration": 10
  }
}
```

**异常情况：**
```json
{
  "type": "error",
  "message": "iperf client进程异常退出，请检查网络连接"
}
```

### 2.3 Django Channels配置

新增以下配置文件：

- `routing.py`：WebSocket路由配置
- `consumers.py`：WebSocket消费者（处理iperf实时数据推送）
- 修改 `asgi.py`：支持WebSocket

**WebSocket Consumer逻辑：**
1. 客户端连接WebSocket
2. Consumer启动iperf监控线程
3. iperf监控线程实时解析输出
4. 每秒通过WebSocket推送数据
5. iperf结束或异常时推送完成/错误消息

### 2.4 iperf执行流程

**后端执行步骤：**
1. 验证用户租用了两个Agent
2. 获取两个Agent的IP地址
3. 在Server Agent上启动 `iperf3 -s`
4. 在Client Agent上启动 `iperf3 -c <server_ip> -t <duration> -l <mtu> ...`
5. 实时读取Client Agent的iperf输出
6. 解析输出并推送WebSocket消息
7. 测试结束或异常时清理iperf进程

**iperf参数映射：**

| 前端参数 | iperf参数 |
|---------|----------|
| protocol=tcp | 无需额外参数（默认TCP） |
| protocol=udp | `-u` |
| duration | `-t <duration>` |
| mtu | `-l <mtu>` |
| bandwidth | `-b <bandwidth>M`（仅UDP） |
| port | `-p <port>` |

---

## 第三章：前端Canvas动画设计

### 3.1 数据流动动画

**动画场景布局：**

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [Agent A]        ────────→         [防火墙]         ────────→         [Agent B]  │
│  Server端        数据包流动         带宽限制         数据包流动         Client端   │
│  IP: 11.11.11.1                                           IP: 22.22.22.1 │
│                                                              │
│  状态指示灯: ○ 绿色运行中                                    状态指示灯: ○ 绿色运行中 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**Canvas绘制元素：**
1. **Agent图标**：矩形框 + 网卡图标 + IP地址文字
2. **防火墙图标**：带防火墙标志的矩形框
3. **数据包粒子**：小圆形/方形粒子，从Client流向Server（经防火墙）
4. **流动效果**：粒子速度根据当前带宽动态变化
5. **防火墙过滤效果**：粒子经过防火墙时有轻微减速或颜色变化（表示带宽限制）

**粒子动画逻辑：**
- 每个粒子独立位置、速度、颜色、大小
- 速度根据当前带宽动态计算
- 粒子对象池复用，最多50个粒子同时流动

### 3.2 数值仪表盘动画

**仪表盘布局：**

```
┌────────────────────────────────────────────┐
│                                            │
│         [圆形仪表盘 - 瞬时速度]             │
│                                            │
│              当前: 125.6 Mbps              │
│              目标: 100 Mbps                │
│              指针指向对应角度               │
│                                            │
├────────────────────────────────────────────┤
│                                            │
│  [平均速度]    [峰值速度]    [传输总量]    │
│   98.3 Mbps    150.2 Mbps    95.0 MB      │
│                                            │
│  数值动态更新，有呼吸灯效果                 │
│                                            │
└────────────────────────────────────────────┘
```

**仪表盘绘制元素：**
1. **圆形表盘**：刻度线（0-200 Mbps）
2. **指针**：根据瞬时速度动态旋转
3. **数值显示**：当前速度、目标速度（UDP时显示）
4. **底部三指标**：平均速度、峰值速度、传输总量
5. **呼吸灯效果**：数值更新时有发光动画

### 3.3 Canvas更新机制

**更新频率：**
- 每秒接收WebSocket数据时更新Canvas
- 使用requestAnimationFrame保持动画流畅
- 粒子流动动画持续运行（60fps）
- 仪表盘指针平滑过渡（不是跳跃式）

**数据映射：**
- 带宽到粒子速度：基准速度 = 5像素/帧 * (带宽/100)
- 带宽到仪表盘角度：-135度到135度范围

### 3.4 颜色和视觉效果

**颜色方案：**
- TCP协议：蓝色粒子（`#00a896`）
- UDP协议：橙色粒子（`#ff6b35`）
- 防火墙：金色边框（`#f9c74f`）
- 高带宽：亮色，低带宽：暗色（动态调整）
- 数值更新时：发光效果（`#00c9b1`呼吸灯）

**视觉效果：**
- 粒子拖尾效果：半透明渐变尾巴
- 防火墙过滤：粒子经过时颜色加深+轻微减速
- 仪表盘：金属质感、刻度线发光
- 数值跳动：CSS动画实现数字滚动效果

---

## 第四章：错误处理和测试方案设计

### 4.1 错误处理场景

| 错误场景 | 处理方式 |
|---------|---------|
| **用户未租用Agent** | 提示"请先租用Agent后再进行带宽测试" |
| **用户租用Agent少于2个** | 提示"需要至少租用2个Agent才能进行带宽测试" |
| **Agent IP未配置** | 提示"Agent IP未配置，请先在Agent管理页面配置IP" |
| **Agent进程异常退出** | WebSocket推送error消息，前端显示错误提示，自动停止测试 |
| **iperf server启动失败** | 提示"iperf server启动失败，请检查Agent状态" |
| **iperf client连接失败** | WebSocket推送error消息，提示网络连接问题 |
| **WebSocket连接断开** | 前端检测到断开后显示"连接中断"，提供重新连接按钮 |
| **手动停止测试** | 前端发送stop请求，后端清理iperf进程，推送test_complete消息 |
| **测试超时** | 后端设置超时保护，超过duration+5秒自动停止 |

### 4.2 进程管理

**iperf进程跟踪：**
- 每个测试分配唯一test_id
- 后端记录server_pid和client_pid
- 使用进程监控线程检查进程状态
- 进程异常退出时立即清理并发送WebSocket消息
- 测试结束或手动停止时kill所有相关进程

**进程清理逻辑：**
- Kill server进程
- Kill client进程
- 从active_tests中移除
- 关闭WebSocket连接

### 4.3 测试方案

**前端测试要点：**
1. Agent选择功能：未租用Agent时的提示
2. 参数验证：测试时间、MTU范围检查
3. WebSocket连接：正常连接、断线重连
4. Canvas动画：粒子流动、仪表盘更新
5. 实时数据显示：数值更新正确性
6. 手动停止：停止按钮功能
7. 测试结果摘要：完成后数据正确显示

**后端测试要点：**
1. API接口：参数验证、权限检查
2. iperf执行：server/client启动、参数传递
3. WebSocket推送：数据格式、实时性
4. 进程管理：正常结束、异常清理
5. 错误处理：各种异常情况

**集成测试场景：**
1. 正常TCP测试流程：开始→实时显示→结束→摘要
2. 正常UDP测试流程：带带宽目标参数
3. 手动停止测试：中途停止，清理进程
4. 网络异常模拟：断开网络，检测异常退出
5. WebSocket断开：前端检测并提示
6. 并发测试保护：同一用户不能同时开多个测试

### 4.4 性能考虑

**Canvas性能优化：**
- 粒子数量限制：最多50个粒子同时流动
- 使用双缓冲Canvas减少闪烁
- 只在数据更新时重绘仪表盘（不是每帧）
- 粒子使用对象池复用（避免频繁创建）

**WebSocket性能：**
- 每秒推送一次数据（不更频繁）
- 消息大小控制在500字节以内

**后端性能：**
- iperf解析使用增量读取（不是一次性读取全部）
- 使用线程池管理测试任务
- 定期清理超时的测试任务

---

## 第五章：文件结构和代码组织设计

### 5.1 新增文件列表

| 文件路径 | 类型 | 功能 |
|---------|------|------|
| `templates/bandwidth_test.html` | HTML模板 | 带宽测试页面 |
| `main/views.py` | 修改 | 新增带宽测试相关视图函数 |
| `main/urls.py` | 修改 | 新增带宽测试URL路由 |
| `main/bandwidth_utils.py` | 新增 | 带宽测试后端逻辑（iperf控制、进程管理） |
| `djangoProject/routing.py` | 新增 | WebSocket路由配置 |
| `djangoProject/consumers.py` | 新增 | WebSocket消费者（带宽测试数据推送） |
| `djangoProject/asgi.py` | 修改 | 配置ASGI支持WebSocket |
| `requirements.txt` | 修改 | 新增channels、daphne依赖 |
| `templates/base.html` | 修改 | 导航栏新增"带宽测试"入口 |

### 5.2 核心代码模块设计

**main/bandwidth_utils.py：**
- `BandwidthTestManager`：带宽测试管理器
  - `active_tests`：活跃测试任务字典
  - `start_test(test_params)`：启动带宽测试
  - `stop_test(test_id)`：停止带宽测试
  - `parse_iperf_output(line)`：解析iperf单行输出
  - `monitor_test(test_id, websocket_consumer)`：监控测试进程，推送数据
- `check_agent_rented(user_identifier, agent_id)`：检查Agent是否被用户租用
- `get_agent_ip(agent_id)`：获取Agent的IP地址

**djangoProject/consumers.py：**
- `BandwidthTestConsumer`：带宽测试WebSocket消费者
  - `connect()`：WebSocket连接
  - `disconnect()`：WebSocket断开
  - `receive()`：接收客户端消息
  - `iperf_data_message()`：推送iperf数据
  - `test_complete_message()`：推送测试完成
  - `error_message()`：推送错误消息

**templates/bandwidth_test.html：**
- HTML模板结构：Agent选择区、参数配置区、Canvas动画区、结果摘要区
- JavaScript：WebSocket连接管理、Canvas动画类、iperf数据处理

### 5.3 URL路由新增

```python
# main/urls.py 新增
path('bandwidth-test/', views.bandwidth_test, name='bandwidth_test'),
path('api/bandwidth/my-agents/', views.api_bandwidth_my_agents),
path('api/bandwidth/start/', views.api_bandwidth_start),
path('api/bandwidth/stop/', views.api_bandwidth_stop),
path('api/bandwidth/status/', views.api_bandwidth_status),

# djangoProject/routing.py 新增
websocket_urlpatterns = [
    re_path(r'^ws/bandwidth/(?P<test_id>\w+)/$', 
            BandwidthTestConsumer.as_asgi()),
]
```

### 5.4 导航栏修改

在 `templates/base.html` 的侧边栏导航中新增：

```html
<div class="nav-item">
    <a href="{% url 'main:bandwidth_test' %}" class="nav-link">
        <i class="fas fa-tachometer-alt"></i>
        <span>带宽测试</span>
    </a>
</div>
```

### 5.5 依赖新增

```txt
# requirements.txt 新增
channels>=4.0.0
daphne>=4.0.0
```

---

## 实现计划

待用户审核通过后，将使用writing-plans技能生成详细的实现计划。

---

## 附录

### A. iperf输出解析示例

**TCP模式输出格式：**
```
[  5]   1.00-2.00   sec  12.5 MBytes  125.6 Mbits/sec  ...
```

**解析字段：**
- interval: 1.00-2.00
- transfer: 12.5 MBytes
- bandwidth: 125.6 Mbits/sec

**UDP模式输出格式：**
```
[  5]   1.00-2.00   sec  12.5 MBytes  100.0 Mbits/sec  0.123 ms  0/100 (0%)
```

**额外字段：**
- jitter: 0.123 ms
- lost_packets: 0/100
- loss_percent: 0%

### B. WebSocket消息类型定义

| type | 说明 |
|------|------|
| `iperf_data` | 实时带宽数据 |
| `test_complete` | 测试完成摘要 |
| `error` | 异常错误 |
| `progress` | 进度信息（可选） |

---

**文档结束**