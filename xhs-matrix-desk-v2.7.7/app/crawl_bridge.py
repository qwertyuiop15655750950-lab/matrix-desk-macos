"""Run the optional Crawl4AI runtime on a browser snapshot, without navigation."""
import json
import os
from pathlib import Path
import subprocess
import threading
from urllib.parse import urlsplit

from .comment_selectors import comment_source_url

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 64 * 1024
APP_DIR = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).with_name('crawl_worker.py')


def _python_path(python_path=None):
    if python_path is not None:
        return Path(python_path).expanduser()
    override = os.environ.get('MATRIX_CRAWL4AI_PYTHON')
    if override:
        return Path(override).expanduser()
    venv = APP_DIR.parent / 'dev-tools' / 'crawl4ai' / 'venv'
    return venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def _invoke(request, *, python_path=None, state_dir=None, timeout=40):
    executable = _python_path(python_path)
    if not executable.is_file():
        raise RuntimeError('Crawl4AI 尚未安装；请安装独立运行环境，或设置 MATRIX_CRAWL4AI_PYTHON')
    state = Path(state_dir) if state_dir is not None else APP_DIR / 'data' / 'crawl4ai'
    state.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        'CRAWL4_AI_BASE_DIRECTORY': str(state.resolve()),
        'LITELLM_LOCAL_MODEL_COST_MAP': 'True',
        'NLTK_DATA': str(state.resolve() / 'nltk'),
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    })
    # The worker has no network operations and denies socket connections before
    # importing Crawl4AI. The browser snapshot is its only content input.
    try:
        proc = subprocess.Popen([str(executable), '-u', str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, env=env, cwd=str(APP_DIR))
    except OSError as exc:
        raise RuntimeError('无法启动 Crawl4AI 独立运行环境') from exc

    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    exceeded = threading.Event()

    def read_stream(name, stream, limit):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                if len(buffers[name]) + len(chunk) > limit:
                    exceeded.set()
                    proc.kill()
                    break
                buffers[name].extend(chunk)
        finally:
            stream.close()

    payload = json.dumps(request, ensure_ascii=False).encode('utf-8')

    def write_request():
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            proc.stdin.close()

    readers = [threading.Thread(target=read_stream,
        args=(name, stream, limit), daemon=True) for name, stream, limit in (
            ('stdout', proc.stdout, MAX_OUTPUT_BYTES),
            ('stderr', proc.stderr, MAX_LOG_BYTES))]
    writer = threading.Thread(target=write_request, daemon=True)
    for thread in readers:
        thread.start()
    writer.start()
    timed_out = False
    try:
        proc.wait(timeout=max(0.1, float(timeout)))
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait(timeout=5)
    finally:
        writer.join(timeout=5)
        for thread in readers:
            thread.join(timeout=5)
    if timed_out:
        raise RuntimeError('Crawl4AI 内容提取超时，请缩小页面范围后重试')
    if exceeded.is_set():
        raise RuntimeError('Crawl4AI 输出超过限制，请使用 CSS 选择器缩小提取范围')
    try:
        reply = json.loads(buffers['stdout'].decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        # Do not surface raw HTML, dependency logs, or any stdout fragments.
        raise RuntimeError('Crawl4AI 未返回有效结果，请检查独立运行环境') from exc
    if not isinstance(reply, dict) or not isinstance(reply.get('ok'), bool):
        raise RuntimeError('Crawl4AI 返回格式错误')
    if not reply['ok']:
        error = reply.get('error')
        raise RuntimeError(error[:500] if isinstance(error, str) else 'Crawl4AI 内容提取失败')
    if proc.returncode != 0 or not isinstance(reply.get('result'), dict):
        raise RuntimeError('Crawl4AI 运行失败')
    return reply['result']


def status(*, python_path=None, state_dir=None):
    """Check the installed offline processors; never starts a browser."""
    try:
        result = _invoke({'operation': 'status'}, python_path=python_path,
            state_dir=state_dir, timeout=20)
        return {'available': True, 'version': result['version'], 'error': ''}
    except Exception as exc:
        return {'available': False, 'version': '', 'error': str(exc)[:500]}


def extract(snapshot, selector='', *, keywords=None, python_path=None,
            state_dir=None, timeout=40):
    """Extract loaded DOM content; keywords tag matches without deleting text."""
    if not isinstance(snapshot, dict):
        raise ValueError('页面快照格式错误')
    html = snapshot.get('html')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('页面快照为空，请先打开需要归档的页面')
    if len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise ValueError('页面 HTML 超过 5MB，请缩小页面范围后重试')
    url = snapshot.get('url')
    try:
        parts = urlsplit(url)
        if (not isinstance(url, str) or len(url) > 4096
                or parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username or parts.password):
            raise ValueError('页面网址无效')
        parts.port
    except (ValueError, TypeError):
        raise ValueError('页面网址仅支持有效的 HTTP 或 HTTPS 地址')
    if not isinstance(selector, str) or len(selector) > 500:
        raise ValueError('CSS 选择器最多 500 个字符')
    if keywords is None:
        keywords = []
    if (not isinstance(keywords, list) or len(keywords) > 50
            or any(not isinstance(word, str) or len(word) > 100 for word in keywords)):
        raise ValueError('关键词请使用数组，最多 50 个，每个最多 100 个字符')
    title = snapshot.get('title', '')
    title = title[:1000] if isinstance(title, str) else ''
    request = {'operation': 'extract', 'snapshot': {
        'url': url, 'title': title, 'html': html},
        'selector': selector.strip(), 'keywords': keywords}
    result = _invoke(request, python_path=python_path, state_dir=state_dir, timeout=timeout)
    if (not isinstance(result.get('markdown'), str) or not result['markdown'].strip()
            or not isinstance(result.get('links'), list)
            or result.get('engine') != 'crawl4ai'):
        raise RuntimeError('Crawl4AI 没有返回有效正文')
    return result


def extract_comments(snapshot, platform, selector='', *, max_comments=1000,
                     python_path=None, state_dir=None, timeout=40):
    """Structure the selected browser's already loaded public comment DOM.

    This never navigates or starts a crawler browser. No credentials, cookies,
    network responses, or page state from outside the bounded snapshot are sent.
    Empty rows mean no matching loaded comments, without a whole-page fallback.
    """
    if not isinstance(snapshot, dict):
        raise ValueError('评论页面快照格式错误')
    html = snapshot.get('html')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('评论页面快照为空，请先打开评论区')
    if len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise ValueError('评论页面 HTML 超过 5MB，请缩小采集范围')
    source_url = comment_source_url(snapshot.get('url'), platform)
    if not isinstance(selector, str) or len(selector) > 500:
        raise ValueError('CSS 选择器最多 500 个字符')
    if isinstance(max_comments, bool) or not isinstance(max_comments, int) or not 1 <= max_comments <= 1000:
        raise ValueError('评论数量必须在 1 至 1000 之间')
    request = {'operation': 'extract_comments', 'platform': platform,
               'snapshot': {'url': source_url, 'html': html},
               'selector': selector.strip(), 'max_comments': max_comments}
    result = _invoke(request, python_path=python_path, state_dir=state_dir, timeout=timeout)
    rows = result.get('rows')
    if (not isinstance(rows, list) or len(rows) > max_comments
            or result.get('engine') != 'crawl4ai' or result.get('platform') != platform
            or result.get('source_url') != source_url
            or any(not isinstance(row, dict)
                   or any(not isinstance(row.get(field), str) for field in
                          ('comment_id', 'user_id', 'nickname', 'content'))
                   or not row['content'].strip()
                   or any(key not in {'comment_id', 'user_id', 'nickname', 'content', 'created_at'} for key in row)
                   or ('created_at' in row and not isinstance(row['created_at'], str))
                   for row in rows)):
        raise RuntimeError('Crawl4AI 未返回有效评论结构')
    return result
