# -*- coding: utf-8 -*-
"""机场订阅导入：解析常见代理订阅格式，提取可直接用的代理。

支持：
1. 纯文本：每行 IP:端口 或 IP:端口:用户:密码
2. Base64 订阅：解码后按纯文本处理（很多机场用这种）
3. Clash YAML：解析 proxies 段（只取 type 为 http/socks5 的）

注意：ss/vmess/trojan 等协议 Playwright 不能直连，需要本地客户端
转成 socks5/http。本模块只提取 http/socks5 类型的。
"""
import base64
import re
import urllib.request


def _fetch(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": "ClashforWindows/0.20.39"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _try_b64(data):
    """尝试 base64 解码，成功返回文本，失败返回 None。"""
    try:
        text = data.decode("utf-8", errors="ignore").strip()
        # base64 特征：长度%4==0，只含合法字符
        if len(text) % 4 != 0 or not re.fullmatch(r"[A-Za-z0-9+/=_\-\s]+",
                                                  text):
            return None
        padded = text + "=" * (-len(text) % 4)
        raw = base64.b64decode(padded, validate=False)
        out = raw.decode("utf-8", errors="ignore")
        if "://" in out or ":" in out:
            return out
    except Exception:
        pass
    return None


def _parse_plain(text):
    """解析纯文本代理列表。返回 [(host, port, user, pass)]。"""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 跳过 ss/vmess/trojan 链接
        if "://" in line:
            continue
        parts = line.split(":")
        if len(parts) == 2 and parts[1].isdigit():
            out.append((parts[0], parts[1], "", ""))
        elif len(parts) == 4 and parts[1].isdigit():
            out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def _parse_clash_yaml(text):
    """极简 Clash YAML 解析：只提 proxies 段的 http/socks5 节点。"""
    out = []
    in_proxies = False
    cur = {}
    for line in text.splitlines():
        if re.match(r"^proxies\s*:", line):
            in_proxies = True
            continue
        if in_proxies and re.match(r"^\S", line) and not line.startswith(" "):
            in_proxies = False
        if not in_proxies:
            continue
        m = re.match(r"^\s*-\s*\{?(.*)", line)
        if m and "name" in line:
            # 单行式 - {name: ..., type: ..., server: ..., port: ...}
            d = dict(re.findall(r"(\w+)\s*:\s*([^,}]+)", line))
            t = (d.get("type") or "").strip().lower()
            if t in ("http", "https", "socks5", "socks"):
                out.append((d.get("server", "").strip(),
                            d.get("port", "").strip(),
                            d.get("username", "").strip(),
                            d.get("password", "").strip()))
            cur = {}
            continue
        m = re.match(r"^\s{2,}-\s*name\s*:\s*(.+)", line)
        if m:
            cur = {"name": m.group(1).strip()}
            continue
        if cur is not None:
            m = re.match(r"^\s{4,}(\w+)\s*:\s*(.+)", line)
            if m:
                cur[m.group(1)] = m.group(2).strip()
                if "port" in cur and "server" in cur and "type" in cur:
                    t = cur["type"].lower()
                    if t in ("http", "https", "socks5", "socks"):
                        out.append((cur["server"], cur["port"],
                                    cur.get("username", ""),
                                    cur.get("password", "")))
                    cur = None
    # 去重保序
    seen, ret = set(), []
    for p in out:
        if p[0] and (p[0], p[1]) not in seen:
            seen.add((p[0], p[1]))
            ret.append(p)
    return ret


def import_subscription(url):
    """导入机场订阅。

    返回 {"proxies": [(host,port,user,pass)],
            "skipped": 跳过的 ss/vmess 等数量,
            "error": 错误信息或 None}
    """
    try:
        raw = _fetch(url.strip())
    except Exception as e:
        return {"proxies": [], "skipped": 0,
                "error": f"订阅拉取失败: {e.__class__.__name__}: {e}"}
    text = raw.decode("utf-8", errors="ignore")
    skipped = 0
    # 统计跳过的特殊协议
    for proto in ("ss://", "vmess://", "vless://", "trojan://"):
        skipped += text.count(proto)

    proxies = []
    # 1. Clash YAML？
    if "proxies:" in text:
        proxies = _parse_clash_yaml(text)
    else:
        # 2. Base64？
        decoded = _try_b64(raw)
        if decoded:
            text = decoded
            for proto in ("ss://", "vmess://", "vless://", "trojan://"):
                skipped += text.count(proto)
        # 3. 纯文本
        proxies = _parse_plain(text)

    # 去重保序
    seen, ret = set(), []
    for p in proxies:
        if p[0] and (p[0], p[1]) not in seen:
            seen.add((p[0], p[1]))
            ret.append(p)
    return {"proxies": ret, "skipped": skipped, "error": None}


def format_proxy(p, scheme="http"):
    """(host,port,user,pass) → 'IP:端口' 或 'IP:端口:用户:密码'。"""
    host, port, user, pwd = p
    if user:
        return f"{host}:{port}:{user}:{pwd}"
    return f"{host}:{port}"
