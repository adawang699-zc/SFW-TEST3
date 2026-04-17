"""
Agent 服务模块
包含服务监听、客户端服务、邮件服务等功能
"""

from .listeners import (
    start_tcp_listener, stop_tcp_listener,
    start_udp_listener, stop_udp_listener,
    listener_states
)
from .clients import client_manager, ClientManager

# 邮件服务在 modules 目录下
from agents.modules.mail_service import mail_service, MailService

__all__ = [
    'start_tcp_listener',
    'stop_tcp_listener',
    'start_udp_listener',
    'stop_udp_listener',
    'listener_states',
    'client_manager',
    'ClientManager',
    'mail_service',
    'MailService'
]