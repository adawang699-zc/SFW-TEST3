#!/bin/bash
# ============================================================
# 虚拟机双网卡桥接配置脚本
#
# 每个虚拟机桥接两个网卡:
#   1. br0 (eth0) - 必须桥接
#   2. ns 管理的网口 - 根据配置决定
#
# 使用: sudo ./vm_dual_bridge_config.sh
# ============================================================

set -e

# ======================= 配置区域 =======================

# 公共网桥 (eth0，所有虚拟机都连接)
COMMON_BRIDGE="br0"
COMMON_BRIDGE_MODEL="e1000e"

# 虚拟机 -> 第二网卡映射
# 格式: "虚拟机名称:命名空间:网口名称"
# 示例:
#   "win10:ns-eth1:eth1"        表示 win10 第二网卡桥接到 ns-eth1 中的 eth1
#   "win10-server:ns-eth2:eth2" 表示 win10-server 第二网卡桥接到 ns-eth2 中的 eth2
#   "win10:ns-eth3:eth3"        可以改成其他 ns
VM_SECOND_NIC_MAPPING=(
    "win10:ns-eth1:eth1"
    "win10-server:ns-eth2:eth2"
)

# 第二网卡使用的网卡模型
SECOND_NIC_MODEL="e1000e"

# 主机上创建的网桥名称前缀 (用于第二网卡)
BRIDGE_PREFIX="brv"

# veth pair 名称前缀
VETH_PREFIX="vh"

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
    local ns=$1
    if ! ip netns list | grep -q "^${ns}"; then
        echo "错误: 网络命名空间 '$ns' 不存在"
        echo "当前的命名空间列表:"
        ip netns list
        return 1
    fi
    return 0
}

check_interface_in_ns() {
    local ns=$1
    local iface=$2
    if ! ip netns exec "$ns" ip link show "$iface" &>/dev/null; then
        echo "错误: 接口 '$iface' 不在命名空间 '$ns' 中"
        echo "命名空间 '$ns' 中的接口:"
        ip netns exec "$ns" ip link show
        return 1
    fi
    return 0
}

check_vm_exists() {
    local vm=$1
    if ! virsh dominfo "$vm" &>/dev/null; then
        echo "警告: 虚拟机 '$vm' 不存在或未定义"
        return 1
    fi
    return 0
}

# 确保 br0 存在并运行
ensure_common_bridge() {
    print_header "检查公共网桥 $COMMON_BRIDGE"

    if ip link show "$COMMON_BRIDGE" &>/dev/null; then
        echo "✓ 网桥 $COMMON_BRIDGE 已存在"
        ip link show "$COMMON_BRIDGE" | head -1
    else
        echo "错误: 公共网桥 $COMMON_BRIDGE 不存在!"
        echo "请先创建 br0 并连接 eth0:"
        echo "  ip link add br0 type bridge"
        echo "  ip link set eth0 master br0"
        echo "  ip link set br0 up"
        echo "  ip link set eth0 up"
        return 1
    fi
    return 0
}

# 配置虚拟机的第一网卡 (br0)
setup_first_nic() {
    local vm_name=$1

    echo ""
    echo "[第一网卡] 配置 $vm_name -> $COMMON_BRIDGE"

    if ! check_vm_exists "$vm_name"; then
        return 1
    fi

    # 检查是否已连接到 br0
    if virsh domiflist "$vm_name" 2>/dev/null | grep -q "$COMMON_BRIDGE"; then
        echo "    ✓ 已连接到 $COMMON_BRIDGE"
        return 0
    fi

    # 检查虚拟机状态
    local vm_state=$(virsh domstate "$vm_name" 2>/dev/null)
    if [[ "$vm_state" == "running" ]]; then
        echo "    虚拟机正在运行，需要关闭才能添加接口"
        return 1
    fi

    # 添加接口
    virsh attach-interface --domain "$vm_name" --type bridge --source "$COMMON_BRIDGE" --model "$COMMON_BRIDGE_MODEL" --persistent
    echo "    ✓ 第一网卡配置完成"
    return 0
}

