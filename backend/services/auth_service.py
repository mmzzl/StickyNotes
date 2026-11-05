"""认证服务：登录、刷新、登出、当前用户，兼容 JWT/Session 双模式。

对外只暴露行为，不暴露令牌机制；具体牌子由 config.auth_mode 决定。

安全约定（jwt 模式）：
- 每个 access/refresh 都带 token_version(ver)，用户登出/改密后 version+1，
  此前签发的所有令牌立即失效（不再依赖客户端「自己丢掉」）。
- refresh 额外带 jti 并落库，轮换时原子消费，旧 refresh 无法重放。
- 多步写统一包在 unit_of_work() 里，避免留下半状态账号或悬空刷新记录。
"""

from datetime import datetime, timedelta, timezone

import jwt

from config import settings
from core import security
from core.exceptions import AuthError, BizError, NotFoundError
from core.logger import log
from core.uow import unit_of_work
from repositories import rbac, roles, sessions, users

_REFRESH_PREFIX = "refresh:"


def _clean_user_payload(user: dict) -> dict:
    return {k: v for k, v in user.items() if k != "password_hash"}


def _refresh_key(jti: str) -> str:
    return _REFRESH_PREFIX + jti


async def authenticate(username: str, password: str) -> dict:
    user = await users.get_by_username(username)
    if not user:
        raise AuthError("用户名或密码错误")
    if not user.get("is_active", True):
        raise AuthError("账号已被禁用")
    if not security.verify_password(password, user.get("password_hash", "")):
        raise AuthError("用户名或密码错误")
    return user


async def issue_tokens(user: dict) -> dict:
    """签发令牌。jwt 模式返回 access+refresh；session 模式返回 session_id。"""
    uid = user["id"]
    version = int(user.get("token_version") or 0)
    if settings.auth_mode == "session":
        token = security.create_session_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_expire_hours)
        await sessions.create(
            {"user_id": uid, "token": token, "expires_at": expires_at}
        )
        return {"session_id": token, "token_type": "session"}

    access = security.create_access_token(uid, token_version=version)
    refresh = security.create_refresh_token(uid, token_version=version)
    # 一次性 refresh：把 jti 落到 sessions 表，refresh 时按 token 原子删除
    jti = security.decode_token(refresh).get("jti") or ""
    await sessions.create({
        "user_id": uid,
        "token": _refresh_key(jti),
        "expires_at": datetime.now(timezone.utc)
        + timedelta(days=settings.jwt_refresh_expire_days),
    })
    return {"access_token": access, "token_type": "bearer", "refresh_token": refresh}


async def login(username: str, password: str) -> dict:
    user = await authenticate(username, password)
    tokens = await issue_tokens(user)
    from services import password_policy

    tokens.update(password_policy.password_status_of(user))
    log.info("用户登录成功: {}", username)
    return tokens


async def register(data) -> dict:
    """开放注册：默认只授予最小权限角色（user），全部写入落在一个事务里。"""
    from pymongo.errors import DuplicateKeyError
    from sqlalchemy.exc import IntegrityError

    from services import password_policy

    username = data.username

    if await users.get_by_username(username):
        raise BizError(message="用户名已存在")

    password_policy.validate_password_strength(data.password)

    async with unit_of_work():
        try:
            user = await users.create({
                "username": username,
                "password_hash": security.hash_password(data.password),
                "display_name": data.display_name or username,
                "email": data.email or "",
                "is_active": True,
                "is_superuser": False,
                "token_version": 0,
                "password_changed_at": datetime.now(timezone.utc),
            })
        except (IntegrityError, DuplicateKeyError):
            # 并发同名注册兜底：唯一约束冲突同样按已存在处理
            raise BizError(message="用户名已存在")

        # 默认 user（仅 note:*）。要恢复旧行为需显式 REGISTER_DEFAULT_ROLE=admin，
        # 但那会让任何匿名注册者获得管理后台读权限，生产禁用。
        role_code = settings.register_default_role
        role = await roles.get_by_code(role_code) if role_code else None
        if role:
            await users.set_roles(user["id"], [role["id"]])
        else:
            log.warning("默认角色 {} 不存在，新用户 {} 不授予任何角色",
                        role_code, username)

        await password_policy.note_initial_password(user["id"], user["password_hash"])
        tokens = await issue_tokens(user)

    tokens["user"] = _clean_user_payload(user)
    return tokens


async def refresh_login(refresh_token: str) -> dict:
    if settings.auth_mode == "session":
        raise AuthError("session 模式不支持刷新令牌")
    try:
        payload = security.decode_token(refresh_token, expected_type="refresh")
    except jwt.PyJWTError:
        raise AuthError("刷新令牌无效或已过期")

    uid = security._user_id_from_sub(payload["sub"])
    user = await users.get(uid)
    if not user or not user.get("is_active", True):
        raise AuthError("用户不存在或已禁用")
    if int(user.get("token_version") or 0) != int(payload.get("ver") or 0):
        raise AuthError("令牌已失效，请重新登录")

    jti = payload.get("jti") or ""
    async with unit_of_work():
        # 原子消费：并发用同一个 refresh 只有一个能成功，杜绝旧令牌重放
        if not jti or not await sessions.consume_token(_refresh_key(jti)):
            raise AuthError("刷新令牌已被使用，请重新登录")
        return await issue_tokens(user)


async def logout(user_id: str) -> None:
    """登出即吊销：JWT 模式靠 version+1 作废旧令牌，Session 模式删会话。"""
    user = await users.get(user_id)
    version = int((user or {}).get("token_version") or 0) + 1
    async with unit_of_work():
        if user:
            await users.update(user_id, {"token_version": version})
        await sessions.delete_by_user(user_id)
    log.info("登出：已吊销用户 {} 的全部令牌 ver={}", user_id, version)


async def change_own_password(user_id: str, old_password: str, new_password: str) -> None:
    """自助改密：须验证旧密码，走统一策略（强度+防重用+刷新改密时间+吊销令牌）。"""
    from services import password_policy

    user = await users.get(user_id)
    if not user:
        raise NotFoundError("用户不存在")
    if not security.verify_password(old_password, user.get("password_hash", "")):
        raise BizError("旧密码不正确")
    await password_policy.apply_password(user, new_password)


async def update_own_profile(user_id: str, data: dict) -> None:
    """自助修改个人资料（显示名/邮箱）。"""
    await users.update(user_id, data)


async def current_user(user_id: str) -> dict:
    """把持有令牌的用户解析成完整的用户信息（含角色、权限）。"""
    user = await users.get(user_id)
    if not user:
        raise NotFoundError("用户不存在")
    if not user.get("is_active", True):
        raise AuthError("账号已被禁用")
    role_ids = await users.role_ids(user_id)
    role_rows = await roles._find_in("id", role_ids)
    role_codes = [r["code"] for r in role_rows]
    perms = await rbac.permission_codes_of_user_flat(user_id)
    return {
        **_clean_user_payload(user),
        "roles": role_codes,
        "permissions": perms,
    }
