#!/usr/bin/env bash
# 卸载服务（不删除代码和数据）
set -euo pipefail
APP_NAME="sticky-notes"
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行: sudo bash uninstall.sh" >&2
  exit 1
fi

# 先停备份定时器，避免卸载过程中又触发一次
systemctl stop ${APP_NAME}-backup.timer 2>/dev/null || true
systemctl disable ${APP_NAME}-backup.timer 2>/dev/null || true
systemctl stop ${APP_NAME} 2>/dev/null || true
systemctl disable ${APP_NAME} 2>/dev/null || true
rm -f /etc/systemd/system/${APP_NAME}.service \
      /etc/systemd/system/${APP_NAME}-backup.service \
      /etc/systemd/system/${APP_NAME}-backup.timer
systemctl daemon-reload
echo "服务已卸载（含每日备份定时器）。代码、data/ 与 data/backups/ 仍在安装目录，如需删除请手动执行 rm -rf <安装目录>"
