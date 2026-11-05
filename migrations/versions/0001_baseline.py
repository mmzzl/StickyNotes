"""基线迁移：与 2026-11-05 时的线上 schema 完全一致。

由 `alembic revision --autogenerate` 对空库生成（唯一真源是 backend/db/sql
的 SQLAlchemy metadata），再把随机 revision id 固定为可读的 0001_baseline。

重要：存量库（已有业务表、无 alembic_version）**不会执行本脚本的 upgrade()**，
而是由 backend/db/migration.py 补齐历史遗留列后 `stamp 0001_baseline`，
然后 upgrade head 只应用 0001 之后的新迁移。这样既不重建表，也不会跳过后续变更。

Revision ID: 0001_baseline
Revises: None
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "captchas",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("session_key", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("captchas", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_captchas_expires_at"), ["expires_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_captchas_session_key"), ["session_key"], unique=False)

    op.create_table(
        "devices",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("vendor", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("location", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("remark", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_devices_name"), ["name"], unique=False)

    op.create_table(
        "login_locks",
        sa.Column("lock_key", sa.String(length=128), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("login_locks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_login_locks_lock_key"), ["lock_key"], unique=True)
        batch_op.create_index(batch_op.f("ix_login_locks_locked_until"), ["locked_until"], unique=False)

    op.create_table(
        "menus",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("menu_type", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=128), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("permission_code", sa.String(length=128), nullable=False),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("menus", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_menus_parent_id"), ["parent_id"], unique=False)

    op.create_table(
        "notes",
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False),
        sa.Column("pos_x", sa.Integer(), nullable=False),
        sa.Column("pos_y", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_notes_owner_id"), ["owner_id"], unique=False)

    op.create_table(
        "password_history",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("password_history", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_password_history_created_at"), ["created_at"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_password_history_user_id"), ["user_id"], unique=False)

    op.create_table(
        "permissions",
        sa.Column("code", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=64), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("permissions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_permissions_code"), ["code"], unique=True)
        batch_op.create_index(batch_op.f("ix_permissions_module"), ["module"], unique=False)

    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_roles_code"), ["code"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_token"), ["token"], unique=True)
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)

    op.create_table(
        "users",
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(), nullable=True),
        sa.Column("token_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_username"), ["username"], unique=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("permission_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role_id"),
    )


def downgrade() -> None:
    """仅供开发时回退到空库；生产环境请勿对基线执行 downgrade。"""
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_username"))
    op.drop_table("users")
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))
        batch_op.drop_index(batch_op.f("ix_sessions_token"))
    op.drop_table("sessions")
    with op.batch_alter_table("roles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_roles_code"))
    op.drop_table("roles")
    with op.batch_alter_table("permissions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_permissions_module"))
        batch_op.drop_index(batch_op.f("ix_permissions_code"))
    op.drop_table("permissions")
    with op.batch_alter_table("password_history", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_password_history_user_id"))
        batch_op.drop_index(batch_op.f("ix_password_history_created_at"))
    op.drop_table("password_history")
    with op.batch_alter_table("notes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_notes_owner_id"))
    op.drop_table("notes")
    with op.batch_alter_table("menus", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_menus_parent_id"))
    op.drop_table("menus")
    with op.batch_alter_table("login_locks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_locks_locked_until"))
        batch_op.drop_index(batch_op.f("ix_login_locks_lock_key"))
    op.drop_table("login_locks")
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_devices_name"))
    op.drop_table("devices")
    with op.batch_alter_table("captchas", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_captchas_session_key"))
        batch_op.drop_index(batch_op.f("ix_captchas_expires_at"))
    op.drop_table("captchas")
