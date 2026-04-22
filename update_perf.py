#!/usr/bin/env python3
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main',
    # Restart Django
    'pkill -f "manage.py runserver" || true',
    'sleep 1',
    'cd /opt/SFW-TEST3 && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &',
    'sleep 2',
]

for cmd in commands:
    print(f"Executing: {cmd[:50]}...")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    if exit_status != 0:
        print(f"  Exit: {exit_status}")
    if out:
        print(f"  Out: {out[:100]}")

# Test performance
print("\nTesting /api/agents/list/ performance...")
script = '''
import requests
import time

start = time.time()
resp = requests.get('http://127.0.0.1:8000/api/agents/list/', timeout=30)
elapsed = time.time() - start
data = resp.json()
agent_count = len(data.get('agents', []))
print('Response:', 'success' if data.get('agents') else 'error')
print('Agent count:', agent_count)
print('Elapsed:', round(elapsed, 3), 'seconds')
'''

cmd = "cat > /tmp/perf.py << 'EOF'\n" + script + "\nEOF"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

cmd2 = 'cd /opt/SFW-TEST3 && timeout 15 sfw/bin/python /tmp/perf.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=20)
out = stdout.read().decode('utf-8', errors='ignore')
print(out)

ssh.close()
print("\nDone!")