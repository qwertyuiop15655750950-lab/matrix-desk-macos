"""Local message bridge to the visible, read-only browser comment plugin."""
from pathlib import Path
from urllib.parse import urlparse
import uuid
from .page_guard import check_page, PageBlocked
from .comment_selectors import PLATFORM_HOSTS

PLUGIN_DIR = Path(__file__).resolve().parent / 'browser_plugin'
SCAN_TIMEOUT_MS = 120000
STOP_GRACE_MS = 2000

HOSTS = PLATFORM_HOSTS


def extension_launch_options():
    """Launch bundled Chromium with only this local unpacked extension enabled."""
    if not (PLUGIN_DIR / 'manifest.json').is_file():
        raise ValueError('本地浏览器采集插件缺失，请检查安装文件')
    folder = str(PLUGIN_DIR)
    return {'channel': 'chromium',
            'args': [f'--disable-extensions-except={folder}', f'--load-extension={folder}'],
            'ignore_default_args': ['--disable-extensions']}


SCAN_SCRIPT = r'''options => new Promise(resolve => {
    let finished = false;
    let timedOut = false;
    let timer;
    let graceTimer;
    const finish = result => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        clearTimeout(graceTimer);
        window.removeEventListener('message', receive);
        resolve(result);
    };
    const receive = event => {
        const message = event.data;
        if (event.source !== window || event.origin !== location.origin || !message
                || message.type !== 'MATRIX_PLUGIN_RESULT'
                || message.requestId !== options.requestId) return;
        const result = message.result;
        finish(timedOut && result && result.ok
            ? {...result, timed_out: true, stopped: true} : result);
    };
    window.addEventListener('message', receive);
    timer = setTimeout(() => {
        timedOut = true;
        window.postMessage({type: 'MATRIX_PLUGIN_CONTROL', requestId: options.requestId,
                            action: 'stop', reason: 'timeout'}, location.origin);
        // Let the plugin return already captured rows before removing the reply
        // listener. The former immediate timeout discarded those rows.
        graceTimer = setTimeout(() => finish({ok: false, items: [],
            error: '插件采集等待超时，停止请求未收到回复；请查看浏览器采集面板后重试'}),
            options.stopGraceMs);
    }, options.timeoutMs);
    window.postMessage({type: 'MATRIX_PLUGIN_SCAN', ...options}, location.origin);
})'''


def scan_with_plugin(page, url, words, platform='douyin', max_comments=100,
                     scroll_times=5, interval_ms=None, collect_all=False):
    """Request DOM scanning and let the user pause or stop it in the browser."""
    parsed = urlparse(url)
    if (platform not in HOSTS or parsed.scheme != 'https'
            or parsed.hostname not in HOSTS[platform]
            or parsed.username or parsed.password):
        return {'ok': False, 'items': [], 'error': '请使用所选平台的完整 HTTPS 视频或笔记地址'}
    if not isinstance(words, (list, tuple)) or any(not isinstance(word, str) for word in words):
        return {'ok': False, 'items': [], 'error': '关键词需要是文字列表'}
    keywords = list(dict.fromkeys(word.strip() for word in words if word.strip()))
    if not keywords and not collect_all:
        return {'ok': False, 'items': [], 'error': '请先设置至少一个关键词'}
    try:
        # Keep tracking URL differences from closing the current comment drawer.
        from .comment_crawler import _same_content
        if not _same_content(page.url, url, platform):
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
        current = urlparse(page.url)
        if (current.scheme != 'https' or current.hostname not in HOSTS[platform]
                or current.username or current.password):
            return {'ok': False, 'items': [], 'error': '浏览器跳转到了其他平台，请打开所选平台的目标页面'}
        check_page(page)
        if not _same_content(page.url, url, platform):
            return {'ok': False, 'items': [], 'error': '打开后的页面不是指定内容，请完成登录并打开目标评论页'}
        page.wait_for_function(
            "document.documentElement.dataset.matrixCollector === 'ready' && "
            "!!document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start')",
            timeout=15000)
        options = {'requestId': uuid.uuid4().hex, 'platform': platform,
                   'keywords': keywords[:200], 'collectAll': bool(collect_all), 'maxComments': min(max(int(max_comments), 1), 1000),
                   'scrollTimes': min(max(int(scroll_times), 0), 20),
                   'timeoutMs': SCAN_TIMEOUT_MS, 'stopGraceMs': STOP_GRACE_MS}
        if interval_ms is not None:
            options['intervalMs'] = min(max(int(interval_ms), 2000), 10000)
        result = page.evaluate(SCAN_SCRIPT, options)
    except PageBlocked as error:
        return error.result
    except Exception:
        return {'ok': False, 'items': [],
                'error': '浏览器采集插件未就绪或页面已关闭，请重新打开该账号浏览器后重试'}
    if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
        return {'ok': False, 'items': [], 'error': '浏览器采集插件返回格式无效'}
    if not result['ok'] and not result.get('blocked'):
        return {'ok': False, 'items': [], 'error': str(result.get('error') or '插件采集失败')}
    items = result.get('items', [])
    if not isinstance(items, list):
        return {'ok': False, 'items': [], 'error': '浏览器采集插件评论格式无效'}
    cleaned = []
    for row in items[:options['maxComments']]:
        if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip():
            continue
        contacts = row.get('contacts') if isinstance(row.get('contacts'), dict) else {}
        identifier = row.get('user_id') if isinstance(row.get('user_id'), str) else ''
        if any(c.isspace() or c in '/?#' for c in identifier):
            identifier = ''
        cleaned.append({'comment_id': str(row.get('comment_id') or ''), 'nickname': row.get('nickname') if isinstance(row.get('nickname'), str) else '',
                        'user_id': identifier,
                        'text': str(row['text']).strip(), 'keyword': str(row.get('keyword') or ''),
                        'contacts': {name: str(contacts.get(name) or '') for name in ('wechat', 'phone', 'qq')}})
    return {**result, 'items': cleaned}
