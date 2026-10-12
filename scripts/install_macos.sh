#!/bin/bash
# Install into this checkout; no account or login directories are replaced.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DESKTOP=1
INSTALL_PHONE=1
WITH_LIVE=0
CHECK_ONLY=0

usage() {
  cat <<'EOF'
用法：bash scripts/install_macos.sh [选项]
  --desktop    只安装桌面矩阵
  --phone      只安装手机工具
  --with-live  为手机工具安装可选的 PyAV 实时解码组件
  --check      只检查系统和 Python，不创建环境、不联网
  --help       显示说明
可用 MATRIX_PYTHON 指定 Python 3.12 的可执行文件。
EOF
}
for option in "$@"; do
  case "$option" in
    --desktop) INSTALL_DESKTOP=1; INSTALL_PHONE=0 ;;
    --phone) INSTALL_DESKTOP=0; INSTALL_PHONE=1 ;;
    --with-live) WITH_LIVE=1 ;;
    --check) CHECK_ONLY=1 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "未知选项：$option" >&2; usage >&2; exit 2 ;;
  esac
done
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "此安装脚本只支持 macOS。" >&2
  exit 2
fi
if [[ "$WITH_LIVE" == 1 && "$INSTALL_PHONE" == 0 ]]; then
  echo "--with-live 需要安装手机工具，请使用 --phone --with-live。" >&2
  exit 2
fi

is_python312() {
  [[ -x "$1" ]] && "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}
SELECTED_PYTHON=""
if [[ -n "${MATRIX_PYTHON:-}" ]]; then
  SELECTED_PYTHON="$MATRIX_PYTHON"
  if ! is_python312 "$SELECTED_PYTHON"; then
    echo "MATRIX_PYTHON 必须指向可用的 Python 3.12 可执行文件。" >&2
    exit 2
  fi
else
  for candidate in "$(command -v python3.12 || true)" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 "$(command -v python3 || true)"; do
    if [[ -n "$candidate" ]] && is_python312 "$candidate"; then
      SELECTED_PYTHON="$candidate"
      break
    fi
  done
  if [[ -z "$SELECTED_PYTHON" ]]; then
    echo "未找到 Python 3.12。请先安装 Python 3.12，再重试。" >&2
    echo "已有 Homebrew 可执行：brew install python@3.12" >&2
    exit 2
  fi
fi

preflight_target() {
  local target="$1"
  local requirements="$2"
  if [[ ! -f "$target/run.py" || ! -f "$target/$requirements" ]]; then
    echo "源码不完整：$target" >&2
    exit 2
  fi
  if [[ -e "$target/venv" ]] && ! is_python312 "$target/venv/bin/python"; then
    echo "已有 venv 不是可用的 Python 3.12 环境：$target/venv" >&2
    echo "请先将此 venv 目录改名备份，再重新安装；不要移动 data 目录。" >&2
    exit 2
  fi
}
DESKTOP_ROOT="$REPO_ROOT/xhs-matrix-desk-v2.7.7"
PHONE_ROOT="$REPO_ROOT/android-device-desk"
if [[ "$INSTALL_DESKTOP" == 1 ]]; then preflight_target "$DESKTOP_ROOT" requirements-mac-install.txt; fi
if [[ "$INSTALL_PHONE" == 1 ]]; then
  preflight_target "$PHONE_ROOT" requirements.txt
  if [[ "$WITH_LIVE" == 1 && ! -f "$PHONE_ROOT/requirements-live.txt" ]]; then
    echo "缺少手机实时解码依赖文件。" >&2; exit 2
  fi
fi
echo "预检通过。Python：$SELECTED_PYTHON"
if [[ "$CHECK_ONLY" == 1 ]]; then
  echo "未安装依赖，未下载浏览器，未启动应用。"
  exit 0
fi

install_environment() {
  local target="$1"
  local requirements="$2"
  if [[ ! -x "$target/venv/bin/python" ]]; then
    "$SELECTED_PYTHON" -m venv "$target/venv"
  fi
  "$target/venv/bin/python" -m pip install --upgrade pip
  "$target/venv/bin/python" -m pip install -r "$target/$requirements"
}
if [[ "$INSTALL_DESKTOP" == 1 ]]; then
  echo "安装桌面矩阵运行环境…"
  install_environment "$DESKTOP_ROOT" requirements-mac-install.txt
  "$DESKTOP_ROOT/venv/bin/python" -m playwright install chromium
fi
if [[ "$INSTALL_PHONE" == 1 ]]; then
  echo "安装手机工具运行环境…"
  install_environment "$PHONE_ROOT" requirements.txt
  if [[ "$WITH_LIVE" == 1 ]]; then
    "$PHONE_ROOT/venv/bin/python" -m pip install -r "$PHONE_ROOT/requirements-live.txt"
  fi
fi
echo "安装完成。双击根目录「启动.command」或「启动手机工具.command」。"
echo "手机工具的 ADB、scrcpy 需要另行安装，详见 docs/macos-install.md。"
