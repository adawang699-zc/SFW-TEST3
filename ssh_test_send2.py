#!/usr/bin/env python3
"""Test send packet via SSH"""
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Write a Python script file on Ubuntu and execute it
script = '''
import requests
import json
import time

data = {
    "interface": "eth4",
    "packet_config": {
        "protocol": "tcp",
        "src_mac": "b4:4b:d6:55:f4:71",
        "dst_mac": "00:11:22:33:44:55",
        "src_ip": "11.11.11.14",
        "dst_ip": "11.11.11.1",
        "src_port": 12345,
        "dst_port": 80,
        "flags": ["SYN"]
    },
    "send_config": {
        "count": 100,
        "interval": 0,
        "continuous": False
    }
}

# Test send packet
print("1. Sending packet to agent_eth4...")
resp = requests.post('http://11.11.11.14:8891/api/send_packet', json=data, timeout=30)
print(f"   Result: {resp.json()}")

# Wait 2 seconds
time.sleep(2)

# Check statistics
print("2. Checking statistics...")
resp = requests.get('http://11.11.11.14:8891/api/statistics', timeout=5)
print(f"   Statistics: {resp.json()}")

# Stop sending
print("3. Stopping...")
resp = requests.post('http://11.11.11.14:8891/api/stop', json={}, timeout=5)
print(f"   Stop result: {resp.json()}")

# Check final statistics
print("4. Final statistics...")
resp = requests.get('http://11.11.11.14:8891/api/statistics', timeout=5)
print(f"   Statistics: {resp.json()}")
'''

# Write script to Ubuntu
cmd = f'''cat > /tmp/test_agent.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''

stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

# Execute script
cmd2 = 'cd /opt/SFW-TEST3 && sfw/bin/python /tmp/test_agent.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=60)
exit_status = stdout.channel.recv_exit_status()
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()