#!/usr/bin/env python
"""数据库备份命令行工具（不依赖服务是否启动）。

    python scripts/backup_db.py --once          # 日常备份（当天已备份则跳过）
    python scripts/backup_db.py --force         # 强制再生成一份
    python scripts/backup_db.py --list          # 列出全部备份
    python scripts/backup_db.py --verify        # 校验最新一份
    python scripts/backup_db.py --verify app.db-20261106-031000.db
    python scripts/backup_db.py --status        # 最近备份状态 / 是否欠账

备份流水同时写入 data/backups/backup.log，供 Windows 计划任务静默执行时排查。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from config import settings  # noqa: E402
from db import backup  # noqa: E402


def _size(n: int | None) -> str:
    if not n:
        return "-"
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StickyNotes SQLite 每日备份")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="生成一份备份（当天已备份则跳过，默认行为）")
    group.add_argument("--force", action="store_true", help="忽略当天幂等，强制生成一份")
    group.add_argument("--list", action="store_true", help="列出全部备份")
    group.add_argument("--verify", nargs="?", const="", metavar="FILE", help="校验备份，默认最新一份")
    group.add_argument("--status", action="store_true", help="显示备份状态")
    args = parser.parse_args(argv)

    if not settings.is_sqlite():
        print(f"当前 DB_BACKEND={settings.db_backend}，本工具只支持 SQLite；"
              "Mongo 请使用 mongodump 等专用工具。")
        return 1

    try:
        if args.list:
            items = backup.list_backups()
            if not items:
                print("暂无备份文件")
            else:
                print(f"共 {len(items)} 份备份（目录 {backup.backup_dir()}）：")
                for it in items:
                    print(f"  {it['file']:<36} {_size(it['size']):>10}  {it['mtime']}")
            return 0

        if args.verify is not None:
            r = backup.verify_backup(args.verify or None)
            print(f"文件: {r['file']}  ({_size(r['size'])})")
            print(f"完整性: {r['integrity']}  表数: {len(r['tables'])}  总行数: {r['rows']}")
            print("校验通过" if r["ok"] else "校验失败")
            return 0 if r["ok"] else 1

        if args.status:
            s = backup.status_summary()
            print(f"备份目录: {backup.backup_dir()}")
            print(f"数据库:   {backup.resolve_db_path()}")
            print(f"启用:     {s['enabled']}   已备份份数: {s['count']}")
            print(f"最近成功: {s['last_success_at'] or '从未'}  "
                  f"文件: {s['last_file'] or '-'}  ({_size(s['last_size'])})")
            print(f"累计成功: {s['total_success']}  累计失败: {s['total_failed']}")
            if s["last_error"]:
                print(f"最近错误: {s['last_error']}")
            print(f"启动补做: {'需要' if backup.needs_catchup() else '暂不需要'}"
                  f"（阈值 {settings.backup_catchup_hours} 小时）")
            return 0

        r = backup.backup_database(force=args.force, reason="cli")
        if r.get("skipped"):
            why = {"disabled": "备份已在配置中关闭",
                   "already-backed-up-today": "今天已成功备份，跳过",
                   "mongodb-unsupported": "非 SQLite 后端"}.get(r.get("reason"), r.get("reason"))
            print(f"{why}（最近: {r.get('last_file') or '-'} {r.get('last_success_at') or ''}）")
            return 0
        print(f"备份成功: {r['file']}  {_size(r['size'])}  "
              f"表数 {len(r['tables'])}  耗时 {r['duration_ms']}ms")
        if r.get("pruned"):
            print(f"清理过期备份: {len(r['pruned'])} 份")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"备份失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
