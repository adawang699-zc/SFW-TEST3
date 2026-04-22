#!/usr/bin/env python3
"""Check physical network topology"""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('192.168.81.105', 22, 'zhangc', 'tdhx@2017', timeout=30)

commands = [
    # Check if eth1 and eth4 are in promiscuous mode
    'ip link show eth1 | grep PROMISC',
    'ip link show eth4 | grep PROMISC',
    # Check if there's any bonding or teaming
    'cat /proc/net/bonding/* 2>/dev/null || echo "No bonding"',
    # Check network device details
    'ethtool -i eth1',
    'ethtool -i eth4',
    # Check link status
    'ethtool eth1 | grep -i link',
    'ethtool eth4 | grep -i link',
    # Check if interfaces are connected to same switch (check carrier)
    'cat /sys/class/net/eth1/carrier',
    'cat /sys/class/net/eth4/carrier',
]

for cmd in commands:
    print(f"\nCMD: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    if out:
        print(f"  Result: {out[:200]}")
    if err:
        print(f"  Error: {err[:100]}")

ssh.close()