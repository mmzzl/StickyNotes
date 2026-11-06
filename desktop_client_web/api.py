# api.py —— 服务端 HTTP 封装（requests 同步，贴 PyQt / pywebview 槽函数）
"""令牌生命周期说明（别再退化回「401 就弹登录」）：

后端 access token 只有 30 分钟，refresh token 7 天且**一次性轮换**（jti 消费）。
以前客户端只把 refresh_token 存进 config.json，却从不调用 /auth/refresh，
于是隔半小时/隔夜再打开就必然 401，只能重新登录。

现在两条路径保证「持续使用就一直在线」：
1. 每次请求前看一眼 access 的 exp，剩余不足 5 分钟就静默续期；
2. 万一仍收到 401（服务端刚过期、机器休眠跨时段等），自动续期并**重放原请求**，
   对上层完全透明。只有 refresh 本身被明确拒绝（过期/重放/改密/登出）
   才清空登录态、回到登录窗口。

多窗口、多客户端实例共用同一份 config.json，而 refresh 是一次性的：
- _auth_lock 让同一进程内并发 401 只发一次刷新；
- 刷新前后重读磁盘配置，别人刚换好的令牌直接采用，避免误判「令牌已失效」；
- 401 后短暂等待再重读一次，给另一个进程留出落盘时间。
"""
import base64
import json
import threading
import time

import requests

from config import DEFAULT_BASE_URL, load_config, save_config
from sticky_log import get_logger

log = get_logger("api")

API_PREFIX = "/api/v1"
HTTP_TIMEOUT = 10
# access 剩余寿命低于此值就提前续期，避免「用着用着突然 401」
REFRESH_AHEAD_SECONDS = 5 * 60
# 另一个客户端实例可能刚轮换完令牌，给它一点落盘时间再重试
_PEER_RACE_WAIT = 0.35


def _jwt_exp(token: str) -> float:
    """读 JWT payload 的 exp，仅用于决定何时提前续期；验签由服务端负责。

    解析不了（session 模式、旧格式）返回 0，调用方会跳过预判、交给 401 兜底。
    """
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return float(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0)
    except (IndexError, ValueError, TypeError, KeyError, AttributeError):
        return 0.0


