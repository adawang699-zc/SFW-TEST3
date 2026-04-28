#!/bin/bash
# network-namespace-setup.sh - Network Namespace 配置脚本
# 功能：创建独立的网络 namespace，让 eth1 和 eth2 像两台独立机器
# 用法：
#   sudo ./network-namespace-setup.sh setup    # 设置 namespace
#   sudo ./network-namespace-setup.sh start    # 启动 namespace 内的服务
#   sudo ./network-namespace-setup.sh stop     # 停止 namespace 内的服务
#   sudo ./network-namespace-setup.sh status   # 查看状态
#   sudo ./network-namespace-setup.sh restore  # 恢复原配置

set -e

# 配置
NS_CLIENT="ns-eth1"
NS_SERVER="ns-eth2"
ETH1="eth1"
ETH2="eth2"
ETH1_IP="192.168.11.100/16"
ETH2_IP="192.168.12.100/16"
PROJECT_PATH="/opt/SFW-TEST3"
PYTHON_PATH="/opt/SFW-TEST3/sfw/bin/python"

# 日志
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# 创建 namespace
create_namespace() {
    log "创建 network namespace..."

    # 创建 namespace（如果不存在）
    if ! ip netns list | grep -q "$NS_CLIENT"; then
        ip netns add "$NS_CLIENT"
        log "创建 namespace: $NS_CLIENT"
    fi

    if ! ip netns list | grep -q "$NS_SERVER"; then
        ip netns add "$NS_SERVER"
        log "创建 namespace: $NS_SERVER"
    fi
}

# 将物理接口移入 namespace
move_interfaces() {
    log "将接口移入 namespace..."

    # 检查接口是否在主 namespace
    if ip link show "$ETH1" 2>/dev/null | grep -q "link/ether"; then
        # 先关闭接口
        ip link set "$ETH1" down
        # 移入 namespace
        ip link set "$ETH1" netns "$NS_CLIENT"
        log "移入 $ETH1 -> $NS_CLIENT"
    fi

    if ip link show "$ETH2" 2>/dev/null | grep -q "link/ether"; then
        ip link set "$ETH2" down
        ip link set "$ETH2" netns "$NS_SERVER"
        log "移入 $ETH2 -> $NS_SERVER"
    fi
}

# 配置 namespace 内的网络
configure_namespace_network() {
    log "配置 namespace 内网络..."

    # 配置 ns-eth1
    ip netns exec "$NS_CLIENT" ip link set "$ETH1" up
    ip netns exec "$NS_CLIENT" ip addr add "$ETH1_IP" dev "$ETH1"
    # 添加默认路由（如果需要出网）
    # ip netns exec "$NS_CLIENT" ip route add default via 192.168.11.1

    # 配置 ns-eth2
    ip netns exec "$NS_SERVER" ip link set "$ETH2" up
    ip netns exec "$NS_SERVER" ip addr add "$ETH2_IP" dev "$ETH2"

    log "网络配置完成"
}

# 启动 namespace 内的服务
start_services() {
    log "启动 namespace 内的服务..."

    # 停止原有服务
    systemctl stop agent-eth1.service 2>/dev/null || true
    systemctl stop agent-eth2.service 2>/dev/null || true

    # 在 namespace 内启动 agent
    # 使用 systemd-run 在 namespace 内运行
    systemd-run --scope --property="NetworkNamespacePath=/var/run/netns/$NS_CLIENT" \
        "$PYTHON_PATH" "$PROJECT_PATH/agents/run_agent.py" eth1 &
    log "启动 agent-eth1 in $NS_CLIENT"

    systemd-run --scope --property="NetworkNamespacePath=/var/run/netns/$NS_SERVER" \
        "$PYTHON_PATH" "$PROJECT_PATH/agents/run_agent.py" eth2 &
    log "启动 agent-eth2 in $NS_SERVER"

    log "服务启动完成"
}

# 停止 namespace 内的服务
stop_services() {
    log "停止 namespace 内的服务..."

    # 查找并杀死 namespace 内的进程
    for pid in $(ip netns pids "$NS_CLIENT" 2>/dev/null); do
        kill "$pid" 2>/dev/null || true
    done

    for pid in $(ip netns pids "$NS_SERVER" 2>/dev/null); do
        kill "$pid" 2>/dev/null || true
    done

    log "服务停止完成"
}

