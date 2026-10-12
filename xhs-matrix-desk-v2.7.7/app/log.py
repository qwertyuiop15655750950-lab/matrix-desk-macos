# -*- coding: utf-8 -*-
"""结构化日志：data/logs/app.log，单文件 2MB、保留 10 份历史。

格式示例：
2026-10-07 21:30:12 task=publish aid=3 stage=open_creator status=start
2026-10-07 21:30:16 task=publish aid=3 stage=open_creator status=success cost_ms=4321
2026-10-07 21:30:16 task=sweep aid=7 stage=scan status=failed error=TimeoutError: .. cost_ms=30123 screenshot=.. | Traceback ...

线程、数据库、Actor 生命周期问题通常可从日志定位；
页面状态/登录验证/弹窗/平台结构变化还需配合错误截图。
"""
import os
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime

from .config import DATA_DIR, ensure_dirs

LOG_DIR = os.path.join(DATA_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "app.log")
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 10
MEM_CAP = 800  # 内存保留最近 800 行

_lock = threading.Lock()
_mem = []  # [(cursor, line)]


def _rotate():
    """RotatingFileHandler 风格：app.log -> .1 -> .2 ... -> .10。"""
    try:
        if not os.path.exists(LOG_PATH):
            return
        if os.path.getsize(LOG_PATH) <= MAX_BYTES:
            return
        for i in range(BACKUP_COUNT, 0, -1):
            src = LOG_PATH if i == 1 else f"{LOG_PATH}.{i - 1}"
            dst = f"{LOG_PATH}.{i}"
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                if os.path.exists(src):
                    os.rename(src, dst)
            except OSError:
                pass
    except OSError:
        pass


def log_event(task=None, aid=None, stage=None, status=None, error=None,
              msg="", cost_ms=None, screenshot=None, tb=None):
    """记一条结构化日志。所有参数可选，至少给一个。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [ts,
             f"task={task or '-'}",
             f"aid={aid or '-'}",
             f"stage={stage or '-'}",
             f"status={status or '-'}"]
    if cost_ms is not None:
        parts.append(f"cost_ms={int(cost_ms)}")
    if error is not None:
        parts.append(f"error={type(error).__name__}: {error}")
    if screenshot:
        parts.append(f"screenshot={screenshot}")
    line = " ".join(parts)
    extra = msg
    if tb:
        extra = (extra + " | " if extra else "") + "Traceback: " + \
            " <- ".join(tb.strip().splitlines()[-4:])
    if extra:
        line += f" | {extra}"
    with _lock:
        try:
            ensure_dirs()
            os.makedirs(LOG_DIR, exist_ok=True)
            _rotate()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        cursor = (_mem[-1][0] + 1) if _mem else 1
        _mem.append((cursor, line))
        del _mem[:-MEM_CAP]
    return line


@contextmanager
def task_span(task, aid=None, stage=None, extra=""):
    """任务生命周期：start -> success/failed(+cost_ms)，异常带 traceback。

    用法：
        with task_span("publish", aid, "fill"):
            ... 业务 ...
    extra 会透传到 start/success/failed 三行（如 shard=2）。
    """
    t0 = time.time()
    log_event(task=task, aid=aid, stage=stage, status="start", msg=extra)
    try:
        yield
    except Exception as e:
        cost = (time.time() - t0) * 1000
        status = "browser_dead" if _looks_dead(e) else "failed"
        log_event(task=task, aid=aid, stage=stage, status=status, error=e,
                  cost_ms=cost, msg=extra, tb=traceback.format_exc(limit=6))
        raise
    else:
        cost = (time.time() - t0) * 1000
        log_event(task=task, aid=aid, stage=stage, status="success",
                  cost_ms=cost, msg=extra)


def _looks_dead(exc):
    msg = f"{type(exc).__name__}: {exc}".lower()
    return ("has been closed" in msg or "target closed" in msg
            or "browser closed" in msg or "context closed" in msg)


def get_logs(cursor=0, limit=200):
    """取 cursor 之后的新日志（UI 轮询用）。"""
    with _lock:
        items = [{"cursor": c, "line": l} for c, l in _mem if c > cursor]
    return items[-limit:], (items[-1]["cursor"] if items else cursor)


def log_path():
    return LOG_PATH
