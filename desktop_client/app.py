"""便签客户端入口：有令牌则进主界面，无效则回登录框。"""
import sys

from PyQt5.QtWidgets import QApplication

from api import Client
from desk import Desk
from login_dialog import LoginDialog


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)     # 托盘常驻
    client = Client()

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

    Desk(client).restore_all()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
