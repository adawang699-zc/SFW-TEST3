#!/usr/bin/env python
"""使用 invoke_shell 方式执行 SSH 命令"""

import paramiko
import time

UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)

shell = ssh.invoke_shell()
shell.settimeout(10)

def send_cmd(cmd, wait_time=2):
    shell.send(cmd + "\n")
    time.sleep(wait_time)
    output = ""
    while shell.recv_ready():
        output += shell.recv(4096).decode('utf-8', errors='ignore')
    return output

# 发送 sudo 密码
print("=== 发送 sudo 密码 ===")
out = send_cmd(f"echo {UBUNTU_PASSWORD} | sudo -S whoami")
print(out)

print("\n=== 1. Namespace 状态 ===")
out = send_cmd("sudo ip netns list")
print(out.split('\n')[-3:-1])  # 取最后几行

print("\n=== 2. Ping 测试 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ping -c 2 192.168.12.100", 5)
print(out)

print("\n=== 3. 检查 Agent 进程 ===")
out = send_cmd("sudo ip netns pids ns-eth1")
print(f"ns-eth1: {out.split('\n')[-2]}")
out = send_cmd("sudo ip netns pids ns-eth2")
print(f"ns-eth2: {out.split('\n')[-2]}")

print("\n=== 4. Agent health ===")
out = send_cmd("sudo ip netns exec ns-eth1 curl -s http://192.168.11.100:8888/api/health", 3)
print(f"eth1: {out.split('\n')[-2]}")
out = send_cmd("sudo ip netns exec ns-eth2 curl -s http://192.168.12.100:8888/api/health", 3)
print(f"eth2: {out.split('\n')[-2]}")

ssh.close()
print("\n完成")