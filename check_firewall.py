#!/usr/bin/env python
"""检查防火墙和直接测试"""

import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

shell = ssh.invoke_shell()
shell.settimeout(30)

def send_cmd(cmd, wait_time=3):
    shell.send(cmd + "\n")
    time.sleep(wait_time)
    output = ""
    while shell.recv_ready():
        output += shell.recv(8192).decode('utf-8', errors='ignore')
    return output

# 初始化
out = send_cmd("echo tdhx@2017 | sudo -S whoami", 2)

print("=== iptables 状态 ===")
out = send_cmd("sudo iptables -L -n | head -20", 2)
print(out)

print("\n=== iptables in ns-eth1 ===")
out = send_cmd("sudo ip netns exec ns-eth1 iptables -L -n", 3)
print(out)

print("\n=== 测试 TCP 连接 ===")
out = send_cmd("sudo ip netns exec ns-eth1 timeout 3 nc -v 192.168.11.100 8888", 5)
print(out)

print("\n=== 测试 TCP 连接 eth2 ===")
out = send_cmd("sudo ip netns exec ns-eth2 timeout 3 nc -v 192.168.12.100 8888", 5)
print(out)

print("\n=== 查看 Agent 进程状态 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ps aux | grep python | grep 8888", 3)
lines = [l for l in out.split('\n') if '8888' in l]
print(f"eth1 进程:\n{chr(10).join(lines)}")

ssh.close()