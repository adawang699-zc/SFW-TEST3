#!/usr/bin/env python
"""检查 Agent 日志"""

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
out = send_cmd(f"echo {UBUNTU_PASSWORD} | sudo -S whoami")

print("=== Agent eth1 日志 ===")
out = send_cmd("sudo cat /opt/SFW-TEST3/logs/agent_eth1_ns.log", 2)
print(out[-2000:] if out else "日志为空")

print("\n=== Agent eth2 日志 ===")
out = send_cmd("sudo cat /opt/SFW-TEST3/logs/agent_eth2_ns.log", 2)
print(out[-2000:] if out else "日志为空")

print("\n=== 检查进程详情 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ps aux | grep gunicorn", 2)
lines = [l.strip() for l in out.split('\n') if 'gunicorn' in l]
print(f"ns-eth1 进程:\n{chr(10).join(lines)}")

out = send_cmd("sudo ip netns exec ns-eth2 ps aux | grep gunicorn", 2)
lines = [l.strip() for l in out.split('\n') if 'gunicorn' in l]
print(f"ns-eth2 进程:\n{chr(10).join(lines)}")

print("\n=== 再次尝试 health ===")
out = send_cmd("sudo ip netns exec ns-eth1 curl -v http://192.168.11.100:8888/api/health", 5)
print(out)

ssh.close()