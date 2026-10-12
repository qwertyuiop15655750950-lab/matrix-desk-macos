#!/bin/bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-desktop}"
if [[ "$#" -gt 0 ]]; then shift; fi
if [[ "$(uname -s)" != Darwin ]]; then
  echo "此启动入口只支持 macOS。" >&2; exit 2
fi
case "$MODE" in
  desktop)
    APP_ROOT="$REPO_ROOT/xhs-matrix-desk-v2.7.7"
    APP_PYTHON="$APP_ROOT/venv/bin/python"
    ENTRYPOINT="$APP_ROOT/启动器.py"
    ;;
  phone)
    APP_ROOT="$REPO_ROOT/android-device-desk"
    APP_PYTHON="${MATRIX_DEVICE_PYTHON:-$APP_ROOT/venv/bin/python}"
    ENTRYPOINT="$APP_ROOT/run.py"
    ;;
  *) echo "未知启动目标：$MODE" >&2; exit 2 ;;
esac
if [[ ! -x "$APP_PYTHON" ]]; then
  echo "尚未安装运行环境，请先双击根目录「安装.command」。" >&2
  exit 2
fi
# Finder launches have a minimal PATH; include common Homebrew tool locations.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ -z "${MATRIX_ADB_PATH:-}" ]] && command -v adb >/dev/null 2>&1; then
  export MATRIX_ADB_PATH="$(command -v adb)"
fi
if [[ -z "${MATRIX_SCRCPY_PATH:-}" ]] && command -v scrcpy >/dev/null 2>&1; then
  export MATRIX_SCRCPY_PATH="$(command -v scrcpy)"
fi
cd "$APP_ROOT"
exec "$APP_PYTHON" "$ENTRYPOINT" "$@"
