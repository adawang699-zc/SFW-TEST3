#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
授权管理工具模块
"""

import os
import json
import subprocess
import logging
import tempfile
import time
import paramiko

logger = logging.getLogger(__name__)


def find_knowledge_license_tool():
    """查找知识库授权工具"""
    import platform

    # 获取项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    license_dir = os.path.join(project_root, 'license')

    # 根据操作系统选择优先查找的文件
    is_windows = platform.system() == 'Windows'

    if is_windows:
        # Windows: 优先查找 .exe 文件
        possible_paths = [
            os.path.join(license_dir, 'hx_knowledge_license_gender.exe'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.exe.bat'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.bat'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.py'),
            os.path.join(license_dir, 'hx_knowledge_license_gender'),
            'hx_knowledge_license_gender.exe',
            'hx_knowledge_license_gender'
        ]
    else:
        # Linux/Unix: 优先查找 .py 文件
        possible_paths = [
            os.path.join(license_dir, 'hx_knowledge_license_gender.py'),
            os.path.join(license_dir, 'hx_knowledge_license_gender'),
            'hx_knowledge_license_gender.py',
            'hx_knowledge_license_gender'
        ]

    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"找到授权工具: {path}")
            # 如果是Python文件，需要添加python前缀
            if path.endswith('.py'):
                return ['python', path]
            else:
                return [path]

        # 如果是相对路径，也尝试在PATH中查找
        if not os.path.isabs(path):
            try:
                test_cmd = [path, '--help'] if not path.endswith('.py') else ['python', path, '--help']
                subprocess.run(test_cmd, capture_output=True, timeout=5)
                logger.info(f"在PATH中找到授权工具: {path}")
                return [path] if not path.endswith('.py') else ['python', path]
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError):
                continue

    return None


def generate_knowledge_license(machine_code, vul_expire, virus_expire, rules_expire, save_path=None):
    """生成知识库授权

    Args:
        machine_code: 机器码
        vul_expire: 漏洞库过期年数（前端传入年数，直接传给工具）
        virus_expire: 病毒库过期年数（前端传入年数，直接传给工具）
        rules_expire: 规则库过期年数（前端传入年数，直接传给工具）
        save_path: 保存路径（可选，如果为None则生成到临时目录并返回文件内容）

    Returns:
        (success, result):
            - 如果save_path为None: 返回文件内容（base64编码）和文件名，供前端下载
            - 如果save_path不为None: 返回文件路径（保持原有行为）
    """
    try:
        # 查找工具
        tool_cmd = find_knowledge_license_tool()
        if not tool_cmd:
            return False, '找不到 hx_knowledge_license_gender 程序。请将程序放置在 license 目录下或确保在系统PATH中可用。'

        # 构建JSON参数（直接使用年数，工具自己处理）
        license_json = {
            "machinecode": machine_code,
            "vul_expire": vul_expire,
            "virus_expire": virus_expire,
            "rules_expire": rules_expire
        }

        # 构建文件名
        filename = f"{machine_code}.lic"

        # 如果save_path为None，生成到临时目录并返回文件内容
        if save_path is None:
            # 创建临时文件
            temp_fd, temp_file_path = tempfile.mkstemp(suffix='.lic', prefix='license_')
            try:
                os.close(temp_fd)  # 关闭文件描述符，避免文件锁定

                # 构建命令（生成到临时文件）
                json_str = json.dumps(license_json)
                cmd = tool_cmd + [
                    'gen',
                    '--json',
                    json_str,
                    '-o',
                    temp_file_path
                ]

                logger.info(f"执行知识库授权生成命令: {' '.join(cmd)}")

                # 执行命令
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if result.returncode == 0:
                    # 读取文件内容
                    with open(temp_file_path, 'rb') as f:
                        file_content = f.read()

                    # 将文件内容编码为base64，方便前端处理
                    import base64
                    file_content_b64 = base64.b64encode(file_content).decode('utf-8')

                    logger.info(f"知识库授权生成成功: {filename}, 文件大小: {len(file_content)} 字节")

                    return True, {
                        'filename': filename,
                        'content': file_content_b64,  # base64编码的文件内容
                        'message': '生成成功，请点击下载'
                    }
                else:
                    error_msg = result.stderr.strip() if result.stderr else f'命令执行失败，返回码: {result.returncode}'
                    logger.error(f"知识库授权生成失败: {error_msg}")
                    return False, error_msg
            finally:
                # 清理临时文件
                if os.path.exists(temp_file_path):
                    try:
                        os.unlink(temp_file_path)
                    except Exception as e:
                        logger.warning(f"清理临时文件失败: {e}")
        else:
            # 原有逻辑：生成到指定路径
            output_path = os.path.join(save_path, filename)

            # 构建命令
            json_str = json.dumps(license_json)
            cmd = tool_cmd + [
                'gen',
                '--json',
                json_str,
                '-o',
                output_path
            ]

            logger.info(f"执行知识库授权生成命令: {' '.join(cmd)}")

            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return True, {
                    'filename': filename,
                    'output_path': output_path,
                    'output': result.stdout.strip() if result.stdout else '生成成功'
                }
            else:
                error_msg = result.stderr.strip() if result.stderr else f'命令执行失败，返回码: {result.returncode}'
                logger.error(f"知识库授权生成失败: {error_msg}")
                return False, error_msg

    except subprocess.TimeoutExpired:
        return False, '命令执行超时'
    except Exception as e:
        logger.exception(f"知识库授权生成异常: {e}")
        return False, str(e)


def decrypt_knowledge_license(file_path):
    """解密知识库授权"""
    try:
        if not os.path.exists(file_path):
            return False, '授权文件不存在'

        # 查找工具
        tool_cmd = find_knowledge_license_tool()
        if not tool_cmd:
            return False, '找不到 hx_knowledge_license_gender 程序。请将程序放置在 license 目录下或确保在系统PATH中可用。'

        # 构建命令
        cmd = tool_cmd + [
            'dec',
            '-i',
            file_path
        ]

        logger.info(f"执行知识库授权解密命令: {' '.join(cmd)}")

        # 执行命令
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return True, result.stdout.strip() if result.stdout else '解密成功'
        else:
            error_msg = result.stderr.strip() if result.stderr else f'命令执行失败，返回码: {result.returncode}'
            logger.error(f"知识库授权解密失败: {error_msg}")
            return False, error_msg

    except subprocess.TimeoutExpired:
        return False, '命令执行超时'
    except Exception as e:
        logger.exception(f"知识库授权解密异常: {e}")
        return False, str(e)


# 设备授权服务器配置
DEVICE_LICENSE_SERVER = {
    'host': '10.40.24.17',
    'username': 'tdhx',
    'password': 'tdhx@2017',
    'port': 22,
    'lic_gen_path': '/home/tdhx/license/x64/lic_gen'
}


def test_device_license_connection():
    """测试设备授权服务器连接"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 连接SSH
        ssh.connect(
            hostname=DEVICE_LICENSE_SERVER['host'],
            port=DEVICE_LICENSE_SERVER['port'],
            username=DEVICE_LICENSE_SERVER['username'],
            password=DEVICE_LICENSE_SERVER['password'],
            timeout=10
        )

        # 测试lic_gen程序是否存在
        stdin, stdout, stderr = ssh.exec_command(f"ls -la {DEVICE_LICENSE_SERVER['lic_gen_path']}")
        exit_code = stdout.channel.recv_exit_status()

        ssh.close()

        if exit_code == 0:
            return True, '授权服务器连接成功'
        else:
            return False, '授权程序不存在'

    except paramiko.AuthenticationException:
        return False, '认证失败，用户名或密码错误'
    except paramiko.SSHException as e:
        return False, f'SSH连接失败: {str(e)}'
    except Exception as e:
        logger.exception(f"测试设备授权服务器连接异常: {e}")
        return False, str(e)


