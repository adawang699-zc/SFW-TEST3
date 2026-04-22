#!/usr/bin/env python3
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Test send packet with correct JSON format
test_data = {
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

# Use Python to make request
cmd = f'''cd /opt/SFW-TEST3 && sfw/bin/python -c "
import requests
import json
resp = requests.post('http://11.11.11.14:8891/api/send_packet', json={json.dumps(test_data)}, timeout=30)
print(resp.json())
"'''

print(f"Executing Python request...")
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
exit_status = stdout.channel.recv_exit_status()
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(f"Result: {out}")
if err:
    print(f"Error: {err}")

ssh.close()