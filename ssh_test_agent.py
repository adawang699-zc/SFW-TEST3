#!/usr/bin/env python3
import paramiko
import json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

# Test from Django server (192.168.81.105) to Agent (11.11.11.14:8891)
commands = [
    # Check Django can reach Agent
    'curl -s --connect-timeout 5 http://11.11.11.14:8891/api/status',
    # Test send packet from Django server
    '''curl -s -X POST http://11.11.11.14:8891/api/send_packet -H "Content-Type: application/json" -d '{"interface":"eth4","packet_config":{"protocol":"tcp","src_mac":"b4:4b:d6:55:f4:71","dst_mac":"00:11:22:33:44:55","src_ip":"11.11.11.14","dst_ip":"11.11.11.1","src_port":12345,"dst_port":80,"tcp_flags":{"syn":True}},"send_config":{"count":100,"interval":0,"continuous":false}}' ''',
    # Check statistics
    'curl -s --connect-timeout 5 http://11.11.11.14:8891/api/statistics',
]

for i, cmd in enumerate(commands):
    print(f"\n{'='*60}")
    print(f"Test {i+1}: {cmd[:50]}...")
    print('='*60)
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(out)
    if err:
        print(f"Error: {err}")

ssh.close()