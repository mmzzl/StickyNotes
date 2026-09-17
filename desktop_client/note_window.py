"""贴纸式便签窗口：无边框置顶、可拖动、可改色、关窗即存（内容/位置同步到后端）。"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QColorDialog, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLineEdit, QTextEdit, QToolButton,
                             QVBoxLayout, QWidget)


class _DragBar(QWidget):
    """便签头部条：按住空白处即拖动整个贴纸窗口（系统级移动）。"""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            e.accept()
            return
        super().mousePressEvent(e)


class NoteWindow(QFrame):
    def __init__(self, client, note, on_need_close=None, on_changed=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.note = note
        self.on_need_close = on_need_close
        self.on_changed = on_changed
        self._color = note.get("color") or "#fff9c4"

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(230, 190)
        self.setWindowTitle("便签")

        # 内容卡片（外层留白给投影，实现圆角贴纸感）
        card = QFrame()
        card.setObjectName("NoteCard")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(60, 50, 10, 110))
        card.setGraphicsEffect(shadow)
        self._card = card

        # 头部：拖动条 + 改色 + 关闭
        bar = _DragBar()
        bar.setObjectName("NoteBar")
        bar.setFixedHeight(34)
        bar.setCursor(Qt.SizeAllCursor)
        grip = QToolButton()
        grip.setText("⠿")
        grip.setObjectName("Grip")
        grip.setCursor(Qt.OpenHandCursor)
        self.color_btn = QToolButton()
        self.color_btn.setText("●")
        self.color_btn.setObjectName("ColorBtn")
        self.color_btn.setCursor(Qt.PointingHandCursor)
        self.color_btn.clicked.connect(self._pick_color)
        self.del_btn = QToolButton()
        self.del_btn.setText("✕")
        self.del_btn.setObjectName("DelBtn")
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.clicked.connect(self._delete)

        bbar = QHBoxLayout()
        bbar.setContentsMargins(4, 0, 6, 0)
        bbar.setSpacing(2)
        bbar.addWidget(grip)
        bbar.addStretch(1)
        bbar.addWidget(self.color_btn)
        bbar.addWidget(self.del_btn)
        bar.setLayout(bbar)

        self.title = QLineEdit(note.get("title") or "")
        self.title.setObjectName("NoteTitle")
        self.title.setPlaceholderText("标题…")
        self.title.textChanged.connect(lambda _: self._save_later())
        self.content = QTextEdit(note.get("content") or "")
        self.content.setObjectName("NoteContent")
        self.content.setPlaceholderText("写点什么…")
        self.content.textChanged.connect(self._save_later)

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(bar)
        body.addWidget(self.title)
        body.addWidget(self.content, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 18)
        outer.addWidget(card)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        self.move(int(note.get("pos_x") or 0), int(note.get("pos_y") or 0))
        self._apply_color(self._color)

    # ---- 主题 ----
    def _apply_color(self, color):
        self._color = color
        self._card.setStyleSheet(
            "#NoteCard { background: %s; border-radius: 12px; }"
            "#NoteBar { background: rgba(0,0,0,0.06);"
            " border-top-left-radius: 12px; border-top-right-radius: 12px; }"
            "#Grip { border: none; background: transparent; color: rgba(0,0,0,0.35); }"
            "#ColorBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.55); font-size: 16px; }"
            "#ColorBtn:hover { color: black; }"
            "#DelBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.45); font-size: 15px; }"
            "#DelBtn:hover { color: #c0392b; }"
            "#NoteTitle { border: none; background: transparent;"
            " font-size: 14px; font-weight: 700; padding: 4px 12px 2px; }"
            "#NoteContent { border: none; background: transparent;"
            " font-size: 13px; line-height: 1.55; padding: 2px 12px 10px; }"
            % color)

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self._apply_color(color.name())
            self._save()  # 改色立即保存

    # ---- 保存 ----
    def _save_later(self):
        self._save_timer.start()  # 防抖：输入/拖动停止 600ms 后上报

    def moveEvent(self, _e):
        self._save_later()  # 拖动停止后延迟上报位置

    def _save(self):
        try:
            data = self.client.update_note(
                self.note["id"],
                title=self.title.text(), content=self.content.toPlainText(),
                color=self._color, pos_x=self.x(), pos_y=self.y())
            if isinstance(data, dict):
                self.note = data
            if self.on_changed:
                self.on_changed(self.note["id"])
        except Exception:  # noqa: BLE001
            pass  # 离线等场景静默，待下次编辑再同步

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
