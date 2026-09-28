#!/usr/bin/env bash
# ============================================================
# 启动脚本
# 用法: ./start.sh start
#       ./start.sh stop
#       ./start.sh --dev      # 本地开发热重载
# ============================================================
set -eo pipefail  # 删掉 u，避免无参数时 $1 unbound
input="${1:-}"   # 给$1赋默认空值，解决unbound variable

run() {
    cd "$(dirname "$0")/backend"
    # 修复：systemd 以 root 运行时 HOME=/root，导致 Python 找不到用户级安装的 uvicorn
    # 强制设置 HOME 为实际用户目录，确保 Python 能搜索到 ~/.local/lib/python3.x/site-packages
    export HOME="/home/home"
    BIN="/usr/local/bin/uvicorn"
    #if [ -x ../.venv/bin/uvicorn ]; then
    #    BIN=../.venv/bin/uvicorn
    #elif command -v uvicorn >/dev/null 2>&1; then
    #    BIN=uvicorn
    #else
    #    echo "未找到 uvicorn，请先创建虚拟环境并安装依赖：" >&2
    #    echo "  python3 -m venv ../.venv && ../.venv/bin/pip install -e '..[dev]'" >&2
    #    exit 1
    #fi

    if [ "$input" = "--dev" ]; then
        # 开发模式，带reload
        exec "$BIN" main:app --host 0.0.0.0 --port 18000 --reload
    else
        # 生产模式，**不要 --reload**
        exec "$BIN" main:app --host 0.0.0.0 --port 18000
    fi
}

stop() {
    # 查找端口18000的uvicorn进程并杀死
    pid=$(lsof -t -i:18000)
    if [ -n "$pid" ]; then
        kill $pid
        sleep 1
        # 强制兜底
        if lsof -t -i:18000 >/dev/null; then
            kill -9 $pid
        fi
    fi
}

if [[ "$input" == "start" ]]; then
    run
elif [[ "$input" == "stop" ]]; then
    stop
elif [[ "$input" == "--dev" ]]; then
    run
else
    echo "用法:"
    echo "  $0 start    启动服务"
    echo "  $0 stop     停止服务"
    echo "  $0 --dev    开发热重载"
    exit 1
fi