# 配置虚拟机的第二网卡 (ns 管理的网口)
setup_second_nic() {
    local vm_name=$1
    local ns_name=$2
    local physical_if=$3

    # 生成名称
    local ns_id=$(echo "$ns_name" | grep -o '[0-9]*$')
    local bridge_name="${BRIDGE_PREFIX}${ns_id}"
    local veth_host="${VETH_PREFIX}${ns_id}h"
    local veth_ns="${VETH_PREFIX}${ns_id}n"
    local bridge_in_ns="br${ns_id}"

    echo ""
    echo "[第二网卡] 配置 $vm_name -> $ns_name ($physical_if)"
    echo "    网桥(主机): $bridge_name"
    echo "    网桥(ns内): $bridge_in_ns"
    echo "    veth pair:  $veth_host <-> $veth_ns"

    # 检查
    check_ns_exists "$ns_name" || return 1
    check_interface_in_ns "$ns_name" "$physical_if" || return 1

    # 1. 在 ns 中创建网桥
    if ip netns exec "$ns_name" ip link show "$bridge_in_ns" &>/dev/null; then
        echo "    ✓ ns 网桥已存在: $bridge_in_ns"
    else
        ip netns exec "$ns_name" ip link add name "$bridge_in_ns" type bridge
        ip netns exec "$ns_name" ip link set "$bridge_in_ns" up
        echo "    ✓ 创建 ns 网桥: $bridge_in_ns"
    fi

    # 2. 将物理网口加入 ns 网桥
    if ip netns exec "$ns_name" ip link show "$physical_if" 2>/dev/null | grep -q "master $bridge_in_ns"; then
        echo "    ✓ 物理网口 $physical_if 已在网桥中"
    else
        ip netns exec "$ns_name" ip link set "$physical_if" master "$bridge_in_ns"
        ip netns exec "$ns_name" ip link set "$physical_if" up
        echo "    ✓ 物理网口 $physical_if 加入网桥"
    fi

    # 3. 创建 veth pair
    if ip link show "$veth_host" &>/dev/null; then
        ip link delete "$veth_host" 2>/dev/null || true
        echo "    ✓ 删除旧的 veth pair"
    fi
    ip link add "$veth_host" type veth peer name "$veth_ns"
    echo "    ✓ 创建 veth pair: $veth_host <-> $veth_ns"

    # 4. 配置 veth 连接
    ip link set "$veth_ns" netns "$ns_name"
    ip netns exec "$ns_name" ip link set "$veth_ns" up
    ip netns exec "$ns_name" ip link set "$veth_ns" master "$bridge_in_ns"
    ip link set "$veth_host" up
    echo "    ✓ veth 连接配置完成"

    # 5. 创建主机网桥
    if ip link show "$bridge_name" &>/dev/null; then
        echo "    ✓ 主机网桥已存在: $bridge_name"
    else
        ip link add name "$bridge_name" type bridge
        ip link set "$bridge_name" up
        echo "    ✓ 创建主机网桥: $bridge_name"
    fi

    # 将 veth_host 加入主机网桥
    if ip link show "$veth_host" 2>/dev/null | grep -q "master $bridge_name"; then
        echo "    ✓ veth_host 已在网桥中"
    else
        ip link set "$veth_host" master "$bridge_name"
        echo "    ✓ veth_host 加入网桥"
    fi

    # 6. 配置虚拟机接口
    if ! check_vm_exists "$vm_name"; then
        return 1
    fi

    # 检查是否已连接
    if virsh domiflist "$vm_name" 2>/dev/null | grep -q "$bridge_name"; then
        echo "    ✓ 虚拟机已连接到 $bridge_name"
        return 0
    fi

    # 检查虚拟机状态
    local vm_state=$(virsh domstate "$vm_name" 2>/dev/null)
    if [[ "$vm_state" == "running" ]]; then
        echo "    虚拟机正在运行，需要关闭才能添加接口"
        return 1
    fi

    virsh attach-interface --domain "$vm_name" --type bridge --source "$bridge_name" --model "$SECOND_NIC_MODEL" --persistent
    echo "    ✓ 第二网卡配置完成"
    return 0
}

# 显示当前配置状态
show_status() {
    print_header "当前配置状态"

    echo ""
    echo "公共网桥 (第一网卡):"
    echo "----------------------------------------"
    ip link show "$COMMON_BRIDGE" 2>/dev/null | head -3 || echo "  $COMMON_BRIDGE 不存在"

    echo ""
    echo "命名空间网桥 (第二网卡):"
    echo "----------------------------------------"
    for ns in $(ip netns list | awk '{print $1}'); do
        echo "  $ns:"
        ip netns exec "$ns" ip link show type bridge 2>/dev/null | grep -E "^[0-9]+" | while read line; do
            echo "    $line"
        done
    done

    echo ""
    echo "主机网桥列表:"
    echo "----------------------------------------"
    ip link show type bridge 2>/dev/null | grep -E "^[0-9]+" | while read line; do
        echo "  $line"
    done

    echo ""
    echo "虚拟机网络接口:"
    echo "----------------------------------------"
    for vm in $(virsh list --all --name); do
        echo "  $vm:"
        virsh domiflist "$vm" 2>/dev/null | tail -n +3 | while read line; do
            echo "    $line"
        done
    done
}

