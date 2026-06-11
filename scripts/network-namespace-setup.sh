#!/bin/bash
# network-namespace-setup.sh - Network Namespace 动态管理脚本
# 功能：为任意网卡创建独立的网络 namespace（从数据库读取配置）
# 用法：
#   sudo ./network-namespace-setup.sh setup-interface <网卡名>          # 设置单个网卡（从DB读取IP/端口）
#   sudo ./network-namespace-setup.sh setup-interface <网卡名> <IP/掩码> <端口>  # 手动指定配置
#   sudo ./network-namespace-setup.sh remove-interface <网卡名>         # 移除网卡namespace
#   sudo ./network-namespace-setup.sh start-agent <网卡名>              # 启动namespace内Agent
#   sudo ./network-namespace-setup.sh stop-agent <网卡名>               # 停止namespace内Agent
#   sudo ./network-namespace-setup.sh status                            # 查看所有namespace状态
#   sudo ./network-namespace-setup.sh list                              # 列出所有namespace
#   sudo ./network-namespace-setup.sh setup-all                         # 设置所有业务网卡（从DB读取）
#   sudo ./network-namespace-setup.sh restore-all                       # 恢复所有网卡到主namespace

set -e

# 配置
PROJECT_PATH="/opt/SFW-TEST3"
PYTHON_PATH="/usr/bin/python3"
export PYTHONPATH="${PROJECT_PATH}:${PROJECT_PATH}/sfw/lib/python3.10/site-packages"
LOG_DIR="/opt/SFW-TEST3/logs"

# 日志
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# 从数据库读取网卡配置
get_interface_config_from_db() {
    local interface="$1"
    local config="$(cd $PROJECT_PATH && $PYTHON_PATH -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoProject.settings')
import django
django.setup()
from main.models import NetworkInterface, LocalAgent

try:
    iface = NetworkInterface.objects.get(name='$interface')
    agent = LocalAgent.objects.filter(interface=iface).first()
    ip = iface.ip_address or ''
    port = agent.port if agent else 8888
    print(f'{ip}|{port}')
except NetworkInterface.DoesNotExist:
    print('')
" 2>/dev/null)"
    echo "$config"
}

# 获取namespace名称
get_namespace_name() {
    local interface="$1"
    echo "ns-${interface}"
}

# 获取服务名称
get_service_name() {
    local interface="$1"
    echo "agent-${interface}-ns"
}

# 检查网卡是否存在（主namespace）
interface_exists_in_main() {
    local interface="$1"
    ip link show "$interface" 2>/dev/null | grep -q "link/ether"
}

# 检查网卡是否在namespace内
interface_in_namespace() {
    local interface="$1"
    ! interface_exists_in_main "$interface"
}

# 检查网卡是否已在namespace的bridge中
interface_in_namespace_bridge() {
    local interface="$1"
    local ns=$(get_namespace_name "$1")
    if ip netns exec "$ns" ip link show "$interface" 2>/dev/null | grep -q "master"; then
        return 0  # 已在bridge中
    fi
    return 1
}

