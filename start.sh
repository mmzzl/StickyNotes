#!/usr/bin/env bash
# ============================================================
# 启动脚本
# 用法: ./start.sh start
#       ./start.sh stop
#       ./start.sh --dev      # 本地开发热重载
# ============================================================
set -eo pipefail  # 删掉 u，避免无参数时 $1 unbound
input="${1:-}"   # 给$1赋默认空值，解决unbound variable
ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-18000}"

run() {
    cd "$ROOT/backend"
    # 优先用项目内虚拟环境，其次 PATH 里的 uvicorn；
    # 不再硬编码 HOME=/home/home 与 /usr/local/bin/uvicorn（换机器就失效）。
    if [ -x "$ROOT/.venv/bin/uvicorn" ]; then
        BIN="$ROOT/.venv/bin/uvicorn"
    elif [ -x "$ROOT/.venv/Scripts/uvicorn.exe" ]; then
        BIN="$ROOT/.venv/Scripts/uvicorn.exe"
    elif command -v uvicorn >/dev/null 2>&1; then
        BIN="$(command -v uvicorn)"
    else
        echo "未找到 uvicorn，请先：python3 -m venv .venv && .venv/bin/pip install -e ." >&2
        exit 1
    fi

    if [ "$input" = "--dev" ]; then
        # 开发模式，带 reload
        exec "$BIN" main:app --host 0.0.0.0 --port "$PORT" --reload
    else
        # 生产模式，**不要 --reload**
        exec "$BIN" main:app --host 0.0.0.0 --port "$PORT"
    fi
}

stop() {
    # 兼容没有 lsof 的环境
    if command -v lsof >/dev/null 2>&1; then
        pid=$(lsof -t -i:"$PORT" 2>/dev/null || true)
    elif command -v fuser >/dev/null 2>&1; then
        pid=$(fuser -n tcp "$PORT" 2>/dev/null || true)
    else
        echo "缺少 lsof/fuser，无法自动停止，请手动结束进程" >&2
        return 1
    fi
    if [ -n "$pid" ]; then
        kill $pid 2>/dev/null || true
        sleep 1
        if command -v lsof >/dev/null 2>&1 && lsof -t -i:"$PORT" >/dev/null 2>&1; then
            kill -9 $pid 2>/dev/null || true
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

