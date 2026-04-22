#!/usr/bin/env python3
"""Test start/stop status monitoring"""
import requests
import json
import time

BASE_URL = "http://192.168.81.105:8000"

def query_status(agent_id):
    """Query Agent status"""
    resp = requests.get(f"{BASE_URL}/api/agents/status/", params={"agent_id": agent_id}, timeout=5)
    return resp.json()

def start_send(agent_id):
    """Start sending packets"""
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
            "count": 200,
            "interval": 0,
            "continuous": False
        }
    }
    resp = requests.post(f"{BASE_URL}/api/send_packet/", json=data, timeout=10)
    return resp.json()

def stop_send(agent_id):
    """Stop sending"""
    resp = requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": agent_id}, timeout=5)
    return resp.json()

def test_start_stop_monitoring():
    """Test start/stop status monitoring"""
    agent_id = "agent_eth4"

    print("=" * 60)
    print("Test 2: Start/Stop Status Monitoring")
    print("=" * 60)

    # 1. Initial status check
    print("\n[Step 1] Check initial status")
    status = query_status(agent_id)
    print(f"  eth4 status: total_sent={status['statistics']['total_sent']}, rate={status['statistics']['rate']}")
    assert status['statistics']['total_sent'] == 0, "Initial state should be 0"
    print("  [PASS] Initial state correct")

    # 2. Start sending
    print("\n[Step 2] Start sending packets")
    result = start_send(agent_id)
    print(f"  Send result: success={result.get('success')}")
    assert result.get('success'), "Send should succeed"
    print("  [PASS] Send started successfully")

    # 3. Wait 1 second then check sending status
    print("\n[Step 3] Wait 1 second then check status")
    time.sleep(1)
    status = query_status(agent_id)
    print(f"  eth4 status: total_sent={status['statistics']['total_sent']}, rate={status['statistics']['rate']}")
    assert status['statistics']['total_sent'] > 0, "Should have sent packets"
    assert status['statistics']['rate'] > 0, "Send rate should be > 0"
    print("  [PASS] Sending status shows correctly")

    # 4. Stop sending
    print("\n[Step 4] Stop sending")
    result = stop_send(agent_id)
    print(f"  Stop result: success={result.get('success')}")
    assert result.get('success'), "Stop should succeed"
    print("  [PASS] Stop succeeded")

    # 5. Wait 0.5 second then check stopped status
    print("\n[Step 5] Wait 0.5 second then check status")
    time.sleep(0.5)
    status = query_status(agent_id)
    print(f"  eth4 status: total_sent={status['statistics']['total_sent']}, rate={status['statistics']['rate']}")
    assert status['statistics']['total_sent'] == 0, "Should clear count after stop"
    assert status['statistics']['rate'] == 0, "Rate should be 0 after stop"
    print("  [PASS] Stopped status updated correctly")

    # 6. Check other Agent status (should remain unchanged)
    print("\n[Step 6] Check eth1 status (should remain not sending)")
    status_eth1 = query_status("agent_eth1")
    print(f"  eth1 status: total_sent={status_eth1['statistics']['total_sent']}, rate={status_eth1['statistics']['rate']}")
    assert status_eth1['statistics']['total_sent'] == 0, "eth1 should remain not sending"
    print("  [PASS] eth1 status correct")

    print("\n" + "=" * 60)
    print("Test 2: Start/Stop Status Monitoring - ALL PASSED")
    print("=" * 60)

if __name__ == "__main__":
    test_start_stop_monitoring()