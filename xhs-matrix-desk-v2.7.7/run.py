#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""矩阵工作台启动入口：python3 run.py（默认 Web 界面），python3 run.py --tk（经典界面）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if getattr(sys, "frozen", False):
    # PyInstaller 打包环境：Chromium 内核随包放在 exe 同级的 ms-playwright 目录，
    # 这里指给 Playwright，避免它去用户目录找浏览器。非打包运行时无影响。
    _bundled_browsers = os.path.join(os.path.dirname(sys.executable), "ms-playwright")
    if os.path.isdir(_bundled_browsers):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _bundled_browsers)


def env_check():
    """启动环境自检：Python 版本 / Playwright / Chromium / 数据目录可写。

    返回 (ok, messages)。Chromium 缺失时给出明确中文指引，
    而不是等用户点发布后才报错。
    """
    msgs = []
    ok = True
    if sys.version_info < (3, 10):
        ok = False
        msgs.append(f"Python 版本过低（{sys.version.split()[0]}），需要 3.10+")
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        ok = False
        msgs.append("Playwright 未安装，请运行：bash setup.sh")
        return ok, msgs
    # Chromium 可执行文件是否存在
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        if not (exe and os.path.exists(exe)):
            raise FileNotFoundError(exe)
    except Exception:
        ok = False
        msgs.append("浏览器组件未安装。请运行：\n"
                    "  python -m playwright install chromium")
    # 数据目录可写
    try:
        from app.config import DATA_DIR, ensure_dirs
        ensure_dirs()
        probe = os.path.join(DATA_DIR, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        ok = False
        msgs.append(f"数据目录不可写：{e}")
    return ok, msgs


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser()
    _ap.add_argument("--tk", action="store_true")
    _ap.add_argument("--worker", action="store_true",
                     help="Worker 模式：连接主控，只跑浏览器任务，无界面")
    _ap.add_argument("--master", default=None,
                     help="本机主控地址，如 http://127.0.0.1:8901")
    _ap.add_argument("--name", default="worker1", help="Worker 名字")
    _ap.add_argument("--cluster-port", type=int, default=8901,
                     help="主控 HTTP 端口")
    _ap.add_argument('--enable-extras', action='store_true', help='显式启用冻结维护的素材库和浏览计划')
    _args = _ap.parse_args()

    if _args.worker:
        if not _args.master:
            print("Worker 模式需要 --master http://127.0.0.1:8901")
            sys.exit(1)
        from app.worker_main import run_worker
        run_worker(_args.master, _args.name)
    elif _args.tk:
        from app.gui import main
        main()
    else:
        try:
            import webview  # noqa: F401
        except ImportError:
            print("未检测到 pywebview，已回退到经典界面。")
            print("如需新版 Web 界面，请运行：bash setup.sh")
            from app.gui import main
            main()
        else:
            _ok, _msgs = env_check()
            if not _ok:
                print("环境检查未通过：")
                for _m in _msgs:
                    print(" -", _m)
                sys.exit(1)
            from app.webapp import main
            # 主控模式：同时启动集群 HTTP 服务给 worker 连。
            # 复用同一个 Api 实例，保证 cluster 状态共享。
            import app.webapp as _w
            from app.cluster_server import start_cluster_server
            _api = _w.Api(enable_extras=_args.enable_extras)
            start_cluster_server(_api.cluster, port=_args.cluster_port,
                                 log_fn=lambda s: print(f"[cluster] {s}", flush=True))
            print(f"本机任务服务已启动：127.0.0.1:{_args.cluster_port}")
            _w.main(api=_api)
