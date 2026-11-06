"""部署产物一致性：Linux 下的 systemd timer / shell 入口 / 安装脚本不能脱节。

这类问题在 Windows 开发机上「看不出来」，要到 Linux 部署时才发现 unit 指向
不存在的脚本或 install.sh 忘了注册 timer。用静态断言把引用关系钉住。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
SCRIPTS = ROOT / "scripts"


def _text(path: Path) -> str:
    assert path.is_file(), f"缺少文件: {path}"
    return path.read_text(encoding="utf-8")


def test_linux_daily_entry_point():
    body = _text(SCRIPTS / "backup_daily.sh")
    assert body.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in body
    assert "backup_db.py" in body and "--once" in body
    # 解释器优先级：PYTHON 覆盖 > 虚拟环境 > 系统 python3
    assert "PYTHON" in body
    assert ".venv/bin/python" in body and "command -v python3" in body


def test_systemd_units_are_wired():
    svc = _text(DEPLOY / "backup.service")
    tmr = _text(DEPLOY / "backup.timer")
    assert "Type=oneshot" in svc
    assert "backup_db.py --once" in svc
    assert "WorkingDirectory=" in svc
    assert "User=" in svc and "Group=" in svc
    assert "OnCalendar=*-*-* 03:30:00" in tmr
    assert "Persistent=true" in tmr          # 关机错过要补跑
    assert "WantedBy=timers.target" in tmr
    assert "Unit=sticky-notes-backup.service" in tmr


def test_systemd_units_are_parsable():
    """unit 是 INI 结构，段名/键写错时 systemd 才会报错，这里提前拦。"""
    import configparser

    for name in ("backup.service", "backup.timer"):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(DEPLOY / name, encoding="utf-8")
        assert cp.sections(), f"{name} 没有任何段"

    svc = configparser.ConfigParser(interpolation=None, strict=False)
    svc.read(DEPLOY / "backup.service", encoding="utf-8")
    assert svc["Service"]["Type"] == "oneshot"
    assert svc["Service"]["ExecStart"].endswith("backup_db.py --once")

    tmr = configparser.ConfigParser(interpolation=None, strict=False)
    tmr.read(DEPLOY / "backup.timer", encoding="utf-8")
    assert tmr["Timer"]["OnCalendar"] == "*-*-* 03:30:00"
    assert tmr["Timer"]["Persistent"] == "true"
    assert tmr["Install"]["WantedBy"] == "timers.target"


def test_install_covers_both_distro_families_and_registers_timer():
    body = _text(DEPLOY / "install.sh")
    # 两种包管理器族都要有依赖提示
    assert "apt" in body and ("dnf" in body or "yum" in body)
    assert "python3-venv" in body
    # 必须按版本挑解释器：RHEL 9 / CentOS Stream 9 默认 3.9 不满足 >=3.10
    assert "sys.version_info >= (3, 10)" in body
    assert "PYBIN" in body and "python3.11" in body
    # 备份脚本与 unit 都要落盘、设权限并启用
    assert "backup_daily.sh" in body
    assert "backup.service" in body and "backup.timer" in body
    assert "enable --now" in body
    # 路径/用户占位符仍可被 sed 替换
    assert "/data/work/sticky-notes" in body
    assert "s|^User=.*|User=$RUN_USER|" in body


def test_uninstall_removes_backup_units():
    body = _text(DEPLOY / "uninstall.sh")
    assert 'APP_NAME="sticky-notes"' in body
    assert "backup.timer" in body and "backup.service" in body
