#!/usr/bin/env python
"""测试 localhost 和其他方式"""

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

print("=== 检查进程是否阻塞 ===")
out = send_cmd("sudo ip netns exec ns-eth1 strace -p 291147 -e trace=network -t 2 2>&1 | head -20", 5)
print(out)

print("\n=== 尝试绑定 0.0.0.0 ===")
out = send_cmd("sudo ip netns exec ns-eth1 pkill -9 python", 2)
time.sleep(1)
cmd = "sudo ip netns exec ns-eth1 bash -c 'cd /opt/SFW-TEST3 && nohup /opt/SFW-TEST3/sfw/bin/python -m http.server 8888 --bind 0.0.0.0 > /opt/SFW-TEST3/logs/simple_http.log 2>&1 &'"
out = send_cmd(cmd, 3)

print("\n=== 测试 0.0.0.0 绑定 ===")
out = send_cmd("sudo ip netns exec ns-eth1 curl -v --max-time 3 http://127.0.0.1:8888/", 6)
print(out)

out = send_cmd("sudo ip netns exec ns-eth1 curl -v --max-time 3 http://192.168.11.100:8888/", 6)
print(out)

print("\n=== 检查端口监听 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ss -tlnp | grep 8888", 2)
print(out)

ssh.close()