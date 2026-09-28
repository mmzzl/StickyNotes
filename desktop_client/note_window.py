"""贴纸式便签窗口：无边框置顶、可拖动、可改色、关窗即存（内容/位置同步到后端）。
🗑 删除=不可恢复（二次确认）；✕ 关闭=收起（内容已自动保存，可在便签桌/恢复全部中找回）。"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (QColor, QCursor, QKeySequence, QTextBlockFormat,
                         QTextCursor)
from PyQt5.QtWidgets import (QApplication, QColorDialog, QFrame,
                             QGraphicsDropShadowEffect, QGridLayout,
                             QHBoxLayout, QLabel,
                             QLineEdit, QMenu, QMessageBox, QShortcut,
                             QStackedWidget, QTextEdit, QToolButton,
                             QVBoxLayout, QWidget)

from config import load_view_modes, save_view_mode
from sticky_log import get_logger

_log = get_logger("note")

try:
    # 所见即所得编辑器。QtWebEngineWidgets 必须在 QApplication 之前导入，
    # app.py 已保证导入顺序；这里再兜一层，WebEngine 不可用时降级为纯源码模式。
    from md_editor import MarkdownEditor
    _RICH_AVAILABLE = True
except Exception:  # noqa: BLE001  # pragma: no cover - 取决于运行环境
    MarkdownEditor = None
    _RICH_AVAILABLE = False


class _DragBar(QWidget):
    """便签头部条：按住空白处即拖动整个贴纸窗口（系统级移动）。"""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            e.accept()
            return
        super().mousePressEvent(e)


def _apply_line_height(text_edit, multiple=1.5, _guard=None):
    """给 QTextEdit/QTextBrowser 全文套用行距（Qt 的 QSS 不支持 line-height）。

    QTextBlockFormat.setLineHeight 以单行高度为 100% 的比例单位，multiple=1.5 即 1.5 倍行距。
    注意：mergeBlockFormat 会改动文档并触发 textChanged，若该信号又回调本函数就会无限递归，
    故用 _guard 做重入保护（调用方传一个可变的 list/字典 作哨兵）。
    """
    if _guard is not None:
        if _guard.get("busy"):
            return
        _guard["busy"] = True
    try:
        fmt = QTextBlockFormat()
        fmt.setLineHeight(int(100 * multiple), QTextBlockFormat.ProportionalHeight)
        doc = text_edit.document()
        block = doc.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            cursor.mergeBlockFormat(fmt)
            block = block.next()
    finally:
        if _guard is not None:
            _guard["busy"] = False


class NoteWindow(QFrame):
    def __init__(self, client, note, on_need_close=None, on_changed=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.note = note
        self.on_need_close = on_need_close
        self.on_changed = on_changed
        self._color = note.get("color") or "#fff9c4"
        self._lh_guard = {"busy": False}   # 行距重入保护哨兵，见 _apply_line_height

        # 防抖定时器必须早于任何会触发 textChanged 的操作创建，否则 _save_later 会空指针
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 不显式设尺寸的话走 Qt 的 sizeHint，只有 284x289（正文区仅 256x192）——
        # 排版视图有标题/列表/代码块，这点高度几乎一直在滚动。给个宽裕的初始尺寸，
        # 窗口仍可自由拉伸。
        self.resize(380, 400)
        self.setMinimumSize(280, 220)
        self.setWindowTitle("便签")

        # 内容卡片（外层留白给投影，实现圆角贴纸感）
        #
        # ⚠ 投影绝不能挂在 card 上：card 是排版视图（QWebEngineView）的祖先，而祖先一旦
        # 挂上 QGraphicsEffect，Qt 就会把整棵子树重定向到离屏缓冲绘制，Chromium 合成器
        # 送来的那一帧不会跟着进缓冲 —— 屏幕上就是一片空白。Qt 侧什么异常都没有，页面里
        # textLen / maxScroll 都正常，滚轮也能滚（用户以为滚动条坏了），只有敲一下字触发
        # 重绘才"活"过来，正是这个 bug 的样子。
        # 改成把投影挂到一个「只有底色、不含子控件」的兄弟节点 shadow_bg 上，两者叠在
        # 顶层同一个格子里：投影照旧由顶层绘制（外圈留白里能看到），而 web 视图不再有
        # 带特效的祖先。两者必须都是顶层的直接子控件 —— 隔一层 holder 的话投影会被
        # 裁在 holder 边界内，阴影就整个消失了（实测左边距 alpha 全 0）。
        shadow_bg = QFrame()
        shadow_bg.setObjectName("NoteShadow")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(60, 50, 10, 110))
        shadow_bg.setGraphicsEffect(shadow)
        self._shadow_bg = shadow_bg

        card = QFrame()
        card.setObjectName("NoteCard")
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
        self.mode_btn = QToolButton()
        self.mode_btn.setObjectName("SaveBtn")
        self.mode_btn.setCursor(Qt.PointingHandCursor)
        self.mode_btn.clicked.connect(self._toggle_mode)
        self.preview_btn = self.mode_btn   # 兼容旧引用名
        self.del_btn = QToolButton()
        self.del_btn.setText("🗑")
        self.del_btn.setObjectName("DelBtn")
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.setToolTip("删除此便签（不可恢复）")
        self.del_btn.clicked.connect(self._confirm_delete)
        self.close_btn = QToolButton()
        self.close_btn.setText("✕")
        self.close_btn.setObjectName("CloseBtn")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setToolTip("收起便签（内容已自动保存，可从便签桌恢复）")
        self.close_btn.clicked.connect(self._close_note)

        bbar = QHBoxLayout()
        bbar.setContentsMargins(4, 0, 6, 0)
        bbar.setSpacing(2)
        bbar.addWidget(grip)
        bbar.addStretch(1)
        bbar.addWidget(self.preview_btn)
        bbar.addWidget(self.save_btn)
        bbar.addWidget(self.color_btn)
        bbar.addWidget(self.del_btn)
        bbar.addWidget(self.close_btn)
        bar.setLayout(bbar)

        self.title = QLineEdit(note.get("title") or "")
        self.title.setObjectName("NoteTitle")
        self.title.setPlaceholderText("标题…")
        self.title.textChanged.connect(lambda _: self._save_later())
        self.content = QTextEdit()
        self.content.setPlainText(note.get("content") or "")  # setPlainText 保留换行（构造函数会按 HTML 塌掉换行）
        self.content.setObjectName("NoteContent")
        self.content.setPlaceholderText("写点什么…（支持 Markdown，点「预览」看成稿）")
        self.content.textChanged.connect(self._save_later)
        self.content.textChanged.connect(self._line_height_on_change)  # 新段落补行距
        _apply_line_height(self.content, 1.5, self._lh_guard)

        # 正文区：index 0 = 源码(QTextEdit)，index 1 = 排版(所见即所得 web 编辑器)
        # web 编辑器较重（独立 Chromium 进程），按需懒创建：只有切到"排版"模式才建
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.content)   # 0 源码
        self._rich = None                             # 1 排版（懒创建）
        self._rich_on = False      # 排版是否已生效并正在显示
        self._rich_want = False    # 排版是否是你想要的状态（加载中时先记着，就绪再切）

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

        # 顶层用网格叠放：投影节点在下、卡片在上，共享同一格
        outer = QGridLayout(self)
        outer.setContentsMargins(14, 14, 14, 18)
        outer.setSpacing(0)
        outer.addWidget(shadow_bg, 0, 0)
        outer.addWidget(card, 0, 0)
        card.raise_()

        self.move(int(note.get("pos_x") or 0), int(note.get("pos_y") or 0))
        self._apply_color(self._color)
        self._apply_saved_mode()   # 恢复这条便签上次停留的视图（源码/排版）

    # ---- 主题 ----
    def _line_height_on_change(self):
        """编辑区内容变化后给所有段落补行距（带重入保护，避免 textChanged 自激递归）。"""
        _apply_line_height(self.content, 1.5, self._lh_guard)

    def _apply_color(self, color):
        self._color = color
        # NoteShadow 是投影载体，必须和 NoteCard 同色同圆角，否则方角会从卡片的圆角
        # 后面露出来；改色时两个一起刷。
        self._shadow_bg.setStyleSheet("#NoteShadow { background: %s;"
                                      " border-radius: 14px; }" % color)
        self._card.setStyleSheet(
            "#NoteCard { background: %s; border-radius: 14px; }"
            "#NoteBar { background: rgba(0,0,0,0.05);"
            " border-top-left-radius: 14px; border-top-right-radius: 14px; }"
            "#Grip { border: none; background: transparent; color: rgba(0,0,0,0.35); }"
            "#ColorBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.55); font-size: 18px; }"
            "#ColorBtn:hover { color: black; }"
            "#SaveBtn { border: none; background: rgba(255,255,255,0.6);"
            " border-radius: 6px; padding: 3px 10px; font-size: 13px;"
            " color: rgba(0,0,0,0.65); }"
            "#SaveBtn:hover { background: rgba(255,255,255,0.95); color: black; }"
            "#DelBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.4); font-size: 15px; padding: 0 2px; }"
            "#DelBtn:hover { color: #c0392b; }"
            "#CloseBtn { border: none; background: transparent;"
            " color: rgba(0,0,0,0.45); font-size: 16px; padding: 0 2px; }"
            "#CloseBtn:hover { color: black; }"
            "#NoteTitle { border: none; background: transparent;"
            " font-size: 16px; font-weight: 700; padding: 6px 14px 4px; }"
            "#NoteContent { border: none; background: transparent;"
            " font-size: 14px; padding: 4px 14px 12px;"
            " selection-background-color: #ffd54f; selection-color: #333; }"
            "#SaveStatus { background: transparent; color: rgba(0,0,0,0.45);"
            " font-size: 12px; padding: 0 14px 8px; }"
            % color)
        # 排版视图自带底色（Chromium 不透明），改色要同步刷进页面
        if self._rich is not None:
            self._rich.set_background(color)

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

    def _clamp_into_screen(self):
        """把便签拉回可见屏幕内。

        便签坐标(pos_x/pos_y)随库保存，换机器/改分辨率/改显示器布局后，旧坐标可能
        落在所有屏幕之外，双击打开就"没反应"。这里按窗口中心所在屏幕判断：没有命中
        任何屏幕就贴到主屏，部分出界则平移回界内。
        """
        screen = (QApplication.screenAt(self.frameGeometry().center())
                  or QApplication.primaryScreen())
        area = screen.availableGeometry()
        w, h = self.width(), self.height()
        x = min(max(self.x(), area.left()), max(area.left(), area.right() - w + 1))
        y = min(max(self.y(), area.top()), max(area.top(), area.bottom() - h + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)   # moveEvent 的防抖会把这个位置一并存回后端

    def showEvent(self, e):
        super().showEvent(e)
        self._clamp_into_screen()
        cur = self.content_stack.currentWidget()
        _log.info("便签 %s 显示 %s 几何=%s 当前页=%s 排版已就绪=%s",
                  self.note.get("id"), self.note.get("title"),
                  self.geometry().getRect(),
                  "排版" if cur is self._rich else "源码", self._rich_loaded())
        QTimer.singleShot(2000, self._log_first_paint)

    def _log_first_paint(self):
        """首屏快照：记下用户这一刻实际看到的内容。

        "第一次打开只有标题没有内容"这种问题光看状态标志位说不清——是页面没内容、
        还是没画出来、还是压根没切过去。这里在窗口显示 2 秒后把真实状态落一条，
        排查时对着日志就知道是哪一种。
        """
        cur = self.content_stack.currentWidget()
        if cur is self._rich:
            _log.info("便签 %s 首屏=排版 排版就绪=%s", self.note.get("id"),
                      self._rich_loaded())
            self._rich.diag("首屏")
        else:
            _log.info("便签 %s 首屏=源码 源码可见字数=%d（排版未就绪，先留在源码）",
                      self.note.get("id"), len(self.content.toPlainText()))

    # ---- 显示模式：源码 <-> 排版 ----
    def _current_md(self) -> str:
        """当前可见编辑器里的 Markdown 源码（保存时统一从这里取）。"""
        if self._rich_on and self._rich is not None:
            return self._rich.markdown()
        return self.content.toPlainText()

    def _ensure_rich(self):
        """懒创建所见即所得编辑器。每个 web 视图对应一个 Chromium 进程，
        所以只在用户真的切到"排版"模式时才建。"""
        if self._rich is None:
            if MarkdownEditor is None:      # WebEngine 不可用，退回源码模式
                return None
            self._rich = MarkdownEditor(background=self._color)
            self._rich.content_changed.connect(self._on_rich_changed)
            self._rich.ready.connect(self._on_rich_ready)
            self.content_stack.addWidget(self._rich)   # index 1
            self._rich.set_markdown(self.content.toPlainText())
        return self._rich

    def _rich_loaded(self) -> bool:
        """排版页面是否已加载完成、可以看内容了。"""
        return self._rich is not None and self._rich.is_ready()

    def _on_rich_ready(self):
        """排版页面加载完成：把最新的源码推进页面，再按意图切过去。

        切之前一直留在源码视图 —— 源码框里本来就有内容，排版页面没就绪就切过去，
        用户看到的就是"便签只有标题、没有内容"。打包版开机时所有便签的 Chromium
        一起排队，首屏空白好几秒就是这个原因。
        """
        if self._rich is None or not self._rich_want:
            _log.info("便签 %s：排版就绪，但意图是源码，不切换", self.note.get("id"))
            return
        self._rich.set_markdown(self.content.toPlainText())
        self.content_stack.setCurrentWidget(self._rich)
        self._rich_on = True
        self._sync_mode_btn()
        _log.info("便签 %s：排版就绪，已切换到排版视图（按钮=%s）",
                  self.note.get("id"), self.mode_btn.text())

    def _sync_mode_btn(self):
        """刷新「排版/源码」按钮的文案与提示，反映真实状态。"""
        if self._rich_on:
            self.mode_btn.setText("源码")
            self.mode_btn.setToolTip("切换到 Markdown 源码编辑")
            return
        self.mode_btn.setText("排版")
        if self._rich is not None and not self._rich.is_ready():
            self.mode_btn.setText("载入中…")
            self.mode_btn.setToolTip("排版视图正在加载，完成后自动切换")
        elif MarkdownEditor is None:
            self.mode_btn.setEnabled(False)
            self.mode_btn.setToolTip("排版视图不可用（缺少 QtWebEngine）")
        else:
            self.mode_btn.setToolTip("切换到排版视图（所见即所得，可直接编辑）")

    def _toggle_mode(self):
        """在「排版（所见即所得）」和「源码（Markdown 原文）」之间切换。"""
        if self._rich_want:
            # 排版 -> 源码：把 web 编辑器里的内容同步回源码框
            # （还没加载完就没有 web 侧内容可拿，源码框里的是最新的，不用动）
            if self._rich_loaded():
                self.content.setPlainText(self._rich.markdown())
            self._rich_want = False
            self._rich_on = False
            self.content_stack.setCurrentWidget(self.content)
        else:
            # 源码 -> 排版
            if self._ensure_rich() is None:
                QMessageBox.information(
                    self, "排版视图不可用",
                    "未能加载排版组件（QtWebEngine），当前仅支持源码编辑。\n"
                    "Debian/Ubuntu 可安装：sudo apt install python3-pyqt5.qtwebengine")
                return
            self._rich_want = True
            self._rich.set_markdown(self.content.toPlainText())
            if self._rich_loaded():
                self.content_stack.setCurrentWidget(self._rich)
                self._rich_on = True
            # 未就绪就先留着源码视图，_on_rich_ready 里再切
        self._sync_mode_btn()
        save_view_mode(str(self.note.get("id") or ""),
                       "rich" if self._rich_want else "source")
        _log.info("便签 %s 手动切换：意图=%s 已生效=%s 按钮=%s",
                  self.note.get("id"),
                  "排版" if self._rich_want else "源码", self._rich_on,
                  self.mode_btn.text())

    def _on_rich_changed(self, md: str):
        """排版模式下内容变化：同步源码框并触发防抖保存。"""
        if self.content.toPlainText() != md:
            self.content.setPlainText(md)
        self._save_later()

    def _apply_saved_mode(self):
        """按上次停留的模式恢复这条便签的视图（每条便签各自记忆）。

        注意别在这里直接切到排版视图：此时窗口还没 show、排版页面也还没加载完，
        直接切过去就是一片空白。改成记下意图，等 ready 信号到了再切
        （见 _on_rich_ready）。期间源码视图本来就显示着内容，用户看不出空档。
        """
        mode = load_view_modes().get(str(self.note.get("id") or ""), "source")
        rich = self._ensure_rich() if mode == "rich" else None
        if rich is not None:
            self._rich_want = True
            if self._rich_loaded():
                self.content_stack.setCurrentWidget(self._rich)
                self._rich_on = True
        self._sync_mode_btn()
        _log.info("便签 %s 恢复显示模式=%s 排版已就绪=%s 当前页=%s 按钮=%s",
                  self.note.get("id"), mode, self._rich_loaded(),
                  type(self.content_stack.currentWidget()).__name__,
                  self.mode_btn.text())

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
                title=self.title.text(), content=self._current_md(),
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
        act_mode = menu.addAction(
            "切到源码编辑" if self._rich_want else "切到排版视图（所见即所得）")
        act_mode.triggered.connect(self._toggle_mode)
        menu.addSeparator()
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
        if self._rich is not None:
            self._rich.shutdown()   # 停掉轮询，避免 web 进程继续跑
        if self.on_need_close:
            self.on_need_close(self)
        super().closeEvent(_e)
