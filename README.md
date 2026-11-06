# StickyNotes · 多用户便签

一个能真正用起来的便签工具：FastAPI + SQLite 后端，同一份数据同时服务三种前端——浏览器便签页、桌面贴纸客户端（pywebview）和管理后台。支持注册登录、多用户隔离、Markdown 排版、全文搜索、待办清单、办公模板与整机备份。

> 端口统一 **18000**。默认超管 `admin / admin123` 仅用于本地体验，公开部署前必须改密、更换 `JWT_SECRET`、单独配置 `DATA_ENCRYPTION_KEY` 并设为 `APP_ENV=production`。

## 功能一览

| 场景 | 能力 |
|---|---|
| 记录 | Markdown 源码 / 排版双视图、脚注与上下标、代码高亮、六种便签颜色、窗口自由拖动并记住位置 |
| 办公 | 一键插入「会议纪要 / 今日待办 / 周报 / 需求速记」模板；任务清单可点勾选；状态栏实时显示 `字数 · ☑ 完成/总数`；单张便签导出 `.md` |
| 整理 | 标题/内容不区分大小写搜索、分页、按更新时间排序 |
| 数据安全 | 整机备份导出 JSON / 追加导入（字段白名单、坏数据跳过、最多 1000 条）；便签严格按 owner 隔离，跨用户访问返回 404 |
| 多端 | Web 便签页 `/`、管理后台 `/admin/`、桌面贴纸客户端 `desktop_client_web/` |
| 运维 | `/health` 返回启动各阶段耗时与 seed 状态；RBAC、JWT/Session 双认证、登录验证码与账户锁定、定时巡检、通知渠道 |

## 快速开始

### 后端

```bash
pip install -r requirements.txt   # 唯一依赖清单；backend/requirements.txt 只是兼容引用
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 18000
```

打开 `http://127.0.0.1:18000/` 用 Web 便签，`/admin/` 是管理后台，`/health` 是健康检查，`/docs` 是接口文档。首次启动自动按版本迁移建库并写种子数据，之后每次启动由版本表 + 种子指纹闸门跳过重复初始化；改造前就存在的老库会自动纳管，不重建表、不丢数据。

Linux 生产部署：

```bash
sudo bash deploy/install.sh /opt/sticky-notes
systemctl status sticky-notes
journalctl -u sticky-notes -f
```

或直接 `./start.sh start` / `./start.sh stop` / `./start.sh --dev`（热重载）；端口可用 `PORT` 环境变量覆盖。

### 桌面贴纸客户端（当前主力前端）

```bash
cd desktop_client_web
pip install -r requirements.txt
python app.py
```

`dist/StickyNotesWeb.exe` 是打包好的 Windows 单文件版。服务端地址默认写在 `config.py` 的 `DEFAULT_BASE_URL`，登录框里也能改，或编辑 `~/.config/sticky_notes/config.json` 的 `base_url`。

**登录态会自动续期**：access 只有 30 分钟，但客户端在它快过期时静默调用 `/auth/refresh`，万一仍收到 401 也会自动续期并重放原请求，上层无感。每次刷新会换到一份新的 7 天 refresh，所以只要 7 天内打开过一次就不会再要求登录。只有 refresh 真正失效（超过 7 天没用、在别处登出/改密）才回登录窗，并弹一次提示。多开客户端共用 `config.json` 时会互相采用已换好的令牌，不会因 refresh 一次性轮换而互相踢下线；配置文件改为原子写入，避免并发写坏 JSON。服务端暂时连不上不会清空登录态。

便签桌的「恢复便签」最多同时打开 8 张（每张是一个独立 WebView 进程，一次全开会卡），其余在列表里双击按需打开；「备份 / 导入」走后端 `/notes/export`、`/notes/import`。

> 另有 `desktop_client/`（PyQt5 版），功能相近，按需选用。`frontend/static/` 是浏览器版，`desktop_client_web/web/` 是贴纸客户端内嵌页面，改功能时注意别改错目录。

## 架构

```
backend/
├── main.py                app 工厂 + lifespan（建表/seed/调度器）+ /health 计时
├── config.py              pydantic-settings 全量配置
├── core/                  security / dependencies / response / exceptions / logger
│   ├── uow.py            工作单元：service 层多步写共用事务，异常整体 rollback
│   ├── rate_limit.py     匿名接口滑动窗口限流（零依赖）
│   └── startup_metrics.py 启动阶段计时（零第三方依赖）
├── db/                    sql 模型 + mongo 索引 + seed（带内容指纹闸门）
│   └── migration.py       alembic 运行时：新库建表 / 老库 stamp / 增量升级
├── schemas/               Pydantic DTO
├── repositories/          数据访问（SQL / Mongo 双实现）
├── services/              业务：auth / rbac / note / device / captcha / notify
├── api/v1/                路由：auth, notes, devices, sys_*, notify
└── scheduler/             inputs.conf 风格定时任务

migrations/                项目根：alembic 版本脚本（唯一 schema 真源是 db/sql 模型）
└── versions/0001_baseline.py
```

