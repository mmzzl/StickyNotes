"""便签客户端入口（pywebview 版）：有令牌则直接进便签桌，否则先登录。

窗口生命周期（Windows / edgechromium 下踩过坑，别再改回去）：

- pywebview 的 GUI 循环只能由主线程 ``webview.start()`` 启动。
- winforms 后端把**第一个窗口**（master）的创建放在主线程，并在那里调用
  ``signal.signal(signal.SIGINT, ...)``；这个调用只允许主线程执行。
- 所以**所有窗口都必须在 start() 之前、在主线程里创建完毕**。
  旧做法是「登录成功后在 js_api 回调线程里 new Desk()」：登录窗先被销毁，
  master 消失，新窗口又被当成 master 重建 → 子线程调用 signal.signal 抛
  ``signal only works in main thread of the main interpreter``；异常被吞成一条
  日志，此时一个窗口都不剩，pywebview 主循环返回、进程静默退出，表现就是
  「登录后窗口不见了，进程也找不着」。
- 现在的做法：没有令牌时先把便签桌窗口**隐藏**建好（它就是 master），再建
  登录窗；登录成功回调只负责 show 这个已存在的窗口，绝不在子线程里
  create_window。登录窗销毁后 master 仍在，主循环不会退出。
"""
import os
import sys

from sticky_log import get_logger, log_path, setup_logging

setup_logging()
log = get_logger("app")

import webview  # noqa: E402

from api import Client  # noqa: E402
from config import config_dir  # noqa: E402
from desk import Desk  # noqa: E402
from login_dialog import LoginDialog  # noqa: E402


def _start_webview():
    """统一启动 pywebview。

    private_mode 默认 True 会让 WebView2 每次都用临时用户数据目录，
    冷启动、复用不了任何缓存，打包后首屏明显变慢。改成持久目录后
    第二次起 WebView2 直接复用 JIT/资源缓存。
    """
    storage = config_dir() / "webview"
    try:
        storage.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning("创建 webview 缓存目录失败：%s", e)
    webview.start(gui=_gui(), private_mode=False, storage_path=str(storage))


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

    logged_in = bool(client.access_token)

    # 无论是否要登录，都在主线程先建便签桌窗口。未登录时隐藏，作为 master 常驻，
    # 避免登录窗销毁后出现「零窗口」让 pywebview 直接结束进程。
    desk = Desk(client, hidden=not logged_in, start_tray=logged_in)
    _bind_auth_failed(client, desk)

    if not logged_in:
        dlg = LoginDialog(client)
        dlg.on_success = desk.activate
        log.info("等待登录（便签桌窗口已预建并隐藏）")

    _start_webview()
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


def _fatal(e):
    """启动期异常兜底：exe 是 console=False，stderr 用户看不见，必须弹窗 + 落日志。"""
    log.exception("客户端启动失败")
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, "便签客户端启动失败：\n%s\n\n详细日志：%s" % (e, log_path()),
                "便签", 0x10)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        _fatal(e)
        sys.exit(1)
