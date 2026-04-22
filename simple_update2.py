#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Update code
cmd = 'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
out = stdout.read().decode('utf-8', errors='ignore')
print("Update result:", out[:100])

ssh.close()