分层铁律：`api → services → repositories → db`，只有 `repositories/` 与 `db/` 能碰 SQLAlchemy/Motor；所有 ID 为 UUID 字符串。唯一例外是 `core/uow.py` 的 `unit_of_work()`——事务边界属于应用服务层，service 可 import 它，但不能直接 import `db.session`。

## 启动性能（实测，可直接复现）

Windows / Python 3.12 / SQLite，稳态下 8 轮交替 A/B 取样中位数（已含 alembic 迁移层）：

| 指标 | 当前 | 旧行为对照 | 变化 |
|---|---|---|---|
| database 阶段 | 132 ms | 361 ms | **−229 ms（约 2.7×）** |
| 总启动 | 4195 ms | 4059 ms | 见下方说明 |

总启动样本在 3.6–5.3 s 之间波动，其中约 3.0–3.3 s 是 FastAPI / Pydantic / SQLAlchemy 的 Python 导入固定成本，与业务无关；两组总启动中位数的差值落在噪声范围内，**不作为优化结论**。可稳定复现的收益在 database 阶段。

迁移层的代价：稳态只做一次轻量连接 + 版本表读取（`current` 快速路径，不 import alembic），相对接入迁移前的 118 ms 仅增加约 14 ms。只有全新库建表、老库纳管、版本升级这三类低频路径才加载 alembic。

优化集中在启动逻辑本身：

- `db/seed.py`：种子内容指纹闸门，稳态只做 3 次校验查询，跳过约 45 次查库与关联表重写
- `repositories/role.py`：权限集合未变化时跳过 DELETE + INSERT
- `config.py` / `db/base.py`：`DEBUG` 与 SQL `echo` 解耦，默认不再回显全部 SQL
- `services/notify/senders.py`：`httpx` 延迟到首次发通知才导入
- `db/migration.py`：用标准库 `ast` 求 head，稳态不加载 alembic；否则每次启动加载完整迁移栈会让 database 阶段从约 120 ms 涨到约 900 ms
- 调度器注册保持串行——实测与数据库初始化并行会和首次 SQLite 连接争资源，反而更慢

复现：

```bash
STARTUP_LEGACY=true python -m uvicorn main:app --port 18000   # 旧行为对照（仅诊断用）
python -m uvicorn main:app --port 18000                        # 优化后
curl http://127.0.0.1:18000/health                             # startup_ms / stages_ms / seed.mode
```

`STARTUP_LEGACY` 只用于性能对照，生产保持 `false`。

## 常用接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 + 启动耗时 + seed 状态（不鉴权） |
| POST | `/api/v1/auth/register` | 开放注册，注册即登录 |
| POST | `/api/v1/auth/login` | 登录（含图形验证码） |
| GET/POST | `/api/v1/notes` | 便签列表（支持 `q/page/page_size`）/ 新建 |
| PUT/DELETE | `/api/v1/notes/{id}` | 修改 / 删除（仅本人） |
| GET | `/api/v1/notes/export` | 导出本人全部便签 JSON |
| POST | `/api/v1/notes/import` | 追加导入 `{"notes":[...]}` |

统一响应：`{success, code, message, data}`。

## 安全设计

| 项 | 实现 | 开关 |
|---|---|---|
| 注册降权 | 新用户默认只拿 `user` 角色（仅 `note:*`），不再绑定 `admin`；可直接访问 `/sys/users` 会被 403 挡下 | `REGISTER_DEFAULT_ROLE` / `REGISTER_ENABLED` |
| 匿名接口限流 | 按「接口 + IP」滑动窗口，验证码、登录、注册超限返回 429（注册单独 5 次/分） | `RATE_LIMIT_ENABLED` / `RATE_LIMIT_PER_MINUTE` |
| 代理头 | 默认不信任 `X-Forwarded-For`，避免伪造 IP 同时绕过账户锁定与限流 | `LOCK_TRUST_PROXY=false` |
| 令牌吊销 | `users.token_version`：登出/改密即 +1，此前签发的 access/refresh 立即失效；refresh 带 `jti` 落库，轮换时原子消费，旧令牌无法重放 | — |
| 事务边界 | `core/uow.py`：注册、改密、角色/权限绑定等多步写共用同一事务，异常整体 rollback；独立调用 repository 时行为不变且失败自动回滚 | — |
| 密钥分离 | `DATA_ENCRYPTION_KEY` 独立加密通知渠道密码，与 JWT 签名密钥解耦；解密按「新密钥 → 旧派生」回退，升级不丢历史密文 | `APP_ENV=production` 时弱密钥**拒绝启动** |

