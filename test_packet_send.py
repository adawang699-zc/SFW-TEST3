#!/usr/bin/env python3
"""测试报文发送功能"""
import requests
import json

# 测试 1: 向 eth4 发送报文
print("=" * 60)
print("测试 1: 报文发送是否单个网卡发送")
print("=" * 60)

data = {
    "agent_id": "agent_eth4",
    "packet_config": {
        "protocol": "tcp",
        "src_mac": "b4:4b:d6:55:f4:71",
        "dst_mac": "00:11:22:33:44:55",
        "src_ip": "11.11.11.14",
        "dst_ip": "11.11.11.1",
        "src_port": 12345,
        "dst_port": 80,
        "tcp_flags": {"syn": True}
    },
    "send_config": {
        "count": 100,
        "interval": 0,
        "continuous": False
    }
}

print("发送报文到 eth4...")
resp = requests.post("http://192.168.81.105:8000/api/send_packet/", json=data, timeout=30)
print("发送结果:", resp.json())