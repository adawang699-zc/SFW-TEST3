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

# Start continuous sending
print('Starting continuous send on eth1...')
data = {
    "agent_id": "agent_eth1",
    "packet_config": {
        "protocol": "tcp",
        "src_mac": "b4:4b:d6:55:f4:6e",
        "dst_mac": "00:11:22:33:44:55",
        "src_ip": "11.11.11.11",
        "dst_ip": "11.11.11.1",
        "src_port": 12345,
        "dst_port": 80,
        "tcp_flags": {"syn": True}
    },
    "send_config": {
        "count": 10000,
        "interval": 0,
        "continuous": True
    }
}
resp = requests.post(BASE_URL + '/api/send_packet/', json=data, timeout=15)
print('Start result:', resp.json())

# Query status while sending
print('\\nQuerying status during sending (5 queries)...')
for i in range(5):
    start = time.time()
    try:
        resp = requests.get(BASE_URL + '/api/agents/status/', params={'agent_id': 'agent_eth1'}, timeout=10)
        elapsed = time.time() - start
        data = resp.json()
        status = data.get('status', 'unknown')
        query_failed = data.get('query_failed', False)
        stats = data.get('statistics', {})
        total = stats.get('total_sent', 0)
        rate = stats.get('rate', 0)
        print(f'Query {i+1}: status={status}, query_failed={query_failed}, total={total}, rate={rate}, elapsed={elapsed:.2f}s')
    except Exception as e:
        elapsed = time.time() - start
        print(f'Query {i+1}: ERROR {e}, elapsed={elapsed:.2f}s')
    time.sleep(1)

# Stop sending
print('\\nStopping...')
resp = requests.post(BASE_URL + '/api/stop_send/', json={'agent_id': 'agent_eth1'}, timeout=15)
print('Stop result:', resp.json())
'''

# Write script to file
cmd = "cat > /tmp/t2.py << 'EOF'\n" + script + "\nEOF"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

cmd2 = 'cd /opt/SFW-TEST3 && timeout 30 sfw/bin/python /tmp/t2.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=35)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()