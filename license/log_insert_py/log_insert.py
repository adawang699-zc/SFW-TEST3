#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Log Data Generator - FAST version using LOAD DATA INFILE
Run on device: /app/local/share/new_self_manage/

Usage:
  python log_insert.py --table whitelist_log --count 1000000 --days 7 --truncate
  python log_insert.py --table whitelist_log --count 500000 --start-time "2025-01-01 00:00:00" --end-time "2025-01-07 23:59:59"
"""

import os
import sys
import time
from global_function.cmdline_oper import *
from global_function.global_var import *

db_proxy = DbProxy(DATA_DB_NAME)


def parse_datetime(dt_str):
    """Parse datetime string to epoch timestamp (compatible with Python 2/3)"""
    if not dt_str:
        return None
    formats = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]
    for fmt in formats:
        try:
            return int(time.mktime(time.strptime(dt_str, fmt)))
        except ValueError:
            continue
    return None


def generate_csv(csv_file, count, base_time, days):
    """Generate CSV file directly (tab-separated)"""
    time_range = days * 86400

    with open(csv_file, 'w') as f:
        for i in xrange(count):
            ts = base_time + (i * 37) % time_range
            ss = (i % 254) + 1
            sh = (i // 254) % 254 + 1
            ds = (i % 254) + 1
            dh = (i // 254) % 254 + 1
            sport = 1024 + (i % 64511)

            # IP hex: IPv4-mapped IPv6 format
            sip = "00000000000000000000ffffc0a8%02x%02x" % (ss, sh)
            dip = "00000000000000000000ffff0a00%02x%02x" % (ds, dh)

            # CSV line: ts\ttu\tprefix\tsip\tdip\tproto\tsport\t502\t\N\t\N\ticmp_type\ticmp_code\tdeep\tmac_s\tmac_d\t2048\tproto\tlog_type
            # Fields match LOAD DATA column order
            line = "%d\t0\tpass\t%s\t%s\t6\t%d\t502\t\\N\t\\N\t%d\t%d\tmodbus_proto%d\t00:00:00:00:01\t00:00:00:00:02\t2048\tmodbus\t2\n" % (
                ts, sip, dip, sport, i%16, i%5, i%100)
            f.write(line)

    return count


def main():
    count = 1000000
    days = 7
    truncate = False
    table_name = 'whitelist_log'
    start_time_str = None
    end_time_str = None

    # Parse args
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ['--count', '-c']:
            count = int(args[i+1])
            i += 2
        elif args[i] in ['--days', '-d']:
            days = int(args[i+1])
            i += 2
        elif args[i] in ['--truncate']:
            truncate = True
            i += 1
        elif args[i] == '--table':
            table_name = args[i+1]
            i += 2
        elif args[i] == '--start-time':
            start_time_str = args[i+1]
            i += 2
        elif args[i] == '--end-time':
            end_time_str = args[i+1]
            i += 2
        else:
            i += 1

    # Calculate time range
    if start_time_str and end_time_str:
        start_ts = parse_datetime(start_time_str)
        end_ts = parse_datetime(end_time_str)
        if start_ts and end_ts and end_ts > start_ts:
            days = max(1, (end_ts - start_ts) // 86400 + 1)
            base_time = start_ts
        else:
            base_time = int(time.time()) - days * 86400
    else:
        base_time = int(time.time()) - days * 86400

    csv_file = '/data/tmp/log_%s.csv' % table_name

    # 精简输出：只打印关键参数
    print("Table:    %s" % table_name)
    print("Count:    %d" % count)
    print("Days:     %d" % days)

    # Truncate
    if truncate:
        db_proxy.write_db("TRUNCATE TABLE %s" % table_name)
        print("(表已清空)")

    # Generate CSV + Import
    start = time.time()
    rows = generate_csv(csv_file, count, base_time, days)
    gen_time = time.time() - start

    load_sql = """LOAD DATA LOCAL INFILE '%s' INTO TABLE %s
FIELDS TERMINATED BY '\\t' LINES TERMINATED BY '\\n'
(@ts, @tu, @prefix, @sip, @dip, @proto, @sport, @dport, @usp, @udp, @it, @ic, @deep, @ms, @md, @eth, @ps, @lt)
SET
  oob_time_sec = @ts,
  oob_time_usec = @tu,
  oob_prefix = @prefix,
  ip_saddr = UNHEX(@sip),
  ip_daddr = UNHEX(@dip),
  ip_protocol = @proto,
  tcp_sport = @sport,
  tcp_dport = @dport,
  udp_sport = NULLIF(@usp, '\\N'),
  udp_dport = NULLIF(@udp, '\\N'),
  icmp_type = @it,
  icmp_code = @ic,
  deep_info = @deep,
  mac_saddr = @ms,
  mac_daddr = @md,
  eth_type = @eth,
  prot_show = @ps,
  log_type = @lt""" % (csv_file, table_name)

    db_proxy.write_db(load_sql)
    import_time = time.time() - start

    # Cleanup
    os.remove(csv_file)

    # Verify & compact output
    res, rows = db_proxy.read_db("SELECT COUNT(*) FROM %s" % table_name)
    total_time = gen_time + import_time
    if rows:
        print("Done, %d rows in %.1fs (%d rows/s), table total: %d rows" % (
            count, total_time, int(count/total_time), rows[0][0]))

if __name__ == '__main__':
    main()
