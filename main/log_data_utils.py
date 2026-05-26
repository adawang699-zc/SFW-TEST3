"""
日志数据批量生成工具模块

用于向防火墙设备的MySQL数据库批量导入测试日志数据。

支持的日志表：
- whitelist_log: 安全策略日志
- ipmac_log: IP/MAC日志
- blacklist_log: 黑名单日志
- attack_log: 攻击防护日志
- ip_session_log: IP会话日志
- tcp_session_log: TCP会话日志
- industry_log: 入侵防御日志
- virus_scan_log: 病毒扫描日志
- events: 系统日志
- operationlogs: 操作日志
"""

import paramiko
import time
import re
import logging
from typing import Dict, List, Any, Optional, Union

from .device_utils import execute_in_backend, execute_ssh_command, get_backend_password

logger = logging.getLogger('main')

# MySQL配置
MYSQL_USER = 'zhangc'
MYSQL_PASSWORD = '123456'
MYSQL_DATABASE = 'keystone'

# 表名与字段映射
NETWORK_LOG_FIELDS = [
    'oob_time_sec',
    'oob_time_usec',
    'oob_prefix',
    'ip_saddr',
    'ip_daddr',
    'ip_protocol',
    'tcp_sport',
    'tcp_dport',
    'udp_sport',
    'udp_dport',
    'icmp_type',
    'icmp_code',
    'deep_info',
    'mac_saddr',
    'mac_daddr',
    'eth_type',
    'prot_show',
    'log_type',
]

EVENTS_FIELDS = [
    'type',
    'sourceName',
    'sourceIp',
    'timestamp',
    'occurredDate',
    'occurredTime',
    'content',
    'level',
    'status',
    'deleted',
    'sysEventType',
    'componetName',
    'priority',
]

OPERATIONLOGS_FIELDS = [
    'operationName',
    'serviceType',
    'subServiceType',
    'operationStatus',
    'user_ip',
    'user',
    'timestamp',
    'deleted',
]

TABLE_FIELD_MAPPING = {
    'whitelist_log': NETWORK_LOG_FIELDS,
    'ipmac_log': NETWORK_LOG_FIELDS,
    'blacklist_log': NETWORK_LOG_FIELDS,
    'attack_log': NETWORK_LOG_FIELDS,
    'ip_session_log': NETWORK_LOG_FIELDS,
    'tcp_session_log': NETWORK_LOG_FIELDS,
    'industry_log': NETWORK_LOG_FIELDS,
    'virus_scan_log': NETWORK_LOG_FIELDS,
    'events': EVENTS_FIELDS,
    'operationlogs': OPERATIONLOGS_FIELDS,
}


def read_until(shell: paramiko.Channel, pattern: str, timeout: int = 60) -> str:
    """等待并读取直到匹配到pattern"""
    output = ''
    start_time = time.time()
    while time.time() - start_time < timeout:
        if shell.recv_ready():
            data = shell.recv(4096).decode('utf-8', errors='ignore')
            output += data
            if pattern in output:
                break
        time.sleep(0.1)
    output = re.sub(r'\x1b\[[0-9;]*m', '', output)
    return output


