# vendor/ —— 排版视图用的第三方 JS

这些文件由 `md_editor.py` 的 `_read_vendor()` 在运行时读入，拼进 QWebEngineView
的 HTML。**不能被 PyInstaller 漏掉**（见 `StickyNotes.spec` 的 `datas=[('vendor','vendor')]`）。

## 版本约束（重要，改动前先读）

| 文件 | 版本 | 备注 |
|------|------|------|
| `marked.min.js` | **5.1.2** | 见下，**不可随意升级** |
| `turndown.min.js` | 7.x | 回写 Markdown 用 |
| `turndown-plugin-gfm.min.js` | 1.0.2 | 表格/删除线等 GFM 支持 |
| `hljs.min.js` | **11.9.0**（common 包，35 语言） | 代码块语法高亮，见下 |
| `hljs-theme.css` | atom-one-dark（精简） | 高亮 token 配色，见下 |

### 为什么 marked 停在 5.1.2

Windows 打包端用的是 **Qt 5.15.2 → Chromium 83**（`PyQtWebEngine-Qt5==5.15.2`，
内部镜像上也只有这一个版本）。

**marked v7 起使用了私有类方法语法**（`class X{ #e(){} }`）。私有类方法需要
**Chromium 84+**，Chromium 83 不支持，会导致整个 `marked.min.js` **解析失败
（SyntaxError: Unexpected token '('）**，`marked` 从未定义。后果是排版视图
`__setMarkdown` 抛错、静默回退到 `editor.textContent = md`：用户看到的是
**原始 Markdown 文本、没有任何排版**（诊断日志表现为 `页面文字=N字 子节点=0`）。

各版本私有方法数量（实测）：

- v4.3.0 → 0
- **v5.1.2 → 0 ← 当前采用（无私有方法的最新版）**
- v7.0.5 → 有
- v9.1.6 → 有
- v12.0.2 → 有

Ubuntu 端系统 QtWebEngine 的 Chromium 更新，能用 v12，所以曾一度"看起来正常"，
掩盖了这个平台差异。

### 升级 marked 的前提

只有当 Windows 端 QtWebEngine 的 Chromium **≥84** 时，才能升级到 v7+。
当前唯一途径是迁移到 **PyQt6**（Qt 6.7 → Chromium 118+），但那是全客户端
破坏性 API 迁移。在此之前**请勿升级 marked**。

### 升级前自检

换 vendor JS 后，在 Windows 上跑一次最小语法自检，确认目标引擎能解析：

```python
# 在 QWebEngine 页面里执行：new Function(<新库源码>) 不抛异常即通过
```

或直接看诊断日志：切「排版」后 `子节点` 应 > 0（渲染出结构），而非 0。

### 关于 hljs.min.js（highlight.js 代码高亮）

`hljs.min.js` 由 `highlight.js@11.9.0` 打包而来，**必须**按下面的方式生成，
否则 Chromium 83 会踩坑：

```bash
# 打包入口必须用 lib/common.js（CJS，直接导出实例）
# 不能用 es/common.js —— 那会导出 {HighlightJS, default} 命名空间对象，
# 结果是 hljs.highlightElement 为 undefined（已踩过）。
esbuild package/lib/common.js --bundle --format=iife --global-name=hljs --target=chrome83 --minify
```

- **入口**：`lib/common.js`（含 35 种语言的 common 包）。
- **target**：`chrome83`，与 Windows 端 Qt 5.15.2 的 Chromium 83 对齐。
  实测打包产物里私有类方法计数 = 0，Chromium 83 可直接解析（并已在真实
  QtWebEngine/Chromium 83 上加载通过：`hljs.versionString` = '11.9.0'）。
- 高亮逻辑在 `md_editor.py` 的 `highlightAll()`，对 `pre code` 逐个
  `hljs.highlightElement()`（对已高亮元素会跳过，幂等）。

### 关于 hljs-theme.css（atom-one-dark 精简版）

取自 `highlight.js@11.9.0` 的 `styles/atom-one-dark.min.css`，**只保留逐 token 的
配色规则**（`.hljs-keyword` / `.hljs-string` / `.hljs-comment` 等）。

**不可覆盖代码块背景**：原主题里的三条"容器盒子"规则已删掉——

```
pre code.hljs{display:block;overflow-x:auto;padding:1em}
code.hljs{padding:3px 5px}
.hljs{color:#abb2bf;background:#282c34}
```

因为代码块的背景/内边距由 `md_editor.py` 的 `#editor pre` / `#editor pre code`
统一决定（背景 `#1e1e1e`）；主题再设一遍会打架，还会把内边距叠成两层。
基础正文色用现有代码块的 `#f0f0f0`，未命中任何 token 的字符也能在深色底上看清。

### hljs 升级前自检

与 marked 同理：换 `hljs.min.js` / `hljs-theme.css` 后，在 Windows（Chromium 83）
上切「排版」，确认代码块里出现彩色 token（`pre code` 下有带 `hljs-*` 类名的
`<span>`），且代码块背景仍是 `#1e1e1e`（`rgb(30,30,30)`）。
