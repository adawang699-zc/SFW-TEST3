"""
WebSocket Consumers for bandwidth test
"""
import json
import logging
from typing import Any, Dict
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger('djangoProject')


class BandwidthTestConsumer(AsyncWebsocketConsumer):
    """带宽测试WebSocket消费者"""

    async def connect(self) -> None:
        """WebSocket连接"""
        test_id = self.scope['url_route']['kwargs']['test_id']
        self.test_id = test_id
        self.group_name = f'bandwidth_{test_id}'

        # 加入频道组
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()
        logger.info(f"WebSocket连接建立: test_id={test_id}")

        # 启动iperf监控线程
        from main.bandwidth_utils import BandwidthTestMonitor
        self.monitor = BandwidthTestMonitor(test_id, self)
        self.monitor.start()

    async def disconnect(self, close_code: int) -> None:
        """WebSocket断开"""
        # 停止监控线程
        if hasattr(self, 'monitor'):
            self.monitor.stop()

        # 离开频道组（添加安全检查）
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
            logger.info(f"WebSocket断开: test_id={self.test_id}, code={close_code}")

    async def receive(self, text_data: str) -> None:
        """接收客户端消息"""
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'stop':
                # 停止测试
                logger.info(f"收到停止请求: test_id={self.test_id}")
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'未知操作: {action}'
                }))
                logger.warning(f"未知操作: {action}")

        except json.JSONDecodeError:
            logger.error(f"无效的JSON数据: {text_data}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': '无效的JSON格式'
            }))
        except Exception as e:
            logger.exception(f"处理消息异常: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': '服务器内部错误'
            }))

    async def iperf_data_message(self, event: Dict[str, Any]) -> None:
        """推送iperf数据"""
        await self.send(text_data=json.dumps(event['data']))

    async def test_complete_message(self, event: Dict[str, Any]) -> None:
        """推送测试完成"""
        await self.send(text_data=json.dumps(event['data']))

    async def error_message(self, event: Dict[str, Any]) -> None:
        """推送错误消息"""
        await self.send(text_data=json.dumps(event['data']))


class PortTestConsumer(AsyncWebsocketConsumer):
    """网口测试WebSocket消费者"""

    async def connect(self) -> None:
        """WebSocket连接"""
        test_id = self.scope['url_route']['kwargs']['test_id']
        self.test_id = test_id
        self.group_name = f'port_test_{test_id}'

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()
        logger.info(f"网口测试WebSocket连接: test_id={test_id}")

        # 启动测试监控线程
        from main.port_test_utils import PortTestMonitor
        self.monitor = PortTestMonitor(test_id, self)
        self.monitor.start()

    async def disconnect(self, close_code: int) -> None:
        """WebSocket断开"""
        if hasattr(self, 'monitor'):
            self.monitor.stop()

        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )
            logger.info(f"网口测试WebSocket断开: test_id={self.test_id}")

    async def receive(self, text_data: str) -> None:
        """接收客户端消息"""
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'stop':
                from main.port_test_utils import PortTestManager
                PortTestManager.stop_test(self.test_id)
                logger.info(f"收到停止请求: test_id={self.test_id}")
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'未知操作: {action}'
                }))
                logger.warning(f"未知操作: {action}")

        except json.JSONDecodeError:
            logger.error(f"无效的JSON数据: {text_data}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': '无效JSON格式'
            }))
        except Exception as e:
            logger.exception(f"处理消息异常: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': '服务器内部错误'
            }))

    async def scenario_result_message(self, event: Dict[str, Any]) -> None:
        """推送场景结果"""
        await self.send(text_data=json.dumps(event['data']))

    async def test_progress_message(self, event: Dict[str, Any]) -> None:
        """推送测试进度"""
        await self.send(text_data=json.dumps(event['data']))

    async def test_complete_message(self, event: Dict[str, Any]) -> None:
        """推送测试完成"""
        await self.send(text_data=json.dumps(event['data']))