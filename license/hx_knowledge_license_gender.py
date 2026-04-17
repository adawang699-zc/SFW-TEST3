#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
知识库授权工具演示版本
用于测试授权管理功能
"""

import argparse
import json
import sys
import os
from datetime import datetime

def generate_license(json_data, output_file):
    """生成授权文件"""
    try:
        # 解析JSON数据
        data = json.loads(json_data)
        
        machine_code = data.get('machinecode', '')
        vul_expire = data.get('vul_expire', 30)
        virus_expire = data.get('virus_expire', 60)
        rules_expire = data.get('rules_expire', 50)
        
        if not machine_code:
            print("[ERROR] 缺少机器码参数", file=sys.stderr)
            return 1
        
        # 创建授权文件内容
        license_content = f"""# 知识库授权文件
# 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
# 机器码: {machine_code}

[LICENSE]
machinecode={machine_code}
vul_expire={vul_expire}
virus_expire={virus_expire}
rules_expire={rules_expire}
generated_time={datetime.now().isoformat()}
status=valid
"""
        
        # 确保输出目录存在
        output_dir = os.path.dirname(output_file)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # 写入授权文件
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(license_content)
        
        print(f"[SUCCESS] 授权文件已生成: {output_file}")
        return 0
        
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON解析错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[ERROR] 生成授权文件失败: {e}", file=sys.stderr)
        return 1

def decrypt_license(input_file):
    """解密授权文件"""
    try:
        if not os.path.exists(input_file):
            print(f"[ERROR] 授权文件不存在: {input_file}", file=sys.stderr)
            return 1
        
        # 读取授权文件
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        print("[INFO] 授权文件内容:")
        print("-" * 40)
        print(content)
        print("-" * 40)
        print("[SUCCESS] 授权文件解密完成")
        return 0
        
    except Exception as e:
        print(f"[ERROR] 解密授权文件失败: {e}", file=sys.stderr)
        return 1

def main():
    parser = argparse.ArgumentParser(description='知识库授权工具演示版本')
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # 生成授权命令
    gen_parser = subparsers.add_parser('gen', help='生成授权文件')
    gen_parser.add_argument('--json', required=True, help='JSON格式的授权数据')
    gen_parser.add_argument('-o', '--output', required=True, help='输出文件路径')
    
    # 解密授权命令
    dec_parser = subparsers.add_parser('dec', help='解密授权文件')
    dec_parser.add_argument('-i', '--input', required=True, help='输入文件路径')
    
    args = parser.parse_args()
    
    if args.command == 'gen':
        return generate_license(args.json, args.output)
    elif args.command == 'dec':
        return decrypt_license(args.input)
    else:
        parser.print_help()
        return 1

if __name__ == '__main__':
    sys.exit(main())
