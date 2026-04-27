#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP Trap Handler Script
用于 snmptrapd traphandle，将 TRAP 数据写入 JSON 文件

snmptrapd traphandle stdin 格式（与 -Lf 输出不同）:
  hostname
  UDP: [IP]:port->[IP]:port
  OID value
  OID value
  ...

参数:
  $1 = 无（traphandle 只传递脚本路径）
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

    # 解析 stdin
    lines = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            lines.append(line)

    if len(lines) >= 1:
        # 第一行：hostname
        trap_data['hostname'] = lines[0]

    if len(lines) >= 2:
        # 第二行：UDP 地址
        # 格式: UDP: [IP]:port->[IP]:port
        udp_match = re.match(r'UDP:\s*\[([^\]]+)\]:(\d+)->\[([^\]]+)\]:(\d+)', lines[1])
        if udp_match:
            trap_data['source_ip'] = udp_match.group(1)
            trap_data['source_port'] = int(udp_match.group(2))

    # 剩余行：OID value（没有等号，没有类型）
    for i in range(2, len(lines)):
        oid_line = lines[i].strip()
        if not oid_line:
            continue

        # 格式: OID value (空格分隔)
        # 例如: sysUpTimeInstance 10:2:42:08.51
        # 例如: snmpTrapOID.0 coldStart
        parts = oid_line.split(None, 1)
        if len(parts) >= 2:
            oid = parts[0]
            value = parts[1]
        else:
            oid = oid_line
            value = ''

        trap_data['oid_values'].append({
            'oid': oid,
            'type': 'Unknown',
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