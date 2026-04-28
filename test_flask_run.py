#!/usr/bin/env python
"""用 Flask 开发服务器测试"""

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

print("=== 杀掉 gunicorn ===")
out = send_cmd("sudo ip netns exec ns-eth1 pkill -9 python", 2)
out = send_cmd("sudo ip netns exec ns-eth2 pkill -9 python", 2)
time.sleep(2)

print("\n=== 使用 Flask run 启动 eth2 ===")
cmd = "sudo ip netns exec ns-eth2 bash -c 'cd /opt/SFW-TEST3 && nohup /opt/SFW-TEST3/sfw/bin/python -c \"from agents.full_agent import app; app.run(host=\"192.168.12.100\",port=8888)\" > /opt/SFW-TEST3/logs/agent_eth2_flask.log 2>&1 &'"
out = send_cmd(cmd, 5)

print("\n=== 使用 Flask run 启动 eth1 ===")
cmd = "sudo ip netns exec ns-eth1 bash -c 'cd /opt/SFW-TEST3 && nohup /opt/SFW-TEST3/sfw/bin/python -c \"from agents.full_agent import app; app.run(host=\"192.168.11.100\",port=8888)\" > /opt/SFW-TEST3/logs/agent_eth1_flask.log 2>&1 &'"
out = send_cmd(cmd, 5)

time.sleep(5)

print("\n=== 检查进程 ===")
out = send_cmd("sudo ip netns pids ns-eth1", 2)
print(f"ns-eth1: {out.split(chr(10))[-3:-1]}")
out = send_cmd("sudo ip netns pids ns-eth2", 2)
print(f"ns-eth2: {out.split(chr(10))[-3:-1]}")

print("\n=== 测试 HTTP ===")
out = send_cmd("sudo ip netns exec ns-eth1 python3 -c \"import urllib.request; r=urllib.request.urlopen('http://192.168.11.100:8888/api/health',timeout=5); print(r.read().decode())\"", 10)
lines = [l for l in out.split(chr(10)) if '{' in l]
print(f"eth1: {lines[-1] if lines else out[-200:]}")

ssh.close()