#!/usr/bin/env python3
"""Test packet send with status check"""
import requests
import json
import time

BASE_URL = "http://192.168.81.105:8000"

def query_status(agent_id):
    resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": agent_id}, timeout=5)
    return resp.json()

def start_send(agent_id, continuous=False):
    data = {
        "agent_id": agent_id,
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
            "continuous": continuous
        }
    }
    resp = requests.post(f"{BASE_URL}/api/send_packet/", json=data, timeout=10)
    return resp.json()

def stop_send(agent_id):
    resp = requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": agent_id}, timeout=5)
    return resp.json()

agent_id = "agent_eth4"

print("1. Query status before send")
status = query_status(agent_id)
print(f"  Status: {status}")

print("\n2. Start sending (count=100, not continuous)")
result = start_send(agent_id, continuous=False)
print(f"  Result: {result}")

print("\n3. Wait 3 seconds")
time.sleep(3)

print("\n4. Query status after send")
status = query_status(agent_id)
print(f"  Status: {json.dumps(status, indent=2)}")

print("\n5. Stop sending")
result = stop_send(agent_id)
print(f"  Result: {result}")

print("\n6. Query status after stop")
time.sleep(0.5)
status = query_status(agent_id)
print(f"  Status: {json.dumps(status, indent=2)}")