# 创建单个网卡的bridge namespace（支持veth桥接到主namespace）
setup_interface_with_bridge() {
    local interface="$1"
    local ip_cidr="$2"
    local port="$3"
    local bridge_name="${4:-br${interface#eth}}"  # 默认: eth1->br1, eth2->br2
    local ns=$(get_namespace_name "$interface")

    # 安全保护：绝对不能操作管理网口和桥接口
    if [ "$interface" = "eth0" ] || [ "$interface" = "br0" ] || echo "$interface" | grep -qE '^(br|virbr|veth|vnet|vh|docker|bond|tun|tap)'; then
        log "错误: 禁止操作接口 $interface！"
        return 1
    fi

    # 如果未提供IP/端口，从数据库读取
    if [ -z "$ip_cidr" ] || [ -z "$port" ]; then
        log "从数据库读取 $interface 配置..."
        local config=$(get_interface_config_from_db "$interface")
        if [ -z "$config" ]; then
            log "错误: 网卡 $interface 不在数据库中"
            return 1
        fi
        local db_ip=$(echo "$config" | cut -d'|' -f1)
        local db_port=$(echo "$config" | cut -d'|' -f2)
        [ -z "$ip_cidr" ] && ip_cidr="${db_ip}/16"
        [ -z "$port" ] && port="$db_port"
    fi

    log "=== 设置网卡 $interface 到 namespace $ns (bridge模式) ==="
    log "配置: IP=$ip_cidr, Port=$port, Bridge=$bridge_name"

    # 检查网卡是否在主namespace
    if ! interface_exists_in_main "$interface"; then
        log "警告: 网卡 $interface 不在主namespace，可能已在其他namespace"
        for existing_ns in $(ip netns list | awk '{print $1}'); do
            if ip netns exec "$existing_ns" ip link show "$interface" 2>/dev/null | grep -q "link/ether"; then
                log "从 $existing_ns 移回主namespace"
                ip netns exec "$existing_ns" ip link set "$interface" down
                ip netns exec "$existing_ns" ip link set "$interface" netns 1
                break
            fi
        done
        sleep 1
    fi

    # 创建namespace（如果不存在）
    if ! ip netns list | grep -q "^$ns"; then
        ip netns add "$ns"
        log "创建 namespace: $ns"
    fi

    # 将网卡移入namespace
    if interface_exists_in_main "$interface"; then
        ip link set "$interface" down
        ip link set "$interface" netns "$ns"
        log "移入 $interface -> $ns"
    fi

    # 启用loopback
    ip netns exec "$ns" ip link set lo up
    log "启用 $ns 的 loopback"

    # 创建bridge并启动
    ip netns exec "$ns" ip link add name "$bridge_name" type bridge 2>/dev/null || true
    ip netns exec "$ns" ip link set "$bridge_name" up
    log "创建bridge: $bridge_name (已在namespace内)"

    # 启用网卡并加入bridge
    ip netns exec "$ns" ip link set "$interface" up
    ip netns exec "$ns" ip link set "$interface" master "$bridge_name"
    log "将 $interface 加入bridge $bridge_name"

    # 检查是否有veth对端，如果有也加入bridge
    for veth_iface in $(ip netns exec "$ns" ip link show | grep -E "^[0-9]+:.*@if" | awk -F': ' '{print $2}' | cut -d'@' -f1); do
        ip netns exec "$ns" ip link set "$veth_iface" up 2>/dev/null || true
        ip netns exec "$ns" ip link set "$veth_iface" master "$bridge_name" 2>/dev/null && {
            log "将veth $veth_iface 加入bridge $bridge_name"
        } || true
    done

    # 配置IP到bridge（不是到物理网卡！）
    ip netns exec "$ns" ip addr add "$ip_cidr" dev "$bridge_name"
    log "配置 $bridge_name IP: $ip_cidr"

    # 确保路由指向bridge
    local network=$(echo "$ip_cidr" | cut -d'/' -f1 | awk -F. '{print $1"."$2".0.0"}')
    local mask=$(echo "$ip_cidr" | cut -d'/' -f2)
    ip netns exec "$ns" ip route replace "${network}/${mask}" dev "$bridge_name" 2>/dev/null || true
    log "更新路由: ${network}/${mask} dev $bridge_name"

    # 创建Agent服务文件（BIND_INTERFACE=bridge_name）
    create_agent_service "$interface" "$ns" "$ip_cidr" "$port" "$bridge_name"

    # 更新数据库 namespace 字段
    update_db_namespace "$interface" "$ns"

    log "=== $interface bridge模式设置完成 ==="
}

