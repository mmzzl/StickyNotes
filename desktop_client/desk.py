"""主界面：托盘 + 新建便签 + 便签窗口管理。"""
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QMessageBox, QSystemTrayIcon

from api import Client
from note_window import NoteWindow


class Desk:
    def __init__(self, client: Client):
        self.client = client
        self.windows: list[NoteWindow] = []
        self.tray = QSystemTrayIcon(QIcon.fromTheme("edit-paste"), QApplication.instance())
        self.tray.setToolTip("便签")
        menu = QMenu()
        act_new = QAction("新建便签", None)
        act_new.triggered.connect(self.new_note)
        act_quit = QAction("退出", None)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_new)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.restore_all())
        self.tray.show()

    def _load(self):
        try:
            return self.client.list_notes()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(None, "提示", f"加载便签失败: {e}")
            return []

    def restore_all(self):
        for n in self._load():
            self.spawn(n, restore=True)

    def new_note(self):
        try:
            note = self.client.create_note()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(None, "提示", str(e))
            return
        self.spawn(note)

    def spawn(self, note, restore=False):
        if restore and any(w.note["id"] == note["id"] for w in self.windows):
            return
        w = NoteWindow(self.client, note, on_need_close=self._drop, parent=None)
        self.windows.append(w)
        w.show()

    def _drop(self, w):
        if w in self.windows:
            self.windows.remove(w)
