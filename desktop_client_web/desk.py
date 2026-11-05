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
# 每张贴纸窗 = 一个独立 WebView 进程。一次性恢复几十个会串行冷启动、把 GUI 线程
# 堵死，这是「打开窗口慢」的主因。默认最多恢复 8 张，其余在便签桌列表双击按需打开。
MAX_RESTORE_WINDOWS = 8


def _web_file(name):
    return os.path.join(_WEB_DIR, name)


class DeskApi:
    """便签桌页面可调的接口。"""

    def __init__(self, desk):
        self._d = desk

    def list_notes(self, q="", page=1, page_size=PAGE_SIZE):
        # 未登录时便签桌窗口只是隐藏预建（占住 master 窗口），不能去打后端
        if not self._d.active:
            return {"items": [], "total": 0}
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

    # ---- 办公：备份 / 迁移 ----
    def export_backup(self):
        return self._d.export_backup()

    def import_backup(self):
        return self._d.import_backup()

    def quit_app(self):
        self._d.quit_app()
        return ""


class Desk:
    def __init__(self, client, hidden=False, start_tray=True):
        """主线程创建窗口。hidden=True 表示「登录前预建」：只占住 master 窗口位，
        不显示、不拉数据、不启动托盘，等 activate() 再激活。

        绝不要在 js_api 回调线程里执行本构造函数：winforms 会把它当成 master
        窗口重建并调用 signal.signal(SIGINT)，只允许主线程调用，见 app.py 说明。
        """
        self.client = client
        self.windows = {}          # note_id -> NoteWindow
        self._items = {}           # note_id -> note dict（列表缓存）
        self._lock = threading.Lock()
        self.active = not hidden   # 是否已完成登录、可以正常访问后端

        self.api = DeskApi(self)
        self.window = webview.create_window(
            title="便签桌",
            url=_web_file("desk.html"),
            js_api=self.api,
            width=560, height=480,
            min_size=(400, 320),
            resizable=True,
            hidden=hidden,
        )
        self.window.events.loaded += self._on_loaded
        self.window.events.closing += self._on_closing
        self.tray = tray_mod.create_tray(self) if start_tray else None
        log.info("便签桌已创建 用户=%s 隐藏=%s", client.username, hidden)

    def activate(self):
        """登录成功后激活便签桌。由 js_api 线程调用，只操作已存在的窗口。

        show() / evaluate_js() 在 winforms 下内部走 Form.Invoke，跨线程安全；
        这里刻意不调用 webview.create_window。
        """
        self.active = True
        self.window.show()
        if self.tray is None:
            self.tray = tray_mod.create_tray(self)
        try:
            self.window.evaluate_js(
                "window.pywebviewStart(%s)" % _js(self.client.username or ""))
        except Exception as e:  # noqa: BLE001
            log.warning("便签桌初始化失败：%s", e)
        self.refresh_desk()
        log.info("便签桌已激活 用户=%s", self.client.username)

    def _on_loaded(self):
        if not self.active:
            return                 # 隐藏预建窗口：未登录，不初始化页面也不请求
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
        """按需恢复：只创建有限个贴纸窗，避免 N 个 WebView 进程串行冷启动卡死界面。"""
        try:
            items, _ = self.client.list_notes(page=1, page_size=PAGE_SIZE)
        except Exception as e:  # noqa: BLE001
            log.warning("恢复全部失败：%s", e)
            return "恢复失败：%s" % e
        for n in items:
            self._items[n.get("id")] = n

        targets = [n for n in items if n.get("id") not in self.windows]
        opened = 0
        for n in targets[:MAX_RESTORE_WINDOWS]:
            self.spawn(n)
            opened += 1
        rest = max(0, len(targets) - opened)
        log.info("恢复便签窗 %d 个（共 %d 张，%d 张留在列表按需打开）",
                 opened, len(items), rest)
        if rest:
            return "已恢复 %d 张，其余 %d 张可在列表中双击打开" % (opened, rest)
        return "已恢复 %d 张" % opened

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

    # ---- 办公：整机备份 / 迁移（复用后端 /notes/export、/notes/import） ----
    def _dialog(self, kind, **kw):
        """统一文件对话框：WinForms 返回 tuple，GTK 返回 str。"""
        path = self.window.create_file_dialog(kind, **kw)
        if isinstance(path, (tuple, list)):
            path = path[0] if path else ""
        return path or ""

    def export_backup(self):
        import json

        import webview

        try:
            data = self.client.export_notes() or {}
        except Exception as e:  # noqa: BLE001
            log.warning("导出备份失败：%s", e)
            return "导出失败：%s" % e
        try:
            path = self._dialog(webview.SAVE_DIALOG,
                                save_filename="sticky-notes-backup.json",
                                file_types=("JSON (*.json)", "All files (*.*)"))
            if not path:
                return ""
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            log.info("已导出 %d 条便签到 %s", data.get("count", 0), path)
            return "已导出 %d 条便签" % data.get("count", 0)
        except Exception as e:  # noqa: BLE001
            log.warning("写备份文件失败：%s", e)
            return "导出失败：%s" % e

    def import_backup(self):
        import json

        import webview

        try:
            path = self._dialog(webview.OPEN_DIALOG,
                                file_types=("JSON (*.json)", "All files (*.*)"))
            if not path:
                return ""
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("notes") if isinstance(data, dict) else data
            if not isinstance(items, list):
                return "备份格式不正确"
            result = self.client.import_notes(items) or {}
            self.refresh_desk()
            msg = "已导入 %d 条，跳过 %d 条" % (result.get("imported", 0),
                                              result.get("skipped", 0))
            log.info("导入备份：%s（%s）", msg, path)
            return msg
        except Exception as e:  # noqa: BLE001
            log.warning("导入备份失败：%s", e)
            return "导入失败：%s" % e

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
