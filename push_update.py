#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Update code
cmd = 'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main && git log -1 --oneline'
chan = ssh.get_transport().open_session()
chan.settimeout(60)
chan.exec_command(cmd)
while not chan.exit_status_ready():
    pass
out = chan.recv(512).decode('utf-8', errors='ignore')
print("Update:", out)

ssh.close()