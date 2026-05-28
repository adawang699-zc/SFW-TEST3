---
name: port-management-design
description: 网口管理功能设计文档 - 测试防火墙网口自协商、速率、双工模式
---

# 网口管理功能设计文档

**日期:** 2026-05-28
**状态:** 已确认

## 功能目标

测试防火墙网口的自协商、速率、双工模式功能是否正常工作。通过控制Ubuntu Agent网口配置,观察防火墙网口响应,验证协商行为符合预期。

## UI布局设计

### 单页面分区式布局

**左侧区域(选择区):**
- 防火墙设备下拉选择框
- 设备网口信息列表(名称、LINK状态、当前速率、双工模式)
- Agent选择列表(勾选两个Agent)
- 网口映射显示(Agent网口 → 防火墙网口)
- "检测拓扑"按钮

**右侧区域(配置+结果区):**
- 测试参数配置区
- 测试结果表格
- 操作按钮(开始测试、停止测试、导出结果)

## 核心流程

### 步骤1: 选择防火墙设备

用户从下拉列表选择防火墙设备,系统显示该设备所有网口信息:
- 网口名称(eth0, eth1, ...)
- LINK状态(Up/Down)
- 当前速率(10M/100M/1000M)
- 双工模式(Half/Full)
- 自协商状态(On/Off)

数据通过SSH执行 `ethtool <interface>` 命令获取。

### 步骤2: 选择Agent

用户勾选两个Agent,系统显示每个Agent的网口信息:
- Agent名称
- 网口名称(ens33, ens34, ...)
- 网口状态

### 步骤3: 拓扑检测

点击"检测拓扑"按钮,系统执行轮询检测:

```
检测逻辑:
1. 记录防火墙所有网口初始LINK状态
2. 逐个DOWN Agent-1的网口:
   - 执行: ethtool -s <agent_interface> autoneg off speed 10 duplex half
   - 等待2秒
   - 检查防火墙哪些网口LINK变为Down
   - 记录映射关系
   - 恢复Agent网口(UP)
3. 逐个DOWN Agent-2的网口(同上)
4. 生成最终映射表: Agent网口 → 防火墙网口
```

只DOWN选择的Agent网口,不影响其他网口。

### 步骤4: 配置测试参数

用户选择测试参数范围:

| 参数 | 选项 |
|------|------|
| 自协商 | 开启 / 关闭 |
| 速率 | 10M / 100M / 1000M / 自动 |
| 双工模式 | 全双工 / 半双工 / 自动 |

提供快速预设场景按钮:
- "完整测试": 自协商开+关,所有速率和双工组合
- "快速验证": 自协商开+1000M+全双工,自协商关+100M+全双工

### 步骤5: 智能组合生成

系统根据参数范围自动生成所有测试场景组合:

```
示例: 选择 自协商=[开,关],速率=[100M,1000M],双工=[全]
生成组合:
1. 自协商开 + 100M + 全双工
2. 自协商开 + 1000M + 全双工
3. 自协商关 + 100M + 全双工
4. 自协商关 + 1000M + 全双工
```

支持手动添加单个自定义场景(作为补充)。

### 步骤6: 执行测试

点击"开始测试",批量执行所有场景:

```
执行流程:
1. 显示测试进度(当前场景/总场景数)
2. 对每个场景:
   a. 配置Agent网口参数(ethtool命令)
   b. 等待3秒让协商生效
   c. SSH到防火墙执行ethtool获取状态
   d. 对比配置与实际状态
   e. 判断PASS/FAIL
   f. 记录结果
   g. 推送WebSocket更新表格
3. 完成后显示汇总统计
```

判断规则:
- 自协商开: 防火墙应自动协商到最高速率+全双工
- 自协商关: 防火墙应匹配强制配置的速率和双工

### 步骤7: 结果展示

**表格对比式展示:**

| 场景 | 自协商 | 速率 | 双工 | 防火墙状态 | 结果 |
|------|--------|------|------|------------|------|
| 1 | 开 | 1000M | 全 | 1000M/Full | PASS |
| 2 | 关 | 100M | 半 | 1000M/Full | FAIL |
| 3 | 关 | 100M | 全 | 100M/Full | PASS |

表格列:
- 场景编号
- 配置参数(自协商、速率、双工)
- 防火墙实际状态(速率、双工)
- 结果(PASS绿色/FAIL红色)
- 详情按钮(展开查看完整ethtool输出)

