"""数据库版本迁移的运行时入口（同步 API，由 sync_schema 经 asyncio.to_thread 调用）。

四条路径，启动时自动判定：

    current        已纳管且版本 == head    → 直接返回，不 import alembic（稳态快速路径）
    fresh          库中无任何表            → upgrade head（基线迁移建全部表）
    legacy-stamped 有业务表、无版本表      → 补历史列 → stamp 0001_baseline
                                             → upgrade head（只跑基线之后的增量迁移）
    upgraded       已纳管但版本落后        → upgrade head

存量库绝不执行基线的 upgrade()：它已有全部表和数据，重跑只会报错。
stamp 到「基线版本」而不是 head，是为了保证以后新增的 0002、0003 仍会
被老库正常应用，不会因为 stamp head 被跳过。

性能：稳态下只需一次轻量 SQLAlchemy 连接 + inspect + 读取版本表，
用标准库 ast 解析脚本目录求 head，不需要加载整个 alembic（约 0.7s）。
只有真正需要建表/升级时才 import alembic。
"""

from __future__ import annotations

import ast
from pathlib import Path

from config import settings
from core.logger import log

BASELINE_REVISION = "0001_baseline"
VERSION_TABLE = "alembic_version"

# 判定「这是一个本项目的存量库」用的业务表集合
_BUSINESS_TABLES = {
    "users", "roles", "permissions", "menus", "sessions", "notes", "devices",
    "captchas", "login_locks", "password_history", "user_roles", "role_permissions",
}

# backend/db/migration.py → 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ASYNC_DRIVERS = {
    "sqlite+aiosqlite": "sqlite",
    "mysql+asyncmy": "mysql+pymysql",
    "postgresql+asyncpg": "postgresql+psycopg2",
}

# 基线之前靠手写 ALTER 补过的列。仅用于 legacy 库纳管前的一次性补齐，
# 新库由 0001_baseline 直接建全，不再走这里。
_LEGACY_USER_COLUMNS = {
    "password_changed_at": "DATETIME",
    "token_version": "INTEGER NOT NULL DEFAULT 0",
}


def sync_database_url() -> str:
    """返回 Alembic（同步执行）可用的 URL，CLI 与运行时共用这一份规则。"""
    raw = (settings.database_url or "").strip()
    if not raw:
        raise RuntimeError("未配置 DATABASE_URL，无法执行数据库迁移")
    for async_driver, sync_driver in _ASYNC_DRIVERS.items():
        if raw.startswith(async_driver + "://"):
            return sync_driver + raw[len(async_driver):]
    return raw


def head_revision_from_scripts() -> str | None:
    """用标准库 ast 读版本脚本求单一 head，避免为「查一下版本」而加载 alembic。

    多个 head（分支未合并）或解析不出结果时返回 None，调用方会回退到完整
    alembic 路径，由它给出准确的多 head 报错。
    """
    versions_dir = PROJECT_ROOT / "migrations" / "versions"
    revisions: dict[str, str | None] = {}

    def _literal(node):
        try:
            value = ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError):
            return None
        return value if isinstance(value, (str, type(None))) else None

    for path in sorted(versions_dir.glob("*.py")):
        rev = down = None
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "revision":
                        rev = _literal(node.value)
                    elif isinstance(target, ast.Name) and target.id == "down_revision":
                        down = _literal(node.value)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "revision":
                    rev = _literal(node.value)
                elif node.target.id == "down_revision":
                    down = _literal(node.value)
        if rev:
            revisions[rev] = down

    if not revisions:
        return None
    parents = {d for d in revisions.values() if d}
    heads = [r for r in revisions if r not in parents]
    return heads[0] if len(heads) == 1 else None


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    # start.sh 会 cd 到 backend/ 再启动，ini 里的相对路径会指错位置，
    # 这里统一用绝对路径覆盖，保证 CLI 与进程内调用行为一致。
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    # sqlalchemy.url 由 migrations/env.py 注入（ConfigParser 的 % 转义在那里处理）
    return cfg


def _read_version(conn, tables: set[str]) -> str | None:
    from sqlalchemy import text

    if VERSION_TABLE not in tables:
        return None
    return conn.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).scalar()


def _patch_legacy_schema(engine) -> list[str]:
    """老库纳管前补齐历史列并回填改密时间，返回实际补的列名。"""
    from sqlalchemy import inspect, text

    added: list[str] = []
    with engine.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("users")}
        for name, ddl in _LEGACY_USER_COLUMNS.items():
            if name not in cols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
                added.append(name)
        # NULL 会被密码策略视为「永不过期」，用建号时间回填
        conn.execute(text(
            "UPDATE users SET password_changed_at = "
            "COALESCE(created_at, CURRENT_TIMESTAMP) WHERE password_changed_at IS NULL"
        ))
    return added


def _known_revision(cfg, revision: str | None) -> bool:
    from alembic.script import ScriptDirectory

    return revision in {s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()}


def run_migrations() -> str:
    """执行迁移，返回路径标签（current / fresh / legacy-stamped / upgraded）。

    同步函数，请勿直接在事件循环里调用。
    """
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.pool import NullPool

    engine = create_engine(sync_database_url(), poolclass=NullPool, future=True)
    try:
        # 检测阶段单独占用连接并立即释放，避免与 alembic 自己的连接/事务互相等待
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            version = _read_version(conn, tables)

        head = head_revision_from_scripts()
        if VERSION_TABLE in tables and head is not None and version == head:
            log.info("数据库已是最新版本 {}，跳过迁移初始化", head)
            return "current"

        from alembic import command

        cfg = _alembic_config()

        if not tables:
            path = "fresh"
            command.upgrade(cfg, "head")

        elif VERSION_TABLE in tables:
            if not _known_revision(cfg, version):
                raise RuntimeError(
                    f"数据库记录的迁移版本 {version!r} 不在 migrations/versions 中，"
                    "可能是代码回退或版本串被改过；请人工核对后再启动"
                )
            path = "upgraded"
            command.upgrade(cfg, "head")

        elif "users" in tables and (tables & _BUSINESS_TABLES):
            # 存量库：先补齐基线之前的历史列，再把版本锚定到 baseline
            added = _patch_legacy_schema(engine)
            if added:
                log.info("存量库补齐历史列: {}", ", ".join(added))
            missing = _BUSINESS_TABLES - tables
            if missing:
                # 只初始化了一半的库不能直接 stamp：否则版本显示最新、表却永远缺。
                # create_all(checkfirst=True) 只建不存在的表，不触碰已有表和数据。
                from db.sql import Base

                log.warning("存量库缺少 {} 张表，按模型补齐: {}",
                            len(missing), sorted(missing))
                Base.metadata.create_all(engine, checkfirst=True)
                # create_all 对「已存在的表」会连索引一起跳过，单独补一次索引，
                # 避免半初始化库纳管后少了唯一约束/查询索引。
                for table in Base.metadata.tables.values():
                    for index in table.indexes:
                        index.create(bind=engine, checkfirst=True)
            command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, "head")
            path = "legacy-stamped"

        else:
            raise RuntimeError(
                "数据库中存在既非本项目的表、又没有 alembic_version，"
                f"拒绝自动纳管以免破坏数据。现有表: {sorted(tables)}"
            )

        with engine.connect() as conn:
            final_version = _read_version(conn, set(inspect(conn).get_table_names()))
        log.info("数据库迁移完成 | 路径={} | 版本={}", path, final_version)
        return path
    finally:
        engine.dispose()
