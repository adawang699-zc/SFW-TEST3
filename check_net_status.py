#!/usr/bin/env python
"""检查网络和端口状态"""

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

print("=== ns-eth1 网络状态 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ip addr show eth1", 2)
print(out)

print("\n=== ns-eth1 端口监听 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ss -tlnp | grep 8888", 2)
print(out)

print("\n=== ns-eth2 网络状态 ===")
out = send_cmd("sudo ip netns exec ns-eth2 ip addr show eth2", 2)
print(out)

print("\n=== ns-eth2 端口监听 ===")
out = send_cmd("sudo ip netns exec ns-eth2 ss -tlnp | grep 8888", 2)
print(out)

print("\n=== socket 连接测试 ===")
out = send_cmd("sudo ip netns exec ns-eth1 python3 -c \"import socket; s=socket.socket(); s.settimeout(2); r=s.connect_ex(('192.168.11.100',8888)); print('Result:',r)\"", 5)
print(out)

ssh.close()