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
- 现在的做法：先把便签桌窗口**隐藏**建好（它就是 master），再建登录窗；
  登录成功回调只负责 show 这个已存在的窗口，绝不在子线程里 create_window。
- 两个窗口都**始终预建、只 show/hide、不销毁**。以前只在未登录时建登录窗，
  自动续期上线后令牌长期有效，用户就再也回不到登录页，换服务器地址 /
  切换账号 / 退出登录都没了入口。登录时把登录窗隐藏即可，需要时再 show。
"""
import os
import sys
import threading

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

    # 启动自检：access 过期/快过期时先用 refresh 静默续期，再探一次后端。
    # 只有「明确鉴权失效」才回登录；服务暂时连不上时保留登录态，
    # 免得每次断网/服务重启都被误踢回登录框。
    logged_in = False
    if client.access_token or client.refresh_token:
        try:
            client.ensure_session()
            client.list_notes()
            logged_in = bool(client.access_token) and not client.auth_invalid
            if logged_in:
                log.info("会话有效，直接进便签桌%s",
                         "（已自动续期）" if client.renewed_this_session else "")
        except Exception as e:  # noqa: BLE001
            log.warning("启动自检未通过：%s", e)
            logged_in = bool(client.access_token) and not client.auth_invalid
    if not logged_in:
        log.info("需要登录（刷新令牌不可用或已失效）")

    # 无论是否要登录，都在主线程先建便签桌窗口。未登录时隐藏，作为 master 常驻，
    # 避免登录窗销毁后出现「零窗口」让 pywebview 直接结束进程。
    desk = Desk(client, hidden=not logged_in, start_tray=logged_in)

    # 登录窗也**始终**预建：已登录时隐藏，需要改服务器/切换账号/退出登录时
    # 直接 show 复用。以前只在「未登录」时建，自动续期后令牌长期有效，就再也
    # 回不到登录页了 —— 换设备地址、退出账号全都没有入口。
    dlg = LoginDialog(client, desk=desk, hidden=logged_in)
    dlg.on_success = desk.activate
    desk.set_login_dialog(dlg)
    _bind_auth_failed(client, desk)

    if not logged_in:
        log.info("等待登录（便签桌与登录窗均已在主线程预建）")
    else:
        log.info("直接进入便签桌（登录窗已隐藏，可随时从「账号」菜单打开）")

    _start_webview()
    return 0


def _bind_auth_failed(client, desk):
    """刷新令牌也失效时（长期未使用 / 别处登出或改密）切回登录窗。

    绝大多数 401 已被 api.Client 的自动续期吃掉，走到这里属于真·失效。
    登录窗是主线程预建并复用的，所以这里可以安全地 show 它，不必再让用户
    「关掉客户端重开」；仍不 create_window，避免 winforms 的 signal 问题。
    """
    def on_auth_failed():
        log.warning("会话已失效，切回登录窗口")
        if desk.login_dialog is None:
            _notify_relogin()
            return
        try:
            desk.enter_logged_out("登录已过期，请重新登录")
            desk.show_login(cancelable=False, reason="登录已过期，请重新登录")
        except Exception:  # noqa: BLE001
            log.exception("切回登录窗失败")
            _notify_relogin()

    client.on_auth_failed = on_auth_failed
    desk.session_expired = on_auth_failed


def _notify_relogin():
    """Windows 下弹一次非阻塞提示。exe 是 console=False，日志用户看不见。"""
    if sys.platform != "win32":
        return

    def show():
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0, "登录已过期，请重新登录。\n\n"
                   "关闭客户端后重新打开，会自动显示登录窗口。",
                "便签 - 登录已过期", 0x40)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=show, daemon=True).start()


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
