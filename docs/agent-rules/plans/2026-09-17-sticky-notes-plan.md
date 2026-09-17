# 便签应用（sticky-notes）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 subagent-driven-development 按任务清单逐任务实现（本会话已确定采用子代理驱动）。步骤用 `- [ ]` 复选框跟踪进度。

**Goal:** 在复制自 fastapi_template 的 `sticky-notes` 项目上新增「便签模块 + 自助注册 + PyQt5 桌面贴纸式客户端」，使后端保持既有 44 个测试全绿。

**Architecture:** 后端沿用模板严格分层（api→services→repositories→db，SQLite/Mongo 双实现，JWT 认证，RBAC 权限）。新增 notes 业务模块（八件套：SQL 模型→schema→repo→service→API→权限 seed），便签按 `owner_id` 在 service 层强制隔离；新增 `POST /auth/register` 开放注册（默认绑定新角色 `user`，仅持有 `note:*` 权限）。前端新增独立 `desktop_client/`（PyQt5）：登录/注册对话框（含验证码）→ 系统托盘主界面 → 每个便签一个无边框可拖动的贴纸窗口，位置/颜色随服务端持久化。浏览器管理后台保持不变。

**Tech Stack:** FastAPI + SQLAlchemy(SQLite) / Motor(MongoDB) / Pydantic v2 / PyJWT / pytest(httpx); PyQt5 + requests（客户端）。

**执行方式（用户已确认）：** 子代理驱动（subagent-driven-development），每个任务由独立子代理完成，任务间评审。

**测试命令约定：** 所有后端测试在 `sticky-notes/backend/` 目录下用 `../.venv/bin/python -m pytest <test_file> -q` 运行（cwd 必须在 `backend/`）。各任务 Test Cases 表中 `同上` 表示"与该表前一行同命令"；`全量回归`即 `../.venv/bin/python -m pytest -q`。基线：44 passed（已确认）。

**Conventions (learned from codebase):**
- Language: Python 3.10+（venv 为 3.12.3），FastAPI + async
- 目录分层：`api/v1/`（路由）→ `services/`（业务）→ `repositories/`（DB，`RepoBase = pick()` 双实现单例）→ `db/sql|mongo`（模型/索引）→ `schemas/`（Pydantic v2）
- 接线：模型加入 `db/sql/__init__.py` 的 `__all__`；repo 单例加入 `repositories/__init__.py` 的 `__all__`
- 响应统一 `core/response.py`: `ok(data,...)` → `{success, code=OK_CODE(0), message, data}`；`paged(...)`；异常用 `BizError(message=...)` / `NotFoundError("便签不存在")` / `AuthError` / `PermissionDeniedError`（见 `core/exceptions.py`）
- 认证：路由级 `dependencies=[Depends(require_permission("<mod>:<op>"))]`（自带登录校验，内部不要再注入 CurrentUserDep）；需要拿当前用户时给处理器加 `user: CurrentUserDep` 形参（`core.dependencies` 已定义 `CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]`），用 `user.id`
- 权限码格式：`<module>:<verb>`（如 `device:list`）；角色码：`superadmin`(通配) / `admin` / `readonly`
- repo 字段白名单：`create(update)` 只写 `schema.model_fields` 的字段（`NoteWrite` 必须含 `owner_id` 才能写 owner）
- repo 通用助手（双实现同名）：`_find_one(filters)`、`_find_all(filters, order_by)`（仅升序）、`get(id)`、`create(data)`、`update(id, data)`、`delete(id)`
- 密码策略默认：≥8 位，含大写/小写/数字（`password_policy.validate_password_strength`）
- 测试：pytest + httpx AsyncClient fixture `client`、`admin_headers`（superadmin 已登录）；验证码链路登录用 `tests.helpers.login_with_captcha(client, username, password)`
- 注释中文，docstring 简洁；命名 snake_case
- 采样文件（样式据此提炼，均已读源码核对）：`repositories/device.py`、`db/sql/device.py`、`schemas/device.py`、`services/device_service.py`、`api/v1/devices.py`、`services/auth_service.py`、`api/v1/auth.py`、`core/dependencies.py`、`core/response.py`、`db/seed.py`、`db/sql/user.py`、`tests/conftest.py`、`tests/helpers.py`

**Domain Skills (from session context):**
- 单测编写: `cospowers:test-code-generator` — 按测试用例设计测试数据并生成项目规范 pytest 代码（推荐）
- 测试方法: `cospowers:test-driven-development` — TDD 红绿循环，先写失败测试
- 排障调试: `cospowers:systematic-debugging` — 证据优先排障
- 代码编写: `cospowers:code-compliance-check` — 提交前代码规范检查 + lint 修复
- 交接前: `cospowers:verification-before-completion` — 声称完成前复核

---

## 文件结构总览

| 文件 | 动作 | 职责 |
|---|---|---|
| `backend/db/sql/note.py` | 新建 | Note SQL 模型 |
| `backend/db/sql/__init__.py` | 修改 | 注册 Note 模型 |
| `backend/db/mongo/indexes.py` | 修改 | 加 notes.owner_id 索引 |
| `backend/schemas/note.py` | 新建 | NoteCreate/Update/Write |
| `backend/repositories/note.py` | 新建 | NoteRepo 双实现 |
| `backend/repositories/__init__.py` | 修改 | 导出 notes 单例 |
| `backend/db/seed.py` | 修改 | 权限码/角色 user/admin 追加 |
| `backend/services/note_service.py` | 新建 | 便签业务 + owner 隔离 |
| `backend/api/v1/notes.py` | 新建 | 便签 CRUD 路由 |
| `backend/api/v1/router.py` | 修改 | 挂载 notes 路由 |
| `backend/schemas/auth.py` | 修改 | RegisterIn |
| `backend/services/auth_service.py` | 修改 | register 流程 |
| `backend/api/v1/auth.py` | 修改 | /auth/register 端点 |
| `.env`（仓库根 `sticky-notes/.env`） | 修改 | APP_NAME/AUTH_MODE 确认 |
| `backend/tests/test_notes_db.py` | 新建 | 模型/schema/repo 测试 |
| `backend/tests/test_notes_api.py` | 新建 | 便签 API 集成测试 |
| `backend/tests/test_register.py` | 新建 | 注册接口测试 |
| `desktop_client/*`（8 个文件） | 新建 | PyQt5 客户端 |
| `docs/design/2026-09-17-sticky-notes-micro-design.md` | 修改 | §6 变更记录 |

