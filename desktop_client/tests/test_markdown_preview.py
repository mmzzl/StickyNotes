"""Markdown 预览回归测试（Qt offscreen，不连网络）。

回归背景：_darken_code 误标 @staticmethod 却调用 self._is_code_fmt，
一点「预览」就抛 NameError —— 本测试锁定预览切换不崩溃。
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt5.QtWidgets import QApplication
except ModuleNotFoundError:  # Linux 开发机未装 PyQt5 → 跳过（Windows 打包机/真机运行）
    QApplication = None


class MarkdownPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QApplication is None:
            raise unittest.SkipTest("当前环境未安装 PyQt5，跳过预览回归测试")
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

    def test_preview_toggle_with_code_no_crash(self):
        from api import Client
        from note_window import NoteWindow

        c = Client(base_url="http://127.0.0.1:1")
        note = {"id": "n1", "title": "标题", "content": "# hi\n```python\nx = 1\n```",
                "color": "#fff9c4", "pos_x": 0, "pos_y": 0}
        w = NoteWindow(c, note)
        try:
            w.content.setPlainText(note["content"])
            w._toggle_preview()               # 预览态渲染 → 触发 _darken_code
            self.assertIs(w.content_stack.currentWidget(), w.preview)
            w._toggle_preview()               # 切回编辑
            self.assertIs(w.content_stack.currentWidget(), w.content)
        finally:
            w.close()

    def test_reopen_preserves_newlines(self):
        """重开便签时换行不能丢：构造函数按 HTML 解析会把 \n 塌成空格，
        NoteWindow 必须用 setPlainText 入数据。"""
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

    def test_is_code_fmt_tolerates_missing_font_families(self):
        """QTextCharFormat 无 fontFamilies()（返回 None）时不能抛 TypeError。
        （PyQt5 的 cf.fontFamilies() 返回 None，join None 会炸。）"""
        from note_window import NoteWindow

        class _FakeFont:
            def __init__(self, family):
                self._family = family
            def families(self):
                return None          # 模拟 PyQt5 收到不存在的 API → None
            def family(self):
                return self._family

        class _FakeCharFormat:
            def __init__(self, family="SimSun"):
                self._font = _FakeFont(family)
            def fontFixedPitch(self):
                return False
            def font(self):
                return self._font

        # 非等宽字体族 → False 且不抛异常（回归：join None 曾抛 TypeError）
        self.assertFalse(NoteWindow._is_code_fmt(_FakeCharFormat("SimSun")))
        # 等宽字体族兜底仍能识别代码
        self.assertTrue(NoteWindow._is_code_fmt(_FakeCharFormat("Consolas")))


if __name__ == "__main__":
    unittest.main()