# 移除bridge namespace
remove_interface_with_bridge() {
    local interface="$1"
    local ns=$(get_namespace_name "$interface")
    local service_name=$(get_service_name "$interface")
    local bridge_name="${2:-br${interface#eth}}"

    log "=== 移除网卡 $interface 的 bridge namespace ==="

    # 停止服务
    systemctl stop "$service_name" 2>/dev/null || true
    systemctl disable "$service_name" 2>/dev/null || true
    rm -f "/etc/systemd/system/${service_name}.service"
    systemctl daemon-reload

    # 从bridge中移除网卡
    if ip netns exec "$ns" ip link show "$interface" 2>/dev/null | grep -q "link/ether"; then
        ip netns exec "$ns" ip link set "$interface" nomaster 2>/dev/null || true
        ip netns exec "$ns" ip link set "$interface" down
        ip netns exec "$ns" ip link set "$interface" netns 1
        ip link set "$interface" up
        log "移回 $interface 到主 namespace"
    fi

    # 删除bridge
    ip netns exec "$ns" ip link delete "$bridge_name" type bridge 2>/dev/null || true
    log "删除bridge: $bridge_name"

    # 删除namespace
    ip netns del "$ns" 2>/dev/null || true
    log "删除 namespace: $ns"

    # 清除数据库
    (cd $PROJECT_PATH && $PYTHON_PATH -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoProject.settings')
import django
django.setup()
from main.models import NetworkInterface
try:
    iface = NetworkInterface.objects.get(name='$interface')
    iface.namespace = ''
    iface.save()
    print(f'已清除: {iface.name} namespace')
except Exception as e:
    print(f'数据库更新失败: {e}')
" 2>/dev/null) || log "数据库更新失败（可忽略）"

    log "=== $interface bridge namespace 已移除 ==="
}
setup_interface() {
    local interface="$1"
    local ip_cidr="$2"
    local port="$3"
    local ns=$(get_namespace_name "$interface")

    # 安全保护：绝对不能操作管理网口和桥接口
    if [ "$interface" = "eth0" ] || [ "$interface" = "br0" ] || echo "$interface" | grep -qE '^(br|virbr|veth|vnet|vh|docker|bond|tun|tap)'; then
        log "错误: 禁止操作接口 $interface！"
        return 1
    fi

    # 如果未提供IP/端口，从数据库读取
    if [ -z "$ip_cidr" ] || [ -z "$port" ]; then
        log "从数据库读取 $interface 配置..."
        local config=$(get_interface_config_from_db "$interface")
        if [ -z "$config" ]; then
            log "错误: 网卡 $interface 不在数据库中"
            return 1
        fi
        local db_ip=$(echo "$config" | cut -d'|' -f1)
        local db_port=$(echo "$config" | cut -d'|' -f2)

        if [ -z "$ip_cidr" ]; then
            if [ -z "$db_ip" ]; then
                log "错误: 网卡 $interface 未配置 IP 地址"
                return 1
            fi
            # 默认使用 /16 掩码
            ip_cidr="${db_ip}/16"
        fi
        if [ -z "$port" ]; then
            port="$db_port"
        fi
    fi

    log "=== 设置网卡 $interface 到 namespace $ns ==="
    log "配置: IP=$ip_cidr, Port=$port"

    # 检查网卡是否在主namespace
    if ! interface_exists_in_main "$interface"; then
        log "警告: 网卡 $interface 不在主namespace，可能已在其他namespace"
        # 尝试从其他namespace移回
        for existing_ns in $(ip netns list | awk '{print $1}'); do
            if ip netns exec "$existing_ns" ip link show "$interface" 2>/dev/null | grep -q "link/ether"; then
                log "从 $existing_ns 移回主namespace"
                ip netns exec "$existing_ns" ip link set "$interface" down
                ip netns exec "$existing_ns" ip link set "$interface" netns 1
                break
            fi
        done
        sleep 1
    fi

    # 创建namespace（如果不存在）
    if ! ip netns list | grep -q "^$ns"; then
        ip netns add "$ns"
        log "创建 namespace: $ns"
    fi

    # 将网卡移入namespace
    if interface_exists_in_main "$interface"; then
        ip link set "$interface" down
        ip link set "$interface" netns "$ns"
        log "移入 $interface -> $ns"
    fi

    # 配置namespace内网络
    # 启用loopback（关键！）
    ip netns exec "$ns" ip link set lo up
    log "启用 $ns 的 loopback"

    # 启用网卡并配置IP
    ip netns exec "$ns" ip link set "$interface" up
    ip netns exec "$ns" ip addr add "$ip_cidr" dev "$interface"
    log "配置 $interface IP: $ip_cidr"

    # 创建Agent服务文件
    create_agent_service "$interface" "$ns" "$ip_cidr" "$port"

    # 更新数据库 namespace 字段
    update_db_namespace "$interface" "$ns"

    log "=== $interface 设置完成 ==="
}

# 获取Agent ID
get_agent_id() {
    local interface="$1"
    echo "agent_${interface}"
}

# 创建Agent systemd服务文件
create_agent_service() {
    local interface="$1"
    local ns="$2"
    local ip_cidr="$3"
    local port="$4"
    local bridge_ifs="$5"  # 可选：桥接接口名（如 br1），用于 BIND_INTERFACE
    local service_name=$(get_service_name "$interface")
    local ip=$(echo "$ip_cidr" | cut -d'/' -f1)
    local agent_id=$(get_agent_id "$interface")
    local bind_iface="${bridge_ifs:-$interface}"
    local service_file="/etc/systemd/system/${service_name}.service"

    log "创建服务文件: $service_file (端口: $port, BIND_INTERFACE=$bind_iface)"

    cat > "$service_file" << EOF
[Unit]
Description=Packet Agent $agent_id (in namespace $ns)
After=network.target
Requires=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_PATH
Environment="AGENT_ID=$agent_id"
Environment="BIND_IP=$ip"
Environment="BIND_INTERFACE=$bind_iface"
Environment="AGENT_PORT=$port"
ExecStart=/usr/bin/ip netns exec $ns env PYTHONPATH=${PROJECT_PATH}:${PROJECT_PATH}/sfw/lib/python3.10/site-packages $PYTHON_PATH -m gunicorn -w 1 -b $ip:$port --preload --timeout 30 agents.full_agent:app
ExecStop=/usr/bin/ip netns exec $ns env PYTHONPATH=${PROJECT_PATH}:${PROJECT_PATH}/sfw/lib/python3.10/site-packages $PYTHON_PATH -c "import sys; sys.exit(0)"
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/agent_${interface}_ns.log
StandardError=append:$LOG_DIR/agent_${interface}_ns.log

LimitNOFILE=65535
TimeoutStartSec=30
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    log "服务文件已创建: $service_name"
}

# 更新数据库 namespace 字段
update_db_namespace() {
    local interface="$1"
    local ns="$2"

    log "更新数据库: $interface -> namespace=$ns"
    (cd $PROJECT_PATH && $PYTHON_PATH -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoProject.settings')
import django
django.setup()
from main.models import NetworkInterface

try:
    iface = NetworkInterface.objects.get(name='$interface')
    iface.namespace = '$ns'
    iface.save()
    print(f'已更新: {iface.name} -> namespace={iface.namespace}')
except NetworkInterface.DoesNotExist:
    print(f'警告: 网卡 $interface 不在数据库中')
" 2>/dev/null) || log "数据库更新失败（可忽略）"
}

# 移除网卡的namespace
remove_interface() {
    local interface="$1"
    local ns=$(get_namespace_name "$interface")
    local service_name=$(get_service_name "$interface")

    log "=== 移除网卡 $interface 的 namespace ==="

    # 停止服务
    systemctl stop "$service_name" 2>/dev/null || true
    systemctl disable "$service_name" 2>/dev/null || true

    # 删除服务文件
    rm -f "/etc/systemd/system/${service_name}.service"
    systemctl daemon-reload

    # 将网卡移回主namespace
    if ip netns exec "$ns" ip link show "$interface" 2>/dev/null | grep -q "link/ether"; then
        ip netns exec "$ns" ip link set "$interface" down
        ip netns exec "$ns" ip link set "$interface" netns 1
        ip link set "$interface" up
        log "移回 $interface 到主 namespace"
    fi

    # 删除namespace
    ip netns del "$ns" 2>/dev/null || true
    log "删除 namespace: $ns"

    # 清除数据库 namespace 字段
    (cd $PROJECT_PATH && $PYTHON_PATH -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoProject.settings')
import django
django.setup()
from main.models import NetworkInterface

try:
    iface = NetworkInterface.objects.get(name='$interface')
    iface.namespace = ''
    iface.save()
    print(f'已清除: {iface.name} namespace')
except Exception as e:
    print(f'数据库更新失败: {e}')
" 2>/dev/null) || log "数据库更新失败（可忽略）"

    log "=== $interface namespace 已移除 ==="
}

# 启动namespace内Agent
start_agent() {
    local interface="$1"
    local service_name=$(get_service_name "$interface")

    log "启动 Agent: $service_name"
    systemctl start "$service_name"
    log "Agent $service_name 已启动"
}

# 停止namespace内Agent
stop_agent() {
    local interface="$1"
    local service_name=$(get_service_name "$interface")

    log "停止 Agent: $service_name"
    systemctl stop "$service_name"
    log "Agent $service_name 已停止"
}

# 查看所有namespace状态
show_status() {
    log "=== Network Namespace 状态 ==="

    echo ""
    echo "Namespace 列表:"
    ip netns list

    for ns in $(ip netns list | awk '{print $1}'); do
        echo ""
        echo "=== $ns 网络配置 ==="
        ip netns exec "$ns" ip addr show 2>/dev/null || echo "namespace不存在"
        ip netns exec "$ns" ip route show 2>/dev/null || true

        echo ""
        echo "$ns 内进程:"
        ip netns pids "$ns" 2>/dev/null || echo "无进程"
    done
}

# 列出所有namespace
list_namespaces() {
    ip netns list
}

# 设置所有业务网卡（从数据库读取）
setup_all() {
    log "=== 设置所有业务网卡（从数据库读取） ==="

    # 从数据库读取所有非管理网卡
    local interfaces="$(cd $PROJECT_PATH && $PYTHON_PATH -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'djangoProject.settings')
import django
django.setup()
from main.models import NetworkInterface, LocalAgent

for iface in NetworkInterface.objects.filter(is_management=False):
    # 跳过桥接/虚拟接口
    skip_prefixes = ('br', 'virbr', 'veth', 'vnet', 'vh', 'docker', 'bond', 'tun', 'tap')
    if iface.name.startswith(skip_prefixes):
        continue
    agent_obj = LocalAgent.objects.filter(interface=iface).first()
    port = agent_obj.port if agent_obj else 8888
    ip = iface.ip_address or ''
    if ip:
        print(f'{iface.name}|{ip}|{port}')
" 2>/dev/null)"

    if [ -z "$interfaces" ]; then
        log "数据库中没有找到可配置的业务网卡"
        return 1
    fi

    for line in $interfaces; do
        local iface=$(echo "$line" | cut -d'|' -f1)
        local ip=$(echo "$line" | cut -d'|' -f2)
        local port=$(echo "$line" | cut -d'|' -f3)
        local ip_cidr="${ip}/16"

        # 检查网卡是否在主namespace（未被其他namespace占用）
        if interface_exists_in_main "$iface"; then
            log "配置网卡: $iface (IP=$ip_cidr, Port=$port)"
            setup_interface "$iface" "$ip_cidr" "$port"
        else
            log "跳过 $iface: 不在主namespace"
        fi
    done

    log "=== 所有网卡设置完成 ==="
}

# 恢复所有网卡到主namespace
restore_all() {
    log "=== 恢复所有网卡到主namespace ==="

    for ns in $(ip netns list | awk '{print $1}'); do
        # 提取网卡名（ns-eth1 -> eth1）
        interface=$(echo "$ns" | sed 's/ns-//')

        remove_interface "$interface"
    done

    log "=== 所有网卡已恢复 ==="
}

# 测试namespace连通性
test_namespace() {
    local interface="$1"
    local ns=$(get_namespace_name "$interface")
    local target_ip="$2"

    log "测试 $ns -> $target_ip 连通性"
    ip netns exec "$ns" ping -c 3 "$target_ip"
}

# 主函数
case "$1" in
    setup-interface)
        if [ $# -lt 2 ]; then
            echo "用法: $0 setup-interface [网卡名] [IP/掩码] [端口]"
            echo "示例: $0 setup-interface eth1                    # 从数据库读取配置"
            echo "      $0 setup-interface eth1 192.168.11.100/16 8888  # 手动指定配置"
            exit 1
        fi
        setup_interface "$2" "$3" "$4"
        ;;
    setup-interface-with-bridge)
        if [ $# -lt 2 ]; then
            echo "用法: $0 setup-interface-with-bridge [网卡名] [IP/掩码] [端口] [桥接名]"
            echo "示例: $0 setup-interface-with-bridge eth1                     # 从数据库读取配置"
            echo "      $0 setup-interface-with-bridge eth1 192.168.11.100/16 8888 br1"
            exit 1
        fi
        setup_interface_with_bridge "$2" "$3" "$4" "$5"
        ;;
    remove-interface)
        if [ $# -lt 2 ]; then
            echo "用法: $0 remove-interface [网卡名]"
            exit 1
        fi
        # 自动检测并选择正确的移除方式
        local ns_for_remove=$(get_namespace_name "$2")
        if ip netns exec "$ns_for_remove" ip link show "$2" 2>/dev/null | grep -q "master"; then
            log "检测到bridge模式，使用bridge移除方式"
            remove_interface_with_bridge "$2"
        else
            remove_interface "$2"
        fi
        ;;
    start-agent)
        if [ $# -lt 2 ]; then
            echo "用法: $0 start-agent [网卡名]"
            exit 1
        fi
        start_agent "$2"
        ;;
    stop-agent)
        if [ $# -lt 2 ]; then
            echo "用法: $0 stop-agent [网卡名]"
            exit 1
        fi
        stop_agent "$2"
        ;;
    status)
        show_status
        ;;
    list)
        list_namespaces
        ;;
    setup-all)
        setup_all
        ;;
    restore-all)
        restore_all
        ;;
    test)
        if [ $# -lt 3 ]; then
            echo "用法: $0 test [网卡名] [目标IP]"
            exit 1
        fi
        test_namespace "$2" "$3"
        ;;
    *)
        echo "用法: $0 {command} [参数]"
        echo ""
        echo "命令说明:"
        echo "  setup-interface [网卡] [IP/掩码] [端口]            - 设置网卡到namespace（标准模式）"
        echo "  setup-interface-with-bridge [网卡] [IP/掩码] [端口] [桥接名]  - 设置网卡到namespace（桥接模式，IP在bridge上）"
        echo "  remove-interface [网卡]                            - 移除网卡namespace（自动检测模式）"
        echo "  start-agent [网卡]                                 - 启动namespace内Agent"
        echo "  stop-agent [网卡]                                  - 停止namespace内Agent"
        echo "  status                                             - 查看所有namespace状态"
        echo "  list                                               - 列出所有namespace"
        echo "  setup-all                                          - 设置所有业务网卡（从DB读取）"
        echo "  restore-all                                        - 恢复所有网卡到主namespace"
        echo "  test [网卡] [目标IP]                               - 测试namespace连通性"
        echo ""
        echo "示例:"
        echo "  sudo $0 setup-interface eth1                              # 标准模式从数据库读取"
        echo "  sudo $0 setup-interface-with-bridge eth1                  # 桥接模式从数据库读取"
        echo "  sudo $0 setup-interface-with-bridge eth1 192.168.11.100/16 8888 br1  # 手动指定"
        echo "  sudo $0 setup-all                                         # 批量设置（标准模式）"
        echo "  sudo $0 remove-interface eth1                            # 自动检测模式移除"
        echo "  sudo $0 status"
        exit 1
        ;;
esac