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
PYTHON_PATH="/opt/SFW-TEST3/sfw/bin/python"
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
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu_deploy.settings')
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

# 创建单个网卡的namespace
setup_interface() {
    local interface="$1"
    local ip_cidr="$2"
    local port="$3"
    local ns=$(get_namespace_name "$interface")

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

# 创建Agent systemd服务文件
create_agent_service() {
    local interface="$1"
    local ns="$2"
    local ip_cidr="$3"
    local port="$4"
    local service_name=$(get_service_name "$interface")
    local ip=$(echo "$ip_cidr" | cut -d'/' -f1)
    local service_file="/etc/systemd/system/${service_name}.service"

    log "创建服务文件: $service_file (端口: $port)"

    cat > "$service_file" << EOF
[Unit]
Description=Packet Agent $interface (in namespace $ns)
After=network.target
Requires=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_PATH
ExecStart=/usr/bin/ip netns exec $ns $PYTHON_PATH -m gunicorn -w 1 -b $ip:$port --preload --timeout 30 agents.full_agent:app
ExecStop=/usr/bin/ip netns exec $ns $PYTHON_PATH -c "import sys; sys.exit(0)"
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
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu_deploy.settings')
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
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu_deploy.settings')
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
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu_deploy.settings')
import django
django.setup()
from main.models import NetworkInterface, LocalAgent

for iface in NetworkInterface.objects.filter(is_management=False):
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
    remove-interface)
        if [ $# -lt 2 ]; then
            echo "用法: $0 remove-interface [网卡名]"
            exit 1
        fi
        remove_interface "$2"
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
        echo "  setup-interface [网卡] [IP/掩码] [端口]  - 设置网卡到namespace"
        echo "  remove-interface [网卡]                  - 移除网卡namespace"
        echo "  start-agent [网卡]                       - 启动namespace内Agent"
        echo "  stop-agent [网卡]                        - 停止namespace内Agent"
        echo "  status                                   - 查看所有namespace状态"
        echo "  list                                     - 列出所有namespace"
        echo "  setup-all                                - 设置所有业务网卡（从DB读取）"
        echo "  restore-all                              - 恢复所有网卡到主namespace"
        echo "  test [网卡] [目标IP]                     - 测试namespace连通性"
        echo ""
        echo "示例:"
        echo "  sudo $0 setup-interface eth1                    # 从数据库读取配置"
        echo "  sudo $0 setup-all                               # 批量设置所有网卡"
        echo "  sudo $0 status"
        exit 1
        ;;
esac