#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件服务模块
提供邮件发送和用户管理功能
"""

import logging
import smtplib
import threading
import sqlite3
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Tuple, Optional, List
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)

# 邮件日志
mail_logs = deque(maxlen=100)
log_lock = threading.Lock()


class MailService:
    """邮件服务"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(os.path.dirname(__file__), 'mail_users.db')
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mail_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    email TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mail_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT,
                    status TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            conn.close()
            logger.info("邮件服务数据库初始化成功")
        except Exception as e:
            logger.exception(f"数据库初始化失败: {e}")

    def send_test_mail(self, smtp_server: str, smtp_port: int, username: str,
                       password: str, recipient: str, subject: str = '测试邮件',
                       body: str = '这是一封测试邮件') -> Tuple[bool, str]:
        """发送测试邮件"""
        try:
            msg = MIMEMultipart()
            msg['From'] = username
            msg['To'] = recipient
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'plain', 'utf-8'))

            # 连接 SMTP 服务器
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()
            server.login(username, password)
            server.sendmail(username, recipient, msg.as_string())
            server.quit()

            # 记录日志
            self._log_mail(username, recipient, subject, 'success')
            logger.info(f"邮件发送成功: {recipient}")
            return True, "邮件发送成功"

        except smtplib.SMTPException as e:
            self._log_mail(username, recipient, subject, f'failed: {e}')
            logger.error(f"邮件发送失败 (SMTP): {e}")
            return False, f"SMTP 错误: {str(e)}"
        except Exception as e:
            self._log_mail(username, recipient, subject, f'failed: {e}')
            logger.exception(f"邮件发送异常: {e}")
            return False, str(e)

    def _log_mail(self, sender: str, recipient: str, subject: str, status: str):
        """记录邮件日志"""
        entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'sender': sender,
            'recipient': recipient,
            'subject': subject,
            'status': status
        }
        with log_lock:
            mail_logs.appendleft(entry)

        # 写入数据库
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO mail_logs (sender, recipient, subject, status, timestamp)
                VALUES (?, ?, ?, ?, ?)
            ''', (sender, recipient, subject, status, entry['timestamp']))
            conn.commit()
            conn.close()
        except:
            pass

    def get_recent_logs(self, limit: int = 50) -> List[Dict]:
        """获取最近邮件日志"""
        with log_lock:
            return list(mail_logs)[:limit]

    def add_user(self, username: str, password: str, email: str) -> Tuple[bool, str]:
        """添加邮件用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO mail_users (username, password, email)
                VALUES (?, ?, ?)
            ''', (username, password, email))
            conn.commit()
            conn.close()
            logger.info(f"添加邮件用户: {username}")
            return True, "用户添加成功"
        except sqlite3.IntegrityError:
            return False, "用户名已存在"
        except Exception as e:
            logger.exception(f"添加用户失败: {e}")
            return False, str(e)

    def delete_user(self, username: str) -> Tuple[bool, str]:
        """删除邮件用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('DELETE FROM mail_users WHERE username = ?', (username,))
            conn.commit()
            conn.close()
            logger.info(f"删除邮件用户: {username}")
            return True, "用户删除成功"
        except Exception as e:
            logger.exception(f"删除用户失败: {e}")
            return False, str(e)

    def get_users(self) -> List[Dict]:
        """获取所有邮件用户"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT username, email, created_at FROM mail_users')
            rows = cursor.fetchall()
            conn.close()

            users = []
            for row in rows:
                users.append({
                    'username': row[0],
                    'email': row[1],
                    'created_at': row[2]
                })
            return users
        except Exception as e:
            logger.exception(f"获取用户失败: {e}")
            return []


# 全局邮件服务实例
mail_service = MailService()