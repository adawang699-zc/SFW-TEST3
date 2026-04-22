#!/usr/bin/env python3
"""Test Agent responsiveness during packet sending"""
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

script = '''
import requests
import time
import threading

BASE_URL = "http://127.0.0.1:8000"

# Start continuous sending from eth1
def start_send():
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
            "count": 1000,
            "interval": 0,
            "continuous": True
        }
    }
    resp = requests.post(f"{BASE_URL}/api/send_packet/", json=data, timeout=30)
    print(f"Start send: {resp.json()}")

# Query status repeatedly
def query_status_loop():
    for i in range(10):
        start = time.time()
        try:
            resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": "agent_eth1"}, timeout=5)
            elapsed = time.time() - start
            data = resp.json()
            status = data.get('status', 'unknown')
            stats = data.get('statistics', {})
            total = stats.get('total_sent', 0)
            print(f"Query {i+1}: status={status}, total_sent={total}, elapsed={elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - start
            print(f"Query {i+1}: ERROR {e}, elapsed={elapsed:.2f}s")
        time.sleep(1)

# Start sending
print("Starting continuous send on eth1...")
start_send()

# Query status while sending
print("\\nQuerying status during sending...")
query_status_loop()

# Stop sending
print("\\nStopping...")
resp = requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": "agent_eth1"}, timeout=5)
print(f"Stop: {resp.json()}")
'''

# Write script
cmd = f'''cat > /tmp/test_status_during_send.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

# Execute
cmd2 = 'cd /opt/SFW-TEST3 && sfw/bin/python /tmp/test_status_during_send.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=60)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()