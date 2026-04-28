#!/usr/bin/env python
"""检查最新日志"""

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

print("=== eth1 最新日志 ===")
out = send_cmd("sudo tail -50 /opt/SFW-TEST3/logs/agent_eth1_ns.log", 3)
print(out[-1500:] if out else "空")

print("\n=== eth2 最新日志 ===")
out = send_cmd("sudo tail -50 /opt/SFW-TEST3/logs/agent_eth2_ns.log", 3)
print(out[-1500:] if out else "空")

print("\n=== 直接测试 socket ===")
out = send_cmd("sudo ip netns exec ns-eth1 bash -c 'timeout 2 nc -zv 192.168.11.100 8888'", 4)
print(out)

ssh.close()