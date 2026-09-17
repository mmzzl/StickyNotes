"""便签业务：所有查询/写入都以当前用户 owner_id 为边界。"""

from core.exceptions import NotFoundError
from repositories import notes


async def list_notes(owner_id: str) -> list:
    """当前用户全部便签（按 updated_at 倒序）。"""
    rows = await notes._find_all({"owner_id": owner_id})
    return sorted(rows, key=lambda n: (n.get("updated_at") or ""), reverse=True)


async def create_note(owner_id: str, data: dict) -> dict:
    return await notes.create({**data, "owner_id": owner_id})


async def _get_own(note_id: str, owner_id: str) -> dict:
    """按 id+owner 命中，未命中/归属他人统一 404，避免泄露存在性。"""
    row = await notes.get(note_id)
    if not row or row["owner_id"] != owner_id:
        raise NotFoundError("便签不存在")
    return row


async def update_own_note(note_id: str, owner_id: str, patch: dict) -> dict:
    await _get_own(note_id, owner_id)
    return await notes.update(note_id, patch)


async def delete_own_note(note_id: str, owner_id: str) -> None:
    await _get_own(note_id, owner_id)
    await notes.delete(note_id)
