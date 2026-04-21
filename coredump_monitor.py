#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Coredump 文件监测模块（独立模块，无外部依赖）

功能：
- SSH 连接设备获取 coredump 文件列表
- 检测新增 coredump 文件
- 发送告警邮件（可选）

使用方法：
    from coredump_monitor import CoredumpMonitor

    monitor = CoredumpMonitor(
        host='192.168.1.10',
        user='admin',
        password='admin_password',
        backend_password='root_password',  # 可选，用于进入后台
        coredump_dir='/data/coredump'
    )

    # 获取当前 coredump 文件列表
    files = monitor.get_coredump_files()

    # 检测新增文件（首次调用返回空，后续对比）
    new_files = monitor.check_new_files()

    # 发送告警邮件
    if new_files:
        monitor.send_alert_email(
            smtp_server='smtp.example.com',
            smtp_port=465,
            sender_email='alert@example.com',
            sender_password='password',
            recipients=['user@example.com'],
            device_name='设备名称'
        )

作者: Claude Code
日期: 2026-04-20
"""

import paramiko
import smtplib
import logging
import time
import socket
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, List, Any, Set, Union


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('coredump_monitor')


class CoredumpMonitor:
    """Coredump 文件监测器"""

    def __init__(
        self,
        host: str,
        user: str = 'admin',
        password: str = '',
        backend_password: Optional[str] = None,
        coredump_dir: str = '/data/coredump',
        port: int = 22,
        timeout: int = 10,
        use_backend: bool = True
    ):
        """
        初始化 Coredump 监测器

        Args:
            host: 设备 IP 地址
            user: SSH 用户名
            password: SSH 密码
            backend_password: 后台/root 密码（用于进入后台执行命令）
            coredump_dir: coredump 文件目录
            port: SSH 端口
            timeout: SSH 超时时间
            use_backend: 是否使用后台模式（需要 backend_password）
        """
        self.host = host
        self.user = user
        self.password = password
        self.backend_password = backend_password
        self.coredump_dir = coredump_dir
        self.port = port
        self.timeout = timeout
        self.use_backend = use_backend

        # 存储上次检测到的文件列表（用于对比新增文件）
        self._last_files: Set[str] = set()

        logger.info(f"CoredumpMonitor 初始化: {host}:{port}, 目录: {coredump_dir}")

    def _create_ssh_connection(self) -> Optional[paramiko.SSHClient]:
        """创建 SSH 连接"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                self.host,
                self.port,
                self.user,
                self.password,
                timeout=self.timeout
            )
            return ssh
        except paramiko.AuthenticationException as e:
            logger.error(f"SSH 认证失败: {self.host}:{self.port}, 错误: {e}")
            return None
        except paramiko.SSHException as e:
            logger.error(f"SSH 异常: {self.host}:{self.port}, 错误: {e}")
            return None
        except socket.timeout:
            logger.error(f"SSH 连接超时: {self.host}:{self.port}")
            return None
        except Exception as e:
            logger.error(f"SSH 连接失败: {self.host}:{self.port}, 错误: {e}")
            return None

    def _execute_command_simple(self, cmd: str) -> Optional[str]:
        """简单模式执行命令（不进入后台）"""
        ssh = self._create_ssh_connection()
        if not ssh:
            return None

        try:
            stdin, stdout, stderr = ssh.exec_command(cmd, timeout=self.timeout)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')

            ssh.close()

            if error and not output:
                logger.error(f"命令执行出错: {error}")
                return None

            return output.strip() if output else None

        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            ssh.close()
            return None

    def _execute_command_backend(self, cmd: str) -> Optional[str]:
        """后台模式执行命令（进入 root 权限）"""
        if not self.backend_password:
            logger.error("后台密码未配置")
            return None

        ssh = self._create_ssh_connection()
        if not ssh:
            return None

        try:
            chan = ssh.invoke_shell()
            chan.settimeout(30)

            # 清空初始输出
            time.sleep(0.1)
            if chan.recv_ready():
                chan.recv(4096)

            # 输入 enter 进入后台
            chan.send('enter\n')
            time.sleep(0.3)
            if chan.recv_ready():
                chan.recv(4096)

            # 输入密码获取 root 权限
            chan.send(self.backend_password + '\n')
            time.sleep(0.3)
            if chan.recv_ready():
                chan.recv(4096)

            # 发送要执行的命令
            chan.send(cmd + '\n')
            time.sleep(0.5)

            # 读取输出
            output = ''
            max_wait = 15
            start_time = time.time()

            while time.time() - start_time < max_wait:
                if chan.recv_ready():
                    data = chan.recv(4096).decode('utf-8', errors='ignore')
                    output += data
                    # 检查是否完成
                    if output.count('\n') > 1:
                        if any(p in output[-20:] for p in ['# ', '$ ', '\n#', '\n$']):
                            time.sleep(0.2)
                            if chan.recv_ready():
                                output += chan.recv(4096).decode('utf-8', errors='ignore')
                            break
                else:
                    time.sleep(0.1)

            ssh.close()

            # 清理 ANSI 转义序列
            output = re.sub(r'\x1b\[[0-9;]*m', '', output)

            # 清理输出
            lines = output.split('\n')
            cleaned_lines = []
            skip_until_command = True

            for line in lines:
                line_stripped = line.strip()

                if skip_until_command:
                    if 'enter' in line_stripped.lower() or 'Password:' in line_stripped:
                        continue
                    if cmd.strip() in line or (len(cmd) > 20 and cmd[:20] in line):
                        skip_until_command = False
                        continue

                if line_stripped:
                    if line_stripped in ['#', '$', '>']:
                        continue
                    if 'Command incomplete' in line_stripped:
                        continue
                    cleaned_lines.append(line)

            result = '\n'.join(cleaned_lines).strip()
            return result if result else None

        except Exception as e:
            logger.error(f"后台命令执行失败: {e}")
            ssh.close()
            return None

    def _execute_command(self, cmd: str) -> Optional[str]:
        """执行命令（自动选择模式）"""
        if self.use_backend and self.backend_password:
            return self._execute_command_backend(cmd)
        else:
            return self._execute_command_simple(cmd)

    def get_coredump_files(self) -> List[Dict[str, Any]]:
        """
        获取 coredump 文件列表

        Returns:
            文件列表 [{'name': 文件名, 'size': 大小, 'time': 时间}]
        """
        files = []

        try:
            # 获取目录中所有文件
            cmd = f"ls -la {self.coredump_dir} 2>/dev/null | grep -v '^d' | grep -v '^total' | sed 's/\\x1b\\[[0-9;]*m//g'"
            result = self._execute_command(cmd)

            if result:
                lines = result.strip().split('\n')
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 9:
                        try:
                            name = parts[-1]
                            size = parts[4]
                            time_str = f"{parts[5]} {parts[6]} {parts[7]}"

                            files.append({
                                'name': name,
                                'size': size,
                                'time': time_str
                            })
                        except IndexError:
                            continue

            logger.info(f"获取 coredump 文件: {len(files)} 个")

        except Exception as e:
            logger.error(f"获取 coredump 文件失败: {e}")

        return files

    def check_new_files(self) -> List[Dict[str, Any]]:
        """
        检测新增 coredump 文件

        Returns:
            新增文件列表 [{'name': 文件名, 'size': 大小, 'time': 时间}]
        """
        current_files = self.get_coredump_files()
        current_file_names = {f['name'] for f in current_files}

        # 对比上次检测结果
        new_file_names = current_file_names - self._last_files

        # 更新上次文件列表
        self._last_files = current_file_names

        # 返回新增文件详情
        new_files = [f for f in current_files if f['name'] in new_file_names]

        if new_files:
            logger.info(f"检测到 {len(new_files)} 个新增 coredump 文件: {new_file_names}")
        else:
            logger.info(f"未检测到新增 coredump 文件")

        return new_files

    def reset_last_files(self) -> None:
        """重置上次文件列表（用于首次初始化，避免误报）"""
        self._last_files = set()

    def init_last_files(self) -> None:
        """初始化上次文件列表（获取当前文件，后续只检测新增）"""
        current_files = self.get_coredump_files()
        self._last_files = {f['name'] for f in current_files}
        logger.info(f"初始化文件列表: {len(self._last_files)} 个现有文件")

    def get_file_count(self) -> int:
        """获取当前 coredump 文件数量"""
        files = self.get_coredump_files()
        return len(files)

    def test_connection(self) -> Dict[str, Any]:
        """
        测试 SSH 连接

        Returns:
            {'success': 是否成功, 'message': 消息}
        """
        ssh = self._create_ssh_connection()
        if ssh:
            ssh.close()
            return {'success': True, 'message': 'SSH 连接成功'}
        else:
            return {'success': False, 'message': 'SSH 连接失败'}

    def format_alert_content(
        self,
        device_name: str,
        new_files: List[Dict[str, Any]],
        all_files: List[Dict[str, Any]]
    ) -> str:
        """
        格式化告警邮件内容

        Args:
            device_name: 设备名称
            new_files: 新增文件列表
            all_files: 所有文件列表

        Returns:
            邮件正文内容
        """
        content = f"""
设备 Coredump 告警通知

设备名称: {device_name}
设备 IP: {self.host}
Coredump 目录: {self.coredump_dir}

新增文件数量: {len(new_files)}
当前文件总数: {len(all_files)}

新增文件列表:
"""
        for f in new_files:
            content += f"  - {f['name']} (大小: {f['size']}, 时间: {f['time']})\n"

        content += f"""
所有文件列表:
"""
        for f in all_files:
            content += f"  - {f['name']} (大小: {f['size']}, 时间: {f['time']})\n"

        content += f"""
请尽快检查设备状态，分析 coredump 文件原因。

---
此邮件由 Coredump 监测模块自动发送
"""

        return content

    def send_alert_email(
        self,
        smtp_server: str,
        smtp_port: int,
        sender_email: str,
        sender_password: str,
        recipients: List[str],
        device_name: str = '未知设备',
        use_ssl: bool = True,
        use_tls: bool = False,
        subject_prefix: str = '[Coredump告警]'
    ) -> bool:
        """
        发送告警邮件

        Args:
            smtp_server: SMTP 服务器地址
            smtp_port: SMTP 端口
            sender_email: 发件人邮箱
            sender_password: 发件人密码
            recipients: 收件人列表
            device_name: 设备名称
            use_ssl: 使用 SSL
            use_tls: 使用 TLS
            subject_prefix: 邮件主题前缀

        Returns:
            是否发送成功
        """
        try:
            # 获取文件信息
            new_files = self.check_new_files()
            all_files = self.get_coredump_files()

            if not new_files:
                logger.info("无新增文件，不发送邮件")
                return False

            # 构建邮件
            msg = MIMEMultipart()
            msg['From'] = sender_email
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = f"{subject_prefix} {device_name} ({self.host}) - 检测到新的Coredump文件"

            # 邮件正文
            content = self.format_alert_content(device_name, new_files, all_files)
            msg.attach(MIMEText(content, 'plain', 'utf-8'))

            # 发送邮件
            if use_ssl:
                smtp = smtplib.SMTP_SSL(smtp_server, smtp_port)
            else:
                smtp = smtplib.SMTP(smtp_server, smtp_port)
                if use_tls:
                    smtp.starttls()

            smtp.login(sender_email, sender_password)
            smtp.sendmail(sender_email, recipients, msg.as_string())
            smtp.quit()

            logger.info(f"告警邮件已发送: {device_name} ({self.host})")
            return True

        except smtplib.SMTPException as e:
            logger.error(f"邮件发送失败 (SMTP): {e}")
            return False
        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False


