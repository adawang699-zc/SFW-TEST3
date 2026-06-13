#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 日志集中配置

Agent 进程独立于 Django 运行，需要自行配置日志系统。
本模块提供统一的 dictConfig 配置，支持：
- 按模块分文件记录日志
- RotatingFileHandler 自动轮转（10MB, 5 backups）
- 控制台输出（stderr，由 systemd 捕获）
- 通过环境变量配置日志目录和实例 ID
"""

import os
import logging
import logging.config
from pathlib import Path

# 默认配置
_DEFAULT_WORK_DIR = '/opt/SFW-TEST3'
_DEFAULT_LOG_DIR = os.path.join(_DEFAULT_WORK_DIR, 'logs')
_MAX_BYTES = 10 * 1024 * 1024  # 10MB
_BACKUP_COUNT = 5


def setup_agent_logging() -> None:
    """初始化 Agent 日志系统

    在 full_agent.py 启动时最先调用，早于所有 agents.* 模块导入。
    读取环境变量：
    - AGENT_LOG_DIR: 日志目录（默认 {AGENT_WORK_DIR}/logs/）
    - AGENT_ID: Agent 实例 ID（默认 agent_eth0），用于实例级日志文件名
    """
    log_dir = os.environ.get('AGENT_LOG_DIR', _DEFAULT_LOG_DIR)
    agent_id = os.environ.get('AGENT_ID', 'agent_eth0')

    # 确保日志目录存在
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    # 构建文件路径
    def _log(name: str) -> str:
        return os.path.join(log_dir, name)

    # 通用 RotatingFileHandler 模板
    def _rfh(filename: str) -> dict:
        return {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': _log(filename),
            'maxBytes': _MAX_BYTES,
            'backupCount': _BACKUP_COUNT,
            'formatter': 'verbose',
            'level': 'INFO',
            'encoding': 'utf-8',
        }

    config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'verbose': {
                'format': '[{asctime}] {levelname} {name}: {message}',
                'style': '{',
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'verbose',
                'level': 'INFO',
                'stream': 'ext://sys.stderr',
            },
            # 实例级主日志
            'agent_file': _rfh(f'agent_{agent_id}.log'),
            # 服务状态日志（add_service_log 路由）
            'service_file': _rfh('agent_service.log'),
            # 工业协议日志
            'industrial_protocol_file': _rfh('agent_industrial_protocol.log'),
            # 报文捕获
            'packet_capture_file': _rfh('agent_packet_capture.log'),
            # 报文发送
            'packet_sender_file': _rfh('agent_packet_sender.log'),
            # 端口扫描
            'port_scanner_file': _rfh('agent_port_scanner.log'),
            # 报文回放
            'packet_replay_file': _rfh('agent_packet_replay.log'),
            # 邮件服务
            'mail_service_file': _rfh('agent_mail_service.log'),
            # DHCP 客户端
            'dhcp_client_file': _rfh('agent_dhcp_client.log'),
        },
        'loggers': {
            # full_agent.py 主 logger
            'agent': {
                'handlers': ['console', 'agent_file'],
                'level': 'INFO',
                'propagate': False,
            },
            # add_service_log 路由
            'agents.service': {
                'handlers': ['console', 'service_file'],
                'level': 'INFO',
                'propagate': False,
            },
            # 工业协议
            'industrial_protocol': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            # ========== agents.modules.* ==========
            'agents.modules.packet_capture': {
                'handlers': ['console', 'packet_capture_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules.packet_sender': {
                'handlers': ['console', 'packet_sender_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules.port_scanner': {
                'handlers': ['console', 'port_scanner_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules.packet_replay': {
                'handlers': ['console', 'packet_replay_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules.mail_service': {
                'handlers': ['console', 'mail_service_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules.dhcp_client_module': {
                'handlers': ['console', 'dhcp_client_file'],
                'level': 'INFO',
                'propagate': False,
            },
            # ========== agents.protocols.* → 工业协议统一文件 ==========
            'agents.protocols.modbus_client': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.modbus_server': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.s7_client': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.s7_server': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.opcua_common': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.opcua_client': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.opcua_server': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.opcua_gateway': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.dnp3_handler_linux': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.enip_handler': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.goose_sender': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.sv_sender': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.ethercat_sender': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.powerlink_sender': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.protocols.dcp_sender': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            # agents.protocols 和 agents.modules 的 fallback
            # 未明确配置的子 logger 会向上传播到此处
            'agents.protocols': {
                'handlers': ['console', 'industrial_protocol_file'],
                'level': 'INFO',
                'propagate': False,
            },
            'agents.modules': {
                'level': 'INFO',
                'propagate': False,
            },
            # agents.services.* (clients, listeners)
            'agents.services': {
                'handlers': ['console', 'service_file'],
                'level': 'INFO',
                'propagate': False,
            },
        },
    }

    logging.config.dictConfig(config)
