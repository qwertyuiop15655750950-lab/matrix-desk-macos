# -*- coding: utf-8 -*-
"""Worker 模式入口：无界面，只跑浏览器任务，听主控调度。

启动：
    python run.py --worker --master http://主控IP:8901 --name worker1
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_worker(master_url, name):
    from app.webapp import Api
    from app.cluster import WorkerClient
    from app.config import ensure_dirs

    ensure_dirs()
    api = Api()
    print(f"[worker:{name}] 初始化完成，连接主控 {master_url}", flush=True)

    client = WorkerClient(master_url, name)

    def get_status():
        n = len(api.conn.execute(
            "SELECT id FROM accounts").fetchall())
        with api._actors_lock:
            load = len(api._actors)
        return n, load

    def on_task(task):
        t = task.get("type")
        p = task.get("payload", {})
        try:
            if t == "ping":
                return {"ok": True, "name": name}
            if t == "browser_screenshot":
                # 截指定账号浏览器图（群控用）
                aid = p.get("aid")
                ab = api._get_actor(aid)
                if not ab:
                    return {"ok": False, "error": "浏览器未启动"}
                import base64

                def _shot(b):
                    pages = b.ctx.pages
                    if not pages:
                        return ""
                    return base64.b64encode(
                        pages[-1].screenshot()).decode("ascii")
                return {"ok": True,
                        "b64": ab.call(_shot, timeout=15)}
            return {"ok": False, "error": f"未知任务类型: {t}"}
        except Exception as e:
            return {"ok": False,
                    "error": f"{e.__class__.__name__}: {e}"}

    client.on_task = on_task
    client.start(get_status)
    # 恢复监控（worker 本地跑私信监控）
    try:
        r = api.monitor_start_all()
        if r.get("started"):
            print(f"[worker:{name}] 已恢复 {r['count']} 个账号监控",
                  flush=True)
    except Exception as e:
        print(f"[worker:{name}] 恢复监控失败: {e}", flush=True)

    print(f"[worker:{name}] 运行中，按 Ctrl+C 停止", flush=True)
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        client.stop()
        print(f"[worker:{name}] 已停止", flush=True)
