"""便签客户端入口（pywebview 版）：有令牌则直接进便签桌，否则先登录。

流程上与 PyQt5 版的差异：

- pywebview 的 GUI 循环只能由主线程 ``webview.start()`` 启动，窗口须在 start
  之前创建，不能用 ``dlg.exec_()`` 那样阻塞等待登录结果。
- 但登录成功后再开便签桌是可以的：GTK 后端对非首个窗口走
  ``glib.idle_add(create)``，这是线程安全的，所以在登录回调所在的线程里直接
  建 Desk 即可，无需轮询。
"""
import os
import sys

from sticky_log import get_logger, log_path, setup_logging

setup_logging()
log = get_logger("app")

import webview  # noqa: E402

from api import Client  # noqa: E402
from desk import Desk  # noqa: E402
from login_dialog import LoginDialog  # noqa: E402


def main() -> int:
    client = Client()
    log.info("启动 pid=%d 日志=%s 便签服务=%s GUI=pywebview",
             os.getpid(), log_path(), client.base_url)

    if client.access_token:
        try:
            client.list_notes()
            log.info("令牌有效，直接进便签桌")
        except Exception as e:  # noqa: BLE001
            log.info("令牌失效（%s），转登录", e)
            client.access_token = ""

    if client.access_token:
        desk = Desk(client)
        _bind_auth_failed(client, desk)
        webview.start(gui=_gui())
        return 0

    dlg = LoginDialog(client)
    dlg.on_success = lambda: (log.info("登录成功，打开便签桌"),
                              _bind_auth_failed(client, Desk(client)))
    webview.start(gui=_gui())
    return 0


def _bind_auth_failed(client, desk):
    """会话过期时返回登录（简版：提示后退出到登录流程，不做热切换）。

    Qt 版会在 401 时静默关掉所有便签窗再弹登录框。pywebview 里窗口生命周期
    由 GUI 循环托管，热切换容易留下半销毁的窗口，这里选择更保守的做法：
    记日志并由用户重新启动。后续如需热切换再单独实现。
    """
    def on_auth_failed():
        log.warning("会话已过期(401)，请重新登录")

    client.on_auth_failed = on_auth_failed
    desk.session_expired = on_auth_failed


def _gui():
    """Linux 强制 GTK（WebKitGTK）；Windows 交给 pywebview 自选（edgechromium）。"""
    if sys.platform.startswith("linux"):
        return "gtk"
    return None


if __name__ == "__main__":
    sys.exit(main())
