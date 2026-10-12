# -*- coding: utf-8 -*-
"""账号浏览器会话：每个账号独占一个浏览器线程（Actor 模型）。

背景：Playwright 的 sync API 对象与其创建线程绑定，跨线程使用
page/context 会出错。之前 sessions/chat_pages 在工作线程创建、
却在 pywebview 回调线程使用，是真实的线程归属 Bug。

本模块保证：一个账号的所有 Playwright 调用（创建 ctx/page、页面
操作、关闭）永远发生在该账号的专属线程内；外部通过 call(fn)
提交任务并阻塞等待结果。fn 只做页面操作，绝不弹窗/阻塞等待
外部线程（否则死锁）。
"""
import concurrent.futures
import queue
import threading
import time

from .config import PAGE_GOTO_TIMEOUT
from .log import log_event


def _is_dead_error(exc):
    """判断异常是否表示浏览器/上下文已死（需要重建 Actor）。"""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return ("has been closed" in msg or "target closed" in msg
            or "browser closed" in msg or "context closed" in msg)


class AccountBrowser(threading.Thread):
    """单个账号的独占浏览器线程。"""

    def __init__(self, aid, account):
        super().__init__(daemon=True, name=f"ab-{aid}")
        self.aid = aid
        self.account = dict(account)
        self._tasks = queue.Queue()
        self._ready = threading.Event()
        self._start_error = None
        self._dead = False  # 浏览器已死：下次 _ensure_actor 会重建
        # 心跳：10 账号 48h 测试时可判断哪个 Actor 长时间无推进
        self.last_active_at = time.time()
        self.current_task = None
        self.current_stage = None
        self.ctx = None
        self.profile_dir = None
        self.chat_page = None

    def run(self):
        from playwright.sync_api import sync_playwright
        from . import profiles as prof
        try:
            with sync_playwright() as pw:
                ctx, profile_dir = prof.launch_account(pw, self.account,
                                            headless=False)
                self.ctx = ctx
                self.profile_dir = profile_dir
                self._ready.set()
                while True:
                    item = self._tasks.get()
                    if item is None:  # 关闭信号
                        break
                    fn, fut = item
                    if fut.cancelled():
                        continue
                    try:
                        fut.set_result(fn(self))
                    except Exception as e:
                        fut.set_exception(e)
        except Exception as e:
            self._start_error = e
            self._ready.set()
        finally:
            try:
                if self.ctx is not None:
                    self.ctx.close()
            except Exception:
                pass
            self.ctx = None
            self.chat_page = None

    def call(self, fn, timeout=180, task=None, stage=None):
        """在浏览器线程内执行 fn(self)，阻塞等待结果并返回/抛异常。

        task/stage 用于心跳记录；浏览器死亡时记 browser_dead 日志。
        """
        if self._start_error is not None:
            raise RuntimeError(f"浏览器启动失败: {self._start_error}")
        if not self.is_alive():
            # 线程已退出或从未启动：直接失败，不空等 90 秒
            raise RuntimeError("浏览器线程未启动或已退出")
        if not self._ready.wait(timeout=90):
            raise RuntimeError("浏览器线程启动超时")
        # _ready 置位后复查：浏览器可能恰在等待期间启动失败，
        # 此时线程已退出，不复查就会把任务丢进无人队列空等 timeout
        if self._start_error is not None:
            raise RuntimeError(f"浏览器启动失败: {self._start_error}")
        if not self.is_alive():
            raise RuntimeError("浏览器线程已退出")
        self.current_task = task or "call"
        self.current_stage = stage
        self.last_active_at = time.time()
        fut = concurrent.futures.Future()
        self._tasks.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except Exception as e:
            if _is_dead_error(e):
                # 浏览器/上下文已死：标记，下次调用方重建 Actor
                self._dead = True
                log_event(task=self.current_task, aid=self.aid,
                          stage=self.current_stage, status="browser_dead",
                          error=e)
            raise
        finally:
            self.last_active_at = time.time()
            self.current_task = None
            self.current_stage = None

    def heartbeat(self):
        """心跳快照：供状态页/日志判断 Actor 是否卡死。"""
        return {"aid": self.aid,
                "alive": self.is_alive() and not self._dead,
                "current_task": self.current_task,
                "current_stage": self.current_stage,
                "idle_sec": round(time.time() - self.last_active_at, 1)}

    def is_dead(self):
        """浏览器是否已死（需要重建）。"""
        return self._dead or not self.is_alive()

    def stop(self):
        """关闭浏览器并退出线程（幂等）。"""
        try:
            self._tasks.put(None)
        except Exception:
            pass
        self.join(timeout=15)

    # ---- 以下运行在浏览器线程内 ----
    def ensure_chat_page(self, chat_url, wait_ms=2500):
        """取复用的私信 page，没有则新建并打开私信页。"""
        if self.chat_page is None or self.chat_page.is_closed():
            self.chat_page = self.ctx.new_page()
        self.chat_page.goto(chat_url, timeout=PAGE_GOTO_TIMEOUT)
        self.chat_page.wait_for_timeout(wait_ms)
        return self.chat_page

    def close_chat_page(self):
        if self.chat_page is not None:
            try:
                self.chat_page.close()
            except Exception:
                pass
            self.chat_page = None
