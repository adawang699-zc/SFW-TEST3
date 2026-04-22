#!/usr/bin/env python3
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main',
    'pkill -f "manage.py runserver" || true',
    'cd /opt/SFW-TEST3 && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &',
]

for cmd in commands:
    print(f"Executing: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=120)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(f"  Exit: {exit_status}")
    if out:
        print(f"  Out: {out[:200]}")

# Wait for Django to start
time.sleep(3)

# Test API
print("\nTesting API...")
stdin, stdout, stderr = ssh.exec_command('curl -s http://127.0.0.1:8000/api/agents/status/?agent_id=agent_eth1', timeout=10)
out = stdout.read().decode('utf-8', errors='ignore')
print(f"  Response: {out[:200]}")

ssh.close()
print("\nDone!")