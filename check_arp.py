#!/usr/bin/env python3
"""Check ARP and network connections"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    # Check ARP table for eth1 and eth4
    'ip neigh show dev eth1',
    'ip neigh show dev eth4',
    # Check if eth1 and eth4 can ping each other's IP
    'ping -c 1 -I eth1 11.11.11.14',
    'ping -c 1 -I eth4 11.11.11.11',
    # Check routing table
    'ip route show table main | grep 11.11.11',
    # Check if there's any bridge configuration
    'ls -la /sys/class/net/eth*/bridge 2>/dev/null || echo "No bridges"',
    # Check if eth1 and eth4 share the same physical device (PCI)
    'ls -la /sys/class/net/',
]

for cmd in commands:
    print(f"\n{'='*50}")
    print(f"CMD: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    print(out[:300])
    if err:
        print(f"Error: {err[:100]}")

ssh.close()