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
from main.device_utils import execute_in_backend
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
        interface: 网口名称

    Returns:
        dict: 网口信息, 包含 raw_output 字段存储原始输出
    """
    cmd = f"ethtool {interface}"
    # 使用 execute_in_backend 进入后台root执行命令
    output = execute_in_backend(
        cmd,
        device.ip,
        device.user,
        device.password,
        backend_password=device.backend_password,
        device_type=device.type,
        port=device.port
    )

    if output:
        parsed = parse_ethtool_output(output)
        parsed['raw_output'] = output
        return parsed

    return {'link': 'error', 'speed': 'error', 'duplex': 'error', 'autoneg': 'error', 'raw_output': ''}


def get_all_firewall_ports(device: TestDevice) -> Tuple[List[Dict[str, Any]], str]:
    """
    获取防火墙所有网口信息（优化版：一次SSH获取所有信息）

    Args:
        device: TestDevice对象

    Returns:
        tuple: (ports列表, 错误消息)
        ports: [{'name': 'eth0', 'link': 'up', ...}, ...]
        error_message: 空字符串表示成功，否则为错误描述
    """
    # 一次性获取所有网口的 ethtool 信息
    # 先列出所有网口，再逐个获取 ethtool 信息
    # 使用 for 循环批量执行，减少 SSH 连接次数
    cmd = """
for iface in $(ls /sys/class/net/); do
    echo "=== $iface ==="
    ethtool $iface 2>/dev/null | grep -E "Link detected|Speed|Duplex|Auto-negotiation"
done
"""
    # 使用 execute_in_backend 进入后台root执行命令
    output = execute_in_backend(
        cmd,
        device.ip,
        device.user,
        device.password,
        backend_password=device.backend_password,
        device_type=device.type,
        port=device.port
    )

    if output is False:
        return [], f"SSH连接失败: {device.ip}:{device.port}@{device.user}"

    if not output:
        return [], "无法获取网口列表"

    # 清理ANSI颜色代码
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    output = ansi_escape.sub('', output)

    # 解析输出
    ports_info = []
    current_iface = None
    current_info = {'link': 'unknown', 'speed': 'unknown', 'duplex': 'unknown', 'autoneg': 'unknown'}

    for line in output.split('\n'):
        line = line.strip()

        # 检测网口名称
        iface_match = re.match(r'=== (\S+) ===', line)
        if iface_match:
            # 保存上一个网口的信息
            if current_iface:
                ports_info.append({
                    'name': current_iface,
                    'link': current_info['link'],
                    'speed': current_info['speed'],
                    'duplex': current_info['duplex'],
                    'autoneg': current_info['autoneg']
                })
            # 开始新网口
            current_iface = iface_match.group(1)
            current_info = {'link': 'unknown', 'speed': 'unknown', 'duplex': 'unknown', 'autoneg': 'unknown'}
            continue

        # 解析 ethtool 输出
        if current_iface:
            link_match = re.search(r'Link detected:\s*(\w+)', line)
            if link_match:
                current_info['link'] = link_match.group(1).lower()

            speed_match = re.search(r'Speed:\s*(\d+Mb/?s|\d+Gb/?s)', line)
            if speed_match:
                current_info['speed'] = speed_match.group(1)

            duplex_match = re.search(r'Duplex:\s*(\w+)', line)
            if duplex_match:
                current_info['duplex'] = duplex_match.group(1)

            autoneg_match = re.search(r'Auto-negotiation:\s*(\w+)', line)
            if autoneg_match:
                current_info['autoneg'] = autoneg_match.group(1).lower()

    # 保存最后一个网口
    if current_iface:
        ports_info.append({
            'name': current_iface,
            'link': current_info['link'],
            'speed': current_info['speed'],
            'duplex': current_info['duplex'],
            'autoneg': current_info['autoneg']
        })

    # 过滤虚拟接口（在Python中过滤，支持所有物理网口格式）
    # 物理网口格式：eth*, ens*, enp*, s*p* (如 s0p1, s1p2), em*, p*p*
    # 虚拟接口：lo, docker, virbr, vnet, br, bond, team, tun, tap, vlan, ip6tnl, sit, teql, sw 等
    virtual_prefixes = [
        'lo', 'docker', 'docker0', 'virbr', 'vnet', 'br', 'br-', 'ifb',
        'bond', 'team', 'tun', 'tap', 'vlan', 'ip6tnl', 'sit', 'teql',
        'sw', 'Virtual', 'agl', 'ext', 'enx', 'usb'
    ]

    # 判断是否为物理网口的函数
    def is_physical_interface(iface):
        # 过滤虚拟接口前缀
        for prefix in virtual_prefixes:
            if iface.lower().startswith(prefix.lower()):
                return False
        # 过滤纯数字或太短的名称
        if iface.isdigit() or len(iface) < 2:
            return False
        # 物理网口格式匹配：
        # eth* (eth0, eth1, eth11...)
        # ens* (ens33, ens192...)
        # enp* (enp0s3, enp3s0...)
        # s*p* (s0p1, s1p0, s0p2...) - 扩展卡槽格式
        # em* (em1, em2...)
        # p*p* (p1p1, p2p2...)
        physical_patterns = [
            r'^eth\d+$',          # eth0, eth1, eth11
            r'^ens\d+$',          # ens33, ens192
            r'^enp\d+s\d+$',      # enp0s3, enp3s0
            r'^enp\d+$',          # enp0, enp1
            r'^s\d+p\d+$',        # s0p1, s1p0, s0p2 - 扩展卡槽
            r'^em\d+$',           # em1, em2
            r'^p\d+p\d+$',        # p1p1, p2p2
            r'^e\d+$',            # e0, e1
        ]
        for pattern in physical_patterns:
            if re.match(pattern, iface):
                return True
        # 如果不匹配任何已知格式，但有 ethtool 输出（link/speed/duplex），可能是物理网口
        return True  # 默认保留，让用户看到

    filtered_ports = [p for p in ports_info if is_physical_interface(p['name'])]

    # 按网口名称排序（支持 eth1, eth2... 和 s0p1, s0p2, s1p0... 格式）
    def sort_key(iface_name):
        # 提取前缀
        prefix = iface_name.rstrip('0123456789')
        # 处理 s0p1 格式：提取 s 作为前缀
        if 'p' in iface_name and iface_name.startswith('s'):
            prefix = 's'
        # 提取第一个数字（s后面的数字或eth后面的数字）
        first_num = int(re.search(r'\d+', iface_name).group()) if re.search(r'\d+', iface_name) else 0
        # 对于 s0p1 格式，提取 p 后的数字
        second_num = int(re.search(r'p(\d+)', iface_name).group(1)) if re.search(r'p(\d+)', iface_name) else 0
        return (prefix, first_num, second_num)

    filtered_ports.sort(key=lambda x: sort_key(x['name']))

    logger.info(f"设备 {device.ip} 网口列表: {len(filtered_ports)} 个网口")

    return filtered_ports, ''


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
    恢复Agent网口（UP + 自协商模式）

    Args:
        agent: LocalAgent对象
        interface: 网口名称

    Returns:
        (success, error_message)
    """
    # 先 UP 网口
    success, result, error = forward_to_agent(
        agent, 'POST', '/api/interface/up',
        data={'interface': interface},
        timeout=10
    )

    if not success:
        return False, f"UP网口失败: {error}"

    # 再配置为自协商模式
    return configure_agent_port(agent, interface, 'on', '1000', 'full')


