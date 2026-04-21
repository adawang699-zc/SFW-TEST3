import paramiko
import sys
import io
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SSH_HOST = '192.168.81.105'
SSH_USER = 'zhangc'
SSH_PASSWORD = 'tdhx@2017'
REMOTE_PATH = '/opt/SFW-TEST3'

def execute_ssh_command(ssh, command, timeout=120):
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
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

    # 多次尝试 git fetch，最多 5 次
    for i in range(5):
        print(f'\n尝试 #{i+1}: git fetch...')
        cmd = f'cd {REMOTE_PATH} && git fetch --prune origin'
        exit_status, out, err = execute_ssh_command(ssh, cmd, timeout=180)

        if exit_status == 0:
            print('[OK] Git fetch 成功!')
            break
        else:
            print(f'[FAIL] Git fetch 失败: {err}')
            if i < 4:
                print('等待 10 秒后重试...')
                time.sleep(10)

    if exit_status != 0:
        print('\n[FAIL] 所有尝试失败，请手动检查网络')
        ssh.close()
        sys.exit(1)

    # Git reset
    print('\n执行 git reset --hard origin/main...')
    cmd = f'cd {REMOTE_PATH} && git reset --hard origin/main'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    print(out)

    if exit_status == 0:
        print('[OK] 同步成功!')

        # 重启 Django
        print('\n重启 Django 服务...')
        execute_ssh_command(ssh, 'pkill -f "manage.py runserver"')

        cmd = f'cd {REMOTE_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &'
        execute_ssh_command(ssh, cmd)

        time.sleep(3)

        cmd = f'pgrep -f "manage.py runserver"'
        exit_status, out, err = execute_ssh_command(ssh, cmd)
        if out.strip():
            print(f'[OK] Django 进程运行中: PID {out.strip()}')
        else:
            print('[FAIL] Django 进程未启动!')
    else:
        print(f'[FAIL] 同步失败: {err}')

    ssh.close()

except Exception as e:
    print(f'连接失败: {e}')
    sys.exit(1)