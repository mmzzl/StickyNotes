"""api/config 纯逻辑测试（mock HOME/XDG，不启动 Qt）。"""
import os
import tempfile
import unittest


class TokenStoreTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._td.name

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._td.cleanup()

    def test_save_load_roundtrip(self):
        import config as cc

        cc.save_config({"base_url": "http://x:1", "access_token": "t",
                        "refresh_token": "r", "username": "u"})
        cfg = cc.load_config()
        self.assertEqual(cfg["access_token"], "t")
        self.assertEqual(cfg["username"], "u")


class _FakeResp401:
    status_code = 401

    def json(self):
        return {"success": False, "code": 401, "message": "登录已过期"}


class _FakeSession401:
    def request(self, *_args, **_kwargs):
        return _FakeResp401()


class AuthExpiryTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._td.name

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._td.cleanup()

    def test_401_triggers_auth_failed_callback(self):
        from api import Client

        c = Client(base_url="http://127.0.0.1:1")
        c.session = _FakeSession401()
        calls = []
        c.on_auth_failed = lambda: calls.append(1)
        with self.assertRaises(Exception):
            c.list_notes()
        self.assertEqual(calls, [1])  # 会话过期 → App 弹回登录框


if __name__ == "__main__":
    unittest.main()
