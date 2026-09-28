"""便签桌列表显示回归测试。

便签正文现在支持标题/列表/代码块，早前列表把正文拼在标题后面
（"标题　·　正文"），长笔记会把行撑得很乱，所以锁定"只显示标题"。
"""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class DeskLabelTest(unittest.TestCase):
    def test_shows_title_only(self):
        from desk import _note_label

        note = {"title": "ubuntu 系统常用命令",
                "content": "#### ls 查看目录\n1. 白色表示文件\n2. 蓝色表示目录"}
        label = _note_label(note)
        self.assertEqual(label, "ubuntu 系统常用命令")
        # 正文一个字都不该漏进列表
        for leak in ("ls 查看目录", "白色表示文件", "·"):
            self.assertNotIn(leak, label)

    def test_title_whitespace_trimmed(self):
        from desk import _note_label

        self.assertEqual(_note_label({"title": "  标题  ", "content": "x"}), "标题")

    def test_falls_back_when_no_title(self):
        from desk import _note_label

        for note in ({"title": "", "content": "只有正文"},
                     {"title": "   ", "content": "标题是空白"},
                     {"title": None, "content": "x"},
                     {}):
            self.assertEqual(_note_label(note), "(无标题)")

    def test_view_modes_file_follows_xdg_at_runtime(self):
        """配置路径必须在调用时解析，而不是导入时算死。

        导入时算死的话，运行期再改 XDG_CONFIG_HOME 就不生效 —— 测试会读写真实的
        用户配置，曾经把测试用的便签 id 写进了 ~/.config（已踩过）。
        """
        from config import load_view_modes, save_view_mode, view_mode_file

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(str(view_mode_file()),
                                 os.path.join(tmp, "sticky_notes", "view_modes.json"))
                save_view_mode("note-x", "rich")
                self.assertEqual(load_view_modes().get("note-x"), "rich")
                self.assertTrue(os.path.exists(
                    os.path.join(tmp, "sticky_notes", "view_modes.json")))
            finally:
                if old is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = old


if __name__ == "__main__":
    unittest.main()
