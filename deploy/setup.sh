#!/bin/bash
# Ubuntu 初始化脚本
# 用于部署多 Agent 一体化平台
#
# 用法: sudo bash deploy/setup.sh

set -e

echo "========== Ubuntu 多 Agent 部署平台初始化 =========="

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 获取项目根目录（脚本所在目录的父目录）
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 安装系统依赖
echo "[1] 安装系统依赖..."
apt update
apt install -y python3 python3-pip python3-venv git curl wget net-tools nmap tcpreplay tcpdump

# 创建或更新虚拟环境
echo "[2] 创建 Python 虚拟环境..."
if [ ! -d "/opt/venv" ]; then
    python3 -m venv /opt/venv
    echo "  虚拟环境已创建: /opt/venv"
else
    echo "  虚拟环境已存在，跳过"
fi

# 安装 Python 依赖
echo "[3] 安装 Python 依赖..."
source /opt/venv/bin/activate
pip install --upgrade pip

if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    pip install -r "$PROJECT_DIR/requirements.txt"
else
    pip install django flask flask-cors scapy psutil requests
    pip install pymodbus python-snap7 || echo "部分工控协议库安装失败，可忽略"
fi

# 创建工作目录
echo "[4] 创建工作目录..."
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/cookie_cache"
mkdir -p "$PROJECT_DIR/knowledge_templates"

# 部署授权工具
echo "[5] 部署授权工具..."
echo "  检查 lic_gen..."
if [ -f "$PROJECT_DIR/license/lic_gen" ] && [ -x "$PROJECT_DIR/license/lic_gen" ]; then
    echo "    ✓ lic_gen 已就绪"
else
    echo "    ✗ lic_gen 未部署，请手动拷贝:"
    echo "      scp tdhx@10.40.24.17:/home/tdhx/license/x64/lic_gen $PROJECT_DIR/license/"
fi

echo "  检查 licgen..."
if [ -f "$PROJECT_DIR/license/licgen" ] && [ -x "$PROJECT_DIR/license/licgen" ]; then
    echo "    ✓ licgen 已就绪"
else
    echo "    ✗ licgen 未部署，请手动拷贝:"
    echo "      scp tdhx@10.40.24.17:/home/tdhx/license/x64/licgen $PROJECT_DIR/license/"
fi

echo "  检查 libcrypto.so.1.0.0..."
if [ -f "/usr/lib/libcrypto.so.1.0.0" ]; then
    echo "    ✓ libcrypto.so.1.0.0 已就绪"
else
    echo "    ✗ libcrypto.so.1.0.0 未部署，请手动拷贝:"
    echo "      scp tdhx@10.40.24.17:/usr/lib/libcrypto.so.1.0.0 /usr/lib/"
fi

echo "  检查 hx_knowledge_license_gender.py..."
if [ -f "$PROJECT_DIR/license/hx_knowledge_license_gender.py" ]; then
    echo "    ✓ 知识库授权工具已就绪"
fi

# 复制 sudoers 配置
echo "[6] 配置 sudo 权限..."
if [ -f "$PROJECT_DIR/deploy/sudoers.django" ]; then
    cp "$PROJECT_DIR/deploy/sudoers.django" /etc/sudoers.d/django
    chmod 440 /etc/sudoers.d/django
    echo "  sudoers 配置已安装"
else
    echo "  deploy/sudoers.django 不存在，跳过"
fi

# 安装 Django systemd 服务
echo "[7] 安装 Django 服务..."
if [ -f "$PROJECT_DIR/deploy/django.service" ]; then
    # 替换默认路径为实际项目路径
    sed "s|/opt/sfw_deploy|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/django.service" > /etc/systemd/system/django.service
    systemctl daemon-reload
    systemctl enable django
    echo "  Django 服务已安装"
else
    echo "  deploy/django.service 不存在，跳过"
fi

# 数据库迁移
echo "[8] 执行数据库迁移..."
cd "$PROJECT_DIR"
source /opt/venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput || true
cd "$OLDPWD"

# 完成
echo ""
echo "========== 初始化完成 =========="
echo ""
echo "下一步操作:"
echo "  1. 配置管理网卡 IP（根据实际网络环境）"
echo "  2. 启动 Django: systemctl start django"
echo "  3. 打开 Web 界面: http://<管理网卡IP>:8000"
echo "  4. 在 Web 界面创建 Agent"
echo ""
echo "常见操作:"
echo "  - 同步代码:     python $PROJECT_DIR/sync_to_ubuntu.py"
echo "  - 重启服务:     python $PROJECT_DIR/restart_ubuntu.py"
echo "  - 查看日志:     journalctl -u django -f"
echo ""
