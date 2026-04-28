#!/usr/bin/env python
"""检查 Flask 日志"""

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

print("=== Flask eth1 日志 ===")
out = send_cmd("sudo cat /opt/SFW-TEST3/logs/agent_eth1_flask.log", 3)
print(out[-1500:] if out else "空")

print("\n=== Flask eth2 日志 ===")
out = send_cmd("sudo cat /opt/SFW-TEST3/logs/agent_eth2_flask.log", 3)
print(out[-1500:] if out else "空")

ssh.close()