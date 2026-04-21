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

    # Git stash + pull
    print('执行 git fetch...')
    cmd = f'cd {REMOTE_PATH} && git fetch --prune origin'
    exit_status, out, err = execute_ssh_command(ssh, cmd)
    print('=== Git Fetch 输出 ===')
    print(out)
    if err:
        print('=== Fetch 错误 ===')
        print(err)

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

    if exit_status == 0:
        print('[OK] 同步成功!')
    else:
        print(f'[FAIL] 同步失败，退出码: {exit_status}')

    ssh.close()
    sys.exit(0)

except Exception as e:
    print(f'连接失败: {e}')
    sys.exit(1)
