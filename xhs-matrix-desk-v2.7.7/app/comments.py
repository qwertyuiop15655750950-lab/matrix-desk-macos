"""评论区采集：账号主页笔记列表 + 单篇笔记评论读取。

只读不操作（不点赞不回复不关注）。选择器走 config/selectors.json
的 comment_page 段，多组备选按序尝试，小红书改版后改配置即可。
"""
import re
import time


def _first_hit(page, selector_csv, timeout=3000):
    """多个备选选择器按序试，返回首个命中的 locator；都没命中返回 None。"""
    for sel in (selector_csv or "").split(","):
        sel = sel.strip()
        if not sel:
            continue
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def get_own_user_id(page, timeout=8000):
    """从已登录页面拿当前账号的 user_id。返回 str，拿不到返回 ''。"""
    # 策略1：右上角头像/个人主页链接
    try:
        for sel in ["a[href*='/user/profile/']"]:
            loc = page.locator(sel).first
            try:
                href = loc.get_attribute("href", timeout=timeout) or ""
            except Exception:
                continue
            m = re.search(r"/user/profile/([a-f0-9]+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    # 策略2：cookie 里找
    try:
        for c in page.context.cookies():
            if c.get("name") in ("userId", "xhs_user_id"):
                return c.get("value") or ""
    except Exception:
        pass
    return ""


def list_profile_notes(page, user_id, sel, max_notes=20):
    """打开个人主页，返回笔记列表 [{note_id, url, title}]。"""
    notes = []
    seen = set()
    try:
        page.goto(sel["profile_url_tpl"].format(user_id=user_id),
                  timeout=30000)
    except Exception as e:
        if "interrupted by another navigation" not in str(e):
            raise
    page.wait_for_timeout(3000)

    # 滚几屏把笔记加载出来
    for _ in range(4):
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(1200)

    link_loc = _first_hit(page, sel.get("note_link", ""))
    if not link_loc:
        return notes
    for el in link_loc.all():
        try:
            href = el.get_attribute("href") or ""
        except Exception:
            continue
        m = re.search(r"/(?:explore|discovery/item)/([a-f0-9]+)", href)
        if not m:
            continue
        nid = m.group(1)
        if nid in seen:
            continue
        seen.add(nid)
        url = "https://www.rednote.com" + href.split("?")[0]
        title = ""
        try:
            title = (el.inner_text() or "").strip().split("\n")[0][:60]
        except Exception:
            pass
        notes.append({"note_id": nid, "url": url, "title": title})
        if len(notes) >= max_notes:
            break
    return notes


def read_note_comments(page, note_url, sel, max_comments=60,
                       scroll_rounds=6):
    """打开一篇笔记，滚动加载评论，返回 [{author, text, time}]。"""
    comments = []
    seen_texts = set()
    try:
        page.goto(note_url, timeout=30000)
    except Exception as e:
        if "interrupted by another navigation" not in str(e):
            raise
    page.wait_for_timeout(3000)

    item_loc = None
    for _ in range(scroll_rounds + 1):
        item_loc = _first_hit(page, sel.get("comment_item", ""))
        if item_loc:
            for el in item_loc.all():
                try:
                    text = ""
                    author = ""
                    ctime = ""
                    t = _first_hit(el, sel.get("comment_text", ""))
                    if t:
                        text = (t.first.inner_text() or "").strip()
                    else:
                        text = (el.inner_text() or "").strip()
                    a = _first_hit(el, sel.get("comment_author", ""))
                    if a:
                        author = (a.first.inner_text() or "").strip()
                    tm = _first_hit(el, sel.get("comment_time", ""))
                    if tm:
                        ctime = (tm.first.inner_text() or "").strip()
                    key = (author, text[:80])
                    if text and key not in seen_texts:
                        seen_texts.add(key)
                        comments.append({"author": author, "text": text,
                                         "time": ctime})
                        if len(comments) >= max_comments:
                            return comments
                except Exception:
                    continue
        # 点“展开更多” / 滚评论区
        more = _first_hit(page, sel.get("load_more", ""), timeout=1500)
        if more:
            try:
                more.first.click(timeout=3000)
                page.wait_for_timeout(1500)
                continue
            except Exception:
                pass
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(1200)
    return comments


def selector_selftest(page, sel):
    """评论区选择器自检：返回 {名称: 是否命中}。"""
    result = {}
    for name in ("note_link", "comment_item", "comment_author",
                 "comment_text"):
        result[name] = _first_hit(page, sel.get(name, ""),
                                  timeout=2000) is not None
    return result
