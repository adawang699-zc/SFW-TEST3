#!/bin/bash
# ============================================================
# 虚拟机桥接到指定 ns 管理网口 - 配置脚本
#
# 场景: 将 KVM 虚拟机桥接到指定的网络命名空间(ns)管理的网口
# 使用: sudo ./vm_ns_bridge_config.sh
# ============================================================

set -e

# ======================= 配置区域 =======================
# 请根据实际需求修改以下映射关系

# 虚拟机 -> 命名空间映射
# 格式: "虚拟机名称:命名空间:网口名称"
# 示例:
#   "win10:ns-eth1:eth1"     表示 win10 虚拟机桥接到 ns-eth1 中的 eth1
#   "win10-server:ns-eth2:eth2" 表示 win10-server 虚拟机桥接到 ns-eth2 中的 eth2
VM_NS_MAPPING=(
    "win10:ns-eth1:eth1"
    "win10-server:ns-eth2:eth2"
)

# 主机上创建的网桥名称前缀
BRIDGE_PREFIX="brv"

# veth pair 名称前缀 (注意: Linux接口名限制15字符)
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

# 创建单个虚拟机的网络配置
setup_vm_network() {
    local vm_name=$1
    local ns_name=$2
    local physical_if=$3

    # 生成名称 (接口名限制15字符，使用短名称)
    # 从 ns-eth1 提取数字 1
    local ns_id=$(echo "$ns_name" | grep -o '[0-9]*$')
    local bridge_name="${BRIDGE_PREFIX}${ns_id}"          # brv1 (4字符)
    local veth_host="${VETH_PREFIX}${ns_id}h"             # vh1h (4字符)
    local veth_ns="${VETH_PREFIX}${ns_id}n"               # vh1n (4字符)
    local bridge_in_ns="br${ns_id}"                       # br1 (3字符)

    print_header "配置虚拟机: $vm_name -> $ns_name / $physical_if"

    # 检查
    check_ns_exists "$ns_name" || return 1
    check_interface_in_ns "$ns_name" "$physical_if" || return 1

    echo "命名空间: $ns_name"
    echo "物理网口: $physical_if"
    echo "网桥(主机): $bridge_name"
    echo "网桥(ns内): $bridge_in_ns"
    echo "veth pair: $veth_host <-> $veth_ns"
    echo ""

    # 1. 在 ns 中创建网桥
    echo "[1/6] 在 $ns_name 中创建网桥 $bridge_in_ns..."
    if ip netns exec "$ns_name" ip link show "$bridge_in_ns" &>/dev/null; then
        echo "    网桥已存在，跳过"
    else
        ip netns exec "$ns_name" ip link add name "$bridge_in_ns" type bridge
        ip netns exec "$ns_name" ip link set "$bridge_in_ns" up
        echo "    ✓ 网桥创建完成"
    fi

    # 2. 将物理网口加入 ns 中的网桥
    echo "[2/6] 将 $physical_if 加入网桥..."
    if ip netns exec "$ns_name" ip link show "$physical_if" 2>/dev/null | grep -q "master $bridge_in_ns"; then
        echo "    接口已在网桥中，跳过"
    else
        ip netns exec "$ns_name" ip link set "$physical_if" master "$bridge_in_ns"
        ip netns exec "$ns_name" ip link set "$physical_if" up
        echo "    ✓ 接口已加入网桥"
    fi

    # 3. 创建 veth pair
    echo "[3/6] 创建 veth pair..."
    if ip link show "$veth_host" &>/dev/null; then
        echo "    veth pair 已存在，删除旧的..."
        ip link delete "$veth_host" 2>/dev/null || true
    fi
    ip link add "$veth_host" type veth peer name "$veth_ns"
    echo "    ✓ veth pair 创建完成: $veth_host <-> $veth_ns"

    # 4. 将 veth_ns 移动到 ns 并加入网桥
    echo "[4/6] 配置 veth 连接..."
    ip link set "$veth_ns" netns "$ns_name"
    ip netns exec "$ns_name" ip link set "$veth_ns" up
    ip netns exec "$ns_name" ip link set "$veth_ns" master "$bridge_in_ns"
    ip link set "$veth_host" up
    echo "    ✓ veth 配置完成"

    # 5. 在主机创建网桥并连接 veth_host
    echo "[5/6] 创建主机网桥 $bridge_name..."
    if ip link show "$bridge_name" &>/dev/null; then
        echo "    网桥已存在"
    else
        ip link add name "$bridge_name" type bridge
        ip link set "$bridge_name" up
        echo "    ✓ 主机网桥创建完成"
    fi

    # 将 veth_host 加入主机网桥
    if ip link show "$veth_host" 2>/dev/null | grep -q "master $bridge_name"; then
        echo "    veth_host 已在网桥中"
    else
        ip link set "$veth_host" master "$bridge_name"
        echo "    ✓ veth_host 已加入网桥"
    fi

    # 6. 配置虚拟机
    echo "[6/6] 配置虚拟机 $vm_name..."
    if check_vm_exists "$vm_name"; then
        # 检查虚拟机是否已有该网桥的接口
        if virsh domiflist "$vm_name" 2>/dev/null | grep -q "$bridge_name"; then
            echo "    虚拟机已连接到 $bridge_name"
        else
            # 虚拟机需要关闭才能添加接口
            local vm_state=$(virsh domstate "$vm_name" 2>/dev/null)
            if [[ "$vm_state" == "running" ]]; then
                echo "    警告: 虚拟机正在运行，需要先关闭才能添加接口"
                echo "    运行: virsh shutdown $vm_name"
                echo "    然后运行: virsh attach-interface --domain $vm_name --type bridge --source $bridge_name --model virtio --persistent"
            else
                virsh attach-interface --domain "$vm_name" --type bridge --source "$bridge_name" --model virtio --persistent
                echo "    ✓ 虚拟机接口配置完成"
            fi
        fi
    else
        echo "    虚拟机不存在，请手动配置"
        echo "    virsh attach-interface --domain $vm_name --type bridge --source $bridge_name --model virtio --persistent"
    fi

    echo ""
    echo "✓ $vm_name 配置完成"
    return 0
}