---

## Task 1: Note SQL 模型 + Mongo 索引

**Files:**
- Create: `backend/db/sql/note.py`
- Modify: `backend/db/sql/__init__.py`（__all__ 加 Note）
- Modify: `backend/db/mongo/indexes.py`（notes owner_id 索引）
- Test: `backend/tests/test_notes_db.py`

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-101 | §3.2.3.1 | normal | 初始化后的 metadata | `notes` 表在 `Base.metadata.tables`；含 id/owner_id/title/content/color/pos_x/pos_y/created_at/updated_at 列；存在 `ix_notes_owner_id` 索引 | unit | `tests/test_notes_db.py::test_note_model_registered` | `../.venv/bin/python -m pytest tests/test_notes_db.py -q` |
| TC-102 | §3.2.3.1 | normal | Mongo indexes.py 源码 | 源码包含 `"notes")` 与 `create_index("owner_id")` | unit | `tests/test_notes_db.py::test_mongo_index_includes_notes` | 同上 |

- [ ] **Step 1: 写失败测试** `backend/tests/test_notes_db.py`

  > **调用 `cospowers:test-driven-development`** 执行 RED 阶段——先写失败测试（含模型/mongo 索引两个断言），确认因"模型未建/索引缺失"而失败，而非语法错误

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd ../backend && ../.venv/bin/python -m pytest tests/test_notes_db.py -q`
Expected: FAIL——ModuleNotFoundError: `db.sql.note`

- [ ] **Step 3: 新建** `backend/db/sql/note.py`

```python
"""便签模型。"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.sql.base import Base, IdMixin, TimestampMixin


class Note(Base, IdMixin, TimestampMixin):
    __tablename__ = "notes"

    owner_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(16), default="#fff9c4")
    pos_x: Mapped[int] = mapped_column(Integer, default=0)
    pos_y: Mapped[int] = mapped_column(Integer, default=0)
```

- [ ] **Step 4: 修改** `backend/db/sql/__init__.py`——观察现有 `__all__`，增加 `Note` 一条（参照 `Device` 的写法，`from db.sql.note import Note` 与本模型类名）。

- [ ] **Step 5: 修改** `backend/db/mongo/indexes.py`——在既有的 `create_index(...)` 列表尾部追加：

```python
    await get_collection("notes").create_index("owner_id")
```

（`get_collection` 为该文件已导入的 helper，模仿 devices 那条的写法。）

- [ ] **Step 6: 运行测试确认通过**

Run: `../.venv/bin/python -m pytest tests/test_notes_db.py -q`
Expected: 2 passed

- [ ] **Step 7: 提交**

```bash
cd ../ && git add backend/db/sql/note.py backend/db/sql/__init__.py backend/db/mongo/indexes.py backend/tests/test_notes_db.py
git commit -m "feat(notes): 新增便签 SQL 模型与 Mongo 索引"
```

---

## Task 2: Note Pydantic Schema

**Files:**
- Create: `backend/schemas/note.py`
- Test: `backend/tests/test_notes_db.py`（追加 2 个用例）

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-201 | §3.2.3 | normal | `NoteCreate()` 无参 | title=""、content=""、color="#fff9c4"、pos_x=0、pos_y=0 | unit | `test_notes_db.py::test_note_create_defaults` | `../.venv/bin/python -m pytest tests/test_notes_db.py -q` |
| TC-202 | §3.2.3.1 | normal | 非法颜色 `"red"` | `ValidationError`（color pattern `^#[0-9a-fA-F]{6}$`） | unit | `test_notes_db.py::test_note_create_color_pattern` | 同上 |

- [ ] **Step 1: 追加失败测试**

```python
import pytest as _pytest
from pydantic import ValidationError


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
```

- [ ] **Step 2: 运行确认失败**

Expected: FAIL（ModuleNotFoundError: schemas.note）

- [ ] **Step 3: 新建** `backend/schemas/note.py`

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Expected: 4 passed（Task1 两条 + 本任务两条）

- [ ] **Step 5: 提交**

```bash
git add backend/schemas/note.py backend/tests/test_notes_db.py
git commit -m "feat(notes): 便签 Pydantic schema（写白名单含 owner_id）"
```

---

## Task 3: Note Repository + 接线

**Files:**
- Create: `backend/repositories/note.py`
- Modify: `backend/repositories/__init__.py`（导出 notes 单例）
- Test: `backend/tests/test_notes_db.py`（追加 repo 冒烟）

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-301 | §3.2.3 | normal | conftest 内存库 | `notes.table=="notes"`；`notes.schema.model_fields` 含 owner_id/title/content/color/pos_x/pos_y | unit | `test_notes_db.py::test_note_repo_wired` | `../.venv/bin/python -m pytest tests/test_notes_db.py -q` |
| TC-302 | §3.2.2.2 | normal | 空库 | `notes.create({owner_id:"u1",title:"t"})` 返回 dict 含 id；`notes._find_one({"owner_id":"u1"})` 命中；`notes.get(id)` 命中；`notes.delete(id)` 后 `get` 为 None | unit(集成DB) | `test_notes_db.py::test_note_repo_crud` | 同上 |

- [ ] **Step 1: 追加失败测试**（需 `pytest.mark.asyncio` 或文件级 `asyncio_mode=auto`，参照现有测试写法）

```python
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
```

- [ ] **Step 2: 运行确认失败**

Expected: FAIL（ImportError: cannot import name `notes`）

- [ ] **Step 3: 新建** `backend/repositories/note.py`

```python
"""便签仓库：SQL/Mongo 双实现由 pick() 决定，写白名单见 schema.NoteWrite。"""

from db.sql.note import Note
from repositories._choose import pick
from schemas.note import NoteWrite

RepoBase = pick()


class NoteRepo(RepoBase):
    model = Note
    table = "notes"
    schema = NoteWrite
```

> 已对照 `repositories/device.py` 逐字核对（`RepoBase = pick()` + 类定义 `model/table/schema` 三属性），无需再改。

- [ ] **Step 4: 修改** `backend/repositories/__init__.py`——仿照 `devices` 的导入与实例化方式：

```python
from repositories.note import NoteRepo

notes = NoteRepo()
```

并在文件底部 `__all__` 中追加 `"NoteRepo", "notes"`。若 `__init__.py` 结构不同（例如设备是 `devices = DeviceRepo()` 直实例化），照抄该模式。

- [ ] **Step 5: 运行测试确认通过**

Expected: 6 passed（前四条 + 两条 repo）
> 若 `async` 测试因 asyncio 模式报错，检查 `pyproject.toml` 的 `[tool.pytest.ini_options]` asyncio 配置并参照现有 `tests/` 中 async 测试的标记方式。

- [ ] **Step 6: 提交**

```bash
git add backend/repositories/note.py backend/repositories/__init__.py backend/tests/test_notes_db.py
git commit -m "feat(notes): 便签仓库双实现接入"
```

---

## Task 4: Seed——note 权限码 + user 角色

**Files:**
- Modify: `backend/db/seed.py`
- Test: `backend/tests/test_seed_notes.py`（新建）

> 本任务必须先于 Task 5，因为便签 API 测试需要 `user` 角色持有 `note:*` 权限。

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-401 | §3.2.3.1 | normal | seed 后 | `roles._find_one({"code":"user"})` 非空 | unit(DB) | `test_seed_notes.py::test_role_user_seeded` | `../.venv/bin/python -m pytest tests/test_seed_notes.py -q` |
| TC-402 | §3.2.3.1 | normal | seed 后 | user 角色权限集 == {note:list,create,update,delete}；admin 含 note 四条 | unit(DB) | `test_seed_notes.py::test_role_user_has_note_perms` | 同上 |
| TC-403 | BACKGROUND | normal | 已 seed 的库 | 二次调用 `seed_all()` 不重复插入（角色数/权限数不变） | unit(DB) | `test_seed_notes.py::test_seed_idempotent` | 同上 |

- [ ] **Step 1: 新建失败测试** `backend/tests/test_seed_notes.py`

```python
"""便签 seed 测试：user 角色、note 权限、幂等。"""
from db import seed


async def _perm_codes(role_id):
    from repositories import permissions, roles

    pid_list = await roles.permission_ids(role_id)
    rows = await permissions._find_in("id", pid_list)
    return {r["code"] for r in rows}


async def test_role_user_seeded():
    from repositories import roles

    assert await roles._find_one({"code": "user"}) is not None


async def test_role_user_has_note_perms():
    from repositories import roles

    rid = (await roles.get_by_code("user"))["id"]
    codes = await _perm_codes(rid)
    assert codes == {"note:list", "note:create", "note:update", "note:delete"}

    admin_rid = (await roles.get_by_code("admin"))["id"]
    admin_codes = await _perm_codes(admin_rid)
    assert {"note:list", "note:create", "note:update", "note:delete"} <= admin_codes


async def test_seed_idempotent():
    from repositories import permissions, roles

    n_roles = len(await roles._find_all())
    n_perms = len(await permissions._find_all())

    await seed.seed_all()

    assert len(await roles._find_all()) == n_roles
    assert len(await permissions._find_all()) == n_perms
```

> 方法名已核实现有代码：`roles.permission_ids(role_id)`、`roles.get_by_code(code)`、`roles._find_all()`、`permissions._find_in("id", [...])`、`seed.seed_all()` 均存在（见 `repositories/role.py`、`repositories/permission.py`、`db/seed.py`）。

- [ ] **Step 2: 运行确认失败**

Expected: FAIL（三个断言各自失败：无 user 角色 / 权限不足 / 数不清）

- [ ] **Step 3: 修改** `backend/db/seed.py`

(a) `INIT_ROLES` 末尾追加：

```python
    {"name": "普通用户", "code": "user", "description": "注册用户，仅管理自己的便签"},
```

(b) `INIT_PERMISSIONS` 设备模块段之后追加：

```python
    # 便签模块
    {"code": "note:list", "name": "便签列表", "module": "note"},
    {"code": "note:create", "name": "新建便签", "module": "note"},
    {"code": "note:update", "name": "修改便签", "module": "note"},
    {"code": "note:delete", "name": "删除便签", "module": "note"},
```

(c) `ROLE_PERMISSION_CODES`：`"admin"` 列表追加 `"note:list", "note:create", "note:update", "note:delete"`；并新增键：

```python
    "user": ["note:list", "note:create", "note:update", "note:delete"],
```

> 保持 `superadmin` 为空列表（is_superuser 通配）。`seed_all` 内部已用 `_create_if_missing` 保证幂等，无需改逻辑。

- [ ] **Step 4: 运行测试确认通过**

Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add backend/db/seed.py backend/tests/test_seed_notes.py
git commit -m "feat(notes): seed 新增 note 权限与 user 角色"
```

---

## Task 5: 便签垂直切片——service + API + 路由 + 集成测试

**Files:**
- Create: `backend/services/note_service.py`
- Create: `backend/api/v1/notes.py`
- Modify: `backend/api/v1/router.py`
- Test: `backend/tests/test_notes_api.py`（新建）

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-501 | §5.1/§5.3 ET-001 | exception | 无 token | GET /api/v1/notes → 401 | API | `test_notes_api.py::test_notes_require_auth` | `../.venv/bin/python -m pytest tests/test_notes_api.py -q` |
| TC-502 | §5.1 TC-004 | normal | alice 登录 | POST 2 条 → GET 返回 2 条且 owner_id=alice、默认色 #fff9c4 | API | `test_notes_api.py::test_notes_crud` | 同上 |
| TC-503 | §5.1 TC-005 | normal | 便签存在 | PUT 改 title/color/pos → 生效；DELETE → GET 少一条 | API | `test_notes_api.py::test_notes_crud` | 同上 |
| TC-504 | §5.1 TC-006 | exception | alice 与 bob 各自登录 | bob GET 看不到 alice 的；bob PUT/DELETE alice 便签 → 404 | API | `test_notes_api.py::test_notes_owner_isolated` | 同上 |
| TC-505 | §5.3 ET-003 | boundary | 负数坐标 | POST 便签 pos_x=-50 → 200 且原值返回 | API | `test_notes_api.py::test_notes_coords_roundtrip` | 同上 |

- [ ] **Step 1: 新建失败测试** `backend/tests/test_notes_api.py`

  > **调用 `cospowers:test-code-generator`** 设计测试数据（两用户/跨用户越权/负数坐标）并按项目规范生成 pytest 代码；**调用 `cospowers:test-driven-development`** 执行 RED——先确认因「无 notes 路由」失败

```python
"""便签 API 集成测试：鉴权 / CRUD / owner 隔离 / 坐标回环。"""
from tests.helpers import login_with_captcha

USER_PW = "Abc12345"


async def _make_user(client, username):
    """直接用 repo 建普通用户（绑定 user 角色），返回该用户登录 token。"""
    from repositories import roles, users

    from datetime import datetime, timezone

    from core import security

    user = await users.create({
        "username": username,
        "password_hash": security.hash_password(USER_PW),
        "display_name": username,
        "email": f"{username}@local",
        "is_active": True,
        "is_superuser": False,
        "password_changed_at": datetime.now(timezone.utc),
    })
    rid = (await roles.get_by_code("user"))["id"]
    await users.set_roles(user["id"], [rid])

    resp = await login_with_captcha(client, username, USER_PW)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _auth(client, token):
    return {"Authorization": f"Bearer {token}"}


async def test_notes_require_auth(client):
    r = await client.get("/api/v1/notes")
    assert r.status_code == 401


async def test_notes_crud(client):
    token = await _make_user(client, "alice")
    h = await _auth(client, token)

    r1 = await client.post("/api/v1/notes", headers=h, json={"title": "a", "content": "x"})
    assert r1.status_code == 200
    n1 = r1.json()["data"]
    r2 = await client.post("/api/v1/notes", headers=h, json={"title": "b"})
    assert r2.status_code == 200

    lst = (await client.get("/api/v1/notes", headers=h)).json()["data"]
    assert len(lst) == 2
    assert all(n["owner_id"] == n1["owner_id"] for n in lst)
    assert all(n["color"] == "#fff9c4" for n in lst)

    up = await client.put(f"/api/v1/notes/{n1['id']}", headers=h,
                          json={"title": "a2", "color": "#ffd54f", "pos_x": 88})
    assert up.status_code == 200
    assert up.json()["data"]["title"] == "a2"
    assert up.json()["data"]["color"] == "#ffd54f"
    assert up.json()["data"]["pos_x"] == 88

    dl = await client.delete(f"/api/v1/notes/{n1['id']}", headers=h)
    assert dl.status_code == 200
    assert len((await client.get("/api/v1/notes", headers=h)).json()["data"]) == 1


async def test_notes_owner_isolated(client):
    tok_a = await _make_user(client, "alice_iso")
    tok_b = await _make_user(client, "bob")
    ha, hb = await _auth(client, tok_a), await _auth(client, tok_b)

    nid = (await client.post("/api/v1/notes", headers=ha, json={"title": "secret"})
           ).json()["data"]["id"]

    assert len((await client.get("/api/v1/notes", headers=hb)).json()["data"]) == 0

    assert (await client.put(f"/api/v1/notes/{nid}", headers=hb, json={"title": "hack"})) \
        .status_code == 404
    assert (await client.delete(f"/api/v1/notes/{nid}", headers=hb)).status_code == 404


async def test_notes_coords_roundtrip(client):
    token = await _make_user(client, "carol")
    h = await _auth(client, token)
    r = await client.post("/api/v1/notes", headers=h, json={"pos_x": -50, "pos_y": 1200})
    assert r.status_code == 200
    n = r.json()["data"]
    assert n["pos_x"] == -50 and n["pos_y"] == 1200
```

> 已按 `seed.py` 建超管的写法核对：`users.create` 接受 `username/password_hash/display_name/email/is_active/is_superuser/password_changed_at`，密码哈希用 `core.security.hash_password(USER_PW)`（与 seed 一致），`set_roles(user_id, role_ids)` 存在。`password_changed_at` 设为 now 以免触发「初始密码强制修改」网关。

- [ ] **Step 2: 运行确认失败**

Expected: FAIL（404/ImportError：无 notes 路由与 service）

- [ ] **Step 3: 新建** `backend/services/note_service.py`

  > **调用 `cospowers:code-compliance-check`** 完成后检查代码规范（命名/分层/异常）

```python
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
```

- [ ] **Step 4: 新建** `backend/api/v1/notes.py`

```python
"""便签 CRUD 路由。"""

from fastapi import APIRouter, Depends

from core.dependencies import CurrentUserDep, require_permission
from core.response import ok
from schemas.note import NoteCreate, NoteUpdate
from services import note_service

router = APIRouter(prefix="/notes", tags=["便签"])


@router.get("", dependencies=[Depends(require_permission("note:list"))])
async def list_notes(user: CurrentUserDep):
    return ok(await note_service.list_notes(user.id))


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
```

- [ ] **Step 5: 修改** `backend/api/v1/router.py`——仿照 devices 的 `include_router` 追加：

```python
from api.v1 import notes as notes_router

api_router.include_router(notes_router.router, dependencies=_password_gate)
```

（若 router.py 的导入/依赖网关变量名不同，以实际文件为准。）

- [ ] **Step 6: 运行测试确认通过**

Expected: 4 passed
> 若 401/403 报错，先确认 Task 4 的 seed 已生效（重启测试进程会重建内存库并 seed）。若 `login_with_captcha` 需要用户在角色绑定后才有权限，确认 `set_roles` 后角色关系已持久化。

- [ ] **Step 7（REFACTOR）: 收尾清理**

删除未使用的 import（如 `note_service` 不再有 `Request` 等）、确认 `update_own_note` patch 不含 None 字段、`list_notes` 排序语义与客户端一致；重跑 Step 6 命令确认仍 4 passed。

- [ ] **Step 8: 提交**

```bash
git add backend/services/note_service.py backend/api/v1/notes.py backend/api/v1/router.py backend/tests/test_notes_api.py
git commit -m "feat(notes): 便签 service/API 垂直切片（owner 隔离）"
```

---

## Task 6: 自助注册接口

**Files:**
- Modify: `backend/schemas/auth.py`（RegisterIn）
- Modify: `backend/services/auth_service.py`（register 流程）
- Modify: `backend/api/v1/auth.py`（/auth/register 端点）
- Test: `backend/tests/test_register.py`（新建）

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-601 | §5.1 TC-001 | normal | 空库 | POST /auth/register {alice/Abc12345} → 200，data 含 access_token/refresh_token | API | `test_register.py::test_register_ok_and_autologin` | `../.venv/bin/python -m pytest tests/test_register.py -q` |
| TC-602 | §5.1 TC-001 | normal | 注册后 | 用返回 token 调 GET /notes → 200（user 角色已具备 note:list） | API | `test_register.py::test_register_ok_and_autologin` | 同上 |
| TC-603 | §5.1 TC-002 | exception | alice 已注册 | 再注册同名 → 400 | API | `test_register.py::test_register_duplicate` | 同上 |
| TC-604 | §5.1 TC-003 | exception | 弱密码 abc | 注册 → 400（密码策略提示） | API | `test_register.py::test_register_weak_pwd` | 同上 |
| TC-605 | §5.1 TC-001 | normal | 注册后 | 用 alice/Abc12345 走验证码登录 → 200（账号真实可登录） | API | `test_register.py::test_register_ok_and_autologin` | 同上 |

- [ ] **Step 1: 新建失败测试** `backend/tests/test_register.py`

```python
"""自助注册接口测试。"""
from tests.helpers import login_with_captcha


async def test_register_ok_and_autologin(client):
    from repositories import roles

    r = await client.post("/api/v1/auth/register", json={
        "username": "alice", "password": "Abc12345", "display_name": "爱丽丝",
    })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data.get("access_token") and data.get("refresh_token")

    headers = {"Authorization": f"Bearer {data['access_token']}"}
    lst = await client.get("/api/v1/notes", headers=headers)
    assert lst.status_code == 200

    assert await roles._find_one({"code": "user"})

    again = await login_with_captcha(client, "alice", "Abc12345")
    assert again.status_code == 200, again.text


async def test_register_duplicate(client):
    await client.post("/api/v1/auth/register", json={"username": "bob", "password": "Abc12345"})
    r2 = await client.post("/api/v1/auth/register", json={"username": "bob", "password": "Abc12345"})
    assert r2.status_code == 400
    assert "存在" in r2.json()["message"]


async def test_register_weak_pwd(client):
    r = await client.post("/api/v1/auth/register", json={"username": "carol", "password": "abc"})
    assert r.status_code == 400
```

- [ ] **Step 2: 运行确认失败**

Expected: FAIL（404 /auth/register 或字段校验）

- [ ] **Step 3: 修改** `backend/schemas/auth.py`——追加：

```python
class RegisterIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    display_name: str | None = Field(None, max_length=64)
    email: str | None = Field(None, max_length=128)
```

- [ ] **Step 4: 修改** `backend/services/auth_service.py`——追加 register 流程（**先读该文件现有 import 与 `security.hash_password`、`issue_tokens` 的确切签名**）：

```python
async def register(data) -> dict:
    """开放注册：查重→密码策略→建号→绑 user 角色→记初始密码→签发令牌（注册即登录）。"""
    from datetime import datetime, timezone

    import sqlalchemy
    from sqlalchemy.exc import IntegrityError

    from core.exceptions import BizError
    from core import security
    from repositories import roles, users
    from services import password_policy

    username = data.username

    if await users.get_by_username(username):
        raise BizError(message="用户名已存在")

    await password_policy.validate_password_strength(data.password)

    try:
        user = await users.create({
            "username": username,
            "password_hash": security.hash_password(data.password),
            "display_name": data.display_name or username,
            "email": data.email or "",
            "is_active": True,
            "is_superuser": False,
            "password_changed_at": datetime.now(timezone.utc),
        })
    except (IntegrityError, sqlalchemy.exc.IntegrityError):
        raise BizError(message="用户名已存在")

    role = await roles.get_by_code("user")
    if role:
        await users.set_roles(user["id"], [role["id"]])

    await password_policy.note_initial_password(user["id"], user["password_hash"])

    tokens = await issue_tokens(user)
    tokens["user"] = user
    return tokens
```

> ⚠️ 对照实际 auth_service：若已有 `register` 相关 import 或 `issue_tokens` 返回不含完整 user 字典，请按真实结构微调返回字段（客户端只依赖 `access_token`/`refresh_token`，参见 Task 8 的 API 封装）。`BizError` 构造签名以 `core/exceptions.py` 为准（可能是 `BizError(status_code, message)`）。

- [ ] **Step 5: 修改** `backend/api/v1/auth.py`——在路由文件追加端点（**放在 auth router 定义之后、参照既有 `login`/`refresh` 端点写法**）：

```python
@router.post("/register", summary="开放注册（注册即登录）")
async def register(body: RegisterIn):
    return ok(await auth_service.register(body), message="注册成功")
```

并确保 `from schemas.auth import RegisterIn` 已导入；注册端点**不进入**密码到期网关（auth 路由整体豁免，无需额外处理）。

- [ ] **Step 6: 运行测试确认通过**

Expected: 3 passed

- [ ] **Step 7: 提交**

```bash
git add backend/schemas/auth.py backend/services/auth_service.py backend/api/v1/auth.py backend/tests/test_register.py
git commit -m "feat(auth): 开放注册接口，注册即登录"
```

---

## Task 7: 配置核对 + 全量回归

**Files:**
- Modify: `.env`（**仓库根** `sticky-notes/.env`，config.py 通过 `BACKEND_DIR.parent` 加载，不是 backend/.env）
- 回归：既有 + 新增全部测试

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-701 | 配置 | normal | .env（仓库根）读取 | `settings.app_name` 含"便签"；`auth_mode=="jwt"`；`db_backend=="sqlite"`；`captcha_enabled is True` | unit | `../.venv/bin/python -c "from config import settings; print(settings.app_name, settings.auth_mode, settings.db_backend)"` 打印符合 | `cd backend && ../.venv/bin/python -m pytest -q` |
| TC-702 | 回归 | regression | 全库 | 全部测试通过（44 + 新增全部绿） | regression | 全量 | `cd backend && ../.venv/bin/python -m pytest -q` |

- [ ] **Step 1: 查看** `.env`（仓库根），确认：
  - `APP_NAME=便签应用`（如原为模板名则修改）
  - `AUTH_MODE=jwt`（桌面客户端用 Bearer 最简；如原值不是 jwt 改为 jwt）
  - `DB_BACKEND=sqlite`（默认即可）
  - `CAPTCHA_ENABLED` 保持默认开启（PyQt 客户端已计划支持验证码，见 Task 8）
  修改后无需重启测试（`_test_env` fixture 会加载真实 .env）。

- [ ] **Step 2: 全量回归**

Run: `../.venv/bin/python -m pytest -q`
Expected: 全部通过（原 44 + 新增 tests/test_notes_db 4 + test_seed_notes 3 + test_notes_api 4 + test_register 3 ≈ 58 passed）

- [ ] **Step 3: 提交**

```bash
git add .env
git commit -m "chore: 便签应用配置核对（app_name/auth_mode）"
```

---

## Task 8: PyQt5 桌面贴纸式客户端

**Files:**
- Create: `desktop_client/requirements.txt`
- Create: `desktop_client/config.py`
- Create: `desktop_client/api.py`
- Create: `desktop_client/login_dialog.py`
- Create: `desktop_client/note_window.py`
- Create: `desktop_client/desk.py`
- Create: `desktop_client/app.py`
- Create: `desktop_client/run.sh`
- Create: `desktop_client/README.md`
- Create: `desktop_client/tests/__init__.py`、`desktop_client/tests/test_api_logic.py`
- Test: 编译冒烟 + 纯逻辑 unitttest + 手工用例（GUI 环境，见 Test Cases）

**Test Cases:**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-801 | §3.2.5 | normal | Python3 venv | 全部模块 `py_compile` 通过 | unit(smoke) | desktop_client 全部 .py | `../.venv/bin/python -m py_compile desktop_client/*.py` |
| TC-802 | §3.2.5 | boundary | requests 库 | `_store_tokens` 写入后 `load_config` 原样取回且含 base_url/token/username | unit(mock HOME) | api.py 纯逻辑 | 见 Step 2 |
| TC-803 | §5/手工验收 | manual | 有显示环境 + 已启动后端 | 登录→建 3 张便签→拖动/改色→重启客户端→位置与内容恢复；注册→自动登录 | manual | 全流程 | 见 Task 9 |

- [ ] **Step 1: 编译冒烟（空壳先行）** 创建 `desktop_client/requirements.txt`：

```
PyQt5>=5.15
requests>=2.28
```

并创建 6 个空模块占位后先让 TC-801 通过（再逐步填充）。**实际做法：直接按 Step 2-8 写全模块，再统一跑 py_compile。**

- [ ] **Step 2: 新建** `desktop_client/config.py` + `desktop_client/api.py`

```python
# config.py —— 配置与令牌持久化
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sticky_notes"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

_EMPTY = {"base_url": DEFAULT_BASE_URL, "access_token": "",
          "refresh_token": "", "username": ""}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_EMPTY)


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass
```

```python
# api.py —— 服务端 HTTP 封装（requests 同步，贴 PyQt 槽函数）
import base64

import requests

from config import DEFAULT_BASE_URL, load_config, save_config

API_PREFIX = "/api/v1"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status
        self.message = message


class Client:
    def __init__(self, base_url: str | None = None):
        cfg = load_config()
        self.base_url = (base_url or cfg.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.session = requests.Session()          # 携带 anon_sid cookie 走验证码登录
        self.access_token = cfg.get("access_token", "")
        self.refresh_token = cfg.get("refresh_token", "")
        self.username = cfg.get("username", "")

    # ---- 基础请求 ----
    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _url(self, path):
        return f"{self.base_url}{API_PREFIX}{path}"

    def _raise(self, resp):
        try:
            msg = resp.json().get("message") or resp.json().get("detail") or resp.text
        except ValueError:
            msg = resp.text
        raise ApiError(f"HTTP {resp.status_code}: {msg}", resp.status_code)

    def _request(self, method, path, json=None):
        resp = self.session.request(method, self._url(path),
                                    json=json or {}, headers=self._headers())
        if resp.status_code >= 400:
            self._raise(resp)
        return resp.json().get("data")

    # ---- 认证 ----
    def fetch_captcha(self):
        resp = self.session.get(self._url("/auth/captcha"))
        if resp.status_code != 200:
            self._raise(resp)
        data = resp.json()["data"]
        return data["captcha_id"], base64.b64decode(data["image_base64"])

    def login(self, username, password, captcha_id, captcha_code):
        data = self._request("POST", "/auth/login", {
            "username": username, "password": password,
            "captcha_id": captcha_id, "captcha_code": captcha_code,
        })
        self._save_tokens(data, username)
        return data

    def register(self, username, password, display_name=""):
        data = self._request("POST", "/auth/register", {
            "username": username, "password": password, "display_name": display_name,
        })
        self._save_tokens(data, username)
        return data

    def _save_tokens(self, data, username):
        self.access_token = data["access_token"]
        self.refresh_token = data.get("refresh_token", "")
        self.username = username
        cfg = load_config()
        cfg.update(base_url=self.base_url, access_token=self.access_token,
                   refresh_token=self.refresh_token, username=username)
        save_config(cfg)

    # ---- 便签 ----
    def list_notes(self):
        items = self._request("GET", "/notes") or []
        return sorted(items, key=lambda n: n.get("updated_at") or "", reverse=True)

    def create_note(self, title="", content="", color="#fff9c4", x=0, y=0):
        return self._request("POST", "/notes", {
            "title": title, "content": content, "color": color, "pos_x": int(x), "pos_y": int(y)})

    def update_note(self, note_id, **fields):
        return self._request("PUT", f"/notes/{note_id}", fields)

    def delete_note(self, note_id):
        return self._request("DELETE", f"/notes/{note_id}")
```

TC-802 逻辑测试（**放在桌面客户端目录 `desktop_client/tests/test_api_logic.py`，用 `unittest` + 不依赖 GUI**）：

```python
"""api/config 纯逻辑测试（mock HOME/XDG，不启动 Qt）。"""
import os
import tempfile
import unittest
from pathlib import Path


class TokenStoreTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._td.name

    def tearDown(self):
        if self._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old_xdg
        self._td.cleanup()

    def test_save_load_roundtrip(self):
        import config as cc

        cc.save_config({"base_url": "http://x:1", "access_token": "t",
                        "refresh_token": "r", "username": "u"})
        cfg = cc.load_config()
        self.assertEqual(cfg["access_token"], "t")
        self.assertEqual(cfg["username"], "u")


if __name__ == "__main__":
    unittest.main()
```

运行：`cd desktop_client && ../../.venv/bin/python -m unittest discover -s tests -v`（**该测试不 import PyQt，可在无显示环境跑通**）

- [ ] **Step 3: 新建** `desktop_client/login_dialog.py`

```python
"""登录/注册对话框（含验证码）。"""
import base64

from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QDialog, QFormLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from api import Client


class LoginDialog(QDialog):
    def __init__(self, client: Client, parent=None):
        super().__init__(parent)
        self.client = client
        self.ok = False
        self.captcha_id = ""
        self.setWindowTitle("便签 - 登录")
        self.setMinimumWidth(380)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_login(), "登录")
        tabs.addTab(self._build_register(), "注册")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        self._refresh_captcha()

    # ---- 登录页 ----
    def _build_login(self):
        w = QWidget()
        self.user = QLineEdit(); self.pwd = QLineEdit(); self.pwd.setEchoMode(QLineEdit.Password)
        self.captcha_input = QLineEdit()
        self.captcha_img = QLabel("加载验证码..."); self.captcha_img.setFixedHeight(60)
        self.btn_reload = QPushButton("换一张")
        self.btn_reload.clicked.connect(self._refresh_captcha)

        form = QFormLayout(w)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.pwd)
        form.addRow(self.captcha_img, self.btn_reload)
        form.addRow("验证码", self.captcha_input)
        btn = QPushButton("登录")
        btn.clicked.connect(self._on_login)
        form.addRow(btn)
        return w

    def _refresh_captcha(self):
        try:
            cid, raw = self.client.fetch_captcha()
            self.captcha_id = cid
            pm = QPixmap(); pm.loadFromData(raw)
            self.captcha_img.setPixmap(pm.scaledToWidth(140))
        except Exception as e:  # noqa: BLE001
            self.captcha_img.setText(f"验证码获取失败: {e}")

    def _on_login(self):
        try:
            self.client.login(self.user.text(), self.pwd.text(),
                              self.captcha_id, self.captcha_input.text())
        except Exception as e:  # noqa: BLE001
            self._fail(str(e)); self._refresh_captcha()
        else:
            self.ok = True
            self.accept()

    # ---- 注册页 ----
    def _build_register(self):
        w = QWidget()
        self.reg_user = QLineEdit(); self.reg_pwd = QLineEdit(); self.reg_pwd.setEchoMode(QLineEdit.Password)
        self.reg_name = QLineEdit()
        form = QFormLayout(w)
        form.addRow("用户名", self.reg_user)
        form.addRow("密码(≥8位含大小写数字)", self.reg_pwd)
        form.addRow("显示名", self.reg_name)
        btn = QPushButton("注册并登录")
        btn.clicked.connect(self._on_register)
        form.addRow(btn)
        return w

    def _on_register(self):
        try:
            self.client.register(self.reg_user.text(), self.reg_pwd.text(),
                                 self.reg_name.text())
        except Exception as e:  # noqa: BLE001
            self._fail(str(e)); self._refresh_captcha()
        else:
            self.ok = True
            self.accept()

    def _fail(self, msg):
        QMessageBox.warning(self, "提示", msg)
```

- [ ] **Step 4: 新建** `desktop_client/note_window.py`

```python
"""贴纸式便签窗口：无边框、可拖动、可改色、关窗即存。"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QColorDialog, QFrame, QHBoxLayout, QLineEdit,
                             QTextEdit, QToolButton, QVBoxLayout)

