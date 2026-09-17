"""贴纸式便签窗口：无边框、可拖动、可改色、关窗即存。"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QColorDialog, QFrame, QHBoxLayout, QLineEdit,
                             QTextEdit, QToolButton, QVBoxLayout)


class NoteWindow(QFrame):
    def __init__(self, client, note, on_need_close=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.note = note
        self.on_need_close = on_need_close
        self._moving = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(220, 180)
        self._apply_color(note.get("color") or "#fff9c4")

        title = QLineEdit(note.get("title") or "")
        title.textChanged.connect(lambda _: self._save_later())
        content = QTextEdit(note.get("content") or "")
        content.textChanged.connect(lambda: self._save_later())
        color_btn = QToolButton()
        color_btn.setText("●")
        color_btn.clicked.connect(self._pick_color)
        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.clicked.connect(self._delete)

        bar = QHBoxLayout()
        bar.addWidget(color_btn)
        bar.addWidget(del_btn, 0, Qt.AlignRight)
        body = QVBoxLayout(self)
        body.addLayout(bar)
        body.addWidget(title)
        body.addWidget(content, 1)

        self._title = title
        self._content = content
        self._color_btn = color_btn
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        self.move(int(note.get("pos_x") or 0), int(note.get("pos_y") or 0))

    def _apply_color(self, color):
        self.setStyleSheet(
            f"NoteWindow {{ background: {color}; border: 1px solid #bdbdbd; }}"
            f"QLineEdit,QTextEdit {{ background: transparent; border: none; }}")

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self._apply_color(color.name())

    def moveEvent(self, _e):
        self._save_timer.start()          # 拖动停止后延迟 600ms 上报位置

    def _save_later(self):
        self._save_timer.start()

    def _save(self):
        try:
            self.client.update_note(
                self.note["id"],
                title=self._title.text(), content=self._content.toPlainText(),
                color=self._current_color(), pos_x=self.x(), pos_y=self.y())
        except Exception:  # noqa: BLE001
            pass

    def _current_color(self):
        import re
        m = re.search(r"background: (#[0-9a-fA-F]{6})", self.styleSheet())
        return m.group(1) if m else "#fff9c4"

    def _delete(self):
        try:
            self.client.delete_note(self.note["id"])
        finally:
            self.close()

    def closeEvent(self, _e):
        if self._save_timer.isActive():
            self._save()
        if self.on_need_close:
            self.on_need_close(self)
        super().closeEvent(_e)
