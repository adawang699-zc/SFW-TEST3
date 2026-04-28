#!/usr/bin/env python
"""Network Namespace Modbus 测试 - 最简版本"""

import paramiko
import sys

UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)

def run(cmd, sudo=False, wait=True):
    if sudo:
        cmd = f"echo {UBUNTU_PASSWORD} | sudo -S {cmd}"
    chan = ssh.get_transport().open_session()
    chan.settimeout(60 if wait else 5)
    chan.exec_command(cmd)
    if wait:
        exit_status = chan.recv_exit_status()
        out = chan.recv(4096).decode('utf-8', errors='ignore')
        err = chan.recv_stderr(4096).decode('utf-8', errors='ignore')
        return exit_status, out, err
    else:
        # 后台命令，不等待
        import time
        time.sleep(1)
        return 0, "", ""

print("=== 1. Namespace 状态 ===")
code, out, err = run("ip netns list", sudo=True, wait=True)
print(out)

print("\n=== 2. Ping 测试 ===")
code, out, err = run("ip netns exec ns-eth1 ping -c 1 192.168.12.100", sudo=True, wait=True)
print(out)

print("\n=== 3. 检查进程 ===")
code, out, err = run("ip netns pids ns-eth1", sudo=True, wait=True)
print(f"ns-eth1 进程: {out.strip()}")
code, out, err = run("ip netns pids ns-eth2", sudo=True, wait=True)
print(f"ns-eth2 进程: {out.strip()}")

print("\n=== 4. Agent health ===")
code, out, err = run("ip netns exec ns-eth1 curl -s http://192.168.11.100:8888/api/health", sudo=True, wait=True)
print(f"eth1: {out.strip()}")
code, out, err = run("ip netns exec ns-eth2 curl -s http://192.168.12.100:8888/api/health", sudo=True, wait=True)
print(f"eth2: {out.strip()}")

print("\n=== 5. Modbus Server ===")
cmd = "ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"interface\":\"192.168.12.100\",\"port\":502}'"
code, out, err = run(cmd, sudo=True, wait=True)
print(f"启动: {out.strip()}")

import time
time.sleep(2)

print("\n=== 6. Modbus Client 连接 ===")
cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"host\":\"192.168.12.100\",\"port\":502}'"
code, out, err = run(cmd, sudo=True, wait=True)
print(f"连接: {out.strip()}")

time.sleep(1)

print("\n=== 7. Modbus 读取 ===")
cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'"
code, out, err = run(cmd, sudo=True, wait=True)
print(f"读取: {out.strip()}")

print("\n=== 8. 抓包验证 ===")
# 先触发一次读取
run("ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'", sudo=True, wait=True)

# 然后检查之前的抓包日志（如果存在）
code, out, err = run("ls -la /opt/SFW-TEST3/logs/*.pcap 2>/dev/null", sudo=True, wait=True)
print(f"抓包文件: {out.strip() if out.strip() else '无'}")

ssh.close()
print("\n测试完成")