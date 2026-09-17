# api.py —— 服务端 HTTP 封装（requests 同步，贴 PyQt 槽函数）
import base64

import requests

from config import DEFAULT_BASE_URL, load_config, save_config

API_PREFIX = "/api/v1"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


class Client:
    def __init__(self, base_url=None):
        cfg = load_config()
        self.base_url = (base_url or cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()          # 携带 anon_sid cookie 走验证码登录
        self.access_token = cfg.get("access_token", "")
        self.refresh_token = cfg.get("refresh_token", "")
        self.username = cfg.get("username", "")

    def set_base_url(self, url):
        """切换服务器地址：换新会话（验证码 cookie 独立）并持久化。"""
        url = (url or DEFAULT_BASE_URL).strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        self.base_url = url
        self.session = requests.Session()
        self.access_token = ""
        self.refresh_token = ""
        self.username = ""
        cfg = load_config()
        cfg.update(base_url=url, access_token="", refresh_token="", username="")
        save_config(cfg)

    # ---- 基础请求 ----
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _url(self, path):
        return f"{self.base_url}{API_PREFIX}{path}"

    def _raise(self, resp):
        try:
            msg = resp.json().get("message") or resp.json().get("detail") or resp.text
        except ValueError:
            msg = resp.text
        raise ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)

    def _request(self, method, path, json=None):
        resp = self.session.request(method, self._url(path),
                                    json=json or {}, headers=self._headers(), timeout=10, verify=False)
        if resp.status_code >= 400:
            self._raise(resp)
        return resp.json().get("data")

    # ---- 认证 ----
    def fetch_captcha(self):
        resp = self.session.get(self._url("/auth/captcha"))
        if resp.status_code != 200:
            self._raise(resp)
        data = resp.json()["data"]
        return data["captcha_id"], base64.b64decode(data["image_base64"])

    def login(self, username, password, captcha_id, captcha_code):
        data = self._request("POST", "/auth/login", {
            "username": username, "password": password,
            "captcha_id": captcha_id, "captcha_code": captcha_code,
        })
        self._save_tokens(data, username)
        return data

    def register(self, username, password, display_name=""):
        data = self._request("POST", "/auth/register", {
            "username": username, "password": password, "display_name": display_name,
        })
        self._save_tokens(data, username)
        return data

    def _save_tokens(self, data, username):
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", "")
        self.username = username
        cfg = load_config()
        cfg.update(base_url=self.base_url, access_token=self.access_token,
                   refresh_token=self.refresh_token, username=username)
        save_config(cfg)

    # ---- 便签 ----
    def list_notes(self):
        items = self._request("GET", "/notes") or []
        return sorted(items, key=lambda n: n.get("updated_at") or "", reverse=True)

    def create_note(self, title="", content="", color="#fff9c4", x=0, y=0):
        return self._request("POST", "/notes", {
            "title": title, "content": content, "color": color, "pos_x": int(x), "pos_y": int(y)})

    def update_note(self, note_id, **fields):
        return self._request("PUT", f"/notes/{note_id}", fields)

    def delete_note(self, note_id):
        return self._request("DELETE", f"/notes/{note_id}")
