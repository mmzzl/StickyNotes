"""便签 Pydantic schema。"""

from pydantic import BaseModel, Field

_COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


class NoteCreate(BaseModel):
    title: str = Field("", max_length=256)
    content: str = ""
    color: str = Field("#fff9c4", pattern=_COLOR_PATTERN)
    pos_x: int = 0
    pos_y: int = 0


class NoteUpdate(BaseModel):
    title: str | None = Field(None, max_length=256)
    content: str | None = None
    color: str | None = Field(None, pattern=_COLOR_PATTERN)
    pos_x: int | None = None
    pos_y: int | None = None


class NoteWrite(NoteCreate):
    """仓库写白名单：owner_id 由服务注入，客户端不可传。"""
    owner_id: str
