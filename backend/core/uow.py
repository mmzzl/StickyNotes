"""工作单元（Unit of Work）：把 service 层的多步写放进同一个数据库事务。

基于 contextvars，天然按请求 / 任务隔离，不需要把 session 逐层往下传。
repository 的单方法调用行为保持不变：没有外层 UoW 时自己提交；一旦处在
UoW 内就复用同一个 session，由最外层统一 commit，异常则整体 rollback。

用法（service 层）：

    from core.uow import unit_of_work

    async with unit_of_work():
        user = await users.create(...)
        await users.set_roles(user["id"], role_ids)
        await password_history.add(...)

这是唯一允许 service 层 import 的 db 相关能力；其余 db/* 仍只属于 repository。
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from config import settings

_current: ContextVar[Any | None] = ContextVar("sticky_uow_session", default=None)


def current_session():
    """当前 UoW 的 SQLAlchemy session；不在事务中则返回 None。"""
    return _current.get()


@asynccontextmanager
async def unit_of_work():
    if not settings.is_sqlite():
        # Mongo 未启用 replica set，不支持跨文档事务；降级为 no-op 保证调用方不炸
        yield
        return

    if _current.get() is not None:
        # 嵌套调用：内层不再开事务，由最外层统一提交
        yield
        return

    from db.session import sessionmaker

    async with sessionmaker()() as s:
        token = _current.set(s)
        try:
            yield
            await s.commit()
        except BaseException:
            await s.rollback()
            raise
        finally:
            _current.reset(token)
