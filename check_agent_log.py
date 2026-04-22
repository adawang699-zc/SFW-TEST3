#!/usr/bin/env python3
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

cmd = 'sudo journalctl -u agent-eth1 -n 50 --no-pager'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
out = stdout.read().decode('utf-8', errors='ignore')
print(out)

ssh.close()