# Troubleshooting Report

**日期**：2026-09-17
**项目**：sticky-notes（部署机 10.74.64.100，/apps/new_apps/sticky-notes）

## Problem Summary
- 故障现象：用户把应用部署到 10.74.64.100 的 http:18000 / ssl:18888 后反馈：① 界面难看 ② 登录后什么都没有 ③ 找不到"新增便签"按钮、期望便签可输入/拖动贴屏 ④ 期望可关闭 ⑤ 期望可贴多张 ⑥ 期望多用户互不影响。
- 影响范围：所有通过浏览器访问的用户（主要为普通/注册用户）
- 分类：Integration/E2E（需求-交付面错位）
- 环境：Linux host（root 下 /usr/local/python3.10 与 /apps/new_apps），uvicorn main:app --host 0.0.0.0 --port 18000 --reload，前端由 backend 在 `/` 托管 frontend/static/index.html

## 5W1H
- What：浏览器打开 10.74.64.100:18000 显示模板默认管理后台（标题"安全管控平台"，UI 陈旧）；注册/登录普通用户后无任何菜单/内容；页面没有任何便签功能
- When：部署完成后持续
- Where：http://10.74.64.100:18000 与 https://10.74.64.100:18888（外部网关映射）→ backend 18000
- Who：所有浏览器端用户；注册用户(user 角色)最明显
- Why（近期变化）：无近期变更——交付物本身就这样（便签功能在 PyQt 桌面客户端，不在网页）
- How：打开部署 URL → 用浏览器登录/注册 → 侧边栏空白；无新增便签入口

## Investigation Process（真实证据）
1. 服务器进程/监听：`ss -tlnp` 显示 `*:18000` 由 `uvicorn main:app --host 0.0.0.0 --port 18000 --reload` 监听，cwd=`/apps/new_apps/sticky-notes/backend`（后端正常）
2. 服务器 git：`git log` HEAD=91eebfb，与本地交付 master 一致（版本对齐）
3. **浏览器视角 curl（决定性证据）**：`curl http://10.74.64.100:18000/` 返回模板自带 SPA，`<title>安全管控平台</title>`、加载 /css/app.css /js/api.js——即**模板默认管理后台**
4. 代码证据：`backend/main.py:83` 以 `StaticFiles(directory=frontend/static, html=True)` 在 `/` 托管前端；`frontend/static/` 下仅模板 index.html，无任何便签页面
5. 权限证据：seed 中 user 角色只有 4 个 note 权限，**无任何菜单**；浏览器管理后台侧边栏按角色菜单渲染 → 注册用户登录 → 空侧边栏 → "登录后什么都没有了"
6. 便签功能归属证据：新增/拖动/关闭/多便签 UI 全部在 `desktop_client/`（PyQt 桌面客户端），浏览器端不存在

## Root Cause Analysis (5-Why)
- 为什么"登录后什么都没有"：浏览器管理后台按角色渲染菜单，user 角色无菜单 → 空
- 为什么找不到便签按钮/不能贴便签：网页版根本没有便签 UI，便签功能只在桌面客户端
- 为什么界面难看：打开的是模板默认 SPA（未建设计），而非便签应用
- 为什么会出现这种错位：用户以"部署到 http/ssl 端口"的方式上线，期望浏览器访问即是便签应用；而项目按此前确认的方案把便签做成了 **PyQt 桌面客户端 + 浏览器仅作管理后台**
- Root cause：**需求-交付面错位** —— 缺少"网页版便签应用"这一交付面

## Key Evidence
- `curl http://10.74.64.100:18000/` → `HTTP 200`，`<title>安全管控平台</title>`，引 /css/app.css /js/api.js /js/ui.js
- `ss -tlnp` → `*:18000 ... ("uvicorn",pid=182261)`；`/proc/182261/cwd -> .../sticky-notes/backend`
- `git log`（服务器）= `91eebfb ...`（= 交付 master）
- `backend/main.py`：`app.mount("/", StaticFiles(directory=frontend/static, html=True))`
- `seed.py`：`ROLE_PERMISSION_CODES["user"] = [note:list/create/update/delete]`，INIT_MENUS 不含 user 可用的便签菜单

## Confidence Assessment
- 置信度：**High（~95%）**
- 证据项：`api_response`（curl 首页 HTML，30%）+ `reproduction`（端口/进程/版本全部复现，35%）+ `code_trace`（main.py 挂载 + seed 权限，35%）
- 未验证项：浏览器登录后的具体空白渲染样式（不影响根因结论）

## Fix Recommendation
给「网页版」补齐便签应用交付面，使其成为浏览器访问的首屏/登录后主界面：
1. 在 `frontend/static` 新增/改造为便签 Web 应用（登录/注册 → 便签桌）
2. 复用现有 `/api/v1/auth/login|register|captcha` 与 `/api/v1/notes`（后端已按 owner 隔离，#6 天然满足）
3. 便签桌：卡片「＋ 新建便签」按钮、卡片内输入标题/内容、卡片可拖动贴屏、可关闭、可贴多张（#3/#4/#5）
4. 界面整体美化（响应现有 token/owner 语义）（#1）
5. 管理后台保留入口（如 /admin 或菜单/角色内可见），供 admin 管理用户角色菜单
6. 说明：桌面 `desktop_client/`（PyQt）仍可用，指向 base_url=http://10.74.64.100:18000

## Prevention Recommendation
- 交付对外的"产品首页"应从"浏览器打开就是便签应用"出发设计；桌面客户端作为补充而非唯一向
- 部署验收应含「浏览器端到端」用例（登录→贴签→隔离），而非仅桌面端
