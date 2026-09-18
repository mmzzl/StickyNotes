"""贴纸式便签窗口：无边框置顶、可拖动、可改色、关窗即存（内容/位置同步到后端）。
✕ 关闭=收起（内容已自动保存，可在便签桌/恢复全部中找回）；删除需右键→删除此便签并二次确认。"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (QColor, QCursor, QKeySequence, QTextBlockFormat,
                         QTextCharFormat, QTextCursor)
from PyQt5.QtWidgets import (QColorDialog, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                             QShortcut, QStackedWidget, QTextBrowser, QTextEdit,
                             QToolButton, QVBoxLayout, QWidget)


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
        self.save_btn = QToolButton()
        self.save_btn.setText("保存")
        self.save_btn.setObjectName("SaveBtn")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip("立即保存（Ctrl+S）")
        self.save_btn.clicked.connect(self._save_manual)
        self.preview_btn = QToolButton()
        self.preview_btn.setText("预览")
        self.preview_btn.setObjectName("SaveBtn")
        self.preview_btn.setCursor(Qt.PointingHandCursor)
        self.preview_btn.setToolTip("Markdown 预览（再点回到编辑）")
        self.preview_btn.clicked.connect(self._toggle_preview)
        self.del_btn = QToolButton()
        self.del_btn.setText("✕")
        self.del_btn.setObjectName("DelBtn")
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.setToolTip("关闭便签（内容已自动保存，可从便签桌恢复）")
        self.del_btn.clicked.connect(self._close_note)

        bbar = QHBoxLayout()
        bbar.setContentsMargins(4, 0, 6, 0)
        bbar.setSpacing(2)
        bbar.addWidget(grip)
        bbar.addStretch(1)
        bbar.addWidget(self.preview_btn)
        bbar.addWidget(self.save_btn)
        bbar.addWidget(self.color_btn)
        bbar.addWidget(self.del_btn)
        bar.setLayout(bbar)

        self.title = QLineEdit(note.get("title") or "")
        self.title.setObjectName("NoteTitle")
        self.title.setPlaceholderText("标题…")
        self.title.textChanged.connect(lambda _: self._save_later())
        self.content = QTextEdit(note.get("content") or "")
        self.content.setObjectName("NoteContent")
        self.content.setPlaceholderText("写点什么…（支持 Markdown，点「预览」看成稿）")
        self.content.textChanged.connect(self._save_later)

        # Markdown 只读预览（Qt 5.14+ 自带 setMarkdown，无需额外依赖）
        self.preview = QTextBrowser()
        self.preview.setObjectName("MdPreview")
        self.preview.setOpenExternalLinks(True)
        self.preview.setFrameStyle(0)
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.content)   # index 0 编辑
        self.content_stack.addWidget(self.preview)   # index 1 预览
        self._preview_on = False

        # Ctrl+S 立即保存提示（右下角小字）
        self.save_status = QLabel("✓ 已保存")
        self.save_status.setObjectName("SaveStatus")
        self.save_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.save_status.hide()

        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(bar)
        body.addWidget(self.title)
        body.addWidget(self.content_stack, 1)
        body.addWidget(self.save_status)

        # 平台标准保存快捷键（Windows/Linux Ctrl+S；macOS Cmd+S）
        shortcut = QShortcut(QKeySequence(QKeySequence.Save), self)
        shortcut.activated.connect(self._save_manual)

        # 右键菜单：保存 / 删除（删除带二次确认）
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_note_menu)

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
            "#SaveBtn { border: none; background: rgba(255,255,255,0.55);"
            " border-radius: 5px; padding: 2px 8px; font-size: 12px;"
            " color: rgba(0,0,0,0.6); }"
            "#SaveBtn:hover { background: rgba(255,255,255,0.95); color: black; }"
            "#DelBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.45); font-size: 15px; }"
            "#DelBtn:hover { color: #c0392b; }"
            "#NoteTitle { border: none; background: transparent;"
            " font-size: 14px; font-weight: 700; padding: 4px 12px 2px; }"
            "#NoteContent { border: none; background: transparent;"
            " font-size: 13px; line-height: 1.55; padding: 2px 12px 10px; }"
            "QTextBrowser#MdPreview { border: none; background: transparent;"
            " font-size: 13px; line-height: 1.55; padding: 2px 12px 10px; }"
            "#SaveStatus { background: transparent; color: rgba(0,0,0,0.45);"
            " font-size: 11px; padding: 0 12px 6px; }"
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

    def _toggle_preview(self):
        """「预览/编辑」切换：预览态只读渲染 Markdown，源数据始终来自编辑框。"""
        self._preview_on = not self._preview_on
        if self._preview_on:
            src = self.content.toPlainText()
            if hasattr(self.preview, "setMarkdown"):
                self.preview.setMarkdown(src or "*（空内容）*")
                self._darken_code(self.preview)
            else:
                self.preview.setPlainText(src or "（空内容）")
            self.content_stack.setCurrentWidget(self.preview)
            self.preview_btn.setText("编辑")
            self.preview_btn.setToolTip("回到 Markdown 源码编辑")
        else:
            self.content_stack.setCurrentWidget(self.content)
            self.preview_btn.setText("预览")
            self.preview_btn.setToolTip("Markdown 预览（再点回到编辑）")

    @staticmethod
    def _darken_code(browser):
        """Markdown 渲染后把代码设为黑底白字：整行代码块铺黑，行内代码黑片。"""
        doc = browser.document()
        char_fmt = QTextCharFormat()
        char_fmt.setBackground(QColor(30, 30, 30))
        char_fmt.setForeground(QColor(240, 240, 240))
        block_fmt = QTextBlockFormat()
        block_fmt.setBackground(QColor(30, 30, 30))
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.charFormat().fontFixedPitch():
                    c = QTextCursor(doc)
                    c.setPosition(frag.position())
                    c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                    c.mergeCharFormat(char_fmt)
                    if frag.length() == block.length() - 1:
                        # 该块整体就是代码（围栏内一行）→ 块背景铺满该行
                        bc = QTextCursor(block)
                        bc.select(QTextCursor.BlockUnderCursor)
                        bc.setBlockFormat(block_fmt)
                it = it.next()
            block = block.next()

    def _save_manual(self):
        """Ctrl+S：立即保存（跳过防抖）并给出已保存提示。"""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save(show_ok=True)

    def _flash_saved(self):
        self.save_status.setText("✓ 已保存")
        self.save_status.show()
        if not hasattr(self, "_ok_timer"):
            self._ok_timer = QTimer(self)
            self._ok_timer.setSingleShot(True)
            self._ok_timer.setInterval(1500)
            self._ok_timer.timeout.connect(self.save_status.hide)
        self._ok_timer.start()

    def _save(self, show_ok=False):
        try:
            data = self.client.update_note(
                self.note["id"],
                title=self.title.text(), content=self.content.toPlainText(),
                color=self._color, pos_x=self.x(), pos_y=self.y())
            if isinstance(data, dict):
                self.note = data
            if self.on_changed:
                self.on_changed(self.note)
            if show_ok:
                self._flash_saved()
        except Exception:  # noqa: BLE001
            pass  # 离线等场景静默，待下次编辑再同步

    def _close_note(self):
        """✕：关闭（收起）便签窗口。内容已自动保存，列表/恢复全部仍可找回。"""
        self.close()

    # ---- 右键菜单 ----
    def _show_note_menu(self, _pos):
        menu = QMenu(self)
        act_save = menu.addAction("保存 (Ctrl+S)")
        act_save.triggered.connect(self._save_manual)
        act_del = menu.addAction("删除此便签…")
        act_del.triggered.connect(self._confirm_delete)
        menu.exec_(QCursor.pos())

    def _confirm_delete(self):
        if QMessageBox.question(
                self, "删除便签", "确定删除这张便签？删除后不可恢复。",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self._delete()

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
