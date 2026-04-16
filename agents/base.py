"""
Agent 基类 - 支持多实例、网卡绑定

Agent 从环境变量读取配置（systemd 传入）:
- AGENT_ID: Agent 标识，如 agent_eth1
- BIND_IP: 绑定的网卡 IP
- BIND_INTERFACE: 发送报文使用的网卡名
- AGENT_PORT: 监听端口
"""

import os
import logging
import threading
import time
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

logger = logging.getLogger('agents')


class BaseAgent:
    """
    Agent 基类

    所有 Agent 都继承此基类，提供:
    - 从环境变量读取配置
    - Flask HTTP API 服务
    - 状态查询接口
    - 网卡绑定信息
    """

    def __init__(self):
        # 从环境变量读取配置（systemd 传入）
        self.agent_id = os.environ.get('AGENT_ID', 'agent_eth0')
        self.bind_ip = os.environ.get('BIND_IP', '0.0.0.0')
        self.bind_interface = os.environ.get('BIND_INTERFACE', 'eth0')
        self.port = int(os.environ.get('AGENT_PORT', '8888'))

        # Flask 应用
        self.app = Flask(__name__)
        CORS(self.app, resources={r"/api/*": {"origins": "*"}})

        # 运行状态
        self.start_time = None
        self.running = True

        # 注册通用 API
        self._register_common_api()

        logger.info(f"Agent 初始化: {self.agent_id}")
        logger.info(f"绑定 IP: {self.bind_ip}, 网卡: {self.bind_interface}, 端口: {self.port}")

    def _register_common_api(self):
        """注册通用 API 接口"""

        @self.app.route('/api/status', methods=['GET'])
        def get_status():
            """Agent 状态查询"""
            return jsonify({
                'agent_id': self.agent_id,
                'bind_ip': self.bind_ip,
                'bind_interface': self.bind_interface,
                'port': self.port,
                'status': 'running' if self.running else 'stopped',
                'uptime': self._get_uptime(),
                'start_time': self.start_time.isoformat() if self.start_time else None
            })

        @self.app.route('/api/health', methods=['GET'])
        def health_check():
            """健康检查"""
            return jsonify({'status': 'healthy', 'agent_id': self.agent_id})

        @self.app.route('/api/interface_info', methods=['GET'])
        def interface_info():
            """返回网卡信息"""
            return jsonify({
                'agent_id': self.agent_id,
                'interface': self.bind_interface,
                'ip': self.bind_ip,
                'port': self.port
            })

        @self.app.route('/api/shutdown', methods=['POST'])
        def shutdown():
            """优雅关闭请求"""
            logger.info(f"收到关闭请求: {self.agent_id}")
            self.running = False
            return jsonify({'status': 'shutdown_requested', 'agent_id': self.agent_id})

    def _get_uptime(self):
        """获取运行时间（秒）"""
        if self.start_time:
            return int(time.time() - self.start_time.timestamp())
        return 0

    def register_api(self, route, handler, methods=['GET']):
        """注册自定义 API"""
        self.app.route(route, methods=methods)(handler)

    def json_response(self, data, status=200):
        """返回 JSON 响应"""
        return jsonify(data), status

    def start(self):
        """启动 Agent"""
        self.start_time = datetime.now()
        logger.info(f"Agent-{self.agent_id} 启动")
        logger.info(f"监听地址: http://{self.bind_ip}:{self.port}")

        # Flask 监听指定 IP 和端口
        self.app.run(
            host=self.bind_ip,
            port=self.port,
            threaded=True,
            use_reloader=False
        )

    def stop(self):
        """停止 Agent"""
        self.running = False
        logger.info(f"Agent-{self.agent_id} 停止")


if __name__ == '__main__':
    # 测试基类
    agent = BaseAgent()
    agent.start()