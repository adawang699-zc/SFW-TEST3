#!/usr/bin/env python3
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    'sudo systemctl status agent-eth4 --no-pager',
    'sudo journalctl -u agent-eth4 -n 20 --no-pager',
    'curl -s http://11.11.11.14:8891/api/status',
]

for cmd in commands:
    print(f"\n{'='*60}")
    print(f"Executing: {cmd}")
    print('='*60)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(out)
    if err:
        print(f"Error: {err}")

ssh.close()