# 查看状态
show_status() {
    log "=== Network Namespace 状态 ==="

    echo ""
    echo "Namespace 列表:"
    ip netns list

    echo ""
    echo "=== $NS_CLIENT 网络配置 ==="
    ip netns exec "$NS_CLIENT" ip addr show 2>/dev/null || echo "namespace 不存在"
    ip netns exec "$NS_CLIENT" ip route show 2>/dev/null || true

    echo ""
    echo "=== $NS_SERVER 网络配置 ==="
    ip netns exec "$NS_SERVER" ip addr show 2>/dev/null || echo "namespace 不存在"
    ip netns exec "$NS_SERVER" ip route show 2>/dev/null || true

    echo ""
    echo "=== Namespace 内进程 ==="
    ip netns pids "$NS_CLIENT" 2>/dev/null || echo "无进程"
    ip netns pids "$NS_SERVER" 2>/dev/null || echo "无进程"

    echo ""
    echo "=== Ping 测试（eth1 -> eth2）==="
    ip netns exec "$NS_CLIENT" ping -c 2 192.168.12.100 2>/dev/null || echo "ping 失败"
}

# 恢复原配置
restore_original() {
    log "恢复原配置..."

    # 停止 namespace 内的服务
    stop_services

    # 将接口移回主 namespace
    if ip netns exec "$NS_CLIENT" ip link show "$ETH1" 2>/dev/null | grep -q "link/ether"; then
        ip netns exec "$NS_CLIENT" ip link set "$ETH1" down
        ip netns exec "$NS_CLIENT" ip link set "$ETH1" netns 1
        ip link set "$ETH1" up
        ip addr add "$ETH1_IP" dev "$ETH1" 2>/dev/null || true
        log "恢复 $ETH1 到主 namespace"
    fi

    if ip netns exec "$NS_SERVER" ip link show "$ETH2" 2>/dev/null | grep -q "link/ether"; then
        ip netns exec "$NS_SERVER" ip link set "$ETH2" down
        ip netns exec "$NS_SERVER" ip link set "$ETH2" netns 1
        ip link set "$ETH2" up
        ip addr add "$ETH2_IP" dev "$ETH2" 2>/dev/null || true
        log "恢复 $ETH2 到主 namespace"
    fi

    # 删除 namespace
    ip netns del "$NS_CLIENT" 2>/dev/null || true
    ip netns del "$NS_SERVER" 2>/dev/null || true
    log "删除 namespace"

    # 重新启动原有服务
    systemctl start agent-eth1.service 2>/dev/null || true
    systemctl start agent-eth2.service 2>/dev/null || true

    log "恢复完成，验证:"
    ping -I 192.168.11.100 -c 2 192.168.12.100
}

# 完整设置
setup() {
    log "=== 开始 Network Namespace 设置 ==="

    # 1. 创建 namespace
    create_namespace

    # 2. 将接口移入 namespace
    move_interfaces

    # 3. 配置网络
    configure_namespace_network

    # 4. 显示状态
    show_status

    log "=== 设置完成 ==="
    log "提示：现在可以手动启动服务或使用 'start' 命令"
}

# 主函数
case "$1" in
    setup)
        setup
        ;;
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    status)
        show_status
        ;;
    restore)
        restore_original
        ;;
    test)
        log "=== 测试 eth1 -> eth2 连通性 ==="
        ip netns exec "$NS_CLIENT" ping -c 3 192.168.12.100
        ;;
    *)
        echo "用法: $0 {setup|start|stop|status|restore|test}"
        echo ""
        echo "命令说明:"
        echo "  setup  - 创建 namespace 并配置网络"
        echo "  start  - 在 namespace 内启动服务"
        echo "  stop   - 停止 namespace 内的服务"
        echo "  status - 查看 namespace 状态"
        echo "  restore- 恢复原配置"
        echo "  test   - 测试 eth1 -> eth2 连通性"
        exit 1
        ;;
esac