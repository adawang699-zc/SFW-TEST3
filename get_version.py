#!/usr/bin/env python3
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Update and check version
cmd = 'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
time.sleep(2)

cmd2 = 'cd /opt/SFW-TEST3 && git log -1 --oneline'
stdin2, stdout2, stderr2 = ssh.exec_command(cmd2, timeout=10)
out = stdout2.read().decode('utf-8', errors='ignore')
print("Latest commit:", out)

ssh.close()