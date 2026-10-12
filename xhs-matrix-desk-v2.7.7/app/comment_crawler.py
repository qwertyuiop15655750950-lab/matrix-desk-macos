"""Read bounded loaded comments using an account-owned bb channel + Crawl4AI.

bb-browser scrolls the visible comment region and captures public DOM only.
Crawl4AI parses the captured HTML offline, without another browser or network.
"""
import hashlib
import re
import time
from urllib.parse import parse_qsl, urlsplit

from .comment_selectors import comment_source_url

MAX_COMMENTS = 100
MAX_SCROLLS = 5
MAX_HTML = 500000



def _same_content(left, right, platform):
    """Compare a platform URL's public content path/IDs, ignoring tracking state."""
    def signature(value):
        parts = urlsplit(comment_source_url(value, platform))
        host = platform
        path = parts.path.rstrip('/') or '/'
        content = re.fullmatch(r'/(?:video|explore|discovery/item)/([A-Za-z0-9_-]+)', path)
        if content:
            return host, 'content:' + content[1], ()
        return host, path, tuple(sorted((key.lower(), item) for key, item in parse_qsl(parts.query)))
    try: return signature(left) == signature(right)
    except ValueError: return False


def _contacts(text):
    def match(pattern, group=1):
        found = re.search(pattern, text, re.I)
        return found.group(group) if found else ''
    return {'wechat': match(r'(?:微信|wx|wechat)[:：\s]*([a-zA-Z0-9_-]{6,20})'),
            'phone': match(r'1[3-9]\d{9}', 0),
            'qq': match(r'(?:QQ)[:：\s]*([1-9]\d{4,10})')}


