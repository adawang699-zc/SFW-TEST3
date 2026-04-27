#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP Trap Handler Script
用于 snmptrapd traphandle，将 TRAP 数据写入 JSON 文件

snmptrapd stdin 格式:
  2026-04-27 12:18:39 localhost [UDP: [127.0.0.1]:37795->[127.0.0.1]:162]:
  OID = TYPE: VALUE\tOID = TYPE: VALUE  (多个 OID 用 Tab 分隔)
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
        trap_data['hostname'] = ''
        trap_data['source_ip'] = sys.argv[1] if sys.argv[1] else 'unknown'

    # 解析 stdin
    lines = sys.stdin.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 解析 UDP 地址行
        # 格式: 2026-04-27 12:18:39 localhost [UDP: [IP]:port->[IP]:port]:
        udp_match = re.search(r'\[UDP:\s*\[([^\]]+)\]:(\d+)->\[([^\]]+)\]:(\d+)\]', line)
        if udp_match:
            trap_data['source_ip'] = udp_match.group(1)
            trap_data['source_port'] = int(udp_match.group(2))
            continue

        # OID 数据可能用 Tab 分隔多个
        # 格式: OID = TYPE: VALUE\tOID = TYPE: VALUE
        parts = line.split('\t')

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # 解析 OID = TYPE: VALUE
            match = re.match(r'^(.+?)\s*=\s*(.+)$', part)
            if match:
                oid = match.group(1).strip()
                type_value = match.group(2).strip()

                # 分离 type 和 value
                # 格式: "TYPE: VALUE" 或 "TYPE VALUE"
                type_match = re.match(r'^(\w+):\s*(.+)$', type_value)
                if type_match:
                    type_str = type_match.group(1)
                    value = type_match.group(2)
                else:
                    # 尝试其他格式
                    space_match = re.match(r'^(\w+)\s+(.+)$', type_value)
                    if space_match:
                        type_str = space_match.group(1)
                        value = space_match.group(2)
                    else:
                        type_str = 'Unknown'
                        value = type_value

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

        # 写入 JSON 文件（追加模式，每行一个 JSON 对象）
        with open(TRAP_LOG_FILE, 'a') as f:
            json.dump(trap, f, ensure_ascii=False)
            f.write('\n')

    except Exception as e:
        print(f'Error processing trap: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()