# 显示当前配置状态
show_status() {
    print_header "当前配置状态"

    echo ""
    echo "命名空间列表:"
    echo "----------------------------------------"
    for ns in $(ip netns list | awk '{print $1}'); do
        echo "  $ns:"
        ip netns exec "$ns" ip link show 2>/dev/null | grep -E "^[0-9]+" | while read line; do
            echo "    $line"
        done
    done

    echo ""
    echo "虚拟机列表:"
    echo "----------------------------------------"
    virsh list --all

    echo ""
    echo "虚拟机网络接口:"
    echo "----------------------------------------"
    for vm in $(virsh list --all --name); do
        echo "  $vm:"
        virsh domiflist "$vm" 2>/dev/null | tail -n +3 | while read line; do
            echo "    $line"
        done
    done

    echo ""
    echo "主机网桥:"
    echo "----------------------------------------"
    ip link show type bridge 2>/dev/null | grep -E "^[0-9]+" | while read line; do
        echo "  $line"
    done
}

# 清理函数
cleanup() {
    local vm_name=$1
    local ns_name=$2

    # 从 ns-eth1 提取数字 1
    local ns_id=$(echo "$ns_name" | grep -o '[0-9]*$')
    local bridge_name="${BRIDGE_PREFIX}${ns_id}"
    local veth_host="${VETH_PREFIX}${ns_id}h"
    local bridge_in_ns="br${ns_id}"

    print_header "清理配置: $vm_name / $ns_name"

    # 从虚拟机移除接口
    if check_vm_exists "$vm_name"; then
        local mac=$(virsh domiflist "$vm_name" 2>/dev/null | grep "$bridge_name" | awk '{print $2}')
        if [[ -n "$mac" ]]; then
            echo "从虚拟机移除接口..."
            virsh detach-interface --domain "$vm_name" --type bridge --mac "$mac" --persistent 2>/dev/null || true
        fi
    fi

    # 删除主机网桥和 veth
    echo "删除主机网桥和 veth..."
    ip link delete "$bridge_name" 2>/dev/null || true
    ip link delete "$veth_host" 2>/dev/null || true

    # 删除 ns 中的网桥
    echo "删除 ns 中的网桥..."
    ip netns exec "$ns_name" ip link delete "$bridge_in_ns" 2>/dev/null || true

    echo "✓ 清理完成"
}

# ======================= 主程序 =======================

print_header "虚拟机网络桥接配置工具"

check_root

# 检查参数
case "${1:-}" in
    status)
        show_status
        exit 0
        ;;
    cleanup)
        if [[ -z "${2:-}" ]]; then
            echo "用法: $0 cleanup <vm-name>"
            exit 1
        fi
        # 查找 vm 对应的 ns
        for mapping in "${VM_NS_MAPPING[@]}"; do
            IFS=':' read -r vm ns iface <<< "$mapping"
            if [[ "$vm" == "$2" ]]; then
                cleanup "$vm" "$ns"
                exit 0
            fi
        done
        echo "未找到虚拟机 $2 的配置"
        exit 1
        ;;
    "")
        # 默认执行配置
        ;;
    *)
        echo "用法: $0 [status|cleanup <vm-name>]"
        exit 1
        ;;
esac

echo "配置映射:"
echo "----------------------------------------"
for mapping in "${VM_NS_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    echo "  $vm -> $ns ($iface)"
done
echo ""

# 执行配置
success_count=0
fail_count=0

for mapping in "${VM_NS_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    if setup_vm_network "$vm" "$ns" "$iface"; then
        ((success_count++))
    else
        ((fail_count++))
    fi
done

# 显示结果
print_header "配置结果"

echo "成功: $success_count"
echo "失败: $fail_count"
echo ""

show_status

echo ""
echo "============================================================"
echo "网络拓扑示意"
echo "============================================================"
echo ""
for mapping in "${VM_NS_MAPPING[@]}"; do
    IFS=':' read -r vm ns iface <<< "$mapping"
    # 从 ns-eth1 提取数字 1
    ns_id=$(echo "$ns" | grep -o '[0-9]*$')
    bridge_name="${BRIDGE_PREFIX}${ns_id}"
    bridge_in_ns="br${ns_id}"
    veth_host="${VETH_PREFIX}${ns_id}h"
    veth_ns="${VETH_PREFIX}${ns_id}n"

    cat << EOF
  ┌──────────────────┐
  │ $vm (VM)         │
  └────────┬─────────┘
           │
  ┌────────▼─────────┐
  │ $bridge_name     │  (主机网桥)
  │   └── $veth_host │
  └────────┬─────────┘
           │ veth pair
  ┌────────▼─────────────────────┐
  │ $ns                          │
  │  ┌────────────────────────┐  │
  │  │ $bridge_in_ns          │  │
  │  │   ├── $veth_ns         │  │
  │  │   └── $iface (物理)    │  │
  │  └────────────────────────┘  │
  └──────────────────────────────┘

EOF
done