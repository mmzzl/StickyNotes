"""Repository 出口：业务层只从这里 import，感知不到底层 DB。

用法：
    from repositories import users, rbac
    user = await users.get_by_username("admin")
"""

from repositories.user import UserRepo
from repositories.role import RoleRepo
from repositories.permission import PermissionRepo
from repositories.menu import MenuRepo
from repositories.device import DeviceRepo
from repositories.session import SessionRepo
from repositories.captcha import CaptchaRepo
from repositories.login_lock import LoginLockRepo
from repositories.password_history import PasswordHistoryRepo
from repositories.rbac import RbacRepo

# 单例（各自持有无状态方法，可安全复用）
users = UserRepo()
roles = RoleRepo()
permissions = PermissionRepo()
menus = MenuRepo()
devices = DeviceRepo()
sessions = SessionRepo()
captchas = CaptchaRepo()
login_locks = LoginLockRepo()
password_history = PasswordHistoryRepo()
rbac = RbacRepo()

__all__ = [
    "UserRepo", "RoleRepo", "PermissionRepo", "MenuRepo", "DeviceRepo", "SessionRepo",
    "CaptchaRepo", "LoginLockRepo", "PasswordHistoryRepo", "RbacRepo",
    "users", "roles", "permissions", "menus", "devices", "sessions", "captchas",
    "login_locks", "password_history", "rbac",
]
