# -*- coding: utf-8 -*-
"""SQLite 建表与连接管理。只用标准库。"""
import os
import sqlite3
from datetime import datetime

from .config import DB_PATH, ensure_dirs


def _now():
    """当前时间，格式 YYYY-MM-DD HH:MM:SS。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nickname TEXT NOT NULL,
    xhs_id TEXT DEFAULT '',
    group_name TEXT DEFAULT '默认分组',
    proxy_scheme TEXT DEFAULT 'http',
    proxy_host TEXT DEFAULT '',
    proxy_port INTEGER DEFAULT 0,
    proxy_user TEXT DEFAULT '',
    proxy_pass TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT DEFAULT '',
    body TEXT DEFAULT '',
    topics TEXT DEFAULT '',
    cover_text TEXT DEFAULT '',
    mode TEXT DEFAULT 'images',
    media_json TEXT DEFAULT '[]',
    template TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS publish_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER,
    draft_id INTEGER,
    title_used TEXT DEFAULT '',
    image_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'done',
    note_url TEXT DEFAULT '',
    screenshot TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT '',
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS custom_words (
    word TEXT PRIMARY KEY,
    category TEXT DEFAULT 'custom'
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    account_ids TEXT DEFAULT '[]',
    scheduled_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    note TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS monitor_targets (
    account_id INTEGER PRIMARY KEY,
    nickname TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS publish_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER NOT NULL,
    account_ids TEXT DEFAULT '[]',
    done_ids TEXT DEFAULT '[]',
    results TEXT DEFAULT '{}',
    jobs_json TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS lead_keywords (
    word TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    preview TEXT DEFAULT '',
    matched_words TEXT DEFAULT '[]',
    status TEXT DEFAULT 'new',
    detected_at TEXT,
    updated_at TEXT,
    UNIQUE(account_id, user_id)
);
CREATE TABLE IF NOT EXISTS customer_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    nickname TEXT DEFAULT '',
    wechat TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    qq TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    detected_at TEXT,
    updated_at TEXT,
    UNIQUE(account_id, user_id)
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def init_db(path=None):
    """初始化数据库（建表，幂等），返回 sqlite3 连接。

    path 为 None 时使用默认库路径（会自动建目录）；
    传入 ":memory:" 或任意文件路径均可。
    """
    if path is None:
        ensure_dirs()
        path = DB_PATH
    elif path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    # 注意：不要加 check_same_thread=False。所有连接都是线程本地的，
    # 跨线程误用应直接抛 ProgrammingError，而不是静默坏数据。
    # WAL + busy_timeout 让“多线程各持一连接”并发读写不报 database is locked。
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
    except Exception:
        pass
    conn.executescript(_SCHEMA)
    draft_cols = {r[1] for r in conn.execute('PRAGMA table_info(drafts)')}
    if 'cover_text' not in draft_cols:
        conn.execute("ALTER TABLE drafts ADD COLUMN cover_text TEXT DEFAULT ''")
    conn.commit()
    # 轻量迁移：给老库补字段（幂等）
    queue_cols = {r[1] for r in conn.execute('PRAGMA table_info(publish_queue)')}
    if 'jobs_json' not in queue_cols:
        conn.execute("ALTER TABLE publish_queue ADD COLUMN jobs_json TEXT DEFAULT '[]'")
    conn.commit()
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(publish_records)").fetchall()]
        if "screenshot" not in cols:
            conn.execute("ALTER TABLE publish_records "
                         "ADD COLUMN screenshot TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(accounts)").fetchall()]
        if "platform" not in cols:
            conn.execute("ALTER TABLE accounts "
                         "ADD COLUMN platform TEXT DEFAULT 'xiaohongshu'")
    except Exception:
        pass
    # 关键词评论采集：采集到的用户归档
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS collected_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT DEFAULT 'xiaohongshu',
            user_id TEXT DEFAULT '',
            nickname TEXT DEFAULT '',
            keyword TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            comment_text TEXT DEFAULT '',
            contact_wechat TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            contact_qq TEXT DEFAULT '',
            status TEXT DEFAULT 'new',
            created_at TEXT,
            UNIQUE(platform, user_id, keyword)
        )""")
    except Exception:
        pass
    # 关键词监控任务
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_monitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT DEFAULT 'xiaohongshu',
            keyword TEXT NOT NULL,
            target_url TEXT DEFAULT '',
            enabled INTEGER DEFAULT 1,
            last_run TEXT DEFAULT '',
            found_count INTEGER DEFAULT 0,
            created_at TEXT
        )""")
    except Exception:
        pass
    conn.commit()
    return conn


def get_conn(path=None):
    """获取数据库连接（自动建目录、建表）。"""
    return init_db(path)
