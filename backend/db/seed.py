"""数据初始化：角色、权限、菜单、超管、示例设备。幂等（重复启动不重复插入）。

启动优化：以「种子内容指纹」作为闸门。稳态启动只做 3 次轻量校验查询，
种子内容与数据库标识均未变化时直接跳过全量初始化；指纹变化、换库或数据
被清空时才执行完整 seed。结果通过 get_seed_stats() 暴露给 GET /health。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from config import BACKEND_DIR, settings
from core import security
from core.logger import log
from repositories import devices, menus, permissions, roles, users

# 改动初始化逻辑时 +1，强制已初始化的库再跑一次
SEED_SCHEMA_VERSION = 2

# 角色：四档开箱即用
INIT_ROLES = [
    {"name": "系统管理员", "code": "superadmin", "description": "拥有全部权限，负责系统配置与用户管理"},
    {"name": "普通管理员", "code": "admin", "description": "负责业务功能日常运维"},
    {"name": "只读用户", "code": "readonly", "description": "只能查看，不能修改"},
    {"name": "普通用户", "code": "user", "description": "注册用户，仅管理自己的便签"},
]

# 权限：按模块分组
INIT_PERMISSIONS = [
    # 系统模块
    {"code": "sys:user:list", "name": "用户列表", "module": "system"},
    {"code": "sys:user:create", "name": "新增用户", "module": "system"},
    {"code": "sys:user:update", "name": "修改用户", "module": "system"},
    {"code": "sys:user:delete", "name": "删除用户", "module": "system"},
    {"code": "sys:role:list", "name": "角色列表", "module": "system"},
    {"code": "sys:role:create", "name": "新增角色", "module": "system"},
    {"code": "sys:role:update", "name": "修改角色", "module": "system"},
    {"code": "sys:role:delete", "name": "删除角色", "module": "system"},
    {"code": "sys:menu:list", "name": "菜单列表", "module": "system"},
    {"code": "sys:menu:create", "name": "新增菜单", "module": "system"},
    {"code": "sys:menu:update", "name": "修改菜单", "module": "system"},
    {"code": "sys:menu:delete", "name": "删除菜单", "module": "system"},
    # 通知模块
    {"code": "sys:notify:list", "name": "通知配置查看", "module": "system"},
    {"code": "sys:notify:update", "name": "通知配置管理", "module": "system"},
    # 设备模块（示例）
    {"code": "device:list", "name": "设备列表", "module": "device"},
    {"code": "device:create", "name": "新增设备", "module": "device"},
    {"code": "device:update", "name": "修改设备", "module": "device"},
    {"code": "device:delete", "name": "删除设备", "module": "device"},
    # 便签模块
    {"code": "note:list", "name": "便签列表", "module": "note"},
    {"code": "note:create", "name": "新建便签", "module": "note"},
    {"code": "note:update", "name": "修改便签", "module": "note"},
    {"code": "note:delete", "name": "删除便签", "module": "note"},
]

# 菜单树：id 固定，便于权限关联与树形构建
INIT_MENUS = [
    {"id": "m_dashboard", "name": "工作台", "menu_type": "menu", "path": "#/dashboard", "icon": "home", "parent_id": "", "sort_order": 1, "permission_code": "", "is_visible": True},
    {"id": "m_device", "name": "资产管理", "menu_type": "dir", "path": "", "icon": "server", "parent_id": "", "sort_order": 2, "permission_code": "", "is_visible": True},
    {"id": "m_sys", "name": "系统管理", "menu_type": "dir", "path": "", "icon": "setting", "parent_id": "", "sort_order": 99, "permission_code": "", "is_visible": True},
    {"id": "m_device_list", "name": "设备列表", "menu_type": "menu", "path": "#/devices", "icon": "", "parent_id": "m_device", "sort_order": 1, "permission_code": "device:list", "is_visible": True},
    {"id": "m_sys_users", "name": "用户管理", "menu_type": "menu", "path": "#/users", "icon": "", "parent_id": "m_sys", "sort_order": 1, "permission_code": "sys:user:list", "is_visible": True},
    {"id": "m_sys_roles", "name": "角色管理", "menu_type": "menu", "path": "#/roles", "icon": "", "parent_id": "m_sys", "sort_order": 2, "permission_code": "sys:role:list", "is_visible": True},
    {"id": "m_sys_menus", "name": "菜单管理", "menu_type": "menu", "path": "#/menus", "icon": "", "parent_id": "m_sys", "sort_order": 3, "permission_code": "sys:menu:list", "is_visible": True},
    {"id": "m_sys_notify", "name": "通知配置", "menu_type": "menu", "path": "#/notify/channels", "icon": "", "parent_id": "m_sys", "sort_order": 4, "permission_code": "sys:notify:list", "is_visible": True},
    {"id": "m_sys_notify_policy", "name": "订阅策略", "menu_type": "menu", "path": "#/notify/policy", "icon": "", "parent_id": "m_sys", "sort_order": 5, "permission_code": "sys:notify:list", "is_visible": True},
]

# 角色拥有的权限（按 code 关联），超级管理员不用绑定（is_superuser 通配）
ROLE_PERMISSION_CODES = {
    "superadmin": [],
    "admin": [
        "sys:user:list", "sys:role:list", "sys:menu:list",
        "sys:notify:list", "sys:notify:update",
        "device:list", "device:create", "device:update", "device:delete",
        "note:list", "note:create", "note:update", "note:delete",
    ],
    "readonly": ["device:list"],
    "user": ["note:list", "note:create", "note:update", "note:delete"],
}

INIT_DEVICES = [
    {"name": "核心交换机-01", "ip": "10.0.0.1", "vendor": "Huawei", "model": "S12700", "location": "机房A", "status": "online", "owner": "网络组", "remark": "核心汇聚"},
    {"name": "防火墙-01", "ip": "10.0.0.2", "vendor": "Sangfor", "model": "AF-2000", "location": "机房A", "status": "online", "owner": "安全组", "remark": ""},
    {"name": "Web服务器", "ip": "10.0.1.10", "vendor": "DELL", "model": "R740", "location": "机房B", "status": "offline", "owner": "运维组", "remark": "计划停机维护"},
]

_LAST_STATS: dict = {"mode": "not_run", "fingerprint": "", "created": 0, "checks": 0}
_created = 0


def get_seed_stats() -> dict:
    """本次启动 seed 的真实行为，供 /health 展示（不是估算值）。"""
    return dict(_LAST_STATS)


def _fingerprint() -> str:
    payload = {
        "schema": SEED_SCHEMA_VERSION,
        "roles": INIT_ROLES,
        "permissions": INIT_PERMISSIONS,
        "menus": INIT_MENUS,
        "devices": INIT_DEVICES,
        "role_permissions": ROLE_PERMISSION_CODES,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _fingerprint_file() -> Path:
    # 数据库标识参与文件名：换库/换 DB_BACKEND 后不会误判「已初始化」
    identity = "|".join(str(v or "") for v in [
        settings.db_backend, settings.database_url, settings.mongo_uri, settings.mongo_db,
    ])
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return BACKEND_DIR.parent / "data" / f"seed.{suffix}.version"


async def _create_if_missing(repo, matcher: dict, data: dict) -> str:
    """存在则返回 id，不存在则创建，返回 id。"""
    global _created

    existing = await repo._find_one(matcher)
    if existing:
        return existing["id"]
    created = await repo.create({**data, **matcher})
    _created += 1
    return created["id"]


async def _data_intact() -> bool:
    """指纹命中时仍做 3 次轻量校验，防止库被清空/换库后误跳过。"""
    global _LAST_STATS

    try:
        role_rows = await roles._find_all()
        perm_rows = await permissions._find_all()
        admin = await users.get_by_username("admin")
    except Exception as e:  # 任一实现不支持时，保守回退到完整 seed
        log.warning("seed 校验查询失败，回退完整初始化: {}", e)
        return False

    _LAST_STATS["checks"] = 3
    have_roles = {r.get("code") for r in role_rows}
    have_perms = {p.get("code") for p in perm_rows}
    return (
        admin is not None
        and all(r["code"] in have_roles for r in INIT_ROLES)
        and all(p["code"] in have_perms for p in INIT_PERMISSIONS)
    )


async def seed_all() -> None:
    """幂等初始化：角色→权限→菜单→超管→绑定→示例数据（带指纹闸门）。"""
    global _LAST_STATS, _created

    _created = 0
    fp = _fingerprint()
    fp_file = _fingerprint_file()
    _LAST_STATS = {"mode": "full", "fingerprint": fp[:12], "created": 0, "checks": 0}

    if settings.startup_legacy:
        log.warning("STARTUP_LEGACY=true：跳过指纹闸门，强制全量 seed（性能对照模式）")
        cached = ""
        _LAST_STATS["mode"] = "legacy_full"
    else:
        try:
            cached = fp_file.read_text(encoding="utf-8").strip() if fp_file.exists() else ""
        except OSError as e:
            log.warning("读取 seed 指纹失败，执行完整初始化: {}", e)
            cached = ""

    if cached == fp and await _data_intact():
        _LAST_STATS["mode"] = "skipped"
        log.info("seed 未变更（指纹 {}），跳过初始化；本次仅 3 次校验查询", fp[:12])
        return

    # 角色
    role_ids = {}
    for r in INIT_ROLES:
        rid = await _create_if_missing(roles, {"code": r["code"]}, r)
        role_ids[r["code"]] = rid

    # 权限
    perm_ids = {}
    for p in INIT_PERMISSIONS:
        pid = await _create_if_missing(permissions, {"code": p["code"]}, p)
        perm_ids[p["code"]] = pid

    # 给非超管角色绑定权限（set_permissions 内部会比对，未变化则不写库）
    for code, pcodes in ROLE_PERMISSION_CODES.items():
        if not pcodes:
            continue
        rid = role_ids[code]
        target_ids = [perm_ids[c] for c in pcodes if c in perm_ids]
        await roles.set_permissions(rid, target_ids)

    # 菜单
    for m in INIT_MENUS:
        await _create_if_missing(menus, {"id": m["id"]}, {k: v for k, v in m.items() if k != "id"})

    # 超管账号
    super_user = await users.get_by_username("admin")
    if not super_user:
        await users.create({
            "username": "admin",
            "password_hash": security.hash_password("admin123"),
            "display_name": "系统管理员",
            "email": "",
            "is_active": True,
            "is_superuser": True,
            "password_changed_at": datetime.now(timezone.utc),
        })
        _created += 1
        log.info("已创建默认超管账号 admin/admin123（生产环境请立即改密）")

    # 示例设备（演示模块数据，幂等：按名字判断）
    for d in INIT_DEVICES:
        existing = await devices._find_one({"name": d["name"]})
        if not existing:
            await devices.create(d)
            _created += 1

    if not settings.startup_legacy:
        try:
            fp_file.parent.mkdir(parents=True, exist_ok=True)
            fp_file.write_text(fp, encoding="utf-8")
        except OSError as e:
            log.warning("写入 seed 指纹失败（下次启动会重跑 seed，不影响本次）: {}", e)

    _LAST_STATS["created"] = _created
    log.info("数据初始化完成，本次新建实体 {} 条", _created)
