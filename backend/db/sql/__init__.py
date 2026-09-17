"""SQL 模型汇总：import 全部模型以注册进 Base.metadata。"""

from db.sql.base import Base
from db.sql.user import User
from db.sql.rbac import Role, Permission, Menu, Session, user_roles, role_permissions
from db.sql.device import Device
from db.sql.note import Note
from db.sql.captcha import Captcha
from db.sql.login_lock import LoginLock
from db.sql.password_history import PasswordHistory

__all__ = [
    "Base",
    "User",
    "Role",
    "Permission",
    "Menu",
    "Session",
    "Device",
    "Note",
    "Captcha",
    "LoginLock",
    "PasswordHistory",
    "user_roles",
    "role_permissions",
]
