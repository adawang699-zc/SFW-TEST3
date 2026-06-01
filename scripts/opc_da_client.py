#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OPC DA 客户端 - 支持 IOPCItemIO 和 IOPCSyncIO
用于生成真实的 OPC DA/DCOM 报文流量

运行环境：
- Windows 系统
- 安装 pywin32: pip install pywin32
- 安装 OPC DA 服务器（如 Matrikon OPC Simulation Server）

作者：Claude
日期：2026-05-29
"""

import sys
import time
import pythoncom
import win32com.client
from win32com.client import Dispatch, constants
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# OPC DA 接口 IID (Interface Identifier)
IID_IOPCServer                = "{63D5F430-CFE4-11D1-9E47-0060081846}"  # IOPCServer
IID_IOPCItemIO                = "{39C27088-D289-11D1-9577-0060B06623B}"  # IOPCItemIO
IID_IOPCSyncIO                = "{39C27090-D289-11D1-9577-0060B06623B}"  # IOPCSyncIO
IID_IOPCAsyncIO2              = "{39C27091-D289-11D1-9577-0060B06623B}"  # IOPCAsyncIO2
IID_IOPCGroup                 = "{39C2708F-D289-11D1-9577-0060B06623B}"  # IOPCGroup
IID_IOPCBrowseServerAddressSpace = "{39C2709A-D289-11D1-9577-0060B06623B}"  # IOPCBrowse
IID_IOPCCommon                = "{39C27084-D289-11D1-9577-0060B06623B}"  # IOPCCommon

# OPC 数据源类型
OPC_DS_CACHE    = 1  # 从缓存读取
OPC_DS_DEVICE   = 2  # 从设备直接读取

# OPC 浏览类型
OPC_BRANCH      = 1  # 按层级浏览
OPC_FLAT        = 2  # 平铺浏览
OPC_ITEM        = 3  # 只浏览叶子节点

# OPC 质量码
OPC_QUALITY_GOOD              = 0xC0  # 192
OPC_QUALITY_BAD               = 0x00  # 0
OPC_QUALITY_BAD_CONFIG_ERROR  = 0x04  # 4
OPC_QUALITY_BAD_NOT_CONNECTED = 0x08  # 8
OPC_QUALITY_BAD_DEVICE_FAILURE= 0x10  # 16
OPC_QUALITY_BAD_COMM_FAILURE  = 0x0C  # 12
OPC_QUALITY_uncertain         = 0x40  # 64
OPC_QUALITY_uncertain_last_usable = 0x44  # 68


def get_quality_string(quality):
    """将质量码转换为字符串"""
    quality_map = {
        OPC_QUALITY_GOOD: "Good",
        OPC_QUALITY_BAD: "Bad",
        OPC_QUALITY_BAD_CONFIG_ERROR: "Bad (Config Error)",
        OPC_QUALITY_BAD_NOT_CONNECTED: "Bad (Not Connected)",
        OPC_QUALITY_BAD_DEVICE_FAILURE: "Bad (Device Failure)",
        OPC_QUALITY_BAD_COMM_FAILURE: "Bad (Comm Failure)",
        OPC_QUALITY_uncertain: "Uncertain",
        OPC_QUALITY_uncertain_last_usuable: "Uncertain (Last Usable)",
    }
    return quality_map.get(quality & 0xC0, f"Unknown ({quality})")


class OPCDAClient:
    """OPC DA 客户端，支持 IOPCItemIO 和 IOPCSyncIO"""

    def __init__(self, server_name=None):
        self.server = None
        self.server_name = server_name
        self.connected = False
        self.groups = {}  # 存储创建的 Group
        self._item_io_supported = None

    def list_available_servers(self):
        """列出本地可用的 OPC DA 服务器"""
        logger.info("正在枚举本地 OPC DA 服务器...")
        servers = []

        try:
            # 使用 OPC Server Enumerator
            opc_enum = Dispatch("OPC.ServerEnumerator")
            server_list = opc_enum.GetOPCServers("")

            for server in server_list:
                servers.append(server)
                logger.info(f"  发现服务器: {server}")

            return servers

        except Exception as e:
            logger.error(f"枚举服务器失败: {e}")
            # 尝试直接获取已注册的 OPC 服务器
            try:
                import win32api
                import win32con

                # 从注册表查找 OPC 服务器
                key = win32api.RegOpenKeyEx(
                    win32con.HKEY_CLASSES_ROOT,
                    "CLSID",
                    0,
                    win32con.KEY_READ
                )
                # 这里简化处理，返回常见服务器名
                common_servers = [
                    "Matrikon.OPC.Simulation.1",
                    "Matrikon.OPC.Simulation",
                    "OPC.SimaticNET",
                    "OPCServer.WinCC",
                ]
                return common_servers

            except:
                return []

    def connect(self, server_name=None, remote_host=None):
        """
        连接 OPC DA 服务器

        Args:
            server_name: OPC 服务器 ProgID（如 "Matrikon.OPC.Simulation.1")
            remote_host: 远程主机名或 IP（如 "192.168.1.100"），None 表示本地
        """
        if server_name:
            self.server_name = server_name

        if not self.server_name:
            logger.error("未指定服务器名称")
            return False

        # 本地连接还是远程连接
        if remote_host:
            logger.info(f"正在连接远程 OPC DA 服务器: {self.server_name}@{remote_host}")
        else:
            logger.info(f"正在连接本地 OPC DA 服务器: {self.server_name}")

        try:
            # 初始化 COM
            pythoncom.CoInitialize()

            # 创建 OPC Server 对象
            self.server = Dispatch(self.server_name)

            # 连接服务器
            # Connect(serverProgID, serverNodeName)
            # serverNodeName: 空字符串=本地，否则是远程机器名/IP
            self.server.Connect(self.server_name, remote_host if remote_host else "")

            self.connected = True
            self._remote_host = remote_host

            # 获取服务器信息
            try:
                status = self.server.GetStatus()
                logger.info(f"服务器状态: Vendor={status[0]}, State={status[1]}")
            except:
                pass

            # 检查是否支持 IOPCItemIO
            self._check_item_io_support()

            logger.info("连接成功 ✓")
            return True

        except pythoncom.com_error as e:
            hr, msg, exc, arg = e
            logger.error(f"COM 错误: HR={hr}, Msg={msg}")
            if remote_host:
                logger.error("远程连接可能失败的原因:")
                logger.error("  1. 远程机器防火墙未开放 DCOM 端口 (TCP 135 + 动态端口)")
                logger.error("  2. 远程 OPC 服务器未启动")
                logger.error("  3. DCOM 安全配置不正确")
                logger.error("  4. Windows 认证失败")
            self.connected = False
            return False
        except Exception as e:
            logger.error(f"连接失败: {e}")
            self.connected = False
            return False

    def _check_item_io_support(self):
        """检查服务器是否支持 IOPCItemIO 接口"""
        try:
            iid = pythoncom.IID(IID_IOPCItemIO)
            item_io = self.server.QueryInterface(iid)
            self._item_io_supported = True
            logger.info("服务器支持 IOPCItemIO ✓")
            return True
        except:
            self._item_io_supported = False
            logger.info("服务器不支持 IOPCItemIO，将使用 IOPCSyncIO")
            return False

    def disconnect(self):
        """断开 OPC DA 服务器"""
        if self.connected and self.server:
            try:
                # 删除所有创建的 Group
                for group_name in list(self.groups.keys()):
                    self._remove_group(group_name)

                self.server.Disconnect()
                logger.info("已断开连接")
            except Exception as e:
                logger.warning(f"断开连接时出错: {e}")

            self.server = None
            self.connected = False

        # 清理 COM
        pythoncom.CoUninitialize()

    def browse(self, branch="", browse_type=OPC_FLAT):
        """浏览 OPC Item 地址空间"""
        if not self.connected:
            logger.error("未连接服务器")
            return []

        logger.info(f"浏览 OPC Items (branch={branch}, type={browse_type})")

        items = []

        try:
            # 尝试使用 IOPCBrowseServerAddressSpace
            iid = pythoncom.IID(IID_IOPCBrowseServerAddressSpace)
            browser = self.server.QueryInterface(iid)

            # 浏览
            # QueryOrganization: 查询组织类型
            org_type = browser.QueryOrganization()
            logger.info(f"地址空间组织类型: {org_type}")

            # Browse: 浏览指定分支
            # 参数: branch, browse_type, filter_criteria, vendor_filter, return_all
            browse_filter = ""  # 空表示不过滤
            vendor_filter = "*"  # 所有厂商
            return_all = True

            more_items = True
            continuation_point = ""

            while more_items:
                result = browser.Browse(
                    branch,
                    browse_type,
                    browse_filter,
                    vendor_filter,
                    return_all,
                    continuation_point
                )

                # result 包含 (item_names, more_elements, continuation_point)
                item_names = result[0]
                more_items = result[1]
                continuation_point = result[2] if len(result) > 2 else ""

                for item in item_names:
                    items.append(item)
                    logger.debug(f"  Item: {item}")

            logger.info(f"共发现 {len(items)} 个 Items")
            return items

        except Exception as e:
            logger.warning(f"IOPCBrowse 接口失败: {e}")
            # 使用备用方法：通过 Group 探索

            # 尝试一些常见的 Item 名称
            common_items = self._guess_common_items()
            if common_items:
                logger.info(f"使用常见 Item 名称: {common_items}")
                return common_items

            return []

    def _guess_common_items(self):
        """猜测常见的 OPC Item 名称"""
        common_patterns = [
            # Matrikon OPC Simulation 常见 Items
            "Random.Int1",
            "Random.Int2",
            "Random.Real4",
            "Random.Real8",
            "Random.String",
            "Random.Boolean",
            "Square Waves.Int1",
            "Square Waves.Int2",
            "Square Waves.Real4",
            "Triangle Waves.Int1",
            "Triangle Waves.Real4",
            "Saw-tooth Waves.Int1",
            "Saw-tooth Waves.Real4",
            "Bucket Brigade.Int1",
            "Bucket Brigade.Real4",
            "Bucket Brigade.String",
            # 其他常见格式
            "Channel1.Device1.Tag1",
            "Device1.Tag1",
            "Tag1",
        ]
        return common_patterns

    def _create_group(self, group_name, update_rate=1000, active=True):
        """创建 OPC Group"""
        if not self.connected:
            return None

        try:
            # 使用 OPCGroups.AddGroup
            groups = self.server.OPCGroups

            # 添加 Group
            group = groups.Add(group_name)

            # 配置 Group
            group.UpdateRate = update_rate
            group.IsActive = active
            group.IsSubscribed = False  # 不订阅回调

            self.groups[group_name] = {
                'group': group,
                'items': {},
                'handles': []
            }

            logger.info(f"创建 Group: {group_name}, UpdateRate={update_rate}")
            return group

        except Exception as e:
            logger.error(f"创建 Group 失败: {e}")
            return None

    def _remove_group(self, group_name):
        """删除 OPC Group"""
        if group_name in self.groups:
            try:
                groups = self.server.OPCGroups
                groups.Remove(group_name)
                del self.groups[group_name]
                logger.info(f"删除 Group: {group_name}")
            except Exception as e:
                logger.warning(f"删除 Group 失败: {e}")

    def _add_items_to_group(self, group_name, item_ids):
        """向 Group 添加 OPC Items"""
        if group_name not in self.groups:
            logger.error(f"Group {group_name} 不存在")
            return []

        group_info = self.groups[group_name]
        group = group_info['group']

        handles = []
        errors = []

        try:
            opc_items = group.OPCItems

            for i, item_id in enumerate(item_ids):
                # AddItem: 返回 ServerHandle
                handle = opc_items.AddItem(item_id, i + 1)

                if handle:
                    handles.append(handle)
                    group_info['items'][handle] = item_id
                    logger.debug(f"添加 Item: {item_id}, Handle={handle}")
                else:
                    handles.append(None)
                    errors.append(item_id)

            group_info['handles'] = handles

            if errors:
                logger.warning(f"部分 Item 添加失败: {errors}")

            return handles

        except Exception as e:
            logger.error(f"添加 Items 失败: {e}")
            return []

    def read_direct(self, item_ids, max_age=0, data_source=OPC_DS_DEVICE):
        """
        直接读取 OPC Items (不创建 Group)
        尝试使用 IOPCItemIO，如果不支持则使用临时 Group

        Args:
            item_ids: Item ID 字符串列表
            max_age: 最大数据年龄（毫秒），0 表示最新
            data_source: OPC_DS_CACHE 或 OPC_DS_DEVICE

        Returns:
            dict: {item_id: {'value': value, 'quality': quality, 'timestamp': timestamp}}
        """
        if not self.connected:
            logger.error("未连接服务器")
            return {}

        logger.info(f"读取 Items: {item_ids}")

        results = {}

        # 尝试使用 IOPCItemIO
        if self._item_io_supported:
            results = self._read_via_item_io(item_ids, max_age)
        else:
            # 使用临时 Group + SyncIO
            results = self._read_via_sync_io(item_ids, data_source)

        return results

    def _read_via_item_io(self, item_ids, max_age):
        """使用 IOPCItemIO.Read 读取"""
        try:
            iid = pythoncom.IID(IID_IOPCItemIO)
            item_io = self.server.QueryInterface(iid)

            # IOPCItemIO.Read(item_ids, max_age)
            # 返回: values, qualities, timestamps, errors

            result = item_io.Read(item_ids, max_age)

            # 解析结果
            values = result[0] if len(result) > 0 else []
            qualities = result[1] if len(result) > 1 else []
            timestamps = result[2] if len(result) > 2 else []
            errors = result[3] if len(result) > 3 else []

            results = {}
            for i, item_id in enumerate(item_ids):
                if i < len(values):
                    results[item_id] = {
                        'value': values[i],
                        'quality': qualities[i] if i < len(qualities) else 0,
                        'quality_str': get_quality_string(qualities[i] if i < len(qualities) else 0),
                        'timestamp': timestamps[i] if i < len(timestamps) else None,
                        'error': errors[i] if i < len(errors) else 0
                    }
                    logger.info(f"  {item_id}: value={values[i]}, quality={get_quality_string(qualities[i] if i < len(qualities) else 0)}")

            return results

        except Exception as e:
            logger.error(f"IOPCItemIO.Read 失败: {e}")
            return {}

    def _read_via_sync_io(self, item_ids, data_source):
        """使用临时 Group + IOPCSyncIO.Read 读取"""
        logger.info("使用 IOPCSyncIO 方式读取...")

        temp_group_name = f"TempReadGroup_{int(time.time())}"

        # 创建临时 Group
        group = self._create_group(temp_group_name, update_rate=1000)
        if not group:
            return {}

        try:
            # 添加 Items
            handles = self._add_items_to_group(temp_group_name, item_ids)

            if not handles:
                return {}

            # 获取 SyncIO 接口
            iid = pythoncom.IID(IID_IOPCSyncIO)
            sync_io = group.QueryInterface(iid)

            # SyncIO.Read(data_source, num_items, server_handles)
            # 返回: values, qualities, timestamps, errors

            valid_handles = [h for h in handles if h is not None]

            result = sync_io.Read(data_source, len(valid_handles), valid_handles)

            # 解析结果
            values = result[0] if len(result) > 0 else []
            qualities = result[1] if len(result) > 1 else []
            timestamps = result[2] if len(result) > 2 else []
            errors = result[3] if len(result) > 3 else []

            results = {}
            for i, item_id in enumerate(item_ids):
                if handles[i] is not None and i < len(values):
                    results[item_id] = {
                        'value': values[i],
                        'quality': qualities[i] if i < len(qualities) else 0,
                        'quality_str': get_quality_string(qualities[i] if i < len(qualities) else 0),
                        'timestamp': timestamps[i] if i < len(timestamps) else None,
                        'error': errors[i] if i < len(errors) else 0
                    }
                    logger.info(f"  {item_id}: value={values[i]}, quality={get_quality_string(qualities[i] if i < len(qualities) else 0)}")

            return results

        except Exception as e:
            logger.error(f"IOPCSyncIO.Read 失败: {e}")
            return {}
        finally:
            # 删除临时 Group
            self._remove_group(temp_group_name)

    def write_direct(self, item_ids, values):
        """
        直接写入 OPC Items

        Args:
            item_ids: Item ID 字符串列表
            values: 要写入的值列表

        Returns:
            dict: {item_id: {'error': error_code, 'success': bool}}
        """
        if not self.connected:
            logger.error("未连接服务器")
            return {}

        logger.info(f"写入 Items: {dict(zip(item_ids, values))}")

        results = {}

        # 尝试使用 IOPCItemIO
        if self._item_io_supported:
            results = self._write_via_item_io(item_ids, values)
        else:
            results = self._write_via_sync_io(item_ids, values)

        return results

    def _write_via_item_io(self, item_ids, values):
        """使用 IOPCItemIO.Write 写入"""
        try:
            iid = pythoncom.IID(IID_IOPCItemIO)
            item_io = self.server.QueryInterface(iid)

            # IOPCItemIO.Write(item_ids, values)
            # 返回: errors

            errors = item_io.Write(item_ids, values)

            results = {}
            for i, item_id in enumerate(item_ids):
                error_code = errors[i] if i < len(errors) else -1
                results[item_id] = {
                    'error': error_code,
                    'success': error_code == 0
                }
                status = "成功" if error_code == 0 else f"失败(error={error_code})"
                logger.info(f"  {item_id}: {status}")

            return results

        except Exception as e:
            logger.error(f"IOPCItemIO.Write 失败: {e}")
            return {}

    def _write_via_sync_io(self, item_ids, values):
        """使用临时 Group + IOPCSyncIO.Write 写入"""
        logger.info("使用 IOPCSyncIO 方式写入...")

        temp_group_name = f"TempWriteGroup_{int(time.time())}"

        # 创建临时 Group
        group = self._create_group(temp_group_name, update_rate=1000)
        if not group:
            return {}

        try:
            # 添加 Items
            handles = self._add_items_to_group(temp_group_name, item_ids)

            if not handles:
                return {}

            # 获取 SyncIO 接口
            iid = pythoncom.IID(IID_IOPCSyncIO)
            sync_io = group.QueryInterface(iid)

            # SyncIO.Write(num_items, server_handles, values)
            # 返回: errors

            valid_handles = [h for h in handles if h is not None]

            errors = sync_io.Write(len(valid_handles), valid_handles, values)

            results = {}
            for i, item_id in enumerate(item_ids):
                if handles[i] is not None and i < len(errors):
                    error_code = errors[i]
                    results[item_id] = {
                        'error': error_code,
                        'success': error_code == 0
                    }
                    status = "成功" if error_code == 0 else f"失败(error={error_code})"
                    logger.info(f"  {item_id}: {status}")

            return results

        except Exception as e:
            logger.error(f"IOPCSyncIO.Write 失败: {e}")
            return {}
        finally:
            # 删除临时 Group
            self._remove_group(temp_group_name)

    def continuous_read(self, item_ids, interval=1.0, count=10):
        """
        持续读取 OPC Items（生成持续的 DCOM 报文流量）

        Args:
            item_ids: Item ID 列表
            interval: 读取间隔（秒）
            count: 读取次数
        """
        logger.info(f"开始持续读取: interval={interval}s, count={count}")

        for i in range(count):
            logger.info(f"--- 第 {i+1}/{count} 次读取 ---")
            results = self.read_direct(item_ids)
            time.sleep(interval)

        logger.info("持续读取完成")

    def generate_traffic(self, item_ids, duration=60, operations=['read', 'write'], write_values=None):
        """
        生成 OPC DA 报文流量（用于防火墙测试）

        Args:
            item_ids: 要操作的 Item ID 列表
            duration: 持续时间（秒）
            operations: 操作类型列表 ['read', 'write', 'browse']
            write_values: 写入值（如果包含 write 操作）
        """
        logger.info(f"开始生成 OPC DA 流量: duration={duration}s, operations={operations}")

        start_time = time.time()
        cycle_count = 0

        while time.time() - start_time < duration:
            cycle_count += 1
            logger.info(f"=== 流量周期 {cycle_count} ===")

            # Browse
            if 'browse' in operations:
                logger.info("执行 Browse 操作...")
                items = self.browse()

            # Read
            if 'read' in operations:
                logger.info("执行 Read 操作...")
                self.read_direct(item_ids)

            # Write
            if 'write' in operations:
                logger.info("执行 Write 操作...")
                if write_values:
                    self.write_direct(item_ids[:len(write_values)], write_values)
                else:
                    # 生成随机写入值
                    import random
                    random_values = [random.randint(0, 100) for _ in item_ids]
                    self.write_direct(item_ids, random_values)

            time.sleep(0.5)  # 每周期间隔

        logger.info(f"流量生成完成，共 {cycle_count} 个周期")


def main():
    """主函数 - 使用示例"""

    print("=" * 60)
    print("OPC DA 客户端测试")
    print("=" * 60)

    # 创建客户端
    client = OPCDAClient()

    # 列出可用服务器
    servers = client.list_available_servers()

    if not servers:
        print("未发现 OPC DA 服务器")
        print("请确保已安装 OPC DA 服务器（如 Matrikon OPC Simulation Server）")
        return

    # 选择服务器（默认使用第一个或 Matrikon）
    server_name = None
    for s in servers:
        if "Matrikon" in s or "Simulation" in s:
            server_name = s
            break

    if not server_name and servers:
        server_name = servers[0]

    print(f"\n选择服务器: {server_name}")

    # 本地连接
    if not client.connect(server_name):
        print("连接失败")
        return

    # 浏览 Items
    print("\n" + "-" * 40)
    print("浏览 OPC Items...")
    items = client.browse()

    if items:
        print(f"发现 {len(items)} 个 Items:")
        for i, item in enumerate(items[:20]):  # 只显示前 20 个
            print(f"  {i+1}. {item}")
        if len(items) > 20:
            print(f"  ... 还有 {len(items) - 20} 个")

    # 选择测试 Items
    test_items = [
        "Random.Int1",
        "Random.Int2",
        "Random.Real4",
        "Random.Real8",
        "Random.Boolean",
    ]

    # 如果浏览到的 Items 有更好的选择，使用它们
    if items:
        # 选取前几个有效 Items
        test_items = items[:5]

    print(f"\n测试 Items: {test_items}")

    # 读取测试
    print("\n" + "-" * 40)
    print("读取 OPC Items...")
    results = client.read_direct(test_items)

    for item_id, data in results.items():
        print(f"  {item_id}:")
        print(f"    值: {data['value']}")
        print(f"    质量: {data['quality_str']}")
        if data['timestamp']:
            print(f"    时间戳: {data['timestamp']}")

    # 写入测试
    print("\n" + "-" * 40)
    print("写入 OPC Items...")
    write_items = test_items[:2]  # 只写入前两个
    write_values = [123, 456.78]

    write_results = client.write_direct(write_items, write_values)

    for item_id, data in write_results.items():
        status = "成功" if data['success'] else f"失败(error={data['error']})"
        print(f"  {item_id}: {status}")

    # 再次读取验证写入
    print("\n" + "-" * 40)
    print("验证写入结果...")
    verify_results = client.read_direct(write_items)

    for item_id, data in verify_results.items():
        print(f"  {item_id}: 值={data['value']}")

    # 持续读取（生成流量）
    print("\n" + "-" * 40)
    print("持续读取测试（5 次）...")
    client.continuous_read(test_items[:3], interval=1.0, count=5)

    # 断开连接
    print("\n" + "-" * 40)
    client.disconnect()

    print("\n测试完成 ✓")


def traffic_generator():
    """流量生成模式 - 用于防火墙测试"""

    print("=" * 60)
    print("OPC DA 流量生成器 - 防火墙测试模式")
    print("=" * 60)

    # 配置
    server_name = "Matrikon.OPC.Simulation.1"
    duration = 60  # 持续时间（秒）

    client = OPCDAClient(server_name)

    if not client.connect():
        print("连接失败，请检查 OPC DA 服务器是否运行")
        return

    # 测试 Items
    test_items = [
        "Random.Int1",
        "Random.Int2",
        "Random.Real4",
        "Random.Real8",
        "Square Waves.Real4",
        "Triangle Waves.Real4",
        "Saw-tooth Waves.Int1",
        "Bucket Brigade.Int1",
    ]

    print(f"\n开始生成流量，持续 {duration} 秒...")
    print("请在防火墙/网络监控工具中捕获 DCOM 报文")
    print("协议特征: TCP 135 (EPMapper) + 动态端口 (DCOM)")

    # 生成流量
    client.generate_traffic(
        item_ids=test_items,
        duration=duration,
        operations=['read', 'write'],
        write_values=[100, 200, 50.5, 75.25, 123.45]
    )

    client.disconnect()
    print("\n流量生成完成")


def remote_connect_test(server_name, remote_host):
    """测试远程 OPC DA 连接"""

    print("=" * 60)
    print("OPC DA 远程连接测试")
    print("=" * 60)

    print(f"服务器: {server_name}")
    print(f"远程主机: {remote_host}")

    client = OPCDAClient(server_name)

    if client.connect(server_name, remote_host):
        print("\n连接成功！")

        # 浏览 Items
        print("\n浏览 OPC Items...")
        items = client.browse()

        if items:
            print(f"发现 {len(items)} 个 Items")
            for i, item in enumerate(items[:10]):
                print(f"  {i+1}. {item}")

        # 读取测试
        if items:
            test_items = items[:3]
            print(f"\n读取测试: {test_items}")
            results = client.read_direct(test_items)

            for item_id, data in results.items():
                print(f"  {item_id}: value={data['value']}, quality={data['quality_str']}")

        client.disconnect()

    else:
        print("\n连接失败")
        print("\n远程连接配置建议:")
        print("1. 目标机器需要启动 OPC DA 服务器")
        print("2. Windows 防火墙需要允许 DCOM:")
        print("   - TCP 135 (RPC Endpoint Mapper)")
        print("   - 动态端口范围 (通过 dcomcnfg.exe 配置)")
        print("3. DCOM 安全配置:")
        print("   - 运行 dcomcnfg.exe")
        print("   - 组件服务 > 计算机 > 我的电脑 > 属性")
        print("   - 配置默认安全和访问权限")
        print("4. 两台机器需要能互相解析主机名（或使用 IP）")
        print("5. 需要有相同域用户或配置匿名访问")


def list_remote_servers(remote_host):
    """列出远程机器上的 OPC DA 服务器"""

    print("=" * 60)
    print(f"枚举远程 OPC DA 服务器: {remote_host}")
    print("=" * 60)

    try:
        pythoncom.CoInitialize()

        # 创建 OPC Server Enumerator
        opc_enum = Dispatch("OPC.ServerEnumerator")

        # GetOPCServers(remoteNodeName)
        servers = opc_enum.GetOPCServers(remote_host)

        print(f"\n远程服务器列表:")
        for s in servers:
            print(f"  - {s}")

        pythoncom.CoUninitialize()
        return servers

    except pythoncom.com_error as e:
        hr, msg, exc, arg = e
        print(f"\nCOM 错误: {msg}")
        print("可能原因:")
        print("  1. 远程主机不可达")
        print("  2. 防火墙阻止 DCOM")
        print("  3. 目标机器未运行 OPC Server Enumerator 服务")
        return []
    except Exception as e:
        print(f"\n错误: {e}")
        return []


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OPC DA 客户端")
    parser.add_argument("--server", default="Matrikon.OPC.Simulation.1", help="OPC 服务器名称")
    parser.add_argument("--host", default=None, help="远程主机 IP/名称（不指定则为本地）")
    parser.add_argument("--traffic", action="store_true", help="生成流量模式")
    parser.add_argument("--duration", type=int, default=60, help="流量持续时间（秒）")
    parser.add_argument("--list", action="store_true", help="列出服务器")
    parser.add_argument("--list-remote", metavar="HOST", help="列出远程主机上的服务器")
    parser.add_argument("--remote-test", nargs=2, metavar=("SERVER", "HOST"), help="测试远程连接")

    args = parser.parse_args()

    if args.list:
        client = OPCDAClient()
        servers = client.list_available_servers()
        print("\n可用的 OPC DA 服务器:")
        for s in servers:
            print(f"  - {s}")

    elif args.list_remote:
        list_remote_servers(args.list_remote)

    elif args.remote_test:
        server_name, remote_host = args.remote_test
        remote_connect_test(server_name, remote_host)

    elif args.traffic:
        traffic_generator()
    else:
        main()