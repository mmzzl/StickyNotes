"""便签 seed 测试：user 角色、note 权限、幂等。"""
from db import seed

NOTE_CODES = {"note:list", "note:create", "note:update", "note:delete"}


async def _perm_codes(role_id):
    from repositories import permissions, roles

    pid_list = await roles.permission_ids(role_id)
    rows = await permissions._find_in("id", pid_list)
    return {r["code"] for r in rows}


async def test_role_user_seeded():
    from repositories import roles

    assert await roles._find_one({"code": "user"}) is not None


async def test_role_user_has_note_perms():
    from repositories import roles

    rid = (await roles.get_by_code("user"))["id"]
    codes = await _perm_codes(rid)
    assert codes == NOTE_CODES

    admin_rid = (await roles.get_by_code("admin"))["id"]
    admin_codes = await _perm_codes(admin_rid)
    assert NOTE_CODES <= admin_codes


async def test_seed_idempotent():
    from repositories import permissions, roles

    n_roles = len(await roles._find_all())
    n_perms = len(await permissions._find_all())

    await seed.seed_all()

    assert len(await roles._find_all()) == n_roles
    assert len(await permissions._find_all()) == n_perms
