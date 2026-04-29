#!/usr/bin/env python3
"""测试 S7 Server 启动 API 和 Agent 数据"""
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.140', 22, 'zhangc', 'tdhx@2017', timeout=30)

# 1. 获取 Agent 列表
print("=== 1. 获取 Agent 列表 ===")
cmd = "curl -s 'http://127.0.0.1:8000/api/agents/list/'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out = stdout.read().decode('utf-8', errors='ignore')

# 解析获取 agent_eth1 的 IP 和 port
agent_info = None
if out:
    try:
        resp = json.loads(out)
        for agent in resp.get('agents', []):
            if agent['agent_id'] == 'agent_eth1' and agent['status'] == 'running':
                agent_info = agent
                print(f"找到 agent_eth1: IP={agent['ip_address']}, Port={agent['port']}, Namespace={agent.get('namespace')}")
                break
    except json.JSONDecodeError as e:
        print(f'JSON Parse Error: {e}')

if not agent_info:
    print("没有找到运行中的 agent_eth1")
    ssh.close()
    exit(1)

ip = agent_info['ip_address']
port = agent_info['port']
namespace = agent_info.get('namespace', '')

# 2. 检查 Agent API 路由列表
print("\n=== 2. 检查 Agent API 路由 ===")
if namespace:
    cmd = f"sudo ip netns exec {namespace} curl -s 'http://{ip}:{port}/api/routes'"
else:
    cmd = f"curl -s 'http://{ip}:{port}/api/routes'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out_routes = stdout.read().decode('utf-8', errors='ignore')
print(out_routes[:2000] if out_routes else "(empty)")

# 3. 测试 Agent 基本状态
print("\n=== 3. 测试 Agent 基本状态 ===")
if namespace:
    cmd = f"sudo ip netns exec {namespace} curl -s 'http://{ip}:{port}/api/status'"
else:
    cmd = f"curl -s 'http://{ip}:{port}/api/status'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out_status = stdout.read().decode('utf-8', errors='ignore')
print(out_status[:1000] if out_status else "(empty)")

# 4. 直接测试 Agent S7 Server API
print("\n=== 4. 直接测试 Agent S7 Server API ===")
data = {
    "server_id": "default",
    "host": "0.0.0.0",
    "port": 102
}
json_str = json.dumps(data)

if namespace:
    cmd = f"sudo ip netns exec {namespace} curl -s -X POST -H 'Content-Type: application/json' -d '{json_str}' 'http://{ip}:{port}/api/industrial_protocol/s7_server/start'"
else:
    cmd = f"curl -s -X POST -H 'Content-Type: application/json' -d '{json_str}' 'http://{ip}:{port}/api/industrial_protocol/s7_server/start'"

print(f"Command: {cmd}")
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out2 = stdout.read().decode('utf-8', errors='ignore')
err2 = stderr.read().decode('utf-8', errors='ignore')
print(f"\n=== Response ===")
print(out2[:2000] if out2 else "(empty)")
print(f"\n=== stderr ===")
print(err2[:500] if err2 else "(empty)")

ssh.close()