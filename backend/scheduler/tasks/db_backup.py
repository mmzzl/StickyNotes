"""每日数据库备份定时任务（由 conf/inputs.conf 注册）。

SQLite 的 backup() 是同步 I/O，放到线程里跑，避免阻塞 APScheduler 所在的事件循环。
异常交给 scheduler_._task_wrapper 统一记录并触发 task_failed 通知。
"""

import asyncio

from config import settings
from core.logger import log
from scheduler.loader import ScheduleItem


async def daily_backup(item: ScheduleItem) -> None:
    if not settings.backup_enabled or not settings.is_sqlite():
        log.info("定时任务[{}] 跳过：备份未启用或非 SQLite 后端", item.title)
        return

    from db.backup import backup_database

    result = await asyncio.to_thread(backup_database, reason="scheduled")
    if result.get("skipped"):
        log.info("定时任务[{}] 备份跳过（{}）：最近 {}",
                 item.title, result.get("reason"), result.get("last_file"))
        return
    log.info("定时任务[{}] 备份完成: {} size={} 表数={} 耗时={}ms 清理={}份",
             item.title, result["file"], result["size"], len(result["tables"]),
             result["duration_ms"], len(result["pruned"]))
