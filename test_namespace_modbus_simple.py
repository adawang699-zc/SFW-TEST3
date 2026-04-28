#!/usr/bin/env python
"""
简化的 Network Namespace Modbus 测试脚本
使用 SSH 执行关键测试步骤
"""

import subprocess
import sys
import time

UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"
PROJECT_PATH = "/opt/SFW-TEST3"


def ssh_cmd(cmd: str, sudo: bool = False) -> str:
    """执行 SSH 命令"""
    if sudo:
        full_cmd = f"echo {UBUNTU_PASSWORD} | sudo -S {cmd}"
    else:
        full_cmd = cmd

    result = subprocess.run(
        [
            "sshpass", "-p", UBUNTU_PASSWORD,
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"{UBUNTU_USER}@{UBUNTU_IP}",
            full_cmd
        ],
        capture_output=True,
        text=True,
        timeout=60
    )
    return result.stdout.strip()


def main():
    print("=" * 60)
    print("Network Namespace Modbus 测试")
    print("=" * 60)

    # Step 1: git pull
    print("\n[1] Ubuntu git pull...")
    out = ssh_cmd(f"cd {PROJECT_PATH} && git pull origin main")
    print(f"  {out[:100]}")

    # Step 2: 停止现有服务
    print("\n[2] 停止现有服务...")
    ssh_cmd("systemctl stop agent-eth1.service", sudo=True)
    ssh_cmd("systemctl stop agent-eth2.service", sudo=True)
    ssh_cmd("systemctl stop network-namespace.service", sudo=True)
    print("  完成")

    # Step 3: 设置 namespace
    print("\n[3] 设置 Network Namespace...")
    out = ssh_cmd(f"{PROJECT_PATH}/scripts/network-namespace-setup.sh setup", sudo=True)
    print(f"  Namespace 已设置")

    # Step 4: 检查 namespace
    print("\n[4] 检查 namespace 状态...")
    out = ssh_cmd("ip netns list", sudo=True)
    print(f"  Namespace: {out}")

    # Step 5: Ping 测试
    print("\n[5] Ping 测试 (eth1 -> eth2)...")
    out = ssh_cmd("ip netns exec ns-eth1 ping -c 2 192.168.12.100", sudo=True)
    print(f"  Ping:\n{out}")

    # Step 6: 启动 Agent in namespace
    print("\n[6] 启动 Agent in namespace...")
    # 使用 cd 在命令前
    ssh_cmd("ip netns exec ns-eth2 bash -c 'cd /opt/SFW-TEST3 && /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.12.100:8888 --preload --timeout 30 agents.full_agent:app &'", sudo=True)
    time.sleep(2)
    ssh_cmd("ip netns exec ns-eth1 bash -c 'cd /opt/SFW-TEST3 && /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.11.100:8888 --preload --timeout 30 agents.full_agent:app &'", sudo=True)
    time.sleep(3)
    print("  Agent 已启动")

    # Step 7: 检查进程
    print("\n[7] 检查 Agent 进程...")
    out1 = ssh_cmd("ip netns pids ns-eth1", sudo=True)
    out2 = ssh_cmd("ip netns pids ns-eth2", sudo=True)
    print(f"  ns-eth1: {out1}")
    print(f"  ns-eth2: {out2}")

    # Step 8: 测试 Agent API
    print("\n[8] 测试 Agent API...")
    out = ssh_cmd("ip netns exec ns-eth2 curl -s http://192.168.12.100:8888/api/health", sudo=True)
    print(f"  eth2 Agent: {out}")
    out = ssh_cmd("ip netns exec ns-eth1 curl -s http://192.168.11.100:8888/api/health", sudo=True)
    print(f"  eth1 Agent: {out}")

    # Step 9: 启动 Modbus Server
    print("\n[9] 启动 Modbus Server in ns-eth2...")
    cmd = "ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"interface\":\"192.168.12.100\",\"port\":502}'"
    out = ssh_cmd(cmd, sudo=True)
    print(f"  Server 启动: {out}")
    time.sleep(2)

    # Step 10: Server 状态
    print("\n[10] Modbus Server 状态...")
    cmd = "ip netns exec ns-eth2 curl -s 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/status?config_id=test'"
    out = ssh_cmd(cmd, sudo=True)
    print(f"  Server 状态: {out}")

    # Step 11: Modbus Client 连接
    print("\n[11] Modbus Client 连接...")
    cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"host\":\"192.168.12.100\",\"port\":502}'"
    out = ssh_cmd(cmd, sudo=True)
    print(f"  Client 连接: {out}")
    time.sleep(1)

    # Step 12: Modbus Client 读取
    print("\n[12] Modbus Client 读取...")
    cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'"
    out = ssh_cmd(cmd, sudo=True)
    print(f"  读取结果: {out}")

    # Step 13: 抓包验证
    print("\n[13] 抓包验证...")
    # 先触发一些 Modbus 流量
    cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'"
    ssh_cmd(cmd, sudo=True)

    # eth1 抓包
    out1 = ssh_cmd("timeout 2 ip netns exec ns-eth1 tcpdump -i eth1 -nn port 502 2>&1 | head -20", sudo=True)
    print(f"  eth1:\n{out1[:500]}")

    # eth2 抓包
    out2 = ssh_cmd("timeout 2 ip netns exec ns-eth2 tcpdump -i eth2 -nn port 502 2>&1 | head -20", sudo=True)
    print(f"  eth2:\n{out2[:500]}")

    # 分析结果
    print("\n" + "=" * 60)
    print("分析结果:")
    if "502" in out1:
        print("  ✓ eth1 上检测到 Modbus 流量")
    else:
        print("  ✗ eth1 上未检测到 Modbus 流量")

    if "502" in out2:
        print("  ✓ eth2 上检测到 Modbus 流量")
    else:
        print("  ✗ eth2 上未检测到 Modbus 流量")

    # 清理
    print("\n清理...")
    ssh_cmd("ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/disconnect' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\"}'", sudo=True)
    ssh_cmd("ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/stop' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\"}'", sudo=True)
    print("  完成")


if __name__ == "__main__":
    main()