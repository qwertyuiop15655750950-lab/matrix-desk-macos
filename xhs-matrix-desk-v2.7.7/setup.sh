#!/bin/bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec /bin/bash "$REPO_ROOT/scripts/install_macos.sh" --desktop "$@"