# 清理所有配置
cleanup_all() {
    print_header "清理所有配置"

    # 关闭虚拟机
    for vm in $(virsh list --name 2>/dev/null); do
        echo "关闭 $vm..."
        virsh shutdown "$vm" 2>/dev/null || true
    done
    sleep 3

    # 清理每个虚拟机的配置
    for mapping in "${VM_SECOND_NIC_MAPPING[@]}"; do
        IFS=':' read -r vm ns iface <<< "$mapping"
        local ns_id=$(echo "$ns" | grep -o '[0-9]*$')
        local bridge_name="${BRIDGE_PREFIX}${ns_id}"
        local veth_host="${VETH_PREFIX}${ns_id}h"
        local bridge_in_ns="br${ns_id}"

        echo ""
        echo "清理 $vm..."

        # 移除虚拟机所有接口 (保留 br0)
        for mac in $(virsh domiflist "$vm" 2>/dev/null | grep -v "$COMMON_BRIDGE" | awk '{print $4}'); do
            if [[ -n "$mac" ]]; then
                virsh detach-interface --domain "$vm" --type bridge --mac "$mac" --persistent 2>/dev/null || true
            fi
        done

        # 删除主机网桥和 veth
        ip link delete "$bridge_name" 2>/dev/null || true
        ip link delete "$veth_host" 2>/dev/null || true

        # 删除 ns 中的网桥
        ip netns exec "$ns" ip link delete "$bridge_in_ns" 2>/dev/null || true
    done

    echo ""
    echo "✓ 清理完成"
}

# ======================= 主程序 =======================

print_header "虚拟机双网卡桥接配置工具"

check_root

# 检查参数
case "${1:-}" in
    status)
        show_status
        exit 0
        ;;
    cleanup)
        cleanup_all
        exit 0
        ;;
    "")
        # 默认执行配置
        ;;
    *)
        echo "用法: $0 [status|cleanup]"
        exit 1
        ;;
esac

# 显示配置
echo ""
echo "配置方案:"
echo "----------------------------------------"
echo "第一网卡 (公共): 所有虚拟机 -> $COMMON_BRIDGE (eth0)"
echo "第二网卡 (独立):"
for mapping in "${VM_SECOND_NIC_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    echo "  $vm -> $ns ($iface)"
done
echo ""

# 确保公共网桥存在
ensure_common_bridge || exit 1

# 关闭所有运行中的虚拟机
print_header "关闭虚拟机"
for vm in $(virsh list --name 2>/dev/null); do
    echo "关闭 $vm..."
    virsh shutdown "$vm" 2>/dev/null || true
done

# 等待虚拟机关闭
echo "等待虚拟机关闭..."
for i in {1..30}; do
    running=$(virsh list --name 2>/dev/null)
    if [[ -z "$running" ]]; then
        echo "✓ 所有虚拟机已关闭"
        break
    fi
    echo "  等待... ($i)"
    sleep 2
done

# 配置每个虚拟机
print_header "配置虚拟机网络"

success_count=0
fail_count=0

for mapping in "${VM_SECOND_NIC_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"

    echo ""
    echo "--------------------------------------------------------"
    echo "配置虚拟机: $vm"
    echo "--------------------------------------------------------"

    # 配置第一网卡
    if setup_first_nic "$vm"; then
        # 配置第二网卡
        if setup_second_nic "$vm" "$ns" "$iface"; then
            ((success_count++))
            echo ""
            echo "✓✓ $vm 双网卡配置完成"
        else
            ((fail_count++))
            echo ""
            echo "✗ $vm 第二网卡配置失败"
        fi
    else
        ((fail_count++))
        echo ""
        echo "✗ $vm 第一网卡配置失败"
    fi
done

# 显示结果
print_header "配置结果"

echo "成功: $success_count"
echo "失败: $fail_count"
echo ""

show_status

# 显示网络拓扑
print_header "网络拓扑"

for mapping in "${VM_SECOND_NIC_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    ns_id=$(echo "$ns" | grep -o '[0-9]*$')
    bridge_name="${BRIDGE_PREFIX}${ns_id}"
    bridge_in_ns="br${ns_id}"
    veth_host="${VETH_PREFIX}${ns_id}h"
    veth_ns="${VETH_PREFIX}${ns_id}n"

    cat << EOF

  ┌───────────────────────────────┐
  │         $vm (VM)              │
  │  ┌─────────┐  ┌───────────┐   │
  │  │ NIC 1   │  │  NIC 2    │   │
  │  │ $COMMON_BRIDGE │  │  $bridge_name │   │
  │  └─────────┘  └───────────┘   │
  └───────┬───────────────┬───────┘
          │               │
          │               │
  ┌───────▼───────┐ ┌─────▼───────┐
  │    $COMMON_BRIDGE    │ │  $bridge_name │
  │   (eth0)      │ │  └─$veth_host│
  │               │ └─────┬───────┘
  └───────────────┘       │ veth
                          │
                  ┌───────▼─────────────┐
                  │      $ns            │
                  │  ┌────────────────┐ │
                  │  │    $bridge_in_ns     │ │
                  │  │  ├─$veth_ns    │ │
                  │  │  └─$iface      │ │
                  │  └────────────────┘ │
                  └─────────────────────┘
EOF
done

echo ""
echo "============================================================"
echo "配置完成! 启动虚拟机:"
echo "============================================================"
for mapping in "${VM_SECOND_NIC_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    echo "  virsh start $vm"
done