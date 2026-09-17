#!/usr/bin/env bash
# 便签客户端启动脚本：首次自动建 venv 并安装依赖
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python app.py
