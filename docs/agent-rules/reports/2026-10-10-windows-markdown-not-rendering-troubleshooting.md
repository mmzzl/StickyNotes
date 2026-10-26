# Troubleshooting Report — Windows 端 Markdown 排版失效

## Problem Summary
- Fault phenomenon: Windows 打包版便签点击「排版 / 源码」后 Markdown 不渲染，正文只显示原始 Markdown 文本（如 `## 标题`、`**bold**` 原样出现），不产生任何 HTML 结构；Ubuntu 端表现正常。
- Impact scope: Windows 打包版（PyInstaller onefile）全部便签的排版视图。
- Duration: 打包版在 Windows 上一直如此（非偶发）。
- Classification: Configuration / Integration（运行时 JS 引擎能力差异）。
- Environment: Windows 10 Pro 19044；Python 3.8.7；PyQt5 5.15.11；Qt 5.15.2；QtWebEngine 5.15.2 = **Chromium 83.0.4103.122**。Ubuntu 端使用系统 `python3-pyqt5.qtwebengine`（Chromium 版本更高）。

## 5W1H
- What: 期望 Markdown 渲染成标题/列表/代码块；实际只有原始文本，切「排版」看不到任何排版。
- When: Windows 打包版一直如此，稳定复现。
- Where: `desktop_client/md_editor.py` 的 QWebEngineView 排版视图，`window.__setMarkdown`。
- Who: Windows 端全部用户。
- Why: 直接原因是 `marked` 未定义（见下）。
- How: 运行打包版 / 直接构造 md_editor 的 HTML 到 Windows QtWebEngine 即可复现。

## Investigation Process
1. 阅读 `md_editor.py`、`StickyNotes.spec`、`build_exe.bat`、`note_window.py`、`config.py`、`sticky_log.py`。
2. 核对打包产物：`vendor\marked.min.js` / `turndown.min.js` / `turndown-plugin-gfm.min.js` 均已打进 `PKG-00.toc`；HTML 构建结果 54626 字节，与日志一致。**排除"文件缺失/未打包"假设。**
3. 读取用户真实日志 `C:\Users\User\.config\sticky_notes\sticky-notes.log`：
   - `诊断 ... 页面文字=196字 子节点=0 ... 可滚动=0`
   - **子节点=0** 说明渲染出的 HTML 无任何元素；这是 `__setMarkdown` 抛异常、回退到 `editor.textContent = md` 的特征。
4. 在 Windows 本机用真实 QtWebEngine 复现（临时脚本，已删除）：
   - `typeof marked = 'undefined'`，而 `typeof TurndownService = 'function'`、`typeof turndownPluginGfm = 'object'` → **只有 marked 未定义**。
   - `marked.parse(...)` → `THROW: marked is not defined`。
   - `document.scripts[0].textContent` 与磁盘文件**逐字节一致（35479 字符）** → 排除 HTML 注入/截断/编码污染。
   - 对 `document.scripts[0]` 源码 `new Function(src)` → `SyntaxError: Unexpected token '('`，且**不经 HTML、直接 `new Function(磁盘原文)` 同样报错** → 问题在 JS 源码本身被该引擎拒绝。
5. 定位最小语法：`class A{#x(){...}}`（私有类**方法**）在本引擎报 `Unexpected token '('`，而私有字段（`#y=2`）、对象展开（`{...x}`）、可选链、空值合并均 OK。
6. 在 `marked.min.js` 中确认：offset 33350 `}#e(e,t){...}`、offset 34402 `}#t(e,t){...}` 为私有类方法；`turndown*.js` 无私有方法。
7. 验证修复机制：把 `.#e(`→`.hx(`、`}#e(`→`}hx(`、`.#t(`→`.ht(`、`}#t(`→`}ht(` 后，`new Function` 返回 `FIXED_OK`。

## Root Cause Analysis (5-Why)
- Why1: 排版视图显示原始 Markdown 而非渲染结果 → `marked` 未定义，`__setMarkdown` 抛错并回退到 `textContent`。
- Why2: 为什么 `marked` 未定义 → `marked.min.js` 整个脚本解析失败（SyntaxError），未执行到赋值。
- Why3: 为什么会解析失败 → 该脚本使用**私有类方法**（`#e(){}` / `#t(){}`）。
- Why4: 为什么私有方法会失败 → 私有类方法需 **Chromium 84+**，Windows 端 **Qt 5.15.2 = Chromium 83** 不支持。
- Why5（根因）：打包端依赖的 Qt/Chromium 运行时版本过旧，而 vendor 里的 `marked v12.0.2` 用了该版本不支持的语法；Ubuntu 端因系统 Chromium 更新而"碰巧"可用，掩盖了兼容性问题。
- **Root cause: vendor JS 库（marked v12）使用了运行时 Chromium（83）不支持的私有类方法语法，导致 marked 解析失败、Markdown 无法渲染。这是版本兼容性问题，与打包是否包含 vendor 无关。**

## Key Evidence
- 用户日志（原文）：
  `诊断 视口=352x301 可见=True 文档高=301 视口高=301 可滚动=0 scrollY=0 页面文字=196字 子节点=0 底色=rgb(255, 249, 196) 滚动条宽=0(overlay，滚动条不可见) 滚轮记录=[]`