def monitor_device_loop(
    host: str,
    user: str,
    password: str,
    backend_password: Optional[str] = None,
    coredump_dir: str = '/data/coredump',
    check_interval: int = 300,
    smtp_config: Optional[Dict[str, Any]] = None,
    device_name: str = '未知设备'
) -> None:
    """
    持续监测设备 coredump 文件（循环模式）

    Args:
        host: 设备 IP
        user: SSH 用户名
        password: SSH 密码
        backend_password: 后台密码
        coredump_dir: coredump 目录
        check_interval: 检查间隔（秒）
        smtp_config: SMTP 配置（可选）
            {
                'server': 'smtp.example.com',
                'port': 465,
                'sender': 'alert@example.com',
                'password': 'xxx',
                'recipients': ['user@example.com'],
                'use_ssl': True
            }
        device_name: 设备名称
    """
    monitor = CoredumpMonitor(
        host=host,
        user=user,
        password=password,
        backend_password=backend_password,
        coredump_dir=coredump_dir
    )

    # 初始化文件列表（避免首次误报）
    monitor.init_last_files()

    logger.info(f"开始监测设备 {device_name} ({host}), 检查间隔: {check_interval}秒")

    while True:
        try:
            # 检测新增文件
            new_files = monitor.check_new_files()

            if new_files:
                logger.warning(f"检测到 {len(new_files)} 个新增 coredump 文件!")

                # 发送告警邮件
                if smtp_config:
                    monitor.send_alert_email(
                        smtp_server=smtp_config.get('server'),
                        smtp_port=smtp_config.get('port', 465),
                        sender_email=smtp_config.get('sender'),
                        sender_password=smtp_config.get('password'),
                        recipients=smtp_config.get('recipients', []),
                        device_name=device_name,
                        use_ssl=smtp_config.get('use_ssl', True)
                    )

        except Exception as e:
            logger.error(f"监测出错: {e}")

        # 等待下次检查
        time.sleep(check_interval)


# 示例用法
if __name__ == '__main__':
    # 示例 1: 单次检测
    print("=== 示例 1: 单次检测 ===")
    monitor = CoredumpMonitor(
        host='192.168.1.10',
        user='admin',
        password='admin_password',
        backend_password='root_password',
        coredump_dir='/data/coredump'
    )

    # 测试连接
    result = monitor.test_connection()
    print(f"连接测试: {result}")

    # 获取文件列表
    files = monitor.get_coredump_files()
    print(f"文件列表: {files}")

    # 检测新增文件
    new_files = monitor.check_new_files()
    print(f"新增文件: {new_files}")

    # 示例 2: 持续监测（带邮件告警）
    # print("\n=== 示例 2: 持续监测 ===")
    # smtp_config = {
    #     'server': 'smtp.exmail.qq.com',
    #     'port': 465,
    #     'sender': 'alert@tdhx.com',
    #     'password': 'xxx',
    #     'recipients': ['user@tdhx.com'],
    #     'use_ssl': True
    # }
    # monitor_device_loop(
    #     host='192.168.1.10',
    #     user='admin',
    #     password='admin_password',
    #     backend_password='root_password',
    #     check_interval=300,
    #     smtp_config=smtp_config,
    #     device_name='防火墙-01'
    # )