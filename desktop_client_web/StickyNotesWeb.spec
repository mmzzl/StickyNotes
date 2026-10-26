# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（pywebview 版）。

与 PyQt5 版的根本区别：这里**不打包任何浏览器内核**。
- Linux：WebKitGTK 由系统提供（gi + WebKit2 typelib），产物里只有 Python 代码和网页资源
- Windows：走系统自带的 WebView2 Runtime（Win10/11 一般预装）

对比旧版 StickyNotes.spec：那边必须 hiddenimports QtWebEngineWidgets，并把整个
QtWebEngineProcess + Chromium 资源打进包，产物动辄 200MB+。

打包：pyinstaller StickyNotesWeb.spec
"""
import os
import sys

block_cipher = None

_HERE = os.path.abspath(".")
_WEB = os.path.join(_HERE, "web")

datas = [
    (os.path.join(_WEB, "note.html"), "web"),
    (os.path.join(_WEB, "note.js"), "web"),
    (os.path.join(_WEB, "editor.css"), "web"),
    (os.path.join(_WEB, "desk.html"), "web"),
    (os.path.join(_WEB, "login.html"), "web"),
    (os.path.join(_WEB, "marked.min.js"), "web"),
    (os.path.join(_WEB, "highlight.pack.js"), "web"),
    (os.path.join(_WEB, "turndown.min.js"), "web"),
    (os.path.join(_WEB, "turndown-plugin-gfm.min.js"), "web"),
]

hiddenimports = [
    "webview.platforms.gtk",     # Linux 后端
    "webview.platforms.edgechromium",   # Windows 后端（在 Linux 上打包时忽略即可）
]

a = Analysis(
    ["app.py"],
    pathex=[_HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 明确剔除：这些是旧版体积的主要来源
        "PyQt5", "PyQt5.QtWebEngineWidgets", "PyQtWebEngine",
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# ---- 剔除系统库 ----
# PyInstaller 会顺着 gi -> Gtk/WebKit 的依赖把整条系统库链打进来（实测 138 个 .so、
# 99.6 MB，最大的是 libicudata 29.4MB / libgtk-3 7.8MB / librsvg 6.5MB）。
# 这些库在目标机器上由 apt 装好，打进来既浪费体积又可能和系统版本冲突。
# hook-gi.py 对 gi.repository 的子 hook 拦不住，所以在 Analysis 结果上直接过滤。
_SYS_PREFIXES = (
    "/lib/x86_64-linux-gnu/",
    "/usr/lib/x86_64-linux-gnu/",
)
# 需要保留的：Python 自身运行时和 pywebview/pystray 真正依赖的少数库由
# PyInstaller 的 Python 收集逻辑保证，这里只剔除 GUI/加密相关的系统组件。
_KEEP = ("libpython", "libffi")

_removed = []
_kept_binaries = []
for _dest, _src, _kind in a.binaries:
    _is_sys = any(_src.startswith(p) for p in _SYS_PREFIXES)
    _should_keep = any(k in os.path.basename(_src) for k in _KEEP)
    if _is_sys and not _should_keep:
        _removed.append(_src)
    else:
        _kept_binaries.append((_dest, _src, _kind))
a.binaries = _kept_binaries
print("[spec] 剔除系统库 %d 个" % len(_removed))

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="StickyNotesWeb",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