- 本机复现（原文）：
  - `typeof marked = 'undefined'`
  - `marked.parse -> 'THROW:marked is not defined'`
  - `rerun marked -> "RERUN-THROW: SyntaxError: Unexpected token ('"`
  - `new Function(SRC): ERR:Unexpected token '('`
  - `private_method -> ERR:Unexpected token '('` / `private_field -> OK:2` / `obj_spread -> OK:1`
  - 修复后：`fixed marked compile: FIXED_OK`
- 引擎版本：`QtWebEngine/5.15.2 Chrome/83.0.4103.122`
- marked 版本头：`marked v12.0.2`
- 私有方法位置：offset 33350 `}#e(e,t){`、offset 34402 `}#t(e,t){`

## Confidence Assessment
- Current confidence: **High (≈95%)**
- Evidence items:
  - `reproduction`（35%）本机稳定复现并在页面内读回状态
  - `code_defect`（40%）定位到私有类方法这一确定语法
  - `log_exact_match`（30%）用户日志 `子节点=0` 与回退路径吻合
  - `code_trace`（35%）marked 未定义 → parse 抛错 → 回退 textContent 的链路已实测
  - 控制实验（私有方法 vs 私有字段 vs 展开）排除了其他语法假设
- Unverified items: 修复后需在 Windows 打包版上端到端确认排版生效（本机已用未打包环境验证编译与机制，打包版待回归）。

## Fix Recommendation
（按优先级，可选其一，建议 1）
1. **降级 / 替换 vendor 的 marked 为不含私有类方法的版本**（如 marked v4.x 或 v9.x，或对 v12 做语法降级），保持 `marked.parse` API 不变，最小改动。
2. 若无网络来源可控：对现有 `vendor/marked.min.js` 做等价改写，把 `#e`/`#t` 私有方法改为普通方法名（已实测可通过解析）。
3. 长期方案：统一 Windows/Ubuntu 运行时的 QtWebEngine/Chromium 版本（升到 Chromium ≥84），消除平台差异。

## Prevention Recommendation
- 引入 vendor JS 时固定并记录其**最低浏览器/Chromium 版本**要求；打包前用一个"目标引擎最小语法自检"步骤（对 vendor JS 跑一次 `new Function`）拦截不兼容语法。
- 在打包 CI 里固定 Windows 端 QtWebEngine 版本，并加一条"排版视图 smoke test"（加载后断言 `typeof marked === 'function'`）。
- 避免 Ubuntu 端因系统 Chromium 更新而"看起来正常"造成的假阳性——打包端应与目标运行端一致验证。

## Fix Record
- 采用方案：**最小修复**——把 `desktop_client/vendor/marked.min.js` 从 **v12.0.2 降级到 v5.1.2**（无私有类方法的最新版本，实测私有方法计数=0），保持 `marked.parse` API 不变，`md_editor.py` 无需改调用方式。
  - 备选（用户已评估后放弃）：升级运行时 Chromium。因内部镜像 `PyQtWebEngine-Qt5` 仅有 5.15.2（Chromium 83），升级必须迁移 PyQt6，属全客户端破坏性 API 变更，成本/风险过大。
- 附带改动：
  - `desktop_client/md_editor.py`：`__setMarkdown` 回退分支增加致命告警（`marked` 未定义时明确提示 vendor 与 Chromium 不兼容）；`_Page.javaScriptConsoleMessage` 对含"致命"的日志抬到 warning，避免此类问题再次静默。
  - 新增 `desktop_client/vendor/README.md`：记录 marked 版本约束（不得在 Chromium<84 时升级到 v7+）与升级前自检方法。
- Changed files:
  - `desktop_client/vendor/marked.min.js`（v12.0.2 → v5.1.2）
  - `desktop_client/md_editor.py`（告警日志）
  - `desktop_client/vendor/README.md`（新增）
  - `docs/agent-rules/reports/2026-10-10-windows-markdown-not-rendering-troubleshooting.md`（本报告）
- Bug scenario test: **pass** —— 未打包 Windows 环境（QtWebEngine/Chromium 83）加载真实 `MarkdownEditor`：`diag 子节点 = 4`（修复前为 0），innerHTML 含 `<h1>/<ul>/<li>/<strong>/<code>` 结构；`typeof marked = 'object'`。
- Regression test: **pass** —— turndown 回写往返正常，`window.__getMarkdown()` 输出合法 Markdown 结构（`#标题`/`- 列表`/`**加粗**`/`` `代码` ``）；`md_editor.py` 语法/编译 OK。
- Residual risks:
  - 本机验证在**未打包** Python 环境完成；打包版（PyInstaller onefile）需在 Windows 上重新打包并做一次人工 smoke test 确认（vendor 已随 `datas` 打入，理论上一致）。
  - 若未来升级到 PyQt6（Chromium≥84），可按 `vendor/README.md` 说明再将 marked 升回 v7+。
  - `turndown`/`turndown-plugin-gfm` 当前实测在 Chromium 83 上可正常加载，无需改动；但仍属同一类"vendor JS ↔ 引擎版本"约束，升级它们时需一并自检。
