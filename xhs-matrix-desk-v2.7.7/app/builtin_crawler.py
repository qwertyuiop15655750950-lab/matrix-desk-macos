"""自有评论采集器：直接使用账号所属 Playwright 页面和本地 DOM 提取代码。
不导入 bb-browser 或 Crawl4AI，不导出登录状态，不调用平台私有接口。
"""
from pathlib import Path
from urllib.parse import urlsplit
from .browser_plugin_bridge import scan_with_plugin
from .comment_selectors import comment_source_url, PLATFORM_HOSTS
from .page_guard import check_page, PageBlocked

SCRIPT = Path(__file__).parent / 'browser_plugin/content.js'

def collect(page, url, words=(), *, collect_all=True, max_comments=1000):
    # Local page code is ours; an external CLI, daemon or second browser is unnecessary.
    try:
        hostname = urlsplit(url).hostname
        platform = next((name for name, hosts in PLATFORM_HOSTS.items() if hostname in hosts), None)
        comment_source_url(url, platform)
        from .comment_crawler import _same_content
        if not _same_content(page.url, url, platform): page.goto(url, wait_until='domcontentloaded', timeout=30000)
        check_page(page)
        page.evaluate(SCRIPT.read_text(encoding='utf-8'))
        if platform == 'douyin':
            for selector in ('[data-e2e="comment-icon"]', '[data-e2e="comment-button"]', 'button[aria-label*="评论"]'):
                button = page.locator(selector)
                if button.count() == 1 and button.is_visible():
                    button.click(timeout=3000)
                    break
    except PageBlocked as error:
        return {**error.result, 'engine':'builtin'}
    except Exception:
        return {'ok': False, 'items': [], 'error':'目标内容页无法打开，请完成登录后重试'}
    return {**scan_with_plugin(page, url, words, platform=platform, collect_all=collect_all,
                max_comments=max_comments, scroll_times=20, interval_ms=2000),
            'engine':'builtin'}
