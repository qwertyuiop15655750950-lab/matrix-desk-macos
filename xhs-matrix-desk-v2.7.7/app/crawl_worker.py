"""JSON worker for offline Crawl4AI processing, in its own Python environment."""
import copy
from contextlib import redirect_stdout
import importlib.metadata
import json
import os
import re
import socket
import sys
from urllib.parse import unquote, urljoin, urlsplit

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def _deny_network(*args, **kwargs):
    raise RuntimeError('离线内容提取禁止网络访问')


def disable_network():
    """Enforce snapshot-only processing, including during dependency imports."""
    socket.create_connection = _deny_network
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.socket.sendto = _deny_network
    socket.getaddrinfo = _deny_network


def _processors():
    try:
        from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        version = importlib.metadata.version('crawl4ai')
        return LXMLWebScrapingStrategy, DefaultMarkdownGenerator, version
    except ImportError as exc:
        raise RuntimeError('Crawl4AI 依赖缺失，请重新安装独立运行环境') from exc


def _url(value):
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('页面网址无效')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('页面网址仅支持 HTTP 或 HTTPS')
        parts.port
    except ValueError:
        raise ValueError('页面网址仅支持有效的 HTTP 或 HTTPS 地址')
    return value


def _process_comments(request, version):
    """Normalize row scope, then let Crawl4AI's real CSS strategy read fields."""
    from comment_selectors import (COMMENT_ID_ATTRIBUTES, COMMENT_SELECTORS,
        PUBLIC_COMMENT_ATTRIBUTES, TIME_ATTRIBUTES, USER_ID_ATTRIBUTES,
        PLATFORM_HOSTS, comment_schema, comment_source_url)
    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    from lxml import html as lhtml
    platform = request.get('platform')
    snapshot = request.get('snapshot')
    if not isinstance(snapshot, dict):
        raise ValueError('评论页面快照格式错误')
    source_url = comment_source_url(snapshot.get('url'), platform)
    html = snapshot.get('html')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('评论页面快照为空')
    if len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise ValueError('评论页面 HTML 超过 5MB')
    selector = request.get('selector', '')
    if not isinstance(selector, str) or len(selector) > 500:
        raise ValueError('CSS 选择器最多 500 个字符')
    maximum = request.get('max_comments', 1000)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 1000:
        raise ValueError('评论数量必须在 1 至 1000 之间')
    config = COMMENT_SELECTORS[platform]
    item_selector = selector.strip() or config['item']
    try:
        doc = lhtml.document_fromstring(html)
    except (ValueError, lhtml.etree.ParserError) as exc:
        raise ValueError('评论页面 HTML 无法解析') from exc

    unwanted = {'script', 'style', 'noscript', 'template', 'form', 'input',
        'textarea', 'select', 'option', 'button', 'iframe', 'object', 'embed',
        'canvas', 'svg', 'head', 'meta', 'link'}
    # Browser capture has already checked computed visibility; this independently
    # removes static hidden/editor state and arbitrary attributes from its input.
    for node in list(doc.iter()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.getparent().remove(node)
            continue
        style = re.sub(r'\s+', '', node.get('style') or '').lower()
        if (node.tag.lower() in unwanted or node.get('id') == 'matrix-browser-collector'
                or node.get('hidden') is not None
                or (node.get('aria-hidden') or '').lower() == 'true'
                or node.get('contenteditable') is not None
                or 'display:none' in style or 'visibility:hidden' in style
                or 'visibility:collapse' in style):
            if node.getparent() is not None:
                node.drop_tree()
            else:
                node.clear()
            continue
        for attribute in list(node.attrib):
            if attribute not in PUBLIC_COMMENT_ATTRIBUTES:
                del node.attrib[attribute]
    try:
        originals = doc.cssselect(item_selector)
        # Validate the caller's selector explicitly. Never interpret malformed
        # CSS as a request to read the whole page or silently change strategy.
        nested_selector = ', '.join(dict.fromkeys((config['item'], item_selector)))
        doc.cssselect(nested_selector)
    except Exception as exc:
        raise ValueError('CSS 选择器无效，请检查评论选择器格式') from exc

    def attribute_value(node, choices):
        if node is None:
            return ''
        return next((node.get(name).strip() for name in choices
                     if isinstance(node.get(name), str) and node.get(name).strip()), '')

    def inside(child, parent):
        return parent is not None and (child is parent or parent in child.iterancestors())

    def first_node(root, choices, excluded=None):
        for choice in choices:
            for candidate in root.cssselect(choice):
                if excluded is not None and (inside(candidate, excluded) or inside(excluded, candidate)):
                    continue
                if candidate.text_content().strip():
                    return candidate
        return None

    def profile_id(href):
        if not href:
            return ''
        try:
            parts = urlsplit(urljoin(source_url, href))
            if (parts.scheme != 'https' or parts.hostname not in PLATFORM_HOSTS[platform]
                    or parts.username or parts.password or parts.port not in (None, 443)):
                return ''
            prefix = '/user/profile/' if platform == 'xiaohongshu' else '/user/'
            if not parts.path.startswith(prefix):
                return ''
            value = unquote(parts.path[len(prefix):].rstrip('/')).strip()
            return identity(value)
        except (ValueError, TypeError):
            return ''

    def identity(value):
        if not isinstance(value, str):
            return ''
        value = value.strip()
        if not value or len(value) > 256 or re.search(r'[\s/?#\x00-\x1f\x7f]', value):
            return ''
        return value

    content = lhtml.Element('div')
    for original in originals:
        row = copy.deepcopy(original)
        row.tail = None
        for nested in row.cssselect(nested_selector):
            if nested is not row and nested.getparent() is not None:
                # Dropping only the nested element retains the outer author's
                # own surrounding text; each reply is extracted separately.
                nested.drop_tree()
        text_node = first_node(row, config['text'])
        if text_node is None:
            continue
        author_node = first_node(row, config['author'], text_node)
        time_node = first_node(row, config['time'], text_node)
        author_link = None
        if author_node is not None:
            links = ([author_node] if author_node.tag.lower() == 'a' else [])
            links += list(author_node.iterancestors('a'))
            links += author_node.cssselect('a[href]')
            for link in links:
                if inside(link, row) and not inside(link, text_node) and profile_id(link.get('href')):
                    author_link = link
                    break
        # Marker wrappers scope the CSS strategy; every value below is read from
        # the original public DOM and no nickname-based identity is fabricated.
        wrapper = lhtml.Element('article')
        wrapper.set('data-matrix-comment-row', '')
        wrapper.set('data-comment-id', attribute_value(row, COMMENT_ID_ATTRIBUTES))
        wrapper.set('data-user-id', attribute_value(row, USER_ID_ATTRIBUTES))
        wrapper.set('data-created-at', attribute_value(row, TIME_ATTRIBUTES))
        text_node.set('data-matrix-comment-content', '')
        if author_node is not None:
            author_node.set('data-matrix-comment-author', '')
            author_node.set('data-user-id', attribute_value(author_node, USER_ID_ATTRIBUTES))
        if author_link is not None:
            author_link.set('data-matrix-comment-author-link', '')
            author_link.set('data-user-id', attribute_value(author_link, USER_ID_ATTRIBUTES))
            # Only validated public profile URLs are retained, and query tokens
            # are excluded before the third-party processor sees any href.
            parts = urlsplit(urljoin(source_url, author_link.get('href')))
            author_link.set('href', f'https://{parts.netloc}{parts.path}')
        if time_node is not None:
            time_node.set('data-matrix-comment-time', '')
            time_node.set('data-created-at', attribute_value(time_node, TIME_ATTRIBUTES))
        for link in row.cssselect('[href]'):
            if link is not author_link:
                link.attrib.pop('href', None)
        wrapper.append(row)
        content.append(wrapper)
    public_html = lhtml.tostring(content, encoding='unicode', method='html', with_tail=False)
    extracted = JsonCssExtractionStrategy(schema=comment_schema()).extract(source_url, public_html)
    rows, seen = [], set()
    for item in extracted:
        text = str(item.get('content') or '').strip()[:10000]
        if not text:
            continue
        user_id = (profile_id(item.get('author_href')) or identity(item.get('author_user_id'))
                   or identity(item.get('profile_user_id')) or identity(item.get('item_user_id')))
        row = {'comment_id': identity(item.get('comment_id')), 'user_id': user_id,
               'nickname': str(item.get('nickname') or '').strip()[:500], 'content': text}
        created_at = str(item.get('time_attribute') or item.get('time_text')
                         or item.get('item_created_at') or '').strip()[:200]
        if created_at:
            row['created_at'] = created_at
        key = (row['comment_id'], row['user_id'], row['nickname'], row['content'])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= maximum:
            break
    return {'rows': rows, 'source_url': source_url, 'platform': platform,
            'engine': 'crawl4ai', 'version': version, 'selector': selector.strip(),
            'matched_nodes': len(originals)}


def process(request):
    """Pure DOM extraction; does not construct AsyncWebCrawler or a browser."""
    if not isinstance(request, dict):
        raise ValueError('内容提取请求格式错误')
    strategy_class, generator_class, version = _processors()
    if request.get('operation') == 'status':
        return {'available': True, 'version': version}
    if request.get('operation') == 'extract_comments':
        return _process_comments(request, version)
    if request.get('operation') != 'extract':
        raise ValueError('不支持的内容提取操作')
    snapshot = request.get('snapshot')
    if not isinstance(snapshot, dict):
        raise ValueError('页面快照格式错误')
    source_url = _url(snapshot.get('url'))
    html = snapshot.get('html')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('页面快照为空')
    if len(html.encode('utf-8')) > MAX_HTML_BYTES:
        raise ValueError('页面 HTML 超过 5MB')
    selector = request.get('selector', '')
    if not isinstance(selector, str) or len(selector) > 500:
        raise ValueError('CSS 选择器最多 500 个字符')
    keywords = request.get('keywords', [])
    if (not isinstance(keywords, list) or len(keywords) > 50
            or any(not isinstance(word, str) or len(word) > 100 for word in keywords)):
        raise ValueError('关键词格式错误')

    from lxml import html as lhtml
    try:
        doc = lhtml.document_fromstring(html)
    except (ValueError, lhtml.etree.ParserError) as exc:
        raise ValueError('页面 HTML 无法解析') from exc
    title = snapshot.get('title')
    if not isinstance(title, str):
        title = ''
    title = title.strip() or ''.join(doc.xpath('//title/text()')).strip()
    metadata = {'title': title[:1000]}
    for field in ('description', 'author', 'language'):
        values = doc.xpath('//meta[translate(@name,"ABCDEFGHIJKLMNOPQRSTUVWXYZ","abcdefghijklmnopqrstuvwxyz")=$name]/@content', name=field)
        if values:
            metadata[field] = str(values[0])[:2000]
    if doc.get('lang'):
        metadata['language'] = doc.get('lang')[:100]

    # Exclude login forms, editors, hidden page state, browser plugin controls,
    # and executable/embedded content before either selecting or converting.
    unwanted = ('script', 'style', 'noscript', 'template', 'form', 'input',
        'textarea', 'select', 'option', 'button', 'iframe', 'object', 'embed',
        'canvas', 'svg', 'head')
    for node in list(doc.iterdescendants()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.getparent().remove(node)
            continue
        inline_style = (node.get('style') or '').replace(' ', '').lower()
        if (node.tag.lower() in unwanted or node.get('id') == 'matrix-browser-collector'
                or node.get('hidden') is not None or node.get('aria-hidden') == 'true'
                or node.get('contenteditable') in ('', 'true')
                or 'display:none' in inline_style or 'visibility:hidden' in inline_style):
            if node.getparent() is not None:
                node.drop_tree()
    bodies = doc.xpath('//body')
    body = bodies[0] if bodies else doc
    if selector.strip():
        try:
            selected = doc.cssselect(selector.strip())
        except Exception as exc:
            raise ValueError('CSS 选择器无效，请检查格式') from exc
        if not selected:
            raise ValueError('CSS 选择器没有匹配到正文，请检查当前页面')
        # Avoid repeated content when the selector matches both a parent and
        # its descendants. Do not include tails outside the selected element.
        selected_set = set(selected)
        content = lhtml.Element('div')
        for node in selected:
            if any(parent in selected_set for parent in node.iterancestors()):
                continue
            selected_node = copy.deepcopy(node)
            selected_node.tail = None
            content.append(selected_node)
    else:
        content = body
    if not content.text_content().strip():
        raise ValueError('页面没有可归档的正文，请打开内容页面后重试')

    # Make all allowed links absolute before the Markdown generator sees them;
    # neither href extraction nor image handling makes any HTTP requests.
    for node in content.xpath('.//a[@href]'):
        href = urljoin(source_url, node.get('href', ''))
        try:
            node.set('href', _url(href))
        except ValueError:
            node.attrib.pop('href', None)
    sanitized_html = lhtml.tostring(content, encoding='unicode', method='html', with_tail=False)
    scraped = strategy_class().scrap(source_url, sanitized_html,
        word_count_threshold=0, remove_forms=True, remove_comments=True,
        exclude_all_images=True, keep_data_attributes=False)
    if not scraped.success:
        raise RuntimeError('Crawl4AI 无法提取当前页面正文')
    generated = generator_class(options={'ignore_images': True, 'body_width': 0}).generate_markdown(
        scraped.cleaned_html, base_url=source_url, citations=False)
    markdown = generated.raw_markdown.strip()
    if not markdown or markdown.startswith(('Error converting HTML', 'Error in markdown generation')):
        raise ValueError('Crawl4AI 没有提取到有效正文')
    links = []
    seen = set()
    for link in [*scraped.links.internal, *scraped.links.external]:
        try:
            href = _url(link.href)
        except ValueError:
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append({'url': href, 'text': (link.text or '')[:1000]})
        if len(links) >= 2000:
            break
    words = list(dict.fromkeys(word.strip() for word in keywords if word.strip()))
    return {'url': source_url, 'title': title[:1000], 'markdown': markdown,
        'links': links, 'metadata': metadata, 'engine': 'crawl4ai',
        'version': version, 'selector': selector.strip(),
        'matched_keywords': [word for word in words if word.casefold() in markdown.casefold()]}


def main():
    disable_network()
    os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('内容提取请求超过限制')
        request = json.loads(raw.decode('utf-8'))
        # Third-party import prints and extraction diagnostics stay out of the
        # JSON channel. The parent caps and discards this local stderr stream.
        with redirect_stdout(sys.stderr):
            result = process(request)
        reply = {'ok': True, 'result': result}
        encoded = json.dumps(reply, ensure_ascii=False).encode('utf-8')
        if len(encoded) > MAX_OUTPUT_BYTES:
            raise ValueError('提取结果超过限制，请使用 CSS 选择器缩小范围')
    except Exception as exc:
        reply = {'ok': False, 'error': str(exc)[:500]}
        encoded = json.dumps(reply, ensure_ascii=False).encode('utf-8')
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
