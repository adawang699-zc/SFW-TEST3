#!/bin/bash
# ============================================================
# 虚拟机桥接到 ns 管理的网口 - 配置脚本
#
# 场景: 将 KVM 虚拟机桥接到网络命名空间(ns)管理的网口
# 使用: sudo ./vm_bridge_to_ns.sh
# ============================================================

set -e

# ======================= 配置区域 =======================
# 请根据实际情况修改以下变量

# 目标网络命名空间名称
NS_NAME="ns1"

# 要桥接的物理网口 (在ns中管理的网口)
PHYSICAL_IF="eth1"

# 创建的网桥名称 (在ns中)
BRIDGE_NAME="br-ns1"

# veth pair 名称
# veth-host: 在主机网络命名空间中，libvirt虚拟机桥接到此接口
# veth-ns:   在目标ns中，连接到网桥
VETH_HOST="veth-host-ns1"
VETH_NS="veth-ns1"

# 网桥IP地址 (可选，如果需要ns有IP)
BRIDGE_IP="192.168.100.1/24"

# ======================= 函数定义 =======================

print_header() {
    echo ""
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "错误: 请使用 root 权限运行此脚本"
        echo "用法: sudo $0"
        exit 1
    fi
}

check_ns_exists() {
    if ! ip netns list | grep -q "^${NS_NAME}"; then
        echo "错误: 网络命名空间 '$NS_NAME' 不存在"
        echo ""
        echo "创建新的命名空间:"
        echo "  ip netns add $NS_NAME"
        echo ""
        echo "当前的命名空间列表:"
        ip netns list
        exit 1
    fi
}

check_interface_in_ns() {
    if ! ip netns exec "$NS_NAME" ip link show "$PHYSICAL_IF" &>/dev/null; then
        echo "错误: 接口 '$PHYSICAL_IF' 不在命名空间 '$NS_NAME' 中"
        echo ""
        echo "命名空间 '$NS_NAME' 中的接口:"
        ip netns exec "$NS_NAME" ip link show
        exit 1
    fi
}

# ======================= 配置步骤 =======================

print_header "步骤 0: 环境检查"

check_root
check_ns_exists
check_interface_in_ns

echo "✓ 环境检查通过"
echo "  - 目标ns: $NS_NAME"
echo "  - 物理网口: $PHYSICAL_IF"
echo "  - 网桥: $BRIDGE_NAME"

# ----------------------------------------------------------

print_header "步骤 1: 在 ns 中创建网桥"

# 检查网桥是否已存在
if ip netns exec "$NS_NAME" ip link show "$BRIDGE_NAME" &>/dev/null; then
    echo "网桥 $BRIDGE_NAME 已存在，跳过创建"
else
    echo "创建网桥 $BRIDGE_NAME..."
    ip netns exec "$NS_NAME" ip link add name "$BRIDGE_NAME" type bridge
    ip netns exec "$NS_NAME" ip link set "$BRIDGE_NAME" up
    echo "✓ 网桥创建完成"
fi

# ----------------------------------------------------------

print_header "步骤 2: 将物理网口加入网桥"

# 检查是否已在网桥中
if ip netns exec "$NS_NAME" ip link show "$PHYSICAL_IF" | grep -q "master $BRIDGE_NAME"; then
    echo "接口 $PHYSICAL_IF 已在网桥 $BRIDGE_NAME 中，跳过"
else
    echo "将 $PHYSICAL_IF 加入网桥 $BRIDGE_NAME..."
    ip netns exec "$NS_NAME" ip link set "$PHYSICAL_IF" master "$BRIDGE_NAME"
    ip netns exec "$NS_NAME" ip link set "$PHYSICAL_IF" up
    echo "✓ 接口已加入网桥"
fi

# ----------------------------------------------------------

print_header "步骤 3: 创建 veth pair 连接主机和 ns"

# 检查是否已存在
if ip link show "$VETH_HOST" &>/dev/null; then
    echo "veth pair 已存在，删除旧的..."
    ip link delete "$VETH_HOST"
fi

echo "创建 veth pair: $VETH_HOST <-> $VETH_NS..."
ip link add "$VETH_HOST" type veth peer name "$VETH_NS"
echo "✓ veth pair 创建完成"

