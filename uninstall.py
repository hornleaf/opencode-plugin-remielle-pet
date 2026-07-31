#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remielle_OpenPet 卸载脚本 — 删除插件与桌宠部署目录。"""
import shutil
import sys
from pathlib import Path

HOME = Path.home()
OPENCODE_DIR = HOME / ".config" / "opencode"
PET_DIR = OPENCODE_DIR / "remielle-openpet"
PLUGIN_DST = OPENCODE_DIR / "plugins" / "remielle-openpet.js"

removed = []
if PLUGIN_DST.exists():
    PLUGIN_DST.unlink()
    removed.append(str(PLUGIN_DST))
if PET_DIR.exists():
    shutil.rmtree(PET_DIR)
    removed.append(str(PET_DIR))

if removed:
    print("Remielle_OpenPet 已卸载:")
    for p in removed:
        print(f"  - {p}")
else:
    print("未发现已安装的 Remielle_OpenPet")

print("提示: 若桌宠正在运行, 请手动关闭 (右键菜单 → 退出)")
sys.exit(0)
