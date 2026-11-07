"""登录/注册窗口（含验证码、可配置服务器地址）。

窗口生命周期（与 app.py 的 master 窗口约束是一套东西，别改回去）：

pywebview 的 winforms 后端把**第一个**窗口当 master，并在创建时调用
signal.signal(SIGINT)，只允许主线程执行。所以登录窗必须在 webview.start()
之前、主线程里创建一次，之后**只 show/hide，绝不 destroy 再 create**。
以前只在「未登录」时才建登录窗，自动续期上线后 config.json 里一直有令牌，
就再也进不了登录页 —— 换服务器地址、切换账号、退出登录全都没了入口。
现在无论是否登录都预建：登录时隐藏，需要时 show() 复用。
"""
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

    def current_server(self):
        return self._d.client.base_url

    def set_server(self, url):
        """改服务器地址：清掉旧令牌，并让便签桌切到「未登录」状态。

        这一步很关键：用户往往正是「换了设备地址、旧地址连不上」才进来改，
        如果还留着旧令牌，重新登录后请求仍会打到旧地址上。
        """
        url = (url or "").strip()
        if not url:
            return "服务器地址不能为空"
        if url == self._d.client.base_url:
            return ""
        self._d.client.set_base_url(url)
        desk = self._d.desk
        if desk is not None and desk.active:
            desk.enter_logged_out("服务器地址已更改，请重新登录")
        return "已切换，请重新登录"

    def cancel(self):
        """取消切换：交回便签桌（令牌未动，原会话可直接继续用）。"""
        self._d.cancel()
        return ""

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
    def __init__(self, client, desk=None, hidden=False):
        self.client = client
        self.desk = desk
        self.ok = False
        self.on_success = None
        self.on_close = None          # 关闭登录窗时由 app 挂上 desk.on_login_closed
        self.cancelable = False
        self.reason = ""
        self._visible = not hidden
        self._loaded = False
        self._quitting = False   # 整个应用退出时才允许真正销毁
        self.api = LoginApi(self)
        self.window = webview.create_window(
            title="便签 - 登录",
            url=_web_file("login.html"),
            js_api=self.api,
            width=420, height=480,
            min_size=(380, 440),
            resizable=True,
            hidden=hidden,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing

    # ---- 显示 / 隐藏（全程复用同一个窗口） ----
    def show(self, cancelable=False, reason=""):
        """显示登录窗。cancelable=True 表示「切换账号」：可取消回原会话。"""
        self.cancelable = cancelable
        self.reason = reason or ""
        self._visible = True
        if self._loaded:
            self._init_page()          # 还没 loaded 就交给 _on_loaded 补做
        try:
            self.window.show()
        except Exception as e:  # noqa: BLE001
            log.warning("显示登录窗失败：%s", e)

    def hide(self):
        self._visible = False
        try:
            self.window.hide()
        except Exception as e:  # noqa: BLE001
            log.warning("隐藏登录窗失败：%s", e)

    def _init_page(self):
        """每次打开都重置表单：带上最新服务器地址、清掉上次的密码/报错/验证码。"""
        try:
            self.window.evaluate_js(
                "window.pywebviewInitLogin && window.pywebviewInitLogin(%s, %s, %s)"
                % (_js(self.client.base_url), _js(bool(self.cancelable)),
                   _js(self.reason)))
        except Exception as e:  # noqa: BLE001
            log.warning("登录页初始化失败：%s", e)

    def _on_loaded(self):
        self._loaded = True
        if self._visible:
            self._init_page()

    def _on_closing(self):
        """取消真实关闭：只隐藏，并把便签桌显示回来。

        绝不能让登录窗被销毁 —— 它是预建复用的，销毁后既不能再 create_window
        （子线程触发 winforms master 初始化会抛 signal 错误），也可能让进程
        停在「一个窗口都不剩但消息循环还在」的隐形状态。只有整个应用退出时
        （destroy() 置了 _quitting）才放行。
        """
        if self._quitting:
            return True
        self.hide()
        cb = self.on_close
        if cb:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("登录窗关闭回调失败")
        return False

    def destroy(self):
        """应用退出：连同这个隐藏窗口一起销毁，否则 pywebview 不会结束消息循环。"""
        self._quitting = True
        self._visible = False
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            log.exception("销毁登录窗失败")

    def cancel(self):
        """页面「返回便签桌」按钮。"""
        self.hide()
        if self.on_close:
            try:
                self.on_close()
            except Exception:  # noqa: BLE001
                log.exception("取消登录回调失败")

    def finish(self):
        """登录/注册成功：**先让便签桌就位，再隐藏登录窗**。返回错误文本或空串。

        顺序不能反：先销毁唯一可见窗口会让 pywebview 认为窗口全没了、主循环
        立即返回。而且回调跑在 js_api 线程，绝不能 create_window。现在回调只
        show 预建好的窗口，登录窗改 hide 复用（下次退出登录还要用它）。
        """
        self.ok = True
        if self.on_success:
            try:
                self.on_success()
            except Exception as e:  # noqa: BLE001
                log.exception("登录成功回调失败，保留登录窗")
                return ("登录已成功，但打开便签桌失败：%s\n"
                        "登录状态已保存，请关闭本窗口后重新启动客户端。" % e)
        self.hide()
        return ""


def _js(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
