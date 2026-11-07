"""账号切换 / 退出登录 / 改服务器地址回归测试（无 GUI、无真实服务）。

历史上真出过的问题：自动续期后 config.json 里一直有令牌，app.main() 就
不再创建登录窗；服务器地址换了（或干脆连不上）时 _renew 又刻意保留登录态，
于是客户端卡在便签桌，没有任何入口改地址、切换账号或退出登录。

这里锁住修复后的关键行为：
- 登录窗始终预建，只 show/hide，绝不 destroy 再 create
- 关闭登录窗必须取消关闭并把便签桌显示回来（不留零可见窗口的隐形进程）
- 退出登录/改地址都要清掉旧令牌，且服务器连不上也能退干净
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)


class _Sig:
    def __init__(self):
        self._handlers = []

    def __iadd__(self, fn):
        self._handlers.append(fn)
        return self

    def fire(self, *a):
        return [fn(*a) for fn in self._handlers]


class _FakeEvents:
    def __init__(self):
        self.loaded = _Sig()
        self.closed = _Sig()
        self.closing = _Sig()


class _FakeWindow:
    def __init__(self, **kw):
        self.kw = kw
        self.events = _FakeEvents()
        self.js_calls = []
        self.destroyed = False
        self.shown = 0
        self.hidden = 0

    def evaluate_js(self, code):
        self.js_calls.append(code)
        return None

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()


class _FakeWebview:
    SAVE_DIALOG = 1
    OPEN_DIALOG = 2

    def __init__(self):
        self.windows = []

    def create_window(self, **kw):
        w = _FakeWindow(**kw)
        self.windows.append(w)
        return w


_ORIG_CREATE_WINDOW = None
_fake_wv = None


def setUpModule():
    global _ORIG_CREATE_WINDOW, _fake_wv
    import webview
    _ORIG_CREATE_WINDOW = webview.create_window
    _fake_wv = _FakeWebview()
    webview.create_window = _fake_wv.create_window


def tearDownModule():
    import webview
    webview.create_window = _ORIG_CREATE_WINDOW


class _FakeSession:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if self.fail:
            import requests
            raise requests.ConnectionError("server unreachable")
        return _Resp(200)


class _Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = ""

    def json(self):
        return self._data


class _IsolatedCfg:
    """把 config.json 隔离到临时目录，避免碰用户真实登录态。"""

    def __init__(self):
        self.td = None
        self._old = None

    def __enter__(self):
        self.td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.td.name
        return self

    def __exit__(self, *a):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.td.cleanup()

    def read(self):
        import config as cfgmod
        return json.loads(cfgmod.config_file().read_text(encoding="utf-8"))

    def seed(self, **kw):
        import config as cfgmod
        data = dict(cfgmod._EMPTY)
        data.update(kw)
        cfgmod.save_config(data)


class ClientAccountTest(unittest.TestCase):
    def test_logout_clears_tokens_even_when_server_unreachable(self):
        import api
        with _IsolatedCfg() as box:
            box.seed(base_url="http://old-host:18000", access_token="A",
                     refresh_token="R", username="admin")
            c = api.Client()
            c.session = _FakeSession(fail=True)
            c.on_auth_failed = lambda: self.fail("主动登出不该触发会话失效回调")
            c.auth_invalid = True          # 模拟之前被判定失效的状态
            c.logout()
            self.assertEqual(c.access_token, "")
            self.assertEqual(c.refresh_token, "")
            self.assertEqual(c.username, "")
            self.assertFalse(c.auth_invalid)
            self.assertFalse(c._notified)
            disk = box.read()
            self.assertEqual(disk["access_token"], "")
            self.assertEqual(disk["refresh_token"], "")
            self.assertEqual(disk["username"], "")
            self.assertEqual(disk["base_url"], "http://old-host:18000")  # 地址保留
            self.assertTrue(any("/auth/logout" in u for u, _ in c.session.calls))

    def test_set_base_url_clears_tokens_and_resets_auth_flag(self):
        import api
        with _IsolatedCfg() as box:
            box.seed(base_url="http://old-host:18000", access_token="A",
                     refresh_token="R", username="admin")
            c = api.Client()
            c.auth_invalid = True
            c._notified = True
            new_url = c.set_base_url("192.168.1.50:18000")
            self.assertEqual(new_url, "http://192.168.1.50:18000")
            self.assertEqual(c.base_url, new_url)
            self.assertEqual(c.access_token, "")
            self.assertEqual(c.refresh_token, "")
            self.assertFalse(c.auth_invalid)
            self.assertFalse(c._notified)
            disk = box.read()
            self.assertEqual(disk["base_url"], new_url)
            self.assertEqual(disk["access_token"], "")


class LoginDialogReuseTest(unittest.TestCase):
    def setUp(self):
        import api
        import login_dialog
        self.mod = login_dialog
        self.cfg = _IsolatedCfg()
        self.cfg.__enter__()
        self.cfg.seed(access_token="", refresh_token="")
        self.client = api.Client()
        self.client.session = _FakeSession()
        self.dlg = login_dialog.LoginDialog(self.client, hidden=True)
        self.dlg.window.events.loaded.fire()

    def tearDown(self):
        self.cfg.__exit__(None, None, None)

    def test_hidden_dialog_does_not_fetch_captcha_on_load(self):
        # 隐藏预建的登录窗不该在启动时白打一次验证码接口
        self.assertFalse(any("pywebviewInitLogin" in c for c in self.dlg.window.js_calls))

    def test_show_initializes_form_with_current_server(self):
        self.dlg.show(cancelable=True, reason="切换账号")
        self.assertTrue(any("pywebviewInitLogin" in c for c in self.dlg.window.js_calls))
        call = [c for c in self.dlg.window.js_calls if "pywebviewInitLogin" in c][-1]
        self.assertIn(self.client.base_url, call)
        self.assertIn("true", call)
        self.assertIn("切换账号", call)
        self.assertGreaterEqual(self.dlg.window.shown, 1)

    def test_finish_hides_instead_of_destroying(self):
        activated = []
        self.dlg.on_success = lambda: activated.append(1)
        err = self.dlg.finish()
        self.assertEqual(err, "")
        self.assertEqual(activated, [1])
        self.assertFalse(self.dlg.window.destroyed, "登录窗不能被销毁，之后还要复用")
        self.assertEqual(self.dlg.window.hidden, 1)

    def test_closing_is_cancelled_and_desk_is_shown_again(self):
        seen = []
        self.dlg.on_close = lambda: seen.append("closed")
        self.dlg.show(cancelable=True)
        results = self.dlg.window.events.closing.fire()
        self.assertIn(False, results, "必须取消真实关闭")
        self.assertFalse(self.dlg.window.destroyed)
        self.assertEqual(seen, ["closed"])

    def test_quit_allows_real_destruction(self):
        self.dlg.destroy()
        self.assertTrue(self.dlg.window.destroyed)
        results = self.dlg.window.events.closing.fire()
        self.assertIn(True, results, "退出时必须放行关闭，否则残留隐形进程")

    def test_cancel_hides_and_notifies(self):
        seen = []
        self.dlg.on_close = lambda: seen.append(1)
        self.dlg.show(cancelable=True)
        self.dlg.api.cancel()
        self.assertEqual(seen, [1])
        self.assertFalse(self.dlg.window.destroyed)

    def test_set_server_same_url_is_noop(self):
        with mock.patch.object(self.client, "set_base_url") as m:
            self.assertEqual(self.dlg.api.set_server(self.client.base_url), "")
            m.assert_not_called()

    def test_set_server_switches_url_and_deactivates_desk(self):
        class _Desk:
            active = True
            def __init__(self):
                self.messages = []
            def enter_logged_out(self, msg):
                self.messages.append(msg)
        fake = _Desk()
        self.dlg.desk = fake
        msg = self.dlg.api.set_server("http://new-host:18000")
        self.assertIn("重新登录", msg)
        self.assertEqual(self.client.base_url, "http://new-host:18000")
        self.assertEqual(self.client.access_token, "")
        self.assertEqual(len(fake.messages), 1)


class DeskAccountTest(unittest.TestCase):
    def setUp(self):
        import api
        import desk as deskmod
        import login_dialog
        self.mod = deskmod
        self._orig_create_tray = deskmod.tray_mod.create_tray
        deskmod.tray_mod.create_tray = lambda d: None   # 测试里不起真实托盘
        self.cfg = _IsolatedCfg()
        self.cfg.__enter__()
        self.cfg.seed(base_url="http://old-host:18000", access_token="A",
                      refresh_token="R", username="admin")
        self.client = api.Client()
        self.client.session = _FakeSession()
        self.desk = deskmod.Desk(self.client, start_tray=False)
        self.dlg = login_dialog.LoginDialog(self.client, desk=self.desk, hidden=True)
        self.desk.set_login_dialog(self.dlg)
        self.dlg.on_success = self.desk.activate
        self.dlg.window.events.loaded.fire()

    def tearDown(self):
        self.mod.tray_mod.create_tray = self._orig_create_tray
        self.cfg.__exit__(None, None, None)

    def _add_fake_note_window(self, nid):
        class _W:
            def __init__(self):
                self.closed = 0
            def request_close(self):
                self.closed += 1
        w = _W()
        self.desk.windows[nid] = w
        return w

    def test_get_settings(self):
        s = self.desk.api.get_settings()
        self.assertEqual(s["username"], "admin")
        self.assertEqual(s["base_url"], "http://old-host:18000")
        self.assertTrue(s["active"])

    def test_show_login_can_be_cancelled_without_touching_tokens(self):
        self.desk.show_login(cancelable=True)
        self.assertTrue(self.dlg.cancelable)
        self.assertEqual(self.client.access_token, "A")
        self.assertTrue(self.desk.active)
        self.assertEqual(self.dlg.window.shown, 1)
        self.desk.on_login_closed()
        self.assertTrue(self.desk.active)
        self.assertGreaterEqual(self.desk.window.shown, 1)

    def test_logout_clears_tokens_closes_notes_and_opens_login(self):
        w = self._add_fake_note_window("n1")
        self.desk.logout()
        self.assertEqual(self.client.access_token, "")
        self.assertEqual(self.client.refresh_token, "")
        self.assertEqual(w.closed, 1)
        self.assertEqual(self.desk.windows, {})
        self.assertFalse(self.desk.active)
        self.assertFalse(self.dlg.cancelable)
        self.assertEqual(self.dlg.window.shown, 1)
        self.assertEqual(self.cfg.read()["access_token"], "")
        self.assertTrue(any("pywebviewLoggedOut" in c for c in self.desk.window.js_calls))

    def test_set_server_from_desk_switches_and_relogin(self):
        self.desk.set_server("http://10.0.0.9:18000")
        self.assertEqual(self.client.base_url, "http://10.0.0.9:18000")
        self.assertEqual(self.client.access_token, "")
        self.assertFalse(self.desk.active)
        self.assertEqual(self.dlg.window.shown, 1)
        self.assertEqual(self.cfg.read()["base_url"], "http://10.0.0.9:18000")

    def test_activate_after_relogin_restores_active_state(self):
        self.desk.logout()
        self.assertFalse(self.desk.active)
        self.desk.activate()
        self.assertTrue(self.desk.active)
        self.assertTrue(any("pywebviewStart" in c for c in self.desk.window.js_calls))

    def test_login_success_callback_is_wired(self):
        self.desk.logout()
        self.dlg.finish()          # 模拟登录成功
        self.assertTrue(self.desk.active)
        self.assertEqual(self.dlg.window.hidden, 1)
        self.assertFalse(self.dlg.window.destroyed)

    def test_quit_destroys_hidden_login_window_too(self):
        self.desk.quit_app()
        self.assertTrue(self.dlg.window.destroyed, "隐藏登录窗不销毁会留下隐形进程")
        self.assertTrue(self.desk.window.destroyed)
        self.assertIsNone(self.desk.tray)


class AppWiringTest(unittest.TestCase):
    """app.main() 无论是否已登录都必须预建登录窗，并挂到 desk 上。"""

    def test_login_dialog_always_precreated(self):
        import app as appmod

        class _FakeDesk:
            active = True
            def __init__(self, client, hidden=False, start_tray=True):
                self.client = client
                self.login_dialog = None
                self.connected = None
            def set_login_dialog(self, dlg):
                self.login_dialog = dlg
                dlg.on_close = lambda: None
                self.connected = dlg.on_close
            def activate(self):
                pass

        class _FakeDlg:
            def __init__(self, client, desk=None, hidden=False):
                self.client = client
                self.desk = desk
                self.hidden = hidden
                self.on_success = None
                self.on_close = None
                self.created.append(self)
            created = []

        class _FakeClient:
            access_token = "A"
            refresh_token = "R"
            auth_invalid = False
            renewed_this_session = False
            base_url = "http://127.0.0.1:18000"
            username = "admin"
            on_auth_failed = None
            def __init__(self):
                pass
            def ensure_session(self):
                return True
            def list_notes(self):
                return [], 0

        cfg = _IsolatedCfg()
        with cfg:
            cfg.seed(access_token="A", refresh_token="R")
            old = (appmod.Client, appmod.Desk, appmod.LoginDialog, appmod._start_webview)
            try:
                appmod.Client = _FakeClient
                appmod.Desk = _FakeDesk
                appmod.LoginDialog = _FakeDlg
                appmod._start_webview = lambda: None
                appmod.main()
            finally:
                appmod.Client, appmod.Desk, appmod.LoginDialog, appmod._start_webview = old
            self.assertEqual(len(_FakeDlg.created), 1)
            dlg = _FakeDlg.created[0]
            self.assertTrue(dlg.hidden, "已登录时登录窗应隐藏预建")
            self.assertIsNotNone(dlg.on_close)
            self.assertIsNotNone(dlg.on_success)


class FrontendContractTest(unittest.TestCase):
    def test_desk_html_has_account_controls(self):
        web = os.path.join(_HERE, "web")
        with open(os.path.join(web, "desk.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("set_server", "show_login", "logout", "get_settings",
                       "pywebviewLoggedOut", "切换服务器 / 重新登录"):
            self.assertIn(needle, html, needle)

    def test_login_html_reset_and_cancel(self):
        web = os.path.join(_HERE, "web")
        with open(os.path.join(web, "login.html"), encoding="utf-8") as f:
            html = f.read()
        for needle in ("pywebviewInitLogin = function (baseUrl, canCancel, reason)",
                       "api().cancel()", "getElementById('cancel')",
                       "api().current_server()", "function boot()"):
            self.assertIn(needle, html, needle)


if __name__ == "__main__":
    unittest.main()
