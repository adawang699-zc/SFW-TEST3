#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC Classic 网关辅助管理
"""

import socket
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

DEPLOYMENT_GUIDE = """
# OPC Classic 网关部署指南

## 一、Windows 虚拟机配置
推荐系统: Windows 10 LTSC，资源: 1C2G，15GB磁盘，桥接网络

## 二、UaGateway 安装
1. 下载: https://www.unified-automation.com/downloads/opc-ua-gateway.html
2. 配置 UA Server 连接: opc.tcp://<Ubuntu_IP>:4840/
3. 启用 DA/HDA/AE Server (ProgID: UaGateway.DA/HDA/AE)

## 三、DCOM 配置
运行 dcomcnfg，配置 UaGateway 组件权限，添加 Everyone 用户
"""

DCOM_CHECKLIST = """
# DCOM 配置检查清单
- [ ] 启动权限添加 Everyone
- [ ] 访问权限添加 Everyone
- [ ] 配置权限添加 Everyone
- [ ] 标识设置为交互式用户
- [ ] 防火墙开放端口 135 和 1024-65535
"""


class OpcUaGatewayHelper:
    """OPC Classic 网关辅助管理"""

    def check_uaserver_reachable(self, host: str, port: int = 4840,
                                  timeout: float = 3.0) -> Dict[str, Any]:
        """检测 UA Server 是否可达"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return {"reachable": result == 0, "host": host, "port": port}
        except Exception as e:
            return {"reachable": False, "host": host, "port": port, "error": str(e)}

    def get_deployment_guide(self) -> str:
        return DEPLOYMENT_GUIDE

    def get_dcom_checklist(self) -> str:
        return DCOM_CHECKLIST

    def get_architecture_diagram(self) -> str:
        return """Ubuntu OPC UA Server -> Windows UaGateway -> OPC Classic Client"""

    def generate_config_template(self, uaserver_ip: str, uaserver_port: int = 4840) -> str:
        endpoint = f"opc.tcp://{uaserver_ip}:{uaserver_port}/"
        return f"配置端点: {endpoint}"


opcua_gateway = OpcUaGatewayHelper()

__all__ = ['OpcUaGatewayHelper', 'opcua_gateway']