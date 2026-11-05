"""数据库迁移路径回归：全部使用 tmp_path 里的临时文件库，不触碰 data/app.db。

覆盖 fresh / current / legacy-stamped / upgraded 四条路径，以及
「未知版本」「库不属于本项目」两个拒绝分支。
"""

import sqlite3

import pytest
from sqlalchemy import create_engine

from config import settings
from db import migration
from db.sql import Base


@pytest.fixture()
def db_file(tmp_path):
    original = settings.database_url
    path = tmp_path / "migration.db"
    settings.database_url = f"sqlite+aiosqlite:///{path.as_posix()}"
    yield path
    settings.database_url = original


def _tables(path):
    c = sqlite3.connect(path)
    try:
        return {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()


def _version(path):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        c.close()


def _users_columns(path):
    c = sqlite3.connect(path)
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(users)")}
    finally:
        c.close()


def _build_legacy_db(path, drop_tables=()):
    """用当前模型建全表，再退化成「改造前老库」：删两列 + 可选删几张表。"""
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("SQLite < 3.35 不支持 ALTER TABLE DROP COLUMN")

    engine = create_engine(migration.sync_database_url())
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    c = sqlite3.connect(path)
    try:
        c.execute(
            "INSERT INTO users (id, username, password_hash, display_name, email, "
            "is_active, is_superuser, created_at, updated_at) "
            "VALUES ('u1','alice','hash','Alice','',1,0,"
            "'2026-01-01 00:00:00','2026-01-01 00:00:00')"
        )
        c.execute("ALTER TABLE users DROP COLUMN token_version")
        c.execute("ALTER TABLE users DROP COLUMN password_changed_at")
        for table in drop_tables:
            c.execute(f"DROP TABLE {table}")
        c.commit()
    finally:
        c.close()


def test_head_revision_is_known():
    assert migration.head_revision_from_scripts() == migration.BASELINE_REVISION


def test_fresh_database_creates_full_schema_then_short_circuits(db_file):
    assert migration.run_migrations() == "fresh"
    assert _version(db_file) == migration.BASELINE_REVISION
    assert migration._BUSINESS_TABLES <= _tables(db_file)
    assert "token_version" in _users_columns(db_file)
    # 稳态：不加载 alembic，直接返回 current
    assert migration.run_migrations() == "current"


def test_legacy_database_stamped_without_data_loss(db_file):
    _build_legacy_db(db_file)
    assert "alembic_version" not in _tables(db_file)
    assert "token_version" not in _users_columns(db_file)

    assert migration.run_migrations() == "legacy-stamped"

    assert _version(db_file) == migration.BASELINE_REVISION
    assert {"token_version", "password_changed_at"} <= _users_columns(db_file)
    assert migration._BUSINESS_TABLES <= _tables(db_file)
    c = sqlite3.connect(db_file)
    try:
        assert c.execute("SELECT COUNT(*) FROM users WHERE username='alice'").fetchone()[0] == 1
        # 老库回填改密时间，不能留 NULL（否则密码永不过期）
        assert c.execute(
            "SELECT COUNT(*) FROM users WHERE password_changed_at IS NULL").fetchone()[0] == 0
    finally:
        c.close()
    assert migration.run_migrations() == "current"


def test_legacy_database_with_missing_tables_repairs_them(db_file):
    """半初始化的老库：stamp 前必须补齐缺表，否则会永远缺表还显示「已是最新」。"""
    _build_legacy_db(db_file, drop_tables=("notes", "captchas"))
    assert "notes" not in _tables(db_file)

    assert migration.run_migrations() == "legacy-stamped"
    assert {"notes", "captchas"} <= _tables(db_file)
    assert _version(db_file) == migration.BASELINE_REVISION


def test_unknown_revision_is_rejected(db_file):
    """版本记录指向已删除/未发布的脚本时，必须明确拒绝启动而不是静默跳过。"""
    engine = create_engine(migration.sync_database_url())
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            from sqlalchemy import text

            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('ghost_revision')"))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="不在 migrations/versions"):
        migration.run_migrations()


def test_unrelated_database_is_rejected(db_file):
    """库里有表但不是本项目的，绝不自动纳管，避免污染外部数据。"""
    c = sqlite3.connect(db_file)
    try:
        c.execute("CREATE TABLE another_app (id INTEGER PRIMARY KEY)")
        c.commit()
    finally:
        c.close()

    with pytest.raises(RuntimeError, match="拒绝自动纳管"):
        migration.run_migrations()