COLORS = ["#fff9c4", "#ffd54f", "#a5d6a7", "#90caf9", "#f48fb1"]


class NoteWindow(QFrame):
    def __init__(self, client, note, on_need_close=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.note = note
        self.on_need_close = on_need_close
        self._moving = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(220, 180)
        self._apply_color(note.get("color") or "#fff9c4")

        title = QLineEdit(note.get("title") or "")
        title.textChanged.connect(lambda _: self._save_later())
        content = QTextEdit(note.get("content") or "")
        content.textChanged.connect(lambda: self._save_later())
        color_btn = QToolButton(); color_btn.setText("●")
        color_btn.clicked.connect(self._pick_color)
        del_btn = QToolButton(); del_btn.setText("✕")
        del_btn.clicked.connect(self._delete)

        bar = QHBoxLayout()
        bar.addWidget(color_btn)
        bar.addWidget(del_btn, 0, Qt.AlignRight)
        body = QVBoxLayout(self)
        body.addLayout(bar)
        body.addWidget(title)
        body.addWidget(content, 1)

        self._title, self._content, self._color_btn = title, content, color_btn
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        self.move(int(note.get("pos_x") or 0), int(note.get("pos_y") or 0))

    def _apply_color(self, color):
        self.setStyleSheet(
            f"NoteWindow {{ background: {color}; border: 1px solid #bdbdbd; }}"
            f"QLineEdit,QTextEdit {{ background: transparent; border: none; }}")

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self._apply_color(color.name())

    def moveEvent(self, _e):
        self._save_timer.start()          # 拖动停止后延迟 600ms 上报位置

    def _save_later(self):
        self._save_timer.start()

    def _save(self):
        try:
            self.client.update_note(
                self.note["id"],
                title=self._title.text(), content=self._content.toPlainText(),
                color=self._current_color(), pos_x=self.x(), pos_y=self.y())
        except Exception:  # noqa: BLE001
            pass

    def _current_color(self):
        import re
        m = re.search(r"background: (#[0-9a-fA-F]{6})", self.styleSheet())
        return m.group(1) if m else "#fff9c4"

    def _delete(self):
        try:
            self.client.delete_note(self.note["id"])
        finally:
            self.close()

    def closeEvent(self, _e):
        if self._save_timer.isActive():
            self._save()
        if self.on_need_close:
            self.on_need_close(self)
        super().closeEvent(_e)
```

- [ ] **Step 5: 新建** `desktop_client/desk.py`

```python
"""主界面：托盘 + 新建便签 + 便签窗口管理。"""
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QMessageBox, QSystemTrayIcon

from api import Client
from note_window import NoteWindow


class Desk:
    def __init__(self, client: Client):
        self.client = client
        self.windows: list[NoteWindow] = []
        self.tray = QSystemTrayIcon(QIcon.fromTheme("edit-paste"), QApplication.instance())
        self.tray.setToolTip("便签")
        menu = QMenu()
        act_new = QAction("新建便签", None); act_new.triggered.connect(self.new_note)
        act_quit = QAction("退出", None); act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_new); menu.addSeparator(); menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.restore_all())
        self.tray.show()

    def _load(self):
        try:
            return self.client.list_notes()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(None, "提示", f"加载便签失败: {e}")
            return []

    def restore_all(self):
        for n in self._load():
            self.spawn(n, restore=True)

    def new_note(self):
        try:
            note = self.client.create_note()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(None, "提示", str(e)); return
        self.spawn(note)

    def spawn(self, note, restore=False):
        if restore and any(w.note["id"] == note["id"] for w in self.windows):
            return
        w = NoteWindow(self.client, note, on_need_close=self._drop, parent=None)
        self.windows.append(w)
        w.show()

    def _drop(self, w):
        if w in self.windows:
            self.windows.remove(w)
