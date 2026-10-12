"""Use the creator page's own text-to-image cards without rewriting user text."""
import random
import time
from .page_guard import check_page

TEMPLATE_NAMES = ('基础','提问','插图','美漫','清新','备忘','涂鸦','边框','便签','手写')


class PosterGenerationError(ValueError):
    pass


def prepare_builtin_poster(page, cover_text, timeout=60000, choose=None):
    if not str(cover_text).strip():
        raise PosterGenerationError('封面大字不能为空')
    check_page(page)
    page.get_by_role('button', name='文字配图', exact=True).click(timeout=15000)
    page.wait_for_function("() => [...document.querySelectorAll('[contenteditable=\"true\"],textarea')].some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')", timeout=15000)
    editors = [loc for loc in page.locator('[contenteditable="true"],textarea').all() if loc.is_visible()]
    if len(editors) != 1:
        raise PosterGenerationError('无法唯一识别文字配图输入框，已停止')
    editor = editors[0]
    editor.fill(cover_text)
    actual = editor.evaluate("e => e.tagName === 'TEXTAREA' ? e.value : e.innerText")
    if actual.replace('\r\n','\n').strip() != cover_text.replace('\r\n','\n').strip():
        raise PosterGenerationError('封面文字填充后不一致，已停止')
    check_page(page)
    page.get_by_text('生成图片', exact=True).click(timeout=15000)
    deadline = time.monotonic() + timeout / 1000
    available = []
    ready_since = None
    last_names = ()
    while time.monotonic() < deadline:
        check_page(page)
        available = []
        for name in TEMPLATE_NAMES:
            for label in page.get_by_text(name, exact=True).all():
                if not label.is_visible(): continue
                ready = label.evaluate("""label => {
                    let node = label;
                    for (let depth=0; node && depth<3; depth++,node=node.parentElement) {
                        const images = [...node.querySelectorAll('img')];
                        if (images.length === 1) return images[0].complete && images[0].naturalWidth > 0;
                        if (images.length > 1) return false;
                    }
                    return false;
                }""")
                if ready: available.append((name,label))
        names = tuple(name for name, _ in available)
        if names != last_names:
            ready_since = time.monotonic()
            last_names = names
        if available and (len(available) == len(TEMPLATE_NAMES) or time.monotonic() - ready_since >= 1):
            break
        page.wait_for_timeout(250)
    if not available:
        raise PosterGenerationError('小红书模板图片未成功加载，已暂停；请人工检查平台页面，不会替换为自绘图片或重复生成')
    name, label = (choose or random.SystemRandom().choice)(available)
    check_page(page)
    label.click(timeout=10000)
    page.get_by_role('button',name='下一步',exact=True).click(timeout=15000)
    check_page(page)
    return {'template':name,'available_templates':len(available)}
