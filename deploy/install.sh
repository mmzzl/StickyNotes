#!/usr/bin/env bash
# ============================================================
# 生产服务器部署脚本（通用 systemd 发行版）
#   Ubuntu 20.04+ / Debian 11+ / CentOS Stream 8+ / RHEL 8+ / Rocky / AlmaLinux
# 用法: sudo bash install.sh [安装目录，默认 /opt/sticky-notes]
# 流程：检查系统依赖 → 配置 .env → 安装 systemd 服务与每日备份定时器 → 启动
# ============================================================
set -euo pipefail

APP_NAME="sticky-notes"
INSTALL_DIR="${1:-/opt/sticky-notes}"
# 与根目录实际使用的 notes.service 保持同一份，不再有两套 unit
SERVICE_FILE="$(dirname "$0")/../notes.service"
SRC_DIR="$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行: sudo bash install.sh" >&2
  exit 1
fi

echo "==> 安装到 $INSTALL_DIR"

# 0. 系统依赖检查：不同发行版包名不同，缺失时直接给出可复制的命令，
#    而不是让 pip 或 venv 在几十秒后抛一段难懂的报错。
PKG=""
if command -v apt-get >/dev/null 2>&1; then
  PKG="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG="dnf"
elif command -v yum >/dev/null 2>&1; then
  PKG="yum"
fi

# 解释器必须是 3.10+：RHEL 9 / CentOS Stream 9 默认 python3 是 3.9，
# 直接用它会在导入 `str | None` 这类新语法时失败，所以按版本挑一个可用的。
PYBIN=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 \
     && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYBIN="$cand"
    break
  fi
done

if [ -z "$PYBIN" ]; then
  echo "未找到 Python 3.10+（项目 requires-python >= 3.10）。请先安装：" >&2
  case "$PKG" in
    apt)
      echo "  Ubuntu 22.04+ / Debian 12+ 自带足够新的 python3："
      echo "    sudo apt-get install -y python3 python3-venv python3-dev"
      ;;
    dnf|yum)
      echo "  RHEL 9 / CentOS Stream 9 / Rocky 9 / Alma 9（默认 3.9，需装 3.11）："
      echo "    sudo $PKG install -y python3.11 python3.11-pip"
      ;;
    *) echo "  请安装 Python 3.10 及以上版本" ;;
  esac
  echo "  CentOS 7/8 已 EOL，建议升级系统或改用容器部署。" >&2
  exit 1
fi

if ! "$PYBIN" -c 'import ensurepip' >/dev/null 2>&1; then
  echo "$PYBIN 缺少 ensurepip，无法创建虚拟环境。请先安装：" >&2
  case "$PKG" in
    apt) echo "  sudo apt-get install -y python3-venv python3-dev" ;;
    dnf|yum) echo "  sudo $PKG install -y python3.11 python3.11-pip" ;;
    *) echo "  请安装 python3-venv / python3-devel 对应包" ;;
  esac
  exit 1
fi

echo "==> 使用解释器: $PYBIN ($("$PYBIN" -V 2>&1))"

if ! command -v curl >/dev/null 2>&1; then
  echo "警告：未找到 curl。notes.service 的启动就绪探测依赖它，服务可能被判启动失败。" >&2
  case "$PKG" in
    apt) echo "  建议先执行: sudo apt-get install -y curl" ;;
    dnf|yum) echo "  建议先执行: sudo $PKG install -y curl" ;;
  esac
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "未找到 systemctl：本脚本依赖 systemd。容器/非 systemd 环境请改用 scripts/backup_daily.sh + cron。" >&2
  exit 1
fi

# 1. 拷贝代码
# alembic.ini + migrations/ 是运行时必需：启动时会按版本同步表结构，缺了服务起不来。
# start.sh 也必须拷贝——notes.service 的 ExecStart 指向它。
# scripts/ 是独立备份 CLI，cron / 计划任务会调用，服务没开也能用。
# requirements.txt 供不使用 PEP 517 的排障/离线安装场景，backend/requirements.txt 会引用它。
mkdir -p "$INSTALL_DIR"
cp -r "$SRC_DIR/backend" "$SRC_DIR/frontend" "$SRC_DIR/migrations" \
      "$SRC_DIR/scripts" "$INSTALL_DIR/"
