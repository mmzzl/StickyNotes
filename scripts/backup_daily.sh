#!/usr/bin/env bash
# Linux / macOS 定时备份入口：供 systemd timer 或 cron 调用。
# CLI 自身带跨进程锁与当天幂等，重复触发是安全的；这里只负责找解释器。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/data/backups"

# 解释器优先级：PYTHON 显式指定 > 项目虚拟环境 > 系统 python3
PY="${PYTHON:-}"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  PY="$ROOT/.venv/bin/python"
fi
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || command -v python || true)"
fi
if [ -z "${PY:-}" ]; then
  echo "未找到 python3，无法执行备份" >&2
  exit 1
fi

exec "$PY" "$ROOT/scripts/backup_db.py" --once
