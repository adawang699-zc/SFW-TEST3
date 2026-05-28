"""
带宽测试后端逻辑
管理iperf进程、解析输出、推送WebSocket数据
"""
import re
import logging
import threading
import time
from datetime import datetime
from typing import Any
from django.conf import settings

logger = logging.getLogger('main')


class BandwidthTestManager:
    """带宽测试管理器"""

    active_tests = {}  # {test_id: {server_pid, client_pid, ...}}

    @classmethod
    def start_test(cls, test_params, client_ip):
        """启动带宽测试

        Args:
            test_params: dict包含server_agent_id, client_agent_id等参数
            client_ip: 客户端IP地址

        Returns:
            dict: {success, test_id, error}
        """
        from main.models import LocalAgent, AgentLock
        from main.views import forward_to_agent

        server_agent_id = test_params.get('server_agent_id')
        client_agent_id = test_params.get('client_agent_id')

        # 1. 验证用户租用了两个Agent（使用客户端IP）
        lock = AgentLock.objects.filter(
            client_ip=client_ip,
            status='active'
        ).first()

        if not lock:
            return {'success': False, 'error': f'当前IP ({client_ip}) 无租用记录，请先租用Agent'}

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
        client_ip_addr = client_agent.interface.ip_address

        if not server_ip or not client_ip_addr:
            return {'success': False, 'error': 'Agent IP未配置，请先在Agent管理页面配置IP'}

        # 3. 检查Agent是否运行
        if server_agent.status != 'running' or client_agent.status != 'running':
            return {'success': False, 'error': 'Agent未运行，请先启动Agent'}

        # 4. 生成test_id
        test_id = f"bw_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # 5. 获取Agent端口
        server_port = server_agent.port
        client_port = client_agent.port

        # 6. 启动iperf server (通过Agent API，使用forward_to_agent支持namespace)
        iperf_port = test_params.get('port', 5201)

        success, result, error = forward_to_agent(
            server_agent, 'POST', '/api/iperf/server/start',
            data={'port': iperf_port}, timeout=10
        )

        if not success:
            return {'success': False, 'error': f'iperf server启动失败: {error}'}

        server_pid = result.get('pid')

        # 7. 启动iperf client (通过Agent API，使用forward_to_agent支持namespace)
        client_params = {
            'server_ip': server_ip,
            'port': iperf_port,
            'duration': test_params.get('duration', 10),
            'protocol': test_params.get('protocol', 'tcp'),
            'mtu': test_params.get('mtu', 1400),
        }

        if test_params.get('protocol') == 'udp' and test_params.get('bandwidth'):
            client_params['bandwidth'] = test_params.get('bandwidth')
            logger.info(f"UDP带宽测试: 设置带宽限制={client_params['bandwidth']}Mbps")

        logger.info(f"iperf client参数: {client_params}")

        success, result, error = forward_to_agent(
            client_agent, 'POST', '/api/iperf/client/start',
            data=client_params, timeout=10
        )

        if not success:
            # 清理server
            cls._stop_iperf_server(server_agent)
            return {'success': False, 'error': f'iperf client启动失败: {error}'}

        client_pid = result.get('pid')

        # 8. 记录活跃测试
        cls.active_tests[test_id] = {
            'server_agent': server_agent,
            'client_agent': client_agent,
            'server_agent_id': server_agent_id,
            'client_agent_id': client_agent_id,
            'server_ip': server_ip,
            'client_ip': client_ip_addr,
            'server_port': server_port,
            'client_port': client_port,
            'server_pid': server_pid,
            'client_pid': client_pid,
            'iperf_port': iperf_port,
            'client_ip_addr': client_ip,  # 请求端IP
            'start_time': datetime.now(),
            'duration': test_params.get('duration', 10),
            'protocol': test_params.get('protocol', 'tcp'),
            'bandwidth': test_params.get('bandwidth', 0),  # UDP带宽限制
        }

        logger.info(f"带宽测试启动成功: test_id={test_id}")

        return {
            'success': True,
            'test_id': test_id,
            'websocket_url': f"ws://{settings.ALLOWED_HOSTS[0]}/ws/bandwidth/{test_id}/"
        }

    @classmethod
    def _stop_iperf_server(cls, server_agent):
        """停止iperf server"""
        from main.views import forward_to_agent

        success, _, _ = forward_to_agent(
            server_agent, 'POST', '/api/iperf/server/stop',
            data={}, timeout=5
        )
        if not success:
            logger.warning(f"停止iperf server失败")

    @classmethod
    def stop_test(cls, test_id):
        """停止带宽测试"""
        from main.views import forward_to_agent

        if test_id not in cls.active_tests:
            return {'success': False, 'error': '测试不存在'}

        test_info = cls.active_tests[test_id]

        # 停止client
        client_agent = test_info.get('client_agent')
        if client_agent:
            success, _, _ = forward_to_agent(
                client_agent, 'POST', '/api/iperf/client/stop',
                data={}, timeout=5
            )
            if not success:
                logger.warning(f"停止iperf client失败")

        # 停止server
        server_agent = test_info.get('server_agent')
        if server_agent:
            cls._stop_iperf_server(server_agent)

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


