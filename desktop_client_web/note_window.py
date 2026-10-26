"""贴纸式便签窗口（pywebview + 系统 WebView）。

与 PyQt5 版的对应关系：
- QFrame 无边框置顶窗      -> frameless=True + on_top=True + transparent=True
- QWebEngineView 内嵌 Chromium -> 系统 WebView（Linux: WebKitGTK / Win: WebView2）
- runJavaScript 轮询同步正文 -> 前端直接调 pywebview.api（js_api），无轮询
- QColorDialog             -> 由 Python 侧弹 Gtk 取色框（见 pick_color）
"""
import os
import sys
import threading
import time

import webview

import gtk_window
from config import load_view_modes, save_view_mode
from sticky_log import get_logger

log = get_logger("note")

_WEB_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "web")


def _web_file(name):
    return os.path.join(_WEB_DIR, name)


class NoteApi:
    """暴露给页面 JS 的接口对象（pywebview 会把公有方法挂到 window.pywebview.api）。"""

    def __init__(self, window_obj):
        self._w = window_obj
        self._save_timer = None
        self._lock = threading.Lock()

    # ---- 内容 ----
    def save_content(self, md):
        self._w.set_content(md or "")
        self._w.schedule_save()
        return ""

    def save_title(self, title):
        self._w.set_title(title or "")
        self._w.schedule_save()
        return ""

    def save_now(self, md):
        self._w.set_content(md or "")
        self._w.save_now()
        return ""

    def remember_mode(self, mode):
        save_view_mode(str(self._w.note.get("id") or ""),
                       "rich" if mode == "rich" else "source")
        return ""

    # ---- 颜色 ----
    def pick_color(self):
        return self._w.pick_color()

    # ---- 删除 / 关闭 ----
    def confirm_delete(self):
        self._w.confirm_delete()
        return ""

    def close_note(self):
        self._w.request_close()
        return ""


