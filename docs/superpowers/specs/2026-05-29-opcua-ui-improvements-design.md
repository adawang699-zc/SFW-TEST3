# OPC UA 工控协议界面优化设计

## 概述

优化 `industrial_protocol.html` 中 OPC UA 协议界面的 5 个问题点。

---

## 1. 服务端变量名称显示

### 问题
当前显示格式为 `Temperature（温度 (正弦波)）`，包含多余的英文和模式描述。

### 解决方案
- 格式改为：`Temperature（温度）` - 英文名 + 简单中文名
- 在 `DEFAULT_VARIABLES` 中新增 `chinese_name` 字段
- 前端渲染时使用 `${v.name}（${v.chinese_name}）`

### 修改文件
- `agents/protocols/opcua_common.py` - 添加 `chinese_name` 字段
- `templates/industrial_protocol.html` - 修改 `opcuaRefreshVariables()` 函数

---

## 2. 客户端目标 IP 自动填充

### 问题
选择客户端 agent 后，目标 IP 需要手动输入。

### 解决方案
- 当选择客户端 agent 时，自动查找另一个运行中的 agent
- 将该 agent 的 IP 地址填充到目标 IP 输入框

### 实现逻辑
```javascript
// 在 renderAgentSelector() 中添加事件处理
// 当选中 client agent 时：
// 1. 从 runningAgents 中找到不同的 agent
// 2. 获取其 IP 地址（管理网或业务网）
// 3. 填充到 opcua-client-ip 输入框
```

### 修改文件
- `templates/industrial_protocol.html` - 修改 `renderAgentSelector()` 函数

---

## 3. 服务端数据显示控制

### 问题
当前每秒自动刷新服务端变量值，用户希望按需读取。

### 解决方案
- **取消自动刷新** - 删除 `opcuaValuesInterval` 定时器
- **添加"动态更新"切换按钮** - 控制是否开启自动刷新
- **按需读取** - 只在以下时机读取数据：
  - 点击"读取"按钮时
  - 写入成功后自动更新显示

### UI 变化
- 服务端变量区添加按钮：`动态更新`（开启/关闭状态）
- 默认关闭动态更新，数据静止显示

### 修改文件
- `templates/industrial_protocol.html`
  - 删除 `opcuaStartValuesRefresh()` 的自动调用
  - 添加动态更新按钮和控制逻辑
  - 在写入成功后调用 `opcuaRefreshValues()`

---

## 4. Float 小数位数统一

### 问题
Float 数据显示小数点后 12 位，不符合实际需求。

### 解决方案
- 客户端和服务端统一显示 **2 位小数**
- 使用 `.toFixed(2)` 格式化

### 修改文件
- `templates/industrial_protocol.html`
  - `opcuaRefreshValues()` - 服务端变量显示
  - `opcuaRead()` - 客户端读取结果显示

---

## 5. 方法调用改为下拉选择

### 问题
手动输入节点 ID 调用方法容易出错，导致 `BadNodeIdInvalid`。

### 解决方案
- 提供方法下拉选择：`ResetCounter`、`SetMode`
- `SetMode` 需要额外输入参数值
- 使用正确的节点 ID 格式：`ns=2;s=SimulationDevice` + 方法名

### UI 变化
- 原来的"调用方法"按钮区域改为：
  - 方法下拉框：`ResetCounter` / `SetMode`
  - 参数输入框（仅 `SetMode` 需要时显示）
  - 执行按钮

### 修改文件
- `templates/industrial_protocol.html`
  - 修改数据读写区域 HTML
  - 新增 `opcuaExecuteMethod()` 函数处理下拉选择

---

## 修改文件清单

| 文件 | 修改内容 |
|------|----------|
| `agents/protocols/opcua_common.py` | 添加 `chinese_name` 字段到 `DEFAULT_VARIABLES` |
| `templates/industrial_protocol.html` | 5 处修改（变量显示、IP填充、动态更新、小数格式、方法调用） |

---

## 实现顺序

1. 添加 `chinese_name` 字段（后端数据）
2. 修改服务端变量显示格式
3. 添加动态更新控制
4. 统一小数位数
5. 实现客户端 IP 自动填充
6. 改造方法调用为下拉选择