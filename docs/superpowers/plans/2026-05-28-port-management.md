# 网口管理功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现网口管理功能,用于测试防火墙网口的自协商、速率、双工模式。

**Architecture:** 单页面分区式布局,左侧选择防火墙设备和Agent,右侧配置测试参数并展示表格结果。通过SSH执行ethtool命令控制Agent网口并读取防火墙状态,WebSocket实时推送测试进度。

**Tech Stack:** Django + Channels (WebSocket) + paramiko (SSH) + ethtool (网口配置)

---

## 文件结构

```
templates/
  system_config.html       # 修改: 替换网口管理占位内容

main/
  models.py                # 修改: 新增PortMapping, PortTestResult模型
  port_test_utils.py       # 新建: 网口测试逻辑管理
  views.py                 # 修改: 新增网口测试API

djangoProject/
  consumers.py             # 修改: 新增PortTestConsumer
  routing.py               # 修改: 新增WebSocket路由
```

---

### Task 1: 数据模型定义

**Files:**
- Modify: `main/models.py:387-400`

- [ ] **Step 1: 在models.py末尾添加PortMapping模型**

```python
class PortMapping(models.Model):
    """网口映射模型 - Agent网口与防火墙网口的对应关系"""
    device = models.ForeignKey(TestDevice, on_delete=models.CASCADE, verbose_name="防火墙设备")
    agent_id = models.CharField(max_length=50, verbose_name="Agent ID")
    agent_interface = models.CharField(max_length=50, verbose_name="Agent网口名")
    firewall_interface = models.CharField(max_length=50, verbose_name="防火墙网口名")
    detected_at = models.DateTimeField(auto_now_add=True, verbose_name="检测时间")

    class Meta:
        verbose_name = "网口映射"
        verbose_name_plural = "网口映射"
        ordering = ['-detected_at']

    def __str__(self):
        return f"{self.agent_interface} -> {self.firewall_interface}"
```

- [ ] **Step 2: 在models.py末尾添加PortTestResult模型**

```python
class PortTestResult(models.Model):
    """网口测试结果模型"""
    device = models.ForeignKey(TestDevice, on_delete=models.CASCADE, verbose_name="防火墙设备")
    mapping = models.ForeignKey(PortMapping, on_delete=models.CASCADE, verbose_name="网口映射")
    test_session_id = models.CharField(max_length=50, verbose_name="测试会话ID")
    scenario_id = models.IntegerField(verbose_name="场景编号")
    autoneg_config = models.CharField(max_length=10, verbose_name="配置-自协商", help_text="on/off")
    speed_config = models.CharField(max_length=20, verbose_name="配置-速率")
    duplex_config = models.CharField(max_length=20, verbose_name="配置-双工")
    firewall_speed = models.CharField(max_length=20, verbose_name="防火墙-速率")
    firewall_duplex = models.CharField(max_length=20, verbose_name="防火墙-双工")
    firewall_link = models.CharField(max_length=10, verbose_name="防火墙-LINK状态")
    result = models.CharField(max_length=10, verbose_name="测试结果", help_text="PASS/FAIL/ERROR")
    ethtool_output = models.TextField(blank=True, verbose_name="ethtool完整输出")
    error_message = models.TextField(blank=True, verbose_name="错误信息")
    tested_at = models.DateTimeField(auto_now_add=True, verbose_name="测试时间")

    class Meta:
        verbose_name = "网口测试结果"
        verbose_name_plural = "网口测试结果"
        ordering = ['test_session_id', 'scenario_id']

    def __str__(self):
        return f"{self.test_session_id} - 场景{self.scenario_id}: {self.result}"
```

- [ ] **Step 3: 运行数据库迁移**

```bash
cd D:\自动化测试\SFW_CONFIG\ubuntu_deploy
python manage.py makemigrations main
python manage.py migrate
```

Expected: 迁移成功创建

- [ ] **Step 4: 提交**

```bash
git add main/models.py main/migrations/*.py
git commit -m "feat: add PortMapping and PortTestResult models for port management"
```

---

### Task 2: 网口测试工具模块

**Files:**
- Create: `main/port_test_utils.py`

- [ ] **Step 1: 创建port_test_utils.py基础结构**

