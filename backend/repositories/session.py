"""Session 会话 Repository（仅 AUTH_MODE=session 使用）。"""

from datetime import datetime, timezone

from db.sql.rbac import Session
from repositories._choose import pick

RepoBase = pick()


def _as_utc(dt) -> datetime:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class SessionRepo(RepoBase):
    model = Session
    table = "sessions"

    async def get_by_token(self, token: str) -> dict | None:
        return await self._find_one({"token": token})

    async def consume_token(self, token: str) -> bool:
        """原子消费一次性令牌：删除命中则返回 True，重放时返回 False。

        用于 refresh token 轮换（jti 落在 sessions 表），避免「读到再删」的竞态。
        """
        return (await self._delete_from("sessions", {"token": token})) > 0

    async def delete_by_user(self, user_id: str) -> None:
        await self._delete_from("sessions", {"user_id": user_id})

    async def cleanup_expired(self) -> None:
        """删除过期会话（跨 DB 通用：取出全部再逐条删）。"""
        rows = await self._find_all()
        now = datetime.now(timezone.utc)
        expired = [
            r["id"]
            for r in rows
            if r.get("expires_at") is not None and _as_utc(r["expires_at"]) < now
        ]
        for sid in expired:
            await self.delete(sid)
