#!/usr/bin/env python
"""
Network Namespace Modbus 测试脚本
在 Ubuntu 上执行完整测试流程
"""

import subprocess
import sys
import paramiko
import time

# ========== Ubuntu 配置 ==========
UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"
UBUNTU_PROJECT_PATH = "/opt/SFW-TEST3"


def ssh_connect() -> paramiko.SSHClient:
    """建立 SSH 连接"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)
    return ssh


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    """执行 SSH 命令"""
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    return exit_status, out, err


def ssh_exec_sudo(ssh: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    """执行 sudo 命令"""
    full_cmd = f"echo {UBUNTU_PASSWORD} | sudo -S {cmd}"
    return ssh_exec(ssh, full_cmd, timeout)


def main():
    print("=" * 60)
    print("Network Namespace Modbus 测试")
    print("=" * 60)

    # 建立 SSH 连接
    print("\n[1] 建立 SSH 连接...")
    try:
        ssh = ssh_connect()
        print("  SSH 连接成功")
    except Exception as e:
        print(f"  SSH 连接失败: {e}")
        sys.exit(1)

    try:
        # Step 2: Git pull
        print("\n[2] Ubuntu git pull...")
        cmd = f"cd {UBUNTU_PROJECT_PATH} && git pull origin main"
        code, out, err = ssh_exec(ssh, cmd)
        print(f"  结果: {out.strip()[:100] if out.strip() else 'Already up to date'}")

        # Step 3: 停止现有服务
        print("\n[3] 停止现有服务...")
        ssh_exec_sudo(ssh, "systemctl stop agent-eth1.service")
        ssh_exec_sudo(ssh, "systemctl stop agent-eth2.service")
        ssh_exec_sudo(ssh, "systemctl stop network-namespace.service")
        print("  服务已停止")

        # Step 4: 设置 Network Namespace
        print("\n[4] 设置 Network Namespace...")
        cmd = f"{UBUNTU_PROJECT_PATH}/scripts/network-namespace-setup.sh setup"
        code, out, err = ssh_exec_sudo(ssh, cmd, timeout=30)
        print(f"  结果:\n{out}")

        # Step 5: 检查 namespace 状态
        print("\n[5] 检查 namespace 状态...")
        cmd = "ip netns list"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  Namespace 列表: {out.strip()}")

        # Step 6: Ping 测试
        print("\n[6] Ping 测试 (eth1 -> eth2)...")
        cmd = "ip netns exec ns-eth1 ping -c 2 192.168.12.100"
        code, out, err = ssh_exec_sudo(ssh, cmd, timeout=10)
        print(f"  Ping 结果:\n{out}")

        # Step 7: 启动 Agent in namespace (手动启动)
        print("\n[7] 启动 Agent in namespace...")
        print("  启动 Agent-eth2 in ns-eth2...")
        cmd = f"ip netns exec ns-eth2 /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.12.100:8888 --preload --timeout 30 agents.full_agent:app &"
        ssh_exec_sudo(ssh, cmd, timeout=5)

        time.sleep(2)

        print("  启动 Agent-eth1 in ns-eth1...")
        cmd = f"ip netns exec ns-eth1 /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.11.100:8888 --preload --timeout 30 agents.full_agent:app &"
        ssh_exec_sudo(ssh, cmd, timeout=5)

        time.sleep(3)

        # Step 8: 检查 Agent 进程
        print("\n[8] 检查 Agent 进程...")
        cmd = f"ip netns pids ns-eth1"
        code, out1, err = ssh_exec_sudo(ssh, cmd)
        cmd = f"ip netns pids ns-eth2"
        code, out2, err = ssh_exec_sudo(ssh, cmd)
        print(f"  ns-eth1 进程: {out1.strip()}")
        print(f"  ns-eth2 进程: {out2.strip()}")

        # Step 9: 测试 Agent API
        print("\n[9] 测试 Agent API...")
        cmd = "ip netns exec ns-eth2 curl -s http://192.168.12.100:8888/api/health"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  eth2 Agent health: {out.strip()}")

        cmd = "ip netns exec ns-eth1 curl -s http://192.168.11.100:8888/api/health"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  eth1 Agent health: {out.strip()}")

        # Step 10: 启动 Modbus Server (in ns-eth2)
        print("\n[10] 启动 Modbus Server in ns-eth2...")
        cmd = "ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{\"config_id\": \"test\", \"interface\": \"192.168.12.100\", \"port\": 502}'"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  Modbus Server 启动结果: {out.strip()}")

        time.sleep(2)

        # Step 11: 查询 Modbus Server 状态
        print("\n[11] 查询 Modbus Server 状态...")
        cmd = "ip netns exec ns-eth2 curl -s 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/status?config_id=test'"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  Modbus Server 状态: {out.strip()}")

        # Step 12: Modbus Client 连接 (from ns-eth1)
        print("\n[12] Modbus Client 连接 (from ns-eth1)...")
        cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{\"config_id\": \"test\", \"host\": \"192.168.12.100\", \"port\": 502}'"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  Modbus Client 连接结果: {out.strip()}")

        time.sleep(1)

        # Step 13: Modbus Client 读取
        print("\n[13] Modbus Client 读取...")
        cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\": \"test\", \"address\": 0, \"count\": 5, \"unit\": 1}'"
        code, out, err = ssh_exec_sudo(ssh, cmd)
        print(f"  Modbus Client 读取结果: {out.strip()}")

        # Step 14: 抓包验证
        print("\n[14] 抓包验证 (tcpdump)...")
        cmd = "ip netns exec ns-eth1 timeout 2 tcpdump -i eth1 -nn port 502 -c 5 2>&1 || true"
        code, out1, err = ssh_exec_sudo(ssh, cmd, timeout=5)
        print(f"  eth1 抓包结果:\n{out1[:500]}")

        cmd = "ip netns exec ns-eth2 timeout 2 tcpdump -i eth2 -nn port 502 -c 5 2>&1 || true"
        code, out2, err = ssh_exec_sudo(ssh, cmd, timeout=5)
        print(f"  eth2 抓包结果:\n{out2[:500]}")

        # Step 15: 清理
        print("\n[15] 清理...")
        # 停止 Modbus
        cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/disconnect' -H 'Content-Type: application/json' -d '{\"config_id\": \"test\"}'"
        ssh_exec_sudo(ssh, cmd)

        cmd = "ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/stop' -H 'Content-Type: application/json' -d '{\"config_id\": \"test\"}'"
        ssh_exec_sudo(ssh, cmd)

        print("\n" + "=" * 60)
        print("测试完成!")
        print("=" * 60)

        # 分析抓包结果
        print("\n[分析] 抓包验证结果:")
        if "port 502" in out1 or "502" in out1:
            print("  ✓ eth1 上有 Modbus 流量 (TCP port 502)")
        else:
            print("  ✗ eth1 上未检测到 Modbus 流量")

        if "port 502" in out2 or "502" in out2:
            print("  ✓ eth2 上有 Modbus 流量 (TCP port 502)")
        else:
            print("  ✗ eth2 上未检测到 Modbus 流量")

    finally:
        ssh.close()


if __name__ == "__main__":
    main()