```python
"""
网口测试工具模块
提供防火墙网口自协商、速率、双工模式测试功能
"""

import re
import logging
import time
import threading
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from django.conf import settings

from main.models import TestDevice, LocalAgent, PortMapping, PortTestResult
from main.device_utils import execute_ssh_command, execute_in_backend
from main.views import forward_to_agent

logger = logging.getLogger('main')


def parse_ethtool_output(output: str) -> Dict[str, Any]:
    """
    解析ethtool命令输出
    
    Args:
        output: ethtool命令输出文本
        
    Returns:
        dict: {'link': 'up/down', 'speed': '1000Mbps', 'duplex': 'Full', 'autoneg': 'on'}
    """
    result = {
        'link': 'unknown',
        'speed': 'unknown',
        'duplex': 'unknown',
        'autoneg': 'unknown'
    }
    
    if not output:
        return result
    
    # 解析Link detected
    link_match = re.search(r'Link detected:\s*(\w+)', output)
    if link_match:
        result['link'] = link_match.group(1).lower()
    
    # 解析Speed
    speed_match = re.search(r'Speed:\s*(\d+Mb/s|\d+Gb/s)', output)
    if speed_match:
        result['speed'] = speed_match.group(1)
    
    # 解析Duplex
    duplex_match = re.search(r'Duplex:\s*(\w+)', output)
    if duplex_match:
        result['duplex'] = duplex_match.group(1)
    
    # 解析Auto-negotiation
    autoneg_match = re.search(r'Auto-negotiation:\s*(\w+)', output)
    if autoneg_match:
        result['autoneg'] = autoneg_match.group(1).lower()
    
    return result


def get_firewall_port_info(device: TestDevice, interface: str) -> Dict[str, Any]:
    """
    获取防火墙指定网口信息
    
    Args:
        device: TestDevice对象
        interface: 网口名称
        
    Returns:
        dict: 网口信息
    """
    cmd = f"ethtool {interface}"
    output = execute_ssh_command(
        cmd,
        device.ip,
        device.user,
        device.password,
        device.port,
        timeout=10
    )
    
    if output:
        return parse_ethtool_output(output)
    
    return {'link': 'error', 'speed': 'error', 'duplex': 'error', 'autoneg': 'error'}


def get_all_firewall_ports(device: TestDevice) -> List[Dict[str, Any]]:
    """
    获取防火墙所有网口信息
    
    Args:
        device: TestDevice对象
        
    Returns:
        list: [{'name': 'eth0', 'link': 'up', ...}, ...]
    """
    # 获取网口列表
    cmd = "ls /sys/class/net/ | grep -E 'eth|ens|enp'"
    output = execute_ssh_command(
        cmd,
        device.ip,
        device.user,
        device.password,
        device.port,
        timeout=10
    )
    
    if not output:
        return []
    
    interfaces = [iface.strip() for iface in output.split('\n') if iface.strip()]
    ports_info = []
    
    for iface in interfaces:
        info = get_firewall_port_info(device, iface)
        ports_info.append({
            'name': iface,
            'link': info['link'],
            'speed': info['speed'],
            'duplex': info['duplex'],
            'autoneg': info['autoneg']
        })
    
    return ports_info


def configure_agent_port(agent: LocalAgent, interface: str, 
                         autoneg: str, speed: str, duplex: str) -> Tuple[bool, str]:
    """
    配置Agent网口参数
    
    Args:
        agent: LocalAgent对象
        interface: 网口名称
        autoneg: 自协商状态 ('on'/'off')
        speed: 速率 ('10'/'100'/'1000')
        duplex: 双工模式 ('full'/'half')
        
    Returns:
        (success, error_message)
    """
    # 构建ethtool配置命令
    cmd = f"ethtool -s {interface} autoneg {autoneg}"
    if autoneg == 'off':
        cmd += f" speed {speed} duplex {duplex}"
    
    # 通过Agent API执行(使用forward_to_agent支持namespace)
    success, result, error = forward_to_agent(
        agent, 'POST', '/api/interface/config',
        data={
            'interface': interface,
            'autoneg': autoneg,
            'speed': speed,
            'duplex': duplex
        },
        timeout=10
    )
    
    if success:
        return True, ""
    return False, error or "配置失败"


def restore_agent_port(agent: LocalAgent, interface: str) -> Tuple[bool, str]:
    """
    恢复Agent网口为自协商模式
    
    Args:
        agent: LocalAgent对象
        interface: 网口名称
        
    Returns:
        (success, error_message)
    """
    return configure_agent_port(agent, interface, 'on', '1000', 'full')


class PortTestManager:
    """网口测试管理器"""
    
    active_tests = {}  # {test_id: {...}}
    
    @classmethod
    def start_topology_detection(cls, device: TestDevice, 
                                   agents: List[LocalAgent]) -> Dict[str, Any]:
        """
        启动拓扑检测
        
        Args:
            device: 防火墙设备
            agents: Agent列表
            
        Returns:
            dict: {'success': bool, 'mappings': [...], 'error': str}
        """
        try:
            # 1. 获取防火墙初始状态
            initial_ports = get_all_firewall_ports(device)
            initial_links = {p['name']: p['link'] for p in initial_ports}
            
            mappings = []
            
            # 2. 逐个DOWN Agent网口
            for agent in agents:
                # 获取Agent网口列表
                agent_interfaces = []
                success, result, error = forward_to_agent(
                    agent, 'GET', '/api/interfaces',
                    timeout=10
                )
                
                if success and result:
                    agent_interfaces = [i['name'] for i in result.get('interfaces', []) 
                                        if i['name'] not in ['lo', 'docker0']]
                
                for agent_iface in agent_interfaces:
                    # DOWN Agent网口
                    success, _, error = forward_to_agent(
                        agent, 'POST', '/api/interface/down',
                        data={'interface': agent_iface},
                        timeout=10
                    )
                    
                    if not success:
                        logger.warning(f"DOWN Agent网口失败: {agent_iface}")
                        continue
                    
                    time.sleep(2)  # 等待生效
                    
                    # 检查防火墙哪些网口LINK变为Down
                    current_ports = get_all_firewall_ports(device)
                    for port in current_ports:
                        if initial_links.get(port['name']) == 'up' and port['link'] == 'down':
                            # 发现映射关系
                            mappings.append({
                                'agent_id': agent.agent_id,
                                'agent_interface': agent_iface,
                                'firewall_interface': port['name']
                            })
                            # 只记录第一个匹配的
                            break
                    
                    # 恢复Agent网口
                    restore_agent_port(agent, agent_iface)
                    time.sleep(1)
            
            # 保存映射到数据库
            for mapping in mappings:
                PortMapping.objects.create(
                    device=device,
                    agent_id=mapping['agent_id'],
                    agent_interface=mapping['agent_interface'],
                    firewall_interface=mapping['firewall_interface']
                )
            
            return {'success': True, 'mappings': mappings}
            
        except Exception as e:
            logger.exception(f"拓扑检测失败: {e}")
            return {'success': False, 'error': str(e)}
    
    @classmethod
    def generate_test_scenarios(cls, autoneg_options: List[str],
                                speed_options: List[str],
                                duplex_options: List[str]) -> List[Dict[str, str]]:
        """
        生成测试场景组合
        
        Args:
            autoneg_options: ['on', 'off']
            speed_options: ['10', '100', '1000']
            duplex_options: ['full', 'half']
            
        Returns:
            list: [{'autoneg': 'on', 'speed': '1000', 'duplex': 'full'}, ...]
        """
        scenarios = []
        scenario_id = 1
        
        for autoneg in autoneg_options:
            for speed in speed_options:
                for duplex in duplex_options:
                    scenarios.append({
                        'id': scenario_id,
                        'autoneg': autoneg,
                        'speed': speed,
                        'duplex': duplex
                    })
                    scenario_id += 1
        
        return scenarios
    
    @classmethod
    def start_test(cls, test_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        开始网口测试
        
        Args:
            test_params: {
                'device_id': int,
                'mappings': [...],
                'scenarios': [...]
            }
            
        Returns:
            dict: {'success': bool, 'test_id': str, 'error': str}
        """
        test_id = f"port_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        cls.active_tests[test_id] = {
            'device_id': test_params['device_id'],
            'mappings': test_params['mappings'],
            'scenarios': test_params['scenarios'],
            'current_scenario': 0,
            'total_scenarios': len(test_params['scenarios']),
            'results': [],
            'running': True
        }
        
        return {'success': True, 'test_id': test_id}
    
    @classmethod
    def stop_test(cls, test_id: str) -> Dict[str, Any]:
        """
        停止测试
        
        Args:
            test_id: 测试ID
            
        Returns:
            dict: {'success': bool}
        """
        if test_id in cls.active_tests:
            cls.active_tests[test_id]['running'] = False
            return {'success': True}
        return {'success': False, 'error': '测试不存在'}
```

