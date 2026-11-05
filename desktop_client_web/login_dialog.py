"""登录/注册窗口（含验证码、可配置服务器地址）。"""
import base64
import os
import sys

import webview

from api import ApiError
from sticky_log import get_logger

log = get_logger("login")

_WEB_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "web")


def _web_file(name):
    return os.path.join(_WEB_DIR, name)


class LoginApi:
    def __init__(self, dlg):
        self._d = dlg

    def fetch_captcha(self):
        cid, raw = self._d.client.fetch_captcha()
        return {"captcha_id": cid,
                "image_base64": base64.b64encode(raw).decode("ascii")}

    def set_server(self, url):
        url = (url or "").strip()
        if not url:
            return ""
        changed = url != self._d.client.base_url
        self._d.client.set_base_url(url)
        return "已切换" if changed else ""

    def login(self, username, password, captcha_id, captcha_code):
        try:
            self._d.client.login(username, password, captcha_id, captcha_code)
        except Exception as e:  # noqa: BLE001
            log.info("登录失败：%s", e)
            return {"ok": False, "error": _msg(e)}
        return self._finish_result()

    def register(self, username, password, display_name=""):
        try:
            self._d.client.register(username, password, display_name)
        except Exception as e:  # noqa: BLE001
            log.info("注册失败：%s", e)
            return {"ok": False, "error": _msg(e)}
        return self._finish_result()

    def _finish_result(self):
        """令牌已落盘，接下来把便签桌显示出来；失败要把原因回给页面。"""
        err = self._d.finish()
        if err:
            return {"ok": False, "error": err}
        return {"ok": True}


def _msg(e):
    if isinstance(e, ApiError):
        return e.message
    return str(e)


class LoginDialog:
    def __init__(self, client):
        self.client = client
        self.ok = False
        self.on_success = None
        self.api = LoginApi(self)
        self.window = webview.create_window(
            title="便签 - 登录",
            url=_web_file("login.html"),
            js_api=self.api,
            width=420, height=460,
            min_size=(380, 420),
            resizable=True,
        )
        self.window.events.loaded += self._on_loaded

    def _on_loaded(self):
        try:
            self.window.evaluate_js(
                "window.pywebviewInitLogin(%s)" % _js(self.client.base_url))
        except Exception as e:  # noqa: BLE001
            log.warning("登录页初始化失败：%s", e)

    def finish(self):
        """登录/注册成功：**先让便签桌就位，再销毁登录窗**。返回错误文本或空串。

        顺序不能反。登录窗一度是唯一可见窗口，先销毁会让 pywebview 认为窗口
        全没了，主循环立即返回、进程静默退出（历史 bug）。另外回调里绝不能
        create_window —— 它跑在 js_api 线程，会触发 winforms 的 master 初始化
        （signal.signal 只允许主线程）。现在回调只 show 预建好的窗口。

        万一激活失败，**保留登录窗**并把原因显示给用户：此时令牌已保存，
        重新启动客户端即可直接进入便签桌。
        """
        self.ok = True
        if self.on_success:
            try:
                self.on_success()
            except Exception as e:  # noqa: BLE001
                log.exception("登录成功回调失败，保留登录窗")
                return ("登录已成功，但打开便签桌失败：%s\n"
                        "登录状态已保存，请关闭本窗口后重新启动客户端。" % e)
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            # 便签桌已经显示，登录窗关不掉不算致命，记日志即可
            log.exception("销毁登录窗失败")
        return ""


def _js(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
