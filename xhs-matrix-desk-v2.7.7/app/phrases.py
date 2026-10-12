"""导流话术库。

存放合规口径的回复话术（站内表达，不含任何站外导流字样）。
纯逻辑模块，只依赖标准库；通过传入的 sqlite3 conn 读写。
"""

import sqlite3

# (分类, 标题, 内容) —— 全部为站内合规表达
DEFAULT_PHRASES: list[tuple[str, str, str]] = [
    (
        "私信回复",
        "营业时间咨询",
        "您好呀～营业时间写在主页简介里啦，点进主页就能看到，欢迎来玩！",
    ),
    (
        "私信回复",
        "价格咨询",
        "谢谢喜欢！团购价和套餐都放在橱窗里了，直接点主页橱窗查看，价格公开透明～",
    ),
    (
        "私信回复",
        "地址路线",
        "地址在主页简介第一行，导航直接搜店名就能到，地铁出来步行 5 分钟！",
    ),
    (
        "私信回复",
        "合作咨询",
        "感谢关注！商务合作请直接私信说明来意，我们看到后会统一回复～",
    ),
    (
        "私信回复",
        "差评安抚",
        "非常抱歉给您带来不好的体验！方便的话请私信告诉我具体情况，我们一定改进，欢迎再来监督～",
    ),
    (
        "评论区引导",
        "引导看主页",
        "主页简介里有地址和营业时间，姐妹们自取～",
    ),
    (
        "评论区引导",
        "引导进橱窗",
        "同款团购链接放在橱窗了，需要的姐妹点我主页进橱窗看看",
    ),
    (
        "评论区引导",
        "回复好评",
        "谢谢姐妹的喜欢！我们会继续保持，欢迎常来玩呀～",
    ),
    (
        "评论区引导",
        "置顶点评回复",
        "统一回复：地址、营业时间都在主页简介，团购在橱窗，大家自取哈～",
    ),
    (
        "主页简介",
        "简介模板-餐饮",
        "📍地址：XX路XX号｜🕘营业：10:00-22:00｜团购请点主页橱窗｜合作请私信",
    ),
    (
        "主页简介",
        "简介模板-服务",
        "本地生活服务号｜门店地址/营业时间见简介｜优惠活动都在橱窗，欢迎自取",
    ),
    (
        "主页简介",
        "简介模板-探店号",
        "本地探店｜真实测评｜合作请私信",
    ),
]

_CREATE_PHRASES_TABLE = """
CREATE TABLE IF NOT EXISTS phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

_ALLOWED_UPDATE_FIELDS = {"category", "title", "content"}


def _row_to_dict(row: sqlite3.Row | tuple) -> dict:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return {"id": row[0], "category": row[1], "title": row[2], "content": row[3],
            "created_at": row[4]}


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE_PHRASES_TABLE)
    conn.commit()


def seed_default_phrases(conn: sqlite3.Connection) -> int:
    """phrases 表为空时插入全部默认话术，返回插入条数；非空返回 0。"""
    _ensure_table(conn)
    cur = conn.execute("SELECT COUNT(*) FROM phrases")
    if cur.fetchone()[0] > 0:
        return 0
    conn.executemany(
        "INSERT INTO phrases (category, title, content) VALUES (?, ?, ?)",
        DEFAULT_PHRASES,
    )
    conn.commit()
    return len(DEFAULT_PHRASES)


def create_phrase(conn: sqlite3.Connection, category: str, title: str, content: str) -> int:
    """新增话术，返回新 id。title/content 为空抛 ValueError。"""
    if not (title or "").strip():
        raise ValueError("话术标题不能为空")
    if not (content or "").strip():
        raise ValueError("话术内容不能为空")
    _ensure_table(conn)
    cur = conn.execute(
        "INSERT INTO phrases (category, title, content) VALUES (?, ?, ?)",
        ((category or "").strip(), title.strip(), content.strip()),
    )
    conn.commit()
    return cur.lastrowid


def list_phrases(conn: sqlite3.Connection, category: str | None = None) -> list[dict]:
    """列出话术，可按分类过滤。"""
    _ensure_table(conn)
    conn.row_factory = sqlite3.Row
    try:
        if category:
            cur = conn.execute(
                "SELECT * FROM phrases WHERE category = ? ORDER BY id", (category,)
            )
        else:
            cur = conn.execute("SELECT * FROM phrases ORDER BY id")
        return [_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.row_factory = None


def get_phrase(conn: sqlite3.Connection, pid: int) -> dict | None:
    """按 id 取话术，不存在返回 None。"""
    _ensure_table(conn)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM phrases WHERE id = ?", (pid,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.row_factory = None


def update_phrase(conn: sqlite3.Connection, pid: int, data: dict) -> bool:
    """更新话术字段（category/title/content），成功返回 True。"""
    _ensure_table(conn)
    fields = {k: v for k, v in (data or {}).items() if k in _ALLOWED_UPDATE_FIELDS}
    if not fields:
        return False
    if "title" in fields and not (fields["title"] or "").strip():
        raise ValueError("话术标题不能为空")
    if "content" in fields and not (fields["content"] or "").strip():
        raise ValueError("话术内容不能为空")
    sets = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE phrases SET {sets} WHERE id = ?", (*fields.values(), pid)
    )
    conn.commit()
    return cur.rowcount > 0


def delete_phrase(conn: sqlite3.Connection, pid: int) -> bool:
    """删除话术，成功返回 True。"""
    _ensure_table(conn)
    cur = conn.execute("DELETE FROM phrases WHERE id = ?", (pid,))
    conn.commit()
    return cur.rowcount > 0
