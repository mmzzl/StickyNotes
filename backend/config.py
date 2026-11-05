"""应用全局配置：pydantic-settings，环境变量 + .env 双层覆盖。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 包所在目录（config.py 所在处）
BACKEND_DIR = Path(__file__).resolve().parent

# 随源码分发的默认签名密钥：仅便于本地体验，生产必须用 .env 覆盖
_DEFAULT_JWT_SECRET = "6dace548df771b4a47e6d1fcc1a1d849a21624026ca2c00df0e8201e935ee30c"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # 容忍 .env 里多余的键
    )

    # 应用基础
    app_name: str = "StickyNotes 便签应用"
    api_prefix: str = "/api/v1"
    debug: bool = False          # 只控制日志细节/热重载，不再打开全量 SQL 回显
    echo_sql: bool = False       # 独立开关：仅排查 SQL 时才置 true
    # 仅供性能对照：true 时回退到「串行初始化 + 全量 seed + SQL 回显」的旧行为
    startup_legacy: bool = False

    # 运行环境：production 下弱密钥/默认口令直接拒绝启动；development 只告警
    app_env: str = "development"
    require_secure_secrets: bool = False
    # 通知渠道敏感字段的独立加密密钥（与 JWT_SECRET 分离，轮换互不影响）
    data_encryption_key: str = ""

    # 允许跨域的前端来源（逗号分隔；生产部署请改为实际域名）
    cors_origins: str = "http://localhost:18000,http://127.0.0.1:18000"

    # 认证方式：jwt | session
    auth_mode: str = "jwt"

    # JWT 认证（默认值仅便于本地体验，生产必须在 .env 覆盖）
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7

    # Session 认证
    session_expire_hours: int = 8
    session_cookie_name: str = "session_id"
    session_cookie_secure: bool = False
    session_cookie_httponly: bool = True

    # 数据库
    db_backend: str = "sqlite"  # sqlite | mongodb
    database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR.parent / 'data' / 'app.db'}"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "app_template"

    # 验证码（登录防爆破）
    captcha_enabled: bool = True           # 是否启用验证码
    captcha_length: int = 4                # 字符个数
    captcha_expire_seconds: int = 300      # 有效期（秒）
    captcha_cookie_name: str = "anon_sid"  # 匿名会话 cookie（绑定验证码防伪造）
    captcha_cookie_httponly: bool = True

    # 账户锁定（暴力破解防护，参考产品语义）
    login_max_failures: int = 5            # 登录失败次数上限（含验证码错误，共用计数）
    captcha_max_failures: int = 3          # 验证码失败次数上限（与登录共用计数）
    lock_seconds: int = 300                # 命中上限后的锁定时间（秒），0 表示不锁定
    # 默认不信任代理头：直连部署时伪造 X-Forwarded-For 可绕过 IP 维度锁定。
    # 仅在确认后端有可信反向代理（会覆盖该头）时才置 true。
    lock_trust_proxy: bool = False

    # 注册与接口限流
    register_enabled: bool = True          # 关闭后不再开放自助注册
    register_default_role: str = "user"    # 新用户默认角色 code；设成 admin 会授予管理权限，生产勿用
    rate_limit_enabled: bool = True        # 进程内滑动窗口限流（单 worker 部署）
    rate_limit_per_minute: int = 30        # 单 IP 单接口每分钟上限

    # 密码策略（长度/复杂度；登录、自助改密、管理员重置统一校验）
    password_min_length: int = 8                # 最小长度
    password_require_upper: bool = True         # 必须含大写字母
    password_require_lower: bool = True         # 必须含小写字母
    password_require_digit: bool = True         # 必须含数字
    password_require_special: bool = False      # 必须含特殊符号
    # 定期修改密码（jwt/session 两种认证模式通用）
    password_expire_days: int = 90              # 密码有效期（天），0 = 不启用定期改密
    password_expire_warning_days: int = 7       # 到期前 N 天登录响应带 warning 提醒
    password_history_count: int = 5             # 记录最近 N 个历史密码并拒绝重用，0 = 不记录

    # 日志
    log_level: str = "INFO"
    log_dir: str = str(BACKEND_DIR.parent / "data" / "logs")

    # 定时任务（inputs.conf 风格配置文件，相对 backend/）
    schedule_conf: str = str(BACKEND_DIR / "conf" / "inputs.conf")

    # ---------- 通知组件（渠道配置 / 订阅策略，对齐 secvisual alarm_policy.json 风格）----------
    notify_enabled: bool = True
    notify_channels_file: str = str(BACKEND_DIR / "conf" / "notify_channels.json")
    notify_policy_file: str = str(BACKEND_DIR / "conf" / "notify_policy.json")
    notify_state_file: str = str(BACKEND_DIR.parent / "data" / "notify_send_state.json")
    schedule_enabled: bool = True

    # 部署
    service_user: str = "admin"
    service_group: str = "admin"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS 逗号分隔字符串 → 列表（避免 .env 里写 JSON 数组的坑）。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def is_sqlite(self) -> bool:
        return self.db_backend == "sqlite"

    def is_mongodb(self) -> bool:
        return self.db_backend == "mongodb"

    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("production", "prod")

    def weak_secrets(self) -> list[str]:
        """返回仍不安全的密钥/口令项，供启动期阻断或告警。"""
        weak = []
        if self.auth_mode == "jwt" and (
            self.jwt_secret == _DEFAULT_JWT_SECRET
            or self.jwt_secret.startswith("change-me")
            or len(self.jwt_secret) < 32
        ):
            weak.append("JWT_SECRET")
        if not self.data_encryption_key or len(self.data_encryption_key) < 32:
            weak.append("DATA_ENCRYPTION_KEY")
        return weak


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
