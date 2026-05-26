#!/usr/bin/env python3
"""Ubuntu 多 Agent 一体化平台部署脚本

首次部署或新环境初始化时，在项目根目录下以 root 身份执行:

    sudo python3 setup.py

此脚本会自动完成:
  1. 安装系统依赖
  2. 创建虚拟环境和安装 Python 包
  3. 部署授权工具（lic_gen / licgen）
  4. 设置目录权限
  5. 执行数据库迁移
  6. 安装 systemd 服务
  7. 验证环境
"""

import os
import sys
import subprocess
import shutil
import getpass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LICENSE_DIR = PROJECT_ROOT / "license"
LOG_DIR = PROJECT_ROOT / "logs"

# ============================================================
# 工具函数
# ============================================================

def step(seq: int, title: str):
    """打印步骤标题"""
    print(f"\n{'=' * 60}")
    print(f"  [{seq}] {title}")
    print(f"{'=' * 60}")

def run(cmd: list, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """执行命令并打印输出"""
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        print(f"  [失败] 返回码 {result.returncode}")
        sys.exit(1)
    return result

def confirm(prompt: str) -> bool:
    """询问用户确认"""
    answer = input(f"  > {prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes")

# ============================================================
# 安装步骤
# ============================================================

def check_root():
    """Step 0: 检查 root 权限"""
    if os.geteuid() != 0:
        print("[错误] 请使用 sudo 运行此脚本: sudo python3 setup.py")
        sys.exit(1)

def check_python():
    """Step 0: 检查 Python 版本"""
    major, minor = sys.version_info[:2]
    if major < 3 or (major == 3 and minor < 10):
        print(f"[错误] 需要 Python 3.10+, 当前为 {major}.{minor}")
        sys.exit(1)
    print(f"  Python {major}.{minor} ✓")

def install_system_deps():
    """Step 1: 安装系统依赖"""
    step(1, "安装系统依赖")
    run(["apt", "update"])
    run(["apt", "install", "-y",
         "python3", "python3-pip", "python3-venv",
         "git", "curl", "wget", "net-tools",
         "nmap", "tcpreplay", "tcpdump",
         "wine"])

def create_venv():
    """Step 2: 创建虚拟环境"""
    step(2, "创建 Python 虚拟环境")
    venv_path = Path("/opt/venv")
    if venv_path.exists():
        print("  虚拟环境已存在，跳过")
        return str(venv_path / "bin" / "python")

    run(["python3", "-m", "venv", str(venv_path)])
    print(f"  虚拟环境创建完成: {venv_path}")
    return str(venv_path / "bin" / "python")

def install_python_deps(python_bin: str):
    """Step 3: 安装 Python 依赖"""
    step(3, "安装 Python 依赖")

    # 使用 requirements.txt
    req_file = PROJECT_ROOT / "requirements.txt"
    if req_file.exists():
        run([python_bin, "-m", "pip", "install", "--upgrade", "pip"])
        run([python_bin, "-m", "pip", "install", "-r", str(req_file)])
    else:
        print("  requirements.txt 不存在，安装默认依赖")
        deps = [
            "django>=5.1", "flask>=2.0", "flask-cors>=3.0",
            "scapy>=2.5", "psutil>=5.9", "requests>=2.28",
        ]
        run([python_bin, "-m", "pip", "install"] + deps)

def create_directories():
    """Step 4: 创建工作目录"""
    step(4, "创建工作目录")
    dirs = [
        LICENSE_DIR,
        LOG_DIR,
        PROJECT_ROOT / "cookie_cache",
        PROJECT_ROOT / "knowledge_templates",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  创建目录: {d}")

def deploy_license_tools():
    """Step 5: 部署授权工具"""
    step(5, "部署授权工具")

    print("  需要从远程授权服务器 10.40.24.17 拷贝以下文件:")
    print("    - lic_gen   (DR 方式设备授权)")
    print("    - licgen    (dev-Code 方式设备授权)")
    print("    - libcrypto.so.1.0.0  (licgen 依赖的 OpenSSL 库)")
    print()

    # 检查文件是否已存在
    lic_gen = LICENSE_DIR / "lic_gen"
    licgen = LICENSE_DIR / "licgen"
    libcrypto = Path("/usr/lib/libcrypto.so.1.0.0")
    py_tool = LICENSE_DIR / "hx_knowledge_license_gender.py"

    all_ok = True
    for f, desc in [(lic_gen, "lic_gen"), (licgen, "licgen"),
                    (libcrypto, "libcrypto.so.1.0.0")]:
        if f.exists() and os.access(f, os.X_OK):
            print(f"  ✓ {desc} 已就绪")
        else:
            all_ok = False
            print(f"  ✗ {desc} 未找到或缺少执行权限")

    # Python 版知识库授权工具
    if py_tool.exists():
        print(f"  ✓ hx_knowledge_license_gender.py 已就绪")

    if all_ok:
        print("\n  所有授权工具已就绪 ✓")
        return

    print()
    if not confirm("是否从 10.40.24.17 拷贝授权工具?"):
        print("  跳过授权工具部署，请稍后手动部署")
        return

    # SCP 拷贝
    remote_user = input("  远程服务器用户名 [tdhx]: ").strip() or "tdhx"
    remote_host = "10.40.24.17"

    remote_base = f"{remote_user}@{remote_host}"

    scp_files = [
        (f"{remote_base}:/home/tdhx/license/x64/lic_gen", str(lic_gen)),
        (f"{remote_base}:/home/tdhx/license/x64/licgen", str(licgen)),
        (f"{remote_base}:/usr/lib/libcrypto.so.1.0.0", str(libcrypto)),
    ]

    for src, dst in scp_files:
        print(f"  拷贝 {src}")
        result = subprocess.run(
            ["scp", src, dst],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(f"    -> {dst} ✓")
        else:
            print(f"    -> 失败: {result.stderr.strip()}")

    # 设置执行权限
    print("\n  设置执行权限...")
    for f in [lic_gen, licgen]:
        if f.exists():
            f.chmod(0o755)
            print(f"  chmod +x {f}")

    # 更新动态链接库缓存
    run(["ldconfig"], check=False)

def setup_sudoers():
    """Step 6: 配置 sudo 权限"""
    step(6, "配置 sudo 权限")

    sudoers_src = PROJECT_ROOT / "deploy" / "sudoers.django"
    sudoers_dst = Path("/etc/sudoers.d/django")

    if sudoers_dst.exists():
        print("  sudoers 配置已存在，跳过")
        return

    if not sudoers_src.exists():
        print("  deploy/sudoers.django 不存在，跳过")
        return

    shutil.copy2(sudoers_src, sudoers_dst)
    sudoers_dst.chmod(0o440)
    print(f"  已安装: {sudoers_dst}")

def setup_systemd():
    """Step 7: 安装 Django systemd 服务"""
    step(7, "安装 Django systemd 服务")

    service_src = PROJECT_ROOT / "deploy" / "django.service"
    service_dst = Path("/etc/systemd/system/django.service")

    if not service_src.exists():
        print("  deploy/django.service 不存在，跳过")
        return

    # 读取模板并替换路径
    content = service_src.read_text()
    content = content.replace("/opt/sfw_deploy", str(PROJECT_ROOT))

    service_dst.write_text(content)
    print(f"  已安装: {service_dst}")

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "django"])

    print("\n  Django 服务已安装 (systemctl start django 启动)")

def run_migrations(python_bin: str):
    """Step 8: 执行数据库迁移"""
    step(8, "执行数据库迁移")

    manage_py = PROJECT_ROOT / "manage.py"
    if not manage_py.exists():
        print("  manage.py 不存在，跳过迁移")
        return

    run([python_bin, "manage.py", "migrate"], cwd=str(PROJECT_ROOT))
    run([python_bin, "manage.py", "collectstatic", "--noinput"],
        cwd=str(PROJECT_ROOT), check=False)

def verify():
    """Step 9: 验证环境"""
    step(9, "验证环境")

    print("  [Python] ", end="")
    run([sys.executable, "--version"])

    # 检查授权工具
    for name in ["lic_gen", "licgen"]:
        path = LICENSE_DIR / name
        if path.exists():
            print(f"  [{name}] {path} ✓")
        else:
            print(f"  [{name}] 未部署")

    for name in ["hx_knowledge_license_gender.py"]:
        path = LICENSE_DIR / name
        if path.exists():
            print(f"  [{name}] {path} ✓")

    # 检查 libcrypto
    lib = Path("/usr/lib/libcrypto.so.1.0.0")
    print(f"  [libcrypto] {'✓ 就绪' if lib.exists() else '✗ 未找到'}")

    # 检查 Django 服务
    result = run(["systemctl", "is-enabled", "django"], check=False)
    if result.returncode == 0:
        print("  [django.service] ✓ 已启用")
    else:
        print("  [django.service] 未启用")

    # 检查数据库
    db = PROJECT_ROOT / "db.sqlite3"
    print(f"  [数据库] {'✓ db.sqlite3 已创建' if db.exists() else 'db.sqlite3 不存在（首次部署正常）'}")

# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  Ubuntu 多 Agent 一体化平台 - 环境部署")
    print("  " + time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    check_root()
    check_python()

    # 如果存在虚拟环境，优先使用
    venv_python = "/opt/venv/bin/python"
    if Path(venv_python).exists():
        python_bin = venv_python
    else:
        python_bin = sys.executable

    install_system_deps()
    python_bin = create_venv()
    install_python_deps(python_bin)
    create_directories()
    deploy_license_tools()
    setup_sudoers()
    setup_systemd()
    run_migrations(python_bin)
    verify()

    print("\n" + "=" * 60)
    print("  部署完成!")
    print("=" * 60)
    print(f"""
下一步操作:
  1. 配置管理网卡 IP（根据实际网络环境）
  2. 启动 Django: systemctl start django
  3. 打开 Web 界面: http://<管理网卡IP>:8000
  4. 在 Web 界面创建 Agent

常见操作:
  - 同步代码:     python sync_to_ubuntu.py
  - 重启服务:     python restart_ubuntu.py
  - 查看日志:     journalctl -u django -f
""")

if __name__ == "__main__":
    import time
    main()
