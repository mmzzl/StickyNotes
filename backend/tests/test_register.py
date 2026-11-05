"""自助注册接口测试：注册即登录 + 仅授予最小权限角色 + 重名/弱密码拒绝。

安全回归：旧实现注册即绑定 admin 角色，匿名者一次调用即可读管理后台、
改通知 webhook。现在默认只给 user（note:*），这里把提权路径钉死。
"""
from tests.helpers import login_with_captcha


async def test_register_ok_and_autologin(client):
    """注册成功即登录；新用户只有便签权限，不得获得任何系统管理权限。"""
    r = await client.post("/api/v1/auth/register", json={
        "username": "alice", "password": "Abc12345", "display_name": "爱丽丝",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data.get("access_token") and data.get("refresh_token")

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    perms = me.json()["data"]["permissions"]
    assert "note:list" in perms
    assert "sys:user:list" not in perms
    assert "sys:notify:update" not in perms
    assert "user" in me.json()["data"]["roles"]
    assert "admin" not in me.json()["data"]["roles"]

    # 直接打管理后台接口：应被权限依赖挡下，而不是返回用户列表
    denied = await client.get("/api/v1/sys/users", headers=headers)
    assert denied.status_code == 403, denied.text

    again = await login_with_captcha(client, "alice", "Abc12345")
    assert again.status_code == 200, again.text


async def test_register_duplicate(client):
    """同名重复注册 → 400 且提示已存在。"""
    await client.post("/api/v1/auth/register", json={
        "username": "bob", "password": "Abc12345",
    })
    r2 = await client.post("/api/v1/auth/register", json={
        "username": "bob", "password": "Abc12345",
    })
    assert r2.status_code == 400
    assert "存在" in r2.json()["message"]


async def test_register_weak_pwd(client):
    """弱密码（缺大写/数字/长度）注册 → 400（密码策略提示）。"""
    r = await client.post("/api/v1/auth/register", json={
        "username": "carol", "password": "abc",
    })
    assert r.status_code == 400
