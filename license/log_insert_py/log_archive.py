#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Historical Log Archive Generator
Creates sparse archive files on firewall and indexes them in SQLite.
Run on device: /app/local/share/new_self_manage/

Usage:
  python log_archive.py --date 2026_01_01 --days 7 --file-size 50 --file-count 8 --table whitelist_log
"""

import os
import sys
import time
import sqlite3

ROOT = '/data/log_archive'
DEFAULT_SEQ_START = 900000


def parse_args():
    """Parse command line arguments"""
    params = {
        'date': time.strftime('%Y_%m_%d'),
        'days': 1,
        'file_size_gb': 50,
        'file_count': 8,
        'table_name': 'whitelist_log',
    }
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--date' and i + 1 < len(args):
            params['date'] = args[i + 1]
            i += 2
        elif args[i] == '--days' and i + 1 < len(args):
            params['days'] = int(args[i + 1])
            i += 2
        elif args[i] == '--file-size' and i + 1 < len(args):
            params['file_size_gb'] = int(args[i + 1])
            i += 2
        elif args[i] == '--file-count' and i + 1 < len(args):
            params['file_count'] = int(args[i + 1])
            i += 2
        elif args[i] == '--table' and i + 1 < len(args):
            params['table_name'] = args[i + 1]
            i += 2
        else:
            i += 1
    return params


def get_next_seq(conn, table_name):
    """获取下一个可用 seq，从 900000 起"""
    cur = conn.cursor()
    try:
        cur.execute("SELECT COALESCE(MAX(seq), 0) FROM archive_file_index WHERE table_name=?", (table_name,))
        row = cur.fetchone()
        max_seq = row[0] if row and row[0] else 0
        return max(DEFAULT_SEQ_START, max_seq) + 1
    except Exception:
        return DEFAULT_SEQ_START + 1


def main():
    params = parse_args()

    table_name = params['table_name']
    start_date = params['date']
    days = params['days']
    file_size = params['file_size_gb'] * 1024 * 1024 * 1024
    file_count = params['file_count']

    # 精简输出：一行参数概要
    print("%s, %s, %d天, %dGBx%d个/天" % (
        table_name, start_date, days, params['file_size_gb'], file_count))

    if not os.path.exists(ROOT):
        os.makedirs(ROOT)

    db_path = os.path.join(ROOT, 'archive_index.db')
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS archive_file_index (
            table_name TEXT, date_dir TEXT, file_name TEXT, file_path TEXT,
            seq INTEGER PRIMARY KEY,
            start_ts INTEGER, end_ts INTEGER, row_count INTEGER,
            file_size_bytes INTEGER, status INTEGER,
            created_at INTEGER, updated_at INTEGER
        )
    """)
    conn.commit()

    next_seq = get_next_seq(conn, table_name)
    first_seq = next_seq

    total_created = 0
    total_skipped = 0

    for day_offset in range(days):
        dt = time.strptime(start_date, '%Y_%m_%d')
        day_ts = int(time.mktime(dt)) + day_offset * 86400
        day_dt = time.localtime(day_ts)
        date_dir = time.strftime('%Y_%m_%d', day_dt)
        dir_path = os.path.join(ROOT, table_name, date_dir)

        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

        for i in range(1, file_count + 1):
            seq = next_seq
            next_seq += 1
            file_name = '%s_%s_test_%06d.csv' % (table_name, date_dir, seq - DEFAULT_SEQ_START)
            file_path = os.path.join(dir_path, file_name)

            if os.path.exists(file_path):
                total_skipped += 1
                continue

            with open(file_path, 'wb') as f:
                f.truncate(file_size)

            start_ts = day_ts
            end_ts = day_ts + 3600 + i
            now_ts = int(time.time())

            cur.execute("""
                INSERT OR REPLACE INTO archive_file_index(
                    table_name, date_dir, file_name, file_path, seq,
                    start_ts, end_ts, row_count, file_size_bytes,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                table_name, date_dir, file_name, file_path, seq,
                start_ts, end_ts, 1, file_size,
                1, now_ts, now_ts
            ))

            total_created += 1

        conn.commit()

    conn.close()

    total_size_gb = total_created * params['file_size_gb']
    last_seq = first_seq + total_created - 1
    if total_created > 0:
        print("seq: %d-%d, created %d files, %d GB" % (first_seq, last_seq, total_created, total_size_gb))
    if total_skipped > 0:
        print("skipped: %d files (already exist)" % total_skipped)
    print("Done")


if __name__ == '__main__':
    main()
