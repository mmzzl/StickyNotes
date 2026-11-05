"""角色 Repository：通用 CRUD + code 查询 + 权限关系维护。"""

from config import settings
from core.uow import unit_of_work
from db.sql.rbac import Role
from repositories._choose import pick

RepoBase = pick()


class RoleRepo(RepoBase):
    model = Role
    table = "roles"

    async def get_by_code(self, code: str) -> dict | None:
        return await self._find_one({"code": code})

    async def permission_ids(self, role_id: str) -> list[str]:
        rows = await self._list_from("role_permissions", {"role_id": role_id})
        return [r["permission_id"] for r in rows]

    async def set_permissions(self, role_id: str, permission_ids: list[str]) -> None:
        """幂等绑定：目标集合与现状一致时跳过 DELETE + INSERT 两次写库。

        STARTUP_LEGACY=true 时保留旧行为（无条件重写），用于性能对照。
        """
        current = set(await self.permission_ids(role_id))
        target = set(permission_ids)
        if not settings.startup_legacy and current == target:
            return
        async with unit_of_work():
            await self._delete_from("role_permissions", {"role_id": role_id})
            if permission_ids:
                await self._insert_from(
                    "role_permissions",
                    [{"role_id": role_id, "permission_id": pid} for pid in permission_ids],
                )
