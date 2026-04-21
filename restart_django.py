import paramiko
import sys
import io

# 设置 stdout 为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SSH_HOST = '192.168.81.105'
SSH_USER = 'zhangc'
SSH_PASSWORD = 'tdhx@2017'
REMOTE_PATH = '/opt/SFW-TEST3'

def execute_ssh_command(ssh, command):
    """执行 SSH 命令并返回结果"""
    stdin, stdout, stderr = ssh.exec_command(command, timeout=120)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    return exit_status, out, err

try:
    print(f'正在连接 {SSH_HOST}...')

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(SSH_HOST, 22, SSH_USER, SSH_PASSWORD, timeout=30, allow_agent=False, look_for_keys=False)

    print('SSH 连接成功!')

    # Git fetch + reset
    print('执行 git sync...')
    cmd = f'cd {REMOTE_PATH} && git fetch --prune origin && git reset --hard origin/main'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    print(out)
    if err:
        print('Error:', err)

    if exit_status == 0:
        print('[OK] 同步成功!')
    else:
        print(f'[FAIL] 同步失败，退出码: {exit_status}')

    # 重启 Django
    print('重启 Django 服务...')
    # 先杀掉旧进程
    cmd = f'pkill -f "manage.py runserver"'
    execute_ssh_command(ssh, cmd)

    # 启动新进程
    cmd = f'cd {REMOTE_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &'
    exit_status, out, err = execute_ssh_command(ssh, cmd)

    if exit_status == 0:
        print('[OK] Django 重启成功!')
    else:
        print(f'[FAIL] Django 重启失败: {err}')

    # 等待服务启动
    import time
    time.sleep(3)

    # 检查服务状态
    cmd = f'pgrep -f "manage.py runserver"'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    if out.strip():
        print(f'[OK] Django 进程运行中: PID {out.strip()}')
    else:
        print('[FAIL] Django 进程未启动!')

    ssh.close()
    sys.exit(0)

except Exception as e:
    print(f'连接失败: {e}')
    sys.exit(1)