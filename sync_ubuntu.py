import paramiko
import sys
import io
import time

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

    # 多次尝试 git fetch
    success = False
    for attempt in range(5):
        print(f'\n尝试 #{attempt+1}: git fetch...')
        cmd = f'cd {REMOTE_PATH} && git fetch --prune origin'
        exit_status, out, err = execute_ssh_command(ssh, cmd)

        if exit_status == 0:
            print('[OK] Git fetch 成功!')
            success = True
            break
        else:
            print(f'[FAIL] Git fetch 失败: {err}')
            if attempt < 4:
                print('等待 15 秒后重试...')
                time.sleep(15)

    if not success:
        print('\n[WARN] Git fetch 失败，尝试使用 reset...')
        # 如果 fetch 失败，尝试直接 reset（可能会有旧代码）

    print('执行 git stash...')
    cmd = f'cd {REMOTE_PATH} && git stash'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    print('=== Git Stash 输出 ===')
    print(out)
    if err:
        print('=== Stash 错误 ===')
        print(err)

    print('执行 git reset --hard...')
    cmd = f'cd {REMOTE_PATH} && git reset --hard origin/main'
    exit_status, out, err = execute_ssh_command(ssh, cmd)

    print('=== Git Reset 输出 ===')
    print(out)
    if err:
        print('=== 错误 ===')
        print(err)

    # 显示当前 commit
    cmd = f'cd {REMOTE_PATH} && git log -1 --oneline'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    print('=== 当前 Commit ===')
    print(out)

    # 重启 Django
    print('\n重启 Django 服务...')
    cmd = f'pkill -f "manage.py runserver"'
    execute_ssh_command(ssh, cmd)

    time.sleep(2)

    cmd = f'cd {REMOTE_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &'
    exit_status, out, err = execute_ssh_command(ssh, cmd)

    time.sleep(3)

    # 检查进程
    cmd = f'pgrep -f "manage.py runserver"'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    if out.strip():
        print(f'[OK] Django 已启动: PID {out.strip()}')
    else:
        print('[FAIL] Django 未启动!')

    ssh.close()
    sys.exit(0)

except Exception as e:
    print(f'连接失败: {e}')
    sys.exit(1)
