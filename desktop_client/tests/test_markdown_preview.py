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


if __name__ == "__main__":
    unittest.main()
