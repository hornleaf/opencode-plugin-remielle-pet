#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Remielle_OpenPet — 一键安装脚本
==========================
用法:
    python install.py          # 完整安装
    python install.py --uninstall   # 卸载(等价于 uninstall.py)

安装内容:
    1. 检查 Python 版本, 安装 Python 依赖 (PySide6)
    2. 部署桌宠程序到 opencode 标准配置目录:
       ~/.config/opencode/remielle-openpet/  (pet.py + gifs/, 保留已有 config.json)
    3. 安装 opencode 插件:
       ~/.config/opencode/plugins/remielle-openpet.js  (opencode 自动加载, 无需改配置)
    4. 生成启动脚本 start.bat (Windows) / start.sh (macOS/Linux)

插件与桌宠均使用标准路径定位, 源码中不硬编码任何用户路径。
"""
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
BASE = Path(__file__).resolve().parent

# opencode 标准配置目录(官方文档指定, 所有平台一致)
OPENCODE_DIR = HOME / ".config" / "opencode"
# 桌宠部署目录(插件用 os.homedir() 推算同一路径, 零硬编码)
PET_DIR = OPENCODE_DIR / "remielle-openpet"
# 插件安装位置(opencode 自动加载 ~/.config/opencode/plugins/*.js)
PLUGIN_DST = OPENCODE_DIR / "plugins" / "remielle-openpet.js"


def check_python():
    if sys.version_info < (3, 9):
        print(f"错误: 需要 Python 3.9+, 当前 {sys.version_info.major}.{sys.version_info.minor}")
        sys.exit(1)
    print(f"[1/4] Python {sys.version_info.major}.{sys.version_info.minor} OK")


def install_deps():
    try:
        import PySide6  # noqa: F401
        print("      PySide6 已安装, 跳过")
    except ImportError:
        print("[2/4] 安装 Python 依赖 (PySide6)...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "PySide6"],
            capture_output=True, text=True)
        if r.returncode != 0:
            print("      安装失败:\n", r.stdout[-1500:], r.stderr[-1500:])
            sys.exit(1)
        print("      PySide6 安装完成")


def deploy_pet():
    print(f"[3/4] 部署桌宠程序 -> {PET_DIR}")
    PET_DIR.mkdir(parents=True, exist_ok=True)

    src_py = BASE / "pet.py"
    if not src_py.exists():
        print(f"      找不到 {src_py}")
        sys.exit(1)
    # 升级部署时保留已有 config.json(用户配置) 与 pet.log
    for name in ("pet.py",):
        shutil.copy2(src_py, PET_DIR / name)
    src_gifs = BASE / "gifs"
    if src_gifs.is_dir():
        (PET_DIR / "gifs").mkdir(parents=True, exist_ok=True)
        for f in src_gifs.glob("*.gif"):
            shutil.copy2(f, PET_DIR / "gifs" / f.name)
    print(f"      pet.py + {len(list((PET_DIR / 'gifs').glob('*.gif')))} 个动图已部署")

    # 生成启动脚本(始终指向部署目录, 双击即可手动常驻启动)
    if sys.platform == "win32":
        bat = PET_DIR / "start.bat"
        bat.write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" pythonw pet.py\r\n', encoding="ascii")
        print(f"      启动脚本: {bat}")
    else:
        sh = PET_DIR / "start.sh"
        sh.write_text("#!/bin/sh\ncd \"$(dirname \"$0\")\"\nnohup python3 pet.py >/dev/null 2>&1 &\n", encoding="ascii")
        sh.chmod(0o755)
        print(f"      启动脚本: {sh}")


def install_plugin():
    print(f"[4/4] 安装 opencode 插件 -> {PLUGIN_DST}")
    src_js = BASE / "remielle-openpet.js"
    if not src_js.exists():
        print(f"      找不到 {src_js}")
        sys.exit(1)
    PLUGIN_DST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_js, PLUGIN_DST)
    print("      插件已安装 (opencode 启动时自动加载)")


def verify():
    print()
    print("验证安装:")
    ok = True
    if not (PET_DIR / "pet.py").exists():
        ok = False
        print("  [FAIL] 桌宠程序缺失")
    if not (PET_DIR / "gifs").is_dir():
        ok = False
        print("  [FAIL] 动图目录缺失")
    if not PLUGIN_DST.exists():
        ok = False
        print("  [FAIL] 插件缺失")
    if ok:
        print("  [OK] 全部文件就位")
    else:
        print("  安装不完整, 请检查输出")
        sys.exit(1)


def print_summary():
    print()
    print("=" * 50)
    print("Remielle_OpenPet 安装完成!")
    print("=" * 50)
    print(f"  桌宠程序: {PET_DIR}")
    print(f"  插件:     {PLUGIN_DST}")
    print()
    print("  下一步:")
    print("    1. 重启 opencode (CLI / Desktop), 桌宠将随 opencode 自动启动")
    if sys.platform == "win32":
        print("    2. 手动常驻启动: 双击 " + str(PET_DIR / "start.bat"))
    else:
        print("    2. 手动常驻启动: " + str(PET_DIR / "start.sh"))
    print("    3. 卸载: python uninstall.py")
    print()
    print("  日志: pet.log | 配置: config.json | 素材: gifs/ (均在部署目录)")


def uninstall():
    print("卸载 Remielle_OpenPet:")
    removed = []
    if PLUGIN_DST.exists():
        PLUGIN_DST.unlink()
        removed.append(str(PLUGIN_DST))
    if PET_DIR.exists():
        shutil.rmtree(PET_DIR)
        removed.append(str(PET_DIR))
    if removed:
        for p in removed:
            print(f"  已删除: {p}")
    else:
        print("  未发现已安装的文件")
    print("  提示: 若桌宠正在运行, 请手动关闭 (右键菜单 → 退出)")


def main():
    if "--uninstall" in sys.argv:
        uninstall()
        return
    check_python()
    install_deps()
    deploy_pet()
    install_plugin()
    verify()
    print_summary()


if __name__ == "__main__":
    main()