def generate_csv_on_device(
    shell: paramiko.Channel,
    table_name: str,
    count: int,
    days: int
) -> str:
    """在设备上生成CSV文件"""
    csv_file = f'/tmp/{table_name}_import.csv'
    logger.info(f'在设备上生成 {table_name} 表的 {count} 条CSV数据...')

    base_time_sec = int(time.time()) - days * 86400
    time_range_sec = days * 86400

    # 网络日志表的数据生成脚本
    if table_name in ['whitelist_log', 'ipmac_log', 'blacklist_log', 'attack_log',
                       'ip_session_log', 'tcp_session_log', 'industry_log', 'virus_scan_log']:
        gen_script = f'''cat > /tmp/gen_csv.sh << 'SCRIPT_EOF'
#!/bin/sh
COUNT=$1
BASE_TIME=$2
TIME_RANGE=$3
CSV_FILE=$4

awk -v count=$COUNT -v base=$BASE_TIME -v range=$TIME_RANGE 'BEGIN {{
    srand();
    for(i=0; i<count; i++) {{
        oob_time_sec = base + (i * 37 % range);
        oob_time_usec = i * 123 % 1000000;
        actions[0]="ACCEPT"; actions[1]="DROP"; actions[2]="REJECT"; actions[3]="LOG";
        oob_prefix = actions[int(rand()*4)];
        src_subnet = (i % 254) + 1;
        src_host = int(i / 254) % 254 + 1;
        dst_subnet = (i % 254) + 1;
        dst_host = int(i / 254) % 254 + 1;
        proto_idx = int(rand()*3);
        if(proto_idx == 0) {{
            ip_protocol = 6;
            tcp_sport = 1024 + (i % 64511);
            tcp_dport = int(rand()*1023) + 1;
            udp_sport = "\\N"; udp_dport = "\\N";
            icmp_type = "\\N"; icmp_code = "\\N";
            prot_show = "TCP";
        }} else if(proto_idx == 1) {{
            ip_protocol = 17;
            tcp_sport = "\\N"; tcp_dport = "\\N";
            udp_sport = 1024 + (i % 64511);
            udp_dport = int(rand()*9) + 1;
            icmp_type = "\\N"; icmp_code = "\\N";
            prot_show = "UDP";
        }} else {{
            ip_protocol = 1;
            tcp_sport = "\\N"; tcp_dport = "\\N";
            udp_sport = "\\N"; udp_dport = "\\N";
            icmp_type = i % 16; icmp_code = i % 5;
            prot_show = "ICMP";
        }}
        mac_saddr = sprintf("%02x:%02x:%02x:%02x:%02x:%02x", i%256, int(i/256)%256, int(i/65536)%256, int(i/16777216)%256, int(i/4294967296)%256, int(i/1099511627776)%256);
        mac_daddr = sprintf("%02x:%02x:%02x:%02x:%02x:%02x", (i+1000000)%256, int((i+1000000)/256)%256, int((i+1000000)/65536)%256, int((i+1000000)/16777216)%256, int((i+1000000)/4294967296)%256, int((i+1000000)/1099511627776)%256);
        log_type = int(rand()*5) + 1;
        deep_info = sprintf("log_%d_%d", i, int(rand()*9000)+1000);
        printf "%d\\t%d\\t%s\\t192.168.%d.%d\\t10.0.%d.%d\\t%d\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t2048\\t%s\\t%d\\n",
            oob_time_sec, oob_time_usec, oob_prefix, src_subnet, src_host, dst_subnet, dst_host,
            ip_protocol, tcp_sport, tcp_dport, udp_sport, udp_dport, icmp_type, icmp_code,
            deep_info, mac_saddr, mac_daddr, prot_show, log_type;
        if(i % 50000 == 0 && i > 0) printf "进度: %d\\n", i;
    }}
}}' > "$CSV_FILE"
SCRIPT_EOF
chmod +x /tmp/gen_csv.sh'''

        shell.send(gen_script + '\n')
        time.sleep(2)
        read_until(shell, '#', timeout=10)

        logger.info('开始生成CSV数据...')
        start_time = time.time()
        shell.send(f'sh /tmp/gen_csv.sh {count} {base_time_sec} {time_range_sec} {csv_file}\n')
        read_until(shell, '#', timeout=300)

        elapsed = time.time() - start_time
        logger.info(f'CSV生成完成，耗时 {elapsed:.2f} 秒')

    return csv_file


def execute_mysql_import(
    shell: paramiko.Channel,
    table_name: str,
    csv_file: str,
    truncate: bool = False,
    mysql_user: str = MYSQL_USER,
    mysql_password: str = MYSQL_PASSWORD,
    mysql_database: str = MYSQL_DATABASE
) -> Dict[str, Any]:
    """执行MySQL导入"""
    logger.info(f'导入 {table_name} 表...')

    shell.send(f"mysql -u{mysql_user} -p'{mysql_password}' --local-infile=1 {mysql_database}\n")
    time.sleep(2)
    output = read_until(shell, 'mysql>', timeout=15)

    if 'mysql>' not in output:
        logger.error(f'MySQL连接失败: {output[:200]}')
        return {'success': False, 'error': output}

    if truncate:
        logger.info('清空表...')
        shell.send(f'TRUNCATE TABLE {table_name};\n')
        time.sleep(1)
        read_until(shell, 'mysql>', timeout=10)

    # 导入前记录数
    shell.send(f'SELECT COUNT(*) FROM {table_name};\n')
    time.sleep(1)
    count_before_output = read_until(shell, 'mysql>', timeout=5)

    # 执行LOAD DATA INFILE
    fields = TABLE_FIELD_MAPPING.get(table_name, NETWORK_LOG_FIELDS)
    field_list = ', '.join(fields)

    load_sql = f"LOAD DATA LOCAL INFILE '{csv_file}' INTO TABLE {table_name} FIELDS TERMINATED BY '\\t' LINES TERMINATED BY '\\n' ({field_list});"

    logger.info('执行 LOAD DATA INFILE...')
    start_time = time.time()
    shell.send(load_sql + '\n')
    output = read_until(shell, 'mysql>', timeout=300)
    elapsed = time.time() - start_time

    if 'ERROR' in output:
        logger.error(f'导入失败: {output[:300]}')
        shell.send('exit;\n')
        time.sleep(1)
        read_until(shell, '#', timeout=3)
        return {'success': False, 'error': output[:300], 'elapsed': elapsed}

    # 导入后记录数
    shell.send(f'SELECT COUNT(*) FROM {table_name};\n')
    time.sleep(1)
    count_after_output = read_until(shell, 'mysql>', timeout=5)

    # 提取记录数
    try:
        lines_before = int(re.search(r'\|\s*(\d+)\s*\|', count_before_output).group(1))
        lines_after = int(re.search(r'\|\s*(\d+)\s*\|', count_after_output).group(1))
        imported = lines_after - lines_before
    except Exception:
        lines_before = 0
        lines_after = 0
        imported = 0

    shell.send('exit;\n')
    time.sleep(1)
    read_until(shell, '#', timeout=3)

    logger.info(f'导入完成: {imported} 条，耗时 {elapsed:.2f}秒')

    return {
        'success': True,
        'before': lines_before,
        'after': lines_after,
        'imported': imported,
        'elapsed': elapsed,
    }


