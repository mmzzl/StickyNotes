"""便签桌主窗口 + 托盘 + 各贴纸便签窗口的管理。"""
import os
import sys
import threading

import webview

import tray as tray_mod
from note_window import NoteWindow
from sticky_log import get_logger

log = get_logger("desk")

_WEB_DIR = os.path.join(
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "web")

PAGE_SIZE = 200


def _web_file(name):
    return os.path.join(_WEB_DIR, name)


class DeskApi:
    """便签桌页面可调的接口。"""

    def __init__(self, desk):
        self._d = desk

    def list_notes(self, q="", page=1, page_size=PAGE_SIZE):
        items, total = self._d.fetch(q, page, page_size)
        return {"items": items, "total": total}

    def open_note(self, note_id):
        self._d.open_note_id(note_id)
        return ""

    def new_note(self):
        self._d.new_note()
        return ""

    def restore_all(self):
        self._d.restore_all()
        return ""

    def quit_app(self):
        self._d.quit_app()
        return ""


class Desk:
    def __init__(self, client):
        self.client = client
        self.windows = {}          # note_id -> NoteWindow
        self._items = {}           # note_id -> note dict（列表缓存）
        self._lock = threading.Lock()

        self.api = DeskApi(self)
        self.window = webview.create_window(
            title="便签桌",
            url=_web_file("desk.html"),
            js_api=self.api,
            width=560, height=480,
            min_size=(400, 320),
            resizable=True,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        self.tray = tray_mod.create_tray(self)
        log.info("便签桌已创建 用户=%s", client.username)

    def _on_loaded(self):
        try:
            self.window.evaluate_js(
                "window.pywebviewStart(%s)" % _js(self.client.username or ""))
        except Exception as e:  # noqa: BLE001
            log.warning("便签桌初始化失败：%s", e)

    def _on_closing(self):
        """关窗不退出：收进托盘（托盘不可用时直接退出，否则窗口没了就没法再打开）。"""
        if self.tray is not None:
            self.window.hide()
            return False
        return True

    # ---- 数据 ----
    def fetch(self, q="", page=1, page_size=PAGE_SIZE):
        try:
            items, total = self.client.list_notes(q=q, page=page, page_size=page_size)
        except Exception as e:  # noqa: BLE001
            log.warning("加载便签失败：%s", e)
            raise
        with self._lock:
            for n in items:
                self._items[n.get("id")] = n
        return items, total

    # ---- 便签操作 ----
    def new_note(self):
        try:
            note = self.client.create_note()
        except Exception as e:  # noqa: BLE001
            log.warning("新建便签失败：%s", e)
            return None
        self.spawn(note)
        self.refresh_desk()
        return note

    def open_note_id(self, note_id):
        w = self.windows.get(note_id)
        if w is not None:
            w.raise_()
            return
        note = self._items.get(note_id)
        if note is None:
            try:
                items, _ = self.client.list_notes(page=1, page_size=PAGE_SIZE)
            except Exception:  # noqa: BLE001
                return
            for n in items:
                self._items[n.get("id")] = n
            note = self._items.get(note_id)
        if note is not None:
            self.spawn(note)

    def restore_all(self):
        try:
            items, _ = self.client.list_notes(page=1, page_size=PAGE_SIZE)
        except Exception as e:  # noqa: BLE001
            log.warning("恢复全部失败：%s", e)
            return
        for n in items:
            self._items[n.get("id")] = n
            self.spawn(n)

    def spawn(self, note):
        nid = note.get("id")
        if nid in self.windows:
            self.windows[nid].raise_()
            return
        w = NoteWindow(self.client, note,
                       on_need_close=self._drop,
                       on_changed=self._note_changed)
        self.windows[nid] = w

    def _drop(self, w):
        nid = w.note.get("id")
        self.windows.pop(nid, None)
        self.refresh_desk()

    def _note_changed(self, note):
        with self._lock:
            self._items[note.get("id")] = note
        self.refresh_desk()

    def refresh_desk(self):
        try:
            self.window.evaluate_js("window.pywebviewRefresh && window.pywebviewRefresh()")
        except Exception:  # noqa: BLE001
            pass

    # ---- 生命周期 ----
    def quit_app(self):
        """真正退出：先撤掉托盘，再关所有窗口。

        _on_closing 在托盘存在时只会 hide() 并取消关闭，所以必须先清空 self.tray，
        否则便签桌窗口不会被真正销毁，pywebview 的消息循环（winforms 下靠
        Application.Exit）就不会返回，进程会残留在任务管理器里。
        """
        tray = self.tray
        self.tray = None
        tray_mod.stop(tray)
        for w in list(self.windows.values()):
            try:
                w.request_close()
            except Exception as e:  # noqa: BLE001
                log.warning("关闭便签窗失败：%s", e)
        try:
            self.window.destroy()
        except Exception as e:  # noqa: BLE001
            log.warning("销毁便签桌失败：%s", e)
        log.info("已退出")


def _js(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