class PortTestManager:
    """网口测试管理器"""

    active_tests = {}  # {test_id: {...}}
    _lock = threading.Lock()  # 线程安全锁

    @classmethod
    def start_topology_detection(cls, device: TestDevice,
                                  agents: List[LocalAgent]) -> Dict[str, Any]:
        """
        启动拓扑检测（无进度推送版本）
        """
        return cls.start_topology_detection_with_progress(device, agents, None)

    @classmethod
    def start_topology_detection_with_progress(cls, device: TestDevice,
                                                agents: List[LocalAgent],
                                                progress_callback: Optional[Any]) -> Dict[str, Any]:
        """
        启动拓扑检测（带进度推送，快速版）

        Args:
            device: 防火墙设备
            agents: Agent列表
            progress_callback: 进度推送函数

        Returns:
            dict: {'success': bool, 'mappings': [...], 'error': str}
        """
        try:
            import json

            # 0. 检查并恢复 Agent 网口状态（确保所有网口都是 UP）
            if progress_callback:
                progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'prepare', 'message': '检查 Agent 网口状态...'})}\n\n")

            for agent in agents:
                agent_iface = agent.interface.name if agent.interface else None
                if not agent_iface:
                    continue

                # 获取 Agent 网口状态
                success, result, error = forward_to_agent(
                    agent, 'GET', f'/api/interface/status?interface={agent_iface}',
                    timeout=5
                )

                if success and result:
                    # 如果网口是 DOWN，先 UP
                    if result.get('state') == 'DOWN' or result.get('operstate') == 'DOWN':
                        logger.info(f"Agent {agent.agent_id} 网口 {agent_iface} 是 DOWN，正在恢复...")
                        if progress_callback:
                            progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'restore', 'agent': agent.agent_id, 'message': f'恢复 {agent_iface} 为 UP 状态...'})}\n\n")

                        # UP 网口
                        success, result, error = forward_to_agent(
                            agent, 'POST', '/api/interface/up',
                            data={'interface': agent_iface},
                            timeout=5
                        )
                        if success:
                            logger.info(f"Agent {agent.agent_id} 网口 {agent_iface} 已恢复为 UP")
                        else:
                            logger.warning(f"恢复 Agent 网口失败: {error}")

            # 等待网口状态稳定
            time.sleep(2)

            # 1. 获取防火墙初始状态
            if progress_callback:
                progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'init', 'message': '获取防火墙初始网口状态...'})}\n\n")

            initial_ports, error = get_all_firewall_ports(device)
            if error:
                return {'success': False, 'error': f'获取防火墙网口失败: {error}'}

            # 记录每个网口的 LINK 状态
            initial_link_status = {p['name']: p['link'] for p in initial_ports}
            initial_up_ports = [p['name'] for p in initial_ports if p['link'] == 'yes']

            logger.info(f"防火墙初始状态: {initial_link_status}")
            logger.info(f"已连接网口: {initial_up_ports}")

            if progress_callback:
                progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'init_done', 'message': f'发现 {len(initial_up_ports)} 个已连接网口', 'up_ports': initial_up_ports})}\n\n")

            mappings = []

            # 2. 逐个DOWN Agent网口
            for idx, agent in enumerate(agents):
                agent_iface = agent.interface.name if agent.interface else None
                if not agent_iface:
                    logger.warning(f"Agent {agent.agent_id} 没有绑定网口")
                    continue

                if progress_callback:
                    progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'detect', 'agent': agent.agent_id, 'interface': agent_iface, 'message': f'正在检测 {agent.agent_id} ({agent_iface})...', 'index': idx, 'total': len(agents)})}\n\n")

                # DOWN Agent网口
                logger.info(f"尝试 DOWN Agent {agent.agent_id} 网口 {agent_iface}")
                success, result, error = forward_to_agent(
                    agent, 'POST', '/api/interface/down',
                    data={'interface': agent_iface},
                    timeout=5
                )

                logger.info(f"DOWN Agent 网口结果: success={success}, result={result}, error={error}")

                if not success:
                    logger.warning(f"DOWN Agent网口失败: {agent_iface}, error: {error}")
                    if progress_callback:
                        progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'down_failed', 'agent': agent.agent_id, 'message': f'DOWN网口失败: {error}'})}\n\n")
                    continue

                # 等待生效（确保网口状态变化传播）
                time.sleep(2)

                # 检查防火墙网口状态变化（只检查已连接网口，加快速度）
                logger.info(f"检查防火墙网口状态变化...")
                current_ports, error = get_all_firewall_ports(device)
                if error:
                    logger.warning(f"获取防火墙当前状态失败: {error}")
                    restore_agent_port(agent, agent_iface)
                    continue

                current_link_status = {p['name']: p['link'] for p in current_ports}
                logger.info(f"防火墙当前状态: {current_link_status}")

                # 检测变化：从 yes 变为 no
                found_mapping = False
                for port_name in initial_up_ports:
                    if initial_link_status.get(port_name) == 'yes' and current_link_status.get(port_name) == 'no':
                        # 发现映射关系
                        mapping = {
                            'agent_id': agent.agent_id,
                            'agent_interface': agent_iface,
                            'firewall_interface': port_name
                        }
                        mappings.append(mapping)
                        logger.info(f"发现映射: {agent_iface} -> {port_name}")

                        if progress_callback:
                            progress_callback(f"data: {json.dumps({'type': 'mapping_found', 'mapping': mapping, 'message': f'发现映射: {agent_iface} → {port_name}'})}\n\n")

                        found_mapping = True
                        # 只记录第一个匹配的
                        break

                if not found_mapping:
                    logger.info(f"Agent {agent_iface} DOWN 后未发现防火墙网口变化")
                    if progress_callback:
                        progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'no_change', 'agent': agent.agent_id, 'message': f'{agent_iface} DOWN 后无变化'})}\n\n")

                # 恢复Agent网口
                logger.info(f"恢复 Agent {agent.agent_id} 网口 {agent_iface}")
                restore_agent_port(agent, agent_iface)
                time.sleep(1)  # 等待恢复生效

            # 保存映射到数据库
            if mappings:
                if progress_callback:
                    progress_callback(f"data: {json.dumps({'type': 'progress', 'step': 'save', 'message': f'保存 {len(mappings)} 个映射到数据库...'})}\n\n")

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