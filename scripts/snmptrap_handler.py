#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP Trap Handler Script
用于 snmptrapd traphandle，将 TRAP 数据写入 JSON 文件

snmptrapd 传递参数:
  $1 = hostname (通常为空或 IP)
  $2 = IP address

stdin 格式 (snmptrapd 默认输出):
  UDP: [IP]:port->[IP]:port  (第一行是地址信息)
  OID TYPE VALUE
  例如:
  UDP: [10.40.20.41]:53074->[192.168.81.105]:162
  DISMAN-EVENT-MIB::sysUpTimeInstance = Timeticks: (77757618) 8 days, 23:59:36.18
"""

import sys
import json
import datetime
import os
import re

# Trap 日志文件
TRAP_LOG_FILE = '/var/log/snmptraps.json'


def parse_trap():
    """解析 snmptrapd 传递的 TRAP 数据"""
    trap_data = {}

    # 命令行参数: hostname 和 IP
    # snmptrapd 通常传递: $1=hostname(空), $2=IP
    if len(sys.argv) >= 3:
        trap_data['hostname'] = sys.argv[1] if sys.argv[1] else ''
        trap_data['source_ip'] = sys.argv[2]
    elif len(sys.argv) >= 2:
        trap_data['hostname'] = ''
        trap_data['source_ip'] = sys.argv[1] if sys.argv[1] else 'unknown'
    else:
        trap_data['hostname'] = ''
        trap_data['source_ip'] = 'unknown'

    trap_data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    trap_data['source_port'] = 0

    # 解析 stdin 中的变量绑定
    oid_values = []
    source_ip_from_udp = None

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # 检查是否是 UDP 地址行
        # 格式: UDP: [IP]:port->[IP]:port 或 UDP: [IP]:port->[IP]:port]:
        udp_match = re.match(r'UDP:\s*\[([^\]]+)\]:(\d+)->\[([^\]]+)\]:(\d+)', line)
        if udp_match:
            # 提取来源 IP
            source_ip_from_udp = udp_match.group(1)
            trap_data['source_ip'] = source_ip_from_udp
            trap_data['source_port'] = int(udp_match.group(2))
            continue

        # 解析 OID = TYPE: VALUE 格式
        # 例如: SNMPv2-MIB::sysUpTimeInstance = Timeticks: (77757618) 8 days, 23:59:36.18
        # 或: SNMPv2-MIB::snmpTrapOID.0 = OID: SNMPv2-SMI::enterprises.2345

        # 尝试匹配 "OID = TYPE: VALUE" 格式
        match = re.match(r'^(.+?)\s*=\s*(.+)$', line)
        if match:
            oid = match.group(1).strip()
            type_value = match.group(2).strip()

            # 分离 type 和 value
            # 格式可能是: "TYPE: VALUE" 或 "TYPE VALUE"
            type_match = re.match(r'^(\w+):\s*(.+)$', type_value)
            if type_match:
                type_str = type_match.group(1)
                value = type_match.group(2)
            else:
                # 可能 TYPE 和 VALUE 没有冒号分隔
                parts = type_value.split(None, 1)
                if len(parts) >= 2:
                    type_str = parts[0]
                    value = parts[1]
                else:
                    type_str = type_value
                    value = ''

            oid_values.append({
                'oid': oid,
                'type': type_str,
                'value': value
            })

    trap_data['oid_values'] = oid_values

    return trap_data


def main():
    """主函数"""
    try:
        trap = parse_trap()

        # 确保 log 目录存在
        log_dir = os.path.dirname(TRAP_LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # 写入 JSON 文件（追加模式，每行一个 JSON 对象）
        with open(TRAP_LOG_FILE, 'a') as f:
            json.dump(trap, f, ensure_ascii=False)
            f.write('\n')

    except Exception as e:
        # 错误写入 stderr（会出现在 snmptrapd 日志中）
        print(f'Error processing trap: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()