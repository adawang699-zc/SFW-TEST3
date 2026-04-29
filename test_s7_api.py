#!/usr/bin/env python3
"""测试 S7 Server 启动 API 和 Agent 数据"""
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.140', 22, 'zhangc', 'tdhx@2017', timeout=30)

# 1. 检查 Agent 服务状态
print("=== 1. 检查 Agent 服务状态 ===")
cmd = "sudo systemctl status agent-eth1.service --no-pager"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out = stdout.read().decode('utf-8', errors='ignore')
print(out[:2000])

# 2. 检查 Agent 日志
print("\n=== 2. 检查 Agent 日志 ===")
cmd = "sudo journalctl -u agent-eth1.service --no-pager -n 50"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out = stdout.read().decode('utf-8', errors='ignore')
print(out[:3000])

# 3. 重启 Agent 服务
print("\n=== 3. 重启 Agent 服务 ===")
cmd = "sudo systemctl restart agent-eth1.service"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
exit_status = stdout.channel.recv_exit_status()
print(f"重启命令返回码: {exit_status}")

# 4. 等待并再次检查状态
print("\n=== 4. 再次检查状态 ===")
cmd = "sleep 3 && sudo systemctl status agent-eth1.service --no-pager"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
out = stdout.read().decode('utf-8', errors='ignore')
print(out[:2000])

ssh.close()