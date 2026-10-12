# -*- coding: utf-8 -*-
"""自动批量发布：软件把标题/正文/话题/图片自动填进创作者平台发布页，
填完自动点击"发布"，截图返回。用户在发布记录中查看截图确认效果。"""
import os
import re

from .config import load_selectors, PAGE_GOTO_TIMEOUT
from .page_guard import check_page

def publish_page_url(kind="image"):
    cfg = load_selectors()
    if kind == "video":
        return cfg.get("publish_video_url") or cfg["creator_home"]
    return cfg.get("publish_image_url") or cfg["creator_home"]


def selector_selftest(page, selectors=None):
    """检查各选择器在当前页面能否命中，返回 {名称: 是否命中}。
    用于小红书改版后快速定位失效的选择器。"""
    selectors = selectors or load_selectors()["selectors"]
    result = {}
    for name, sel in selectors.items():
        try:
            result[name] = page.locator(sel).count() > 0
        except Exception:
            result[name] = False
    return result


def fill_publish_form(page, title, body, topics, image_paths, selectors=None,
                      timeout=12000, builtin_cover=None):
    """填充发布表单（不点发布）。返回 {'ok': bool, 'warnings': [..]}。"""
    check_page(page)
    template_info = None
    if builtin_cover is not None:
        from .xhs_poster import prepare_builtin_poster
        template_info = prepare_builtin_poster(page, builtin_cover)
    selectors = selectors or load_selectors()["selectors"]
    warnings = []

    def _fill(name, value):
        sel = selectors.get(name)
        if not sel:
            warnings.append(f"选择器缺失: {name}")
            return False
        try:
            page.wait_for_function("selector => [...document.querySelectorAll(selector)].some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden')", arg=sel, timeout=timeout)
            candidates = [loc for loc in page.locator(sel).all() if loc.is_visible()]
            if len(candidates) != 1:
                warnings.append(f'无法唯一识别输入框: {name}')
                return False
            loc = candidates[0]
            loc.wait_for(state="visible", timeout=timeout)
            loc.fill(value or "")
            actual = loc.evaluate("e => 'value' in e ? e.value : e.innerText")
            if str(actual).replace('\r\n','\n').strip() != str(value or '').replace('\r\n','\n').strip():
                warnings.append(f'填充后文字不一致: {name}')
                return False
            return True
        except Exception as e:
            warnings.append(f"填充失败 [{name}]: {e.__class__.__name__}")
            return False

    ok_title = _fill("publish_title", title)
    # The creator page has no separate topic input in the observed workflow.
    # Keep the supplied topic text verbatim at the end of the body editor.
    body_text = body or ''
    if topics:
        body_text += ('\n\n' if body_text else '') + topics
    ok_body = _fill("publish_content", body_text)

    ok_images = True
    if image_paths:
        missing = [p for p in image_paths if not os.path.exists(p)]
        if missing:
            warnings.append(f"图片不存在: {missing[0]} 等 {len(missing)} 个")
            ok_images = False
        else:
            try:
                sel = selectors.get("publish_upload")
                page.locator(sel).first.set_input_files(image_paths, timeout=timeout)
            except Exception as e:
                warnings.append(f"图片上传失败: {e.__class__.__name__}")
                ok_images = False

    return {"ok": ok_title and ok_body and ok_images, "warnings": warnings, 'template_info':template_info}


def click_publish(page, selectors=None, timeout=15000):
    """自动点击发布按钮，等待发布完成。返回 (True/False, 说明)。

    成功需要明确的成功页标记或可见成功提示；结果不明确时交给人工核对。
    """
    check_page(page)
    selectors = selectors or load_selectors()["selectors"]
    # 多组备选：创作者平台的"发布"按钮文案可能不同
    cands = ["button:text-is('发布')", "button:text-is('定时发布')", "button:text-is('发布笔记')",
             selectors.get("publish_button") or "", "[class*='publish-btn']"]
    btn = None
    for sel in cands:
        if not sel:
            continue
        try:
            for loc in page.locator(sel).all()[:10]:
                if loc.is_visible() and loc.is_enabled():
                    btn = loc
                    break
            if btn is not None:
                break
        except Exception:
            continue
    if btn is None:
        return False, "找不到发布按钮"
    try:
        btn.scroll_into_view_if_needed(timeout=timeout)
        check_page(page)
        btn.click(timeout=timeout)
    except Exception as e:
        return False, f"待核对：发布点击未能确认完成（{e.__class__.__name__}），不会自动再次点击，请核对笔记管理"
    try:
        page.wait_for_function(
            """() => {
                const url = new URL(location.href);
                if (['creator.rednote.com','creator.xiaohongshu.com'].includes(url.hostname)
                    && url.searchParams.get('published') === 'true') return true;
                return [...document.querySelectorAll('[role="alert"],.el-message--success,.success-message')]
                    .some(node => node.getClientRects().length > 0
                        && getComputedStyle(node).visibility !== 'hidden'
                        && /发布成功|提交成功/.test(node.innerText || ''));
            }""",
            timeout=60000)
        return True, "已发布"
    except Exception:
        return False, "待核对：已点击发布，未检测到明确成功结果；队列会暂停，请先核对笔记管理，不要直接重复发布"


def prepare_page(context, kind="image"):
    """在账号上下文中打开发布页，返回 page。调用方随后填充表单、
    自动点击发布。"""
    page = context.new_page()
    page.goto(publish_page_url(kind), timeout=PAGE_GOTO_TIMEOUT)
    return page
