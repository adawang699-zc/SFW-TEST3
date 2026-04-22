#!/usr/bin/env python3
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

cmd = '''cd /opt/SFW-TEST3 && sfw/bin/python -c "
import requests
import time

BASE_URL = 'http://127.0.0.1:8000'

# Test status query
print('Testing status query...')
start = time.time()
resp = requests.get(f'{BASE_URL}/api/agents/status/', params={'agent_id': 'agent_eth1'}, timeout=10)
elapsed = time.time() - start
print(f'Response: {json.dumps(resp.json(), indent=2)}')
print(f'Elapsed: {elapsed:.2f}s")
"'''

stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()