```

- [ ] **Step 6: 新建** `desktop_client/app.py`

```python
"""便签客户端入口：有令牌则进主界面，无效则回登录框。"""
import sys

from PyQt5.QtWidgets import QApplication

from api import Client
from desk import Desk
from login_dialog import LoginDialog


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)     # 托盘常驻
    client = Client()

    if client.access_token:
        try:
            client.list_notes()               # 校验令牌有效性
        except Exception:                     # noqa: BLE001
            client.access_token = ""          # 失效则重新登录

    if not client.access_token:
        dlg = LoginDialog(client)
        dlg.exec_()
        if not dlg.ok:
            return 0

    Desk(client).restore_all()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: 新建** `desktop_client/run.sh`（并 `chmod +x`）

```bash
#!/usr/bin/env bash
# 便签客户端启动脚本：首次自动建 venv 并安装依赖
set -e
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python app.py
```

- [ ] **Step 8: 新建** `desktop_client/README.md`（≤40 行：功能、依赖、启动、登录/注册说明、服务器地址修改方式：「登录框右上角或 `~/.config/sticky_notes/config.json` 的 `base_url`」）

- [ ] **Step 9: 运行编译冒烟（TC-801）与逻辑测试（TC-802）**

Run（均在仓库根 `sticky-notes/` 下执行）：
```bash
.venv/bin/python -m py_compile desktop_client/config.py desktop_client/api.py \
  desktop_client/login_dialog.py desktop_client/note_window.py desktop_client/desk.py desktop_client/app.py
cd desktop_client && ../.venv/bin/python -m unittest discover -s tests -v
```
Expected: py_compile 无输出（成功）；unittest 1 passed（test_save_load_roundtrip）
> ⚠️ 若相对路径不对，用绝对路径 `/home/master/workspace/new_apps/xiamen/sticky-notes/.venv/bin/python`。

