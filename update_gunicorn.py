#!/usr/bin/env python3
import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main',
    # 安装 gunicorn
    'cd /opt/SFW-TEST3 && sfw/bin/python -m pip install gunicorn -q',
    # 停止旧的 Agent 服务
    'sudo systemctl stop agent-eth1 agent-eth4',
    # 更新服务配置文件（使用 gunicorn）
    '''sudo tee /etc/systemd/system/agent-eth1.service << 'EOF'
[Unit]
Description=Packet Agent agent_eth1 (eth1)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=agent_eth1"
Environment="BIND_IP=11.11.11.11"
Environment="BIND_INTERFACE=eth1"
Environment="AGENT_PORT=8888"
WorkingDirectory=/opt/SFW-TEST3
ExecStart=/opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 11.11.11.11:8888 --preload --timeout 30 agents.full_agent:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF''',
    '''sudo tee /etc/systemd/system/agent-eth4.service << 'EOF'
[Unit]
Description=Packet Agent agent_eth4 (eth4)
After=network.target

[Service]
Type=simple
Environment="AGENT_ID=agent_eth4"
Environment="BIND_IP=11.11.11.14"
Environment="BIND_INTERFACE=eth4"
Environment="AGENT_PORT=8891"
WorkingDirectory=/opt/SFW-TEST3
ExecStart=/opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 11.11.11.14:8891 --preload --timeout 30 agents.full_agent:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF''',
    'sudo systemctl daemon-reload',
    'sudo systemctl start agent-eth1 agent-eth4',
]

for cmd in commands:
    print(f"\nExecuting: {cmd[:50]}...")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if exit_status != 0:
        print(f"  Exit: {exit_status}")
    if out:
        print(f"  Out: {out[:100]}")
    if err and 'password' not in err.lower():
        print(f"  Err: {err[:100]}")

# Wait for agents to start
time.sleep(5)

# Test status
print("\nTesting Agent status...")
cmd = '''cd /opt/SFW-TEST3 && sfw/bin/python -c "
import requests
import time

resp = requests.get('http://127.0.0.1:8000/api/agents/status/', params={'agent_id': 'agent_eth1'}, timeout=10)
print('eth1:', resp.json())

resp = requests.get('http://127.0.0.1:8000/api/agents/status/', params={'agent_id': 'agent_eth4'}, timeout=10)
print('eth4:', resp.json())
"'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
print(out)

ssh.close()
print("\nDone!")