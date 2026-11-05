"""Alembic 环境配置。

唯一真源是 backend/db/sql 里的 SQLAlchemy metadata —— 迁移脚本与运行时模型
共用同一份定义，避免「迁移建的表」和「代码要的表」慢慢漂移。

数据库 URL 统一取自 config.settings.database_url（pydantic-settings 已按
「环境变量 DATABASE_URL > .env > 代码默认值」解析），再由 db/migration.py 的
sync_database_url() 把异步驱动换成同步驱动 —— Alembic 自身是同步执行的，
CLI 与应用进程内调用因此走完全相同的解析逻辑。
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db.migration import sync_database_url  # noqa: E402 —— URL 规则与运行时共用
from db.sql import Base  # noqa: E402  —— import 全部模型，注册进 metadata

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    except Exception:  # noqa: BLE001 —— 日志配置失败不应阻断迁移
        pass

target_metadata = Base.metadata

# ConfigParser 会把 % 当插值符；Windows 路径或含 % 的口令需转义
config.set_main_option("sqlalchemy.url", sync_database_url().replace("%", "%%"))


def _configure(connection=None) -> None:
    kwargs = dict(
        target_metadata=target_metadata,
        # SQLite 不支持多数 ALTER：batch 模式用「重建表」实现后续迁移
        render_as_batch=True,
        compare_type=True,
        compare_server_default=True,
        user_module_prefix="sa.",
    )
    if connection is None:
        context.configure(url=sync_database_url(), literal_binds=True,
                          dialect_opts={"paramstyle": "named"}, **kwargs)
    else:
        context.configure(connection=connection, **kwargs)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库（`alembic upgrade head --sql`）。"""
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式。支持由 backend/db/migration.py 传入已有 connection。"""
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(sync_database_url(), poolclass=pool.NullPool, future=True)
    try:
        with engine.connect() as conn:
            _configure(conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
