#!/usr/bin/env python
"""检查内核网络参数"""

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

print("=== ns-eth1 路由表 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ip route show", 2)
print(out)

print("\n=== ns-eth1 本地路由表 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ip route show table local", 2)
print(out)

print("\n=== eth1 接口状态 ===")
out = send_cmd("sudo ip netns exec ns-eth1 ip link show eth1", 2)
print(out)

print("\n=== 内核参数 ===")
out = send_cmd("sudo ip netns exec ns-eth1 sysctl net.ipv4.conf.eth1.rp_filter net.ipv4.conf.eth1.accept_local", 3)
print(out)

print("\n=== 尝试设置 accept_local ===")
out = send_cmd("sudo ip netns exec ns-eth1 sysctl -w net.ipv4.conf.eth1.accept_local=1", 2)
print(out)

print("\n=== 再次测试连接 ===")
out = send_cmd("sudo ip netns exec ns-eth1 curl -v --max-time 3 http://192.168.11.100:8888/", 6)
print(out)

ssh.close()