class NoteWindow:
    def __init__(self, client, note, on_need_close=None, on_changed=None):
        self.client = client
        self.note = note
        self.on_need_close = on_need_close
        self.on_changed = on_changed
        self._color = note.get("color") or "#fff9c4"
        self._content = note.get("content") or ""
        self._title = note.get("title") or ""
        self._save_timer = None
        self._closed = False
        self._born = time.time()

        self.api = NoteApi(self)
        mode = load_view_modes().get(str(note.get("id") or ""), "source")
        _setup_drag_region()

        self.window = webview.create_window(
            title="便签",
            url=_web_file("note.html"),
            js_api=self.api,
            width=380, height=400,
            min_size=(280, 220),
            x=int(note.get("pos_x") or 0) or None,
            y=int(note.get("pos_y") or 0) or None,
            frameless=True,
            # Windows/edgechromium 下 easy_drag 只让 .pywebview-drag-region（#bar）可拖，
            # 正文仍可选字；Linux 下它会变成整窗拖动（正文选不中），故交给 gtk_window 手工挂。
            easy_drag=(sys.platform == "win32"),
            on_top=True,
            transparent=True,
            background_color="#000000",   # 必须 6 位 hex；透明由 transparent=True 负责
            text_select=True,
            resizable=True,
            confirm_close=False,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.moved += self._on_moved
        self.window.events.closed += self._on_closed
        log.info("创建便签窗《%s》id=%s 模式=%s pos=%s,%s",
                 self._title, self.note.get("id"), mode,
                 note.get("pos_x"), note.get("pos_y"))

    # ---- 前端初始化 ----
    def _on_loaded(self):
        try:
            payload = {
                "title": self._title, "content": self._content,
                "color": self._color, "mode": "rich" if self._mode_rich() else "source",
            }
            self.window.evaluate_js("window.pywebviewInit(%s)" % _js(payload))
            native = getattr(self.window, "native", None)
            if native is not None:
                gtk_window.attach(native)
            log.info("便签 %s 页面加载完成 %dms 内容 %d 字",
                     self.note.get("id"), int((time.time() - self._born) * 1000),
                     len(self._content))
        except Exception as e:  # noqa: BLE001
            log.warning("前端初始化失败：%s", e)

    def _mode_rich(self):
        return load_view_modes().get(str(self.note.get("id") or ""), "source") == "rich"

    # ---- 状态写入 ----
    def set_content(self, md):
        self._content = md

    def set_title(self, title):
        self._title = title

    # ---- 保存 ----
    def schedule_save(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.6, self.save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def save_now(self):
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        pos = None
        native = getattr(self.window, "native", None)
        if native is not None:
            pos = gtk_window.get_position(native)
        try:
            fields = {"title": self._title, "content": self._content, "color": self._color}
            if pos:
                fields["pos_x"], fields["pos_y"] = int(pos[0]), int(pos[1])
            data = self.client.update_note(self.note["id"], **fields)
            if isinstance(data, dict):
                self.note = data
            if self.on_changed:
                self.on_changed(self.note)
        except Exception:  # noqa: BLE001
            pass    # 离线等场景静默，待下次编辑再同步

    def _on_moved(self, *_a):
        self.schedule_save()

    # ---- 颜色 ----
    def pick_color(self):
        """弹系统取色框（Windows 原生 / Linux GTK）。"""
        color = _pick_color_win(self._color) if sys.platform == "win32" \
            else _pick_color_gtk(self._color)
        if color:
            self._color = color
            self.save_now()
        return color or ""

    # ---- 删除 / 关闭 ----
    def confirm_delete(self):
        if _confirm("确定删除这张便签？删除后不可恢复。"):
            try:
                self.client.delete_note(self.note["id"])
            finally:
                self.request_close()

    def request_close(self):
        try:
            self.window.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _on_closed(self):
        if self._closed:
            return
        self._closed = True
        self.save_now()
        log.info("便签 %s 关闭（存活 %dms）", self.note.get("id"),
                 int((time.time() - self._born) * 1000))
        if self.on_need_close:
            self.on_need_close(self)

    def show(self):
        pass    # create_window 之后由 webview.start 统一显示

    def raise_(self):
        try:
            self.window.restore()
        except Exception:  # noqa: BLE001
            pass


def _setup_drag_region():
    """Windows 下让拖动只认 .pywebview-drag-region（#bar 及其子元素），
    避免 easy_drag 把整窗都变成拖动区、正文选不中文字。

    Linux 走 gtk_window 手工挂标题栏拖动，不需要这个开关。
    """
    if sys.platform == "win32":
        try:
            import webview as _wv
            _wv.settings['DRAG_REGION_DIRECT_TARGET_ONLY'] = True
        except Exception as e:  # noqa: BLE001  # pragma: no cover
            log.warning("设置拖拽区域失败：%s", e)


def _confirm(message, title="删除便签"):
    """跨平台确认框（pywebview 没有原生 confirm）。

    Windows 用 user32.MessageBoxW；Linux 用 Gtk.MessageDialog。
    两者都不可用时才退回 False，调用方据此不执行删除。
    """
    if sys.platform == "win32":
        return _confirm_win(message, title)
    return _confirm_gtk(message, title)


def _confirm_win(message, title):
    """Windows 原生消息框：MB_YESNO(4) | MB_ICONWARNING(0x30)，YES=6。"""
    try:
        import ctypes
        MB_YESNO = 0x04
        MB_ICONWARNING = 0x30
        res = ctypes.windll.user32.MessageBoxW(
            None, message, title, MB_YESNO | MB_ICONWARNING)
        return res == 6
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        log.warning("Windows 确认框失败：%s", e)
        return False


def _confirm_gtk(message, title):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dlg = Gtk.MessageDialog(
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=message)
        dlg.set_title(title)
        resp = dlg.run()
        dlg.destroy()
        return resp == Gtk.ResponseType.YES
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        log.warning("GTK 确认框失败：%s", e)
        return False


def _pick_color_gtk(current):
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dlg = Gtk.ColorChooserDialog(title="选择便签颜色")
        dlg.set_use_alpha(False)
        try:
            from gi.repository import Gdk
            dlg.set_rgba(Gdk.RGBA(*_hex_to_rgba(current)))
        except Exception:  # noqa: BLE001
            pass
        resp = dlg.run()
        color = None
        if resp == Gtk.ResponseType.OK:
            rgba = dlg.get_rgba()
            color = "#%02x%02x%02x" % (
                int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        dlg.destroy()
        return color or ""
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        log.warning("GTK 取色失败：%s", e)
        return ""


def _pick_color_win(current):
    """Windows 原生取色框：comdlg32.ChooseColorW。

    COLORREF 是 0x00BBGGRR（BGR 顺序），与 #RRGGBB 需要互换红蓝。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class CHOOSECOLORW(ctypes.Structure):
            _fields_ = [
                ("lStructSize", wintypes.DWORD),
                ("hwndOwner", wintypes.HWND),
                ("hInstance", wintypes.HWND),
                ("rgbResult", wintypes.DWORD),
                ("lpCustColors", ctypes.POINTER(wintypes.DWORD)),
                ("Flags", wintypes.DWORD),
                ("lCustData", ctypes.c_void_p),
                ("lpfnHook", ctypes.c_void_p),
                ("lpTemplateName", wintypes.LPCWSTR),
            ]

        def hex_to_cref(h):
            h = (h or "").lstrip("#")
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            r, g, b = (int(h[i:i + 2], 16) if len(h) == 6 else 0 for i in (0, 2, 4))
            return (b << 16) | (g << 8) | r

        def cref_to_hex(v):
            return "#%02x%02x%02x" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)

        cust = (wintypes.DWORD * 16)()
        cc = CHOOSECOLORW()
        cc.lStructSize = ctypes.sizeof(CHOOSECOLORW)
        cc.hwndOwner = None
        cc.rgbResult = hex_to_cref(current)
        cc.lpCustColors = cust
        cc.Flags = 0x00000001 | 0x00000004   # CC_RGBINIT | CC_FULLOPEN
        if not ctypes.windll.comdlg32.ChooseColorW(ctypes.byref(cc)):
            return ""
        return cref_to_hex(cc.rgbResult)
    except Exception as e:  # noqa: BLE001  # pragma: no cover
        log.warning("Windows 取色失败：%s", e)
        return ""


def _hex_to_rgba(color):
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (r, g, b, 1.0)


def _js(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
