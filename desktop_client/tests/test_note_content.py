"""便签正文编辑区回归测试（Qt offscreen，不连网络）。

覆盖两套编辑模式：
- 源码模式：QTextEdit，构造函数必须用 setPlainText 入数据（否则 \n 被 HTML 塌成空格）
- 排版模式：所见即所得 web 编辑器（MarkdownEditor），懒创建 + 与源码框双向同步

排版模式内部是 QtWebEngine（offscreen 下起不来），所以这里用替身注入，
只测 NoteWindow 这层的切换/同步/记忆逻辑，不依赖真实浏览器进程。
"""
import inspect
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt5.QtCore import pyqtSignal
    from PyQt5.QtWidgets import QApplication, QWidget
except ModuleNotFoundError:  # Linux 开发机未装 PyQt5 → 跳过
    QApplication = None
    QWidget = object
    pyqtSignal = None

# 必须早于 QApplication 实例化：note_window 会连带导入 QtWebEngineWidgets，
# 与 app.py 的导入顺序要求一致。
if QApplication is not None:
    import note_window as note_window_mod
    from note_window import NoteWindow  # noqa: F401


class _StubRich(QWidget):
    """MarkdownEditor 的最小替身，避免测试里真的拉起 Chromium。

    继承 QWidget 才能塞进 QStackedWidget（真实实现就是个 QWidget 容器）。
    ready=True 是「页面已加载、内容已推进」的状态；ready=False 用来模拟
    打包版开机时排版页面还在排队的真实情形。
    """

    content_changed = pyqtSignal(str)
    ready = pyqtSignal()

    def __init__(self, parent=None, background=None, ready=True):
        QWidget.__init__(self, parent)
        self._md = ""
        self.bg = None
        self.calls = []
        self.on_change = None
        self._ready = bool(ready)
        self._bg = background

    def set_markdown(self, md):
        self._md = md or ""
        self.calls.append(("set", self._md))

    def set_background(self, color):
        self.bg = color

    def markdown(self):
        return self._md

    def is_ready(self):
        return self._ready

    def finish_load(self):
        """模拟排版页面加载完成。"""
        self._ready = True
        self.ready.emit()

    def emit_change(self, md):
        """模拟用户在 web 编辑器里输入。"""
        self._md = md
        if self.on_change:
            self.on_change(md)

    def shutdown(self):
        pass


class NoteContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication is None:
            raise unittest.SkipTest("当前环境未安装 PyQt5，跳过便签正文回归测试")
        cls._app = QApplication.instance() or QApplication([])
        cls._td = tempfile.TemporaryDirectory()
        cls._old_xdg = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = cls._td.name

    @classmethod
    def tearDownClass(cls):
        if cls._old_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = cls._old_xdg
        cls._td.cleanup()

    def _make(self, note_id, content, loaded=True):
        from api import Client
        from note_window import NoteWindow

        c = Client(base_url="http://127.0.0.1:1")
        note = {"id": note_id, "title": "标题", "content": content,
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        w = NoteWindow(c, note)

        stub = _StubRich(ready=loaded)

        def fake_ensure():
            if w._rich is None:
                w._rich = stub
                stub.on_change = w._on_rich_changed
                stub.ready.connect(w._on_rich_ready)
                w.content_stack.addWidget(w._rich)
            return stub

        w._ensure_rich = fake_ensure
        return w, stub

    def test_reopen_preserves_newlines(self):
        """重开便签时换行不能丢：构造函数按 HTML 解析会把 \\n 塌成空格。"""
        from api import Client
        from note_window import NoteWindow

        c = Client(base_url="http://127.0.0.1:1")
        note = {"id": "n2", "title": "多行",
                "content": "第一行\n第二行\n第三行",
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        w = NoteWindow(c, note)
        try:
            self.assertEqual(w.content.toPlainText(), "第一行\n第二行\n第三行")
        finally:
            w.close()

    def test_mode_toggle_switches_widget(self):
        """排版/源码切换要真的换页，且按钮文案跟着变。"""
        w, stub = self._make("n3", "#### 标题\n1. 一")
        try:
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.mode_btn.text(), "排版")

            w._toggle_mode()
            self.assertIs(w.content_stack.currentWidget(), w._rich)
            self.assertEqual(w.mode_btn.text(), "源码")

            w._toggle_mode()
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.mode_btn.text(), "排版")
        finally:
            w.close()

    def test_rich_is_lazy(self):
        """web 编辑器较重（独立 Chromium 进程），默认不该创建。"""
        from api import Client
        from note_window import NoteWindow

        c = Client(base_url="http://127.0.0.1:1")
        w = NoteWindow(c, {"id": "n4", "title": "", "content": "x",
                           "color": "#fff9c4", "pos_x": 0, "pos_y": 0})
        try:
            self.assertIsNone(w._rich)
        finally:
            w.close()

    def test_source_to_rich_syncs_markdown(self):
        """源码 -> 排版：源码内容要送进 web 编辑器。"""
        w, stub = self._make("n5", "#### 标题\n1. 一\n2. 二")
        try:
            w._toggle_mode()
            self.assertEqual(stub._md, "#### 标题\n1. 一\n2. 二")
        finally:
            w.close()

    def test_rich_to_source_keeps_markdown(self):
        """排版 -> 源码：web 编辑器里的内容要回填源码框，且换行不丢。"""
        w, stub = self._make("n6", "旧内容")
        try:
            w._toggle_mode()
            stub._md = "# 新标题\n\n段落二"
            w._toggle_mode()
            self.assertEqual(w.content.toPlainText(), "# 新标题\n\n段落二")
        finally:
            w.close()

    def test_rich_change_updates_source_and_triggers_save(self):
        """排版态输入要同步回源码框并触发防抖保存（否则存的是旧内容）。"""
        w, stub = self._make("n7", "初始")
        try:
            w._toggle_mode()
            stub.emit_change("改过的内容")
            self.assertEqual(w.content.toPlainText(), "改过的内容")
            self.assertEqual(w._current_md(), "改过的内容")
            self.assertTrue(w._save_timer.isActive())
        finally:
            w.close()

    def test_current_md_follows_active_mode(self):
        """保存时取值要跟着当前可见的编辑器走。"""
        w, stub = self._make("n8", "源码内容")
        try:
            self.assertEqual(w._current_md(), "源码内容")
            w._toggle_mode()
            self.assertEqual(w._current_md(), "源码内容")  # 刚同步过，一致
            stub._md = "排版内容"
            self.assertEqual(w._current_md(), "排版内容")
        finally:
            w.close()

    def test_view_mode_remembered_per_note(self):
        """每条便签各自记住上次的视图：切到排版后落盘，重开仍是排版。"""
        from config import load_view_modes

        w, stub = self._make("n9", "x")
        try:
            w._toggle_mode()   # -> 排版
            self.assertEqual(load_view_modes().get("n9"), "rich")
            w._toggle_mode()   # -> 源码
            self.assertEqual(load_view_modes().get("n9"), "source")
        finally:
            w.close()

    def test_rich_mode_restored_on_reopen(self):
        """记住的排版模式要在重开便签时自动恢复。

        这里用的是真实 MarkdownEditor，但 offscreen 下 QtWebEngine 起不来，
        页面永远不会 ready —— 正好就是"没就绪"的真实情形：意图要记住，
        但绝不能切过去（切过去就是首屏空白）。
        """
        from api import Client
        from config import save_view_mode
        from note_window import NoteWindow

        save_view_mode("n10", "rich")
        c = Client(base_url="http://127.0.0.1:1")
        w = NoteWindow(c, {"id": "n10", "title": "", "content": "内容",
                           "color": "#fff9c4", "pos_x": 0, "pos_y": 0})
        try:
            self.assertIsNotNone(w._rich)          # 排版视图确实建了
            self.assertTrue(w._rich_want)           # 意图记下了
            self.assertFalse(w._rich_on)            # 但还没生效（页面没就绪）
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.mode_btn.text(), "载入中…")
        finally:
            w.close()

    def test_restore_keeps_source_visible_until_rich_ready(self):
        """首屏不能空白：排版页面没就绪时要留在源码视图，就绪后才切。

        复现的正是"第一次打开只有标题、没有内容"：开机时所有便签的 Chromium
        一起排队，排版页面要等好几秒，原来在这段时间里就已经切过去了。
        """
        from api import Client
        from config import save_view_mode

        save_view_mode("n13", "rich")
        stub = _StubRich(ready=False)
        old = note_window_mod.MarkdownEditor
        note_window_mod.MarkdownEditor = lambda background=None, parent=None: stub
        try:
            c = Client(base_url="http://127.0.0.1:1")
            w = note_window_mod.NoteWindow(
                c, {"id": "n13", "title": "", "content": "真实内容",
                    "color": "#fff9c4", "pos_x": 0, "pos_y": 0})
        finally:
            note_window_mod.MarkdownEditor = old
        try:
            self.assertIsNotNone(w._rich)
            self.assertTrue(w._rich_want)
            self.assertFalse(w._rich_on)
            # 首屏停在源码视图上 —— 那里本来就有内容，用户不会看到空白
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.content.toPlainText(), "真实内容")

            stub.finish_load()          # 页面加载完成
            self.assertTrue(w._rich_on)
            self.assertIs(w.content_stack.currentWidget(), w._rich)
            self.assertEqual(stub._md, "真实内容")   # 内容确实推过去了
            self.assertEqual(w.mode_btn.text(), "源码")
        finally:
            w.close()

    def test_toggle_before_ready_waits_then_switches(self):
        """页面没就绪就点「排版」：先不切，加载完成后自动切过去。"""
        w, stub = self._make("n14", "#### 标题\n正文", loaded=False)
        try:
            w._toggle_mode()
            self.assertTrue(w._rich_want)
            self.assertFalse(w._rich_on)
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.mode_btn.text(), "载入中…")

            stub.finish_load()
            self.assertTrue(w._rich_on)
            self.assertIs(w.content_stack.currentWidget(), w._rich)
            self.assertEqual(w.mode_btn.text(), "源码")
            self.assertEqual(stub._md, "#### 标题\n正文")
        finally:
            w.close()

    def test_toggle_back_while_loading_cancels_pending_switch(self):
        """加载途中再点一下「源码」要能取消，别等就绪后又抢着切回去。"""
        w, stub = self._make("n15", "源码里的内容", loaded=False)
        try:
            w._toggle_mode()       # -> 排版（意图记下，暂不切）
            w._toggle_mode()       # -> 源码（取消）
            self.assertFalse(w._rich_want)
            stub.finish_load()     # 迟到的 ready 不该再切
            self.assertFalse(w._rich_on)
            self.assertIs(w.content_stack.currentWidget(), w.content)
            self.assertEqual(w.content.toPlainText(), "源码里的内容")
        finally:
            w.close()

    def test_rich_view_has_no_graphics_effect_ancestor(self):
        """排版视图的祖先链上不能有 QGraphicsEffect，否则首屏整片空白。

        真机 bug：投影原本挂在内容卡片（NoteCard）上，而卡片是 QWebEngineView 的
        祖先。祖先带 QGraphicsEffect 时 Qt 会把整棵子树重定向到离屏缓冲绘制，
        Chromium 合成器送来的帧进不去 —— 屏幕上什么都没有，页面里 textLen /
        maxScroll 却都正常，滚轮也能滚（用户就报"滚动条坏了"），敲一下字才显形。
        投影现在挂在只有底色、不含子控件的 NoteShadow 兄弟节点上。
        """
        w, stub = self._make("n16", "#### 标题\n\n正文")
        try:
            w._toggle_mode()          # 切到排版
            self.assertIs(w._rich, stub)
            node = w._rich
            while node is not None:
                self.assertIsNone(
                    node.graphicsEffect(),
                    f"{type(node).__name__} 带了 QGraphicsEffect，"
                    f"web 视图会被重定向绘制（首屏空白）")
                node = node.parentWidget()
            # 投影本身还在，只是换到了兄弟节点上
            self.assertIsNotNone(w._shadow_bg.graphicsEffect())
        finally:
            w.close()

    def test_apply_color_syncs_rich_background(self):
        """改便签颜色要刷进排版页面。

        Chromium 会自己铺一层不透明白底，不接管底色的话内容区整块变白
        （真机上排查出来的 bug）。这里锁住颜色确实传到了编辑器。
        """
        w, stub = self._make("n11", "x")
        try:
            w._toggle_mode()
            self.assertIs(w._rich, stub)
            w._apply_color("#e8f0ff")
            self.assertEqual(stub.bg, "#e8f0ff")
        finally:
            w.close()

    def test_rich_background_baked_into_page_css(self):
        """底色要写进页面初始 CSS。

        只在页面加载后才用 JS 铺色的话，加载完成前会闪一下 Chromium 的白底。
        """
        import md_editor

        css = md_editor._CSS.replace("__NOTE_BG__", "#e8f0ff")
        self.assertNotIn("__NOTE_BG__", css)
        self.assertIn("background: #e8f0ff", css)

    def test_scrollbar_styled_in_page_css(self):
        """排版视图必须自带滚动条样式。

        本机 Qt 5.15.3 的 QWebEngineView 直接继承 QWidget（不是
        QAbstractScrollArea），没有 verticalScrollBar()/scrollBarPolicy() 可用，
        滚动完全交给 Chromium。Chromium 默认滚动条是 15px 宽、带上下箭头按钮的
        浅灰条，浅色便签上几乎看不见，用户反馈"滚动条失效"。自定义
        ::-webkit-scrollbar 才会切到常驻（非 overlay）滚动条。
        """
        import md_editor

        css = md_editor._CSS
        self.assertIn("::-webkit-scrollbar", css)
        # 轨道/拇指都要有明确颜色，否则等于没画
        self.assertIn("::-webkit-scrollbar-thumb", css)
        self.assertIn("::-webkit-scrollbar-track", css)

    def test_page_settings_force_scrollbar_and_disable_anim(self):
        """排版视图必须显式打开滚动条、关掉滚动动画。

        这两个属性只能通过 page.settings() 设 —— 本机 PyQt5 5.15.6 把
        QWebEnginePage::Attribute 映射到了 QWebEngineSettings 上，
        QWebEnginePage 继承 QObject、没有 setAttribute。

        - ShowScrollBars：否则 Chromium 可能用 overlay（浮层自动隐藏）滚动条，
          便签里看不到滚动条（用户反馈"排版滚动条失效"，而源码框正常）。
        - ScrollAnimatorEnabled：Qt 5.15 默认开启，Chromium 滚轮滚动靠合成器
          逐帧推进；小窗口/被遮挡/XWayland 下帧不产生就表现为滚轮没反应。

        用假 page 对象验证，避免测试里真的拉起 Chromium。
        """
        import md_editor
        from PyQt5.QtWebEngineWidgets import QWebEngineSettings

        class _FakeSettings:
            def __init__(self):
                self.set = {}

            def setAttribute(self, attr, value):
                self.set[attr] = value

        class _FakePage:
            def __init__(self):
                self._st = _FakeSettings()

            def settings(self):
                return self._st

        page = _FakePage()
        md_editor.tune_page_settings(page)
        self.assertIs(page.settings().set[QWebEngineSettings.ShowScrollBars], True)
        self.assertIs(
            page.settings().set[QWebEngineSettings.ScrollAnimatorEnabled], False)

    def test_wheel_events_reach_the_log(self):
        """排版视图的滚轮事件要能进日志。

        "排版不能滚"这类问题 Qt 侧完全看不到：没有报错、没有事件。只能让页面
        收到滚轮时 console.log 一下，再由 _Page.javaScriptConsoleMessage 接住写进
        日志。日志里没有 [wheel] 行，就说明事件根本没进 Chromium，可以立刻把
        范围缩到 Qt/输入层而不是页面/CSS。
        """
        import md_editor

        self.assertIn("console.log('[wheel]", md_editor._HTML)
        self.assertIn("[wheel]", inspect.getsource(md_editor._Page))

    def test_diag_measures_scrollbar_and_range(self):
        """诊断要量出"有没有可滚动范围"和"滚动条是不是可见"。

        - maxScroll = docH - clientH：为 0 说明内容不够长，本来就不该有滚动条，
          这是正常情况不是 bug。不量这个就会把"内容短"误报成"滚动坏了"。
        - sbw = innerWidth - clientWidth：经典滚动条占布局宽度（>0，肉眼可见），
          overlay 滚动条差值为 0（看不见也摸不着，用户就说"没有滚动条"）。
        """
        import md_editor

        src = inspect.getsource(md_editor.MarkdownEditor.diag)
        self.assertIn("__diag", src)
        js = md_editor._HTML
        self.assertIn("maxScroll", js)
        self.assertIn("sbw: window.innerWidth - de.clientWidth", js)
        # 诊断必须在页面就绪后才跑，否则拿到的是空页面
        self.assertIn("self._ready", inspect.getsource(md_editor.MarkdownEditor.diag))

    def test_failed_page_load_is_retried(self):
        """页面加载失败要重试，不能让便签永久空白。

        _on_load 收到 ok=False 就直接放弃的话，_ready 一直是 False：轮询不启动、
        内容回写不启动，这条便签既不显示内容也存不下用户的输入，而且永远不会
        自愈。必须重试有限次。
        """
        import md_editor

        self.assertGreaterEqual(md_editor.MarkdownEditor.MAX_LOAD_TRIES, 1)
        self.assertGreater(md_editor.MarkdownEditor.RETRY_MS, 0)
        src = inspect.getsource(md_editor.MarkdownEditor._on_load)
        # 失败分支必须安排重试，而不是直接 return 掉
        self.assertIn("_retry.start", src)
        self.assertIn("MAX_LOAD_TRIES", src)

    def test_note_window_has_usable_default_size(self):
        """便签初始尺寸要够装内容。

        不显式 resize 时 Qt 走 sizeHint，只有 284x289（正文区 256x192），
        排版视图一有标题/列表/代码块就溢出，滚动条几乎一直在用。
        """
        w, _ = self._make("n12", "内容")
        try:
            self.assertGreaterEqual(w.width(), 340)
            self.assertGreaterEqual(w.height(), 360)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
