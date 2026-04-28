#!/usr/bin/env python
"""重启 Agent (去掉 preload) 并测试"""

import paramiko
import time

UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)

shell = ssh.invoke_shell()
shell.settimeout(30)

def send_cmd(cmd, wait_time=3):
    shell.send(cmd + "\n")
    time.sleep(wait_time)
    output = ""
    while shell.recv_ready():
        output += shell.recv(8192).decode('utf-8', errors='ignore')
    return output

# 初始化 sudo
out = send_cmd(f"echo {UBUNTU_PASSWORD} | sudo -S whoami", 2)

print("=== 杀掉之前的 Agent ===")
out = send_cmd("sudo ip netns exec ns-eth1 pkill -9 -f 'gunicorn.*192.168.11'", 2)
out = send_cmd("sudo ip netns exec ns-eth2 pkill -9 -f 'gunicorn.*192.168.12'", 2)
time.sleep(2)

print("\n=== 启动 Agent eth2 (无 preload) ===")
cmd = "sudo ip netns exec ns-eth2 bash -c 'cd /opt/SFW-TEST3 && nohup /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.12.100:8888 --timeout 30 agents.full_agent:app > /opt/SFW-TEST3/logs/agent_eth2_ns.log 2>&1 &'"
out = send_cmd(cmd, 5)

print("\n=== 启动 Agent eth1 (无 preload) ===")
cmd = "sudo ip netns exec ns-eth1 bash -c 'cd /opt/SFW-TEST3 && nohup /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.11.100:8888 --timeout 30 agents.full_agent:app > /opt/SFW-TEST3/logs/agent_eth1_ns.log 2>&1 &'"
out = send_cmd(cmd, 5)

time.sleep(5)

print("\n=== 检查进程 ===")
out = send_cmd("sudo ip netns pids ns-eth1", 2)
print(f"ns-eth1: {out.split(chr(10))[-3:-1]}")
out = send_cmd("sudo ip netns pids ns-eth2", 2)
print(f"ns-eth2: {out.split(chr(10))[-3:-1]}")

print("\n=== Agent health ===")
out = send_cmd("sudo ip netns exec ns-eth1 curl -s --max-time 5 http://192.168.11.100:8888/api/health", 7)
lines = [l.strip() for l in out.split(chr(10)) if l.strip() and 'curl' not in l and 'sudo' not in l]
print(f"eth1: {lines[-1] if lines else 'timeout'}")

out = send_cmd("sudo ip netns exec ns-eth2 curl -s --max-time 5 http://192.168.12.100:8888/api/health", 7)
lines = [l.strip() for l in out.split(chr(10)) if l.strip() and 'curl' not in l and 'sudo' not in l]
print(f"eth2: {lines[-1] if lines else 'timeout'}")

# 启动 Modbus Server
print("\n=== 启动 Modbus Server ===")
cmd = "sudo ip netns exec ns-eth2 curl -s --max-time 10 -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"interface\":\"192.168.12.100\",\"port\":502}'"
out = send_cmd(cmd, 12)
lines = [l.strip() for l in out.split(chr(10)) if l.strip() and '{' in l]
print(f"Server: {lines[-1] if lines else 'no response'}")

time.sleep(2)

# Modbus Client 连接
print("\n=== Modbus Client 连接 ===")
cmd = "sudo ip netns exec ns-eth1 curl -s --max-time 10 -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"host\":\"192.168.12.100\",\"port\":502}'"
out = send_cmd(cmd, 12)
lines = [l.strip() for l in out.split(chr(10)) if l.strip() and '{' in l]
print(f"Connect: {lines[-1] if lines else 'no response'}")

time.sleep(1)

# Modbus 读取
print("\n=== Modbus 读取 ===")
cmd = "sudo ip netns exec ns-eth1 curl -s --max-time 10 -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'"
out = send_cmd(cmd, 12)
lines = [l.strip() for l in out.split(chr(10)) if l.strip() and '{' in l]
print(f"Read: {lines[-1] if lines else 'no response'}")

ssh.close()
print("\n=== 完成 ===")