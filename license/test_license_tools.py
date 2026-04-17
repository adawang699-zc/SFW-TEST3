#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
授权工具路径测试脚本
用于验证授权工具是否正确配置
"""

import os
import subprocess
import sys

def test_knowledge_license_tool():
    """测试知识库授权工具"""
    print("=" * 50)
    print("测试知识库授权工具")
    print("=" * 50)
    
    # 获取当前脚本目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    
    print(f"项目根目录: {project_root}")
    print(f"License目录: {script_dir}")
    
    # 可能的工具路径
    possible_paths = [
        os.path.join(script_dir, 'hx_knowledge_license_gender.exe'),
        os.path.join(script_dir, 'hx_knowledge_license_gender'),
        'hx_knowledge_license_gender.exe',
        'hx_knowledge_license_gender'
    ]
    
    print("\n检查可能的工具路径:")
    for i, path in enumerate(possible_paths, 1):
        exists = os.path.exists(path) if os.path.isabs(path) else "需要PATH测试"
        print(f"{i}. {path} - {'存在' if exists == True else '不存在' if exists == False else exists}")
    
    # 测试PATH中的工具
    print("\n测试PATH中的工具:")
    for tool_name in ['hx_knowledge_license_gender.exe', 'hx_knowledge_license_gender']:
        try:
            result = subprocess.run([tool_name, '--help'], 
                                  capture_output=True, 
                                  timeout=5,
                                  text=True)
            print(f"✓ {tool_name} 在PATH中可用")
            if result.stdout:
                print(f"  输出: {result.stdout[:100]}...")
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as e:
            print(f"✗ {tool_name} 在PATH中不可用: {type(e).__name__}")
    
    print("\n建议:")
    print("1. 将 hx_knowledge_license_gender.exe 放置在 license 目录下")
    print("2. 或者将工具添加到系统PATH环境变量中")
    print("3. 确保工具有执行权限")

def test_device_license_connection():
    """测试设备授权服务器连接"""
    print("\n" + "=" * 50)
    print("测试设备授权服务器连接")
    print("=" * 50)
    
    try:
        import paramiko
        print("✓ paramiko 模块已安装")
        
        # 测试连接配置
        config = {
            'host': '10.40.24.17',
            'username': 'tdhx',
            'password': 'tdhx@2017',
            'port': 22,
            'lic_gen_path': '/home/tdhx/license/x64/lic_gen'
        }
        
        print(f"服务器: {config['host']}:{config['port']}")
        print(f"用户: {config['username']}")
        print(f"工具路径: {config['lic_gen_path']}")
        
        print("\n尝试连接...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        ssh.connect(
            hostname=config['host'],
            port=config['port'],
            username=config['username'],
            password=config['password'],
            timeout=10
        )
        
        print("✓ SSH连接成功")
        
        # 测试工具是否存在
        stdin, stdout, stderr = ssh.exec_command(f"ls -la {config['lic_gen_path']}")
        exit_code = stdout.channel.recv_exit_status()
        
        if exit_code == 0:
            print("✓ 授权工具存在")
            output = stdout.read().decode('utf-8')
            print(f"  文件信息: {output.strip()}")
        else:
            print("✗ 授权工具不存在")
            error = stderr.read().decode('utf-8')
            print(f"  错误: {error.strip()}")
        
        # 测试工具帮助
        stdin, stdout, stderr = ssh.exec_command(f"{config['lic_gen_path']} --help")
        exit_code = stdout.channel.recv_exit_status()
        
        if exit_code == 0:
            print("✓ 授权工具可执行")
            help_output = stdout.read().decode('utf-8')
            print(f"  帮助信息: {help_output[:200]}...")
        else:
            print("✗ 授权工具不可执行")
        
        ssh.close()
        
    except ImportError:
        print("✗ paramiko 模块未安装")
        print("  请运行: pip install paramiko")
    except Exception as e:
        print(f"✗ 连接失败: {e}")

if __name__ == "__main__":
    print("授权工具配置测试")
    print("时间:", subprocess.run(['date'], capture_output=True, text=True, shell=True).stdout.strip())
    
    test_knowledge_license_tool()
    test_device_license_connection()
    
    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)
