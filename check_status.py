#!/usr/bin/env python3
"""Check current sending status"""
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

script = '''
import requests

# Check both agents status
for agent_id in ['agent_eth1', 'agent_eth4']:
    resp = requests.get(f'http://127.0.0.1:8000/api/agents/status/', params={'agent_id': agent_id}, timeout=5)
    data = resp.json()
    stats = data.get('statistics', {})
    print(f"{agent_id}:")
    print(f"  status: {data.get('status')}")
    print(f"  interface: {data.get('interface')}")
    print(f"  total_sent: {stats.get('total_sent', 0)}")
    print(f"  rate: {stats.get('rate', 0)}")
    print(f"  bandwidth: {stats.get('bandwidth', 0)}")
    print()
'''

cmd = f'''cat > /tmp/check_status.py << 'EOFSCRIPT'
{script}
EOFSCRIPT'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=10)
stdout.channel.recv_exit_status()

cmd2 = 'cd /opt/SFW-TEST3 && sfw/bin/python /tmp/check_status.py'
stdin, stdout, stderr = ssh.exec_command(cmd2, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
print(out)

ssh.close()