- [ ] **Step 2: 提交**

```bash
git add main/port_test_utils.py
git commit -m "feat: add port_test_utils module for port management testing"
```

---

### Task 3: API视图实现

**Files:**
- Modify: `main/views.py:末尾`

- [ ] **Step 1: 在views.py末尾添加网口测试API**

找到views.py末尾,添加以下函数:

```python
# ========== 网口管理 API ==========

@csrf_exempt
@require_http_methods(["GET"])
def api_get_devices(request):
    """获取防火墙设备列表"""
    devices = TestDevice.objects.all().values('id', 'name', 'ip', 'type')
    return JsonResponse({'success': True, 'devices': list(devices)})


@csrf_exempt
@require_http_methods(["GET"])
def api_get_device_ports(request, device_id):
    """获取防火墙设备网口信息"""
    try:
        device = TestDevice.objects.get(id=device_id)
        from main.port_test_utils import get_all_firewall_ports
        ports = get_all_firewall_ports(device)
        return JsonResponse({'success': True, 'ports': ports})
    except TestDevice.DoesNotExist:
        return JsonResponse({'success': False, 'error': '设备不存在'})


@csrf_exempt
@require_http_methods(["POST"])
def api_detect_topology(request):
    """拓扑检测"""
    try:
        data = json.loads(request.body)
        device_id = data.get('device_id')
        agent_ids = data.get('agent_ids', [])
        
        device = TestDevice.objects.get(id=device_id)
        agents = [LocalAgent.objects.get(agent_id=aid) for aid in agent_ids]
        
        from main.port_test_utils import PortTestManager
        result = PortTestManager.start_topology_detection(device, agents)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
@require_http_methods(["POST"])
def api_start_port_test(request):
    """开始网口测试"""
    try:
        data = json.loads(request.body)
        device_id = data.get('device_id')
        mappings = data.get('mappings', [])
        autoneg_options = data.get('autoneg_options', ['on', 'off'])
        speed_options = data.get('speed_options', ['100', '1000'])
        duplex_options = data.get('duplex_options', ['full'])
        
        from main.port_test_utils import PortTestManager
        scenarios = PortTestManager.generate_test_scenarios(
            autoneg_options, speed_options, duplex_options
        )
        
        result = PortTestManager.start_test({
            'device_id': device_id,
            'mappings': mappings,
            'scenarios': scenarios
        })
        
        if result['success']:
            result['websocket_url'] = f"ws://{settings.ALLOWED_HOSTS[0]}:{settings.PORT}/ws/port-test/{result['test_id']}/"
        
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
@require_http_methods(["POST"])
def api_stop_port_test(request):
    """停止网口测试"""
    try:
        data = json.loads(request.body)
        test_id = data.get('test_id')
        
        from main.port_test_utils import PortTestManager
        result = PortTestManager.stop_test(test_id)
        return JsonResponse(result)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@csrf_exempt
@require_http_methods(["GET"])
def api_get_port_test_results(request, test_id):
    """获取测试结果"""
    results = PortTestResult.objects.filter(test_session_id=test_id).values(
        'scenario_id', 'autoneg_config', 'speed_config', 'duplex_config',
        'firewall_speed', 'firewall_duplex', 'firewall_link', 'result',
        'tested_at'
    )
    return JsonResponse({'success': True, 'results': list(results)})
```