> 行为变化：登出 / 改密后旧令牌立即失效（401）；每个 refresh token 只能用一次。桌面贴纸客户端会自动用 refresh 续期并重放 401 请求，所以正常使用时不会再被踢回登录框（见「桌面贴纸客户端」）。只有 refresh 本身失效才会回登录窗；窗口不做热切换，重启客户端即显示登录框。

## 测试与工程化

```bash
pytest backend/tests -q                 # 后端全量（含安全加固回归）
pytest desktop_client_web/tests -q     # 桌面 web 版逻辑 + 令牌自动续期回归
ruff check backend desktop_client_web --select F   # 致命规则静态检查
```

`desktop_client/tests` 需要 PyQt5 环境，用该目录自带的 venv 单独运行；两个桌面目录都叫 `tests`，合并收集会包名冲突，故 `pyproject.toml` 的 `testpaths` 只收后端。CI 见 `.github/workflows/ci.yml`（Python 3.11 / 3.12：lint + 后端测试 + 桌面逻辑测试）。

依赖清单以 `pyproject.toml` 为真源，`requirements.txt` 与之对齐，`backend/requirements.txt` 只是 `-r ../requirements.txt` 的兼容引用。

## 数据库迁移

表结构由 `migrations/` 下的 alembic 版本脚本管理，**唯一真源是 `backend/db/sql/` 的 SQLAlchemy 模型**。应用启动自动执行，无需手工干预：

| 库状态 | 启动行为 |
|---|---|
| 全新空库 | `upgrade head`，由 `0001_baseline` 建全部表与索引 |
| 改造前的老库（有业务表、无 `alembic_version`） | 补齐历史列 → `stamp 0001_baseline` → 应用之后的增量迁移，**不重建表、不动数据** |
| 已纳管 | `upgrade head`；版本记录指向不存在的脚本时直接拒绝启动 |

老库 stamp 到「基线」而非 head，保证后续 `0002`、`0003` 仍会被正常应用。`db/migration.py` 里的 `_LEGACY_USER_COLUMNS` 只在老库纳管前跑一次，属于历史兼容层；**新字段一律写迁移，不要再往里加**。

新增字段的标准流程：

```bash
# 1. 改模型 backend/db/sql/*.py
# 2. 生成迁移（DATABASE_URL 指向一个已 upgrade 到 head 的库）
alembic revision --autogenerate -m "add note pinned"
# 3. 人工审阅生成的脚本（SQLite 的 ALTER 走 batch 模式，务必确认没丢数据）
alembic upgrade head
alembic check          # 应输出 No new upgrade operations detected
```

运维命令（先停服务；`DATABASE_URL` 需与 `.env` 一致，环境变量可直接覆盖）：

```bash
alembic current                    # 当前版本
alembic history
alembic upgrade head               # 手工升级
alembic check                      # 模型与库是否漂移，适合放进 CI
alembic downgrade 0001_baseline    # 谨慎：会执行基线逆操作，生产勿用
```

`alembic.ini` 必须保持纯 ASCII：alembic 用 `encoding="locale"` 读它，中文 Windows 的 GBK 会直接解码失败。URL 不在 ini 里，由 `migrations/env.py` 与 `db/migration.py` 共用的 `sync_database_url()` 注入，把 `sqlite+aiosqlite` 换成同步 `sqlite` 驱动。SQLite 保持单 worker，避免多进程同时抢迁移锁；多实例滚动升级请先在网关侧摘流量。

## 公开部署前的安全清单

- 生成两个不同的随机密钥写入 `.env`：`JWT_SECRET`、`DATA_ENCRYPTION_KEY`；设 `APP_ENV=production`（弱密钥会拒绝启动）
- 改掉默认超管口令 `admin/admin123`；`REGISTER_DEFAULT_ROLE` 保持 `user`，或直接 `REGISTER_ENABLED=false`
- `DEBUG=false`、`ECHO_SQL=false`；`CORS_ORIGINS` 改成实际域名，不要用 `*`
- `CAPTCHA_ENABLED=true`、`RATE_LIMIT_ENABLED=true`、`LOCK_SECONDS` 保持开启
- 只有确认可信反向代理会**覆盖** `X-Forwarded-For` 时才设 `LOCK_TRUST_PROXY=true`
- 数据目录 `data/` 权限收紧（含 `app.db`、日志、seed 指纹文件）
- SQLite 场景保持单 worker，避免定时任务重复执行与写锁竞争

## 文档

- `docs/design/2026-09-17-sticky-notes-micro-design.md`：便签模块与桌面端设计说明书
- `docs/新增功能模块指南.md`：新增业务模块的标准流程
- `desktop_client/README.md`：PyQt5 版打包与排错
