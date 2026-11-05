#!/usr/bin/env bash
# Linux (Ubuntu/Debian) 打包脚本，对应 build_exe.bat
#
# 与 Windows 版的差异：
#   1. pywebview 在 Linux 上走 GTK/WebKit2 后端，运行时依赖系统库（python3-gi、
#      gir1.2-webkit2-4.0/4.1、libayatana-appindicator3），PyInstaller 打不进这些。
#      目标机器也需要装同样的一批库，脚本会给出 apt 命令。
#   2. PyGObject(gi) 不能从 pip 可靠安装，必须用系统的 python3-gi。因此虚拟环境
#      要用 --system-site-packages 创建，否则打包出来运行时报 "No module named 'gi'"。
#   3. WebKitGTK 的 .so 与 GIR 资源由系统提供，不随包分发；spec 里已把它们排除。
#
# 用法：
#   ./build_exe.sh              # 完整打包
#   ./build_exe.sh --check      # 只检查环境依赖，不打包
#   ./build_exe.sh --clean      # 打包前清理 build/ dist-linux/
#   ./build_exe.sh --console    # 保留终端输出（调试用，能看到 traceback）

set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="StickyNotesWeb"
SPEC="${APP_NAME}.spec"
DIST_DIR="dist-linux"
CHECK_ONLY=0
CLEAN=0
CONSOLE=0

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --clean) CLEAN=1 ;;
    --console) CONSOLE=1 ;;
    -h|--help) sed -n '/^# Linux (Ubuntu/,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "未知参数：$arg（可用：--check / --clean / --help）" >&2; exit 2 ;;
  esac
done

info() { printf '\033[1;34m[build]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn ]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------- 1. 平台与基础工具 ----------
[[ "$(uname -s)" == "Linux" ]] || fail "这个脚本只能在 Linux 上运行（当前：$(uname -s)）"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || fail "找不到 $PY，请先安装 python3"

PY_VER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
info "Python：$("$PY" --version 2>&1)（$PY_VER）"
"$PY" - <<'PYCHECK' || fail "需要 Python >= 3.10"
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PYCHECK

# ---------- 2. 系统依赖检查 ----------
# 这些是运行时库，PyInstaller 打不进包，目标机也要装。
missing_pkgs=()

have_apt_pkg() {
  command -v dpkg >/dev/null 2>&1 && dpkg -s "$1" >/dev/null 2>&1
}

# PyGObject / GTK
if ! "$PY" -c "import gi" >/dev/null 2>&1; then
  missing_pkgs+=("python3-gi" "python3-gi-cairo")
fi

# WebKit2：pywebview 的 gtk 后端需要 gir1.2-webkit2-4.1（24.04）或 4.0（22.04）
if ! "$PY" -c "
import gi
for v in ('4.1', '4.0'):
    try:
        gi.require_version('WebKit2', v); raise SystemExit(0)
    except Exception: pass
raise SystemExit(1)
" >/dev/null 2>&1; then
  # 导不进 WebKit2：按发行版版本推荐整套包（24.04 用 4.1，22.04 用 4.0）
  if have_apt_pkg libwebkit2gtk-4.1-0; then
    missing_pkgs+=("gir1.2-webkit2-4.1")
  else
    missing_pkgs+=("gir1.2-webkit2-4.1" "libwebkit2gtk-4.1-0")
  fi
fi

# 托盘图标：pystray 优先 AppIndicator3，回退 Ayatana 或 GTK 状态图标
if ! "$PY" -c "
import gi
for ns in ('AyatanaAppIndicator3', 'AppIndicator3'):
    try:
        gi.require_version(ns, '0.1'); raise SystemExit(0)
    except Exception: pass
raise SystemExit(1)
" >/dev/null 2>&1; then
  warn "未检测到 AppIndicator，托盘会回退到 GTK 状态图标（部分桌面环境不显示）"
  warn "建议安装：sudo apt install libayatana-appindicator3-1 gir1.2-ayatanaappindicator3-0.1"
fi

if (( ${#missing_pkgs[@]:-0} > 0 )); then
  fail "缺少系统依赖：${missing_pkgs[*]:-}
请先执行（Ubuntu/Debian）：
  sudo apt update
  sudo apt install -y ${missing_pkgs[*]:-}"
fi

info "系统依赖检查通过（gi / WebKit2 / 托盘）"

# ---------- 3. 打包工具 ----------
"$PY" -c "import PyInstaller" >/dev/null 2>&1 || {
  warn "未安装 PyInstaller，尝试安装…"
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install "pyinstaller>=6.0"
}
"$PY" -c "import webview" >/dev/null 2>&1 || fail "未安装 pywebview，请执行：$PY -m pip install -r requirements.txt"

if [[ "$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" != *"/usr/lib/"* ]]; then
  "$PY" -c "import gi" >/dev/null 2>&1 || \
    warn "当前是虚拟环境且找不到 gi。请用系统 Python 重跑，或建 venv 时加 --system-site-packages"
fi

if (( CHECK_ONLY )); then
  info "仅检查模式，环境就绪。"
  exit 0
fi

# ---------- 4. 清理 ----------
if (( CLEAN )); then
  info "清理 build/ 与 $DIST_DIR/"
  rm -rf build "$DIST_DIR"
fi

# ---------- 5. 打包 ----------
info "开始打包（onefile，首次较慢）…"
(( CONSOLE )) && { info "已启用控制台窗口（STICKY_CONSOLE=1）"; export STICKY_CONSOLE=1; }
command -v upx >/dev/null 2>&1 || warn "未安装 upx，跳过压缩（不影响功能；sudo apt install upx-ucl 可启用）"

"$PY" -m PyInstaller --noconfirm --clean \
  --distpath "$DIST_DIR" \
  --workpath build \
  "$SPEC"

BIN="$DIST_DIR/$APP_NAME"
[[ -f "$BIN" ]] || fail "打包失败：未生成 $BIN"
chmod +x "$BIN"

info "产物：$BIN（$(du -h "$BIN" | cut -f1)）"

# ---------- 6. 冒烟检查 ----------
info "检查可执行文件依赖（只列缺失项）…"
if command -v ldd >/dev/null 2>&1; then
  # onefile 是自解压包，ldd 看不出内部依赖，只能确认外壳可执行
  if ldd "$BIN" 2>/dev/null | grep -q "not found"; then
    warn "外壳有缺失依赖："
    ldd "$BIN" 2>/dev/null | grep "not found" || true
  else
    info "外壳依赖正常"
  fi
fi

cat <<EOF

完成。
  可执行文件：$(pwd)/$BIN
  运行：./$BIN
  日志：~/.config/sticky_notes/sticky-notes.log

注意：目标机器同样需要这些系统库，否则启动会失败：
  sudo apt install -y python3-gi gir1.2-webkit2-4.1 libwebkit2gtk-4.1-0 libayatana-appindicator3-1
（22.04 把 4.1/4.0 换成 gir1.2-webkit2-4.0 libwebkit2gtk-4.0-37）
EOF
