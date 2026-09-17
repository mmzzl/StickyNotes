"""便签 DB 模型 / Mongo 索引测试。"""
from pathlib import Path


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
