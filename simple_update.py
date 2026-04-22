#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Update code
cmd = 'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main && echo "UPDATE_DONE"'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
out = stdout.read().decode('utf-8', errors='ignore')
print("Update:", out[:100])

# Test API performance
cmd2 = 'cd /opt/SFW-TEST3 && timeout 10 sfw/bin/python -c "
import requests
import time
start = time.time()
resp = requests.get(\'http://127.0.0.1:8000/api/agents/list/\', timeout=30)
elapsed = time.time() - start
print(\'Elapsed:\', round(elapsed, 3), \'s\')
"'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=15)
out = stdout.read().decode('utf-8', errors='ignore')
print("Performance:", out)

ssh.close()