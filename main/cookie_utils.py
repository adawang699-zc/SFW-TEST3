#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cookie 缓存工具模块
"""
import os
import json
import logging
from datetime import datetime, timedelta
from djangoProject.config import COOKIE_CACHE_EXPIRY_MINUTES

logger = logging.getLogger(__name__)

# Cookie缓存目录
COOKIE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cookie_cache')

# 确保缓存目录存在
if not os.path.exists(COOKIE_CACHE_DIR):
    os.makedirs(COOKIE_CACHE_DIR)


def get_cached_cookie(ip_address: str) -> str | None:
    """
    从缓存中获取cookie

    Args:
        ip_address: 设备IP地址

    Returns:
        cookie字符串或None
    """
    cookie_file = os.path.join(COOKIE_CACHE_DIR, f"{ip_address.replace('.', '_')}.json")

    if not os.path.exists(cookie_file):
        return None

    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            cookie_data = json.load(f)

        # 检查cookie是否过期
        saved_time = datetime.fromisoformat(cookie_data.get('timestamp', ''))
        if datetime.now() - saved_time > timedelta(minutes=COOKIE_CACHE_EXPIRY_MINUTES):
            logger.debug(f"Cookie已过期: {ip_address}")
            return None

        return cookie_data.get('cookie', '')
    except json.JSONDecodeError as e:
        logger.error(f"读取cookie缓存JSON解析错误: {ip_address}, 错误: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"读取cookie缓存出错: {ip_address}, 错误: {str(e)}")
        return None


def save_cookie_to_cache(ip_address: str, cookie: str) -> bool:
    """
    保存cookie到缓存

    Args:
        ip_address: 设备IP地址
        cookie: cookie字符串

    Returns:
        是否保存成功
    """
    cookie_file = os.path.join(COOKIE_CACHE_DIR, f"{ip_address.replace('.', '_')}.json")

    try:
        cookie_data = {
            'ip_address': ip_address,
            'cookie': cookie,
            'timestamp': datetime.now().isoformat()
        }

        with open(cookie_file, 'w', encoding='utf-8') as f:
            json.dump(cookie_data, f, ensure_ascii=False, indent=2)
        logger.debug(f"Cookie已保存到缓存: {ip_address}")
        return True
    except Exception as e:
        logger.error(f"保存cookie缓存出错: {ip_address}, 错误: {str(e)}")
        return False