# 将 veth-ns 移动到目标命名空间
echo "将 $VETH_NS 移动到命名空间 $NS_NAME..."
ip link set "$VETH_NS" netns "$NS_NAME"
echo "✓ veth 已移动到 ns"

# 启动接口
echo "启动 veth 接口..."
ip link set "$VETH_HOST" up
ip netns exec "$NS_NAME" ip link set "$VETH_NS" up
echo "✓ veth 接口已启动"

# ----------------------------------------------------------

print_header "步骤 4: 将 veth-ns 加入网桥"

# 检查是否已在网桥中
if ip netns exec "$NS_NAME" bridge link show | grep -q "$VETH_NS"; then
    echo "$VETH_NS 已在网桥中，跳过"
else
    echo "将 $VETH_NS 加入网桥 $BRIDGE_NAME..."
    ip netns exec "$NS_NAME" ip link set "$VETH_NS" master "$BRIDGE_NAME"
    echo "✓ veth-ns 已加入网桥"
fi

# ----------------------------------------------------------

print_header "步骤 5: 创建主机网桥 (供 libvirt 使用)"

# 创建一个主机网桥，虚拟机将桥接到此网桥
# 然后通过 veth pair 连接到目标 ns

LIBVIRT_BRIDGE="br-vm-ns1"

if ip link show "$LIBVIRT_BRIDGE" &>/dev/null; then
    echo "网桥 $LIBVIRT_BRIDGE 已存在"
else
    echo "创建主机网桥 $LIBVIRT_BRIDGE..."
    ip link add name "$LIBVIRT_BRIDGE" type bridge
    ip link set "$LIBVIRT_BRIDGE" up
    echo "✓ 主机网桥创建完成"
fi

# 将 veth-host 加入主机网桥
echo "将 $VETH_HOST 加入主机网桥 $LIBVIRT_BRIDGE..."
ip link set "$VETH_HOST" master "$LIBVIRT_BRIDGE"
echo "✓ veth-host 已加入主机网桥"

# ----------------------------------------------------------

print_header "配置完成!"

echo ""
echo "网络拓扑:"
echo "  ┌──────────┐"
echo "  │    VM    │ (libvirt 虚拟机)"
echo "  └────┬─────┘"
echo "       │ 桥接"
echo "  ┌────▼─────────────────────┐"
echo "  │ $LIBVIRT_BRIDGE (主机)    │"
echo "  └────┬─────────────────────┘"
echo "       │"
echo "  ┌────▼─────────────────────┐"
echo "  │ $VETH_HOST (veth pair)   │"
echo "  └────┬─────────────────────┘"
echo "       │"
echo "  ┌────▼─────────────────────────────────┐"
echo "  │ 网络命名空间: $NS_NAME                │"
echo "  │  ┌─────────────────────────────┐    │"
echo "  │  │ $BRIDGE_NAME                │    │"
echo "  │  │  ├── $VETH_NS (veth pair)   │    │"
echo "  │  │  └── $PHYSICAL_IF (物理网口) │    │"
echo "  │  └─────────────────────────────┘    │"
echo "  └─────────────────────────────────────┘"
echo ""

echo "==================== Libvirt 配置 ===================="
echo ""
echo "在虚拟机 XML 中使用以下配置:"
echo ""
cat << 'EOF'
<interface type='bridge'>
  <mac address='52:54:00:xx:xx:xx'/>
  <source bridge='br-vm-ns1'/>
  <model type='virtio'/>
</interface>
EOF

echo ""
echo "或使用 virsh 命令:"
echo "  virsh attach-interface --domain <VM_NAME> --type bridge --source br-vm-ns1 --model virtio --persistent"
echo ""
echo "==================== 验证命令 ===================="
echo ""
echo "# 查看主机网桥"
echo "ip link show $LIBVIRT_BRIDGE"
echo ""
echo "# 查看 ns 中的网桥"
echo "ip netns exec $NS_NAME ip link show $BRIDGE_NAME"
echo ""
echo "# 查看网桥端口"
echo "ip netns exec $NS_NAME bridge link show"
echo ""
echo "# 测试连通性 (在 VM 中)"
echo "ping <目标IP>"