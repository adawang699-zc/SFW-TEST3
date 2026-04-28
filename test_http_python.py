#!/usr/bin/env python
"""直接测试 HTTP 请求"""

import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

shell = ssh.invoke_shell()
shell.settimeout(60)

def send_cmd(cmd, wait_time=5):
    shell.send(cmd + "\n")
    time.sleep(wait_time)
    output = ""
    while shell.recv_ready():
        output += shell.recv(8192).decode('utf-8', errors='ignore')
    return output

# 初始化
out = send_cmd("echo tdhx@2017 | sudo -S whoami", 2)

print("=== 使用 Python 测试 HTTP ===")
cmd = """sudo ip netns exec ns-eth1 python3 -c "
import urllib.request
import json
try:
    req = urllib.request.urlopen('http://192.168.11.100:8888/api/health', timeout=5)
    print(req.read().decode())
except Exception as e:
    print(f'Error: {e}')
"
"""
out = send_cmd(cmd, 10)
print(out)

print("\n=== 使用 Python 测试 eth2 ===")
cmd = """sudo ip netns exec ns-eth2 python3 -c "
import urllib.request
import json
try:
    req = urllib.request.urlopen('http://192.168.12.100:8888/api/health', timeout=5)
    print(req.read().decode())
except Exception as e:
    print(f'Error: {e}')
"
"""
out = send_cmd(cmd, 10)
print(out)

ssh.close()