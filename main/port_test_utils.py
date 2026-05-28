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
from asgiref.sync import async_to_sync

from main.models import TestDevice, LocalAgent, PortMapping, PortTestResult
from main.device_utils import execute_ssh_command
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

    # 解析Speed (支持 Mb/s, Mbps, Gb/s, Gbps 格式)
    speed_match = re.search(r'Speed:\s*(\d+Mb/?s|\d+Gb/?s)', output)
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
        interface: 口名称

    Returns:
        dict: 网口信息, 包含 raw_output 字段存储原始输出
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
        parsed = parse_ethtool_output(output)
        parsed['raw_output'] = output
        return parsed

    return {'link': 'error', 'speed': 'error', 'duplex': 'error', 'autoneg': 'error', 'raw_output': ''}


def get_all_firewall_ports(device: TestDevice) -> Tuple[List[Dict[str, Any]], str]:
    """
    获取防火墙所有网口信息

    Args:
        device: TestDevice对象

    Returns:
        tuple: (ports列表, 错误消息)
        ports: [{'name': 'eth0', 'link': 'up', ...}, ...]
        error_message: 空字符串表示成功，否则为错误描述
    """
    # 获取网口列表 (包括各种命名模式: eth, ens, enp, eno, enx, etc.)
    cmd = "ls /sys/class/net/ | grep -vE 'lo|docker|virbr|vnet|br|wlan'"
    output = execute_ssh_command(
        cmd,
        device.ip,
        device.user,
        device.password,
        device.port,
        timeout=10
    )

    if output is False:
        return [], f"SSH连接失败: {device.ip}:{device.port}@{device.user}"

    if not output:
        return [], "无法获取网口列表"

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

    return ports_info, ''


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
    _lock = threading.Lock()  # 线程安全锁

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

        with cls._lock:
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
        with cls._lock:
            if test_id in cls.active_tests:
                cls.active_tests[test_id]['running'] = False
                return {'success': True}
        return {'success': False, 'error': '测试不存在'}


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
                            ethtool_output=result['ethtool_output'],
                            error_message=result.get('error_message', '')
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
            'ethtool_output': '',
            'error_message': ''
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
                result['ethtool_output'] = firewall_info.get('raw_output', '')

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

        except Exception as e:
            result['result'] = 'ERROR'
            result['error_message'] = str(e)

        finally:
            # 恢复Agent网口 (无论成功或异常都要恢复)
            for mapping in mappings:
                try:
                    agent = LocalAgent.objects.get(agent_id=mapping['agent_id'])
                    restore_agent_port(agent, mapping['agent_interface'])
                except Exception as restore_error:
                    logger.warning(f"恢复Agent网口失败: {restore_error}")

        return result

    def stop(self) -> None:
        """停止监控"""
        self.running = False

    def _push_progress(self, current: int, total: int) -> None:
        """推送进度"""
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