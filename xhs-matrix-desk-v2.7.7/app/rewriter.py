"""多账号差异化改写引擎。

把同一份内容改写成多个版本，分发到不同账号时降低文本重复度。
纯逻辑模块，只依赖标准库；改写是确定性的（random.Random(seed)）。
"""

import random
import re

# 同义词库：key -> 可替换的同义表达（本地生活 / 营销 / 口播场景）
SYNONYMS: dict[str, list[str]] = {
    "探店": ["探店打卡", "实地探访", "实探"],
    "优惠": ["福利", "钜惠", "让利"],
    "推荐": ["安利", "种草", "分享"],
    "好吃": ["美味", "可口", "超赞"],
    "好喝": ["醇香", "顺滑", "回甘"],
    "便宜": ["实惠", "划算", "高性价比"],
    "老板": ["店主", "主理人"],
    "新开": ["新开业", "新店首发"],
    "打卡": ["拔草", "签到"],
    "环境": ["氛围", "装潢", "格调"],
    "服务": ["接待", "体验感"],
    "味道": ["口感", "风味"],
    "分量": ["份量", "量大管饱"],
    "排队": ["等位", "人气爆棚"],
    "火爆": ["爆满", "人气旺"],
    "必吃": ["必点", "招牌必试"],
    "隐藏": ["私藏", "小众"],
    "惊喜": ["彩蛋", "意外之喜"],
    "测评": ["实测", "亲测"],
    "真实": ["实在", "靠谱"],
    "干货": ["硬货", "实用帖"],
    "收藏": ["码住", "mark一下"],
    "分享": ["安利", "放送"],
    "教程": ["攻略", "保姆级教程"],
    "新手": ["小白", "入门选手"],
    "避雷": ["避坑", "排雷"],
    "踩雷": ["翻车", "踩坑"],
    "闭眼": ["放心", "无脑"],
    "冲": ["入", "入手"],
    "宝藏": ["宝藏店", "神仙店"],
    "出片": ["拍照好看", "好出片"],
    "氛围感": ["松弛感", "仪式感"],
    "性价比": ["质价比", "闭眼入不亏"],
    "回头客": ["老客", "常客"],
    "团建": ["聚会", "聚餐"],
    "约会": ["小聚", "碰头"],
    "午餐": ["午市", "工作餐"],
    "晚餐": ["夜宵", "晚市"],
    "周末": ["周末档", "假期"],
    "姐妹": ["姐妹们", "集美们"],
    "家人们": ["宝宝们", "友友们"],
    "赶紧": ["速速", "快快"],
    "限时": ["限量", "手慢无"],
    "开业": ["试营业", "盛大开业"],
    "福利": ["宠粉福利", "粉丝专享"],
    "人均": ["客单价", "人均消费"],
}

# 标题前后缀池（"" 表示不用前后缀）
TITLE_PREFIXES: list[str] = ["", "🔥", "✨", "📌"]
TITLE_SUFFIXES: list[str] = [
    "",
    "｜本地探店",
    "｜真实测评",
    "｜建议收藏",
    "｜干货分享",
    "｜避雷指南",
    "｜探店实测",
]

_SENT_SPLIT_RE = re.compile(r"([。！？\n]+)")
_TOPIC_SEP_RE = re.compile(r"[，,、;；|｜]")


def _replace_synonyms(text: str, rng: random.Random, prob: float = 0.6) -> str:
    """按概率把文本中的同义词 key 替换为随机同义表达。key 按长度降序处理。"""
    for key in sorted(SYNONYMS.keys(), key=len, reverse=True):
        if key in text and rng.random() < prob:
            text = text.replace(key, rng.choice(SYNONYMS[key]))
    return text


def _rewrite_title(title: str, rng: random.Random) -> str:
    if not title:
        return title
    title = _replace_synonyms(title, rng)
    prefix = rng.choice(TITLE_PREFIXES)
    suffix = rng.choice(TITLE_SUFFIXES)
    return f"{prefix}{title}{suffix}"


def _split_sentences(body: str) -> list[str]:
    """按 。！？\n 切句，保留分隔符。"""
    parts = _SENT_SPLIT_RE.split(body)
    sentences: list[str] = []
    buf = ""
    for part in parts:
        if _SENT_SPLIT_RE.fullmatch(part or ""):
            buf += part
            sentences.append(buf)
            buf = ""
        else:
            buf += part
    if buf:
        sentences.append(buf)
    return [s for s in sentences if s.strip()]


def _rewrite_body(body: str, rng: random.Random) -> str:
    if not body:
        return body
    sentences = _split_sentences(body)
    sentences = [_replace_synonyms(s, rng) for s in sentences]
    if len(sentences) > 1:
        # 首句不动，其余句按 seed 轮转
        first, rest = sentences[0], sentences[1:]
        k = rng.randrange(len(rest))
        rest = rest[k:] + rest[:k]
        sentences = [first] + rest
    return "".join(sentences)


def _split_topics(topics: str) -> list[str]:
    t = topics.replace("#", " ").replace("＃", " ")
    t = _TOPIC_SEP_RE.sub(" ", t)
    parts = [p.strip() for p in t.split() if p.strip()]
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return seen


def _rewrite_topics(topics: str, rng: random.Random) -> str:
    if not topics:
        return topics
    tags = _split_topics(topics)
    if not tags:
        return ""
    rng.shuffle(tags)
    keep = rng.randint(1, len(tags))  # 至少保留 1 个
    selected = tags[:keep]
    use_hash = "#" in topics or "＃" in topics
    if use_hash:
        return " ".join(t if t.startswith("#") else "#" + t for t in selected)
    return " ".join(selected)


def rewrite_variation(title: str, body: str, topics: str, seed: int) -> dict:
    """生成一个差异化版本。相同 seed + 相同输入 => 完全相同输出。"""
    title = title or ""
    body = body or ""
    topics = topics or ""
    rng = random.Random(seed)
    return {
        "title": _rewrite_title(title, rng),
        "body": _rewrite_body(body, rng),
        "topics": _rewrite_topics(topics, rng),
    }


def batch_variations(title: str, body: str, topics: str, n: int) -> list[dict]:
    """seed=0..n-1 生成 n 个版本。"""
    if n <= 0:
        return []
    return [rewrite_variation(title, body, topics, seed) for seed in range(n)]
