#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OPC UA 公共定义
包含数据模拟模式、模拟数据生成函数、常量定义
"""

import math
import random
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import deque

logger = logging.getLogger(__name__)

# OPC UA 默认端口
OPCUA_PORT = 4840

# OPC UA 命名空间
OPCUA_NAMESPACE = "http://opcua.simulator.local"

# 数据模拟模式类型
SIMULATION_MODES = {
    'sine': '正弦波',
    'random': '随机波动',
    'step': '阶梯波',
    'toggle': '定时切换',
    'counter': '计数器',
    'constant': '常量'
}

# 默认变量配置
DEFAULT_VARIABLES = [
    {
        'name': 'Temperature',
        'type': 'Float',
        'mode': 'sine',
        'params': {'base': 25.0, 'amplitude': 5.0, 'period': 60.0},
        'description': '温度 (正弦波)'
    },
    {
        'name': 'Pressure',
        'type': 'Float',
        'mode': 'random',
        'params': {'base': 100.0, 'range': 10.0},
        'description': '压力 (随机波动)'
    },
    {
        'name': 'Flow',
        'type': 'Float',
        'mode': 'step',
        'params': {'steps': [40.0, 50.0, 60.0, 70.0], 'step_interval': 30.0},
        'description': '流量 (阶梯波)'
    },
    {
        'name': 'Speed',
        'type': 'Float',
        'mode': 'sine',
        'params': {'base': 1500.0, 'amplitude': 200.0, 'period': 30.0},
        'description': '转速 (正弦波)'
    },
    {
        'name': 'Level',
        'type': 'Float',
        'mode': 'random',
        'params': {'base': 50.0, 'range': 15.0},
        'description': '液位 (随机波动)'
    },
    {
        'name': 'SwitchState',
        'type': 'Boolean',
        'mode': 'toggle',
        'params': {'interval': 120.0},
        'description': '开关状态'
    },
    {
        'name': 'AlarmActive',
        'type': 'Boolean',
        'mode': 'alarm',
        'params': {'trigger_threshold': 30.0, 'clear_threshold': 28.0},
        'description': '报警状态'
    },
    {
        'name': 'Counter',
        'type': 'Int32',
        'mode': 'counter',
        'params': {'max': 1000},
        'description': '计数器'
    },
    {
        'name': 'Mode',
        'type': 'Int32',
        'mode': 'constant',
        'params': {'value': 1},
        'description': '运行模式'
    }
]


def generate_simulated_value(config: Dict, elapsed_time: float) -> Any:
    """
    根据配置生成模拟数据值

    Args:
        config: 变量配置字典，包含 mode 和 params
        elapsed_time: 从启动开始经过的时间（秒）

    Returns:
        生成的值
    """
    mode = config.get('mode', 'constant')
    params = config.get('params', {})

    if mode == 'sine':
        # 正弦波: base + amplitude * sin(2π * elapsed / period)
        base = params.get('base', 0.0)
        amplitude = params.get('amplitude', 1.0)
        period = params.get('period', 60.0)
        return base + amplitude * math.sin(2 * math.pi * elapsed_time / period)

    elif mode == 'random':
        # 随机波动: base + random(-range, range)
        base = params.get('base', 0.0)
        range_val = params.get('range', 1.0)
        return base + random.uniform(-range_val, range_val)

    elif mode == 'step':
        # 阶梯波: 按 step_interval 切换到不同的 step 值
        steps = params.get('steps', [0.0])
        step_interval = params.get('step_interval', 30.0)
        step_idx = int(elapsed_time / step_interval) % len(steps)
        return steps[step_idx]

    elif mode == 'toggle':
        # 定时切换: 每 interval 秒切换布尔值
        interval = params.get('interval', 60.0)
        return int(elapsed_time / interval) % 2 == 1

    elif mode == 'counter':
        # 计数器: 循环计数到 max
        max_val = params.get('max', 100)
        return int(elapsed_time) % max_val

    elif mode == 'constant':
        # 常量
        return params.get('value', 0)

    elif mode == 'alarm':
        # 报警状态: 由外部逻辑更新，返回 None 表示需要特殊处理
        return None

    return 0


def check_alarm_trigger(temperature: float, alarm_config: Dict) -> Optional[str]:
    """
    检查温度是否触发/清除报警

    Args:
        temperature: 当前温度值
        alarm_config: 报警配置

    Returns:
        'trigger' - 触发报警
        'clear' - 清除报警
        None - 无变化
    """
    trigger_threshold = alarm_config.get('trigger_threshold', 30.0)
    clear_threshold = alarm_config.get('clear_threshold', 28.0)

    if temperature > trigger_threshold:
        return 'trigger'
    elif temperature < clear_threshold:
        return 'clear'
    return None


class HistoryBuffer:
    """历史数据缓冲区"""

    def __init__(self, max_size: int = 10000):
        self._buffer: deque = deque(maxlen=max_size)
        self._max_size = max_size

    def append(self, value: Any, quality: str = 'Good'):
        """添加历史记录"""
        self._buffer.append({
            'timestamp': datetime.now().isoformat(),
            'value': value,
            'quality': quality
        })

    def get_records(self, count: int = None) -> List[Dict]:
        """获取历史记录"""
        if count is None:
            return list(self._buffer)
        return list(self._buffer)[-count:]

    def get_records_by_time(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """按时间范围获取历史记录"""
        result = []
        for record in self._buffer:
            ts = datetime.fromisoformat(record['timestamp'])
            if start_time <= ts <= end_time:
                result.append(record)
        return result

    def clear(self):
        """清空缓冲"""
        self._buffer.clear()

    def size(self) -> int:
        """获取当前记录数"""
        return len(self._buffer)


# 检测 asyncua 是否可用
OPCUA_AVAILABLE = False
OPCUA_LIB_ERROR = ""

try:
    import asyncua
    from asyncua import Server, Client, ua
    OPCUA_AVAILABLE = True
    logger.info("asyncua 导入成功 - OPC UA 可用")
except ImportError as e:
    OPCUA_LIB_ERROR = str(e)
    logger.warning(f"asyncua 未安装 - OPC UA 不可用: {e}")


def get_install_instructions() -> str:
    """获取安装指南"""
    return "pip install asyncua"


# 导出的类和函数
__all__ = [
    'OPCUA_PORT',
    'OPCUA_NAMESPACE',
    'OPCUA_AVAILABLE',
    'OPCUA_LIB_ERROR',
    'SIMULATION_MODES',
    'DEFAULT_VARIABLES',
    'generate_simulated_value',
    'check_alarm_trigger',
    'HistoryBuffer',
    'get_install_instructions',
]