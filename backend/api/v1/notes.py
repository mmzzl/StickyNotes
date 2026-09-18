"""便签 CRUD 路由。"""

from fastapi import APIRouter, Depends, Query

from core.dependencies import CurrentUserDep, require_permission
from core.response import ok, paged
from schemas.note import NoteCreate, NoteUpdate
from services import note_service

router = APIRouter(prefix="/notes", tags=["便签"])


@router.get("", dependencies=[Depends(require_permission("note:list"))])
async def list_notes(user: CurrentUserDep,
                     q: str = Query("", description="标题/内容关键字"),
                     page: int = Query(1, ge=1),
                     page_size: int = Query(100, ge=1, le=500)):
    items, total = await note_service.list_notes(user.id, q=q, page=page, page_size=page_size)
    return ok(paged(items, total, page, page_size))


@router.post("", dependencies=[Depends(require_permission("note:create"))])
async def create_note(user: CurrentUserDep, body: NoteCreate):
    return ok(await note_service.create_note(user.id, body.model_dump()))


@router.put("/{note_id}", dependencies=[Depends(require_permission("note:update"))])
async def update_note(user: CurrentUserDep, note_id: str, body: NoteUpdate):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return ok(await note_service.update_own_note(note_id, user.id, patch))


@router.delete("/{note_id}", dependencies=[Depends(require_permission("note:delete"))])
async def delete_note(user: CurrentUserDep, note_id: str):
    await note_service.delete_own_note(note_id, user.id)
    return ok(message="删除成功")
