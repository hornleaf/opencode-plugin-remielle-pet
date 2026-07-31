#!/bin/sh
# Remielle_OpenPet 源码模式手动启动 (macOS / Linux)
cd "$(dirname "$0")"
nohup python3 pet.py >/dev/null 2>&1 &
echo "Remielle OpenPet 已启动 (日志: pet.log)"
