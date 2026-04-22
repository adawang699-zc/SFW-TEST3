#!/usr/bin/env python3
import paramiko
import socket

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30, banner_timeout=30)

    # Update code with longer timeout
    cmd = 'cd /opt/SFW-TEST3 && git fetch origin && git reset --hard origin/main'
    chan = ssh.get_transport().open_session()
    chan.settimeout(120)
    chan.exec_command(cmd)

    # Wait for completion
    while not chan.exit_status_ready():
        pass

    exit_status = chan.recv_exit_status()
    out = chan.recv(4096).decode('utf-8', errors='ignore')
    print(f"Update exit: {exit_status}")
    print(f"Output: {out[:200]}")

except Exception as e:
    print(f"Error: {e}")
finally:
    ssh.close()