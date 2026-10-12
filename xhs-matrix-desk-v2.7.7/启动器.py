"""Apply optional runtime locations without shell sourcing; then run the app."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '.matrix-launch.json'
ALLOWED = {'MATRIX_BB_BROWSER_DIR', 'MATRIX_NODE_BIN', 'MATRIX_CRAWL4AI_PYTHON',
           'MATRIX_ADB_PATH', 'MATRIX_SCRCPY_PATH'}
if CONFIG.is_file():
    values = json.loads(CONFIG.read_text(encoding='utf-8'))
    for key, value in values.items():
        if key in ALLOWED and isinstance(value, str):
            os.environ.setdefault(key, value)
os.chdir(ROOT)
os.execv(sys.executable, [sys.executable, str(ROOT / 'run.py'), *sys.argv[1:]])
