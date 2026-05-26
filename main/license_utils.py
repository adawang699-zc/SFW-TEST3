#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
授权管理工具模块
"""

import os
import sys
import json
import subprocess
import logging
import tempfile
import time

logger = logging.getLogger(__name__)

def find_knowledge_license_tool():
    """查找知识库授权工具"""
    # 获取项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    license_dir = os.path.join(project_root, 'license')

    # 检测是否在 Linux 上运行
    is_linux = sys.platform.startswith('linux')

    if is_linux:
        # Linux 上优先使用 Python 版本，跳过 .exe 和 .bat
        possible_paths = [
            os.path.join(license_dir, 'hx_knowledge_license_gender.py'),
            os.path.join(license_dir, 'hx_knowledge_license_gender'),
            'hx_knowledge_license_gender',
        ]
    else:
        # Windows 上保持原有搜索顺序
        possible_paths = [
            os.path.join(license_dir, 'hx_knowledge_license_gender.exe'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.exe.bat'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.bat'),
            os.path.join(license_dir, 'hx_knowledge_license_gender.py'),
            os.path.join(license_dir, 'hx_knowledge_license_gender'),
            'hx_knowledge_license_gender.exe',
            'hx_knowledge_license_gender',
        ]

    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"找到授权工具: {path}")
            # 如果是Python文件，使用当前解释器路径（兼容 Ubuntu 上 python3 或 venv）
            if path.endswith('.py'):
                return [sys.executable, path]
            else:
                return [path]

        # 如果是相对路径，也尝试在PATH中查找
        if not os.path.isabs(path):
            try:
                python_cmd = sys.executable if path.endswith('.py') else path
                test_cmd = [path, '--help'] if not path.endswith('.py') else [sys.executable, path, '--help']
                subprocess.run(test_cmd, capture_output=True, timeout=5)
                logger.info(f"在PATH中找到授权工具: {path}")
                return [python_cmd] if not path.endswith('.py') else [sys.executable, path]
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

# 设备授权工具本地路径配置（从 10.40.24.17 拷贝到本地 license 目录）
DEVICE_LICENSE_CONFIG = {
    'lic_gen_path': 'license/lic_gen',     # DR 方式工具（相对于项目根目录）
    'licgen_path': 'license/licgen',       # dev-Code 方式工具（相对于项目根目录）
}

def _get_license_dir():
    """获取项目 license 目录"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, 'license')

def test_device_license_connection():
    """测试设备授权工具（本地检查）"""
    try:
        license_dir = _get_license_dir()
        lic_gen_local = os.path.join(license_dir, 'lic_gen')
        licgen_local = os.path.join(license_dir, 'licgen')

        # 检查 DR 工具
        dr_exists = os.path.exists(lic_gen_local) and os.access(lic_gen_local, os.X_OK)
        # 检查 dev-Code 工具
        devcode_exists = os.path.exists(licgen_local) and os.access(licgen_local, os.X_OK)

        if not dr_exists and not devcode_exists:
            return False, f'未找到授权工具，请将 lic_gen/licgen 拷贝到 {license_dir} 目录并设置执行权限'

        tools = []
        if dr_exists:
            tools.append('lic_gen (DR)')
        if devcode_exists:
            tools.append(f'licgen (dev-Code)')

        logger.info(f"设备授权工具检查通过: {', '.join(tools)}")
        return True, f'设备授权工具就绪: {", ".join(tools)}'

    except Exception as e:
        logger.exception(f"测试设备授权工具异常: {e}")
        return False, str(e)

def generate_device_license(auth_name, machine_code):
    """生成设备授权（本地执行）"""
    try:
        license_dir = _get_license_dir()
        lic_gen_path = os.path.join(license_dir, 'lic_gen')

        if not os.path.exists(lic_gen_path):
            return False, f'授权工具 lic_gen 不存在，请先将其拷贝到 {license_dir} 目录'

        if not os.access(lic_gen_path, os.X_OK):
            return False, f'授权工具 lic_gen 无执行权限，请执行: chmod +x {lic_gen_path}'

        # 构建JSON参数
        license_json = {
            "name": auth_name,
            "mc": machine_code
        }
        json_str = json.dumps(license_json, ensure_ascii=False)

        filename = f"{machine_code}.lic"
        output_path = os.path.join(tempfile.gettempdir(), filename)

        # 构建命令
        cmd = [lic_gen_path, '-j', json_str, '-p', output_path]

        logger.info(f"执行设备授权生成命令: {' '.join(cmd)}")

        # 执行命令
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            # 检查文件是否生成
            if os.path.exists(output_path):
                # 读取文件内容
                with open(output_path, 'rb') as f:
                    file_content = f.read()

                # 清理临时文件
                try:
                    os.unlink(output_path)
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {e}")

                logger.info(f"设备授权生成成功: {filename}, 文件大小: {len(file_content)} 字节")

                return True, {
                    'filename': filename,
                    'content': file_content,
                    'message': '设备授权生成成功'
                }
            else:
                return False, '授权文件生成失败（输出文件不存在）'
        else:
            error_msg = result.stderr.strip() if result.stderr else f'命令执行失败，返回码: {result.returncode}'
            logger.error(f"设备授权生成失败: {error_msg}")
            return False, error_msg

    except subprocess.TimeoutExpired:
        return False, '命令执行超时'
    except Exception as e:
        logger.exception(f"设备授权生成异常: {e}")
        return False, str(e)