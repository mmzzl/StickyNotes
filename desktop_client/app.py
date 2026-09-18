"""便签客户端入口：有令牌则进便签桌，无效则回登录框。"""
import sys

from PyQt5.QtWidgets import QApplication

from api import Client
from desk import Desk
from login_dialog import LoginDialog

# 全局主题：统一圆角、配色（黄主色）
APP_QSS = """
QWidget { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 14px; color: #333; }
QMainWindow, QDialog { background: #f4f3ed; }
QLineEdit, QTextEdit {
    background: #fff; border: 1px solid #ddd; border-radius: 8px;
    padding: 7px 10px; selection-background-color: #ffd54f;
}
QLineEdit:focus, QTextEdit:focus { border: 1px solid #ffb300; }
QPushButton {
    background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 7px 16px;
}
QPushButton:hover { border-color: #ffb300; color: #a87700; }
QPushButton#BtnPrimary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffc838, stop:1 #ffb300);
    border: none; color: #4a3300; font-weight: 700; padding: 9px 18px;
}
QPushButton#BtnPrimary:hover { color: #2e2000; }
QPushButton#BtnNew {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ffc838, stop:1 #ffb300);
    border: none; color: #4a3300; font-weight: 700; padding: 9px 20px; font-size: 15px;
}
QPushButton#BtnNew:hover { color: #2e2000; }
QPushButton#BtnGhost { background: transparent; border: 1px solid #ccc; }
QFrame#DeskBar { background: #fbf9f2; border-bottom: 1px solid #e6e2d2; }
QLabel#DeskBrand { font-size: 19px; font-weight: 700; color: #57450a; }
QLabel#DeskUser { color: #907c3a; font-size: 13px; }
QLineEdit#DeskSearch { padding: 5px 10px; }
QLabel#DeskCount { color: #999; font-size: 12px; }
QListWidget#DeskList {
    background: #f7f5ee; border: none; padding: 10px;
}
QListWidget#DeskList::item {
    background: #fff; border-radius: 10px; padding: 10px 12px;
    border: 1px solid #ece9df;
}
QListWidget#DeskList::item:hover { border-color: #ffd54f; background: #fffdf4; }
QListWidget#DeskList::item:selected {
    background: #fff3cd; color: #333; border-color: #ffc838;
}
QLabel#DeskHint { color: #999; font-size: 12px; padding: 8px 14px; background: #f4f3ed; }
QTabWidget::pane { border: 1px solid #ddd; border-radius: 8px; background: #fff; }
QTabBar::tab {
    background: transparent; padding: 8px 24px; margin-right: 2px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; color: #666;
}
QTabBar::tab:selected { background: #ffd54f; color: #4a3300; font-weight: 700; }
QLabel#CaptchaImg { background: #eee; border: 1px solid #ddd; border-radius: 6px; }
QLabel#ServerStatus { color: #2e8b57; font-size: 12px; }
QMessageBox QPushButton { min-width: 72px; }
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("便签")
    app.setQuitOnLastWindowClosed(False)     # 托盘常驻
    app.setStyleSheet(APP_QSS)
    client = Client()

    desk_ref = {"obj": None}
    guard = {"busy": False}

    def relogin():
        """令牌过期(401)：静默关掉便签窗，弹回登录框；登录成功回到便签桌，取消则退出。"""
        if guard["busy"]:
            return
        guard["busy"] = True
        try:
            client.on_auth_failed = None     # teardown 期间避免连环触发
            old = desk_ref["obj"]
            if old is not None:
                old.tray.hide()
                old.win.hide()
                for w in list(old.windows):
                    w.on_need_close = None   # 关窗不走 _drop，避免过期状态下再拉列表
                    w.close()
            dlg = LoginDialog(client)
            dlg.exec_()
            if dlg.ok:
                desk_ref["obj"] = Desk(client)
                desk_ref["obj"].show()
                client.on_auth_failed = relogin
            else:
                QApplication.quit()
        finally:
            guard["busy"] = False

    client.on_auth_failed = relogin

    if client.access_token:
        try:
            client.list_notes()               # 校验令牌有效性
        except Exception:                     # noqa: BLE001
            client.access_token = ""          # 失效则重新登录

    if not client.access_token:
        dlg = LoginDialog(client)
        dlg.exec_()
        if not dlg.ok:
            return 0

    desk_ref["obj"] = Desk(client)
    desk_ref["obj"].show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
