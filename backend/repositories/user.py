"""用户 Repository：通用 CRUD + 用户名查询 + 角色关系维护。"""

from core.uow import unit_of_work
from db.sql.user import User
from repositories._choose import pick

RepoBase = pick()


class UserRepo(RepoBase):
    model = User
    table = "users"

    async def get_by_username(self, username: str) -> dict | None:
        return await self._find_one({"username": username})

    async def role_ids(self, user_id: str) -> list[str]:
        rows = await self._list_from("user_roles", {"user_id": user_id})
        return [r["role_id"] for r in rows]

    async def set_roles(self, user_id: str, role_ids: list[str]) -> None:
        """先删后插必须原子：中断时不能把用户的角色清空。"""
        async with unit_of_work():
            await self._delete_from("user_roles", {"user_id": user_id})
            if role_ids:
                await self._insert_from(
                    "user_roles", [{"user_id": user_id, "role_id": rid} for rid in role_ids]
                )
