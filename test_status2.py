#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

script = '''
import requests
import time
import json

BASE_URL = 'http://127.0.0.1:8000'

print('Testing status query...')
start = time.time()
resp = requests.get(BASE_URL + '/api/agents/status/', params={'agent_id': 'agent_eth1'}, timeout=10)
elapsed = time.time() - start
print('Response:', json.dumps(resp.json(), indent=2))
print('Elapsed:', elapsed, 'seconds')
'''

# Write script to file
cmd = "cat > /tmp/t.py << 'EOF'\n" + script + "\nEOF"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

cmd2 = 'cd /opt/SFW-TEST3 && sfw/bin/python /tmp/t.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()