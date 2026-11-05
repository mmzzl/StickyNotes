"""FastAPI 应用入口：启动时初始化 DB + seed + 定时任务，并托管静态前端。

启动各阶段耗时由 core.startup_metrics 打点，可从 GET /health 读取，
用于运维就绪探测与量化启动优化效果。
"""

from contextlib import asynccontextmanager

# 必须尽量靠前导入：覆盖后续全部第三方库 import + create_app() 的耗时
from core.startup_metrics import begin, end, report, set_ready

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.v1.router import api_router
from config import BACKEND_DIR, settings
from core.exceptions import register_exception_handlers
from core.logger import log, setup_logging
from core.response import ok

# 静态前端目录（frontend/static），从 backend/main.py 向上两级
FRONTEND_DIR = BACKEND_DIR.parent / "frontend" / "static"



@asynccontextmanager
async def lifespan(app: FastAPI):
    end("import_app")  # 模块 import + create_app 完成

    begin("logging")
    setup_logging()
    end("logging")

    log.info("启动 {}（auth_mode={}, db={}）",
             settings.app_name, settings.auth_mode, settings.db_backend)
    weak = settings.weak_secrets()
    if weak:
        msg = "检测到不安全配置：{}（生成方式见 .env.example）".format(", ".join(weak))
        if settings.is_production() or settings.require_secure_secrets:
            # 生产环境不带着默认签名密钥启动：任何人都能伪造管理员令牌
            raise RuntimeError(msg + "；已拒绝启动，请先在 .env 中覆盖这些值")
        log.warning("{}；当前为开发模式仅告警", msg)

    # 实测结论：与调度器并行会和首次 SQLite 连接争资源，反而拉长 database 阶段。
    # 调度器注册只有几十毫秒，串行执行更稳定、总耗时也更低。
    await _init_db()
    if settings.schedule_enabled:
        await _init_scheduler()

    set_ready()
    snap = report()
    log.info("服务就绪 | 启动总耗时 {} ms | 阶段明细 {}", snap["startup_ms"], snap["stages_ms"])
    log.info("健康检查: GET /health")

    yield

    if settings.schedule_enabled:
        _shutdown_scheduler()
    await _shutdown_db()


async def _init_db() -> None:
    from db import init_database

    begin("database")
    try:
        await init_database()
    finally:
        end("database")


async def _shutdown_db() -> None:
    from db import dispose

    await dispose()


async def _init_scheduler() -> None:
    from scheduler.scheduler_ import setup_scheduler

    begin("scheduler")
    try:
        # AsyncIOScheduler 必须在主事件循环所在线程 start，不能丢进 to_thread。
        # 与数据库初始化保持串行：实测并行会和首次 SQLite 连接争资源，反而更慢。
        setup_scheduler()
    finally:
        end("scheduler")


def _shutdown_scheduler() -> None:
    from scheduler.scheduler_ import shutdown_scheduler

    shutdown_scheduler()


def _health() -> dict:
    """健康检查 + 启动耗时报告；不鉴权、不受密码到期网关影响。"""
    from db.seed import get_seed_stats

    data = report()
    data.update({
        "app": settings.app_name,
        "version": "0.1.0",
        "auth_mode": settings.auth_mode,
        "db_backend": settings.db_backend,
        "seed": get_seed_stats(),
    })
    return ok(data)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="多用户便签应用：Web 便签 + PyQt 桌面贴纸 + 管理后台（RBAC / JWT / SQLite）",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    # 必须在根路径静态挂载之前注册，否则会被 StaticFiles 遮蔽
    app.add_api_route("/health", _health, methods=["GET"], tags=["运维"],
                      summary="健康检查与启动耗时报告")

    # 管理后台：模板 SPA 位于 frontend/static/admin/，经 /admin 访问
    admin_dir = FRONTEND_DIR / "admin"
    if admin_dir.is_dir():
        app.mount("/admin", StaticFiles(directory=str(admin_dir), html=True), name="admin")
    # 根路径托管便签 Web 应用；须在 /admin 之后挂载，避免遮蔽
    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "18000")),
                reload=settings.debug)
