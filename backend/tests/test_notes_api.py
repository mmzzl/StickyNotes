"""便签 API 集成测试：鉴权 / CRUD / owner 隔离 / 坐标回环。"""
from tests.helpers import login_with_captcha

USER_PW = "Abc12345"


async def _make_user(client, username):
    """直接用 repo 建普通用户（绑定 user 角色），返回该用户登录 token。"""
    from repositories import roles, users

    from datetime import datetime, timezone

    from core import security

    user = await users.create({
        "username": username,
        "password_hash": security.hash_password(USER_PW),
        "display_name": username,
        "email": f"{username}@local",
        "is_active": True,
        "is_superuser": False,
        "password_changed_at": datetime.now(timezone.utc),
    })
    rid = (await roles.get_by_code("user"))["id"]
    await users.set_roles(user["id"], [rid])

    resp = await login_with_captcha(client, username, USER_PW)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _auth(client, token):
    return {"Authorization": f"Bearer {token}"}


async def test_notes_require_auth(client):
    r = await client.get("/api/v1/notes")
    assert r.status_code == 401


async def test_notes_crud(client):
    token = await _make_user(client, "alice_api")
    h = await _auth(client, token)

    r1 = await client.post("/api/v1/notes", headers=h, json={"title": "a", "content": "x"})
    assert r1.status_code == 200
    n1 = r1.json()["data"]
    r2 = await client.post("/api/v1/notes", headers=h, json={"title": "b"})
    assert r2.status_code == 200

    lst = (await client.get("/api/v1/notes", headers=h)).json()["data"]
    assert len(lst) == 2
    assert all(n["owner_id"] == n1["owner_id"] for n in lst)
    assert all(n["color"] == "#fff9c4" for n in lst)

    up = await client.put(f"/api/v1/notes/{n1['id']}", headers=h,
                          json={"title": "a2", "color": "#ffd54f", "pos_x": 88})
    assert up.status_code == 200
    assert up.json()["data"]["title"] == "a2"
    assert up.json()["data"]["color"] == "#ffd54f"
    assert up.json()["data"]["pos_x"] == 88

    dl = await client.delete(f"/api/v1/notes/{n1['id']}", headers=h)
    assert dl.status_code == 200
    assert len((await client.get("/api/v1/notes", headers=h)).json()["data"]) == 1


async def test_notes_owner_isolated(client):
    tok_a = await _make_user(client, "alice_iso")
    tok_b = await _make_user(client, "bob_api")
    ha, hb = await _auth(client, tok_a), await _auth(client, tok_b)

    nid = (await client.post("/api/v1/notes", headers=ha, json={"title": "secret"})
           ).json()["data"]["id"]

    assert len((await client.get("/api/v1/notes", headers=hb)).json()["data"]) == 0

    assert (await client.put(f"/api/v1/notes/{nid}", headers=hb, json={"title": "hack"})) \
        .status_code == 404
    assert (await client.delete(f"/api/v1/notes/{nid}", headers=hb)).status_code == 404


async def test_notes_coords_roundtrip(client):
    token = await _make_user(client, "carol_api")
    h = await _auth(client, token)
    r = await client.post("/api/v1/notes", headers=h, json={"pos_x": -50, "pos_y": 1200})
    assert r.status_code == 200
    n = r.json()["data"]
    assert n["pos_x"] == -50 and n["pos_y"] == 1200
