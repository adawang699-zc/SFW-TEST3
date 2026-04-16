#!/bin/bash
# Ubuntu 初始化脚本
# 用于部署多 Agent 一体化平台

set -e

echo "========== Ubuntu 多 Agent 部署平台初始化 =========="

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 安装系统依赖
echo "[1] 安装系统依赖..."
apt update
apt install -y python3 python3-pip python3-venv git curl wget net-tools

# 创建工作目录
echo "[2] 创建工作目录..."
mkdir -p /opt/sfw_deploy
mkdir -p /opt/sfw_deploy/logs
mkdir -p /opt/sfw_deploy/packets

# 创建 Python 虚拟环境
echo "[3] 创建 Python 虚拟环境..."
python3 -m venv /opt/venv
source /opt/venv/bin/activate

# 安装 Python 依赖
echo "[4] 安装 Python 依赖..."
pip install django flask flask-cors scapy psutil requests

# 安装工控协议依赖（可选）
pip install pymodbus python-snap7 || echo "部分工控协议库安装失败，可忽略"

# 克隆项目（如果是从 Git 部署）
if [ ! -d "/opt/sfw_deploy/.git" ]; then
    echo "[5] 克隆项目..."
    # git clone <your-repo-url> /opt/sfw_deploy
    echo "请手动克隆项目到 /opt/sfw_deploy"
fi

# 复制 sudoers 配置
echo "[6] 配置 sudo 权限..."
cp deploy/sudoers.django /etc/sudoers.d/django
chmod 440 /etc/sudoers.d/django

# 安装 Django systemd 服务
echo "[7] 安装 Django 服务..."
cp deploy/django.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable django

# 设置管理网卡（需要手动配置）
echo "[8] 配置管理网卡..."
echo "请在 settings.py 或环境变量中设置 MANAGEMENT_INTERFACE"
echo "例如: MANAGEMENT_INTERFACE=eth0"

# 数据库迁移
echo "[9] 执行数据库迁移..."
cd /opt/sfw_deploy
source /opt/venv/bin/activate
python manage.py migrate

# 完成
echo "========== 初始化完成 =========="
echo ""
echo "下一步操作:"
echo "1. 配置网卡 IP 地址（netplan）"
echo "2. 启动 Django: systemctl start django"
echo "3. 打开 Web 界面: http://<管理网卡IP>:8000"
echo "4. 在 Web 界面创建 Agent"
echo ""