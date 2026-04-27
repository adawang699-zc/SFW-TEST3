#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP Trap Handler Script
用于 snmptrapd traphandle，将 TRAP 数据写入 JSON 文件

snmptrapd traphandle stdin 格式 (和 -Lf 输出相同):
  时间戳 hostname [UDP: [IP]:port->[IP]:port]:
  OID = TYPE: VALUE
  ...

参数:
  $1 = hostname (通常为空)
  $2 = IP address
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
    trap_data = {
        'hostname': '',
        'source_ip': 'unknown',
        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source_port': 0,
        'oid_values': []
    }

    # 命令行参数: hostname 和 IP
    if len(sys.argv) >= 3:
        trap_data['hostname'] = sys.argv[1] if sys.argv[1] else ''
        trap_data['source_ip'] = sys.argv[2]
    elif len(sys.argv) >= 2:
        trap_data['source_ip'] = sys.argv[1] if sys.argv[1] else 'unknown'

    # 解析 stdin
    # snmptrapd 输出格式：
    #   第1行: 时间戳 hostname [UDP: [IP]:port->[IP]:port]:
    #   第2行: OID1 = TYPE1: VALUE1\tOID2 = TYPE2: VALUE2\t...
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # 忽略时间戳行（格式: "2026-04-27 12:23:16 hostname [UDP:...]:")
        # 这一行包含整个 header 信息，以冒号结尾
        if re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}', line):
            # 从 header 行提取 UDP 地址信息
            udp_match = re.search(r'\[UDP:\s*\[([^\]]+)\]:(\d+)->\[([^\]]+)\]:(\d+)\]', line)
            if udp_match:
                trap_data['source_ip'] = udp_match.group(1)
                trap_data['source_port'] = int(udp_match.group(2))
            # 提取 hostname（时间戳后、UDP前的部分）
            hostname_match = re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(\S+)\s+\[UDP:', line)
            if hostname_match:
                trap_data['hostname'] = hostname_match.group(1)
            continue

        # OID 数据行（可能用制表符分隔多个 OID）
        # 先按制表符分割成多个 OID 条目
        oid_parts = line.split('\t')

        for oid_part in oid_parts:
            oid_part = oid_part.strip()
            if not oid_part:
                continue

            # 解析 OID = TYPE: VALUE 格式
            # 有效 OID 格式: 以数字或 MIB 名称开头（如 .1.3.6 或 SNMPv2-MIB::）
            if not re.match(r'^[\d\.]+', oid_part) and not re.match(r'^[A-Za-z][A-Za-z0-9\-]*::', oid_part):
                continue

            match = re.match(r'^(.+?)\s*=\s*(.+)$', oid_part)
            if match:
                oid = match.group(1).strip()
                type_value = match.group(2).strip()

                # 分离 type 和 value
                type_match = re.match(r'^(\w+):\s*(.+)$', type_value)
                if type_match:
                    type_str = type_match.group(1)
                    value = type_match.group(2)
                else:
                    # 尝试空格分隔
                    parts = type_value.split(None, 1)
                    if len(parts) >= 2:
                        type_str = parts[0]
                        value = parts[1]
                    else:
                        type_str = type_value
                        value = ''

                trap_data['oid_values'].append({
                    'oid': oid,
                    'type': type_str,
                    'value': value
                })

    return trap_data


def main():
    """主函数"""
    try:
        trap = parse_trap()

        # 确保 log 目录存在
        log_dir = os.path.dirname(TRAP_LOG_FILE)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        # 写入 JSON 文件
        with open(TRAP_LOG_FILE, 'a') as f:
            json.dump(trap, f, ensure_ascii=False)
            f.write('\n')

    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()