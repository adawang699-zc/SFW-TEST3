#!/usr/bin/env python
"""
Network Namespace Modbus 测试脚本 - 简化版本
"""

import paramiko
import sys
import time

# Ubuntu 配置
UBUNTU_IP = "192.168.81.105"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"
PROJECT_PATH = "/opt/SFW-TEST3"


def ssh_connect() -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)
    return ssh


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, sudo: bool = False, timeout: int = 30) -> tuple:
    if sudo:
        cmd = f"echo {UBUNTU_PASSWORD} | sudo -S {cmd}"
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    return exit_status, out, err


def main():
    print("=" * 60)
    print("Network Namespace Modbus 测试")
    print("=" * 60)

    print("\n[1] 建立 SSH 连接...")
    ssh = ssh_connect()
    print("  连接成功")

    try:
        # Step 2: git pull
        print("\n[2] Git pull...")
        code, out, err = ssh_exec(ssh, f"cd {PROJECT_PATH} && git pull origin main")
        print(f"  {out.strip()[:80]}")

        # Step 3: 停止服务
        print("\n[3] 停止现有服务...")
        ssh_exec(ssh, "systemctl stop agent-eth1.service", sudo=True, timeout=10)
        ssh_exec(ssh, "systemctl stop agent-eth2.service", sudo=True, timeout=10)
        ssh_exec(ssh, "systemctl stop network-namespace.service", sudo=True, timeout=10)
        print("  完成")

        # Step 4: 设置 namespace (关键步骤，可能需要较长时间)
        print("\n[4] 设置 Network Namespace...")
        code, out, err = ssh_exec(ssh, f"{PROJECT_PATH}/scripts/network-namespace-setup.sh setup", sudo=True, timeout=60)
        print(f"  Namespace 已创建")
        if err:
            print(f"  错误: {err[:200]}")

        # Step 5: 检查 namespace
        print("\n[5] Namespace 状态...")
        code, out, err = ssh_exec(ssh, "ip netns list", sudo=True)
        print(f"  {out.strip()}")

        # Step 6: Ping 测试
        print("\n[6] Ping 测试 (eth1 -> eth2)...")
        code, out, err = ssh_exec(ssh, "ip netns exec ns-eth1 ping -c 2 192.168.12.100", sudo=True, timeout=15)
        print(f"  {out.strip()}")

        if "time=" in out:
            print("  [OK] Ping 成功")
        else:
            print("  [FAIL] Ping 失败")

        # Step 7: 启动 Agent (使用 nohup 完全后台运行)
        print("\n[7] 启动 Agent in namespace...")
        # 先杀掉之前的进程
        ssh_exec(ssh, "ip netns exec ns-eth2 pkill -f gunicorn", sudo=True, timeout=5)
        ssh_exec(ssh, "ip netns exec ns-eth1 pkill -f gunicorn", sudo=True, timeout=5)

        # eth2 Agent - 使用 nohup 并重定向所有输出
        cmd = "ip netns exec ns-eth2 nohup bash -c 'cd /opt/SFW-TEST3 && /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.12.100:8888 --preload --timeout 30 agents.full_agent:app' > /opt/SFW-TEST3/logs/agent_eth2_ns.log 2>&1 &"
        # 用分号分隔命令，让 sudo 执行多个命令
        ssh_exec(ssh, f"echo {UBUNTU_PASSWORD} | sudo -S sh -c '{cmd}'", timeout=5)

        time.sleep(3)

        # eth1 Agent
        cmd = "ip netns exec ns-eth1 nohup bash -c 'cd /opt/SFW-TEST3 && /opt/SFW-TEST3/sfw/bin/python -m gunicorn -w 1 -b 192.168.11.100:8888 --preload --timeout 30 agents.full_agent:app' > /opt/SFW-TEST3/logs/agent_eth1_ns.log 2>&1 &"
        ssh_exec(ssh, f"echo {UBUNTU_PASSWORD} | sudo -S sh -c '{cmd}'", timeout=5)
        time.sleep(3)
        print("  Agent 启动命令已执行")

        # Step 8: 检查进程
        print("\n[8] Agent 进程...")
        code, out1, err = ssh_exec(ssh, "ip netns pids ns-eth1", sudo=True)
        code, out2, err = ssh_exec(ssh, "ip netns pids ns-eth2", sudo=True)
        print(f"  ns-eth1: {out1.strip()}")
        print(f"  ns-eth2: {out2.strip()}")

        # Step 9: 测试 Agent health
        print("\n[9] Agent health...")
        code, out, err = ssh_exec(ssh, "ip netns exec ns-eth2 curl -s http://192.168.12.100:8888/api/health", sudo=True)
        print(f"  eth2: {out.strip()}")

        code, out, err = ssh_exec(ssh, "ip netns exec ns-eth1 curl -s http://192.168.11.100:8888/api/health", sudo=True)
        print(f"  eth1: {out.strip()}")

        # Step 10: 启动 Modbus Server
        print("\n[10] 启动 Modbus Server...")
        cmd = "ip netns exec ns-eth2 curl -s -X POST 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/start' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"interface\":\"192.168.12.100\",\"port\":502}'"
        code, out, err = ssh_exec(ssh, cmd, sudo=True)
        print(f"  {out.strip()}")
        time.sleep(2)

        # Step 11: Server 状态
        print("\n[11] Server 状态...")
        cmd = "ip netns exec ns-eth2 curl -s 'http://192.168.12.100:8888/api/industrial_protocol/modbus_server/status?config_id=test'"
        code, out, err = ssh_exec(ssh, cmd, sudo=True)
        print(f"  {out.strip()}")

        # Step 12: Client 连接
        print("\n[12] Client 连接...")
        cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/connect' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"host\":\"192.168.12.100\",\"port\":502}'"
        code, out, err = ssh_exec(ssh, cmd, sudo=True)
        print(f"  {out.strip()}")
        time.sleep(1)

        # Step 13: Client 读取
        print("\n[13] Client 读取...")
        cmd = "ip netns exec ns-eth1 curl -s -X POST 'http://192.168.11.100:8888/api/industrial_protocol/modbus_client/read' -H 'Content-Type: application/json' -d '{\"config_id\":\"test\",\"address\":0,\"count\":5,\"unit\":1}'"
        code, out, err = ssh_exec(ssh, cmd, sudo=True)
        print(f"  {out.strip()}")

        # 分析结果
        print("\n" + "=" * 60)
        print("验证结果:")

        if "success" in out or "registers" in out:
            print("  [OK] Modbus 读取成功")
        else:
            print("  [FAIL] Modbus 读取失败")

        print("\n提示: 要验证流量是否走物理网口，请在 Ubuntu 上手动执行:")
        print("  # eth1 抓包")
        print("  sudo ip netns exec ns-eth1 tcpdump -i eth1 -nn port 502")
        print("  # eth2 抓包")
        print("  sudo ip netns exec ns-eth2 tcpdump -i eth2 -nn port 502")

    finally:
        ssh.close()


if __name__ == "__main__":
    main()