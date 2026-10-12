# -*- coding: utf-8 -*-
"""分布式集群：主控 (master) + 工作节点 (worker)。

架构：
    [Master] 1 台：Web UI、中央数据库、任务分发、数据聚合
       ↑↓ HTTP API (REST + 心跳)
    [Worker] N 台：每台 10-15 个账号，跑浏览器自动化

Worker 启动：python run.py --worker --master http://主控IP:端口 --name worker1
Master 启动：python run.py --master (默认就是主控模式)

协议：
- Worker → Master: POST /api/worker/heartbeat {name, accounts, load}
- Worker → Master: GET  /api/worker/tasks?name=xxx → 待执行任务列表
- Worker → Master: POST /api/worker/result {task_id, status, data}
- Master → Worker: 任务通过 tasks 接口下发
"""
import json
import threading
import time
import urllib.request
import urllib.error

HEARTBEAT_INTERVAL = 30  # 秒


# ============ Worker 侧 ============

class WorkerClient:
    """Worker 用的主控客户端：心跳、拉任务、上报结果。"""

    def __init__(self, master_url, name, log_fn=None):
        self.master = master_url.rstrip("/")
        self.name = name
        self.log = log_fn or (lambda s: print(s, flush=True))
        self._stop = threading.Event()
        self._hb_thread = None
        self._task_thread = None
        self.on_task = None  # 回调：on_task(task_dict) -> result_dict

    def _req(self, method, path, data=None, timeout=15):
        url = self.master + path
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            self.log(f"[worker] {method} {path} -> HTTP {e.code}")
        except Exception as e:
            self.log(f"[worker] {method} {path} 失败: {e}")
        return None

    def heartbeat(self, accounts, load):
        """上报心跳：本机账号数、负载。"""
        return self._req("POST", "/api/worker/heartbeat", {
            "name": self.name,
            "accounts": accounts,
            "load": load,
            "ts": time.time(),
        })

    def fetch_tasks(self):
        r = self._req("GET", f"/api/worker/tasks?name={self.name}")
        return (r or {}).get("tasks", [])

    def report(self, task_id, status, data=None):
        return self._req("POST", "/api/worker/result", {
            "name": self.name,
            "task_id": task_id,
            "status": status,
            "data": data or {},
            "ts": time.time(),
        })

    def start(self, get_status_fn):
        """启动心跳 + 任务轮询线程。get_status_fn() -> (账号数, 负载)。"""
        self._hb_thread = threading.Thread(
            target=self._hb_loop, args=(get_status_fn,), daemon=True)
        self._task_thread = threading.Thread(
            target=self._task_loop, daemon=True)
        self._hb_thread.start()
        self._task_thread.start()
        self.log(f"[worker] {self.name} 已连接主控 {self.master}")

    def _hb_loop(self, get_status_fn):
        while not self._stop.is_set():
            try:
                accounts, load = get_status_fn()
                self.heartbeat(accounts, load)
            except Exception as e:
                self.log(f"[worker] 心跳异常: {e}")
            self._stop.wait(HEARTBEAT_INTERVAL)

    def _task_loop(self):
        while not self._stop.is_set():
            try:
                for task in self.fetch_tasks():
                    tid = task.get("id")
                    self.log(f"[worker] 执行任务 {tid}: "
                             f"{task.get('type')}")
                    result = {"ok": False, "error": "无处理器"}
                    if self.on_task:
                        try:
                            result = self.on_task(task) or result
                        except Exception as e:
                            result = {"ok": False,
                                      "error": f"{e.__class__.__name__}: {e}"}
                    self.report(tid,
                                "done" if result.get("ok") else "failed",
                                result)
            except Exception as e:
                self.log(f"[worker] 任务轮询异常: {e}")
            self._stop.wait(10)

    def stop(self):
        self._stop.set()


# ============ Master 侧 ============

class ClusterState:
    """主控内存状态：worker 注册表 + 任务队列。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.workers = {}   # name -> {accounts, load, last_seen}
        self.tasks = {}     # task_id -> task
        self._seq = 0

    def heartbeat(self, name, accounts, load):
        with self._lock:
            self.workers[name] = {
                "name": name, "accounts": accounts, "load": load,
                "last_seen": time.time(),
            }

    def live_workers(self, timeout=90):
        now = time.time()
        with self._lock:
            return [w for w in self.workers.values()
                    if now - w["last_seen"] < timeout]

    def push_task(self, worker_name, type_, payload):
        """给指定 worker 下发任务。返回 task_id。"""
        with self._lock:
            self._seq += 1
            tid = f"t{self._seq}"
            self.tasks[tid] = {
                "id": tid, "worker": worker_name, "type": type_,
                "payload": payload, "status": "pending",
                "created": time.time(), "result": None,
            }
            return tid

    def pop_tasks(self, worker_name):
        """worker 拉取自己的待执行任务。"""
        with self._lock:
            out = [t for t in self.tasks.values()
                   if t["worker"] == worker_name
                   and t["status"] == "pending"]
            for t in out:
                t["status"] = "running"
            return [{"id": t["id"], "type": t["type"],
                     "payload": t["payload"]} for t in out]

    def report_result(self, task_id, status, data):
        with self._lock:
            t = self.tasks.get(task_id)
            if t:
                t["status"] = status
                t["result"] = data
                t["finished"] = time.time()
            return t

    def task_status(self, task_id):
        with self._lock:
            return self.tasks.get(task_id)
