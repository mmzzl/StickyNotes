"""登录/注册对话框（含验证码）。"""
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QDialog, QFormLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTabWidget, QVBoxLayout,
                             QWidget)

from api import Client


class LoginDialog(QDialog):
    def __init__(self, client: Client, parent=None):
        super().__init__(parent)
        self.client = client
        self.ok = False
        self.captcha_id = ""
        self.setWindowTitle("便签 - 登录")
        self.setMinimumWidth(380)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_login(), "登录")
        tabs.addTab(self._build_register(), "注册")
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        self._refresh_captcha()

    # ---- 登录页 ----
    def _build_login(self):
        w = QWidget()
        self.user = QLineEdit()
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.Password)
        self.captcha_input = QLineEdit()
        self.captcha_img = QLabel("加载验证码...")
        self.captcha_img.setFixedHeight(60)
        self.btn_reload = QPushButton("换一张")
        self.btn_reload.clicked.connect(self._refresh_captcha)

        form = QFormLayout(w)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.pwd)
        form.addRow(self.captcha_img, self.btn_reload)
        form.addRow("验证码", self.captcha_input)
        btn = QPushButton("登录")
        btn.clicked.connect(self._on_login)
        form.addRow(btn)
        return w

    def _refresh_captcha(self):
        try:
            cid, raw = self.client.fetch_captcha()
            self.captcha_id = cid
            pm = QPixmap()
            pm.loadFromData(raw)
            self.captcha_img.setPixmap(pm.scaledToWidth(140))
        except Exception as e:  # noqa: BLE001
            self.captcha_img.setText(f"验证码获取失败: {e}")

    def _on_login(self):
        try:
            self.client.login(self.user.text(), self.pwd.text(),
                              self.captcha_id, self.captcha_input.text())
        except Exception as e:  # noqa: BLE001
            self._fail(str(e))
            self._refresh_captcha()
        else:
            self.ok = True
            self.accept()

    # ---- 注册页 ----
    def _build_register(self):
        w = QWidget()
        self.reg_user = QLineEdit()
        self.reg_pwd = QLineEdit()
        self.reg_pwd.setEchoMode(QLineEdit.Password)
        self.reg_name = QLineEdit()
        form = QFormLayout(w)
        form.addRow("用户名", self.reg_user)
        form.addRow("密码(≥8位含大小写数字)", self.reg_pwd)
        form.addRow("显示名", self.reg_name)
        btn = QPushButton("注册并登录")
        btn.clicked.connect(self._on_register)
        form.addRow(btn)
        return w

    def _on_register(self):
        try:
            self.client.register(self.reg_user.text(), self.reg_pwd.text(),
                                 self.reg_name.text())
        except Exception as e:  # noqa: BLE001
            self._fail(str(e))
            self._refresh_captcha()
        else:
            self.ok = True
            self.accept()

    def _fail(self, msg):
        QMessageBox.warning(self, "提示", msg)
