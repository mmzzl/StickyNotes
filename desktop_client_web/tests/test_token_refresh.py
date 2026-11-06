"""令牌自动续期回归测试（无 GUI、无真实服务）。

历史上真出过的问题：access 30 分钟过期，客户端却从不调用 /auth/refresh，
隔一阵子就 401 被踢回登录框。这里锁住续期链路的关键行为：
- 提前续期、401 后重放、并发只刷一次
- 登录接口自身的 401 不能误判成会话过期
- 断网保留登录态、只有 refresh 明确失效才清空
- 多实例共用 config.json 时采用别人已换好的令牌
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest

import requests

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)

import api  # noqa: E402
import config as cfgmod  # noqa: E402


def _jwt(kind, exp):
    payload = base64.urlsafe_b64encode(
        json.dumps({"type": kind, "exp": exp}).encode()).decode().rstrip("=")
    return "header." + payload + ".sig"


def _resp(status, data=None, message=""):
    return _Resp(status, data, message)


class _Resp:
    def __init__(self, status, data=None, message=""):
        self.status_code = status
        self.text = message
        self._data = data

    def json(self):
        if self._data is not None:
            return self._data
        return {"message": self.text}


class _FakeSession:
    """记录调用并把响应决策交给测试提供的 handler。"""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self._lock = threading.Lock()

    def _log(self, kind, method, url, extra):
        with self._lock:
            self.calls.append((kind, method, url, extra))

    def request(self, method, url, **kw):
        self._log("request", method, url, kw.get("headers", {}).get("Authorization"))
        return self.handler("request", method, url, kw)

    def post(self, url, **kw):
        self._log("post", "POST", url, (kw.get("json") or {}).get("refresh_token"))
        return self.handler("post", "POST", url, kw)

    def get(self, url, **kw):
        self._log("get", "GET", url, "")
        return self.handler("get", "GET", url, kw)

    def count(self, kind):
        return len([c for c in self.calls if c[0] == kind])


class TokenRefreshTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.td.name
        self._old_wait = api._PEER_RACE_WAIT
        api._PEER_RACE_WAIT = 0        # 测试不等落盘竞态窗口
        self.notified = []
        self.new_pair = (self._jwt("future-access", 3600), "new-refresh")

    def tearDown(self):
        api._PEER_RACE_WAIT = self._old_wait
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.td.cleanup()

    @staticmethod
    def _jwt(kind, ttl):
        return _jwt(kind, time.time() + ttl)

    def _seed(self, access_ttl=3600, refresh="refresh-1"):
        cfgmod.save_config({
            "base_url": "http://127.0.0.1:18000",
            "access_token": self._jwt("access", access_ttl),
            "refresh_token": refresh,
            "username": "tester",
        })

    def _client(self, handler, **seed_kw):
        self._seed(**seed_kw)
        c = api.Client()
        c.session = _FakeSession(handler)
        c.on_auth_failed = lambda: self.notified.append(1)
        return c

    def _refresh_ok(self, kind, method, url, kw):
        if kind == "post":
            return _resp(200, {"data": {
                "access_token": self.new_pair[0],
                "refresh_token": self.new_pair[1],
                "token_type": "bearer"}})
        return _resp(200, {"data": {"items": [], "total": 0}})

    # ---- 核心链路 ----
    def test_proactive_refresh_before_expiry(self):
        c = self._client(self._refresh_ok, access_ttl=60)   # 剩余不足 5 分钟
        self.assertEqual(c.list_notes(), ([], 0))
        self.assertEqual(c.session.count("post"), 1)
        self.assertEqual(c.access_token, self.new_pair[0])
        disk = cfgmod.load_config()
        self.assertEqual(disk["refresh_token"], "new-refresh")
        self.assertTrue(c.renewed_this_session)

    def test_fresh_token_does_not_refresh(self):
        c = self._client(self._refresh_ok, access_ttl=3600)
        c.list_notes()
        self.assertEqual(c.session.count("post"), 0)

    def test_401_triggers_refresh_and_replays(self):
        seen = {"n": 0}

        def handler(kind, method, url, kw):
            if kind == "post":
                return self._refresh_ok(kind, method, url, kw)
            seen["n"] += 1
            if seen["n"] == 1:
                return _resp(401, message="令牌无效或已过期")
            return _resp(200, {"data": {"items": [{"id": "a"}], "total": 1}})

        c = self._client(handler, access_ttl=3600)
        items, total = c.list_notes()
        self.assertEqual((len(items), total), (1, 1))
        self.assertEqual(c.session.count("post"), 1)
        self.assertEqual(c.session.count("request"), 2)   # 原请求 + 重放
        # 重放必须带新令牌
        self.assertIn(self.new_pair[0], c.session.calls[-1][3])
        self.assertFalse(self.notified)

    def test_refresh_failure_clears_tokens_and_notifies_once(self):
        def handler(kind, method, url, kw):
            return _resp(401, message="刷新令牌已被使用，请重新登录")

        c = self._client(handler, access_ttl=60)
        with self.assertRaises(api.ApiError):
            c.list_notes()
        self.assertTrue(c.auth_invalid)
        self.assertEqual(c.access_token, "")
        self.assertEqual(cfgmod.load_config()["refresh_token"], "")
        self.assertEqual(len(self.notified), 1)
        # 后续请求不再反复打刷新接口、也不重复提示
        with self.assertRaises(api.ApiError):
            c.list_notes()
        self.assertEqual(c.session.count("post"), 2)      # 只有首轮的重试
        self.assertEqual(len(self.notified), 1)

    def test_login_401_is_not_session_expiry(self):
        def handler(kind, method, url, kw):
            return _resp(401, message="用户名或密码错误")

        c = self._client(handler)
        with self.assertRaises(api.ApiError) as ctx:
            c.login("u", "bad", "cid", "1234")
        self.assertEqual(ctx.exception.status, 401)
        self.assertEqual(c.session.count("post"), 0)
        self.assertFalse(self.notified)
        self.assertTrue(c.access_token)                    # 登录态没被误清

    # ---- 异常与并发 ----
    def test_network_error_keeps_login_state(self):
        def handler(kind, method, url, kw):
            if kind == "post":
                raise requests.ConnectionError("service down")
            return _resp(200, {"data": {"items": [], "total": 0}})

        c = self._client(handler, access_ttl=60)
        c.list_notes()
        self.assertTrue(c.access_token)
        self.assertTrue(c.refresh_token)
        self.assertFalse(c.auth_invalid)
        self.assertFalse(self.notified)

    def test_server_5xx_does_not_log_out(self):
        def handler(kind, method, url, kw):
            if kind == "post":
                return _resp(500, message="internal error")
            return _resp(200, {"data": {"items": [], "total": 0}})

        c = self._client(handler, access_ttl=60)
        c.list_notes()
        self.assertTrue(c.refresh_token)
        self.assertFalse(c.auth_invalid)

    def test_concurrent_401_only_one_refresh(self):
        barrier = threading.Barrier(6)

        def handler(kind, method, url, kw):
            if kind == "post":
                return self._refresh_ok(kind, method, url, kw)
            token = kw.get("headers", {}).get("Authorization") or ""
            if self.new_pair[0] in token:
                return _resp(200, {"data": {"items": [], "total": 0}})
            return _resp(401, message="令牌无效或已过期")

        c = self._client(handler, access_ttl=3600)
        errors = []

        def worker():
            try:
                barrier.wait()
                c.list_notes()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(c.session.count("post"), 1, "并发 401 只能消费一次 refresh")

    def test_peer_process_tokens_are_adopted(self):
        """另一个客户端实例已续期并写盘：本实例直接采用，不再消费 refresh。"""
        c = self._client(self._refresh_ok, access_ttl=60)
        cfgmod.save_config({
            "base_url": "http://127.0.0.1:18000",
            "access_token": self._jwt("access", 3600),
            "refresh_token": "peer-refresh",
            "username": "tester",
        })
        self.assertTrue(c.list_notes() == ([], 0))
        self.assertEqual(c.session.count("post"), 0)
        self.assertEqual(c.refresh_token, "peer-refresh")

    def test_ensure_session_recovers_when_access_missing(self):
        c = self._client(self._refresh_ok)
        c.clear_tokens()
        c.refresh_token = "refresh-1"      # 模拟只丢了 access 的旧配置
        self.assertTrue(c.ensure_session())
        self.assertTrue(c.access_token)
        self.assertEqual(c.session.count("post"), 1)

    def test_jwt_exp_parser(self):
        self.assertAlmostEqual(api._jwt_exp(self._jwt("access", 120)),
                               time.time() + 120, delta=2)
        self.assertEqual(api._jwt_exp("not-a-jwt"), 0)


if __name__ == "__main__":
    unittest.main()
