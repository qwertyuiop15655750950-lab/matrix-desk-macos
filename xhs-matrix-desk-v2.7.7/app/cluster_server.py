# -*- coding: utf-8 -*-
"""主控 HTTP 服务：给 worker 用的 REST API。

Endpoints:
- POST /api/worker/heartbeat {name, accounts, load}
- GET  /api/worker/tasks?name=xxx
- POST /api/worker/result {name, task_id, status, data}
- GET  /api/cluster/status → 主控面板用：在线 worker 列表
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs


class ClusterHandler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n:
                return json.loads(self.rfile.read(n).decode())
        except Exception:
            pass
        return {}

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read_json()
        c = self.server.cluster
        if path == "/api/worker/heartbeat":
            c.heartbeat(data.get("name", "?"),
                        data.get("accounts", 0),
                        data.get("load", 0))
            return self._send({"ok": True})
        if path == "/api/worker/result":
            c.report_result(data.get("task_id"), data.get("status"),
                            data.get("data"))
            return self._send({"ok": True})
        return self._send({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path, qs = parsed.path, parse_qs(parsed.query)
        c = self.server.cluster
        if path == "/api/worker/tasks":
            name = (qs.get("name") or ["?"])[0]
            return self._send({"ok": True,
                               "tasks": c.pop_tasks(name)})
        if path == "/api/cluster/status":
            return self._send({"ok": True,
                               "workers": c.live_workers()})
        return self._send({"ok": False, "error": "not found"}, 404)

    def log_message(self, *a):
        log_fn = self.server.log_fn
        if log_fn:
            log_fn(" ".join(str(x) for x in a))


def start_cluster_server(cluster, port=8901, log_fn=None):
    """启动主控 HTTP 服务（后台线程）。返回 (server, thread)。"""
    # This API has no authentication. Keep account tasks on this computer.
    server = HTTPServer(("127.0.0.1", port), ClusterHandler)
    # Each listening server owns its state and callback. Mutating the handler
    # class redirected requests from every existing server to the last one.
    server.cluster = cluster
    server.log_fn = log_fn
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, t
