"""启动阶段计时（零第三方依赖）。

在 lifespan 各阶段用 begin/end 打点，结果由 GET /health 暴露，
既用于运维就绪探测，也用于量化「启动优化」前后效果。

注意：本模块必须尽量早地被 import，才能覆盖全部第三方库的导入耗时。
"""

from __future__ import annotations

import os
import time
from threading import Lock

# 模块首次加载 ≈ 进程启动后开始 import 应用的时刻
_T0 = time.perf_counter()
_starts: dict[str, float] = {}
_stages_ms: dict[str, float] = {}
_lock = Lock()
_ready = False
_ready_ms: float | None = None


def begin(name: str) -> None:
    """开始一个计时阶段；同名阶段可重复调用（后者覆盖）。"""
    with _lock:
        _starts[name] = time.perf_counter()


def end(name: str) -> float:
    """结束阶段并返回耗时（毫秒）。未 begin 的按 _T0 起算。"""
    now = time.perf_counter()
    with _lock:
        start = _starts.pop(name, _T0)
        ms = round((now - start) * 1000, 2)
        _stages_ms[name] = ms
    return ms


# 覆盖：全部顶层 import + create_app() 构建应用
begin("import_app")


def set_ready() -> None:
    """lifespan 全部完成、服务可接受请求时调用。"""
    global _ready, _ready_ms
    with _lock:
        _ready = True
        _ready_ms = round((time.perf_counter() - _T0) * 1000, 2)


def report() -> dict:
    with _lock:
        return {
            "status": "ok" if _ready else "starting",
            "ready": _ready,
            "startup_ms": _ready_ms,
            "uptime_ms": round((time.perf_counter() - _T0) * 1000, 2),
            "stages_ms": dict(_stages_ms),
            "pid": os.getpid(),
        }