- [ ] **Step 10（REFACTOR）: 客户端收尾清理**

删除未使用的 import（`login_dialog.py` 的 `QVBoxLayout/QLabel` 若未用则删、`note_window.py` 的 `COLORS` 若未用则删）、确认所有 `except Exception` 有 `# noqa: BLE001`、`run.sh` 可执行位已加；重跑 Step 9 两个命令确认仍通过。

- [ ] **Step 11: 提交**

```bash
cd ../ && git add desktop_client
git commit -m "feat(client): PyQt5 桌面贴纸式便签客户端"
```

---

## Task 9: 端到端验收 + 文档收尾

**Files:**
- Modify: `docs/design/2026-09-17-sticky-notes-micro-design.md`（§6 变更记录）
- 运行：后端 + 客户端手工验收（GUI 环境）

**Test Cases（手工 / 集成）：**

| ID | Source | Type | Preconditions/Input | Expected Assertions | Automation | Test Target | Command |
|---|---|---|---|---|---|---|---|
| TC-901 | §3.2.2.1 | E2E | 后端已起 | curl 注册→登录→建便签→再登录位置保留 | API/手动 | 全链路 | 见 Step 2 |
| TC-902 | §3.2.5 | manual | 有显示环境 | 客户端注册→自动登录→贴 3 张→拖动/改色→退出重启→内容+坐标恢复；托盘图标常驻；多账号切换各自便签隔离 | manual | PyQt 客户端 | 见 Step 3 |
| TC-903 | §4.1 RT-001 | regression | 全库 | `pytest -q` 全绿（≈58） | regression | 全量 | `../.venv/bin/python -m pytest -q` |

