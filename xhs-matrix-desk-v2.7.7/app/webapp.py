"""桌面入口：组合平台 API，管理窗口和共享运行状态。"""
import os
import threading
from datetime import datetime
from . import account_hub
from . import dy_workspace as douyin
from . import leads as leads_mod
from . import phrases as phr
from .log import log_event
from .config import APP_NAME, APP_VERSION, DATA_DIR, DB_PATH, SELECTORS_PATH, ensure_dirs, find_cjk_font
from .db import get_conn, init_db
from .cluster import ClusterState
from .xhs_api import XhsAPI
from .douyin_api import DouyinAPI
from .experimental_api import ExperimentalAPI
from .batch_api import BatchAPI
from .browse_api import BrowseAPI
from .metrics_api import MetricsAPI
from .native_browser_api import NativeBrowserAPI

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

class PromptManager:
    """工作线程 -> 前端的弹窗桥接。ask 会阻塞工作线程直到前端作答。

    用 FIFO 队列承载并发弹窗：两个线程同时 ask 时不再互相覆盖，
    前端按顺序逐个作答，先问的先被看到。
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = []
        self._counter = 0

    def ask(self, kind, title, msg, options):
        with self.lock:
            self._counter += 1
            ev = threading.Event()
            p = {"id": self._counter, "kind": kind, "title": title,
                 "msg": msg, "options": list(options),
                 "event": ev, "answer": None}
            self.pending.append(p)
        ev.wait()
        with self.lock:
            ans = p["answer"]
            if p in self.pending:
                self.pending.remove(p)
        return ans

    def poll(self):
        with self.lock:
            if not self.pending:
                return None
            c = self.pending[0]
            return {"id": c["id"], "kind": c["kind"], "title": c["title"],
                    "msg": c["msg"], "options": c["options"]}

    def answer(self, pid, answer):
        with self.lock:
            for c in self.pending:
                if c["id"] == int(pid):
                    c["answer"] = answer
                    c["event"].set()
                    return True
        return False


class Api(XhsAPI, DouyinAPI, BatchAPI, BrowseAPI, MetricsAPI, ExperimentalAPI, NativeBrowserAPI):
    def __init__(self, db_path=None, enable_extras=False):
        self.enable_extras = bool(enable_extras)
        self.db_path = db_path or DB_PATH
        ensure_dirs()
        initial_conn = init_db(self.db_path)
        douyin.ensure(initial_conn)
        account_hub.ensure(initial_conn)
        if self.enable_extras:
            from . import materials
            materials.ensure(initial_conn)
        initial_conn.close()
        self._dy_browsers = douyin.BrowserPool()
        self._channels_browsers = douyin.BrowserPool("channels")
        # 线程本地连接：每个线程首次访问时建自己的连接。
        # 之前是单连接跨线程共享，sqlite 默认 check_same_thread 会抛
        # ProgrammingError（轮转引擎线程已命中）。WAL 模式保证跨连接可见性。
        self._conn_local = threading.local()
        leads_mod.ensure_default_keywords(self.conn)
        phr.seed_default_phrases(self.conn)
        self.pm = PromptManager()
        # 账号浏览器 Actor：{aid: AccountBrowser}，所有 Playwright 调用
        # 都发生在各自账号的专属线程内，外部只通过 actor.call 提交任务。
        self._actors = {}
        self._actors_lock = threading.Lock()
        self._message_open_state = {}
        # Reject competing work for one XHS actor before it enters its queue.
        # A timed-out running callback retains its reservation until it exits.
        self._browser_jobs = {}
        self._browser_jobs_lock = threading.Lock()
        self.log_buf = []
        self.log_lock = threading.Lock()
        self._reminder_started = False
        self._scan = {"running": False, "accounts_done": 0,
                      "accounts_total": 0, "convs_done": 0,
                      "convs_total": 0, "current": "", "found": 0}
        self._scan_lock = threading.Lock()
        # 队列进度读-改-写串行化（防多 worker 同时标记同一队列丢更新）
        self._queue_lock = threading.RLock()
        self._publish_state_lock = threading.Lock()
        self._publish_busy = False
        # 分布式集群状态（主控模式用）
        self.cluster = ClusterState()
        self._send_times = {}
        self._last_sent = {}
        self._monitor_slots = 6
        self._engine = None
        self._warmup = None
        from .batch_collect import BatchCollector
        self._batch = BatchCollector(self.db_path, self._run_batch_source)
        from .warmup import WarmupService
        self._warmup = WarmupService(self.db_path, self._run_browse_target)
        self.metrics_setup()

    @property
    def conn(self):
        """当前线程的数据库连接（线程本地，WAL 模式跨线程可见）。"""
        c = getattr(self._conn_local, "c", None)
        if c is None:
            c = get_conn(self.db_path)
            self._conn_local.c = c
        return c

    def close_thread_conn(self):
        """关闭当前线程的数据库连接。

        短命工作线程退出前调用，防止线程本地连接泄漏 fd。
        长驻线程（sweep 分片、reminder、Actor）不需要调。
        """
        c = getattr(self._conn_local, "c", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            try:
                del self._conn_local.c
            except AttributeError:
                pass

    def _ok(self, **kw):
        return {"ok": True, **kw}

    def _err(self, e):
        return {"ok": False, "error": str(e)}

    def _log(self, msg):
        with self.log_lock:
            self.log_buf.append(
                {"t": datetime.now().strftime("%H:%M:%S"), "msg": msg})
        log_event(msg=msg)

    def app_info(self):
        return self._ok(version=APP_VERSION, data_dir=DATA_DIR,
                        font=find_cjk_font() or "未找到",
                        selectors_path=SELECTORS_PATH,
                        features={'extras': self.enable_extras, 'warmup': True, 'android': False})

    def prompt_poll(self):
        return self._ok(prompt=self.pm.poll())

    def open_phone_desk(self):
        """Launch the separate device app only on an explicit UI request."""
        import subprocess
        import sys
        from pathlib import Path
        with self._actors_lock:
            process = getattr(self, '_phone_desk_process', None)
            if process is not None and process.poll() is None:
                return self._ok(already_running=True)
            folder = Path(__file__).resolve().parents[2] / 'android-device-desk'
            if not (folder / 'run.py').is_file():
                return self._err('未找到独立手机工作台，请保留 android-device-desk 目录')
            log_path = folder / 'device-startup.log'
            with log_path.open('ab') as log:
                self._phone_desk_process = subprocess.Popen(
                    [sys.executable, str(folder / 'run.py')], cwd=folder,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
            return self._ok(already_running=False)

    def prompt_answer(self, pid, answer):
        return self._ok(done=self.pm.answer(pid, answer))


def main(db_path=None, api=None):
    import webview
    api = api or Api(db_path=db_path)
    api.start_reminder()
    try:
        r = api.monitor_start_all()
        if r.get("started"):
            print(f"已恢复 {r['count']} 个账号的私信监控")
    except Exception as e:
        print("恢复监控失败:", e)
    window = webview.create_window(APP_NAME, os.path.join(UI_DIR, "index.html"),
                          width=1240, height=820, js_api=api)
    from .native_browser import NativeBrowser, available
    if available():
        api._native_browser = NativeBrowser(window, DATA_DIR, api._native_import)
    def save_manual_sessions():
        manager = getattr(api, "_native_browser", None)
        if manager is not None:
            try: manager.hide(manager.epoch + 1)
            except Exception: pass
    window.events.closing += save_manual_sessions
    try:
        webview.start(debug=False)
    finally:
        api.metrics_close()
        api._batch.close()
        if api._warmup is not None: api._warmup.close()
        api.browsers_close()
        for pool in (api._dy_browsers, api._channels_browsers):
            if hasattr(pool, 'close_all'): pool.close_all()
        api.close_thread_conn()