def _resp_message(resp) -> str:
    try:
        body = resp.json()
        return body.get("message") or body.get("detail") or resp.text
    except ValueError:
        return resp.text


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
        self.on_auth_failed = None                 # 会话真正失效时回调，由 app 挂载
        self.access_token = cfg.get("access_token", "")
        self.refresh_token = cfg.get("refresh_token", "")
        self.username = cfg.get("username", "")
        # refresh 被明确拒绝后置位，防止每个窗口都反复打刷新接口
        self.auth_invalid = False
        self._auth_lock = threading.RLock()
        self._notified = False
        self._renewed_at = 0.0

    def set_base_url(self, url):
        """切换服务器地址：换新会话（验证码 cookie 独立）并持久化。"""
        url = (url or DEFAULT_BASE_URL).strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        self.base_url = url
        self.session = requests.Session()
        self.clear_tokens()
        self.username = ""
        cfg = load_config()
        cfg.update(base_url=url, username="")
        save_config(cfg)

    def clear_tokens(self):
        """只清令牌，保留服务器地址与上次用户名，登录页体验更顺。"""
        self.access_token = ""
        self.refresh_token = ""
        cfg = load_config()
        cfg.update(access_token="", refresh_token="")
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
        raise ApiError(f"HTTP {resp.status_code}: {_resp_message(resp)}", resp.status_code)

    def _request(self, method, path, json=None, params=None):
        # 登录/注册/验证码这类接口自身的 401 是「账号或验证码错误」，
        # 不能当成会话过期去续期，否则输错密码也会被提示重新登录。
        auth_path = path.startswith("/auth/")
        if not auth_path:
            self._ensure_fresh_access()
        # GET 不带请求体，避免部分代理对 GET+body 的兼容问题
        payload = None if method == "GET" else (json or {})

        def send():
            return self.session.request(method, self._url(path),
                                        json=payload, params=params,
                                        headers=self._headers(),
                                        timeout=HTTP_TIMEOUT, verify=False)

        sent_token = self.access_token
        resp = send()
        if (resp.status_code == 401 and not auth_path
                and self.refresh_token and not self.auth_invalid):
            if self._renew(stale=sent_token):
                resp = send()          # 用新令牌重放，上层完全无感
        if resp.status_code >= 400:
            if resp.status_code == 401 and not auth_path:
                self._handle_auth_failed()
            self._raise(resp)
        return resp.json().get("data")

    # ---- 自动续期 ----
    def ensure_session(self) -> bool:
        """启动自检：有 refresh 就保证拿到可用 access（必要时静默续期）。

        不抛异常；返回是否仍持有可用登录态。服务暂时不可达时会保留令牌，
        不会被误踢回登录框。
        """
        if not (self.access_token or self.refresh_token):
            return False
        self._ensure_fresh_access()
        return bool(self.access_token) and not self.auth_invalid

    @property
    def renewed_this_session(self) -> bool:
        return self._renewed_at > 0

    def _ensure_fresh_access(self):
        if self.auth_invalid or not self.refresh_token:
            return
        exp = _jwt_exp(self.access_token) if self.access_token else 0
        if self.access_token and not exp:
            return                      # 非 JWT：交给 401 兜底
        if exp and exp - time.time() >= REFRESH_AHEAD_SECONDS:
            return                      # 还新，不用打扰服务端
        self._renew()

    def _adopt_peer_tokens(self, before: str):
        """重读磁盘配置，尽量采用其他客户端刚换好的令牌。

        返回 None 表示已采用现成令牌、无需再刷新；返回字符串表示要用它去刷新；
        返回空串表示没有任何可用 refresh。
        """
        disk = load_config()
        disk_access = disk.get("access_token") or ""
        disk_refresh = disk.get("refresh_token") or ""
        fresh = (disk_access and disk_access != before
                 and _jwt_exp(disk_access) - time.time() > REFRESH_AHEAD_SECONDS)
        if disk_refresh and disk_refresh != self.refresh_token:
            self.refresh_token = disk_refresh
            if fresh:
                self.access_token = disk_access
                self.username = disk.get("username") or self.username
                log.info("已采用其他客户端续期后的令牌")
                return None
        elif fresh:
            self.access_token = disk_access
            log.info("已采用其他客户端续期后的 access 令牌")
            return None
        return self.refresh_token or ""

    def _renew(self, stale: str | None = None) -> bool:
        """用一次性 refresh 换新令牌；成功返回 True，需要重新登录返回 False。

        stale 是触发本次续期的那个请求所用的 access。多个窗口会同时 401，
        先拿到锁的线程换好令牌后，其余线程凭 stale 直接判定「已被同行处理」，
        避免把刚签发的一次性 refresh 又消费掉（那会让别人误判会话失效）。
        """
        with self._auth_lock:
            if self.auth_invalid:
                return False
            if stale and self.access_token and self.access_token != stale:
                return True
            before = self.access_token
            for attempt in range(2):
                token = self._adopt_peer_tokens(before)
                if token is None:
                    return True
                if not token:
                    return False
                try:
                    resp = self.session.post(
                        self._url("/auth/refresh"),
                        json={"refresh_token": token},
                        headers={"Content-Type": "application/json"},
                        timeout=HTTP_TIMEOUT, verify=False)
                except requests.RequestException as e:
                    # 断网/服务没起来不是「登录失效」，保留令牌下次自动恢复
                    log.warning("自动续期失败（服务暂不可用），保留登录态：%s", e)
                    return False
                if resp.status_code < 400:
                    data = {}
                    try:
                        data = resp.json().get("data") or {}
                    except ValueError:
                        pass
                    if not data.get("access_token"):
                        log.warning("自动续期响应缺少 access_token")
                        return False
                    self._save_tokens(data, self.username)
                    self.auth_invalid = False
                    self._notified = False
                    self._renewed_at = time.time()
                    log.info("登录已自动续期，无需重新登录")
                    return True
                if resp.status_code != 401:
                    log.warning("自动续期被拒 HTTP %s：%s（保留登录态）",
                                resp.status_code, _resp_message(resp))
                    return False
                # 401 也可能是另一个客户端刚消费了同一份 refresh：等它落盘再重读
                if attempt == 0:
                    time.sleep(_PEER_RACE_WAIT)
                    before = self.access_token
                    continue
                log.warning("刷新令牌已失效，需要重新登录：%s", _resp_message(resp))
                # 统一走失效处理：清空落盘令牌 + 只提示一次，下次启动直接进登录框
                self._handle_auth_failed()
                return False
            return False

    def _handle_auth_failed(self):
        """真·会话失效：清空令牌并只提示一次（多个窗口会同时 401）。"""
        with self._auth_lock:
            if self._notified:
                return
            self._notified = True
            self.auth_invalid = True
        self.clear_tokens()
        log.warning("登录已失效，下次启动客户端会显示登录窗口")
        cb = self.on_auth_failed
        if cb:
            try:
                cb()
            except Exception:  # noqa: BLE001
                log.exception("会话失效回调失败")

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
        self.auth_invalid = False
        self._notified = False
        cfg = load_config()
        cfg.update(base_url=self.base_url, access_token=self.access_token,
                   refresh_token=self.refresh_token, username=username)
        save_config(cfg)

    # ---- 便签 ----
    def list_notes(self, q="", page=1, page_size=200):
        """分页+关键字搜索列表；返回 (items, total)。"""
        data = self._request("GET", "/notes", params={
            "q": q, "page": page, "page_size": page_size}) or {}
        items = sorted(data.get("items") or [],
                       key=lambda n: n.get("updated_at") or "", reverse=True)
        return items, data.get("total") or 0

    def create_note(self, title="", content="", color="#fff9c4", x=0, y=0):
        return self._request("POST", "/notes", {
            "title": title, "content": content, "color": color, "pos_x": int(x), "pos_y": int(y)})

    def update_note(self, note_id, **fields):
        return self._request("PUT", f"/notes/{note_id}", fields)

    def delete_note(self, note_id):
        return self._request("DELETE", f"/notes/{note_id}")

    # ---- 办公：整机备份 / 迁移 ----
    def export_notes(self):
        """导出本人全部便签（服务端返回 {format, version, count, notes}）。"""
        return self._request("GET", "/notes/export")

    def import_notes(self, notes):
        """追加导入备份条目，返回 {imported, skipped, limit}。"""
        return self._request("POST", "/notes/import", {"notes": notes})