def scan_comments(adapter, url, words, platform='douyin', max_comments=100,
                  scroll_times=5, interval_ms=4000, timeout_seconds=120):
    """Return keyword matches and retain collected partial rows on stop/timeout.

    The first 80 seconds are reserved for visible browsing at most; one bounded
    offline extraction follows. This stays within the account worker's timeout.
    A webpage change ends collection and never captures the new page's comments.
    """
    source = comment_source_url(url, platform)
    if (not isinstance(words, (list, tuple)) or not words
            or any(not isinstance(word, str) for word in words)):
        raise ValueError('请先设置文字关键词列表')
    keywords = list(dict.fromkeys(word.strip() for word in words if word.strip()))
    if not keywords or len(keywords) > 200 or any(len(word) > 200 for word in keywords):
        raise ValueError('关键词需要为 1–200 个短文字')
    max_comments = min(MAX_COMMENTS, max(1, int(max_comments)))
    scroll_times = min(MAX_SCROLLS, max(0, int(scroll_times)))
    interval_ms = min(10000, max(2000, int(interval_ms)))
    timeout_seconds = min(120.0, max(0.1, float(timeout_seconds)))
    overall_deadline = time.monotonic() + timeout_seconds
    browsing_deadline = time.monotonic() + max(0.1, timeout_seconds - 40)
    # Keep an opened comment drawer intact when already on the requested page.
    if not _same_content(adapter.url, source, platform):
        adapter.goto(url, wait_until='domcontentloaded', timeout=30000)
    collection_url = comment_source_url(adapter.url, platform)
    if not _same_content(collection_url, source, platform):
        raise ValueError('打开后的页面不是指定内容，已取消采集；请完成登录并重新打开目标评论页')
    source = collection_url
    adapter.start_comment_job()
    fragments, seen, total_html = [], set(), 0
    stopped = timed_out = truncated = False
    reason = warning = ''
    title = ''

    def control():
        nonlocal stopped, timed_out, reason
        if time.monotonic() >= browsing_deadline:
            stopped = timed_out = True; reason = 'timeout'; return {'stopped': True}
        state = adapter.comment_state()
        if state.get('stopped'):
            stopped = True; reason = str(state.get('reason') or 'user')
        return state

    def wait_active(milliseconds=0):
        elapsed = 0.0
        while True:
            state = control()
            if stopped: return False
            if not state.get('paused') and elapsed >= milliseconds: return True
            step = min(250, max(1, milliseconds - elapsed)) if not state.get('paused') else 250
            adapter.wait_for_timeout(step)
            if not state.get('paused'): elapsed += step

    def collect():
        nonlocal title, total_html, truncated
        snapshot = adapter.comment_snapshot()
        if snapshot.get('stopped'): return
        if comment_source_url(snapshot.get('url'), platform) != collection_url:
            raise ValueError('评论页面已切换')
        title = str(snapshot.get('title') or '')[:500]
        truncated = truncated or bool(snapshot.get('truncated'))
        identities = snapshot.get('identities', [])
        for index, fragment in enumerate(snapshot.get('fragments', [])):
            identity = identities[index] if index < len(identities) else ''
            key = identity or hashlib.sha256(fragment.encode()).digest()
            if key in seen: continue
            if total_html + len(fragment) > MAX_HTML:
                truncated = True; break
            seen.add(key); fragments.append(fragment); total_html += len(fragment)
            if len(fragments) > max_comments: truncated = True
        adapter.comment_progress(len(fragments), f'已截取 {len(fragments)} 个评论区元素，滚动后继续读取。')

    result = None
    try:
        # Give the user time to open the drawer after navigating to a video.
        initial_deadline = min(browsing_deadline, time.monotonic() + 10)
        while wait_active() and not fragments:
            collect()
            if fragments or time.monotonic() >= initial_deadline: break
            if not wait_active(500): break
        for _ in range(scroll_times):
            if not fragments or not wait_active(): break
            if not adapter.scroll_comments(): break
            if not wait_active(interval_ms): break
            collect()
    except Exception as error:
        # A closed/navigated page can invalidate the bb request after earlier
        # snapshots. Keep those snapshots for the same offline extraction.
        if not fragments:
            result = {'ok': False, 'items': [], 'read': 0, 'error': str(error)}
        stopped = True; reason = reason or 'page_unavailable'
        warning = '页面操作已结束，保留先前已截取的评论。'
    try:
        if not fragments:
            if result is not None:
                pass
            elif stopped:
                result = {'ok': True, 'items': [], 'read': 0}
            else:
                result = {'ok': False, 'items': [], 'read': 0,
                          'error': '未读取到评论，请完成登录并打开评论区；页面选择器可能需要校准'}
        else:
            from .crawl_bridge import extract_comments
            adapter.comment_progress(len(fragments), 'Crawl4AI 正在解析已加载的评论。')
            snapshot = {'url': source, 'title': title, 'html': '<html><body>' + ''.join(fragments) + '</body></html>'}
            remaining = max(1.0, min(40.0, overall_deadline - time.monotonic()))
            extracted = extract_comments(snapshot, platform, timeout=remaining, max_comments=max_comments)
            rows, row_seen, items = [], set(), []
            for row in extracted.get('rows', []):
                text = str(row.get('content') or '').strip()[:10000]
                if not text: continue
                user_id = str(row.get('user_id') or '').strip()[:300]
                nickname = str(row.get('nickname') or '').strip()[:500]
                comment_id = str(row.get('comment_id') or '').strip()[:300]
                key = ('id', comment_id) if comment_id else ('content', user_id, nickname, text)
                if key in row_seen: continue
                row_seen.add(key); rows.append(row)
                keyword = next((word for word in keywords if word.casefold() in text.casefold()), '')
                if keyword:
                    items.append({'comment_id': comment_id, 'user_id': user_id, 'nickname': nickname,
                                  'text': text, 'keyword': keyword, 'contacts': _contacts(text),
                                  'created_at': str(row.get('created_at') or '')[:200]})
                if len(rows) >= max_comments: break
            result = {'ok': bool(rows), 'items': items, 'read': len(rows),
                      'extraction_engine': extracted.get('engine', 'crawl4ai'),
                      'extraction_version': extracted.get('version', '')}
            if not rows:
                result['error'] = '评论区元素存在，但未解析出评论正文；页面选择器需要校准'
            # A stop clicked while the subprocess was parsing still preserves
            # its parsed rows; it does not imply all available comments loaded.
            try:
                state = adapter.comment_state()
                if state.get('stopped'):
                    stopped = True; reason = str(state.get('reason') or 'user')
            except Exception: pass
        result.update(engine='crawl4ai', browser_engine='bb-browser', sourceUrl=source,
                      stopped=stopped, timed_out=timed_out, stop_reason=reason,
                      truncated=truncated, warning=warning)
        return result
    finally:
        try:
            if result and result.get('ok'):
                suffix = '已停止并保留结果' if stopped else '采集完成'
                text = f"{suffix}：解析 {result['read']} 条，命中 {len(result['items'])} 条。"
            else:
                text = (result or {}).get('error', '爬虫解析未完成，请检查工具或页面后重试。')
            adapter.comment_progress((result or {}).get('read', len(fragments)), text, finished=True)
        except Exception: pass