提供导出功能(CSV/Excel格式)。

## 数据模型

### TestDevice(防火墙设备)

```python
class TestDevice(models.Model):
    name = models.CharField(max_length=100)  # 设备名称
    ip = models.CharField(max_length=50)  # SSH IP地址
    port = models.IntegerField(default=22)  # SSH端口
    ssh_user = models.CharField(max_length=50)  # SSH用户名
    ssh_password = models.CharField(max_length=100)  # SSH密码
    created_at = models.DateTimeField(auto_now_add=True)
```

### PortMapping(网口映射)

```python
class PortMapping(models.Model):
    device = models.ForeignKey(TestDevice, on_delete=models.CASCADE)
    agent_id = models.CharField(max_length=50)  # Agent ID
    agent_interface = models.CharField(max_length=50)  # Agent网口名
    firewall_interface = models.CharField(max_length=50)  # 防火墙网口名
    detected_at = models.DateTimeField(auto_now_add=True)
```

### TestResult(测试结果)

```python
class TestResult(models.Model):
    device = models.ForeignKey(TestDevice, on_delete=models.CASCADE)
    mapping = models.ForeignKey(PortMapping, on_delete=models.CASCADE)
    scenario_id = models.IntegerField()  # 场景编号
    autoneg = models.CharField(max_length=10)  # 配置: on/off
    speed = models.CharField(max_length=20)  # 配置速率
    duplex = models.CharField(max_length=20)  # 配置双工
    firewall_speed = models.CharField(max_length=20)  # 防火墙实际速率
    firewall_duplex = models.CharField(max_length=20)  # 防火墙实际双工
    result = models.CharField(max_length=10)  # PASS/FAIL
    ethtool_output = models.TextField()  # 完整ethtool输出
    tested_at = models.DateTimeField(auto_now_add=True)
```

## 技术方案

### 后端API

| API | 方法 | 功能 |
|-----|------|------|
| `/api/devices/` | GET | 获取防火墙设备列表 |
| `/api/devices/<id>/ports/` | GET | 获取设备网口信息 |
| `/api/port-test/detect-topology/` | POST | 拓扑检测 |
| `/api/port-test/start/` | POST | 开始测试 |
| `/api/port-test/stop/` | POST | 停止测试 |
| `/api/port-test/results/<id>/` | GET | 获取测试结果 |

### WebSocket

- 路径: `ws://host/ws/port-test/<test_id>/`
- 推送内容: 场景进度、测试结果、状态更新

### SSH命令执行

使用现有 `main/device_utils.py` 中的 `execute_ssh_command` 函数:
- 通过paramiko连接防火墙设备
- 执行ethtool命令获取/配置网口状态
- 解析命令输出提取关键信息

### ethtool命令

```bash
# 查询网口状态
ethtool eth0

# 配置网口参数
ethtool -s eth0 autoneg off speed 100 duplex half

# 开启自协商
ethtool -s eth0 autoneg on
```

## 错误处理

1. SSH连接失败: 显示错误,提示检查设备配置
2. ethtool命令失败: 记录错误,继续执行其他场景
3. Agent网口配置失败: 跳过该场景,标记为ERROR
4. 拓扑检测失败: 提示手动选择网口映射

## 测试验证

1. 选择已知正常的防火墙设备测试
2. 验证拓扑检测准确性(手动确认网口连接)
3. 验证各种参数组合的PASS/FAIL判断正确
4. 测试WebSocket实时更新功能
5. 测试导出功能

## 文件结构

```
templates/
  port_test.html          # 网口管理页面模板

main/
  models.py               # 新增TestDevice, PortMapping, TestResult模型
  device_utils.py         # SSH命令执行(已有execute_ssh_command)
  port_test_utils.py      # 网口测试逻辑管理
  views.py                # 新增API视图
  consumers.py            # WebSocket消费者(新增port_test类型)

static/
  js/port_test.js         # 前端交互脚本
```

## 实现优先级

1. **P0**: 基础框架(页面模板、模型定义、API骨架)
2. **P0**: 拓扑检测功能(核心流程)
3. **P0**: 单场景测试执行
4. **P1**: 智能组合批量执行
5. **P1**: WebSocket实时更新
6. **P2**: 预设场景按钮
7. **P2**: 结果导出功能
8. **P3**: 详情展开查看ethtool完整输出