"""SQLite 每日备份回归：全部使用 tmp_path，绝不触碰 data/app.db 与正式备份目录。

覆盖：WAL 一致性快照、完整性校验与表行数比对、当天幂等/force、保留策略
（不误删人工备份）、成功/失败状态记录、启动补做判定、Mongo/关闭开关跳过。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from db import backup


class _Cfg:
    """隔离配置：让 backup 模块完全工作在 tmp_path 里，不读写正式目录。"""

    def __init__(self, root: Path, **overrides):
        self.db_backend = "sqlite"
        self.database_url = f"sqlite+aiosqlite:///{(root / 'app.db').as_posix()}"
        self.backup_enabled = True
        self.backup_dir = str(root / "backups")
        self.backup_state_file = str(root / "backups" / "backup_state.json")
        self.backup_retention_days = 30
        self.backup_min_keep = 7
        self.backup_catchup_hours = 26
        self.__dict__.update(overrides)

    def is_sqlite(self) -> bool:
        return self.db_backend == "sqlite"


@pytest.fixture()
def env(tmp_path):
    cfg = _Cfg(tmp_path)
    src = tmp_path / "app.db"
    con = sqlite3.connect(src)
    con.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    con.executemany("INSERT INTO notes(body) VALUES (?)", [(f"n{i}",) for i in range(5)])
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO users(name) VALUES ('alice')")
    con.commit()
    con.close()
    return tmp_path, cfg, src


def _rows(path: Path, table: str = "notes") -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def _state(cfg) -> dict:
    return json.loads(Path(cfg.backup_state_file).read_text("utf-8"))


def test_backup_is_consistent_snapshot(env):
    tmp_path, cfg, src = env
    r = backup.backup_database(cfg=cfg)
    assert r["ok"] and not r["skipped"]
    assert r["tables"] == {"notes": 5, "users": 1}
    assert r["size"] > 0

    out = Path(cfg.backup_dir) / r["file"]
    assert out.is_file() and _rows(out) == 5

    v = backup.verify_backup(out, cfg=cfg)
    assert v["ok"] and v["integrity"] == "ok" and v["rows"] == 6

    # 备份是快照：源库随后新增的行不会跑进已生成的备份里
    con = sqlite3.connect(src)
    con.executemany("INSERT INTO notes(body) VALUES (?)", [("later",)] * 3)
    con.commit()
    con.close()
    assert _rows(out) == 5 and _rows(src) == 8


def test_wal_backup_captures_uncheckpointed_commits(env):
    """WAL 模式下最新提交可能只在 -wal 里；backup() 必须包含它们。"""
    tmp_path, cfg, src = env
    con = sqlite3.connect(src)
    assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    con.execute("INSERT INTO notes(body) VALUES ('in-wal')")
    con.commit()
    # 故意不 checkpoint、不关闭连接：这正是「直接复制主库文件」会漏数据的场景
    r = backup.backup_database(cfg=cfg)
    con.close()
    out = Path(cfg.backup_dir) / r["file"]
    assert _rows(out) == 6
    assert backup.verify_backup(out, cfg=cfg)["ok"]


def test_same_day_idempotent_and_force(env):
    tmp_path, cfg, src = env
    first = backup.backup_database(cfg=cfg)
    again = backup.backup_database(cfg=cfg)
    assert again["skipped"] and again["reason"] == "already-backed-up-today"
    assert again["last_file"] == first["file"]
    assert len(backup.list_backups(cfg=cfg)) == 1

    forced = backup.backup_database(force=True, cfg=cfg)
    assert not forced["skipped"] and forced["file"] != first["file"]
    assert len(backup.list_backups(cfg=cfg)) == 2
    assert _state(cfg)["total_success"] == 2

    # 同一天但备份文件被手工删掉 → 不应误判为已备份
    (Path(cfg.backup_dir) / forced["file"]).unlink()
    third = backup.backup_database(cfg=cfg)
    assert not third["skipped"]


def test_prune_keeps_minimum_and_never_deletes_manual_backups(env):
    tmp_path, cfg, src = env
    cfg.backup_retention_days = 1
    cfg.backup_min_keep = 3
    d = Path(cfg.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    old = (datetime.now() - timedelta(days=90)).timestamp()
    for i in range(10):
        p = d / f"app.db-202601{i + 1:02d}-030000.db"
        p.write_bytes(b"old-backup")
        os.utime(p, (old, old))
    manual = d / "app.db.pre-alembic-20261105-190307"
    manual.write_bytes(b"manual-backup")
    os.utime(manual, (old, old))

    removed = backup.prune_backups(cfg=cfg)
    assert manual.exists(), "人工备份绝不能被自动清理误删"
    assert len(list(d.glob("app.db-*.db"))) >= 3, "至少保留 min_keep 份"
    assert len(removed) > 0


def test_failure_records_status_and_cleans_temp(env):
    tmp_path, cfg, src = env
    missing = tmp_path / "nope.db"
    with pytest.raises(FileNotFoundError):
        backup.backup_database(db_path=missing, cfg=cfg)
    st = _state(cfg)
    assert st["total_failed"] == 1 and "FileNotFoundError" in st["last_error"]
    assert not list(Path(cfg.backup_dir).glob(".*.tmp"))

    # 之后仍能成功备份，失败计数不影响成功路径
    r = backup.backup_database(cfg=cfg)
    assert r["ok"] and _state(cfg)["total_success"] == 1


def test_corrupted_backup_is_detected(env):
    tmp_path, cfg, src = env
    d = Path(cfg.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "app.db-20261101-030000.db"
    bad.write_bytes(b"this is not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        backup.verify_backup(bad, cfg=cfg)


def test_needs_catchup_and_status_summary(env):
    tmp_path, cfg, src = env
    sp = Path(cfg.backup_state_file)
    assert backup.needs_catchup(cfg=cfg) is True

    sp.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().isoformat(timespec="seconds")
    sp.write_text(json.dumps({"last_success_at": now, "last_file": "x.db",
                              "total_success": 4}), encoding="utf-8")
    assert backup.needs_catchup(cfg=cfg) is False
    s = backup.status_summary(cfg=cfg)
    assert s["enabled"] and s["total_success"] == 4 and s["count"] == 0

    stale = (datetime.now() - timedelta(hours=48)).isoformat(timespec="seconds")
    sp.write_text(json.dumps({"last_success_at": stale}), encoding="utf-8")
    assert backup.needs_catchup(cfg=cfg) is True
    cfg.backup_catchup_hours = 72
    assert backup.needs_catchup(cfg=cfg) is False


def test_disabled_and_mongodb_are_skipped(env):
    tmp_path, cfg, src = env
    cfg.backup_enabled = False
    assert backup.backup_database(cfg=cfg)["reason"] == "disabled"
    cfg.backup_enabled = True
    cfg.db_backend = "mongodb"
    assert backup.backup_database(cfg=cfg)["reason"] == "mongodb-unsupported"
    assert backup.status_summary(cfg=cfg)["enabled"] is False


def test_resolve_db_path_variants(env):
    tmp_path, cfg, src = env
    assert backup.resolve_db_path(f"sqlite:///{(tmp_path / 'a.db').as_posix()}") == tmp_path / "a.db"
    assert backup.resolve_db_path(f"sqlite+aiosqlite:///{(tmp_path / 'b.db').as_posix()}") == tmp_path / "b.db"
    # 四斜杠绝对形式：Windows 上会落在当前盘符，只断言尾部解析正确
    assert backup.resolve_db_path("sqlite:////var/tmp/c.db").as_posix().endswith("/var/tmp/c.db")
    with pytest.raises(ValueError):
        backup.resolve_db_path("sqlite:///:memory:")
    with pytest.raises(ValueError):
        backup.resolve_db_path("postgresql://u:p@host/db")


def test_process_lock_is_exclusive(env):
    tmp_path, cfg, src = env
    d = Path(cfg.backup_dir)
    d.mkdir(parents=True, exist_ok=True)
    entered, release = threading.Event(), threading.Event()

    def hold():
        with backup._process_lock(d):
            entered.set()
            release.wait(2)

    t = threading.Thread(target=hold)
    t.start()
    assert entered.wait(2)
    with pytest.raises(TimeoutError):
        with backup._process_lock(d, timeout=0.5):
            pass
    release.set()
    t.join(2)
    assert not (d / backup.LOCK_NAME).exists()
