from .config import PAGE_GOTO_TIMEOUT
from .log import log_event, task_span
# -*- coding: utf-8 -*-
"""全账号私信监控引擎（v2.0 轮转架构）。

目标：100 个账号的私信全覆盖监控，在一台 Mac 上跑得动。
做法：不给每个账号开一个浏览器进程，而是 K 个分片工作线程，
每个线程独占一个 headless 浏览器，分片轮转账号：
  打开账号上下文（storage_state 凭证 + 专属代理）→ 读会话列表 → 关闭上下文 → 下一个。
100 账号 / 6 槽位 ≈ 每 3~5 分钟每个账号被查一次；内存占用约 1.5~2.5GB。

风控：
- 同一账号同时有手动（可见）浏览器在用时，本轮跳过，避免双会话冲突；
- 账号间随机抖动 2~6 秒 + 分片错峰；
- 只读；登录失效只标记状态。
"""
import os
import random
import threading
import time
from datetime import datetime

from .messages import CHAT_URL
from .profiles import DESKTOP_UA


def parse_unread(s):
    """'2'->2, '99+'->99, ''->0。"""
    digits = ""
    for ch in (s or "").strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else 0


def default_launch_browser():
    """每分片一个 headless 浏览器（调用线程独占，线程安全）。"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            # WebRTC 强制走代理，防真实 IP 泄露（全账号隔离）
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        ])
    except Exception:
        # launch 失败必须 stop，否则 dispatcher greenlet 残留运行中的
        # loop，导致本线程后续所有 sync_playwright() 都报
        # "inside the asyncio loop"（假报错，真原因是第一次没清理）
        try:
            pw.stop()
        except Exception:
            pass
        raise

    class _B:
        def new_context(self, **kw):
            return browser.new_context(**kw)

        def close(self):
            try:
                browser.close()
            finally:
                pw.stop()

    return _B()


class SweepMonitor:
    def __init__(self, *, slots, get_cred, fetch_convs, launch_browser=None,
                 lead_hook=None, retry_cooldown=60):
        """
        get_cred(aid) -> {"storage_state": path|None, "proxy": dict|None,
                          "skip": "原因"|None}
        fetch_convs(aid, page) -> 会话列表；page 已打开私信页；异常上抛。
        launch_browser() -> 有 new_context(**kw)/close() 的浏览器对象。
        lead_hook(aid, convs) -> 线索识别钩子（可选，异常内部吞掉）。
        """
        self._slots = max(1, int(slots))
        self._get_cred = get_cred
        self._fetch_convs = fetch_convs
        self._launch_browser = launch_browser or default_launch_browser
        self._lead_hook = lead_hook
        self._retry_cooldown = max(0, retry_cooldown)
        self._lock = threading.Lock()
        self.enabled = set()
        self.nicknames = {}
        self.status = {}
        self.baselines = {}
        self.events = []
        self._err_streak = {}
        self._shard_times = {}
        self._stop = threading.Event()
        self._threads = []

    # ---- 控制 ----
    def add(self, aid, nickname=""):
        with self._lock:
            self.enabled.add(aid)
            if nickname:
                self.nicknames[aid] = nickname
            self.status.setdefault(aid, {"state": "waiting", "unread": 0,
                                         "last_check": "", "error": ""})
        self.start()

    def remove(self, aid):
        with self._lock:
            self.enabled.discard(aid)
            self.status.pop(aid, None)
            self.baselines.pop(aid, None)

    def clear(self):
        with self._lock:
            self.enabled.clear()
            self.status.clear()
            self.baselines.clear()

    def start(self):
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return
            self._stop.clear()
            self._threads = [
                threading.Thread(target=self._shard_loop, args=(i,),
                                 daemon=True, name=f"sweep-{i}")
                for i in range(self._slots)]
            for t in self._threads:
                t.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            ths = list(self._threads)
            self._threads = []
        for t in ths:
            t.join(timeout=5)

    @property
    def running(self):
        return any(t.is_alive() for t in self._threads)

    # ---- 分片轮转 ----
    def _shard_loop(self, idx):
        # 分片自愈：浏览器起不来也不永久死亡，冷却 60s 后重试，
        # 代理/网络恢复后自动回到轮转（Go/No-Go：shard 不得无故永久停止）。
        while not self._stop.is_set():
            browser = None
            try:
                browser = self._launch_browser()
                while not self._stop.is_set():
                    with self._lock:
                        aids = sorted(self.enabled)
                    shard = [a for i, a in enumerate(aids)
                             if i % self._slots == idx]
                    if not shard:
                        if self._stop.wait(5):
                            break
                        continue
                    t0 = time.time()
                    for aid in shard:
                        if self._stop.is_set():
                            break
                        self._check_account(browser, aid, shard_idx=idx)
                    with self._lock:
                        self._shard_times[idx] = round(time.time() - t0, 1)
            except Exception as e:  # 浏览器起不来：标记本分片账号异常
                log_event(task="sweep", stage="shard", status="failed",
                          error=e, msg=f"shard={idx} 浏览器启动失败，60s 后重试")
                with self._lock:
                    for i, a in enumerate(sorted(self.enabled)):
                        if i % self._slots == idx and a in self.status:
                            self.status[a]["state"] = "error"
                            self.status[a]["error"] = f"浏览器启动失败: {e}"[:60]
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
            if not self._stop.is_set():
                # 冷却后重试；stop() 可立即唤醒
                self._stop.wait(self._retry_cooldown)

    def _check_account(self, browser, aid, shard_idx=0):
        with task_span("sweep", aid, "scan",
                      extra=f"shard={shard_idx}"):
            return self._check_account_inner(browser, aid, shard_idx)

    def _check_account_inner(self, browser, aid, shard_idx=0):
        try:
            cred = self._get_cred(aid)
        except Exception as e:
            self._record(aid, [], f"凭证读取失败: {e}"[:60])
            return
        if cred.get("skip"):
            with self._lock:
                st = self.status.get(aid)
                if st:
                    st["state"] = "paused"
                    st["error"] = cred["skip"][:60]
                    st["last_check"] = datetime.now().strftime("%H:%M:%S")
            return
        ss = cred.get("storage_state")
        if not ss or not os.path.exists(ss):
            self._record(aid, [], "未同步监控凭证，请先扫码登录")
            return
        ctx = None
        try:
            ctx = browser.new_context(
                storage_state=ss, proxy=cred.get("proxy"),
                locale="zh-CN", timezone_id="America/New_York",
                viewport={"width": 1366, "height": 900},
                user_agent=DESKTOP_UA)
            page = ctx.new_page()
            page.goto(CHAT_URL, timeout=PAGE_GOTO_TIMEOUT)
            page.wait_for_timeout(2500)
            convs = self._fetch_convs(aid, page)
            self._record(aid, convs or [], "")
            if self._lead_hook:
                try:
                    self._lead_hook(aid, convs or [])
                except Exception:
                    pass
        except Exception as e:
            self._record(aid, [], str(e)[:60])
        finally:
            try:
                if ctx is not None:
                    ctx.close()
            except Exception:
                pass
        time.sleep(random.uniform(2, 6))

    # ---- 结果记录 ----
    def _record(self, aid, convs, error):
        total = sum(parse_unread(c.get("unread")) for c in convs)
        now_s = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            first = aid not in self.baselines
            prev = self.baselines.get(aid, {})
            cur = {c.get("user_id"): parse_unread(c.get("unread"))
                   for c in convs if c.get("user_id")}
            if not first and not error:
                for uid, n in cur.items():
                    delta = n - prev.get(uid, 0)
                    if delta > 0:
                        nick = next((c.get("nickname", "") for c in convs
                                     if c.get("user_id") == uid), "")
                        self.events.append({
                            "t": now_s, "aid": aid,
                            "nickname": self.nicknames.get(aid, f"账号{aid}"),
                            "text": f"「{nick or '某用户'}」发来 {delta} 条新私信"})
            self.baselines[aid] = cur
            if error:
                self._err_streak[aid] = self._err_streak.get(aid, 0) + 1
            else:
                self._err_streak[aid] = 0
            err_text = error
            if error and self._err_streak.get(aid, 0) >= 5:
                err_text = (f"连续{self._err_streak[aid]}次异常，"
                            f"请检查代理/网络/登录态：{error}")
            self.status[aid] = {"state": "error" if error else "running",
                                "unread": total, "last_check": now_s,
                                "error": err_text}

    # ---- 查询 ----
    def get_status(self):
        with self._lock:
            sweep_sec = max(self._shard_times.values()) if self._shard_times else 0
            return {
                "running": self.running,
                "slots": self._slots,
                "total": len(self.enabled),
                "sweep_sec": sweep_sec,
                "accounts": {str(aid): dict(st)
                             for aid, st in self.status.items()},
                "nicknames": {str(aid): n
                              for aid, n in self.nicknames.items()},
            }

    def get_events(self, cursor=0):
        with self._lock:
            items = self.events[int(cursor):]
            return {"items": items, "cursor": len(self.events)}
