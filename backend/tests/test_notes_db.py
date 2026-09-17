"""便签 DB 模型 / Mongo 索引 / Pydantic schema 测试。"""
from pathlib import Path

import pytest as _pytest
from pydantic import ValidationError


def test_note_model_registered():
    from db.sql.base import Base
    from db.sql.note import Note  # noqa: F401  触发模型注册

    table = Base.metadata.tables["notes"]
    cols = set(table.columns.keys())
    assert {"id", "owner_id", "title", "content", "color",
            "pos_x", "pos_y", "created_at", "updated_at"} <= cols
    assert "ix_notes_owner_id" in {i.name for i in table.indexes}


def test_mongo_index_includes_notes():
    src = (Path(__file__).resolve().parents[1] / "db" / "mongo" / "indexes.py").read_text()
    assert '"notes")' in src
    assert 'create_index("owner_id")' in src


def test_note_create_defaults():
    from schemas.note import NoteCreate

    n = NoteCreate()
    assert n.title == ""
    assert n.content == ""
    assert n.color == "#fff9c4"
    assert n.pos_x == 0 and n.pos_y == 0


def test_note_create_color_pattern():
    from schemas.note import NoteCreate

    with _pytest.raises(ValidationError):
        NoteCreate(color="red")


async def test_note_repo_wired():
    from repositories import notes

    assert notes.table == "notes"
    fields = set(notes.schema.model_fields.keys())
    assert {"owner_id", "title", "content", "color", "pos_x", "pos_y"} <= fields


async def test_note_repo_crud():
    from repositories import notes

    created = await notes.create({"owner_id": "u1", "title": "t", "pos_x": 10, "pos_y": 20})
    assert created["id"]
    assert created["pos_x"] == 10

    one = await notes._find_one({"owner_id": "u1"})
    assert one and one["id"] == created["id"]

    assert (await notes.get(created["id"]))["title"] == "t"

    await notes.delete(created["id"])
    assert await notes.get(created["id"]) is None
