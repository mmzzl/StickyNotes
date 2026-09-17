"""便签仓库：SQL/Mongo 双实现由 pick() 决定，写白名单见 schema.NoteWrite。"""

from db.sql.note import Note
from repositories._choose import pick
from schemas.note import NoteWrite

RepoBase = pick()


class NoteRepo(RepoBase):
    model = Note
    table = "notes"
    schema = NoteWrite
