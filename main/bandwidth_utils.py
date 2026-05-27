"""
带宽测试后端逻辑
管理iperf进程、解析输出、推送WebSocket数据
"""
import re
import logging
import threading
import time
import requests
from datetime import datetime
from django.conf import settings

logger = logging.getLogger('main')


class BandwidthTestManager:
    """带宽测试管理器"""

    active_tests = {}  # {test_id: {server_pid, client_pid, ...}}

    @classmethod
    def start_test(cls, test_params, user_identifier):
        """启动带宽测试

        Args:
            test_params: dict包含server_agent_id, client_agent_id等参数
            user_identifier: 用户标识符

        Returns:
            dict: {success, test_id, error}
        """
        from main.models import LocalAgent, AgentLock

        server_agent_id = test_params.get('server_agent_id')
        client_agent_id = test_params.get('client_agent_id')

        # 1. 验证用户租用了两个Agent
        lock = AgentLock.objects.filter(
            user_identifier=user_identifier,
            status='active'
        ).first()

        if not lock:
            return {'success': False, 'error': '请先租用Agent后再进行带宽测试'}

        locked_agent_ids = [a.agent_id for a in lock.agents.all()]

        if server_agent_id not in locked_agent_ids or client_agent_id not in locked_agent_ids:
            return {'success': False, 'error': '只能测试自己租用的Agent'}

        # 2. 获取Agent对象和IP
        try:
            server_agent = LocalAgent.objects.get(agent_id=server_agent_id)
            client_agent = LocalAgent.objects.get(agent_id=client_agent_id)
        except LocalAgent.DoesNotExist:
            return {'success': False, 'error': 'Agent不存在'}

        server_ip = server_agent.interface.ip_address
        client_ip = client_agent.interface.ip_address

        if not server_ip or not client_ip:
            return {'success': False, 'error': 'Agent IP未配置，请先在Agent管理页面配置IP'}

        # 3. 检查Agent是否运行
        if server_agent.status != 'running' or client_agent.status != 'running':
            return {'success': False, 'error': 'Agent未运行，请先启动Agent'}

        # 4. 生成test_id
        test_id = f"bw_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 5. 获取Agent端口
        server_port = server_agent.port
        client_port = client_agent.port

        # 6. 启动iperf server (通过Agent API)
        iperf_port = test_params.get('port', 5201)

        try:
            resp = requests.post(
                f"http://{server_ip}:{server_port}/api/iperf/server/start",
                json={'port': iperf_port},
                timeout=10
            )
            result = resp.json()

            if not result.get('success'):
                return {'success': False, 'error': f"iperf server启动失败: {result.get('error')}"}

            server_pid = result.get('pid')

        except Exception as e:
            return {'success': False, 'error': f"iperf server启动失败: {str(e)}"}

        # 7. 启动iperf client (通过Agent API)
        try:
            client_params = {
                'server_ip': server_ip,
                'port': iperf_port,
                'duration': test_params.get('duration', 10),
                'protocol': test_params.get('protocol', 'tcp'),
                'mtu': test_params.get('mtu', 1400),
            }

            if test_params.get('protocol') == 'udp' and test_params.get('bandwidth'):
                client_params['bandwidth'] = test_params.get('bandwidth')

            resp = requests.post(
                f"http://{client_ip}:{client_port}/api/iperf/client/start",
                json=client_params,
                timeout=10
            )
            result = resp.json()

            if not result.get('success'):
                # 清理server
                cls._stop_iperf_server(server_ip, server_port)
                return {'success': False, 'error': f"iperf client启动失败: {result.get('error')}"}

            client_pid = result.get('pid')

        except Exception as e:
            # 清理server
            cls._stop_iperf_server(server_ip, server_port)
            return {'success': False, 'error': f"iperf client启动失败: {str(e)}"}

        # 8. 记录活跃测试
        cls.active_tests[test_id] = {
            'server_agent_id': server_agent_id,
            'client_agent_id': client_agent_id,
            'server_ip': server_ip,
            'client_ip': client_ip,
            'server_port': server_port,
            'client_port': client_port,
            'server_pid': server_pid,
            'client_pid': client_pid,
            'iperf_port': iperf_port,
            'user_identifier': user_identifier,
            'start_time': datetime.now(),
            'duration': test_params.get('duration', 10),
        }

        logger.info(f"带宽测试启动成功: test_id={test_id}")

        return {
            'success': True,
            'test_id': test_id,
            'websocket_url': f"ws://{settings.ALLOWED_HOSTS[0]}/ws/bandwidth/{test_id}/"
        }

    @classmethod
    def _stop_iperf_server(cls, server_ip, server_port):
        """停止iperf server"""
        try:
            requests.post(
                f"http://{server_ip}:{server_port}/api/iperf/server/stop",
                timeout=5
            )
        except Exception as e:
            logger.warning(f"停止iperf server失败: {e}")

    @classmethod
    def stop_test(cls, test_id):
        """停止带宽测试"""
        if test_id not in cls.active_tests:
            return {'success': False, 'error': '测试不存在'}

        test_info = cls.active_tests[test_id]

        # 停止client
        try:
            requests.post(
                f"http://{test_info['client_ip']}:{test_info['client_port']}/api/iperf/client/stop",
                timeout=5
            )
        except Exception as e:
            logger.warning(f"停止iperf client失败: {e}")

        # 停止server
        cls._stop_iperf_server(test_info['server_ip'], test_info['server_port'])

        # 移除记录
        del cls.active_tests[test_id]

        logger.info(f"带宽测试已停止: test_id={test_id}")

        return {'success': True}

    @classmethod
    def parse_iperf_output(cls, line):
        """解析iperf单行输出

        Returns:
            dict: {instant_speed, avg_speed, peak_speed, total_bytes, interval, transfer}
        """
        # iperf3 输出格式: [  5]   1.00-2.00   sec  12.5 MBytes  125.6 Mbits/sec
        pattern = r'\[\s*\d+\]\s+(\d+\.\d+)-(\d+\.\d+)\s+sec\s+(\d+\.\d+)\s+(MBytes|KBytes|GBytes)\s+(\d+\.\d+)\s+(Mbits/sec|Kbits/sec|Gbits/sec)'

        match = re.search(pattern, line)
        if not match:
            return None

        interval_start = float(match.group(1))
        interval_end = float(match.group(2))
        transfer = float(match.group(3))
        transfer_unit = match.group(4)
        bandwidth = float(match.group(5))
        bandwidth_unit = match.group(6)

        # 转换单位
        if transfer_unit == 'KBytes':
            transfer = transfer / 1024  # -> MBytes
        elif transfer_unit == 'GBytes':
            transfer = transfer * 1024  # -> MBytes

        if bandwidth_unit == 'Kbits/sec':
            bandwidth = bandwidth / 1000  # -> Mbits/sec
        elif bandwidth_unit == 'Gbits/sec':
            bandwidth = bandwidth * 1000  # -> Mbits/sec

        return {
            'interval': interval_end - interval_start,
            'transfer': transfer,  # MBytes
            'instant_speed': bandwidth,  # Mbits/sec
            'avg_speed': 0.0,  # 后续计算
            'peak_speed': 0.0,  # 后续计算
            'total_bytes': 0,  # 后续计算
        }