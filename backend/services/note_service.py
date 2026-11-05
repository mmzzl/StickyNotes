"""便签业务：所有查询/写入都以当前用户 owner_id 为边界。"""

from __future__ import annotations

from datetime import datetime, timezone

from core.exceptions import NotFoundError
from repositories import notes
from schemas.note import NoteCreate

# 备份文件只含业务字段，绝不外泄 id / owner_id / 时间戳
PORTABLE_FIELDS = ("title", "content", "color", "pos_x", "pos_y")
MAX_IMPORT_ITEMS = 1000


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


async def export_notes(owner_id: str) -> dict:
    """导出本人全部便签为可移植 JSON 备份。

    分页取全，不受列表接口单页 500 条上限影响；不含任何标识/归属信息。
    """
    all_rows: list[dict] = []
    page = 1
    while True:
        rows, total = await notes.list(filters={"owner_id": owner_id}, page=page, size=500)
        all_rows.extend(rows)
        if not rows or len(all_rows) >= total:
            break
        page += 1

    items = [
        {k: row.get(k) for k in PORTABLE_FIELDS}
        for row in sorted(all_rows, key=lambda n: n.get("created_at") or "")
    ]
    return {
        "format": "sticky-notes-backup",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "notes": items,
    }


async def import_notes(owner_id: str, items: list[dict]) -> dict:
    """从备份追加导入（不覆盖现有便签）。

    逐条经 NoteCreate 做字段白名单与校验：坏数据跳过而非整批失败；
    owner_id 一律强制为当前用户，防止越权写入他人名下。
    """
    imported = 0
    skipped = 0
    for raw in items[:MAX_IMPORT_ITEMS]:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        try:
            clean = NoteCreate(**{k: raw[k] for k in PORTABLE_FIELDS if k in raw}).model_dump()
        except Exception:
            skipped += 1
            continue
        await notes.create({**clean, "owner_id": owner_id})
        imported += 1

    if len(items) > MAX_IMPORT_ITEMS:
        skipped += len(items) - MAX_IMPORT_ITEMS
    return {"imported": imported, "skipped": skipped, "limit": MAX_IMPORT_ITEMS}
