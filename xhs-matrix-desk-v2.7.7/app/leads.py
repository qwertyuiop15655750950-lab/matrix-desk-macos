# -*- coding: utf-8 -*-
"""线索中心：只读意向识别。

只分析私信会话列表的预览文本，命中意向关键词即记为线索。
不做任何自动发送、自动回复，发送一律人工。
"""

DEFAULT_KEYWORDS = [
    "价格", "多少钱", "怎么卖", "报价",
    "加盟", "代理", "合作", "招商",
    "地址", "在哪", "位置", "怎么走",
    "怎么买", "购买", "下单", "链接",
    "咨询", "联系", "电话", "微信", "vx",
    "预约", "报名", "课程", "体验",
    "优惠", "折扣", "团购",
]

LEAD_STATUS_CN = {
    "new": "待跟进",
    "contacted": "已联系",
    "done": "已成交",
    "ignored": "已忽略",
}


def detect_intent(text, keywords):
    """返回 text 中命中的关键词列表（保持 keywords 顺序，去重）。"""
    if not text:
        return []
    hits = []
    for w in keywords or []:
        w = (w or "").strip()
        if w and w not in hits and w in text:
            hits.append(w)
    return hits


def ensure_default_keywords(conn):
    """关键词表为空时写入默认词，返回当前词数。"""
    cur = conn.execute("SELECT COUNT(*) c FROM lead_keywords")
    if cur.fetchone()["c"]:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO lead_keywords(word) VALUES(?)",
        [(w,) for w in DEFAULT_KEYWORDS])
    conn.commit()


def get_keywords(conn):
    cur = conn.execute("SELECT word FROM lead_keywords ORDER BY word")
    return [r["word"] for r in cur.fetchall()]


def save_keywords(conn, words):
    words = [w.strip() for w in (words or []) if w and w.strip()]
    conn.execute("DELETE FROM lead_keywords")
    conn.executemany(
        "INSERT INTO lead_keywords(word) VALUES(?)", [(w,) for w in words])
    conn.commit()
    return len(words)
