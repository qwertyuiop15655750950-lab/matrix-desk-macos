#!/bin/bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec /bin/bash "$REPO_ROOT/scripts/launch_macos.sh" phone "$@"