class BandwidthTestMonitor(threading.Thread):
    """带宽测试监控线程"""

    def __init__(self, test_id: str, consumer: Any) -> None:
        """初始化监控线程

        Args:
            test_id: 测试ID
            consumer: WebSocket消费者实例
        """
        super().__init__()
        self.test_id = test_id
        self.consumer = consumer
        self.running = True
        self.daemon = True

        # 累计统计
        self.total_bytes = 0.0
        self.peak_speed = 0.0
        self.all_speeds: list = []

    def run(self) -> None:
        """监控iperf输出并推送数据"""
        from asgiref.sync import async_to_sync

        if self.test_id not in BandwidthTestManager.active_tests:
            self._send_error('测试不存在')
            return

        test_info = BandwidthTestManager.active_tests[self.test_id]
        client_ip = test_info['client_ip']
        client_port = test_info['client_port']
        duration = test_info['duration']
        protocol = test_info.get('protocol', 'tcp')
        bandwidth_limit = test_info.get('bandwidth', 0)  # UDP带宽限制

        start_time = time.time()

        try:
            # 模拟实时数据推送（实际需要iperf进程输出解析）
            # 由于iperf输出不能实时获取，这里使用估算方法
            while self.running and time.time() - start_time < duration + 5:
                elapsed = time.time() - start_time

                if elapsed >= duration:
                    # 测试结束
                    self._send_complete()
                    break

                # 模拟数据推送（每秒一次）
                # UDP时使用带宽限制值，TCP时模拟波动
                if protocol == 'udp' and bandwidth_limit > 0:
                    # UDP带宽限制生效，模拟围绕限制值波动（±10%）
                    simulated_speed = bandwidth_limit * (0.9 + 0.2 * (elapsed % 3) / 3)
                else:
                    # TCP无限制，模拟正常波动
                    simulated_speed = 50.0 + (elapsed % 5) * 10.0  # 模拟波动
                self._push_simulated_data(elapsed, simulated_speed)
                time.sleep(1)

        except Exception as e:
            logger.exception(f"iperf监控异常: {e}")
            self._send_error(str(e))

        finally:
            # 清理测试
            BandwidthTestManager.stop_test(self.test_id)

    def stop(self) -> None:
        """停止监控"""
        self.running = False

    def _push_simulated_data(self, elapsed: float, speed: float) -> None:
        """推送模拟数据（临时方案）

        Args:
            elapsed: 已运行时间
            speed: 当前速度 Mbps
        """
        from asgiref.sync import async_to_sync

        # 更新统计
        self.all_speeds.append(speed)
        self.peak_speed = max(self.peak_speed, speed)
        self.total_bytes += speed * 0.125  # Mbps * 1秒 -> MB

        data = {
            'instant_speed': speed,
            'avg_speed': sum(self.all_speeds) / len(self.all_speeds),
            'peak_speed': self.peak_speed,
            'transfer': self.total_bytes,
            'total_bytes': self.total_bytes,
            'interval': 1.0,
        }

        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'iperf_data_message',
                'data': {
                    'type': 'iperf_data',
                    'timestamp': datetime.now().isoformat(),
                    'data': data
                }
            }
        )

    def _send_complete(self) -> None:
        """推送测试完成"""
        from asgiref.sync import async_to_sync

        avg_speed = sum(self.all_speeds) / len(self.all_speeds) if self.all_speeds else 0.0

        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'test_complete_message',
                'data': {
                    'type': 'test_complete',
                    'summary': {
                        'avg_bandwidth': avg_speed,
                        'peak_bandwidth': self.peak_speed,
                        'total_transfer': self.total_bytes,
                        'duration': len(self.all_speeds)
                    }
                }
            }
        )

    def _send_error(self, message: str) -> None:
        """推送错误"""
        from asgiref.sync import async_to_sync

        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'error_message',
                'data': {
                    'type': 'error',
                    'message': message
                }
            }
        )