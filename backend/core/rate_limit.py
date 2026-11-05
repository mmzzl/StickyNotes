"""进程内滑动窗口限流（零依赖）。

适用场景：单 worker 部署（README 已要求，多 worker 会重复跑定时任务）。
多副本 / 多进程部署请在网关或 Redis 侧做全局限流，本模块只作兜底。

与 account_lock_service 的区别：
- 账户锁定按「用户名/IP + 失败次数」，防密码爆破；
- 本模块按「接口 + IP + 时间窗」，防验证码、注册这类**匿名**接口被刷。
"""
import threading
import time
from collections import defaultdict, deque

from config import settings
from core.exceptions import RateLimitedError

_buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()
_MAX_TRACKED_KEYS = 10000


def check(scope: str, key: str, limit: int | None = None, window: int = 60) -> None:
    """记录一次访问；超出窗口上限抛 RateLimitedError。

    scope 通常是接口名，key 通常是客户端 IP。limit=None 时取配置默认值。
    """
    if not settings.rate_limit_enabled:
        return
    limit = limit if limit is not None else settings.rate_limit_per_minute
    if limit <= 0:
        return

    now = time.monotonic()
    cutoff = now - window
    with _lock:
        # 惰性清理：仅在桶数量过多时扫一遍，避免每个请求都 O(n)
        if len(_buckets) > _MAX_TRACKED_KEYS:
            for k, q in list(_buckets.items()):
                while q and q[0] < cutoff:
                    q.popleft()
                if not q:
                    del _buckets[k]

        q = _buckets[(scope, key or "unknown")]
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= limit:
            retry = max(1, int(window - (now - q[0])) + 1)
            raise RateLimitedError(
                f"请求过于频繁，请 {retry} 秒后再试", retry_after=retry)
        q.append(now)


def reset() -> None:
    """清空计数（测试 / 运维手动复位用）。"""
    with _lock:
        _buckets.clear()
