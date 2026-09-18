"""便签业务：所有查询/写入都以当前用户 owner_id 为边界。"""

from core.exceptions import NotFoundError
from repositories import notes


async def list_notes(owner_id: str, q: str = "", page: int = 1, page_size: int = 100):
    """本人便签分页列表（按 updated_at 倒序）；q 对标题/内容不区分大小写模糊匹配。"""
    keyword = q.strip() or None
    rows, total = await notes.list(
        filters={"owner_id": owner_id},
        keyword=keyword,
        keyword_fields=["title", "content"],
        page=max(1, page),
        size=max(1, min(page_size, 500)),
    )
    rows = sorted(rows, key=lambda n: (n.get("updated_at") or ""), reverse=True)
    return rows, total


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