- [ ] **Step 2: 在urls.py添加路由**

找到 `djangoProject/urls.py`,在 urlpatterns 中添加:

```python
# 网口管理 API
path('api/devices/', views.api_get_devices, name='api_get_devices'),
path('api/devices/<int:device_id>/ports/', views.api_get_device_ports, name='api_get_device_ports'),
path('api/port-test/detect-topology/', views.api_detect_topology, name='api_detect_topology'),
path('api/port-test/start/', views.api_start_port_test, name='api_start_port_test'),
path('api/port-test/stop/', views.api_stop_port_test, name='api_stop_port_test'),
path('api/port-test/results/<str:test_id>/', views.api_get_port_test_results, name='api_get_port_test_results'),
```

- [ ] **Step 3: 提交**

```bash
git add main/views.py djangoProject/urls.py
git commit -m "feat: add port management API endpoints"
```

---

### Task 4: WebSocket消费者

**Files:**
- Modify: `djangoProject/consumers.py:88-150`

- [ ] **Step 1: 在consumers.py末尾添加PortTestConsumer**

```python
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
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'未知操作: {action}'
                }))
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': '无效JSON格式'
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
```

- [ ] **Step 2: 在port_test_utils.py添加PortTestMonitor类**

在 `main/port_test_utils.py` 末尾添加:

