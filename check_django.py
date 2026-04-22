#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Check Django process and restart if needed
commands = [
    'pgrep -f "manage.py runserver" || echo "Django not running"',
    'curl -s --connect-timeout 5 http://127.0.0.1:8000/api/agents/list/ | head -c 200 || echo "Django API not responding"',
]

for cmd in commands:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore')
    print(f"CMD: {cmd}")
    print(f"Result: {out}")

ssh.close()