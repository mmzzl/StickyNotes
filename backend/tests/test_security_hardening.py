"""安全加固回归：令牌吊销、refresh 一次性、接口限流、事务原子性、密钥分离。

这些用例对应架构评审里被判定为「设计缺陷」的几项，改动后必须能自动验证。
"""
import pytest

from config import _DEFAULT_JWT_SECRET, settings
from core import rate_limit, security
from core.uow import unit_of_work
from tests.helpers import login_with_captcha


# ---------------------------------------------------------------
# JWT 吊销
# ---------------------------------------------------------------

async def test_logout_revokes_access_token(client, admin_headers):
    """登出后旧 access token 必须立即失效，而不是等 30 分钟自然过期。"""
    assert (await client.get("/api/v1/auth/me", headers=admin_headers)).status_code == 200
    out = await client.post("/api/v1/auth/logout", headers=admin_headers)
    assert out.status_code == 200, out.text
    after = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert after.status_code == 401, after.text


async def test_password_change_revokes_old_tokens(client):
    """改密后旧令牌失效（自助改密走统一密码策略入口）。"""
    await client.post("/api/v1/auth/register", json={
        "username": "revokee", "password": "Startpw1",
    })
    r = await login_with_captcha(client, "revokee", "Startpw1")
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}

    ch = await client.post("/api/v1/auth/change-password", json={
        "old_password": "Startpw1", "new_password": "Fresh1234",
    }, headers=h)
    assert ch.status_code == 200, ch.text
    assert (await client.get("/api/v1/auth/me", headers=h)).status_code == 401


async def test_refresh_token_is_single_use(client, admin_headers):
    """refresh 轮换后旧 refresh 不能再换一对新令牌（防重放）。"""
    r = await login_with_captcha(client, "admin", "admin123")
    old_refresh = r.json()["data"]["refresh_token"]

    first = await client.post("/api/v1/auth/refresh",
                              json={"refresh_token": old_refresh})
    assert first.status_code == 200, first.text
    assert first.json()["data"]["refresh_token"] != old_refresh

    replay = await client.post("/api/v1/auth/refresh",
                               json={"refresh_token": old_refresh})
    assert replay.status_code == 401, replay.text


# ---------------------------------------------------------------
# 限流
# ---------------------------------------------------------------

async def test_rate_limit_blocks_captcha_flood(client, monkeypatch):
    """匿名验证码接口被连续刷时返回 429。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_minute", 3)
    rate_limit.reset()
    try:
        codes = [(await client.get("/api/v1/auth/captcha")).status_code
                 for _ in range(5)]
        assert 429 in codes, codes
    finally:
        rate_limit.reset()


async def test_register_respects_disabled_switch(client, monkeypatch):
    """REGISTER_ENABLED=false 时自助注册被关闭。"""
    monkeypatch.setattr(settings, "register_enabled", False)
    r = await client.post("/api/v1/auth/register", json={
        "username": "nobody", "password": "Abc12345",
    })
    assert r.status_code == 400 and "关闭" in r.json()["message"]


# ---------------------------------------------------------------
# 事务原子性
# ---------------------------------------------------------------

async def test_uow_rolls_back_all_writes():
    """UoW 内任一步抛异常，之前已 flush 的写也不能落库。"""
    from repositories import users

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with unit_of_work():
            await users.create({
                "username": "atomic_probe",
                "password_hash": "not-a-real-hash",
                "display_name": "探针",
            })
            raise Boom()

    assert await users.get_by_username("atomic_probe") is None


# ---------------------------------------------------------------
# 密钥管理
# ---------------------------------------------------------------

def test_encryption_survives_jwt_secret_rotation(monkeypatch):
    """独立配置数据加密密钥后，轮换 JWT_SECRET 不影响已加密的渠道密钥。"""
    monkeypatch.setattr(settings, "data_encryption_key", "K" * 40)
    cipher = security.encrypt_secret("smtp-password")
    monkeypatch.setattr(settings, "jwt_secret", "B" * 40)
    assert security.decrypt_secret(cipher) == "smtp-password"


def test_legacy_ciphertext_still_readable(monkeypatch):
    """从旧版（无独立数据密钥）升级后，历史密文仍可解密。"""
    monkeypatch.setattr(settings, "data_encryption_key", "")
    monkeypatch.setattr(settings, "jwt_secret", "L" * 40)
    cipher = security.encrypt_secret("legacy-secret")
    monkeypatch.setattr(settings, "data_encryption_key", "K" * 40)
    assert security.decrypt_secret(cipher) == "legacy-secret"


def test_weak_secret_detection(monkeypatch):
    """生产环境密钥检查能识别默认值，配置齐全后放行。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", "A" * 40)
    monkeypatch.setattr(settings, "data_encryption_key", "K" * 40)
    assert settings.weak_secrets() == []
    assert settings.is_production() is True

    monkeypatch.setattr(settings, "jwt_secret", _DEFAULT_JWT_SECRET)
    assert "JWT_SECRET" in settings.weak_secrets()
