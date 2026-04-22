#!/usr/bin/env python3
"""Check network topology and agent binding"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    # Check network interfaces
    'ip addr show eth1 eth4',
    # Check if there's a bridge
    'ip link show type bridge',
    # Check systemd services config
    'cat /etc/systemd/system/agent-eth1.service',
    'cat /etc/systemd/system/agent-eth4.service',
    # Check if any agent is running with wrong interface
    'ps aux | grep python | grep agent',
]

for cmd in commands:
    print(f"\n{'='*60}")
    print(f"CMD: {cmd}")
    print('='*60)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(out[:500])
    if err:
        print(f"Error: {err[:200]}")

ssh.close()