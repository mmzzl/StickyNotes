"""自助注册接口测试：注册即登录 + 普通管理员角色（note:list + 后台菜单可用）+ 重名/弱密码拒绝。"""
from tests.helpers import login_with_captcha


async def test_register_ok_and_autologin(client):
    """注册成功即登录：返回令牌；已分配「普通管理员」角色（note:list + sys:user:list 可用）；真实可登录。"""
    from repositories import roles

    r = await client.post("/api/v1/auth/register", json={
        "username": "alice", "password": "Abc12345", "display_name": "爱丽丝",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data.get("access_token") and data.get("refresh_token")

    # 注册即分配「普通管理员」角色：具备便签权限，且浏览器后台可见系统菜单。
    # 便签 HTTP API 与注册任务并行开发（本任务上游仅 T4），此处以 /auth/me
    # 的权限/角色码断言同义语义，避免耦合未落地的 note API 路由。
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert "note:list" in me.json()["data"]["permissions"]
    assert "sys:user:list" in me.json()["data"]["permissions"]  # 普通管理员可见用户管理
    assert "admin" in me.json()["data"]["roles"]

    assert await roles._find_one({"code": "user"})

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
