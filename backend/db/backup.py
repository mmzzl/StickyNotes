"""SQLite 每日在线备份：一致性快照、完整性校验、保留策略与状态记录。

为什么不用「复制 data/app.db」：SQLite 在 WAL 模式下，最新提交可能还留在
-wal 文件里，直接 copy 主库文件会得到缺少近期数据、甚至结构损坏的副本。
这里改用 ``sqlite3.Connection.backup()``，由 SQLite 自己在持读锁的前提下
生成一致快照，源库无需停服、无需 checkpoint。

对外主要函数：
    backup_database()   生成一份备份（当天已成功则幂等跳过，force 可强制）
    needs_catchup()     距上次成功是否超过阈值，供服务启动补做
    list_backups()      列出本模块生成的备份
    verify_backup()     校验某个备份的 integrity_check 与表行数
    status_summary()    给 /health 用的精简状态
    read_status()       读取完整状态文件

只依赖标准库 + config.settings，可被 scripts/backup_db.py 在服务未启动时
独立调用；Mongo 后端明确跳过（该后端需各自的 dump 工具）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from config import settings

# 只清理本模块产出的文件，绝不误删 data/backups/ 里的人工备份
# （例如 app.db.pre-alembic-20261105-190307）
BACKUP_RE = re.compile(r"^app\.db-\d{8}-\d{6}(?:-\d+)?\.db$")
STATE_NAME = "backup_state.json"
LOCK_NAME = ".backup.lock"
LOG_NAME = "backup.log"

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _cfg(name: str, override: Any, cfg: Any = None) -> Any:
    if override is not None:
        return override
    return getattr(cfg or settings, name)


def _absolute(path: str | Path) -> Path:
    """相对路径统一按项目根解析，避免受启动时工作目录影响。"""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def backup_dir(cfg: Any = None) -> Path:
    return _absolute(_cfg("backup_dir", None, cfg))


def state_file(cfg: Any = None) -> Path:
    return _absolute(_cfg("backup_state_file", None, cfg))


def resolve_db_path(url: str | None = None, cfg: Any = None) -> Path:
    """从 SQLAlchemy 风格的 database_url 解析出 SQLite 文件路径。

    支持 sqlite:///C:/x.db、sqlite+aiosqlite:///C:/x.db、sqlite:////tmp/x.db；
    :memory: 无文件可备份，直接报错。
    """
    raw = str(url if url is not None else _cfg("database_url", None, cfg) or "")
    m = re.match(r"^sqlite(?:\+[\w]+)?:///(.+)$", raw)
    if not m:
        raise ValueError(f"非 SQLite 文件数据库，无法备份: {raw!r}")
    target = m.group(1)
    if target in (":memory:", "", "/:memory:"):
        raise ValueError("内存数据库没有可备份的文件")
    return _absolute(target)


# ---------------------------------------------------------------------------
# 状态文件 / 日志
# ---------------------------------------------------------------------------

def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_status(state_path: str | Path | None = None, cfg: Any = None) -> dict:
    p = Path(state_path) if state_path else state_file(cfg)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_status(state_path: Path, **patch: Any) -> None:
    data = read_status(state_path)
    data.update(patch)
    _atomic_json(state_path, data)


def _log_line(message: str, directory: Path) -> None:
    """独立记录一份备份流水：Windows 计划任务用 pythonw 静默执行时也有据可查。"""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / LOG_NAME).open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 跨进程互斥（服务内调度 + Windows 计划任务可能同时触发）
# ---------------------------------------------------------------------------

@contextmanager
def _process_lock(directory: Path, timeout: float = 30.0, stale_seconds: float = 300.0):
    """用 O_EXCL 锁文件互斥；进程崩溃留下的陈旧锁超过 5 分钟自动清理。"""
    lock = directory / LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    acquired = False
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            acquired = True
            try:
                os.write(fd, f"pid={os.getpid()} ts={time.time()}\n".encode("ascii"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0
            if age > stale_seconds:
                try:
                    lock.unlink()
                except OSError as e:
                    # 锁文件属于别的用户（如 cron 用 root、服务用 admin）时删不掉，
                    # 继续空转只会白等到超时，这里直接给出可诊断的错误。
                    raise TimeoutError(f"陈旧锁无法清理（权限不足？）: {lock} ({e})") from e
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"备份锁被占用超过 {timeout:.0f}s: {lock}")
            time.sleep(0.2)
    try:
        yield
    finally:
        if acquired:
            try:
                lock.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 备份执行
# ---------------------------------------------------------------------------

def _table_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """逐表行数，用于比对快照与源库是否一致（本地库很小，开销可忽略）。"""
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    stats = {}
    for name in names:
        quoted = '"' + name.replace('"', '""') + '"'
        stats[name] = conn.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
    return stats


def _copy_verified(src: Path, tmp: Path) -> dict[str, int]:
    """在线一致性备份 + 目标库完整性校验 + 与源库逐表行数比对。"""
    src_conn = sqlite3.connect(str(src), timeout=30)
    src_conn.execute("PRAGMA query_only = ON")  # 备份过程绝不写源库
    dst_conn = sqlite3.connect(str(tmp))
    try:
        # pages/sleep 分块复制：大库也能让出写锁，不会长时间阻塞业务请求
        src_conn.backup(dst_conn, pages=64, sleep=0.05)
        dst_stats = _table_stats(dst_conn)
        src_stats = _table_stats(src_conn)
        if dst_stats != src_stats:
            raise RuntimeError(f"快照与源库表数据不一致: {src_stats} != {dst_stats}")
        integrity = dst_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"备份文件完整性校验失败: {integrity}")
    finally:
        dst_conn.close()
        src_conn.close()
    return dst_stats


def _unique_path(directory: Path, stamp: str) -> Path:
    candidate = directory / f"app.db-{stamp}.db"
    n = 2
    while candidate.exists():
        candidate = directory / f"app.db-{stamp}-{n}.db"
        n += 1
    return candidate


def _today_done(state: dict, directory: Path) -> bool:
    """当天已有成功备份且文件仍在 → 幂等跳过（force 可绕过）。"""
    last_at, name = state.get("last_success_at"), state.get("last_file")
    if not last_at or not name:
        return False
    try:
        if datetime.fromisoformat(last_at).date() != datetime.now().date():
            return False
    except ValueError:
        return False
    return (directory / str(name)).exists()


def prune_backups(directory: str | Path | None = None,
                  retention_days: int | None = None,
                  min_keep: int | None = None,
                  cfg: Any = None) -> list[str]:
    """按天数清理，但永远保留最近 min_keep 份；只删本模块命名格式的文件。"""
    d = Path(directory) if directory else backup_dir(cfg)
    if not d.is_dir():
        return []
    retention = int(_cfg("backup_retention_days", retention_days, cfg))
    keep = int(_cfg("backup_min_keep", min_keep, cfg))
    files = sorted((p for p in d.iterdir() if p.is_file() and BACKUP_RE.match(p.name)),
                   key=lambda p: p.name, reverse=True)
    protected = set(files[:keep])
    cutoff = time.time() - retention * 86400
    removed = []
    for p in files:
        if p in protected or p.stat().st_mtime >= cutoff:
            continue
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return removed


def backup_database(*, force: bool = False, reason: str = "manual",
                    db_path: str | Path | None = None,
                    directory: str | Path | None = None,
                    retention_days: int | None = None,
                    min_keep: int | None = None,
                    state_path: str | Path | None = None,
                    cfg: Any = None) -> dict:
    """生成一份一致性备份。成功返回详情 dict，失败抛异常并写入失败状态。

    当天已成功备份过则直接跳过（force=True 强制新生成一份）。
    """
    cfg = cfg or settings
    if not _cfg("backup_enabled", None, cfg):
        return {"ok": True, "skipped": True, "reason": "disabled"}
    if not cfg.is_sqlite():
        return {"ok": True, "skipped": True, "reason": "mongodb-unsupported"}

    src = Path(db_path) if db_path else resolve_db_path(cfg=cfg)
    out = Path(directory) if directory else backup_dir(cfg)
    out.mkdir(parents=True, exist_ok=True)
    st_path = Path(state_path) if state_path else state_file(cfg)

    t0 = time.monotonic()
    with _process_lock(out):
        state = read_status(st_path)
        if not force and _today_done(state, out):
            # 计划任务静默执行时也需要有痕迹，便于确认「确实跑过」
            _log_line(f"SKIP already-backed-up-today file={state.get('last_file')} "
                      f"reason={reason}", out)
            return {
                "ok": True, "skipped": True, "reason": "already-backed-up-today",
                "last_file": state.get("last_file"),
                "last_success_at": state.get("last_success_at"),
            }

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = _unique_path(out, stamp)
        tmp = out / f".{final.name}.tmp"
        if tmp.exists():
            tmp.unlink()
        try:
            if not src.is_file():
                raise FileNotFoundError(f"数据库文件不存在: {src}")
            tables = _copy_verified(src, tmp)
            size = tmp.stat().st_size
            os.replace(tmp, final)  # 校验通过才原子落盘，不留下半截文件
            duration = int((time.monotonic() - t0) * 1000)
            removed = prune_backups(out, retention_days, min_keep, cfg)
            now = datetime.now().isoformat(timespec="seconds")
            _write_status(
                st_path,
                last_success_at=now, last_file=final.name, last_size=size,
                last_duration_ms=duration, last_tables=tables,
                last_reason=reason, last_error=None,
                total_success=int(state.get("total_success") or 0) + 1,
            )
            _log_line(f"OK   {final.name} size={size} tables={len(tables)} "
                      f"duration={duration}ms reason={reason} pruned={len(removed)}", out)
            return {
                "ok": True, "skipped": False, "file": final.name,
                "path": str(final), "size": size, "duration_ms": duration,
                "tables": tables, "pruned": removed, "reason": reason,
                "created_at": now,
            }
        except Exception as exc:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            _write_status(
                st_path,
                last_error=f"{type(exc).__name__}: {exc}",
                last_error_at=datetime.now().isoformat(timespec="seconds"),
                total_failed=int(state.get("total_failed") or 0) + 1,
            )
            _log_line(f"FAIL {type(exc).__name__}: {exc} reason={reason}", out)
            raise


# ---------------------------------------------------------------------------
# 查询 / 校验 / 启动补做
# ---------------------------------------------------------------------------

def list_backups(directory: str | Path | None = None, cfg: Any = None) -> list[dict]:
    d = Path(directory) if directory else backup_dir(cfg)
    if not d.is_dir():
        return []
    files = sorted((p for p in d.iterdir() if p.is_file() and BACKUP_RE.match(p.name)),
                   key=lambda p: p.name, reverse=True)
    return [{
        "file": p.name,
        "size": p.stat().st_size,
        "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
    } for p in files]


def verify_backup(path: str | Path | None = None,
                  directory: str | Path | None = None,
                  cfg: Any = None) -> dict:
    """校验备份能否打开、integrity_check 是否 ok、各表行数。默认校验最新一份。"""
    d = Path(directory) if directory else backup_dir(cfg)
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = d / p
    else:
        items = list_backups(d, cfg)
        if not items:
            raise FileNotFoundError(f"备份目录中没有备份文件: {d}")
        p = d / items[0]["file"]
    if not p.is_file():
        raise FileNotFoundError(f"备份文件不存在: {p}")
    conn = sqlite3.connect(str(p))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = _table_stats(conn)
    finally:
        conn.close()
    return {
        "ok": integrity == "ok", "file": p.name, "size": p.stat().st_size,
        "integrity": integrity, "tables": tables,
        "rows": sum(tables.values()),
    }


def needs_catchup(state_path: str | Path | None = None, cfg: Any = None) -> bool:
    """距上次成功备份超过 backup_catchup_hours 小时（或从未备份）→ 启动补做。"""
    hours = int(_cfg("backup_catchup_hours", None, cfg))
    st = read_status(state_path, cfg)
    last = st.get("last_success_at")
    if not last:
        return True
    try:
        return datetime.now() - datetime.fromisoformat(last) > timedelta(hours=hours)
    except ValueError:
        return True


def status_summary(cfg: Any = None) -> dict:
    cfg = cfg or settings
    st = read_status(cfg=cfg)
    return {
        "enabled": bool(_cfg("backup_enabled", None, cfg)) and cfg.is_sqlite(),
        "last_success_at": st.get("last_success_at"),
        "last_file": st.get("last_file"),
        "last_size": st.get("last_size"),
        "count": len(list_backups(cfg=cfg)),
        "last_error": st.get("last_error"),
        "total_success": int(st.get("total_success") or 0),
        "total_failed": int(st.get("total_failed") or 0),
    }