def import_log_data(
    host: str,
    table_name: str,
    count: int = 100000,
    days: int = 7,
    truncate: bool = False,
    user: str = 'admin',
    password: str = '',
    backend_password: Optional[str] = None,
    device_type: Optional[str] = None,
    mysql_user: str = MYSQL_USER,
    mysql_password: str = MYSQL_PASSWORD
) -> Dict[str, Any]:
    """
    导入日志数据到指定表

    Args:
        host: 设备IP地址
        table_name: 表名（支持单个表或'all'）
        count: 数据量（条数）
        days: 时间范围（天数）
        truncate: 导入前是否清空表
        user: SSH用户名
        password: SSH密码
        backend_password: 后台密码
        device_type: 设备类型
        mysql_user: MySQL用户名
        mysql_password: MySQL密码

    Returns:
        导入结果字典
    """
    backend_pwd = get_backend_password(device_type, backend_password)

    logger.info(f'开始导入日志数据: {host}, 表={table_name}, 数量={count}')

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, 22, user, password, timeout=30)

        shell = ssh.invoke_shell()
        shell.settimeout(60)
        time.sleep(1)

        while shell.recv_ready():
            shell.recv(4096)

        shell.send('enter\n')
        time.sleep(1)
        shell.send(backend_pwd + '\n')
        time.sleep(2)

        while shell.recv_ready():
            shell.recv(4096)

        # 处理单个表或所有表
        if table_name == 'all':
            tables = list(TABLE_FIELD_MAPPING.keys())
            results = {}
            for table in tables:
                csv_file = generate_csv_on_device(shell, table, count, days)
                result = execute_mysql_import(shell, table, csv_file, truncate, mysql_user, mysql_password)
                results[table] = result
                shell.send(f'rm -f {csv_file}\n')
                time.sleep(0.5)
                read_until(shell, '#', timeout=3)
            shell.close()
            ssh.close()
            return {'success': True, 'results': results}
        else:
            csv_file = generate_csv_on_device(shell, table_name, count, days)
            result = execute_mysql_import(shell, table_name, csv_file, truncate, mysql_user, mysql_password)
            shell.send(f'rm -f {csv_file}\n')
            time.sleep(0.5)
            read_until(shell, '#', timeout=3)
            shell.close()
            ssh.close()
            return result

    except Exception as e:
        logger.error(f'导入失败: {e}')
        return {'success': False, 'error': str(e)}


def get_log_table_count(
    host: str,
    table_name: str = 'all',
    user: str = 'admin',
    password: str = '',
    backend_password: Optional[str] = None,
    device_type: Optional[str] = None,
    mysql_user: str = MYSQL_USER,
    mysql_password: str = MYSQL_PASSWORD,
    mysql_database: str = MYSQL_DATABASE
) -> Dict[str, int]:
    """
    获取日志表的记录数

    Args:
        host: 设备IP地址
        table_name: 表名（支持单个表或'all'）
        user: SSH用户名
        password: SSH密码
        backend_password: 后台密码
        device_type: 设备类型

    Returns:
        表名与记录数的映射
    """
    backend_pwd = get_backend_password(device_type, backend_password)

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, 22, user, password, timeout=30)

        shell = ssh.invoke_shell()
        shell.settimeout(60)
        time.sleep(1)

        while shell.recv_ready():
            shell.recv(4096)

        shell.send('enter\n')
        time.sleep(1)
        shell.send(backend_pwd + '\n')
        time.sleep(2)

        while shell.recv_ready():
            shell.recv(4096)

        shell.send(f"mysql -u{mysql_user} -p'{mysql_password}' {mysql_database}\n")
        time.sleep(2)
        read_until(shell, 'mysql>', timeout=15)

        tables = [table_name] if table_name != 'all' else list(TABLE_FIELD_MAPPING.keys())
        counts = {}

        for table in tables:
            shell.send(f'SELECT COUNT(*) FROM {table};\n')
            time.sleep(1)
            output = read_until(shell, 'mysql>', timeout=5)
            try:
                count = int(re.search(r'\|\s*(\d+)\s*\|', output).group(1))
                counts[table] = count
            except Exception:
                counts[table] = 0

        shell.send('exit;\n')
        time.sleep(1)
        shell.close()
        ssh.close()

        return counts

    except Exception as e:
        logger.error(f'获取记录数失败: {e}')
        return {}