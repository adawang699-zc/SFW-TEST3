#!/usr/bin/env python
"""
重启Ubuntu项目脚本
"""
import paramiko
import time

# ========== Ubuntu 配置 ==========
UBUNTU_IP = "192.168.81.140"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"
UBUNTU_PROJECT_PATH = "/opt/SFW-TEST3"


def ssh_connect():
    """建立 SSH 连接"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)
    return ssh


def ssh_exec(ssh, cmd, timeout=60, wait_for_output=True):
    """执行 SSH 命令"""
    print(f"执行: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)

    if wait_for_output:
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode('utf-8', errors='ignore')
        err = stderr.read().decode('utf-8', errors='ignore')
        if out:
            print(f"输出:\n{out}")
        if err:
            print(f"错误:\n{err}")
        return exit_status, out, err
    else:
        # 不等待输出，直接返回
        time.sleep(1)
        return 0, "", ""


def main():
    print("=" * 50)
    print("重启Ubuntu项目")
    print("=" * 50)

    print("\n[连接] 建立 SSH 连接...")
    try:
        ssh = ssh_connect()
        print("  SSH 连接成功")
    except Exception as e:
        print(f"  SSH 连接失败: {e}")
        return

    try:
        print("\n[步骤1] 停止 Django...")
        ssh_exec(ssh, "pkill -f 'manage.py runserver' || true")
        time.sleep(2)

        print("\n[步骤2] 启动 Django...")
        cmd = f"cd {UBUNTU_PROJECT_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &"
        ssh_exec(ssh, cmd, wait_for_output=False)
        time.sleep(5)

        print("\n[步骤3] 检查 Django...")
        code, out, err = ssh_exec(ssh, "pgrep -f 'manage.py runserver'")
        if out.strip():
            print(f"  Django 运行中 (PID: {out.strip()})")
        else:
            print("  Django 未运行，检查日志...")
            ssh_exec(ssh, f"cd {UBUNTU_PROJECT_PATH} && tail -50 logs/django.log || true")

        print("\n[步骤4] 重启 Agent 服务...")
        for agent in ['agent-eth1', 'agent-eth2', 'agent-eth3', 'agent-eth4']:
            print(f"\n  重启 {agent}...")
            cmd = f"echo {UBUNTU_PASSWORD} | sudo -S systemctl restart {agent}.service"
            ssh_exec(ssh, cmd, timeout=30)
            time.sleep(2)

            print(f"  检查 {agent} 状态...")
            cmd = f"echo {UBUNTU_PASSWORD} | sudo -S systemctl status {agent}.service --no-pager"
            code, out, err = ssh_exec(ssh, cmd, timeout=30)
            if "active (running)" in out:
                print(f"  {agent} 运行正常")
            else:
                print(f"  {agent} 状态异常")

        print("\n" + "=" * 50)
        print("重启完成！")
        print("=" * 50)

    finally:
        ssh.close()


if __name__ == "__main__":
    main()