cp "$SRC_DIR/pyproject.toml" "$SRC_DIR/alembic.ini" "$SRC_DIR/start.sh" \
   "$SRC_DIR/requirements.txt" "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/start.sh"
chmod +x "$INSTALL_DIR/scripts/backup_db.py" "$INSTALL_DIR/scripts/backup_daily.sh"
cp "$SERVICE_FILE" /etc/systemd/system/${APP_NAME}.service
# 每日备份：oneshot service + timer，错过的执行由 systemd 在开机后补跑
cp "$SRC_DIR/deploy/backup.service" /etc/systemd/system/${APP_NAME}-backup.service
cp "$SRC_DIR/deploy/backup.timer" /etc/systemd/system/${APP_NAME}-backup.timer

# 2. 创建运行用户（默认 admin/admin，按 /etc/passwd 判断）
RUN_USER="${SERVICE_USER:-admin}"
RUN_GROUP="${SERVICE_GROUP:-admin}"
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  useradd -r -s /sbin/nologin "$RUN_USER" 2>/dev/null || true
fi

# 3. .env 配置
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$SRC_DIR/.env.example" "$INSTALL_DIR/.env"
  echo "已生成 $INSTALL_DIR/.env，请编辑其中的 JWT_SECRET / 数据库等配置！"
fi

# 4. 创建虚拟环境并安装依赖
echo "==> 安装 Python 依赖（虚拟环境 $INSTALL_DIR/.venv）"
"$PYBIN" -m venv "$INSTALL_DIR/.venv" || exit 1
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# 5. 目录权限（数据目录需可写）
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/data/backups"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR/data"

# 6. 修正 unit 里的路径/用户占位（主服务与备份服务共用同一份占位约定）
for unit in "${APP_NAME}.service" "${APP_NAME}-backup.service"; do
  sed -i "s|/data/work/sticky-notes|$INSTALL_DIR|g; s|^User=.*|User=$RUN_USER|; s|^Group=.*|Group=$RUN_GROUP|" \
    "/etc/systemd/system/$unit"
done
# timer 的 Unit= 跟随实际服务名（APP_NAME 改名时不会指错）
sed -i "s|sticky-notes-backup.service|${APP_NAME}-backup.service|" \
  "/etc/systemd/system/${APP_NAME}-backup.timer"

# 7. 注册并启动
systemctl daemon-reload
systemctl enable ${APP_NAME} || true
systemctl restart ${APP_NAME}

# 8. 启用每日备份定时器，并立刻生成首份（当天已备份会幂等跳过）
echo "==> 启用每日备份定时器（03:30，错过开机补跑）"
if systemctl enable --now ${APP_NAME}-backup.timer; then
  systemctl start ${APP_NAME}-backup.service \
    || echo "  首次备份未成功，详见 journalctl -u ${APP_NAME}-backup -n 50" >&2
else
  echo "  警告：定时器启用失败，可手动 systemctl enable --now ${APP_NAME}-backup.timer" >&2
fi

echo ""
echo "部署完成："
echo "  服务: systemctl status $APP_NAME"
echo "  日志: journalctl -u $APP_NAME -f"
echo "  页面: http://<服务器IP>:18000/   默认账号 admin/admin123（请立即改密）"
echo "  健康检查: curl http://127.0.0.1:18000/health"
echo "  备份: systemctl list-timers ${APP_NAME}-backup.timer"
echo "  备份日志: journalctl -u ${APP_NAME}-backup -n 50"
echo "  手动备份: sudo systemctl start ${APP_NAME}-backup.service"
echo "  记得修改 $INSTALL_DIR/.env 的 JWT_SECRET 后重启服务"
