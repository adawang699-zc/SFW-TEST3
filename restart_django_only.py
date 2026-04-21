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

    # 杀掉旧进程
    print('停止 Django...')
    execute_ssh_command(ssh, 'pkill -f "manage.py runserver"')
    time.sleep(2)

    # 启动新进程
    print('启动 Django...')
    cmd = f'cd {REMOTE_PATH} && nohup sfw/bin/python manage.py runserver 0.0.0.0:8000 > logs/django.log 2>&1 &'
    exit_status, out, err = execute_ssh_command(ssh, cmd)

    time.sleep(3)

    # 检查进程
    cmd = f'pgrep -f "manage.py runserver"'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    if out.strip():
        print(f'[OK] Django 已启动: PID {out.strip()}')

        # 检查日志
        cmd = f'tail -20 {REMOTE_PATH}/logs/django.log'
        exit_status, out, err = execute_ssh_command(ssh, cmd)
        print('\nDjango 启动日志:')
        print(out)
    else:
        print('[FAIL] Django 未启动!')

    ssh.close()

except Exception as e:
    print(f'连接失败: {e}')
    sys.exit(1)