"""
带宽测试后端逻辑
管理iperf进程、轮询Agent获取真实数据、推送WebSocket数据
"""
import logging
import threading
import time
from datetime import datetime
from typing import Any
from django.conf import settings

logger = logging.getLogger('main')


class BandwidthTestManager:
    """带宽测试管理器"""

    active_tests = {}  # {test_id: {server_agent, client_agent, ...}}

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
            'bandwidth': test_params.get('bandwidth', 0),
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
            logger.warning("停止iperf server失败")

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
                logger.warning("停止iperf client失败")

        # 停止server
        server_agent = test_info.get('server_agent')
        if server_agent:
            cls._stop_iperf_server(server_agent)

        # 移除记录
        del cls.active_tests[test_id]

        logger.info(f"带宽测试已停止: test_id={test_id}")

        return {'success': True}


class BandwidthTestMonitor(threading.Thread):
    """带宽测试监控线程 - 轮询Agent获取真实iperf3数据"""

    def __init__(self, test_id: str, consumer: Any) -> None:
        super().__init__()
        self.test_id = test_id
        self.consumer = consumer
        self.running = True
        self.daemon = True

    def run(self) -> None:
        from asgiref.sync import async_to_sync

        if self.test_id not in BandwidthTestManager.active_tests:
            self._send_error('测试不存在')
            return

        test_info = BandwidthTestManager.active_tests[self.test_id]
        duration = test_info['duration']
        protocol = test_info.get('protocol', 'tcp')
        client_agent = test_info['client_agent']
        server_agent = test_info['server_agent']

        start_time = time.time()

        # 累计统计（发送端）
        sender_peak = 0.0
        sender_all_speeds = []
        sender_total_bytes = 0.0

        # 累计统计（接收端）
        receiver_peak = 0.0
        receiver_all_speeds = []
        receiver_total_bytes = 0.0

        try:
            while self.running and time.time() - start_time < duration + 5:
                elapsed = time.time() - start_time

                # 轮询两端 Agent 的 stats API
                sender_stats = self._poll_agent_stats(client_agent)
                receiver_stats = self._poll_agent_stats(server_agent)

                # 提取发送端数据
                sender_instant = sender_stats.get('instant_bps', 0)
                if sender_instant > 0:
                    sender_all_speeds.append(sender_instant)
                    sender_peak = max(sender_peak, sender_instant)
                    sender_total_bytes = sender_stats.get('total_bytes', 0)

                # 提取接收端数据
                receiver_instant = receiver_stats.get('instant_bps', 0)
                if receiver_instant > 0:
                    receiver_all_speeds.append(receiver_instant)
                    receiver_peak = max(receiver_peak, receiver_instant)
                    receiver_total_bytes = receiver_stats.get('total_bytes', 0)

                # 推送数据
                data = {
                    'sender_speed': round(sender_instant, 2),
                    'sender_avg': round(sum(sender_all_speeds) / len(sender_all_speeds), 2) if sender_all_speeds else 0,
                    'sender_peak': round(sender_peak, 2),
                    'sender_total': round(sender_total_bytes, 2),
                    'receiver_speed': round(receiver_instant, 2),
                    'receiver_avg': round(sum(receiver_all_speeds) / len(receiver_all_speeds), 2) if receiver_all_speeds else 0,
                    'receiver_peak': round(receiver_peak, 2),
                    'receiver_total': round(receiver_total_bytes, 2),
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

                # 检查是否结束
                if elapsed >= duration + 2 and not sender_stats.get('running', False):
                    break

                time.sleep(1)

            # 推送完成
            self._send_complete(sender_all_speeds, sender_peak, sender_total_bytes,
                                receiver_all_speeds, receiver_peak, receiver_total_bytes,
                                duration)

        except Exception as e:
            logger.exception(f"iperf监控异常: {e}")
            self._send_error(str(e))

        finally:
            BandwidthTestManager.stop_test(self.test_id)

    def stop(self) -> None:
        self.running = False

    def _poll_agent_stats(self, agent) -> dict:
        """轮询Agent的iperf stats API"""
        from main.views import forward_to_agent

        try:
            success, result, _ = forward_to_agent(
                agent, 'GET', '/api/iperf/stats',
                data=None, timeout=5
            )
            if success and result.get('success'):
                stats = result.get('stats', {})
                # 判断此Agent是server还是client
                if stats.get('server', {}).get('running'):
                    return stats['server']
                if stats.get('client', {}).get('running'):
                    return stats['client']
            return {}
        except Exception as e:
            logger.warning(f"轮询Agent stats失败: {e}")
            return {}

    def _send_complete(self, sender_speeds, sender_peak, sender_total,
                       receiver_speeds, receiver_peak, receiver_total,
                       duration) -> None:
        from asgiref.sync import async_to_sync

        sender_avg = sum(sender_speeds) / len(sender_speeds) if sender_speeds else 0.0
        receiver_avg = sum(receiver_speeds) / len(receiver_speeds) if receiver_speeds else 0.0

        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'test_complete_message',
                'data': {
                    'type': 'test_complete',
                    'summary': {
                        'sender_avg': round(sender_avg, 2),
                        'sender_peak': round(sender_peak, 2),
                        'sender_total': round(sender_total, 2),
                        'receiver_avg': round(receiver_avg, 2),
                        'receiver_peak': round(receiver_peak, 2),
                        'receiver_total': round(receiver_total, 2),
                        'duration': duration
                    }
                }
            }
        )

    def _send_error(self, message: str) -> None:
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
