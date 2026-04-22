#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Test status query after fix
script = '''
import requests
import time

BASE_URL = "http://127.0.0.1:8000"

print("Test 1: Query status without sending")
start = time.time()
resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": "agent_eth1"}, timeout=10)
elapsed = time.time() - start
print(f"  Status: {resp.json()}")
print(f"  Elapsed: {elapsed:.2f}s")

print("\\nTest 2: Query status for eth4")
start = time.time()
resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": "agent_eth4"}, timeout=10)
elapsed = time.time() - start
print(f"  Status: {resp.json()}")
print(f"  Elapsed: {elapsed:.2f}s")
'''

cmd = '''cat > /tmp/test_query.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

cmd2 = 'cd /opt/SFW-TEST3 && timeout 30 sfw/bin/python /tmp/test_query.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
print(out)

ssh.close()