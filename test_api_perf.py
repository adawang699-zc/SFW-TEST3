#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

    # Test API performance directly
    script = '''
import requests
import time

# Test agents list API
print("Testing /api/agents/list/...")
start = time.time()
resp = requests.get('http://127.0.0.1:8000/api/agents/list/', timeout=30)
elapsed = time.time() - start
data = resp.json()
agent_count = len(data.get('agents', []))
print('Agents:', agent_count)
print('Elapsed:', round(elapsed, 3), 'seconds')

# Test my-rented API (used in packet send page)
print("\\nTesting /api/agents/my-rented/...")
start = time.time()
resp = requests.get('http://127.0.0.1:8000/api/agents/my-rented/', timeout=30)
elapsed = time.time() - start
print('Elapsed:', round(elapsed, 3), 'seconds')
'''

    cmd = "cat > /tmp/test_perf.py << 'EOF'\n" + script + "\nEOF"
    chan = ssh.get_transport().open_session()
    chan.settimeout(30)
    chan.exec_command(cmd)
    while not chan.exit_status_ready():
        pass

    cmd2 = 'cd /opt/SFW-TEST3 && timeout 20 sfw/bin/python /tmp/test_perf.py'
    chan2 = ssh.get_transport().open_session()
    chan2.settimeout(25)
    chan2.exec_command(cmd2)
    while not chan2.exit_status_ready():
        pass
    out = chan2.recv(4096).decode('utf-8', errors='ignore')
    print(out)

except Exception as e:
    print(f"Error: {e}")
finally:
    ssh.close()