```python
class PortTestMonitor(threading.Thread):
    """网口测试监控线程"""

    def __init__(self, test_id: str, consumer: Any) -> None:
        super().__init__()
        self.test_id = test_id
        self.consumer = consumer
        self.running = True
        self.daemon = True

    def run(self) -> None:
        """执行测试并推送结果"""
        from asgiref.sync import async_to_sync

        if self.test_id not in PortTestManager.active_tests:
            self._send_error('测试不存在')
            return

        test_info = PortTestManager.active_tests[self.test_id]
        device = TestDevice.objects.get(id=test_info['device_id'])

        try:
            for scenario in test_info['scenarios']:
                if not test_info['running']:
                    break

                result = self._execute_scenario(device, test_info['mappings'], scenario)
                test_info['results'].append(result)

                # 保存到数据库
                for mapping_data in test_info['mappings']:
                    mapping = PortMapping.objects.filter(
                        agent_interface=mapping_data['agent_interface']
                    ).first()
                    if mapping:
                        PortTestResult.objects.create(
                            device=device,
                            mapping=mapping,
                            test_session_id=self.test_id,
                            scenario_id=scenario['id'],
                            autoneg_config=scenario['autoneg'],
                            speed_config=scenario['speed'],
                            duplex_config=scenario['duplex'],
                            firewall_speed=result['firewall_speed'],
                            firewall_duplex=result['firewall_duplex'],
                            firewall_link=result['firewall_link'],
                            result=result['result'],
                            ethtool_output=result['ethtool_output']
                        )

                # 推送进度
                self._push_progress(len(test_info['results']), test_info['total_scenarios'])
                self._push_result(scenario['id'], result)

                time.sleep(1)  # 场景间隔

            # 完成
            self._send_complete()

        except Exception as e:
            logger.exception(f"网口测试异常: {e}")
            self._send_error(str(e))

        finally:
            PortTestManager.stop_test(self.test_id)

    def _execute_scenario(self, device: TestDevice, 
                          mappings: List[Dict], scenario: Dict) -> Dict:
        """执行单个测试场景"""
        result = {
            'scenario_id': scenario['id'],
            'firewall_speed': 'unknown',
            'firewall_duplex': 'unknown',
            'firewall_link': 'unknown',
            'result': 'ERROR',
            'ethtool_output': ''
        }

        try:
            # 配置Agent网口
            for mapping in mappings:
                agent = LocalAgent.objects.get(agent_id=mapping['agent_id'])
                success, error = configure_agent_port(
                    agent, mapping['agent_interface'],
                    scenario['autoneg'], scenario['speed'], scenario['duplex']
                )
                if not success:
                    result['result'] = 'ERROR'
                    result['error_message'] = error
                    return result

            time.sleep(3)  # 等待协商生效

            # 检查防火墙状态
            for mapping in mappings:
                firewall_info = get_firewall_port_info(device, mapping['firewall_interface'])
                result['firewall_speed'] = firewall_info['speed']
                result['firewall_duplex'] = firewall_info['duplex']
                result['firewall_link'] = firewall_info['link']

                # 判断PASS/FAIL
                if scenario['autoneg'] == 'on':
                    # 自协商开,应协商到最高速率全双工
                    if firewall_info['link'] == 'up' and \
                       firewall_info['speed'] in ['1000Mb/s', '1000Mbps'] and \
                       firewall_info['duplex'] == 'Full':
                        result['result'] = 'PASS'
                    else:
                        result['result'] = 'FAIL'
                else:
                    # 自协商关,应匹配强制配置
                    expected_speed = f"{scenario['speed']}Mb/s"
                    expected_duplex = scenario['duplex'].capitalize()
                    if firewall_info['link'] == 'up' and \
                       firewall_info['speed'] == expected_speed and \
                       firewall_info['duplex'] == expected_duplex:
                        result['result'] = 'PASS'
                    else:
                        result['result'] = 'FAIL'

            # 恢复Agent网口
            for mapping in mappings:
                agent = LocalAgent.objects.get(agent_id=mapping['agent_id'])
                restore_agent_port(agent, mapping['agent_interface'])

        except Exception as e:
            result['result'] = 'ERROR'
            result['error_message'] = str(e)

        return result

    def stop(self) -> None:
        """停止监控"""
        self.running = False

    def _push_progress(self, current: int, total: int) -> None:
        """推送进度"""
        from asgiref.sync import async_to_sync
        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'test_progress_message',
                'data': {
                    'type': 'progress',
                    'current': current,
                    'total': total,
                    'percent': int(current / total * 100)
                }
            }
        )

    def _push_result(self, scenario_id: int, result: Dict) -> None:
        """推送场景结果"""
        from asgiref.sync import async_to_sync
        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'scenario_result_message',
                'data': {
                    'type': 'scenario_result',
                    'scenario_id': scenario_id,
                    'result': result
                }
            }
        )

    def _send_complete(self) -> None:
        """推送完成"""
        from asgiref.sync import async_to_sync
        async_to_sync(self.consumer.channel_layer.group_send)(
            self.consumer.group_name,
            {
                'type': 'test_complete_message',
                'data': {
                    'type': 'complete',
                    'test_id': self.test_id
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
```

