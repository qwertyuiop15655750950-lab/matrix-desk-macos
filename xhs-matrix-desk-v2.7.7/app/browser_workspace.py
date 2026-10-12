"""Ephemeral manual browser view; uses existing account actors and never starts them."""
import base64
import io
import math
import time
import uuid
from PIL import Image

KEYS = frozenset({'Enter', 'Backspace', 'Delete', 'Tab', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'})


def find_page(context, token):
    if not isinstance(token, str) or len(token) != 32:
        raise ValueError('页面标识无效，请刷新总览')
    page = next((p for p in context.pages if not p.is_closed() and getattr(p, '_matrix_preview_token', None) == token), None)
    if page is None:
        raise ValueError('该页面已关闭，请刷新总览')
    return page


def capture(context, token=''):
    open_pages = [p for p in context.pages if not p.is_closed()]
    tabs = []
    for page in open_pages[:64]:
        page._matrix_preview_token = getattr(page, '_matrix_preview_token', None) or uuid.uuid4().hex
        try: title = page.title()[:100]
        except Exception: title = '页面'
        tabs.append({'token': page._matrix_preview_token, 'title': title})
    pages = [find_page(context, token)] if token else open_pages[:4]
    result = []
    for page in pages:
        page._matrix_preview_token = getattr(page, '_matrix_preview_token', None) or uuid.uuid4().hex
        item = {'token': page._matrix_preview_token}
        try:
            url = page.url
            size = page.evaluate('() => ({width:innerWidth,height:innerHeight})')
            raw = page.screenshot(type='jpeg', quality=65 if token else 40, full_page=False, timeout=6000)
            if page.url != url or page.evaluate('() => ({width:innerWidth,height:innerHeight})') != size:
                raise ValueError('page changed while capturing')
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail((1440, 1000) if token else (600, 400))
                buffer = io.BytesIO()
                image.convert('RGB').save(buffer, format='JPEG', quality=70 if token else 55)
            # Only a full frame authorizes input. Thumbnail refresh cannot invalidate it.
            frame = uuid.uuid4().hex if token else ''
            if token:
                page._matrix_manual_frame = {'id': frame, 'time': time.monotonic(), 'url': url, 'size': size}
            item.update(title=page.title()[:100], image='data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode(), frame=frame)
        except Exception:
            item.update(title='页面预览不可用', error='页面未就绪或已关闭，请刷新后重试')
        result.append(item)
    return {'items': result, 'tabs': tabs, 'more': 0 if token else max(0, len(open_pages)-4)}


def manual_input(context, token, frame, action, params):
    page = find_page(context, token)
    state = getattr(page, '_matrix_manual_frame', None)
    if (not state or state['id'] != frame or time.monotonic()-state['time'] > 8
            or page.url != state['url'] or page.evaluate('() => ({width:innerWidth,height:innerHeight})') != state['size']):
        raise ValueError('画面已经变化，请刷新后重新操作')
    if not isinstance(params, dict):
        raise ValueError('操作参数无效')
    if action not in ('click', 'scroll', 'key', 'text'):
        raise ValueError('不支持该手动操作')
    from .page_guard import check_page
    check_page(page)
    def number(name, low, high):
        value = params.get(name)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('操作坐标或滚动距离无效')
        return value
    if action in ('click', 'scroll'):
        x, y = number('x', 0, 1), number('y', 0, 1)
        delta = number('delta', -900, 900) if action == 'scroll' else 0
    elif action == 'key':
        key = params.get('key')
        if key not in KEYS:
            raise ValueError('不支持该按键')
    else:
        content = params.get('text')
        if not isinstance(content, str) or not content or len(content) > 2000:
            raise ValueError('输入文字应为 1–2000 字')
        if not page.evaluate('() => {const e=document.activeElement;return !!e && (e.isContentEditable || ["INPUT","TEXTAREA"].includes(e.tagName));}'):
            raise ValueError('请先点击页面中的输入框')
    # Consume before dispatch; a network failure never replays a click or text.
    page._matrix_manual_frame = None
    if action == 'click':
        page.mouse.click(min(state['size']['width']-1, x*state['size']['width']), min(state['size']['height']-1, y*state['size']['height']))
    elif action == 'scroll':
        page.mouse.move(min(state['size']['width']-1, x*state['size']['width']), min(state['size']['height']-1, y*state['size']['height']))
        page.mouse.wheel(0, delta)
    elif action == 'key':
        page.keyboard.press(key)
    else:
        page.keyboard.insert_text(content)
    return {'accepted': True}
