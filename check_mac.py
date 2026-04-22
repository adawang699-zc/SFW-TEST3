#!/usr/bin/env python3
"""Check captured packets MAC addresses"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Check MAC addresses in captured packets
cmd = '''
echo "eth1 MAC: b4:4b:d6:55:f4:6e"
echo "eth4 MAC: b4:4b:d6:55:f4:71"
echo ""
echo "=== eth1 capture ==="
sudo tcpdump -r /tmp/eth1_capture.pcap -nn -e 2>&1 | grep "b4:4b" | head -10
echo ""
echo "=== eth4 capture ==="
sudo tcpdump -r /tmp/eth4_capture.pcap -nn -e 2>&1 | grep "b4:4b" | head -10
'''

stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
err = stderr.read().decode('utf-8', errors='ignore')
print(out)
if err:
    print(f"Error: {err}")

ssh.close()