- [ ] **Step 1: 全量回归复跑**

Run: `../.venv/bin/python -m pytest -q`
Expected: 全部通过（≈58 passed）

- [ ] **Step 2: 后端 API 手工链路（curl）**

```bash
# 1) 起后端
backend/start.sh 或 uvicorn main:app --app-dir backend --port 8000 &
# 2) 注册
curl -s -X POST http://127.0.0.1:8000/api/v1/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"Abc12345"}' | python3 -m json.tool
# 3) 用返回的 access_token 建便签（TOKEN=... 换成实际值）
curl -s -X POST http://127.0.0.1:8000/api/v1/notes -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"title":"hi","content":"hello","pos_x":40,"pos_y":60}'
# 4) 拉列表（应含上条，owner 为自己的 id）
curl -s http://127.0.0.1:8000/api/v1/notes -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

- [ ] **Step 3: PyQt 客户端手工验收（显示环境）**

```bash
cd desktop_client && ./run.sh
```
核对清单：注册新用户→自动进入主界面；«新建便签»贴 3 张；拖动窗口→60ms 后位置上报；改色按钮换色；«✕»删除；退出程序→重启→3 张便签内容与坐标恢复；托盘菜单可新建/退出；再注册第二账号→两个账号便签互相隔离。

- [ ] **Step 4: 回填设计文档 §6 变更记录**

在 `docs/design/2026-09-17-sticky-notes-micro-design.md` 的 §6 表格填入实际改动（新增 notes 模块/注册接口/seed/PyQt 客户端），并勾选附录自检清单末项"§6 变更控制：编码完成后已更新并补充确认人"。

- [ ] **Step 5: 最终提交**

```bash
git add -A
git commit -m "docs: 更新便签微设 §6 变更记录，收尾"
git log --oneline
```

---

## 验收清单（收尾前逐项核对）

- [ ] 后端 `pytest -q` 全绿（原 44 + 新增压测全过）
- [ ] note 接口：未登录 401；登录 CRUD 正常；跨用户 404 隔离；坐标回环
- [ ] 注册：成功+自动登录；重名 400；弱密码 400
- [ ] seed：`user` 角色仅 4 个 note 权限；admin 含 note；二次 seed 幂等
- [ ] Mongo 索引：`notes.owner_id` 已加（代码层）
- [ ] PyQt：编译冒烟 + token 存取单测通过；GUI 手工验收按 TC-902 清单完成
- [ ] 设计文档 §6 变更记录已回填、附录自检勾完
