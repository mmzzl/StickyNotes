"""便签客户端（pywebview 版）回归测试。

无显示环境下 GTK/pywebview 起不来，所以这里把 webview 换成替身，
只验证与 GUI 无关、但历史上真出过 bug 的那部分逻辑：
- NoteWindow 的内容/标题状态写入与保存调用
- Desk 的列表、新建、打开去重
- 前端资源完整性（marked/turndown/vendor 齐全、HTML 里引用的文件都在）

运行：../.venv/bin/python -m pytest tests/ -q
"""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)


def _web_dir():
    """web/ 的真实路径。用 __file__ 推导，不依赖当前工作目录——
    dispatch 到 tests/ 下再跑时 cwd 可能不同，写死相对路径会误判。"""
    return os.path.join(_HERE, "web")


class _FakeEvents:
    def __init__(self):
        self.loaded = _Sig()
        self.moved = _Sig()
        self.closed = _Sig()
        self.closing = _Sig()


class _Sig:
    """模拟 pywebview 的事件容器：`+=` 注册回调。"""
    def __init__(self):
        self._handlers = []

    def __iadd__(self, fn):
        self._handlers.append(fn)
        return self

    def fire(self, *a):
        for fn in self._handlers:
            fn(*a)


class _FakeWindow:
    last = None

    def __init__(self, **kw):
        _FakeWindow.last = self
        self.kw = kw
        self.events = _FakeEvents()
        self.native = None
        self.destroyed = False
        self.js_calls = []

    def evaluate_js(self, code):
        self.js_calls.append(code)

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()

    def show(self):
        pass

    def hide(self):
        pass

    def restore(self):
        pass


class _FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, **kw):
        w = _FakeWindow(**kw)
        self.windows.append(w)
        return w


class _FakeClient:
    def __init__(self):
        self.base_url = "http://127.0.0.1:1"
        self.username = "tester"
        self.access_token = "tok"
        self.notes = {}
        self.on_auth_failed = None
        self.updates = []
        self.deletes = []

    def list_notes(self, q="", page=1, page_size=200):
        items = sorted(self.notes.values(), key=lambda n: n.get("id"))
        return items, len(items)

    def create_note(self, **kw):
        nid = "n%d" % (len(self.notes) + 1)
        n = {"id": nid, "title": "", "content": "", "color": "#fff9c4",
             "pos_x": 0, "pos_y": 0}
        self.notes[nid] = n
        return n

    def update_note(self, nid, **fields):
        self.updates.append((nid, dict(fields)))
        self.notes[nid].update(fields)
        return self.notes[nid]

    def delete_note(self, nid):
        self.deletes.append(nid)
        self.notes.pop(nid, None)
        return {}


def setUpModule():
    import webview
    import note_window
    import desk
    global _fake
    _fake = _FakeWebview()
    webview.create_window = _fake.create_window
    note_window.webview = webview
    desk.webview = webview
    for mod in (note_window, desk):
        mod._WEB_DIR = os.path.join(_HERE, "web")


class NoteWindowStateTest(unittest.TestCase):
    def setUp(self):
        import note_window
        self.mod = note_window
        self.td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.td.name
        self.client = _FakeClient()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.td.cleanup()

    def test_set_content_and_title(self):
        note = {"id": "n1", "title": "T", "content": "第一行\n第二行",
                "color": "#fff9c4", "pos_x": 10, "pos_y": 20}
        w = self.mod.NoteWindow(self.client, note)
        self.assertEqual(w._content, "第一行\n第二行")
        w.set_content("# 新内容")
        w.set_title("新标题")
        self.assertEqual(w._content, "# 新内容")
        self.assertEqual(w._title, "新标题")

    def test_save_writes_through_client(self):
        note = {"id": "n1", "title": "", "content": "x",
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        self.client.notes["n1"] = dict(note)
        w = self.mod.NoteWindow(self.client, note)
        w.set_content("保存我")
        w.save_now()
        nid, fields = self.client.updates[-1]
        self.assertEqual(nid, "n1")
        self.assertEqual(fields["content"], "保存我")

    def test_delete_calls_client(self):
        note = {"id": "n1", "title": "", "content": "",
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        self.client.notes["n1"] = dict(note)
        w = self.mod.NoteWindow(self.client, note)
        self.mod._confirm = lambda _m: True
        w.confirm_delete()
        self.assertIn("n1", self.client.deletes)

    def test_newlines_preserved(self):
        """回归：重开便签不能把换行塌掉（原 Qt 版用 HTML 解析踩过这个坑）。"""
        raw = "第一行\n第二行\n第三行"
        note = {"id": "n2", "title": "多行", "content": raw,
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        w = self.mod.NoteWindow(self.client, note)
        self.assertEqual(w._content, raw)


class DeskTest(unittest.TestCase):
    def setUp(self):
        import desk
        self.mod = desk
        self.td = tempfile.TemporaryDirectory()
        self._old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.td.name
        self.client = _FakeClient()
        self.client.notes["a"] = {"id": "a", "title": "A", "content": "ca",
                                  "color": "#fff9c4", "pos_x": 0, "pos_y": 0}

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._old
        self.td.cleanup()

    def test_list_notes(self):
        d = self.mod.Desk(self.client)
        items, total = d.fetch()
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "a")

    def test_new_note_spawns_window(self):
        d = self.mod.Desk(self.client)
        note = d.new_note()
        self.assertIsNotNone(note)
        self.assertIn(note["id"], d.windows)

    def test_open_same_note_twice_dedupes(self):
        d = self.mod.Desk(self.client)
        d.fetch()
        d.open_note_id("a")
        d.open_note_id("a")
        self.assertEqual(len([k for k in d.windows if k == "a"]), 1)

    def test_drop_removes_window(self):
        d = self.mod.Desk(self.client)
        d.fetch()
        d.open_note_id("a")
        w = d.windows["a"]
        d._drop(w)
        self.assertNotIn("a", d.windows)


class FrontendAssetsTest(unittest.TestCase):
    def test_vendor_files_present(self):
        web = _web_dir()
        for name in ("marked.min.js", "turndown.min.js",
                     "turndown-plugin-gfm.min.js"):
            self.assertTrue(os.path.exists(os.path.join(web, name)), name)

    def test_web_files_present(self):
        web = _web_dir()
        for name in ("note.html", "note.js", "editor.css",
                     "desk.html", "login.html"):
            self.assertTrue(os.path.exists(os.path.join(web, name)), name)

    def test_note_html_references_existing_files(self):
        import re
        web = _web_dir()
        with open(os.path.join(web, "note.html"), encoding="utf-8") as f:
            html = f.read()
        refs = re.findall(r'(?:src|href)="([^"]+)"', html)
        self.assertTrue(refs, "note.html 没有引用任何资源")
        for r in refs:
            if r.startswith("http"):
                continue
            # vendor 下的 js 在打包时会被拷到 web/，这里只校验 web/ 内的
            path = os.path.join(web, r)
            self.assertTrue(os.path.exists(path), r)


if __name__ == "__main__":
    unittest.main()
