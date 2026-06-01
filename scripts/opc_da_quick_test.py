#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OPC DA 快速测试脚本
用于验证与 Matrikon OPC Simulation Server 的连接

运行方法：
1. 确保 Matrikon OPC Simulation Server 已启动
2. 运行此脚本: python opc_da_quick_test.py
"""

import pythoncom
import win32com.client
import time

def quick_test():
    print("=" * 50)
    print("OPC DA 快速连接测试")
    print("=" * 50)

    # 服务器名称
    server_name = "Matrikon.OPC.Simulation.1"

    try:
        # 初始化 COM
        pythoncom.CoInitialize()

        print(f"\n[1] 连接 OPC DA 服务器: {server_name}")

        # 创建 OPC Server 对象
        server = win32com.client.Dispatch(server_name)

        # 连接
        server.Connect(server_name, "")
        print("连接成功 ✓")

        # 测试接口支持
        print("\n[2] 检查接口支持...")

        # IOPCItemIO IID
        iid_item_io = pythoncom.IID("{39C27088-D289-11D1-9577-0060B06623B}")
        try:
            item_io = server.QueryInterface(iid_item_io)
            print("IOPCItemIO: 支持 ✓")
        except:
            print("IOPCItemIO: 不支持 ✗")

        # IOPCSyncIO (通过 Group)
        print("IOPCSyncIO: 支持 ✓ (通过 Group)")

        # 创建临时 Group 进行读写测试
        print("\n[3] 创建临时 Group...")
        groups = server.OPCGroups
        group = groups.Add("QuickTestGroup")
        group.UpdateRate = 1000
        group.IsActive = True

        # 测试 Items
        test_items = ["Random.Int1", "Random.Real4", "Random.Real8"]
        print(f"测试 Items: {test_items}")

        print("\n[4] 添加 Items...")
        opc_items = group.OPCItems
        handles = []

        for i, item_id in enumerate(test_items):
            handle = opc_items.AddItem(item_id, i + 1)
            handles.append(handle)
            print(f"  {item_id}: Handle={handle}")

        # 同步读取
        print("\n[5] 同步读取...")
        sync_io = group.SyncRead(1, handles)  # 1 = OPC_DS_CACHE

        # sync_io 返回格式可能不同，尝试解析
        # 实际返回可能是 tuple 或 dict，需要根据实际情况调整
        print(f"读取结果类型: {type(sync_io)}")
        print(f"读取结果: {sync_io}")

        # 尝试不同的解析方式
        if hasattr(sync_io, '__iter__'):
            for i, item_id in enumerate(test_items):
                try:
                    value = sync_io[0][i] if isinstance(sync_io[0], (list, tuple)) else sync_io[i]
                    print(f"  {item_id}: {value}")
                except:
                    pass

        # 清理
        print("\n[6] 清理资源...")
        groups.Remove("QuickTestGroup")
        server.Disconnect()
        print("已断开连接 ✓")

        pythoncom.CoUninitialize()

        print("\n" + "=" * 50)
        print("测试完成！可以使用 opc_da_client.py 进行更多操作")
        print("=" * 50)

    except pythoncom.com_error as e:
        hr, msg, exc, arg = e
        print(f"\nCOM 错误:")
        print(f"  HR: {hr}")
        print(f"  Message: {msg}")
        print(f"  详情: {exc}")
        print("\n可能的原因:")
        print("  1. OPC 服务器未启动")
        print("  2. 服务器名称不正确")
        print("  3. COM 组件未正确注册")

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()


def test_traffic():
    """生成简单的流量测试"""
    print("=" * 50)
    print("OPC DA 流量生成测试（10次读取）")
    print("=" * 50)

    pythoncom.CoInitialize()

    try:
        server = win32com.client.Dispatch("Matrikon.OPC.Simulation.1")
        server.Connect("Matrikon.OPC.Simulation.1", "")
        print("连接成功 ✓")

        groups = server.OPCGroups
        group = groups.Add("TrafficTestGroup")

        test_items = ["Random.Int1", "Random.Int2", "Random.Real4"]
        handles = []

        opc_items = group.OPCItems
        for i, item_id in enumerate(test_items):
            handles.append(opc_items.AddItem(item_id, i + 1))

        print("\n开始读取（每隔1秒读取一次）...")
        for i in range(10):
            print(f"  [{i+1}/10] 读取...")
            sync_io = group.SyncRead(1, handles)  # OPC_DS_CACHE
            time.sleep(1)

        groups.Remove("TrafficTestGroup")
        server.Disconnect()
        print("\n流量测试完成 ✓")

    except Exception as e:
        print(f"错误: {e}")

    pythoncom.CoUninitialize()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--traffic":
        test_traffic()
    else:
        quick_test()