# -*- coding: utf-8 -*-
"""浏览器 profile 管理：每个账号一个独立的 Playwright 持久化上下文，
绑定专属代理（美国住宅 IP），扫码登录后会话按账号隔离落盘。
说明：这是 profile 级隔离（独立 Cookie/存储/代理），非完整反指纹伪装。"""
import os
from urllib.parse import urlsplit

from .config import DATA_DIR, PROFILES_DIR, load_selectors, PAGE_GOTO_TIMEOUT
LOGIN_POLL_MS = 2000

# 与轮转引擎 headless 上下文一致的桌面 UA（环境一致性：登录态与监控态同指纹基线）
DESKTOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")


def get_profile_dir(account_id):
    d = os.path.join(PROFILES_DIR, f"account_{account_id}")
    os.makedirs(d, exist_ok=True)
    return d


def build_proxy(account):
    """由账号代理配置生成 Playwright proxy 参数；未配置返回 None。"""
    host = (account.get("proxy_host") or "").strip()
    if not host:
        return None
    scheme = (account.get("proxy_scheme") or "http").strip() or "http"
    port = account.get("proxy_port") or 0
    server = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
    proxy = {"server": server}
    if account.get("proxy_user"):
        proxy["username"] = account["proxy_user"]
    if account.get("proxy_pass"):
        proxy["password"] = account["proxy_pass"]
    return proxy


def launch_account(pw, account, headless=True):
    """启动该账号的持久化浏览器上下文。返回 (context, profile_dir)。

    pw 为 sync_playwright() 上下文对象。
    headless 默认 True：后台任务（发布/扫描/监控）不弹窗口省内存；
    只有用户点「启动浏览器」扫码时才用 headless=False。
    """
    profile_dir = get_profile_dir(account["id"])
    proxy = build_proxy(account)
    args = ["--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            # WebRTC 强制走代理，防真实 IP 泄露
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
    if headless:
        # 后台省内存：禁 GPU/扩展/后台节流相关
        args += ["--disable-gpu", "--disable-extensions",
                 "--disable-background-networking",
                 "--disable-sync"]
    kwargs = {
        "user_data_dir": profile_dir,
        "headless": headless,
        "locale": "zh-CN",
        "timezone_id": "America/New_York",  # 配合美国 IP 的属地一致性
        "user_agent": DESKTOP_UA,
        "args": args,
    }
    # Visible windows must use their real content size; a fixed 900px
    # viewport clips the publish footer on smaller laptop displays.
    if headless:
        kwargs["viewport"] = {"width": 1366, "height": 900}
    else:
        kwargs["no_viewport"] = True
    if proxy:
        kwargs["proxy"] = proxy
    if not headless:
        from .browser_plugin_bridge import extension_launch_options
        extension_options = extension_launch_options()
        args.extend(extension_options.pop('args'))
        kwargs.update(extension_options)
    context = pw.chromium.launch_persistent_context(**kwargs)
    from .native_session import apply_state
    apply_state(context, DATA_DIR, "xiaohongshu", account["id"])
    return context, profile_dir


def get_egress_ip(page, timeout=15000):
    """通过浏览器当前上下文查出口 IP（走账号代理）。
    返回 (ip, 国家/地区) 或 ('', '')。"""
    for url in ("https://api.ipify.org?format=json",
                "https://ifconfig.me/ip"):
        try:
            resp = page.request.get(url, timeout=timeout)
            if resp.ok:
                body = resp.text().strip()
                if "format=json" in url:
                    import json as _json
                    ip = _json.loads(body).get("ip", "")
                else:
                    ip = body.split()[0]
                # 简单校验 IPv4/IPv6
                if "." in ip or ":" in ip:
                    return ip, ""
        except Exception:
            continue
    return "", ""


def open_areacode_menu(page):
    """普通点击官方区号控件，仅在可见菜单出现后报告成功。"""
    from .comment_selectors import PLATFORM_HOSTS
    try:
        parsed = urlsplit(page.url)
        trusted = (parsed.scheme == "https" and not parsed.username
                   and not parsed.password and parsed.port in (None, 443)
                   and parsed.hostname in PLATFORM_HOSTS["xiaohongshu"])
    except ValueError:
        trusted = False
    if not trusted:
        return {"ok": False, "status": "unsupported_page",
                "error": "请先打开小红书或 REDnote 的官方登录页"}

    menu = page.locator('input[placeholder="搜索国家/地区"]:visible')
    try:
        if menu.is_visible():
            return {"ok": True, "status": "opened",
                    "msg": "区号列表已打开，请在浏览器中选择国家/地区"}
        controls = page.locator('.country-code-select, .country-code')
        for control in controls.all():
            if control.is_visible():
                control.click(timeout=3000)
                break
        else:
            return {"ok": False, "status": "not_found",
                    "error": "未找到区号按钮，请先打开手机号登录窗口"}
        # 国内站可能只显示不支持切换的提示，不能把点击本身当成成功。
        try:
            menu.wait_for(state="visible", timeout=1500)
        except Exception:
            pass
        if menu.is_visible():
            return {"ok": True, "status": "opened",
                    "msg": "区号列表已打开，请在浏览器中选择国家/地区"}
        if "暂不支持切换地区" in page.locator('body').inner_text():
            return {"ok": False, "status": "unsupported",
                    "error": "当前国内网页版提示暂不支持切换地区；"
                             "海外手机号请使用 https://www.rednote.com/ 的登录入口"}
        return {"ok": False, "status": "not_opened",
                "error": "区号列表未打开，请在浏览器中检查页面提示"}
    except Exception:
        return {"ok": False, "status": "click_failed",
                "error": "无法打开区号列表，请在浏览器中手动检查登录窗口"}


def force_click_areacode(page):
    """兼容旧调用名；不再强制点击，返回是否真实打开菜单。"""
    return open_areacode_menu(page)["ok"]


def is_logged_in(page, selectors=None):
    """用选择器判断页面是否处于登录态。"""
    selectors = selectors or load_selectors()["selectors"]
    try:
        page.wait_for_selector(selectors["login_avatar"], timeout=4000)
        return True
    except Exception:
        return False


def ensure_login(context, xhs_home=None, wait_timeout=180000):
    """打开小红书首页；未登录则等待用户扫码，返回 True/False(超时)。
    调用方负责把浏览器展示给用户扫码。"""
    selectors = load_selectors()
    page = context.pages[0] if context.pages else context.new_page()
    try:
        # domcontentloaded 即可：小红书是重型 SPA，等 load 经常超时
        page.goto(xhs_home or selectors["xhs_home"],
                  timeout=PAGE_GOTO_TIMEOUT,
                  wait_until="domcontentloaded")
    except Exception as e:
        raise RuntimeError(
            "小红书首页 30 秒打不开：检查该账号的代理是否可用，"
            "或本机网络能否正常访问 xiaohongshu.com"
            f"（{str(e)[:80]}）")
    if is_logged_in(page):
        return True
    # 等待用户扫码登录
    try:
        page.wait_for_selector(selectors["selectors"]["login_avatar"],
                               timeout=wait_timeout)
        return True
    except Exception:
        return False


def storage_state_path(account_id):
    """监控引擎用的会话凭证（storage_state JSON）。扫码登录后从持久化上下文导出。"""
    d = os.path.join(DATA_DIR, "storage_state")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"account_{account_id}.json")


def export_storage_state(context, account_id):
    """把已登录上下文的 Cookie/存储导出为监控凭证。返回文件路径。"""
    path = storage_state_path(account_id)
    context.storage_state(path=path)
    return path


def has_storage_state(account_id):
    return os.path.exists(storage_state_path(account_id))
