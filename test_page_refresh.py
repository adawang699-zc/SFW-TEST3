#!/usr/bin/env python3
"""Test page refresh state persistence"""
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
            "count": 1000,
            "interval": 0,
            "continuous": True  # Continuous mode
        }
    }
    resp = requests.post(f"{BASE_URL}/api/send_packet/", json=data, timeout=10)
    return resp.json()

def stop_send(agent_id):
    """Stop sending"""
    resp = requests.post(f"{BASE_URL}/api/stop_send/", json={"agent_id": agent_id}, timeout=5)
    return resp.json()

def test_page_refresh_state():
    """Test state persistence after page refresh"""
    agent_id = "agent_eth4"

    print("=" * 60)
    print("Test 3: Page Refresh State Persistence")
    print("=" * 60)

    # 1. Start continuous sending
    print("\n[Step 1] Start continuous sending")
    result = start_send(agent_id)
    print(f"  Send result: success={result.get('success')}")
    assert result.get('success'), "Send should succeed"
    print("  [PASS] Continuous sending started")

    # 2. Wait 2 seconds and check status (simulate user viewing page)
    print("\n[Step 2] Wait 2 seconds, check sending status")
    time.sleep(2)
    status = query_status(agent_id)
    stats = status.get('statistics', {})
    total_sent = stats.get('total_sent', 0)
    rate = stats.get('rate', 0)
    print(f"  eth4 status: total_sent={total_sent}, rate={rate}")
    is_sending_before = total_sent > 0
    print(f"  Is sending: {is_sending_before}")
    assert is_sending_before, "Should be sending"
    print("  [PASS] Sending detected")

    # 3. Simulate page refresh - query status again
    print("\n[Step 3] Simulate page refresh - query status again")
    time.sleep(0.5)
    status = query_status(agent_id)
    stats = status.get('statistics', {})
    total_sent = stats.get('total_sent', 0)
    rate = stats.get('rate', 0)
    print(f"  eth4 status: total_sent={total_sent}, rate={rate}")
    is_sending_after = total_sent > 0 and rate > 0
    print(f"  Is sending (after refresh): {is_sending_after}")
    assert is_sending_after, "Should still show sending after refresh"
    print("  [PASS] State persisted after refresh")

    # 4. Stop sending
    print("\n[Step 4] Stop sending")
    result = stop_send(agent_id)
    print(f"  Stop result: success={result.get('success')}")
    assert result.get('success'), "Stop should succeed"
    print("  [PASS] Stop succeeded")

    # 5. Verify stopped state after refresh
    print("\n[Step 5] Verify stopped state after refresh")
    time.sleep(0.5)
    status = query_status(agent_id)
    stats = status.get('statistics', {})
    total_sent = stats.get('total_sent', 0)
    rate = stats.get('rate', 0)
    print(f"  eth4 status: total_sent={total_sent}, rate={rate}")
    assert total_sent == 0, "Should be stopped"
    assert rate == 0, "Rate should be 0"
    print("  [PASS] Stopped state persisted")

    print("\n" + "=" * 60)
    print("Test 3: Page Refresh State Persistence - ALL PASSED")
    print("=" * 60)

if __name__ == "__main__":
    test_page_refresh_state()