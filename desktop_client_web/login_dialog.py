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
        self._d.ok = True
        self._d.finish()
        return {"ok": True}

    def register(self, username, password, display_name=""):
        try:
            self._d.client.register(username, password, display_name)
        except Exception as e:  # noqa: BLE001
            log.info("注册失败：%s", e)
            return {"ok": False, "error": _msg(e)}
        self._d.ok = True
        self._d.finish()
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
        """登录/注册成功：关掉登录窗并通知入口开便签桌。"""
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass
        if self.on_success:
            try:
                self.on_success()
            except Exception as e:  # noqa: BLE001
                log.warning("登录成功回调失败：%s", e)


def _js(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
