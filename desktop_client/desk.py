"""主界面：登录后可见的「便签桌」主窗口 + 托盘 + 贴纸式便签窗口管理。"""
from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QFrame, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                             QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
                             QVBoxLayout, QWidget)

from api import Client
from note_window import NoteWindow


def make_sticky_icon() -> QIcon:
    """自行绘制黄色便签图标（QIcon.fromTheme 在 Windows 常为空）。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 213, 79))
    p.drawRoundedRect(QRectF(12, 8, 40, 44), 8, 8)
    # 右上折角
    path = QPainterPath()
    path.moveTo(QPointF(52, 8))
    path.lineTo(QPointF(42, 8))
    path.lineTo(QPointF(52, 18))
    path.closeSubpath()
    p.setBrush(QColor(224, 172, 31))
    p.drawPath(path)
    # 文字线
    p.setBrush(QColor(160, 120, 20))
    p.drawRoundedRect(QRectF(18, 26, 28, 3), 2, 2)
    p.drawRoundedRect(QRectF(18, 33, 20, 3), 2, 2)
    p.end()
    return QIcon(pm)


def _note_preview(note: dict, limit: int = 40) -> str:
    title = (note.get("title") or "").strip()
    content = (note.get("content") or "").replace("\n", " ").strip()
    if title:
        return title if not content else f"{title}　·　{content}"
    return content[:limit] if content else "(无标题)"


class DeskWindow(QMainWindow):
    """登录后显示的便签桌：顶部工具栏 + 便签列表 + 「＋ 新建便签」。"""

    def __init__(self, desk: "Desk"):
        super().__init__()
        self.desk = desk
        self.setWindowTitle("便签桌")
        self.setWindowIcon(make_sticky_icon())
        self.resize(560, 480)
        self.setMinimumSize(400, 320)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- 顶部工具栏 ----
        bar = QFrame()
        bar.setObjectName("DeskBar")
        bar.setFixedHeight(58)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 16, 0)
        bl.setSpacing(10)

        brand = QLabel("📝 便签桌")
        brand.setObjectName("DeskBrand")
        self.user_label = QLabel("")
        self.user_label.setObjectName("DeskUser")

        self.btn_new = QPushButton("＋ 新建便签")
        self.btn_new.setObjectName("BtnNew")
        self.btn_new.setCursor(Qt.PointingHandCursor)
        self.btn_new.clicked.connect(self.desk.new_note)

        self.btn_restore = QPushButton("恢复全部")
        self.btn_restore.setObjectName("BtnGhost")
        self.btn_restore.setCursor(Qt.PointingHandCursor)
        self.btn_restore.clicked.connect(self.desk.restore_all)

        self.btn_quit = QPushButton("退出")
        self.btn_quit.setObjectName("BtnGhost")
        self.btn_quit.setCursor(Qt.PointingHandCursor)
        self.btn_quit.clicked.connect(self.desk.quit_app)

        bl.addWidget(brand)
        bl.addSpacing(8)
        bl.addWidget(self.user_label)
        bl.addStretch(1)
        bl.addWidget(self.btn_restore)
        bl.addWidget(self.btn_new)
        bl.addWidget(self.btn_quit)
        lay.addWidget(bar)

        # ---- 搜索 / 计数 / 加载更多 ----
        frow = QWidget()
        fl = QHBoxLayout(frow)
        fl.setContentsMargins(12, 8, 12, 0)
        fl.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("DeskSearch")
        self.search.setPlaceholderText("搜索标题/内容（回车）")
        self.search.returnPressed.connect(self.desk.on_search)
        self.count_label = QLabel("")
        self.count_label.setObjectName("DeskCount")
        self.load_more = QPushButton("加载更多")
        self.load_more.setObjectName("BtnGhost")
        self.load_more.setCursor(Qt.PointingHandCursor)
        self.load_more.clicked.connect(self.desk.load_more)
        self.load_more.hide()
        fl.addWidget(self.search, 1)
        fl.addWidget(self.count_label)
        fl.addWidget(self.load_more)
        lay.addWidget(frow)

        # ---- 便签列表 ----
        self.list = QListWidget()
        self.list.setObjectName("DeskList")
        self.list.setSpacing(6)
        self.list.itemDoubleClicked.connect(self._on_activate)
        hint = QLabel("双击列表项打开/前置对应贴纸便签；点「＋ 新建便签」贴一张，可拖动到屏幕任意位置并随窗保存。")
        hint.setObjectName("DeskHint")
        hint.setWordWrap(True)
        lay.addWidget(self.list, 1)
        lay.addWidget(hint)
        self.setCentralWidget(root)

    def _on_activate(self, item: QListWidgetItem):
        note = item.data(Qt.UserRole)
        if isinstance(note, dict):
            self.desk.open_note(note)

    def set_items(self, items):
        self.list.clear()
        for note in items:
            item = QListWidgetItem(_note_preview(note))
            item.setData(Qt.UserRole, note)
            item.setToolTip((note.get("content") or "")[:200] or "（空便签）")
            self.list.addItem(item)

    def confirm_loaded(self, loaded, total):
        self.count_label.setText(f"显示 {loaded} / 共 {total} 条")
        self.load_more.setVisible(total > loaded)

    def closeEvent(self, _e):
        # 关窗收进托盘，不退出程序
        self.hide()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.desk.tray.showMessage("便签桌", "已最小化到托盘，单击图标可恢复。",
                                       QSystemTrayIcon.Information, 2000)
        _e.ignore()


class Desk:
    """统筹便签桌主窗口、托盘与各贴纸便签窗口。"""

    def __init__(self, client: Client):
        self.client = client
        self.windows: list[NoteWindow] = []
        self._items: list[dict] = []
        self._total = 0
        self._page = 1
        self._q = ""
        self._PAGE_SIZE = 200

        self.tray = QSystemTrayIcon(make_sticky_icon(), QApplication.instance())
        self.tray.setToolTip("便签")
        menu = QMenu()
        act_open = QAction("打开便签桌", None)
        act_open.triggered.connect(self.show)
        act_new = QAction("新建便签", None)
        act_new.triggered.connect(self.new_note)
        act_restore = QAction("恢复全部便签", None)
        act_restore.triggered.connect(self.restore_all)
        act_quit = QAction("退出", None)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_open)
        menu.addSeparator()
        menu.addAction(act_new)
        menu.addAction(act_restore)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r == QSystemTrayIcon.Trigger and self.show())
        self.tray.show()

        self.win = DeskWindow(self)
        self.win.user_label.setText(f"当前用户：{self.client.username or '-'}")

    # ---- 生命周期 ----
    def show(self):
        self.reload()
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def quit_app(self):
        self.tray.hide()
        QApplication.quit()

    # ---- 数据（分页 + 搜索）----
    def notes(self):
        return list(self._items)

    def reload(self):
        self.reload_page(reset=True)

    def reload_page(self, reset=False):
        if reset:
            self._page = 1
            self._items = []
        try:
            items, total = self.client.list_notes(
                q=self._q, page=self._page, page_size=self._PAGE_SIZE)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.win, "提示", f"加载便签失败: {e}")
            return
        if reset:
            self._items = items
        else:
            self._items.extend(items)
        self._total = total
        self.win.set_items(self._items)
        self.win.confirm_loaded(len(self._items), self._total)

    def load_more(self):
        self._page += 1
        self.reload_page()

    def on_search(self):
        self._q = (self.win.search.text() or "").strip()
        self.reload_page(reset=True)

    # ---- 便签操作 ----
    def new_note(self):
        # 新建后回到第一页并清空搜索，保证新便签立刻可见
        self._q = ""
        self.win.search.setText("")
        try:
            note = self.client.create_note()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.win, "提示", str(e))
            return
        self.spawn(note)
        self.reload_page(reset=True)

    def open_note(self, note):
        """列表中已有则前置，否则打开贴纸窗口。"""
        for w in self.windows:
            if w.note.get("id") == note.get("id"):
                w.show()
                w.raise_()
                w.activateWindow()
                return
        self.spawn(note)

    def restore_all(self):
        for n in self.notes():
            self.spawn(n, restore=True)
        self.reload_page(reset=True)

    def spawn(self, note, restore=False):
        if restore and any(w.note.get("id") == note.get("id") for w in self.windows):
            return
        w = NoteWindow(self.client, note,
                       on_need_close=self._drop,
                       on_changed=self._note_changed)
        self.windows.append(w)
        w.show()

    def _note_changed(self, note):
        """便签保存后可调用：用最新数据更新便签桌列表项（文字 + 存储数据）。"""
        for i in range(self.win.list.count()):
            item = self.win.list.item(i)
            data = item.data(Qt.UserRole)
            if isinstance(data, dict) and data.get("id") == note.get("id"):
                item.setData(Qt.UserRole, note)
                item.setText(_note_preview(note))
                item.setToolTip((note.get("content") or "")[:200] or "（空便签）")
                break

    def _drop(self, w):
        if w in self.windows:
            self.windows.remove(w)
        self.reload_page(reset=True)
