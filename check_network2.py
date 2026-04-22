#!/usr/bin/env python3
"""Check detailed network and process info"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    # Check eth1 and eth4 separately
    'ip addr show eth1',
    'ip addr show eth4',
    # Check process details with ports
    'sudo ss -tlnp | grep 8888',
    'sudo ss -tlnp | grep 8891',
    # Check if interfaces are on same physical machine
    'ip route show',
    # Check if eth1 and eth4 are connected via bridge or bond
    'ls -la /sys/class/net/',
]

for cmd in commands:
    print(f"\n{'='*60}")
    print(f"CMD: {cmd}")
    print('='*60)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(out)
    if err:
        print(f"Error: {err}")

ssh.close()