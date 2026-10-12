"""敏感词自查。

发布前扫描标题 / 正文 / 话题，提示导流风险词与广告法风险词。
纯逻辑模块，只依赖标准库；自定义词通过传入的 sqlite3 conn 读写。
"""

import sqlite3

CATEGORIES: dict[str, str] = {
    "drainage": "导流风险",
    "ad_law": "广告法风险",
    "custom": "自定义",
}

# (词, 类别)
DEFAULT_WORDS: list[tuple[str, str]] = [
    # ---- 导流风险 ----
    ("微信", "drainage"),
    ("vx", "drainage"),
    ("v信", "drainage"),
    ("威信", "drainage"),
    ("加微", "drainage"),
    ("加v", "drainage"),
    ("私聊", "drainage"),
    ("私信我", "drainage"),
    ("私我", "drainage"),
    ("留联系方式", "drainage"),
    ("联系方式", "drainage"),
    ("+v", "drainage"),
    ("微商", "drainage"),
    ("导流", "drainage"),
    ("跳转", "drainage"),
    ("站外", "drainage"),
    ("二维码", "drainage"),
    ("扫码", "drainage"),
    ("企微", "drainage"),
    ("企业微信", "drainage"),
    ("qq", "drainage"),
    ("电话", "drainage"),
    ("手机号", "drainage"),
    ("引流", "drainage"),
    ("小号", "drainage"),
    ("备用号", "drainage"),
    ("滴滴我", "drainage"),
    ("戳我", "drainage"),
    # ---- 广告法风险 ----
    ("最", "ad_law"),
    ("第一", "ad_law"),
    ("国家级", "ad_law"),
    ("顶级", "ad_law"),
    ("首选", "ad_law"),
    ("100%", "ad_law"),
    ("根治", "ad_law"),
    ("治愈", "ad_law"),
    ("致富", "ad_law"),
    ("最高", "ad_law"),
    ("最佳", "ad_law"),
    ("首家", "ad_law"),
    ("独家", "ad_law"),
    ("最低价", "ad_law"),
    ("全网最低", "ad_law"),
    ("零风险", "ad_law"),
    ("包治", "ad_law"),
    ("根除", "ad_law"),
    ("永久", "ad_law"),
    ("绝对", "ad_law"),
    ("保证", "ad_law"),
    ("承诺", "ad_law"),
    ("暴富", "ad_law"),
    ("躺赚", "ad_law"),
    ("一夜暴富", "ad_law"),
]

_CREATE_CUSTOM_TABLE = """
CREATE TABLE IF NOT EXISTS custom_words (
    word TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'custom'
)
"""


def _check_words(text: str, words: list[tuple[str, str]]) -> list[dict]:
    text_l = (text or "").lower()
    hits: list[dict] = []
    for word, category in words:
        w = (word or "").lower()
        if not w:
            continue
        count = text_l.count(w)
        if count > 0:
            hits.append({"word": word, "category": category, "count": count})
    return hits


def check(text: str, extra_words: list[str] | None = None) -> list[dict]:
    """扫描文本，返回 [{'word','category','count'}]。大小写不敏感，空文本返回 []。"""
    if not text:
        return []
    words = list(DEFAULT_WORDS)
    for w in extra_words or []:
        words.append((w, "custom"))
    return _check_words(text, words)


def get_custom_words(conn: sqlite3.Connection) -> list[str]:
    """读取自定义敏感词；表不存在时返回 []，不抛异常。"""
    try:
        cur = conn.execute("SELECT word FROM custom_words ORDER BY word")
        return [row[0] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []


def add_custom_word(conn: sqlite3.Connection, word: str, category: str = "custom") -> None:
    """新增自定义敏感词。空词抛 ValueError。"""
    if not (word or "").strip():
        raise ValueError("敏感词不能为空")
    conn.execute(_CREATE_CUSTOM_TABLE)
    conn.execute(
        "INSERT OR REPLACE INTO custom_words (word, category) VALUES (?, ?)",
        (word.strip(), category),
    )
    conn.commit()


def remove_custom_word(conn: sqlite3.Connection, word: str) -> None:
    """删除自定义敏感词。空词抛 ValueError。"""
    if not (word or "").strip():
        raise ValueError("敏感词不能为空")
    conn.execute(_CREATE_CUSTOM_TABLE)
    conn.execute("DELETE FROM custom_words WHERE word = ?", (word.strip(),))
    conn.commit()


def check_with_db(text: str, conn: sqlite3.Connection) -> list[dict]:
    """合并内置词库 + 数据库自定义词进行检测。"""
    if not text:
        return []
    words = list(DEFAULT_WORDS)
    for w in get_custom_words(conn):
        words.append((w, "custom"))
    return _check_words(text, words)
