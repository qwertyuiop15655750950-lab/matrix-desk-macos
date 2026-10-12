# -*- coding: utf-8 -*-
"""关键词评论采集：扫指定视频/笔记的评论区，命中关键词的评论者归档。

流程：
1. 打开目标视频/笔记 URL
2. 滚动加载评论
3. 评论命中关键词 → 提取评论者昵称/ID/评论内容
4. 写入 collected_users 表（去重）
5. 评论里的联系方式（微信/手机/QQ）也一并提取

只读，不点赞、不回复、不关注。
"""
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse, unquote

# 联系方式正则（复用 contacts 模块的）
WECHAT_RE = re.compile(r"(?:微信|wx|wechat)[:：\s]*([a-zA-Z0-9_-]{6,20})",
                       re.I)
PHONE_RE = re.compile(r"1[3-9]\d{9}")
QQ_RE = re.compile(r"(?:QQ|qq)[:：\s]*([1-9]\d{4,10})")


def _extract_contacts(text):
    """从文本提联系方式。返回 dict。"""
    out = {"wechat": "", "phone": "", "qq": ""}
    m = WECHAT_RE.search(text or "")
    if m:
        out["wechat"] = m.group(1)
    m = PHONE_RE.search(text or "")
    if m:
        out["phone"] = m.group(0)
    m = QQ_RE.search(text or "")
    if m:
        out["qq"] = m.group(1)
    return out


def _profile_user_id(href, source_url, platform):
    """Read a profile identifier only from a same-platform profile link."""
    if not href:
        return ''
    parsed = urlparse(urljoin(source_url, href))
    hosts = ({'douyin.com', 'www.douyin.com'} if platform == 'douyin'
             else {'xiaohongshu.com', 'www.xiaohongshu.com',
                   'rednote.com', 'www.rednote.com'})
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in hosts:
        return ''
    match = re.fullmatch(r'/user/(?:profile/)?([^/]+)/?', parsed.path)
    if not match:
        return ''
    identifier = unquote(match.group(1)).strip()
    if not identifier or any(c.isspace() or c in '/?#' for c in identifier):
        return ''
    return identifier


def scan_video_comments(page, url, keywords, platform="xiaohongshu",
                        max_comments=100, scroll_times=5):
    """Read visible comments from the chosen account's browser.

    Capture before and after scrolling, because virtualized comment lists can
    discard earlier rows. A missing author identifier never becomes a nickname.
    Selectors still require calibration against the actual platform page.
    """
    if platform == 'douyin':
        from . import douyin as dy
        sel = dy.SELECTORS['comment']
    else:
        from .config import load_selectors
        sel = load_selectors()['comment_page']
    words = [str(word).strip() for word in keywords if str(word).strip()]
    if not words:
        return {'ok': False, 'error': '请先设置至少一个关键词', 'items': []}
    try:
        # Keep a manually opened comment panel when already on this video.
        if page.url != url:
            page.goto(url, timeout=30000, wait_until='domcontentloaded')
            page.wait_for_timeout(3000)
        page.wait_for_selector(sel['item'], state='visible', timeout=10000)
    except Exception:
        return {'ok': False,
                'error': '未能读取评论，请在浏览器完成登录并打开目标视频评论区后重试；页面选择器可能需要校准',
                'items': []}

    results = []
    seen = set()
    readable = 0
    maximum = min(max(int(max_comments), 1), 1000)
    rounds = min(max(int(scroll_times), 0), 20)
    for current_round in range(rounds + 1):
        try:
            items = page.locator(sel['item']).all()
        except Exception:
            return {'ok': False, 'error': '评论区已关闭或页面发生变化，请重新打开后采集', 'items': []}
        for item in items:
            try:
                text = (item.locator(sel['text']).first.inner_text(timeout=2000) or '').strip()
                author = (item.locator(sel['author']).first.inner_text(timeout=2000) or '').strip()
            except Exception:
                continue
            if not text:
                continue
            user_id = ''
            for selector in (sel['author'], "a[href*='/user/']"):
                try:
                    href = item.locator(selector).first.get_attribute('href', timeout=2000)
                    user_id = _profile_user_id(href, url, platform)
                except Exception:
                    continue
                if user_id:
                    break
            key = (user_id, author, text)
            if key in seen:
                continue
            seen.add(key)
            readable += 1
            hit = next((word for word in words if word.casefold() in text.casefold()), None)
            if hit:
                results.append({'nickname': author, 'user_id': user_id,
                                'text': text, 'keyword': hit,
                                'contacts': _extract_contacts(text)})
            if readable >= maximum:
                break
        if readable >= maximum or current_round == rounds:
            break
        try:
            if platform == 'douyin':
                # Put the wheel over the comment panel instead of the video feed.
                panel = page.locator(sel['list']).first
                if not panel.is_visible(timeout=1000):
                    break
                panel.hover(timeout=2000)
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(2000)
        except Exception:
            break
    if not readable:
        return {'ok': False,
                'error': '找到了评论容器，但未能读取评论内容；请检查评论区或校准页面选择器',
                'items': []}
    return {'ok': True, 'items': results, 'read': readable}


def archive_users(conn, platform, keyword, source_url, items):
    """归档采集到的用户（去重）。返回新增数。"""
    now = datetime.now().isoformat(timespec="seconds")
    added = 0
    for it in items:
        if not it.get("user_id"):
            continue
        try:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO collected_users
                (platform, user_id, nickname, keyword, source_url,
                 comment_text, contact_wechat, contact_phone, contact_qq,
                 created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (platform, it["user_id"], it["nickname"], keyword,
                  source_url, it["text"],
                  it["contacts"]["wechat"], it["contacts"]["phone"],
                  it["contacts"]["qq"], now))
            if cursor.rowcount > 0:
                added += 1
        except Exception:
            pass
    conn.commit()
    return added
