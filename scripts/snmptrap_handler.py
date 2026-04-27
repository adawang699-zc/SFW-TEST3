#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNMP Trap Handler Script
用于 snmptrapd traphandle，将 TRAP 数据写入 JSON 文件

snmptrapd traphandle 参数:
  $1 = hostname
  $2 = IP address

stdin 格式 (每行一个 OID 绑定):
  OID TYPE VALUE
  例如:
  .1.3.6.1.2.1.1.3.0 Timeticks "(12345) 0:02:03.45"
  .1.3.6.1.6.3.1.1.4.1.0 OID ".1.3.6.1.4.1.12345"

参考: http://www.net-snmp.org/docs/man/snmptrapd.conf.html
"""

import sys
import json
import datetime
import os

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
    # $1 = hostname, $2 = IP address
    if len(sys.argv) >= 3:
        trap_data['hostname'] = sys.argv[1] if sys.argv[1] else ''
        trap_data['source_ip'] = sys.argv[2]
    elif len(sys.argv) >= 2:
        trap_data['source_ip'] = sys.argv[1] if sys.argv[1] else 'unknown'

    # 解析 stdin 中的变量绑定
    # 格式: OID TYPE VALUE (每行一个)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # 分割为 OID, TYPE, VALUE
        parts = line.split(None, 2)  # 分割成最多 3 部分
        if len(parts) >= 3:
            oid = parts[0]
            type_str = parts[1]
            value = parts[2]
        elif len(parts) == 2:
            oid = parts[0]
            type_str = parts[1]
            value = ''
        elif len(parts) == 1:
            oid = parts[0]
            type_str = ''
            value = ''
        else:
            continue

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