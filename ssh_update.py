#!/usr/bin/env python3
"""SSH to Ubuntu and update code"""
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.140', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main',
    'sudo systemctl restart agent-eth1 agent-eth4',
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
    if err:
        print(f"  Err: {err[:200]}")

ssh.close()
print("Done!")

# Wait for agents to restart
time.sleep(3)

# Test status
import requests
resp = requests.get("http://192.168.81.140:8000/api/agents/status/?agent_id=agent_eth4", timeout=5)
print(f"eth4 status after restart: {resp.json()}")