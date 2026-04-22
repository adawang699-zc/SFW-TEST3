#!/usr/bin/env python3
"""Test complete flow via Django API from Ubuntu"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

script = '''
import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def query_status(agent_id):
    resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": agent_id}, timeout=5)
    return resp.json()

def start_send(agent_id):
    data = {
        "agent_id": agent_id,
        "packet_config": {
            "protocol": "tcp",
            "src_mac": "b4:4b:d6:55:f4:71",
            "dst_mac": "00:11:22:33:44:55",
            "src_ip": "11.11.11.14",
            "dst_ip": "11.11.11.1",
            "src_port": 12345,
            "dst_port": 80,
            "tcp_flags": {"syn": True}
        },
        "send_config": {
            "count": 100,
            "interval": 0,
            "continuous": False
        }
    }
    resp = requests.post(f"{BASE_URL}/api/send_packet/", json=data, timeout=30)
    return resp.json()

def stop_send(agent_id):
    resp = requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": agent_id}, timeout=5)
    return resp.json()

agent_id = "agent_eth4"

print("=" * 60)
print("Test: Django API -> Agent")
print("=" * 60)

print("\\n1. Query initial status")
status = query_status(agent_id)
print(f"   Status: {json.dumps(status, indent=2)}")

print("\\n2. Start sending")
result = start_send(agent_id)
print(f"   Result: {result}")

print("\\n3. Wait 3 seconds")
time.sleep(3)

print("\\n4. Check status after send")
status = query_status(agent_id)
stats = status.get('statistics', {})
print(f"   total_sent: {stats.get('total_sent', 0)}, rate: {stats.get('rate', 0)}")

print("\\n5. Stop sending")
result = stop_send(agent_id)
print(f"   Result: {result}")

print("\\n6. Check final status")
time.sleep(0.5)
status = query_status(agent_id)
stats = status.get('statistics', {})
print(f"   total_sent: {stats.get('total_sent', 0)}, rate: {stats.get('rate', 0)}")

print("\\n" + "=" * 60)
print("Test Complete")
print("=" * 60)
'''

# Write script
cmd = f'''cat > /tmp/test_django_api.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

# Execute
cmd2 = 'cd /opt/SFW-TEST3 && sfw/bin/python /tmp/test_django_api.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=60)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()