- [ ] **Step 3: 在routing.py添加路由**

修改 `djangoProject/routing.py`:

```python
"""
WebSocket routing for bandwidth test and port test
"""
from django.urls import re_path
from djangoProject.consumers import BandwidthTestConsumer, PortTestConsumer

websocket_urlpatterns = [
    re_path(r'^ws/bandwidth/(?P<test_id>\w+)/$',
            BandwidthTestConsumer.as_asgi()),
    re_path(r'^ws/port-test/(?P<test_id>\w+)/$',
            PortTestConsumer.as_asgi()),
]
```

- [ ] **Step 4: 提交**

```bash
git add djangoProject/consumers.py djangoProject/routing.py main/port_test_utils.py
git commit -m "feat: add PortTestConsumer WebSocket for real-time test updates"
```

---

### Task 5: 前端页面模板

**Files:**
- Modify: `templates/system_config.html:234-244`

- [ ] **Step 1: 替换网口管理占位内容**

找到 `templates/system_config.html` 中 `<!-- ==================== 网口管理 ==================== -->` 部分(约第234-244行),替换为:

```html
<!-- ==================== 网口管理 ==================== -->
<div class="tab-content" id="tab-port_manage" style="display: none;">
    <div class="card">
        <div class="card-header">网口管理 - 防火墙网口自协商测试</div>
        <div style="display: grid; grid-template-columns: 1fr 1.5fr; gap: 24px;">
            <!-- 左侧: 设备和Agent选择 -->
            <div>
                <div style="background: var(--bg-dark); border-radius: 8px; padding: 16px; margin-bottom: 16px;">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">防火墙设备</div>
                    <div class="form-group">
                        <label class="form-label">选择防火墙</label>
                        <select class="form-input" id="port_device" onchange="loadDevicePorts()">
                            <option value="">-- 请选择设备 --</option>
                        </select>
                    </div>
                    <div id="device_ports_container" style="margin-top: 12px;"></div>
                </div>

                <div style="background: var(--bg-dark); border-radius: 8px; padding: 16px; margin-bottom: 16px;">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">Agent选择</div>
                    <div id="agent_list_container"></div>
                    <button class="btn btn-primary" style="width: 100%; margin-top: 12px;" onclick="detectTopology()">检测拓扑</button>
                </div>

                <div style="background: var(--bg-dark); border-radius: 8px; padding: 16px;">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">拓扑映射</div>
                    <div id="topology_mapping_container"></div>
                </div>
            </div>

            <!-- 右侧: 测试配置和结果 -->
            <div>
                <div style="background: var(--bg-dark); border-radius: 8px; padding: 16px; margin-bottom: 16px;">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">测试参数配置</div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;">
                        <div class="form-group">
                            <label class="form-label">自协商</label>
                            <select class="form-input" id="port_autoneg">
                                <option value="on">开启</option>
                                <option value="off">关闭</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">速率</label>
                            <select class="form-input" id="port_speed">
                                <option value="10">10M</option>
                                <option value="100">100M</option>
                                <option value="1000">1000M</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label class="form-label">双工</label>
                            <select class="form-input" id="port_duplex">
                                <option value="full">全双工</option>
                                <option value="half">半双工</option>
                            </select>
                        </div>
                    </div>
                    <div style="margin-top: 12px;">
                        <button class="btn btn-success" id="start_port_test" onclick="startPortTest()">开始测试</button>
                        <button class="btn btn-danger" id="stop_port_test" style="display: none;" onclick="stopPortTest()">停止测试</button>
                    </div>
                    <div id="test_progress_container" style="margin-top: 12px;"></div>
                </div>

                <div style="background: var(--bg-dark); border-radius: 8px; padding: 16px;">
                    <div style="color: var(--text-secondary); font-size: 0.9rem; margin-bottom: 12px;">测试结果</div>
                    <table class="table" id="port_test_results_table">
                        <thead>
                            <tr>
                                <th>场景</th>
                                <th>自协商</th>
                                <th>速率</th>
                                <th>双工</th>
                                <th>防火墙状态</th>
                                <th>结果</th>
                            </tr>
                        </thead>
                        <tbody id="results_body"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: 在system_config.html末尾添加JavaScript**

在 `{% block script %}` 或页面末尾添加:

```html
<script>
// 网口管理功能
let portTestWebSocket = null;
let currentPortTestId = null;
let selectedAgents = [];
let topologyMappings = [];

