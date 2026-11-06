#!/usr/bin/env bash
# ============================================================
# 生产服务器部署脚本（CentOS/物理机）
# 用法: sudo bash install.sh [安装目录，默认 /opt/sticky-notes]
# 流程：安装依赖 → 配置 .env → 安装 systemd 服务 → 启动
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
chmod +x "$INSTALL_DIR/scripts/backup_db.py"
cp "$SERVICE_FILE" /etc/systemd/system/${APP_NAME}.service

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
python3 -m venv "$INSTALL_DIR/.venv" || exit 1
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# 5. 目录权限（数据目录需可写）
mkdir -p "$INSTALL_DIR/data" "$INSTALL_DIR/data/backups"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR"
chown -R "$RUN_USER:$RUN_GROUP" "$INSTALL_DIR/data"

# 6. 修正 service 里的路径/用户占位
sed -i "s|/data/work/sticky-notes|$INSTALL_DIR|g; s|^User=.*|User=$RUN_USER|; s|^Group=.*|Group=$RUN_GROUP|" /etc/systemd/system/${APP_NAME}.service

# 7. 注册并启动
systemctl daemon-reload
systemctl enable ${APP_NAME} || true
systemctl restart ${APP_NAME}

echo ""
echo "部署完成："
echo "  服务: systemctl status $APP_NAME"
echo "  日志: journalctl -u $APP_NAME -f"
echo "  页面: http://<服务器IP>:18000/   默认账号 admin/admin123（请立即改密）"
echo "  健康检查: curl http://127.0.0.1:18000/health"
echo "  记得修改 $INSTALL_DIR/.env 的 JWT_SECRET 后重启服务"
