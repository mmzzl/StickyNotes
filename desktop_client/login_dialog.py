"""登录/注册对话框（含验证码、可配置服务器地址）。"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
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
        self.setMinimumWidth(400)
        self.setObjectName("LoginDialog")

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        # 服务器地址
        srv_row = QHBoxLayout()
        srv_row.addWidget(QLabel("服务器地址"))
        self.server_url = QLineEdit(self.client.base_url)
        self.server_url.setPlaceholderText("http://host:port")
        self.server_url.editingFinished.connect(self._on_server_changed)
        self.server_status = QLabel("")
        self.server_status.setObjectName("ServerStatus")
        srv_row.addWidget(self.server_url, 1)
        srv_row.addWidget(self.server_status)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_login(), "登录")
        self.tabs.addTab(self._build_register(), "注册")

        lay.addLayout(srv_row)
        lay.addWidget(self.tabs)
        self._refresh_captcha()

    # ---- 服务器地址 ----
    def _on_server_changed(self):
        url = self.server_url.text().strip()
        if not url:
            return
        if url != self.client.base_url:
            self.client.set_base_url(url)
            self.server_status.setText("已切换")
        else:
            self.server_status.setText("")
        self._refresh_captcha()

    # ---- 登录页 ----
    def _build_login(self):
        w = QWidget()
        self.user = QLineEdit()
        self.user.setPlaceholderText("用户名")
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.Password)
        self.pwd.setPlaceholderText("密码")
        self.captcha_input = QLineEdit()
        self.captcha_input.setPlaceholderText("验证码")
        self.captcha_img = QLabel("加载验证码...")
        self.captcha_img.setObjectName("CaptchaImg")
        self.captcha_img.setFixedSize(150, 52)
        self.captcha_img.setCursor(Qt.PointingHandCursor)
        self.captcha_img.mousePressEvent = lambda _e: self._refresh_captcha()
        self.btn_reload = QPushButton("换一张")
        self.btn_reload.setObjectName("BtnGhost")
        self.btn_reload.clicked.connect(self._refresh_captcha)

        form = QFormLayout(w)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.pwd)
        cp_row = QHBoxLayout()
        cp_row.addWidget(self.captcha_img)
        cp_row.addWidget(self.btn_reload)
        form.addRow(cp_row)
        form.addRow("验证码", self.captcha_input)
        btn = QPushButton("登 录")
        btn.setObjectName("BtnPrimary")
        btn.clicked.connect(self._on_login)
        form.addRow(btn)
        return w

    def _refresh_captcha(self):
        try:
            cid, raw = self.client.fetch_captcha()
            self.captcha_id = cid
            pm = QPixmap()
            pm.loadFromData(raw)
            self.captcha_img.setPixmap(pm.scaledToWidth(148))
            self.captcha_img.setStyleSheet("")
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
        self.reg_user.setPlaceholderText("登录用，2~64 位")
        self.reg_pwd = QLineEdit()
        self.reg_pwd.setEchoMode(QLineEdit.Password)
        self.reg_pwd.setPlaceholderText("≥8 位，含大小写字母与数字")
        self.reg_name = QLineEdit()
        self.reg_name.setPlaceholderText("可选")
        form = QFormLayout(w)
        form.addRow("用户名", self.reg_user)
        form.addRow("密码", self.reg_pwd)
        form.addRow("显示名", self.reg_name)
        btn = QPushButton("注册并登录")
        btn.setObjectName("BtnPrimary")
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