def generate_device_license(auth_name, machine_code):
    """生成设备授权"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # 连接SSH
        ssh.connect(
            hostname=DEVICE_LICENSE_SERVER['host'],
            port=DEVICE_LICENSE_SERVER['port'],
            username=DEVICE_LICENSE_SERVER['username'],
            password=DEVICE_LICENSE_SERVER['password'],
            timeout=10
        )

        # 构建JSON参数
        license_json = {
            "name": auth_name,
            "mc": machine_code
        }
        json_str = json.dumps(license_json, ensure_ascii=False)

        # 远程文件路径
        remote_filename = f"{machine_code}.lic"
        remote_file_path = f"/tmp/{remote_filename}"

        # 构建命令
        cmd = f"{DEVICE_LICENSE_SERVER['lic_gen_path']} -j '{json_str}' -p {remote_file_path}"

        logger.info(f"执行设备授权生成命令: {cmd}")

        # 执行命令
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
        exit_code = stdout.channel.recv_exit_status()

        stdout_content = stdout.read().decode('utf-8', errors='ignore').strip()
        stderr_content = stderr.read().decode('utf-8', errors='ignore').strip()

        if exit_code == 0:
            # 检查文件是否生成
            stdin, stdout, stderr = ssh.exec_command(f"ls -la {remote_file_path}")
            file_check_exit = stdout.channel.recv_exit_status()

            if file_check_exit == 0:
                # 下载文件内容
                sftp = ssh.open_sftp()
                temp_file_path = None
                try:
                    # 创建临时文件
                    temp_fd, temp_file_path = tempfile.mkstemp(suffix='.lic')
                    os.close(temp_fd)  # 关闭文件描述符，避免文件锁定

                    # 下载文件
                    sftp.get(remote_file_path, temp_file_path)

                    # 读取文件内容
                    with open(temp_file_path, 'rb') as f:
                        file_content = f.read()

                    # 清理远程文件
                    ssh.exec_command(f"rm -f {remote_file_path}")

                    logger.info(f"设备授权生成成功: {remote_filename}, 文件大小: {len(file_content)} 字节")

                    return True, {
                        'filename': remote_filename,
                        'content': file_content,
                        'message': '设备授权生成成功'
                    }
                except Exception as e:
                    logger.exception(f"下载授权文件异常: {e}")
                    return False, f'下载授权文件失败: {str(e)}'
                finally:
                    sftp.close()
                    # 清理本地临时文件
                    if temp_file_path and os.path.exists(temp_file_path):
                        try:
                            os.unlink(temp_file_path)
                        except Exception as e:
                            logger.warning(f"清理临时文件失败: {e}")
            else:
                return False, '授权文件生成失败'
        else:
            error_msg = stderr_content if stderr_content else f'命令执行失败，返回码: {exit_code}'
            logger.error(f"设备授权生成失败: {error_msg}")
            return False, error_msg

    except paramiko.AuthenticationException:
        return False, '认证失败，用户名或密码错误'
    except paramiko.SSHException as e:
        return False, f'SSH连接失败: {str(e)}'
    except Exception as e:
        logger.exception(f"设备授权生成异常: {e}")
        return False, str(e)
    finally:
        if 'ssh' in locals():
            ssh.close()