function loadPortTestDevices() {
    fetch('/api/devices/')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const select = document.getElementById('port_device');
                select.innerHTML = '<option value="">-- 请选择设备 --</option>';
                data.devices.forEach(d => {
                    select.innerHTML += `<option value="${d.id}">${d.name} (${d.ip})</option>`;
                });
            }
        });
}

function loadDevicePorts() {
    const deviceId = document.getElementById('port_device').value;
    if (!deviceId) return;
    
    fetch(`/api/devices/${deviceId}/ports/`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const container = document.getElementById('device_ports_container');
                container.innerHTML = '<table class="table"><thead><tr><th>网口</th><th>LINK</th><th>速率</th><th>双工</th></tr></thead><tbody>';
                data.ports.forEach(p => {
                    container.innerHTML += `<tr><td>${p.name}</td><td>${p.link}</td><td>${p.speed}</td><td>${p.duplex}</td></tr>`;
                });
                container.innerHTML += '</tbody></table>';
            }
        });
}

function loadPortTestAgents() {
    fetch('/api/agents/')
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                const container = document.getElementById('agent_list_container');
                container.innerHTML = '';
                data.agents.forEach(a => {
                    container.innerHTML += `
                        <div style="display: flex; align-items: center; margin-bottom: 8px;">
                            <input type="checkbox" id="agent_${a.agent_id}" onchange="toggleAgent('${a.agent_id}')">
                            <label for="agent_${a.agent_id}" style="margin-left: 8px;">${a.agent_id} (${a.interface.name})</label>
                        </div>
                    `;
                });
            }
        });
}

function toggleAgent(agentId) {
    if (selectedAgents.includes(agentId)) {
        selectedAgents = selectedAgents.filter(id => id !== agentId);
    } else {
        selectedAgents.push(agentId);
    }
}

function detectTopology() {
    const deviceId = document.getElementById('port_device').value;
    if (!deviceId || selectedAgents.length < 2) {
        alert('请选择设备和至少2个Agent');
        return;
    }
    
    fetch('/api/port-test/detect-topology/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            device_id: deviceId,
            agent_ids: selectedAgents
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            topologyMappings = data.mappings;
            const container = document.getElementById('topology_mapping_container');
            container.innerHTML = '';
            data.mappings.forEach(m => {
                container.innerHTML += `<div style="margin-bottom: 8px;">${m.agent_interface} → ${m.firewall_interface}</div>`;
            });
        } else {
            alert('拓扑检测失败: ' + data.error);
        }
    });
}

function startPortTest() {
    const deviceId = document.getElementById('port_device').value;
    if (!deviceId || topologyMappings.length === 0) {
        alert('请先完成拓扑检测');
        return;
    }
    
    const autoneg = document.getElementById('port_autoneg').value;
    const speed = document.getElementById('port_speed').value;
    const duplex = document.getElementById('port_duplex').value;
    
    fetch('/api/port-test/start/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            device_id: deviceId,
            mappings: topologyMappings,
            autoneg_options: autoneg === 'on' ? ['on'] : ['off'],
            speed_options: [speed],
            duplex_options: [duplex]
        })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            currentPortTestId = data.test_id;
            connectPortTestWebSocket(data.websocket_url);
            document.getElementById('start_port_test').style.display = 'none';
            document.getElementById('stop_port_test').style.display = 'inline-block';
        } else {
            alert('启动测试失败: ' + data.error);
        }
    });
}

