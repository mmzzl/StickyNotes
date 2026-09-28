# sticky_log.py —— 便签客户端的运行日志
#
# 为什么需要：排版视图（QtWebEngine / Chromium）里的问题——首屏空白、滚动条不灵、
# 滚轮没反应——在 Qt 这侧看不到任何报错，窗口也不报错、日志也没有，全靠猜。
# 这里把关键节点和页面内测量值落到一个文件里，排查时直接看日志就能定位。
#
# 用法：
#     from sticky_log import get_logger
#     log = get_logger("md")
#     log.info("页面加载完成 ok=%s 耗时=%dms", ok, ms)
#
# 环境变量：
#     STICKY_LOG=debug|info|warn|error|off   控制日志级别（默认 info）
#     STICKY_LOG_FILE=/path/to/x.log         覆盖日志文件路径
"""便签客户端的运行日志（落文件，便于排查排版视图这类"窗口不报错"的问题）。"""
import logging
import os
import sys
import time

from config import config_dir

_LEVELS = {
    "off": logging.CRITICAL + 10,
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

_configured = False
_t0 = time.time()   # 进程内基准时刻，日志里统一打"启动后多少毫秒"


def log_path():
    """日志文件路径。优先 STICKY_LOG_FILE，否则固定放在配置目录里。"""
    override = os.environ.get("STICKY_LOG_FILE")
    if override:
        return override
    return str(config_dir() / "sticky-notes.log")


def _level():
    return _LEVELS.get(os.environ.get("STICKY_LOG", "info").strip().lower(),
                       logging.INFO)


class _Fmt(logging.Formatter):
    """带"启动后 N ms"的格式：所有节点能按时间轴对齐，看得出谁在等谁。"""

    default_time_format = "%H:%M:%S"

    def format(self, record):
        record.elapsed_ms = int((time.time() - _t0) * 1000)
        return super().format(record)


def setup_logging():
    """装好根 logger。app 入口调一次；重复调用是安全的。"""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("sticky")
    root.setLevel(_level())
    root.propagate = False
    if root.handlers:
        return

    fmt = _Fmt("%(asctime)s.%(msecs)03d +%(elapsed_ms)6dms [%(name)s] %(message)s",
               datefmt="%H:%M:%S")

    path = log_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:      # 磁盘满/只读目录：降级成只往 stderr 打，不影响使用
        print(f"[sticky] 无法写日志文件 {path}: {e}", file=sys.stderr)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str) -> logging.Logger:
    """取一个子 logger。没调 setup_logging() 也会自动补上，省得到处记得初始化。"""
    setup_logging()
    return logging.getLogger("sticky." + name)


def ms_since(t: float) -> int:
    """距某个 time.time() 点的毫秒数，日志里量耗时用。"""
    return int((time.time() - t) * 1000)
