# OPC UA 工控协议界面优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化 OPC UA 界面的变量显示、IP自动填充、数据刷新控制、小数格式和方法调用

**Architecture:** 修改后端变量定义添加中文名，前端 JavaScript 修改显示逻辑、添加控制按钮、实现自动填充和下拉选择

**Tech Stack:** Python (Django), JavaScript, HTML, asyncua (OPC UA库)

---

## 文件结构

| 文件 | 修改内容 | 任务 |
|------|----------|------|
| `agents/protocols/opcua_common.py` | 添加 `chinese_name` 字段 | Task 1 |
| `templates/industrial_protocol.html` | 变量显示格式 + 动态更新按钮 + 小数格式 + IP填充 + 方法下拉 | Task 2-6 |

---

### Task 1: 添加变量中文名称字段

**Files:**
- Modify: `agents/protocols/opcua_common.py:35-99`

- [ ] **Step 1: 修改 DEFAULT_VARIABLES 添加 chinese_name 字段**

```python
# agents/protocols/opcua_common.py
DEFAULT_VARIABLES = [
    {
        'name': 'Temperature',
        'type': 'Float',
        'mode': 'sine',
        'params': {'base': 25.0, 'amplitude': 5.0, 'period': 60.0},
        'chinese_name': '温度',
        'description': '温度 (正弦波)'
    },
    {
        'name': 'Pressure',
        'type': 'Float',
        'mode': 'random',
        'params': {'base': 100.0, 'range': 10.0},
        'chinese_name': '压力',
        'description': '压力 (随机波动)'
    },
    {
        'name': 'Flow',
        'type': 'Float',
        'mode': 'step',
        'params': {'steps': [40.0, 50.0, 60.0, 70.0], 'step_interval': 30.0},
        'chinese_name': '流量',
        'description': '流量 (阶梯波)'
    },
    {
        'name': 'Speed',
        'type': 'Float',
        'mode': 'sine',
        'params': {'base': 1500.0, 'amplitude': 200.0, 'period': 30.0},
        'chinese_name': '转速',
        'description': '转速 (正弦波)'
    },
    {
        'name': 'Level',
        'type': 'Float',
        'mode': 'random',
        'params': {'base': 50.0, 'range': 15.0},
        'chinese_name': '液位',
        'description': '液位 (随机波动)'
    },
    {
        'name': 'SwitchState',
        'type': 'Boolean',
        'mode': 'toggle',
        'params': {'interval': 120.0},
        'chinese_name': '开关状态',
        'description': '开关状态'
    },
    {
        'name': 'AlarmActive',
        'type': 'Boolean',
        'mode': 'alarm',
        'params': {'trigger_threshold': 30.0, 'clear_threshold': 28.0},
        'chinese_name': '报警状态',
        'description': '报警状态'
    },
    {
        'name': 'Counter',
        'type': 'Int32',
        'mode': 'counter',
        'params': {'max': 1000},
        'chinese_name': '计数器',
        'description': '计数器'
    },
    {
        'name': 'Mode',
        'type': 'Int32',
        'mode': 'constant',
        'params': {'value': 1},
        'chinese_name': '运行模式',
        'description': '运行模式'
    }
]
```

- [ ] **Step 2: 修改 get_variables 方法返回 chinese_name**

在 `agents/protocols/opcua_server.py` 的 `get_variables()` 方法中添加 `chinese_name`:

```python
# agents/protocols/opcua_server.py:384-394
def get_variables(self) -> List[Dict]:
    """获取变量列表"""
    result = []
    for name, config in self._datastore.items():
        result.append({
            "name": name,
            "type": config.get('type'),
            "mode": config.get('mode'),
            "chinese_name": config.get('params', {}).get('chinese_name', name),
            "description": config.get('params', {}).get('description', name)
        })
    return result
```

注意：需要从 `DEFAULT_VARIABLES` 传递 `chinese_name` 到 `params`，在 `_create_address_space` 中：

