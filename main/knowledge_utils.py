#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识库加密和升级工具模块
用于创建和升级防火墙知识库
"""

import os
import json
import base64
import logging
import hashlib
import random
from Crypto import Random
from Crypto.Cipher import AES
import requests
from djangoProject.config import (
    SSL_VERIFY, REQUEST_TIMEOUT, USER_AGENT,
    FIREWALL_LOGIN_USER, FIREWALL_LOGIN_PASSWORD, FIREWALL_LOGIN_PIN
)

logger = logging.getLogger(__name__)

# AES密钥（与pycrypto.py一致）
AES_KEY = '123hdf456ABC!@#$'


def get_fresh_cookie(ip: str) -> tuple:
    """
    通过登录获取新的Cookie

    Args:
        ip: 设备IP

    Returns:
        (success, cookie_or_error): 成功返回(True, cookie)，失败返回(False, 错误信息)
    """
    try:
        from .cookie_utils import save_cookie_to_cache

        # 设置请求头
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

        # 登录数据
        login_data = {
            "loginuser": FIREWALL_LOGIN_USER,
            "pin": FIREWALL_LOGIN_PIN,
            "pw": FIREWALL_LOGIN_PASSWORD,
            "username": FIREWALL_LOGIN_USER
        }

        # 构建登录URL
        url = f"https://{ip}/checkUser"

        logger.info(f"自动获取Cookie: {url}")

        # 发送登录请求
        response = requests.post(
            url,
            json=login_data,
            headers=headers,
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:
            # 从响应头中提取cookie
            cookie_header = response.headers.get("Set-Cookie", "")

            if cookie_header:
                cookie = cookie_header.split(";")[0]
                # 保存到缓存
                save_cookie_to_cache(ip, cookie)
                logger.info(f"Cookie获取成功: {ip}")
                return True, cookie
            else:
                return False, "登录成功但未获取到Cookie"
        else:
            return False, f"登录失败，状态码: {response.status_code}"

    except Exception as e:
        logger.exception(f"获取Cookie失败: {e}")
        return False, str(e)


def add_version_to_json(json_content: str, version: str, time: str) -> str:
    """
    在JSON内容开头添加version和time字段

    Args:
        json_content: 原始JSON字符串
        version: 版本号（如 "1.1.1"）
        time: 更新时间（如 "2026-10-10"）

    Returns:
        添加字段后的JSON字符串
    """
    try:
        data = json.loads(json_content)
        # 在开头插入version和time
        new_data = {
            "version": version,
            "time": time
        }
        new_data.update(data)
        return json.dumps(new_data, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: {e}")
        raise ValueError("无效的JSON格式")


def get_context_block(data: bytes, filename: str) -> bytes:
    """
    添加padding（与pycrypto.py一致）

    Args:
        data: 原始数据
        filename: 文件名

    Returns:
        添加padding后的数据
    """
    dl = len(data) + len(filename) + 8
    need = 16 - (dl % 16)
    l = need + len(filename) + 8
    lfn = len(filename)
    add = b' ' * need

    # 格式: 文件名长度(4位) + 文件名 + padding + 总长度(4位)
    extra = f'{lfn:4d}{filename}'.encode() + add + f'{l:4d}'.encode()
    return data + extra


def encrypt_knowledge_file(input_path: str, output_path: str = None) -> tuple:
    """
    加密知识库文件

    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径（可选，默认为input_path.bin）

    Returns:
        (success, result): 成功返回(True, 输出路径)，失败返回(False, 错误信息)
    """
    try:
        with open(input_path, 'rb') as f:
            data = f.read()

        # 添加padding
        filename = os.path.basename(input_path)
        data = get_context_block(data, filename)

        # base64编码
        data = base64.b64encode(data)

        # AES加密
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(AES_KEY.encode(), AES.MODE_CFB, iv)
        ciphertext = iv + cipher.encrypt(data)

        # 输出文件
        if output_path is None:
            output_path = input_path + '.bin'

        with open(output_path, 'wb') as f:
            f.write(ciphertext)

        logger.info(f"加密成功: {input_path} -> {output_path}")
        return True, output_path

    except Exception as e:
        logger.exception(f"加密失败: {e}")
        return False, str(e)


def create_knowledge_package(json_content: str, version: str, time: str) -> tuple:
    """
    创建知识库升级包（内存中处理，不写临时文件）

    Args:
        json_content: JSON内容
        version: 版本号
        time: 更新时间

    Returns:
        (success, result): 成功返回(True, 二进制内容)，失败返回(False, 错误信息)
    """
    try:
        # 添加version和time
        modified_json = add_version_to_json(json_content, version, time)

        # 转为bytes
        data = modified_json.encode('utf-8')

        # 添加padding
        data = get_context_block(data, 'service.json')

        # base64编码
        data = base64.b64encode(data)

        # AES加密
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(AES_KEY.encode(), AES.MODE_CFB, iv)
        ciphertext = iv + cipher.encrypt(data)

        return True, ciphertext

    except Exception as e:
        logger.exception(f"创建知识库包失败: {e}")
        return False, str(e)


def upgrade_knowledge_to_device(ip: str, file_content: bytes, cookie: str = None, auto_get_cookie: bool = False) -> tuple:
    """
    升级知识库到设备

    Args:
        ip: 设备IP
        file_content: 加密后的文件内容
        cookie: 认证Cookie（可选，不提供则尝试从缓存获取）
        auto_get_cookie: 是否自动获取Cookie（当缓存中没有时自动登录获取）

    Returns:
        (success, result): 成功返回(True, 响应内容)，失败返回(False, 错误信息)
    """
    try:
        cookie = _get_or_fetch_cookie(ip, cookie, auto_get_cookie)
        if isinstance(cookie, tuple) and not cookie[0]:
            return cookie

        # 构建URL
        url = f"https://{ip}/serviceImport"

        # 准备文件
        files = {
            'service_fileUpload': ('service.bin', file_content, 'application/octet-stream')
        }

        headers = _build_headers(cookie)

        logger.info(f"升级知识库到设备: {url}")

        response = requests.post(
            url,
            files=files,
            headers=headers,
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        logger.info(f"升级响应: status={response.status_code}, body={response.text[:200]}")

        if response.status_code == 200:
            return True, response.text
        else:
            return False, f"请求失败，状态码: {response.status_code}, 响应: {response.text}"

    except Exception as e:
        logger.exception(f"升级知识库失败: {e}")
        return False, str(e)


def create_vul_package(zip_content: bytes, build_time: str, version: str) -> tuple:
    """
    创建漏洞库升级包

    Args:
        zip_content: 原始zip文件内容
        build_time: 构建时间（如 "2026-03-09"）
        version: 版本号（如 "2026030901"）

    Returns:
        (success, result): 成功返回(True, 二进制内容)，失败返回(False, 错误信息)
    """
    import io
    import zipfile

    try:
        input_buffer = io.BytesIO(zip_content)
        output_buffer = io.BytesIO()

        build_time_modified = False
        version_modified = False

        with zipfile.ZipFile(input_buffer, 'r') as zip_in:
            logger.info(f"漏洞库ZIP内文件列表: {[item.filename for item in zip_in.infolist()}")

            with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_out:
                for item in zip_in.infolist():
                    filename = item.filename
                    # 处理可能的路径分隔符
                    basename = filename.split('/')[-1]

                    if basename == 'build_time' or filename == 'build_time':
                        zip_out.writestr(item, build_time.encode('utf-8'))
                        logger.info(f"已修改 build_time: {build_time}")
                        build_time_modified = True
                    elif basename == 'version' or filename == 'version':
                        zip_out.writestr(item, version.encode('utf-8'))
                        logger.info(f"已修改 version: {version}")
                        version_modified = True
                    else:
                        zip_out.writestr(item, zip_in.read(filename))

        if not build_time_modified:
            logger.warning("未找到 build_time 文件")
        if not version_modified:
            logger.warning("未找到 version 文件")

        return True, output_buffer.getvalue()

    except Exception as e:
        logger.exception(f"创建漏洞库包失败: {e}")
        return False, str(e)


def upgrade_vul_to_device(ip: str, file_content: bytes, cookie: str = None, auto_get_cookie: bool = False) -> tuple:
    """
    升级漏洞库到设备

    Args:
        ip: 设备IP
        file_content: 升级包内容
        cookie: 认证Cookie
        auto_get_cookie: 是否自动获取Cookie

    Returns:
        (success, result): 成功返回(True, 响应内容)，失败返回(False, 错误信息)
    """
    try:
        cookie = _get_or_fetch_cookie(ip, cookie, auto_get_cookie)
        if isinstance(cookie, tuple) and not cookie[0]:
            return cookie

        headers = _build_headers(cookie)

        # 第一步：上传文件
        upload_url = f"https://{ip}/upload_vulfile"
        files = {
            'vul_upgrade_fileUpload': ('vul.lib', file_content, 'application/octet-stream')
        }

        logger.info(f"上传漏洞库文件请求: URL={upload_url}")

        response = requests.post(
            upload_url,
            files=files,
            headers=headers,
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return False, f"上传失败，状态码: {response.status_code}, 响应: {response.text}"

        logger.info(f"漏洞库上传响应: {response.text[:200]}")

        # 第二步：执行升级
        upgrade_url = f"https://{ip}/vul_update"
        data = {
            "loginuser": FIREWALL_LOGIN_USER,
            "action": 1
        }

        logger.info(f"执行漏洞库升级请求: URL={upgrade_url}")

        response = requests.post(
            upgrade_url,
            json=data,
            headers={"Cookie": cookie, "User-Agent": USER_AGENT},
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        logger.info(f"漏洞库升级响应: status={response.status_code}, body={response.text[:200]}")

        if response.status_code == 200:
            return True, response.text
        else:
            return False, f"升级失败，状态码: {response.status_code}, 响应: {response.text}"

    except Exception as e:
        logger.exception(f"升级漏洞库失败: {e}")
        return False, str(e)


def create_virus_package(zip_content: bytes, vul_time: str, version: str) -> tuple:
    """
    创建病毒库升级包

    Args:
        zip_content: 原始zip文件内容
        vul_time: 时间（如 "2026-03-09"）
        version: 版本号（如 "2026030901"）

    Returns:
        (success, result): 成功返回(True, 二进制内容)，失败返回(False, 错误信息)
    """
    import io
    import zipfile

    try:
        input_buffer = io.BytesIO(zip_content)
        output_buffer = io.BytesIO()

        vul_time_modified = False
        version_modified = False

        with zipfile.ZipFile(input_buffer, 'r') as zip_in:
            logger.info(f"病毒库ZIP内文件列表: {[item.filename for item in zip_in.infolist()}")

            with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_out:
                for item in zip_in.infolist():
                    filename = item.filename
                    # 处理可能的路径分隔符
                    basename = filename.split('/')[-1]

                    if basename == 'vul_time' or filename == 'vul_time':
                        zip_out.writestr(item, vul_time.encode('utf-8'))
                        logger.info(f"已修改 vul_time: {vul_time}")
                        vul_time_modified = True
                    elif basename == 'version' or filename == 'version':
                        zip_out.writestr(item, version.encode('utf-8'))
                        logger.info(f"已修改 version: {version}")
                        version_modified = True
                    else:
                        zip_out.writestr(item, zip_in.read(filename))

        if not vul_time_modified:
            logger.warning("未找到 vul_time 文件")
        if not version_modified:
            logger.warning("未找到 version 文件")

        return True, output_buffer.getvalue()

    except Exception as e:
        logger.exception(f"创建病毒库包失败: {e}")
        return False, str(e)


def upgrade_virus_to_device(ip: str, file_content: bytes, cookie: str = None, auto_get_cookie: bool = False) -> tuple:
    """
    升级病毒库到设备

    Args:
        ip: 设备IP
        file_content: 升级包内容
        cookie: 认证Cookie
        auto_get_cookie: 是否自动获取Cookie

    Returns:
        (success, result): 成功返回(True, 响应内容)，失败返回(False, 错误信息)
    """
    try:
        cookie = _get_or_fetch_cookie(ip, cookie, auto_get_cookie)
        if isinstance(cookie, tuple) and not cookie[0]:
            return cookie

        headers = _build_headers(cookie)

        # 第一步：上传文件
        upload_url = f"https://{ip}/probe_vul_file_upload"
        files = {
            'vul_file_upload': ('virus.lib', file_content, 'application/octet-stream')
        }

        logger.info(f"上传病毒库文件请求: URL={upload_url}")

        response = requests.post(
            upload_url,
            files=files,
            headers=headers,
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return False, f"上传失败，状态码: {response.status_code}, 响应: {response.text}"

        logger.info(f"病毒库上传响应: {response.text[:200]}")

        # 第二步：执行升级
        upgrade_url = f"https://{ip}/probe_vul_upgrade"
        data = {
            "loginuser": FIREWALL_LOGIN_USER,
            "action": 1
        }

        logger.info(f"执行病毒库升级请求: URL={upgrade_url}")

        response = requests.post(
            upgrade_url,
            json=data,
            headers={"Cookie": cookie, "User-Agent": USER_AGENT},
            verify=SSL_VERIFY,
            timeout=REQUEST_TIMEOUT
        )

        logger.info(f"病毒库升级响应: status={response.status_code}, body={response.text[:200]}")

        if response.status_code == 200:
            return True, response.text
        else:
            return False, f"升级失败，状态码: {response.status_code}, 响应: {response.text}"

    except Exception as e:
        logger.exception(f"升级病毒库失败: {e}")
        return False, str(e)


def _get_or_fetch_cookie(ip: str, cookie: str = None, auto_get_cookie: bool = False):
    """
    获取或自动获取Cookie的内部函数

    Returns:
        成功返回cookie字符串，失败返回(False, error)元组
    """
    from .cookie_utils import get_cached_cookie

    if not cookie:
        cookie = get_cached_cookie(ip)
        if not cookie:
            if auto_get_cookie:
                logger.info(f"缓存中没有Cookie，尝试自动登录: {ip}")
                success, result = get_fresh_cookie(ip)
                if success:
                    return result
                else:
                    return (False, f"自动获取Cookie失败: {result}")
            else:
                return (False, "缺少Cookie，请先登录设备")
    return cookie


def _generate_upgrade_sign_params() -> dict:
    """
    生成升级请求所需的签名参数

    Returns:
        dict: 包含 loginuser, __, sign 参数
    """
    import time

    # 当前时间戳
    timestamp = str(int(time.time()))

    # 生成6位随机字符(0-f，小写)
    hex_chars = '0123456789abcdef'
    random_str = ''.join([random.choice(hex_chars) for _ in range(6)])

    # __参数: 随机6字符 + 时间戳
    random_param = random_str + timestamp

    # sign参数: MD5(__的值 + "!=32&*^%$#%$3")
    sign_str = random_param + "!=32&*^%$#%$3"
    sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest().lower()

    logger.info(f"签名计算: timestamp={timestamp}, random={random_str}")
    logger.info(f"__参数: {random_param}")
    logger.info(f"签名字符串: {sign_str}")
    logger.info(f"签名结果: {sign}")

    return {
        'loginuser': FIREWALL_LOGIN_USER,
        '__': random_param,
        'sign': sign
    }


def _build_headers(cookie: str) -> dict:
    """构建请求头"""
    return {
        "Cookie": cookie,
        "User-Agent": USER_AGENT,
    }