"""数据库统一入口：按 DB_BACKEND 路由到 sqlite(SQLAlchemy) 或 mongodb(Motor)。

对外只暴露四个函数，业务层不感知底层。
表结构由 migrations/ 下的 alembic 版本迁移管理；mongodb 无 schema，只建索引。
"""

import asyncio

from config import settings
from core.logger import log


async def sync_schema() -> None:
    """应用启动时同步表结构（幂等）。

    - sqlite：交给 alembic。全新库执行基线建表；改造前就存在的老库先补齐
      历史列再 stamp 基线，绝不重建表或丢数据（详见 db/migration.py）。
      迁移是同步 I/O，放到线程里跑，避免阻塞 lifespan 的事件循环。
    - mongodb：无表结构，只确保索引存在。
    """
    if settings.is_sqlite():
        from db.migration import run_migrations

        path = await asyncio.to_thread(run_migrations)
        log.info("SQLite 表结构就绪（迁移路径 {}）", path)
    else:
        from db.mongo.indexes import ensure_indexes

        await ensure_indexes()


async def init_database() -> None:
    """初始化数据库并写入 seed 数据（幂等）。"""
    await sync_schema()
    from db.seed import seed_all

    await seed_all()


# 兼容旧调用：语义已由「无脑 create_all」变为「按版本同步」
create_all = sync_schema


async def dispose() -> None:
    from db.base import dispose_engine, close_mongo_client

    await dispose_engine()
    await close_mongo_client()
    log.info("数据库连接已关闭")
