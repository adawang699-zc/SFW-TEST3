#!/usr/bin/env python3
"""Test packet send isolation between eth1 and eth4"""
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

script = '''
import requests
import subprocess
import time
import threading
import json

BASE_URL = "http://127.0.0.1:8000"

# Start tcpdump on eth1 and eth4
print("Starting tcpdump on eth1 and eth4...")

def run_tcpdump(interface, output_file):
    cmd = f"sudo timeout 10 tcpdump -i {interface} -c 50 -nn 'tcp and port 80' -w {output_file} 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    return result.stdout

# Start tcpdump in background
tcpdump_eth1 = threading.Thread(target=lambda: run_tcpdump('eth1', '/tmp/eth1_capture.pcap'))
tcpdump_eth4 = threading.Thread(target=lambda: run_tcpdump('eth4', '/tmp/eth4_capture.pcap'))
tcpdump_eth1.start()
tcpdump_eth4.start()

# Wait for tcpdump to start
time.sleep(2)

# Send packets from eth1
print("Sending packets from eth1...")
data = {
    "agent_id": "agent_eth1",
    "packet_config": {
        "protocol": "tcp",
        "src_mac": "b4:4b:d6:55:f4:6e",  # eth1 MAC
        "dst_mac": "00:11:22:33:44:55",
        "src_ip": "11.11.11.11",  # eth1 IP
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
print(f"Send result: {resp.json()}")

# Wait for tcpdump to finish
time.sleep(8)
tcpdump_eth1.join()
tcpdump_eth4.join()

# Check capture files
print("\\nChecking capture files...")
for iface in ['eth1', 'eth4']:
    cmd = f"sudo tcpdump -r /tmp/{iface}_capture.pcap -nn 2>&1 | head -20"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
    lines = result.stdout.strip().split('\\n')
    packet_count = len([l for l in lines if 'IP' in l])
    print(f"{iface}: {packet_count} packets captured")
    if packet_count > 0:
        print(f"  Sample: {lines[0] if lines else 'none'}")

# Stop sending
requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": "agent_eth1"}, timeout=5)
'''

# Write script
cmd = f'''cat > /tmp/test_isolation.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

# Execute
print("Running isolation test...")
cmd2 = 'cd /opt/SFW-TEST3 && sudo sfw/bin/python /tmp/test_isolation.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=90)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()