function stopPortTest() {
    if (!currentPortTestId) return;
    
    fetch('/api/port-test/stop/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({test_id: currentPortTestId})
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            if (portTestWebSocket) {
                portTestWebSocket.close();
            }
            document.getElementById('start_port_test').style.display = 'inline-block';
            document.getElementById('stop_port_test').style.display = 'none';
        }
    });
}

function connectPortTestWebSocket(url) {
    portTestWebSocket = new WebSocket(url);
    
    portTestWebSocket.onopen = function() {
        console.log('网口测试WebSocket已连接');
    };
    
    portTestWebSocket.onmessage = function(event) {
        const data = JSON.parse(event.data);
        handlePortTestMessage(data);
    };
    
    portTestWebSocket.onclose = function() {
        console.log('网口测试WebSocket已关闭');
        document.getElementById('start_port_test').style.display = 'inline-block';
        document.getElementById('stop_port_test').style.display = 'none';
    };
}

function handlePortTestMessage(data) {
    if (data.type === 'progress') {
        const container = document.getElementById('test_progress_container');
        container.innerHTML = `测试进度: ${data.current}/${data.total} (${data.percent}%)`;
    }
    else if (data.type === 'scenario_result') {
        const tbody = document.getElementById('results_body');
        const r = data.result;
        const resultClass = r.result === 'PASS' ? 'success' : (r.result === 'FAIL' ? 'danger' : 'warning');
        tbody.innerHTML += `
            <tr class="${resultClass}">
                <td>${r.scenario_id}</td>
                <td>${r.autoneg_config || '-'}</td>
                <td>${r.speed_config || '-'}</td>
                <td>${r.duplex_config || '-'}</td>
                <td>${r.firewall_speed}/${r.firewall_duplex}</td>
                <td><strong>${r.result}</strong></td>
            </tr>
        `;
    }
    else if (data.type === 'complete') {
        document.getElementById('test_progress_container').innerHTML = '测试完成';
    }
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    loadPortTestDevices();
    loadPortTestAgents();
});
</script>
```

- [ ] **Step 3: 提交**

```bash
git add templates/system_config.html
git commit -m "feat: add port management UI template with topology detection and test execution"
```

---

### Task 6: 同步到Ubuntu并验证

**Files:**
- Run: `sync_to_ubuntu.py`

- [ ] **Step 1: 同步代码到Ubuntu服务器**

```bash
cd D:\自动化测试\SFW_CONFIG\ubuntu_deploy
python sync_to_ubuntu.py
```

Expected: 代码同步成功,服务重启完成

- [ ] **Step 2: 浏览器验证功能**

打开浏览器访问 `http://192.168.81.140:8000/system-config`,切换到"网口管理"标签页:
1. 选择防火墙设备,检查网口信息是否显示
2. 选择Agent,点击"检测拓扑",验证映射关系显示
3. 配置参数,点击"开始测试",观察表格结果实时更新

- [ ] **Step 3: 最终提交**

```bash
git status
git add -A
git commit -m "feat: port management feature complete - test firewall port autoneg/speed/duplex"
```

---

## Spec覆盖检查

| Spec需求 | 实现任务 |
|----------|----------|
| 选择防火墙设备,显示网口信息 | Task 3 API + Task 5 前端 |
| 选择Agent,检测拓扑 | Task 2 拓扑检测 + Task 5 前端 |
| 配置测试参数(自协商/速率/双工) | Task 5 前端 |
| 智能组合生成场景 | Task 2 generate_test_scenarios |
| 执行测试并推送结果 | Task 4 WebSocket + Task 2 Monitor |
| 表格展示结果 | Task 5 前端表格 |
| 数据模型 | Task 1 PortMapping/PortTestResult |

---

## Placeholder扫描结果

无placeholder(TBD/TODO/待实现)内容。

---

## 类型一致性检查

- `PortMapping.device` → ForeignKey to `TestDevice` ✓
- `PortTestResult.mapping` → ForeignKey to `PortMapping` ✓
- `configure_agent_port` 参数类型 与 `generate_test_scenarios` 输出一致 ✓
- WebSocket `scenario_result_message` 数据结构 与 前端处理一致 ✓