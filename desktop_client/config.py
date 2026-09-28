# config.py —— 配置与令牌持久化
import json
import os
from pathlib import Path

DEFAULT_BASE_URL = "http://10.74.64.100:18000"

_EMPTY = {"base_url": DEFAULT_BASE_URL, "access_token": "",
          "refresh_token": "", "username": ""}


def config_dir() -> Path:
    """配置目录。

    刻意做成函数而不是模块级常量：XDG_CONFIG_HOME 可能在导入之后才被改
    （测试就靠这个把配置隔离到临时目录）。若在导入时就把路径算死，运行期
    再改环境变量就不生效，测试会读写真实的用户配置。
    """
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sticky_notes"


def config_file() -> Path:
    return config_dir() / "config.json"


def view_mode_file() -> Path:
    """每条便签上次停留的显示模式（源码/排版），与账号无关，单独存一个轻量文件。"""
    return config_dir() / "view_modes.json"


def load_config() -> dict:
    f = config_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_EMPTY)


def save_config(cfg: dict) -> None:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / "config.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def load_view_modes() -> dict:
    """读取 {便签id: 模式}。模式取值 "source"（源码）/"rich"（排版）。"""
    f = view_mode_file()
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_view_mode(note_id: str, mode: str) -> None:
    """记住某条便签的显示模式；写失败不打扰用户（顶多是下次不记住）。"""
    if not note_id:
        return
    modes = load_view_modes()
    if modes.get(note_id) == mode:
        return
    modes[note_id] = mode
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "view_modes.json").write_text(
            json.dumps(modes, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