```python
# agents/protocols/opcua_server.py:121-149 修改
for var_config in DEFAULT_VARIABLES:
    name = var_config['name']
    var_type = var_config['type']
    mode = var_config['mode']
    params = var_config['params']
    # 添加 chinese_name 到 params
    params['chinese_name'] = var_config.get('chinese_name', name)
    params['description'] = var_config.get('description', name)
    ...
```

- [ ] **Step 3: Commit**

```bash
git add agents/protocols/opcua_common.py agents/protocols/opcua_server.py
git commit -m "feat: 添加 OPC UA 变量中文名称字段

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 修改服务端变量显示格式

**Files:**
- Modify: `templates/industrial_protocol.html:4374-4396`

- [ ] **Step 1: 修改 opcuaRefreshVariables 函数中的显示格式**

找到 `opcuaRefreshVariables()` 函数，修改变量名显示部分：

```javascript
// templates/industrial_protocol.html 约 4374-4396 行
async function opcuaRefreshVariables() {
    if (!selectedServerAgentId) return;
    const result = await apiRequest('/api/industrial/opcua_server/variables/', 'GET', { agent_id: selectedServerAgentId });
    if (result.success && result.variables) {
        const tbody = document.getElementById('opcua-variables-tbody');
        tbody.innerHTML = '';
        // 保存变量类型信息用于写入验证
        window.opcuaVariableTypes = {};
        result.variables.forEach(v => {
            window.opcuaVariableTypes[v.name] = v.type;
            const modeNames = {
                'sine': '正弦波', 'random': '随机', 'step': '阶梯波',
                'toggle': '切换', 'alarm': '报警', 'counter': '计数器', 'constant': '常量'
            };
            // 变量名格式：英文（中文）
            const displayName = v.chinese_name ? `${v.name}（${v.chinese_name}）` : v.name;
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${displayName}</td><td>${v.type}</td><td>${modeNames[v.mode] || v.mode}</td><td id="opcua-var-${v.name}">-</td>`;
            tbody.appendChild(tr);
        });
        // 立即获取当前值
        opcuaRefreshValues();
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add templates/industrial_protocol.html
git commit -m "feat: OPC UA 变量名显示改为 英文(中文) 格式

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 添加动态更新控制按钮

**Files:**
- Modify: `templates/industrial_protocol.html:1416-1430` (变量列表区域)
- Modify: `templates/industrial_protocol.html:4436-4447` (定时器函数)

- [ ] **Step 1: 在服务端变量区域添加动态更新按钮**

找到 `opcua-variables-section` 的 HTML 部分，添加动态更新按钮：

```html
<!-- templates/industrial_protocol.html 约 1416-1430 行 -->
<div class="config-section" id="opcua-variables-section" style="display: none;">
    <h5 style="margin-bottom: 12px;">模拟变量列表</h5>
    <div style="display: flex; gap: 8px; margin-bottom: 12px; align-items: center;">
        <button class="btn btn-primary" onclick="opcuaRefreshVariables()">刷新变量</button>
        <button class="btn btn-secondary" id="opcua-dynamic-update-btn" onclick="opcuaToggleDynamicUpdate()">动态更新: 关闭</button>
        <button class="btn btn-secondary" onclick="opcuaShowHistory('Temperature')">查看温度历史</button>
    </div>
    <div style="overflow-x: auto;">
        <table class="data-table" id="opcua-variables-table">
            <thead>
                <tr><th>变量名</th><th>类型</th><th>模式</th><th>当前值</th></tr>
            </thead>
            <tbody id="opcua-variables-tbody"></tbody>
        </table>
    </div>
</div>
```

- [ ] **Step 2: 添加动态更新控制变量和函数**

在 JavaScript 部分（约 4436 行附近）添加：

```javascript
// templates/industrial_protocol.html JavaScript 区域
// OPC UA 动态更新状态
let opcuaDynamicUpdateEnabled = false;

// 切换动态更新
function opcuaToggleDynamicUpdate() {
    opcuaDynamicUpdateEnabled = !opcuaDynamicUpdateEnabled;
    const btn = document.getElementById('opcua-dynamic-update-btn');
    if (opcuaDynamicUpdateEnabled) {
        btn.textContent = '动态更新: 开启';
        btn.classList.remove('btn-secondary');
        btn.classList.add('btn-success');
        opcuaStartValuesRefresh();
    } else {
        btn.textContent = '动态更新: 关闭';
        btn.classList.remove('btn-success');
        btn.classList.add('btn-secondary');
        opcuaStopValuesRefresh();
    }
}

// OPC UA 变量值自动刷新定时器
let opcuaValuesInterval = null;
function opcuaStartValuesRefresh() {
    if (opcuaValuesInterval) clearInterval(opcuaValuesInterval);
    opcuaValuesInterval = setInterval(opcuaRefreshValues, 1000);
}
function opcuaStopValuesRefresh() {
    if (opcuaValuesInterval) {
        clearInterval(opcuaValuesInterval);
        opcuaValuesInterval = null;
    }
}
```

- [ ] **Step 3: 修改 opcuaServerToggle 启动时不自动刷新**

修改 `opcuaServerToggle()` 函数，启动成功后不调用 `opcuaStartValuesRefresh()`：

```javascript
// templates/industrial_protocol.html 约 4359-4366 行
if (result.success) {
    opcuaServerRunning = true;
    activeConnections.opcua_server = true;
    updateOpcuaServerButtonState(true);
    document.getElementById('opcua-variables-section').style.display = 'block';
    showToast('OPC UA Server 启动成功', 'success');
    opcuaRefreshVariables();
    // 不自动开启动态更新，用户手动控制
}
```

- [ ] **Step 4: Commit**

```bash
git add templates/industrial_protocol.html
git commit -m "feat: OPC UA 服务端添加动态更新开关按钮

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 统一 Float 小数位数为 2 位

**Files:**
- Modify: `templates/industrial_protocol.html:4419-4422` (服务端显示)
- Modify: `templates/industrial_protocol.html:4557-4561` (客户端显示)

- [ ] **Step 1: 修改服务端变量值显示格式**

找到 `opcuaRefreshValues()` 函数中的小数处理部分：

```javascript
// templates/industrial_protocol.html 约 4419-4426 行
if (td) {
    const varType = varTypes[name] || '';
    let displayValue = data.value;
    if (varType === 'Int32' || varType === 'Int16' || varType === 'UInt32' || varType === 'UInt16') {
        displayValue = Math.round(data.value);
    } else if (varType === 'Float' || varType === 'Double') {
        // 统一显示 2 位小数
        displayValue = typeof data.value === 'number' ? data.value.toFixed(2) : data.value;
    } else if (varType === 'Boolean') {
        displayValue = data.value ? 'true' : 'false';
    }
    td.textContent = displayValue;
}
```

- [ ] **Step 2: 修改客户端读取结果显示格式**

找到 `opcuaRead()` 函数：

```javascript
// templates/industrial_protocol.html 约 4551-4561 行
async function opcuaRead() {
    if (!selectedClientAgentId) { showToast('请先选择 Client Agent', 'error'); return; }
    const nodeId = document.getElementById('opcua-rw-node').value;
    if (!nodeId) { showToast('请输入节点 ID', 'error'); return; }
    const result = await apiRequest('/api/industrial/opcua_client/read/', 'POST', { agent_id: selectedClientAgentId, node_id: nodeId });
    const div = document.getElementById('opcua-rw-result');
    if (result.success) {
        // 格式化数值显示
        let displayValue = result.value;
        if (typeof result.value === 'number') {
            displayValue = result.value.toFixed(2);
        }
        div.innerHTML = `<span style="color: var(--success);">读取成功:</span> <strong>${displayValue}</strong>`;
    } else {
        div.innerHTML = `<span style="color: red;">读取失败: ${result.message || ''}</span>`;
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/industrial_protocol.html
git commit -m "feat: OPC UA Float 数值统一显示 2 位小数

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 客户端目标 IP 自动填充

**Files:**
- Modify: `templates/industrial_protocol.html` (renderAgentSelector 函数)

- [ ] **Step 1: 找到 renderAgentSelector 函数并修改**

在文件中找到 `renderAgentSelector()` 函数，添加自动填充逻辑：

```javascript
// templates/industrial_protocol.html 中的 renderAgentSelector 函数
function renderAgentSelector(containerId, agents, type) {
    const container = document.getElementById(containerId);
    if (!agents || agents.length === 0) {
        container.innerHTML = '<span style="color: var(--text-muted);">没有可用的 Agent</span>';
        return;
    }

    let html = '';
    agents.forEach(agent => {
        const isSelected = (type === 'client' && selectedClientAgentId === agent.id) ||
                          (type === 'server' && selectedServerAgentId === agent.id);
        const selectedClass = isSelected ? 'agent-btn-selected' : '';
        html += `<button class="btn btn-sm ${selectedClass}" onclick="selectAgent('${agent.id}', '${type}', '${agent.name}', '${agent.ip || ''}')">${agent.name}</button>`;
    });
    container.innerHTML = html;
}
```

- [ ] **Step 2: 修改 selectAgent 函数添加自动填充逻辑**

找到 `selectAgent()` 函数，添加 IP 自动填充：

```javascript
// templates/industrial_protocol.html 中的 selectAgent 函数
function selectAgent(agentId, type, agentName, agentIp) {
    if (type === 'client') {
        selectedClientAgentId = agentId;
        selectedClientAgentName = agentName;
        // 自动填充：找到另一个 agent 的 IP
        if (runningAgents && runningAgents.length >= 2) {
            const otherAgent = runningAgents.find(a => a.id !== agentId);
            if (otherAgent && otherAgent.ip) {
                // 优先使用业务网 IP，其次管理网 IP
                const targetIp = otherAgent.business_ip || otherAgent.management_ip || otherAgent.ip;
                document.getElementById('opcua-client-ip').value = targetIp;
            }
        }
        // 更新按钮状态
        updateAgentButtons('client');
        syncClientStatus();
    } else if (type === 'server') {
        selectedServerAgentId = agentId;
        selectedServerAgentName = agentName;
        updateAgentButtons('server');
        syncServerStatus();
    }
}
```

- [ ] **Step 3: Commit**

```bash
git add templates/industrial_protocol.html
git commit -m "feat: OPC UA 客户端选择 Agent 后自动填充目标 IP

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 方法调用改为下拉选择

**Files:**
- Modify: `templates/industrial_protocol.html:1301-1333` (数据读写区域 HTML)
- Modify: `templates/industrial_protocol.html:4639-4669` (方法调用函数)

- [ ] **Step 1: 修改数据读写区域的 HTML**

找到 `opcua-readwrite-section` 部分，将"调用方法"按钮改为下拉选择：

```html
<!-- templates/industrial_protocol.html 约 1301-1333 行 -->
<div class="config-section" id="opcua-readwrite-section" style="display: none;">
    <h5 style="margin-bottom: 12px;">数据读写</h5>
    <div style="margin-bottom: 12px; padding: 8px; background: var(--bg-secondary); border-radius: 4px; font-size: 0.85rem;">
        <strong style="color: var(--primary);">常用节点 ID:</strong>
        <div style="margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px;">
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Temperature'">温度</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Pressure'">压力</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Flow'">流量</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Speed'">转速</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Level'">液位</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.SwitchState'">开关</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Counter'">计数器</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.Mode'">模式</button>
            <button class="btn btn-sm" style="padding: 4px 8px; font-size: 0.8rem;" onclick="document.getElementById('opcua-rw-node').value='ns=2;s=SimulationDevice.AlarmActive'">报警</button>
        </div>
    </div>
    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
        <div class="form-group">
            <label class="form-label">节点 ID</label>
            <input type="text" id="opcua-rw-node" class="form-input" placeholder="ns=2;s=SimulationDevice.Temperature">
        </div>
        <div class="form-group">
            <label class="form-label">写入值</label>
            <input type="text" id="opcua-rw-value" class="form-input" placeholder="值 (数字或布尔)">
        </div>
    </div>
    <div style="display: flex; gap: 8px; margin-top: 12px;">
        <button class="btn btn-primary" onclick="opcuaRead()">读取</button>
        <button class="btn btn-warning" onclick="opcuaWrite()">写入</button>
    </div>
    <div id="opcua-rw-result" style="margin-top: 12px; padding: 8px; background: var(--bg-dark); border-radius: 4px;"></div>

    <!-- 方法调用区域 -->
    <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border);">
        <h5 style="margin-bottom: 12px;">方法调用</h5>
        <div style="display: grid; grid-template-columns: 1fr 1fr auto; gap: 12px; align-items: end;">
            <div class="form-group">
                <label class="form-label">选择方法</label>
                <select id="opcua-method-select" class="form-input" onchange="onMethodSelectChange()">
                    <option value="ResetCounter">ResetCounter - 重置计数器</option>
                    <option value="SetMode">SetMode - 设置运行模式</option>
                </select>
            </div>
            <div class="form-group" id="opcua-method-param-group" style="display: none;">
                <label class="form-label">参数值</label>
                <input type="number" id="opcua-method-param" class="form-input" value="1" min="0" max="10">
            </div>
            <div class="form-group" style="display: flex; align-items: end;">
                <button class="btn btn-info" onclick="opcuaExecuteMethod()">执行方法</button>
            </div>
        </div>
        <div id="opcua-method-result" style="margin-top: 12px; padding: 8px; background: var(--bg-dark); border-radius: 4px;"></div>
    </div>
</div>
```

- [ ] **Step 2: 添加方法选择变化处理函数**

```javascript
// templates/industrial_protocol.html JavaScript 区域
// 方法选择变化处理
function onMethodSelectChange() {
    const methodSelect = document.getElementById('opcua-method-select');
    const paramGroup = document.getElementById('opcua-method-param-group');
    if (methodSelect.value === 'SetMode') {
        paramGroup.style.display = 'block';
    } else {
        paramGroup.style.display = 'none';
    }
}
```

- [ ] **Step 3: 添加新的方法执行函数**

```javascript
// templates/industrial_protocol.html JavaScript 区域
// OPC UA 方法执行（使用下拉选择）
async function opcuaExecuteMethod() {
    if (!selectedClientAgentId) { showToast('请先选择 Client Agent', 'error'); return; }

    const methodSelect = document.getElementById('opcua-method-select');
    const methodName = methodSelect.value;

    // 准备参数
    let args = [];
    if (methodName === 'SetMode') {
        const paramValue = parseInt(document.getElementById('opcua-method-param').value);
        args = [paramValue];
    }

    // 服务端对象节点
    const objectNode = 'ns=2;s=SimulationDevice';

    const result = await apiRequest('/api/industrial/opcua_client/method/', 'POST', {
        agent_id: selectedClientAgentId,
        object_node: objectNode,
        method_name: methodName,
        args: args
    });

    const div = document.getElementById('opcua-method-result');
    if (result.success) {
        div.innerHTML = `<span style="color: var(--success);">方法调用成功:</span> <strong>${JSON.stringify(result.result)}</strong>`;
        // 刷新变量值
        opcuaRefreshVariables();
    } else {
        div.innerHTML = `<span style="color: red;">方法调用失败: ${result.message || ''}</span>`;
    }
}
```

- [ ] **Step 4: 删除旧的 opcuaCallMethod 函数**

删除原来的 `opcuaCallMethod()` 函数（约 4639-4669 行），因为已被新的 `opcuaExecuteMethod()` 替代。

- [ ] **Step 5: Commit**

```bash
git add templates/industrial_protocol.html
git commit -m "feat: OPC UA 方法调用改为下拉选择，修复节点格式

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review 检查

**1. Spec coverage:**
- ✅ 变量名格式：Task 1 + Task 2
- ✅ IP 自动填充：Task 5
- ✅ 动态更新控制：Task 3
- ✅ Float 小数位：Task 4
- ✅ 方法下拉：Task 6

**2. Placeholder scan:**
- ✅ 所有代码完整
- ✅ 无 TBD/TODO

**3. Type consistency:**
- ✅ `chinese_name` 在 Task 1 定义，Task 2 使用
- ✅ `opcuaDynamicUpdateEnabled` 在 Task 3 定义和使用
- ✅ 函数名一致

---

## 最终提交

完成所有任务后，同步到 Ubuntu：

```bash
python sync_to_ubuntu.py
```