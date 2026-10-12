# -*- coding: utf-8 -*-
"""客户联系方式提取：从私信全文中识别微信号 / 手机号 / QQ。

只读分析，不做任何发送动作。
微信号/QQ 要求关键词上下文（降低误伤），手机号直接正则。
"""
import re

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
WECHAT_KWS = ["微信号", "微信", "vx", "VX", "v信", "威信", "加我", "微❤", "微心"]
WECHAT_ID_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.\-]{5,19}")
QQ_KWS = ["QQ", "qq", "QQ号", "扣扣", "企鹅"]
QQ_ID_RE = re.compile(r"(?<![\d])([1-9]\d{4,10})(?![\d])")
_STRIP_TAIL = "._-"

STATUS_CN = {"new": "待添加", "added": "已添加", "ignored": "已忽略"}


def _after_keyword(text, kws, id_re, window=40):
    """在关键词后 window 字符内匹配 id 格式。"""
    found = []
    for kw in kws:
        start = 0
        while True:
            i = text.find(kw, start)
            if i < 0:
                break
            seg = text[i + len(kw):i + len(kw) + window].lstrip(" :：-_—～~")
            m = id_re.match(seg)
            if m:
                cand = m.group(0).rstrip(_STRIP_TAIL)
                if cand and cand not in found:
                    found.append(cand)
            start = i + len(kw)
    return found


def extract_contacts(text):
    """返回 {"phones": [...], "wechats": [...], "qqs": [...]}。"""
    text = text or ""
    phones = sorted(set(PHONE_RE.findall(text)))
    wechats = _after_keyword(text, WECHAT_KWS, WECHAT_ID_RE)
    qqs = [q for q in _after_keyword(text, QQ_KWS, QQ_ID_RE)
           if not any(q in p for p in phones)]
    # 微信号去掉纯数字误伤（正则已要求首字母，此处防御）
    wechats = [w for w in wechats if not w.isdigit()]
    return {"phones": phones, "wechats": wechats, "qqs": qqs}


def snippet_for(text, found, width=60):
    """取第一条含联系方式的消息片段。"""
    needles = found["phones"] + found["wechats"] + found["qqs"]
    for line in (text or "").splitlines():
        if any(n and n in line for n in needles):
            line = line.strip()
            return line[:width] + ("…" if len(line) > width else "")
    return (text or "").strip().splitlines()[0][:width] \
        if (text or "").strip() else ""
