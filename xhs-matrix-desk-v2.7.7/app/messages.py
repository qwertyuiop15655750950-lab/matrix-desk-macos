from .config import PAGE_GOTO_TIMEOUT
# -*- coding: utf-8 -*-
"""私信消息中心：基于账号已挂载的真实浏览器读写 www.xiaohongshu.com/chat。

选择器与发送流程参考 MIT 开源项目 adyingfish/xiaohongshu-skills 的私信模块。
本软件的风控策略（比直接调用更保守）：
- 监控只读、按需轮询（仅当用户打开消息页并手动开启后），不做 7x24 后台全账号轮询；
- 发送必须用户逐条确认，不做自动回复 / 关键词回复 / 批量群发；
- 防重：同一会话不允许连续发送相同正文；
- 频率限制由调用方（webapp.Api）执行。
"""
import re
import time
from urllib.parse import urlsplit

CHAT_URL = "https://www.rednote.com/chat"
MAX_TEXT_LEN = 1000  # 网页按 UTF-16 length 计长度

USER_ID_RE = re.compile(r"[0-9a-fA-F]{24}$")

DEFAULT_SELECTORS = {
    "login_flag": ".login-container",
    "conv_item": '.xhs-im-conv-item[data-conv-kind="c2c"]',
    "conv_id_attr": "data-conv-id",
    "conv_name": ".xhs-im-conv-item__name",
    "conv_preview": ".xhs-im-conv-item__summary-text",
    "conv_time": ".xhs-im-conv-item__time",
    "conv_unread": ".xhs-im-conv-item__bottom-right",
    "chat_window": ".xhs-im-chat-window",
    "msg_item": ".xhs-im-msg-list .chat-item",
    "bubble": ".chat-item__bubble",
    "bubble_text": ".xhs-im-bubble__text",
    "bubble_me": ".chat-item__bubble--me",
    "bubble_other": ".chat-item__bubble--other",
    "editor": ".xhs-im-chat-window .xhs-im-input-bar-editor",
    "send_btn": ".xhs-im-input-bar-send",
}

_JS_CONVS = """(sel) => {
  const t = el => (el && el.textContent || '').trim();
  return [...document.querySelectorAll(sel.conv_item)].map(el => ({
    user_id: el.getAttribute(sel.conv_id_attr) || '',
    nickname: t(el.querySelector(sel.conv_name)),
    preview: t(el.querySelector(sel.conv_preview)),
    time_label: t(el.querySelector(sel.conv_time)),
    unread: t(el.querySelector(sel.conv_unread)),
  })).filter(c => c.user_id || c.nickname);
}"""

_JS_THREAD = """(sel) => {
  const t = el => (el && el.textContent || '').trim();
  return [...document.querySelectorAll(sel.msg_item)].map(el => {
    const b = el.querySelector(sel.bubble);
    const dir = el.querySelector(sel.bubble_me) ? 'outgoing'
      : (el.querySelector(sel.bubble_other) ? 'incoming' : 'unknown');
    return { direction: dir, text: t(b && b.querySelector(sel.bubble_text)),
             has_img: !!(b && b.querySelector('img')) };
  }).filter(m => m.text || m.has_img);
}"""

_JS_FILL = """(args) => {
  const sel = args.sel, content = args.content;
  const ed = document.querySelector(sel.editor);
  if (!ed || ed.getAttribute('contenteditable') !== 'true') return {error: '输入框不可用'};
  ed.focus();
  ed.textContent = content;
  ed.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText'}));
  return {filled: true, echo: ed.textContent};
}"""

_JS_SEND = """(args) => {
  const sel = args.sel, content = args.content;
  const ed = document.querySelector(sel.editor);
  if (!ed) return {error: '输入框不可用'};
  if ((ed.textContent || '') !== content) return {error: '发送前正文发生变化，已停止'};
  const items = [...document.querySelectorAll(sel.msg_item)]
    .filter(el => el.querySelector(sel.bubble_me));
  const last = items.length ? (items[items.length-1].querySelector(sel.bubble_text)||{}).textContent : '';
  if ((last || '').trim() === content.trim()) return {error: '检测到重复私信，已停止'};
  ed.focus();
  ed.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter',
    keyCode: 13, which: 13, bubbles: true, cancelable: true, shiftKey: false}));
  return {submitted: true};
}"""


def utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


def require_chat_page(page):
    """首页和 404 页不能被当成空的消息列表。"""
    from .comment_selectors import PLATFORM_HOSTS
    try:
        parsed = urlsplit(page.url)
        valid_origin = (parsed.scheme == "https" and not parsed.username
                        and not parsed.password and parsed.port in (None, 443)
                        and parsed.hostname in PLATFORM_HOSTS["xiaohongshu"])
        chat_path = parsed.path == "/chat" or parsed.path.startswith("/chat/")
    except ValueError:
        valid_origin = chat_path = False
    if not valid_origin or not chat_path:
        raise RuntimeError("未进入可用的网页私信页面（可能跳转首页或 404）；"
                           "请人工确认该账号的消息入口，不能据此判断没有新消息")


def is_logged_in(page, sel):
    """检查已进入消息页后的登录态；入口失效时明确报错。"""
    require_chat_page(page)
    try:
        return page.evaluate(
            "(s) => !document.querySelector(s)", sel["login_flag"]) is True
    except Exception:
        return False


def conversation_list(page, sel):
    """返回 [{user_id, nickname, preview, time_label, unread}]。"""
    require_chat_page(page)
    try:
        items = page.evaluate(_JS_CONVS, sel)
    except Exception as error:
        raise RuntimeError('会话读取失败，请检查消息页面后重试') from error
    if not isinstance(items, list):
        raise RuntimeError('会话数据格式异常，不能判断没有消息')
    if not items and page.evaluate('(sel) => !!(document.querySelector(sel.conv_item) || document.querySelector(sel.chat_window))', sel) is not True:
        raise RuntimeError('没有识别到网页消息界面，不能判断没有消息；请使用平台 App 检查')
    return [c for c in items if isinstance(c, dict)]


def read_thread(page, sel):
    """读取当前打开会话的消息 [{direction, text, has_img}]，旧→新。"""
    require_chat_page(page)
    try:
        items = page.evaluate(_JS_THREAD, sel)
    except Exception as error:
        raise RuntimeError('对话读取失败，请检查消息页面后重试') from error
    if not isinstance(items, list):
        raise RuntimeError('对话数据格式异常')
    return [m for m in items if isinstance(m, dict)]


def open_conversation(page, user_id):
    page.goto(f"{CHAT_URL}/{user_id}", timeout=PAGE_GOTO_TIMEOUT)
    page.wait_for_timeout(1500)


def fill_reply(page, sel, content):
    """仅填写不发送。返回 (ok, echo_or_error)。"""
    if not content.strip():
        return False, "正文为空"
    if utf16_len(content) > MAX_TEXT_LEN:
        return False, f"正文超过 {MAX_TEXT_LEN} 字限制"
    require_chat_page(page)
    from .page_guard import check_page
    check_page(page)
    try:
        r = page.evaluate(_JS_FILL, {"sel": sel, "content": content})
    except Exception as e:
        return False, f"填写失败: {e}"
    if r.get("error"):
        return False, r["error"]
    return True, r.get("echo", "")


def send_current(page, sel, content, timeout=8):
    """对已填写好的输入框按 Enter 发送，并核验新消息出现。"""
    require_chat_page(page)
    from .page_guard import check_page
    check_page(page)
    try:
        r = page.evaluate(_JS_SEND, {"sel": sel, "content": content})
    except Exception as e:
        return False, f"发送失败: {e}"
    if r.get("error"):
        return False, r["error"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(800)
        msgs = read_thread(page, sel)
        outs = [m for m in msgs if m["direction"] == "outgoing" and m["text"]]
        if outs and outs[-1]["text"].strip() == content.strip():
            return True, "已发送并核验"
    return False, "已提交但未核验到新消息，请在浏览器中人工确认"


def selector_selftest(page, sel):
    """消息页选择器自检，返回 {key: bool}。"""
    out = {}
    for key, css in sel.items():
        if key in ("conv_id_attr", "login_flag"):
            continue
        try:
            out[key] = page.evaluate(
                "(s) => document.querySelector(s) !== null", css) is True
        except Exception:
            out[key] = False
    try:
        out["login_ok"] = is_logged_in(page, sel)
    except Exception:
        out["login_ok"] = False
    return out
