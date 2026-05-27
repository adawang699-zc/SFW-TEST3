"""
WebSocket Consumers for bandwidth test
"""
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger('djangoProject')


class BandwidthTestConsumer(AsyncWebsocketConsumer):
    """带宽测试WebSocket消费者"""

    async def connect(self):
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

    async def disconnect(self, close_code):
        """WebSocket断开"""
        # 离开频道组
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        logger.info(f"WebSocket断开: test_id={self.test_id}, code={close_code}")

    async def receive(self, text_data):
        """接收客户端消息"""
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'stop':
                # 停止测试
                logger.info(f"收到停止请求: test_id={self.test_id}")

        except json.JSONDecodeError:
            logger.error(f"无效的JSON数据: {text_data}")

    async def iperf_data_message(self, event):
        """推送iperf数据"""
        await self.send(text_data=json.dumps(event['data']))

    async def test_complete_message(self, event):
        """推送测试完成"""
        await self.send(text_data=json.dumps(event['data']))

    async def error_message(self, event):
        """推送错误消息"""
        await self.send(text_data=json.dumps(event['data']))