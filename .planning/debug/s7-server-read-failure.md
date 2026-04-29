---
status: investigating
trigger: "调查 S7 Server 启动后数据块读取失败的 bug"
created: 2026-04-29T10:00:00
updated: 2026-04-29T10:05:00
---

## Current Focus
hypothesis: full_agent.py 中简化版本的 s7_server_start 存在关键缺陷 - 未调用 register_s7_areas 导致 ctypes_buffers 未初始化，且可能存在进程隔离问题
test: 检查代码逻辑，对比两个版本的实现差异
expecting: 确认 root cause
next_action: 分析代码差异，确定根本原因

## Symptoms
expected: S7 Server 启动后，读取 DB 块数据应成功返回数据
actual: 启动成功后读取失败，返回 {"success": false, "error": "服务端不存在"} 或超时
errors: "服务端不存在" 或超时
reproduction: 1. 启动 S7 Server 2. 尝试读取 DB 块数据
started: 功能实现时

## Eliminated
<!-- APPEND only -->

## Evidence
<!-- APPEND only -->

- timestamp: 2026-04-29T10:01:00
  checked: full_agent.py s7_server_start 函数（行 1140-1243）
  found: full_agent.py 有简化版本的 s7_server_start，注释说明 "简化：不调用 register_s7_areas（避免死锁）"
  implication: 简化版本缺少关键初始化步骤

- timestamp: 2026-04-29T10:02:00
  checked: full_agent.py s7_server_get_data 函数（行 1310-1378）
  found: s7_server_get_data 检查 server_id in s7_servers 和 server_id in s7_data_storage，直接从 s7_data_storage 读取
  implication: get_data 不依赖 ctypes_buffers，理论上应该能读取数据

- timestamp: 2026-04-29T10:03:00
  checked: full_agent.py 全局变量导入（行 1103-1138）
  found: s7_servers, s7_server_lock, s7_data_storage 从 industrial_protocol_base 导入
  implication: 全局变量应该是共享的

- timestamp: 2026-04-29T10:04:00
  checked: industrial_protocol_base.py register_s7_areas 函数（行 2208-2330）
  found: register_s7_areas 使用 with s7_server_lock 锁，检查 server_id in s7_servers
  implication: 在 s7_server_start 锁内调用 register_s7_areas 会导致死锁！这就是简化版本不调用它的原因

- timestamp: 2026-04-29T10:05:00
  checked: industrial_protocol_base.py 完整版本 s7_server_start（行 3291+）
  found: 完整版本在服务器启动后调用 register_s7_areas 和 sync_s7_data_to_server，使用 time.sleep(0.5) 等待后注册
  implication: 完整版本在锁外调用 register_s7_areas，避免死锁

- timestamp: 2026-04-29T10:06:00
  checked: Flask 路由定义冲突问题
  found: two files define same route: full_agent.py line 1140 and industrial_protocol_base.py line 3280 both have `@app.route('/api/industrial_protocol/s7_server/start')`
  implication: Flask uses full_agent.py's simplified version, industrial_protocol_base.py's complete version is ignored

- timestamp: 2026-04-29T10:07:00
  checked: simplified version s7_server_start missing key steps
  found: full_agent.py's s7_server_start (line 1196 comment) says "简化：不调用 register_s7_areas（避免死锁）" - missing: register_s7_areas, ctypes_buffers init, callback registration, sync_s7_data_to_server
  implication: S7 Client cannot read data via snap7 protocol because DB areas are not registered

- timestamp: 2026-04-29T10:08:00
  checked: industrial_protocol_base.py complete version implementation (line 3705-3710)
  found: complete version calls register_s7_areas OUTSIDE the lock after time.sleep(0.5), avoiding deadlock
  implication: correct pattern: 1. init in lock, 2. start server in lock, 3. exit lock, 4. sleep, 5. register areas outside lock

## Resolution
root_cause: full_agent.py 中简化版本的 s7_server_start 存在两个关键问题：
1. **未调用 register_s7_areas**：导致 snap7 服务器未注册 DB 区域，S7 Client 通过协议读取时失败
2. **死锁恐惧导致错误简化**：注释说明避免死锁而不调用 register_s7_areas，但完整版本展示了正确的解决方案（在锁外调用）
fix: 修改 full_agent.py 的 s7_server_start，参照 industrial_protocol_base.py 完整版本的模式：
1. 在锁内初始化数据存储
2. 在锁内启动服务器
3. 退出锁
4. time.sleep(0.5) 等待服务器稳定
5. 在锁外调用 register_s7_areas(server_id, db_list=[1, 2, 3])
6. 在锁外调用 sync_s7_data_to_server(server_id)
7. 在锁内验证注册结果并存储 server_info
verification: 需测试：1. 启动成功 2. API读取成功 3. S7 Client协议读取成功
files_changed: []