#!/usr/bin/env python
"""
远程同步脚本 - 同步代码到 Ubuntu 并重启服务
使用方法: python sync_to_ubuntu.py

注意事项:
1. 代码同步通过 git 完成（本地 push -> Ubuntu pull）
2. 同步完成后重启 Django 和 Agent 服务
3. 此脚本不应删除，每次同步都调用此脚本
"""

import subprocess
import sys
import paramiko
import time

# ========== Ubuntu 配置 ==========
UBUNTU_IP = "192.168.81.140"
UBUNTU_USER = "zhangc"
UBUNTU_PASSWORD = "tdhx@2017"
UBUNTU_PROJECT_PATH = "/opt/SFW-TEST3"

# ========== 本地配置 ==========
LOCAL_PROJECT_PATH = r"D:\自动化测试\SFW_CONFIG\ubuntu_deploy"


def run_local_command(cmd: str, cwd: str = LOCAL_PROJECT_PATH) -> tuple[int, str, str]:
    """执行本地命令"""
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    return result.returncode, result.stdout, result.stderr


def ssh_connect() -> paramiko.SSHClient:
    """建立 SSH 连接"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(UBUNTU_IP, 22, UBUNTU_USER, UBUNTU_PASSWORD, timeout=30)
    return ssh


def ssh_exec(ssh: paramiko.SSHClient, cmd: str, timeout: int = 120, background: bool = False) -> tuple[int, str, str]:
    """执行 SSH 命令

    Args:
        background: 如果为 True，不等待命令完成（用于 nohup 后台命令）
    """
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)

    if background:
        # 后台命令不等待退出状态，立即返回
        time.sleep(1)  # 给命令启动时间
        out = ""
        err = ""
        return 0, out, err

    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    return exit_status, out, err


def step1_local_git_push() -> bool:
    """步骤1: 本地 git push"""
    print("\n[步骤1] 本地 git push...")

    # 检查是否有未提交的更改
    code, out, err = run_local_command("git status --porcelain")
    if out.strip():
        print("  发现未提交的更改，先提交...")
        # 添加所有更改
        run_local_command("git add -A")
        # 提交（使用时间戳作为消息）
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        run_local_command(f'git commit -m "auto sync: {timestamp}"')

    # 推送到远程
    code, out, err = run_local_command("git push origin main")
    if code != 0:
        print(f"  git push 失败: {err}")
        return False
    print("  git push 成功")
    return True


def step2_ubuntu_git_pull(ssh: paramiko.SSHClient) -> bool:
    """步骤2: Ubuntu git pull"""
    print("\n[步骤2] Ubuntu git pull...")

    # 先检查并切换到 SSH remote（避免 HTTPS 连接问题）
    cmd = f"cd {UBUNTU_PROJECT_PATH} && git remote get-url origin"
    code, out, err = ssh_exec(ssh, cmd)
    current_url = out.strip()

    # 如果是 HTTPS，切换到 SSH
    if current_url.startswith("https://github.com"):
        ssh_url = "git@github.com:adawang699-zc/SFW-TEST3.git"
        print(f"  切换 remote: {current_url} -> {ssh_url}")
        cmd = f"cd {UBUNTU_PROJECT_PATH} && git remote set-url origin {ssh_url}"
        ssh_exec(ssh, cmd)

    # 执行 git pull
    cmd = f"cd {UBUNTU_PROJECT_PATH} && git pull origin main"
    code, out, err = ssh_exec(ssh, cmd)

    if code != 0:
        print(f"  git pull 失败: {err}")
        return False
    print(f"  git pull 成功: {out.strip()}")
    return True


def step3_restart_django(ssh: paramiko.SSHClient) -> bool:
    """步骤3: 重启 Django"""
    print("\n[步骤3] 重启 Django...")

    # 停止现有进程
    cmd = "pkill -f 'manage.py runserver'"
    ssh_exec(ssh, cmd, timeout=10)
    time.sleep(2)

    # 启动 Django (后台运行)
    cmd = f"cd {UBUNTU_PROJECT_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &"
    code, out, err = ssh_exec(ssh, cmd, background=True)

    if code != 0:
        print(f"  启动 Django 失败: {err}")
        return False

    # 等待启动
    time.sleep(3)

    # 验证是否启动
    cmd = "pgrep -f 'manage.py runserver'"
    code, out, err = ssh_exec(ssh, cmd)
    if out.strip():
        print(f"  Django 启动成功 (PID: {out.strip()})")
        return True
    else:
        print("  Django 启动失败，未找到进程")
        return False


def step4_restart_agents(ssh: paramiko.SSHClient) -> bool:
    """步骤4: 重启 Agent 服务"""
    print("\n[步骤4] 重启 Agent 服务...")

    # 使用 sudo 重启 agent-eth1 服务
    cmd = f"echo {UBUNTU_PASSWORD} | sudo -S systemctl restart agent-eth1.service"
    code, out, err = ssh_exec(ssh, cmd)

    if code != 0 and "not found" not in err.lower():
        print(f"  重启 agent-eth1 失败: {err}")

    # 等待服务启动
    time.sleep(2)

    # 检查服务状态
    cmd = f"echo {UBUNTU_PASSWORD} | sudo -S systemctl status agent-eth1.service --no-pager"
    code, out, err = ssh_exec(ssh, cmd)

    if "active (running)" in out:
        print("  agent-eth1 服务运行正常")
        return True
    else:
        print(f"  agent-eth1 服务状态异常: {out[:200]}")
        return False


def main():
    """主函数"""
    print("=" * 50)
    print("远程同步脚本 - 同步代码到 Ubuntu")
    print("=" * 50)

    # 步骤1: 本地 push
    if not step1_local_git_push():
        print("\n同步失败: 本地 git push 失败")
        sys.exit(1)

    # 建立 SSH 连接
    print("\n[连接] 建立 SSH 连接...")
    try:
        ssh = ssh_connect()
        print("  SSH 连接成功")
    except Exception as e:
        print(f"  SSH 连接失败: {e}")
        sys.exit(1)

    try:
        # 步骤2: Ubuntu pull
        if not step2_ubuntu_git_pull(ssh):
            print("\n同步失败: Ubuntu git pull 失败")
            sys.exit(1)

        # 步骤3: 重启 Django
        step3_restart_django(ssh)

        # 步骤4: 重启 Agent
        step4_restart_agents(ssh)

        print("\n" + "=" * 50)
        print("同步完成!")
        print("=" * 50)

    finally:
        ssh.close()


if __name__ == "__main__":
    main()