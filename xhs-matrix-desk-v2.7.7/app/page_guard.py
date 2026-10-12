"""Read visible platform access warnings; never retry or solve them."""
from pathlib import Path
from urllib.parse import urlsplit
from .comment_selectors import PLATFORM_HOSTS

SUPPORTED_HOSTS = frozenset().union(*PLATFORM_HOSTS.values(), {
    'channels.weixin.qq.com', 'creator.rednote.com', 'creator.xiaohongshu.com'})

SCRIPT = (Path(__file__).parent / 'browser_plugin/page_guard.js').read_text(encoding='utf-8')


class PageBlocked(ValueError):
    def __init__(self, result):
        self.result = {'ok': False, 'items': [], 'read': 0, 'stopped': True, **result}
        super().__init__(result['error'])


def check_page(page):
    parsed = urlsplit(page.url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.hostname not in SUPPORTED_HOSTS):
        raise ValueError('页面已离开支持的平台，已停止自动操作')
    page.evaluate(SCRIPT)
    result = page.evaluate('globalThis.__matrixReadAccessGate()')
    if not isinstance(result, dict) or not isinstance(result.get('blocked'), bool):
        raise ValueError('无法检查页面访问状态，已停止自动操作')
    if result['blocked']:
        raise PageBlocked(result)

