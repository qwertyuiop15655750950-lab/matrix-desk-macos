'小红书账号、发布、私信及采集 API。'
import base64
import io
import json
import os
import threading
import time
from datetime import datetime
from . import accounts as acct
from .session import AccountBrowser
from .log import log_event, get_logs, task_span
from . import contacts as contacts_mod
from . import leads as leads_mod
from . import messages as msg_mod
from .monitor import SweepMonitor
from . import phrases as phr
from . import poster as poster_mod
from . import profiles as prof
from . import publisher as pub
from . import records as rec_mod
from . import rewriter
from . import schedules as sched
from . import sensitive as sens
from .config import POSTER_DIR, ERROR_SHOTS_DIR, load_selectors, PAGE_GOTO_TIMEOUT
from .db import get_conn
from .api_ui import _thumb_data_uri

VIDEO_EXTS = (".mp4", ".mov")

class XhsAPI:
    def _call_xhs_browser(self, aid, fn, timeout=180, require_open=True):
        """Reserve one account without blocking; cancel callbacks still queued.

        Actor.call cannot remove an enqueued callback when its caller times out.
        The state lock makes cancellation and callback start mutually exclusive;
        a callback that already started keeps the account busy until it returns.
        """
        key = ('xiaohongshu', int(aid))
        state = {'started': False, 'cancelled': False}
        state_lock = threading.Lock()
        with self._browser_jobs_lock:
            if key in self._browser_jobs:
                raise ValueError('该账号正在执行浏览器任务，请等待任务结束或停止当前任务后重试')
            self._browser_jobs[key] = state

        def release():
            with self._browser_jobs_lock:
                if self._browser_jobs.get(key) is state:
                    self._browser_jobs.pop(key)

        def guarded(browser):
            with state_lock:
                if state['cancelled']:
                    raise ValueError('账号浏览器任务已取消，请重新操作')
                state['started'] = True
            try:
                from .native_session import apply_state
                from .config import DATA_DIR
                apply_state(browser.ctx, DATA_DIR, "xiaohongshu", aid)
                return fn(browser)
            finally:
                release()

        try:
            actor = self._get_actor(aid) if require_open else self._ensure_actor(aid)
            if actor is None: raise ValueError('请先打开该账号浏览器并进入需要提取的页面')
            return actor.call(guarded, timeout=timeout)
        except Exception:
            with state_lock:
                state['cancelled'] = True
                queued = not state['started']
            if queued: release()
            raise

    def accounts_list(self):
        return self._ok(items=acct.list_accounts(self.conn))

    MAX_ACCOUNTS = 20  # 本地封顶 20 个账号

    def account_create(self, data):
        try:
            conn = self.conn
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                n = conn.execute(
                    "SELECT COUNT(*) FROM accounts").fetchone()[0]
                if n >= self.MAX_ACCOUNTS:
                    return self._err(
                        f"已达上限 {self.MAX_ACCOUNTS} 个账号，"
                        "删掉不用的再添加")
                return self._ok(id=acct.create_account(conn, data or {}))
        except ValueError as e:
            return self._err(e)

    def account_update(self, aid, data):
        try:
            return self._ok(done=acct.update_account(self.conn, int(aid),
                                                     data or {}))
        except ValueError as e:
            return self._err(e)

    def proxies_import_sub(self, url, scheme="http"):
        """从机场订阅 URL 导入代理。

        解析订阅中的 http/socks5 节点，返回可直接分配的代理列表文本
        （一行一个 IP:端口[:用户:密码]）。ss/vmess/trojan 会被跳过并计数。
        """
        from . import subscribe as sub_mod
        if not (url or "").strip():
            return self._err("请填写订阅链接")
        r = sub_mod.import_subscription(url.strip())
        if r["error"]:
            return self._err(r["error"])
        lines = [sub_mod.format_proxy(p) for p in r["proxies"]]
        msg = f"解析出 {len(lines)} 个可用代理"
        if r["skipped"]:
            msg += f"（跳过 {r['skipped']} 个 ss/vmess/trojan，需本地客户端转换）"
        if not lines:
            msg = "没解析出可用的 http/socks5 代理" + (
                f"（订阅里 {r['skipped']} 个是 ss/vmess/trojan 协议，"
                "浏览器直连不了）" if r["skipped"] else "")
            return self._err(msg)
        return self._ok(text="\n".join(lines), count=len(lines),
                        skipped=r["skipped"], msg=msg)

    def proxies_assign(self, aids, text, scheme="http"):
        """批量分配代理：一行一个（IP:端口 或 IP:端口:用户:密码），
        按行顺序一号一IP分配给目标账号。"""
        import re as _re
        aids = [int(a) for a in (aids or [])]
        if not aids:
            return self._err("请选择目标账号")
        proxies, errors = [], []
        for i, ln in enumerate((text or "").splitlines(), 1):
            ln = ln.strip()
            if not ln:
                continue
            s_ = _re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", ln)
            parts = s_.split(":")
            if len(parts) < 2 or not parts[0] or not parts[1].isdigit():
                errors.append(f"第{i}行格式错误")
                continue
            proxies.append({
                "proxy_scheme": scheme or "http",
                "proxy_host": parts[0], "proxy_port": int(parts[1]),
                "proxy_user": parts[2] if len(parts) > 2 else "",
                "proxy_pass": parts[3] if len(parts) > 3 else ""})
        if not proxies:
            return self._err("没有可用的代理行" +
                             ("（" + "、".join(errors[:3]) + "）" if errors
                              else ""))
        assigned = 0
        for aid, px in zip(aids, proxies):
            a = acct.get_account(self.conn, aid)
            if not a:
                errors.append(f"账号{aid}不存在")
                continue
            acct.update_account(self.conn, aid, px)
            assigned += 1
        msg = f"已分配 {assigned} 个"
        if len(proxies) < len(aids):
            msg += f"（代理不足，还剩 {len(aids) - len(proxies)} 个账号未分配）"
        if errors:
            msg += "；" + "、".join(errors[:3])
        return self._ok(assigned=assigned, msg=msg)

    def account_delete(self, aid):
        return self._ok(done=acct.delete_account(self.conn, int(aid)))

    def account_get(self, aid):
        return self._ok(item=acct.get_account(self.conn, int(aid)))

    def accounts_export(self):
        return self._ok(data=acct.export_accounts(self.conn, "json"))

    def accounts_import_text(self, text, fmt):
        try:
            r = acct.import_accounts(self.conn, text or "", fmt or "json")
            return self._ok(**r)
        except ValueError as e:
            return self._err(e)

    def _get_actor(self, aid):
        """取该账号的浏览器线程（没有或已死则返回 None，不创建）。"""
        with self._actors_lock:
            ab = self._actors.get(int(aid))
        return ab if ab is not None and not ab.is_dead() else None

    def _ensure_actor(self, aid):
        """取或创建该账号的浏览器线程。Playwright 对象只活在该线程内。

        已死的 Actor 自动重建（浏览器崩溃自愈）。
        """
        aid = int(aid)
        with self._browser_jobs_lock:
            if self._browser_jobs.get(('xiaohongshu', aid), {}).get('type') == 'publish':
                raise ValueError('该账号正在发布，完成后可再次打开浏览器')
        with self._actors_lock:
            ab = self._actors.get(aid)
            if ab is not None:
                if ab.is_dead():
                    self._actors.pop(aid, None)
                    log_event(task="browser", aid=aid, stage="actor",
                              status="rebuilt", msg="浏览器已死，自动重建")
                else:
                    return ab
            a = acct.get_account(self.conn, aid)
            if not a:
                raise RuntimeError("账号不存在")
            ab = AccountBrowser(aid, a)
            ab.start()
            self._actors[aid] = ab
            return ab

    def _drop_actor(self, aid):
        """关闭该账号浏览器并回收线程（幂等）。"""
        with self._actors_lock:
            ab = self._actors.pop(int(aid), None)
        if ab is not None:
            try:
                ab.stop()
            except Exception:
                pass

    def browsers_status(self):
        with self._actors_lock:
            beats = [ab.heartbeat() for ab in self._actors.values()]
        running = sorted(b["aid"] for b in beats if b["alive"])
        return self._ok(running=running, heartbeats=beats)

    def browsers_close(self):
        with self._actors_lock:
            items = list(self._actors.items())
            self._actors.clear()
        for _aid, ab in items:
            try:
                ab.stop()
            except Exception:
                pass
        return self._ok()

    def browsers_grid(self):
        """群控视角：返回所有运行中浏览器的实时截图（base64 缩略图）。

        每个账号一项：{aid, nickname, url, b64}。无截图时 b64 为 ''。
        """
        import base64
        result = []
        with self._actors_lock:
            items = list(self._actors.items())
        for aid, ab in items:
            a = acct.get_account(self.conn, aid)
            nickname = (a["nickname"] if a else "") or f"账号{aid}"

            def _shot(b):
                pages = b.ctx.pages
                if not pages:
                    return "", ""
                pg = pages[-1]
                try:
                    buf = pg.screenshot()
                    return base64.b64encode(buf).decode("ascii"), pg.url
                except Exception:
                    return "", ""

            try:
                b64, url = ab.call(_shot, timeout=15)
            except Exception:
                b64, url = "", ""
            result.append({"aid": aid, "nickname": nickname,
                           "url": url, "b64": b64})
        return self._ok(items=result)

    def cluster_status(self):
        """集群状态：在线 worker 列表（主控面板用）。"""
        return self._ok(workers=self.cluster.live_workers())

    def monitors_list(self):
        rows = self.conn.execute(
            "SELECT * FROM keyword_monitors ORDER BY id DESC").fetchall()
        return self._ok(items=[dict(r) for r in rows])

    def monitor_create(self, data):
        from datetime import datetime as _dt
        d = data or {}
        if not d.get("keyword"):
            return self._err("请填写关键词")
        cur = self.conn.execute(
            """INSERT INTO keyword_monitors
               (platform, keyword, target_url, created_at)
               VALUES (?,?,?,?)""",
            (d.get("platform", "xiaohongshu"), d["keyword"],
             d.get("target_url", ""), _dt.now().isoformat(timespec="s")))
        self.conn.commit()
        return self._ok(id=cur.lastrowid)

    def monitor_delete(self, mid):
        self.conn.execute("DELETE FROM keyword_monitors WHERE id=?",
                          (int(mid),))
        self.conn.commit()
        return self._ok()

    def monitor_run(self, mid):
        """执行一次关键词采集（后台线程）。"""
        import threading as _th
        _th.Thread(target=self._monitor_worker, args=(int(mid),),
                   daemon=True).start()
        return self._ok(msg="开始采集…")

    def _monitor_worker(self, mid):
        from . import collector as col_mod
        try:
            m = self.conn.execute(
                "SELECT * FROM keyword_monitors WHERE id=?",
                (mid,)).fetchone()
            if not m:
                return
            m = dict(m)
            if m["platform"] == "douyin":
                self._log("【关键词采集】抖音任务请在抖音工作台选择抖音账号执行，避免使用小红书登录目录")
                return
            keywords = [k.strip() for k in m["keyword"].split(",")
                        if k.strip()]
            # 用第一个有浏览器的账号执行（只读扫描）
            with self._actors_lock:
                actors = list(self._actors.items())
            if not actors:
                self._log(f"【关键词采集】无运行中的浏览器，请先启动一个账号")
                return
            aid, ab = actors[0]

            def _scan(b):
                pages = b.ctx.pages
                pg = pages[-1] if pages else b.ctx.new_page()
                from .browser_plugin_bridge import scan_with_plugin
                return scan_with_plugin(pg, m["target_url"], keywords, platform=m["platform"])

            r = ab.call(_scan, timeout=180)
            if not r.get("ok"):
                self._log(f"【关键词采集】失败: {r.get('error')}")
                return
            added = col_mod.archive_users(
                self.conn, m["platform"], m["keyword"],
                m["target_url"], r["items"])
            from datetime import datetime as _dt
            self.conn.execute(
                """UPDATE keyword_monitors SET last_run=?,
                   found_count=found_count+? WHERE id=?""",
                (_dt.now().isoformat(timespec="s"), added, mid))
            self.conn.commit()
            self._log(f"【关键词采集】{m['keyword']} 扫到 "
                      f"{len(r['items'])} 条，新增归档 {added} 人")
        except Exception as e:
            self._log(f"【关键词采集】异常: {e}")
        finally:
            self.close_thread_conn()

    def collected_users_list(self, keyword="", limit=200):
        q = "SELECT * FROM collected_users"
        args = []
        if keyword:
            q += " WHERE keyword LIKE ? OR nickname LIKE ?"
            args = [f"%{keyword}%", f"%{keyword}%"]
        q += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        rows = self.conn.execute(q, args).fetchall()
        return self._ok(items=[dict(r) for r in rows])

    def collected_user_set_status(self, uid, status):
        self.conn.execute(
            "UPDATE collected_users SET status=? WHERE id=?",
            (status, int(uid)))
        self.conn.commit()
        return self._ok()

    def accounts_check_ips(self):
        """全账号 IP 隔离检测：查每个运行中浏览器的出口 IP，
        标记重复 IP（多个账号共用同一出口 = 隔离失效）。

        返回 {items: [{aid, nickname, proxy, egress_ip, dup}], }
        """
        """全账号 IP 隔离检测：查每个运行中浏览器的出口 IP，
        标记重复 IP（多个账号共用同一出口 = 隔离失效）。

        返回 {items: [{aid, nickname, proxy, egress_ip, dup}], }
        """
        result = []
        ip_to_aids = {}
        with self._actors_lock:
            items = list(self._actors.items())
        for aid, ab in items:
            a = acct.get_account(self.conn, aid)
            nickname = (a["nickname"] if a else "") or f"账号{aid}"
            proxy = ""
            if a and a.get("proxy_host"):
                proxy = f"{a['proxy_host']}:{a.get('proxy_port') or ''}"

            def _ip(b):
                pages = b.ctx.pages
                pg = pages[-1] if pages else None
                if pg is None:
                    pg = b.ctx.new_page()
                    close_after = True
                else:
                    close_after = False
                try:
                    ip, _ = prof.get_egress_ip(pg)
                    return ip
                finally:
                    if close_after:
                        try:
                            pg.close()
                        except Exception:
                            pass

            try:
                egress = ab.call(_ip, timeout=30) or ""
            except Exception:
                egress = ""
            result.append({"aid": aid, "nickname": nickname,
                           "proxy": proxy, "egress_ip": egress})
            if egress:
                ip_to_aids.setdefault(egress, []).append(aid)
        # 标重复
        dup_ips = {ip for ip, aids in ip_to_aids.items() if len(aids) > 1}
        for r in result:
            r["dup"] = r["egress_ip"] in dup_ips
            if r["dup"]:
                others = [x["nickname"] for x in result
                          if x["egress_ip"] == r["egress_ip"]
                          and x["aid"] != r["aid"]]
                r["dup_with"] = "、".join(others)
        return self._ok(items=result,
                        dup_count=len(dup_ips))

    def browser_launch(self, aid):
        threading.Thread(target=self._launch_worker, args=(int(aid),),
                         daemon=True).start()
        return self._ok(msg="正在启动浏览器…")

    def browser_login_queue(self, aids):
        """排队登录：逐个启动浏览器扫码，一次只开一个，省内存。

        每完成一个自动转后台，再问是否继续下一个。
        """
        aids = [int(a) for a in (aids or [])]
        if not aids:
            return self._err("请选择要登录的账号")
        threading.Thread(target=self._login_queue_worker, args=(aids,),
                         daemon=True).start()
        return self._ok(msg=f"开始排队登录 {len(aids)} 个账号…")

    def browser_click_areacode(self, aid):
        """辅助点开登录页的区号下拉（用户手动点不动时用）。"""
        aid = int(aid)
        ab = self._get_actor(aid)
        if ab is None:
            return self._err("请先启动该账号的浏览器")

        def _click(b):
            pages = b.ctx.pages
            if not pages:
                raise RuntimeError("浏览器没有打开的页面")
            return prof.open_areacode_menu(pages[-1])

        try:
            result = ab.call(_click, timeout=10)
            if result["ok"]:
                return self._ok(msg=result["msg"], status=result["status"])
            return self._err(result["error"])
        except Exception as e:
            return self._err(str(e) or "点击失败")

    def _launch_worker(self, aid):
        try:
            ab = self._ensure_actor(aid)
            ok = ab.call(lambda b: prof.ensure_login(b.ctx), timeout=240)
            if ok:
                self.identity_sync(aid)
                try:
                    ab.call(lambda b: prof.export_storage_state(b.ctx, aid))
                except Exception:
                    pass
            if ok:
                ans = self.pm.ask(
                    "confirm", "登录成功",
                    "会话已保存。浏览器窗口要关掉转后台吗？\n"
                    "（关掉更省内存，后台任务照常跑；要看再点启动浏览器）",
                    ["转后台", "保持窗口"])
                if ans == "转后台":
                    self._drop_actor(aid)
                    self._log(f"【账号{aid}】已转后台")
                    return
            self.pm.ask("info", "登录状态",
                        ("登录成功，会话已保存，监控凭证已同步，浏览器保持运行。"
                         if ok else "登录超时，请重试。"), ["知道了"])
        except Exception as e:
            msg = f"{e.__class__.__name__}: {e}"
            # 浏览器在启动过程中被关闭：大概率是用户手动关了窗口，
            # 或浏览器进程崩溃
            if "TargetClosedError" in e.__class__.__name__:
                msg = ("浏览器在启动时被关闭了。\n"
                       "• 如果是你手动关的窗口，重新点「启动浏览器」\n"
                       "• 如果没手动关，可能是浏览器崩溃，把日志发给开发者")
            self.pm.ask("info", "启动失败", msg, ["知道了"])
        finally:
            self.close_thread_conn()

    def _login_queue_worker(self, aids):
        """排队登录：一次只开一个浏览器，扫完自动转后台，再下一个。"""
        try:
            total = len(aids)
            for i, aid in enumerate(aids, 1):
                a = acct.get_account(self.conn, aid)
                name = (a["nickname"] if a else "") or f"账号{aid}"
                # 如果已经登录，跳过
                ab = self._ensure_actor(aid)
                try:
                    ok = ab.call(lambda b: prof.ensure_login(b.ctx),
                                 timeout=240)
                except Exception as e:
                    self._log(f"【{name}】排队登录异常: {e}")
                    ok = False
                if ok:
                    self.identity_sync(aid)
                    try:
                        ab.call(lambda b: prof.export_storage_state(b.ctx,
                                                                    aid))
                    except Exception:
                        pass
                # 自动转后台，省内存
                self._drop_actor(aid)
                self._log(f"【{name}】排队登录 {'成功' if ok else '失败'}，"
                          f"已转后台 ({i}/{total})")
                if not ok:
                    ans = self.pm.ask(
                        "confirm", f"排队登录 ({i}/{total})",
                        f"【{name}】登录超时或失败。\n继续登录下一个吗？",
                        ["继续下一个", "停止排队"])
                    if ans != "继续下一个":
                        break
                    continue
                if i < total:
                    ans = self.pm.ask(
                        "confirm", f"排队登录 ({i}/{total})",
                        f"【{name}】登录成功，已转后台。\n"
                        f"继续登录下一个（剩余 {total - i} 个）吗？",
                        ["继续下一个", "停止排队"])
                    if ans != "继续下一个":
                        break
            self.pm.ask("info", "排队登录", "排队登录结束。", ["知道了"])
        except Exception as e:
            self._log(f"排队登录异常: {e}")
            self.pm.ask("info", "排队登录", f"排队中断: {e}", ["知道了"])
        finally:
            self.close_thread_conn()

    def browser_selftest(self, aid):
        threading.Thread(target=self._selftest_worker, args=(int(aid),),
                         daemon=True).start()
        return self._ok(msg="正在打开并检测发布页…")

    def _selftest_worker(self, aid):
        try:
            ab = self._ensure_actor(aid)
            ok = ab.call(lambda b: prof.ensure_login(b.ctx), timeout=240)
            if not ok:
                self.pm.ask("info", "登录超时", "请先完成扫码登录再自检。",
                            ["知道了"])
                return
            res = ab.call(lambda b: self._do_publish_selftest(b))
            lines = [f"{'✅' if v else '❌'} {k}" for k, v in res.items()]
            miss = [k for k, v in res.items() if not v]
            tail = ("\n\n❌ 的选择器请发回给开发者校准。" if miss
                    else "\n\n全部命中，发布填充可用。")
            self.pm.ask("info", "选择器自检结果",
                        "\n".join(lines) + tail, ["知道了"])
        except Exception as e:
            self.pm.ask("info", "自检失败", f"{e.__class__.__name__}: {e}",
                        ["知道了"])
        finally:
            self.close_thread_conn()

    def _do_publish_selftest(self, ab):
        """运行在浏览器线程内：发布页选择器自检。"""
        page = ab.ctx.new_page()
        try:
            try:
                page.goto(pub.publish_page_url("image"),
                          timeout=PAGE_GOTO_TIMEOUT)
            except Exception as e:
                # 小红书登录后会自动跳转（如 /explore）：goto 报
                # "interrupted by another navigation" 不算失败，
                # 页面已落在最终 URL，直接继续检测选择器
                if "interrupted by another navigation" not in str(e):
                    raise
            return pub.selector_selftest(page)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def drafts_list(self):
        cur = self.conn.execute("SELECT * FROM drafts ORDER BY id")
        return self._ok(items=[dict(r) for r in cur.fetchall()])

    def draft_save(self, data):
        data = data or {}
        if not (data.get("title") or "").strip():
            return self._err("标题不能为空")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if data.get("id"):
            self.conn.execute(
                "UPDATE drafts SET title=?,body=?,topics=?,mode=?,"
                "media_json=?,template=?,updated_at=? WHERE id=?",
                (data["title"], data.get("body", ""), data.get("topics", ""),
                 data.get("mode", "images"),
                 json.dumps(data.get("media", []), ensure_ascii=False),
                 data.get("template", "rice"), now, data["id"]))
        else:
            self.conn.execute(
                "INSERT INTO drafts(title,body,topics,mode,media_json,"
                "template,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (data["title"], data.get("body", ""), data.get("topics", ""),
                 data.get("mode", "images"),
                 json.dumps(data.get("media", []), ensure_ascii=False),
                 data.get("template", "rice"), now, now))
        self.conn.commit()
        did = int(data['id']) if data.get('id') else self.conn.execute('SELECT last_insert_rowid()').fetchone()[0]
        if 'cover_text' in data:
            self.conn.execute('UPDATE drafts SET cover_text=? WHERE id=?', (str(data.get('cover_text') or ''), did))
            self.conn.commit()
        return self._ok(id=did)

    def draft_delete(self, did):
        self.conn.execute("DELETE FROM drafts WHERE id=?", (int(did),))
        self.conn.commit()
        return self._ok()

    def pick_files(self):
        try:
            import webview
            if not webview.windows:
                return self._err("无窗口")
            paths = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True,
                file_types=("媒体文件 (*.png;*.jpg;*.jpeg;*.mp4;*.mov)",
                            "所有文件 (*.*)"))
            return self._ok(paths=list(paths or []))
        except Exception as e:
            return self._err(e)

    def poster_templates(self):
        return self._ok(items=poster_mod.list_templates())

    def draft_import_docx(self, path=None):
        from .draft_docx import parse_docx, import_entries
        try:
            if not path:
                import webview
                if not webview.windows:
                    return self._err('无窗口，请先打开工作台')
                paths = webview.windows[0].create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                    file_types=('Word 文案 (*.docx)',))
                if not paths:
                    return self._ok(cancelled=True)
                path = paths[0]
            entries = parse_docx(path)
            return self._ok(**import_entries(self.conn, entries, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        except (ValueError, OSError) as error:
            return self._err(str(error))

    def poster_generate(self, text, template, title):
        try:
            paths = poster_mod.generate_poster(
                text or "", template=template or "rice", title=title or "",
                out_dir=POSTER_DIR, basename="dzb")
            return self._ok(images=[
                {"path": p, "thumb": _thumb_data_uri(p)} for p in paths])
        except (ValueError, RuntimeError) as e:
            return self._err(e)

    def publish_start(self, draft_id, aids):
        if not aids:
            return self._err("请至少选择一个账号")
        from .publish_runtime import start
        return start(self, self._publish_worker, int(draft_id), list(dict.fromkeys(int(a) for a in aids)))

    def publish_log(self, cursor=0):
        with self.log_lock:
            items = self.log_buf[int(cursor):]
            return self._ok(items=items, cursor=len(self.log_buf))

    @staticmethod
    def _shot(page, aid, task, stage):
        """错误现场截图：{ts}_account{aid}_{task}_{stage}.png。
        用于排查选择器/页面变化/登录验证/弹窗，日志中同步记录路径。"""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = f"{ts}_account{aid}_{task}_{stage}.png"
            path = os.path.join(ERROR_SHOTS_DIR, name)
            page.screenshot(path=path, full_page=True)
            return path
        except Exception:
            return ""

    def _publish_one(self, pw, conn, draft, did, aid, seed):
        """处理单个账号的发布（改写→敏感词→大字报生成→填表→人工确认）。
        返回 'done' / 'skipped'。"""
        with task_span("publish", aid, "one"):
            from .publish_runtime import publish_one
            return publish_one(self, self._publish_one_inner, pw, conn, draft, did, aid, seed)

    def _publish_one_inner(self, pw, conn, draft, did, aid, seed):
        a = acct.get_account(conn, aid)
        if not a:
            return "skipped"
        # Publish exactly the text reviewed by the user; no hidden automatic rewriting.
        var = {key: draft[key] for key in ('title', 'body', 'topics')}
        hits = sens.check_with_db(var["title"] + "\n" + var["body"], conn)
        if hits:
            # 全自动模式：敏感词只记警告不弹窗，由用户看截图复核
            words = "、".join({h["word"] for h in hits})
            self._log(f"【{a['nickname']}】敏感词提醒: {words}（已继续发布）")
            log_event(task="publish", aid=aid, stage="sensitive",
                      status="warning", msg=f"敏感词: {words}")
        if draft["mode"] == "poster":
            kind = "image"
            imgs = []
        else:
            imgs = json.loads(draft["media_json"] or "[]")
            kind = ("video" if any(str(m).lower().endswith(VIDEO_EXTS)
                                   for m in imgs) else "image")
        self._log(f"正在处理【{a['nickname']}】…")
        builtin_cover = (draft.get('cover_text') or var['body']) if draft['mode'] == 'poster' else None
        ab = self._get_actor(aid)
        if ab is not None:
            # 用户手动打开了该账号浏览器：页面操作走其专属线程
            def _flow(b):
                if not prof.ensure_login(b.ctx):
                    raise RuntimeError("LOGIN_TIMEOUT")
                page = pub.prepare_page(b.ctx, kind=kind)
                b.pub_page = page  # 存下来给自动点发布/截图用
                try:
                    return pub.fill_publish_form(page, var["title"],
                                                 var["body"], var["topics"],
                                                 imgs, builtin_cover=builtin_cover)
                except Exception as e:
                    shot = self._shot(page, aid, "publish", "fill")
                    log_event(task="publish", aid=aid, stage="fill",
                              status="failed", error=e, screenshot=shot)
                    raise
            try:
                res = ab.call(_flow, timeout=300, task="publish",
                              stage="fill")
            except RuntimeError as e:
                if "LOGIN_TIMEOUT" in str(e):
                    self.pm.ask("info", "登录超时",
                                f"【{a['nickname']}】扫码登录超时，已跳过",
                                ["知道了"])
                else:
                    self.pm.ask("info", "发布异常",
                                f"【{a['nickname']}】{e}", ["知道了"])
                return "skipped"
        else:
            # 工作线程自建一次性浏览器：创建与使用都在本线程内
            # 全自动：填充 → 点发布 → 截图 → 关闭浏览器
            owned, ctx = False, None
            try:
                ctx, _ = prof.launch_account(pw, a, headless=builtin_cover is None)
                owned = True
            except Exception as e:
                self.pm.ask("info", "浏览器启动失败",
                            f"【{a['nickname']}】{e}", ["知道了"])
                return "skipped"
            try:
                if not prof.ensure_login(ctx, wait_timeout=1500):
                    self._log(f"【{a['nickname']}】登录已失效，请在账号工作台扫码后重试；本次已跳过")
                    return "skipped"
                page = pub.prepare_page(ctx, kind=kind)
                try:
                    res = pub.fill_publish_form(page, var["title"],
                                                var["body"], var["topics"],
                                                imgs, builtin_cover=builtin_cover)
                except Exception as e:
                    shot = self._shot(page, aid, "publish", "fill")
                    log_event(task="publish", aid=aid, stage="fill",
                              status="failed", error=e, screenshot=shot)
                    raise
                for w in res["warnings"]:
                    self._log(f"【{a['nickname']}】警告: {w}")
                if not res['ok']:
                    self._log('表单未完整填充，已停止发布，请人工检查')
                    return 'blocked: 表单未完整填充'
                self._log(f"【{a['nickname']}】内容已填好，自动点击发布…")
                pub_ok, pub_msg = pub.click_publish(page)
                shot = ""
                try:
                    shot = self._shot(page, aid, "publish", "done")
                except Exception:
                    pass
                return self._finish_publish(
                    conn, did, aid, a["nickname"], var["title"],
                    1 if builtin_cover is not None else len(imgs), pub_ok, pub_msg, shot)
            finally:
                if owned:
                    try:
                        ctx.close()
                    except Exception:
                        pass
        for w in res["warnings"]:
            self._log(f"【{a['nickname']}】警告: {w}")
        if not res['ok']:
            return 'blocked: 表单未完整填充'
        # 全自动发布：点发布按钮 → 等完成 → 截图
        self._log(f"【{a['nickname']}】内容已填好，自动点击发布…")

        def _autopub(b):
            page = getattr(b, "pub_page", None)
            if page is None or page.is_closed():
                pages = b.ctx.pages
                page = pages[-1] if pages else None
            if page is None:
                return (False, "找不到发布页面", "")
            ok, msg = pub.click_publish(page)
            shot = ""
            try:
                shot = self._shot(page, aid, "publish", "done")
            except Exception:
                pass
            return (ok, msg, shot)

        try:
            pub_ok, pub_msg, shot = ab.call(
                _autopub, timeout=180, task="publish", stage="autopub")
        except Exception as e:
            pub_ok, pub_msg, shot = False, f"自动发布异常: {e}", ""
        return self._finish_publish(
            conn, did, aid, a["nickname"], var["title"], 1 if builtin_cover is not None else len(imgs),
            pub_ok, pub_msg, shot)

    def _finish_publish(self, conn, did, aid, nickname, title,
                        img_count, pub_ok, pub_msg, shot):
        """自动发布收尾：记日志、写发布记录、返回 done/skipped。"""
        if pub_ok:
            log_event(task="publish", aid=aid, stage="autopub",
                      status="success", msg=f"【{nickname}】{pub_msg}",
                      screenshot=shot)
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO publish_records(account_id,draft_id,"
                "title_used,image_count,status,created_at,screenshot)"
                " VALUES(?,?,?,?,?,?,?)",
                (aid, did, title, img_count, "done", now, shot))
            conn.commit()
            self._log(f"【{nickname}】已发布，截图已保存")
            return "done"
        self._log(f"【{nickname}】发布失败: {pub_msg}")
        log_event(task="publish", aid=aid, stage="autopub",
                  status="failed", msg=f"【{nickname}】{pub_msg}",
                  screenshot=shot)
        if pub_msg.startswith('待核对'):
            return 'review_required'
        self.pm.ask("info", "发布失败",
                    f"【{nickname}】{pub_msg}", ["知道了"])
        return "skipped"

    def _publish_worker(self, did, aids, queue_id=None, queue_key=None):
        conn = get_conn(self.db_path)
        cur = conn.execute("SELECT * FROM drafts WHERE id=?", (did,))
        row = cur.fetchone()
        if not row:
            conn.close()
            self._log("草稿不存在，发布终止")
            return
        draft = dict(row)
        self._log(f"开始：【{draft['title']}】→ {len(aids)} 个账号")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                for i, aid in enumerate(aids):
                    if (queue_id is not None
                            and self._queue_is_cancelled(queue_id)):
                        self._log("队列已取消，停止")
                        break
                    try:
                        result = self._publish_one(pw, conn, draft, did,
                                                   aid, i)
                    except Exception as e:
                        # 单账号异常只记失败并继续下一个，不拖垮整个队列
                        # （task_span 已记 failed+traceback+cost_ms）
                        from .page_guard import PageBlocked
                        from .xhs_poster import PosterGenerationError
                        prefix = 'blocked' if isinstance(e,(PageBlocked,PosterGenerationError)) else 'failed'
                        result = f'{prefix}: {e.__class__.__name__}: {e}'
                        self._log(f'【{aid}】处理异常：{result}')
                    if queue_id is not None:
                        self._queue_mark(queue_id, queue_key if queue_key is not None else aid, result)
        except Exception as e:
            self.pm.ask("info", "发布流程出错",
                        f"{e.__class__.__name__}: {e}", ["知道了"])
        finally:
            conn.close()
            self.close_thread_conn()
        self._log("批量发布流程结束")

    def _queue_row(self, qid):
        cur = self.conn.execute("SELECT * FROM publish_queue WHERE id=?",
                                (int(qid),))
        r = cur.fetchone()
        return dict(r) if r else None

    def _queue_is_cancelled(self, qid):
        r = self._queue_row(qid)
        return (not r) or r["status"] == "cancelled"

    def _queue_mark(self, qid, aid, result):
        with self._queue_lock:
            self._queue_mark_locked(qid, aid, result)

    def _queue_mark_locked(self, qid, aid, result):
        r = self._queue_row(qid)
        if not r:
            return
        done = json.loads(r["done_ids"] or "[]")
        results = json.loads(r["results"] or "{}")
        if aid not in done and not str(result).startswith('blocked:'):
            done.append(aid)
        results[str(aid)] = result
        total = self._queue_jobs(r)
        status = "finished" if len(done) >= len(total) else r["status"]
        if result == 'review_required' or str(result).startswith('blocked:'):
            status = 'paused'
        self.conn.execute(
            "UPDATE publish_queue SET done_ids=?, results=?, status=?"
            " WHERE id=?",
            (json.dumps(done), json.dumps(results), status, qid))
        self.conn.commit()

    def queue_create(self, draft_id, aids):
        with self._queue_lock:
            if self._publish_busy: return self._err("发布任务进行中，请稍后新建队列")
            return self._queue_create_locked(draft_id, aids)

    def _queue_create_locked(self, draft_id, aids, jobs=None):
        aids = list(dict.fromkeys(int(a) for a in (aids or [])))
        if not aids:
            return self._err("请至少选择一个账号")
        cur = self.conn.execute(
            "SELECT id FROM publish_queue WHERE status IN ('active','paused')")
        if cur.fetchone():
            return self._err("已有进行中的队列，请先完成或取消")
        cur = self.conn.execute("SELECT title FROM drafts WHERE id=?",
                                (int(draft_id),))
        if not cur.fetchone():
            return self._err("草稿不存在")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self.conn.execute(
            "INSERT INTO publish_queue(draft_id, account_ids, done_ids,"
            " results, status, created_at, jobs_json) VALUES(?,?,?,?,?,?,?)",
            (int(draft_id), json.dumps(aids), "[]", "{}", "active", now, json.dumps(jobs or [])))
        self.conn.commit()
        return self._ok(id=cur.lastrowid)

    @staticmethod
    def _queue_jobs(row):
        return json.loads(row.get('jobs_json') or '[]') or [
            {'key':aid,'aid':aid,'did':row['draft_id']} for aid in json.loads(row['account_ids'] or '[]')]

    def queue_create_many(self, draft_ids, aids):
        with self._queue_lock:
            if self._publish_busy: return self._err('发布任务进行中，请先暂停')
            dids = list(dict.fromkeys(int(did) for did in (draft_ids or [])))
            accounts = list(dict.fromkeys(int(aid) for aid in (aids or [])))
            if not dids or not accounts: return self._err('请勾选文案和小红书账号')
            if len(dids) > 500: return self._err('单队列最多 500 条文案')
            for did in dids:
                if not self.conn.execute('SELECT id FROM drafts WHERE id=?',(did,)).fetchone():
                    return self._err('所选文案已删除，请重新选择')
            if any(not acct.get_account(self.conn, aid) for aid in accounts):
                return self._err('所选账号已删除，请重新选择')
            jobs = [{'key':f'{i+1}:{did}:{accounts[i%len(accounts)]}', 'did':did, 'aid':accounts[i%len(accounts)]}
                    for i,did in enumerate(dids)]
            response = self._queue_create_locked(dids[0], accounts, jobs)
            if not response.get('ok'): return response
            return self._ok(id=response['id'],total=len(jobs),browser_slots=1)

    def queue_get(self):
        cur = self.conn.execute(
            "SELECT * FROM publish_queue WHERE status IN ('active','paused','finished')"
            " ORDER BY id DESC LIMIT 1")
        r = cur.fetchone()
        if not r:
            return self._ok(queue=None)
        d = dict(r)
        jobs = self._queue_jobs(d)
        done = json.loads(d["done_ids"] or "[]")
        pending = [job['key'] for job in jobs if job['key'] not in done]
        names, job_details = {}, []
        results = json.loads(d['results'] or '{}')
        for job in jobs:
            a = acct.get_account(self.conn, job['aid'])
            name = a['nickname'] if a else f"账号{job['aid']}"
            title_row = self.conn.execute('SELECT title FROM drafts WHERE id=?',(job['did'],)).fetchone()
            title = title_row['title'] if title_row else '草稿已删除'
            names[str(job['key'])] = name
            job_details.append({**job,'nickname':name,'title':title,'result':results.get(str(job['key']),'pending')})
        cur2 = self.conn.execute("SELECT title FROM drafts WHERE id=?",
                                 (d["draft_id"],))
        t = cur2.fetchone()
        return self._ok(queue={
            "id": d["id"], "draft_title": t["title"] if t else "",
            "total": len(jobs), "done": len(done), "jobs":job_details,
            "pending": pending, "pending_names": names,
            "results": json.loads(d["results"] or "{}"),
            "status": d["status"], "processing": self._publish_busy,
            "browser_slots": 1})

    def queue_process_next(self, auto_continue=False):
        r = self.queue_get()
        qq = r.get("queue")
        if not qq:
            return self._err("没有进行中的队列")
        if not qq["pending"]:
            return self._err("队列已完成")
        from .publish_runtime import start
        return start(self, self._queue_worker, qq["id"], bool(auto_continue))

    def _queue_worker(self, qid, auto_continue):
        try:
            self.conn.execute("UPDATE publish_queue SET status='active' WHERE id=? AND status='paused'",(qid,))
            self.conn.commit()
            self._queue_worker_inner(qid, auto_continue)
        finally:
            self.close_thread_conn()

    def _queue_worker_inner(self, qid, auto_continue):
        while True:
            r = self._queue_row(qid)
            if not r or r["status"] not in ("active", "paused"):
                break
            if r["status"] == "paused" and auto_continue:
                break  # 暂停只打断连续处理
            jobs = self._queue_jobs(r)
            done = json.loads(r["done_ids"] or "[]")
            pending = [job for job in jobs if job['key'] not in done]
            if not pending:
                break
            if r["status"] == "paused":
                self.conn.execute(
                    "UPDATE publish_queue SET status='active' WHERE id=?",
                    (qid,))
                self.conn.commit()
            job = pending[0]
            if r.get('jobs_json') not in (None,'','[]'):
                self._publish_worker(job['did'], [job['aid']], queue_id=qid, queue_key=job['key'])
            else:
                self._publish_worker(job['did'], [job['aid']], queue_id=qid)
            after = self._queue_row(qid)
            if after and job['key'] not in json.loads(after["done_ids"] or "[]"):
                self.conn.execute("UPDATE publish_queue SET status='paused' WHERE id=? AND status='active'", (qid,))
                self.conn.commit()
                self._log("本账号未完成处理，队列已暂停，请检查日志后重试")
                break
            if not auto_continue:
                break

    def queue_pause(self):
        cur = self.conn.execute(
            "UPDATE publish_queue SET status='paused'"
            " WHERE status='active'")
        self.conn.commit()
        return self._ok(paused=cur.rowcount > 0)

    def queue_cancel(self):
        self.conn.execute(
            "UPDATE publish_queue SET status='cancelled'"
            " WHERE status IN ('active','paused')")
        self.conn.commit()
        return self._ok()

    SENDS_PER_HOUR = 20

    def _msg_sel(self):
        base = dict(msg_mod.DEFAULT_SELECTORS)
        try:
            base.update(load_selectors().get("message_page", {}))
        except Exception:
            pass
        return base

    def messages_open(self, aid):
        aid = int(aid)
        if not acct.get_account(self.conn, aid): return self._err('账号不存在')
        with self._actors_lock:
            if self._message_open_state.get(aid, {}).get('state') == 'opening':
                return self._ok(msg='消息页正在打开', state='opening')
            self._message_open_state[aid] = {'state':'opening', 'error':''}
        threading.Thread(target=self._messages_open_worker,
                         args=(aid,), daemon=True).start()
        return self._ok(msg="正在打开私信页…")

    def messages_open_status(self, aid):
        with self._actors_lock:
            return self._ok(**dict(self._message_open_state.get(int(aid), {'state':'idle','error':''})))

    def _set_message_open_state(self, aid, state, error=''):
        with self._actors_lock:
            self._message_open_state[aid] = {'state':state, 'error':error}

    def _messages_open_worker(self, aid):
        try:
            ab = self._ensure_actor(aid)
            ok = ab.call(lambda b: prof.ensure_login(b.ctx), timeout=240)
            if not ok:
                self._set_message_open_state(aid, 'error', '登录超时，请在该账号浏览器完成扫码后重试')
                return
            sel = self._msg_sel()
            logged = ab.call(lambda b: self._do_open_chat(b, sel))
            if not logged:
                self._set_message_open_state(aid, 'error', '消息页未登录，请在浏览器检查登录状态')
            else:
                self._set_message_open_state(aid, 'ready')
        except Exception as e:
            self._set_message_open_state(aid, 'error', str(e) or '打开消息页失败')
        finally:
            self.close_thread_conn()

    def _do_open_chat(self, ab, sel):
        """运行在浏览器线程内：打开私信页并确认登录态。"""
        page = ab.ensure_chat_page(msg_mod.CHAT_URL)
        return msg_mod.is_logged_in(page, sel)

    def _chat_actor_or_err(self, aid):
        """取 actor；没有则返回错误 dict（调用方直接 return）。"""
        ab = self._get_actor(int(aid))
        if ab is None:
            return None, self._err("请先点击「打开消息页」并完成登录")
        return ab, None

    def messages_conversations(self, aid):
        aid = int(aid)
        ab, err = self._chat_actor_or_err(aid)
        if err:
            return err
        sel = self._msg_sel()

        def _read(b):
            page = b.chat_page
            if page is None or page.is_closed():
                raise RuntimeError("请先点击「打开消息页」并完成登录")
            if not msg_mod.is_logged_in(page, sel):
                raise RuntimeError("私信页未登录")
            return msg_mod.conversation_list(page, sel)

        try:
            return self._ok(items=ab.call(_read))
        except Exception as e:
            return self._err(str(e) or "读取会话失败")

    def messages_thread(self, aid, user_id):
        aid = int(aid)
        if not msg_mod.USER_ID_RE.match(user_id or ""):
            return self._err("会话 ID 非法")
        ab, err = self._chat_actor_or_err(aid)
        if err:
            return err
        sel = self._msg_sel()

        def _read(b):
            page = b.chat_page
            if page is None or page.is_closed():
                raise RuntimeError("请先点击「开始监控」打开私信页")
            msg_mod.open_conversation(page, user_id)
            return msg_mod.read_thread(page, sel)

        try:
            return self._ok(items=ab.call(_read))
        except Exception as e:
            return self._err(str(e) or "读取消息失败")

    def messages_send(self, aid, user_id, nickname, text):
        aid = int(aid)
        text = (text or "").strip()
        if not text:
            return self._err("回复正文为空")
        if msg_mod.utf16_len(text) > msg_mod.MAX_TEXT_LEN:
            return self._err(f"正文超过 {msg_mod.MAX_TEXT_LEN} 字限制")
        if not msg_mod.USER_ID_RE.match(user_id or ""):
            return self._err("会话 ID 非法")
        # 频率限制：每账号每小时最多 SENDS_PER_HOUR 条
        now = time.time()
        times = [t for t in self._send_times.get(aid, []) if now - t < 3600]
        if len(times) >= self.SENDS_PER_HOUR:
            return self._err(f"频率限制：该账号本小时已发送 {len(times)} 条，请稍后再试")
        # 防重：同一会话不连续发相同正文
        if self._last_sent.get((aid, user_id)) == text:
            return self._err("与上一条发送内容相同，已拦截（防重）")
        ab, err = self._chat_actor_or_err(aid)
        if err:
            return err
        sel = self._msg_sel()

        def _send(b):
            page = b.chat_page
            if page is None or page.is_closed():
                raise RuntimeError("请先点击「开始监控」打开私信页")
            try:
                cur = page.url
            except Exception:
                cur = ""
            if user_id not in cur:
                msg_mod.open_conversation(page, user_id)
            ok, msg = msg_mod.fill_reply(page, sel, text)
            if not ok:
                raise RuntimeError(msg)
            ok, msg = msg_mod.send_current(page, sel, text)
            if not ok:
                raise RuntimeError(msg)
            return msg

        try:
            msg = ab.call(_send, timeout=120)
        except Exception as e:
            return self._err(str(e) or "发送失败")
        times.append(now)
        self._send_times[aid] = times
        self._last_sent[(aid, user_id)] = text
        return self._ok(msg=msg)

    def messages_send_quota(self, aid):
        now = time.time()
        times = [t for t in self._send_times.get(int(aid), []) if now - t < 3600]
        return self._ok(used=len(times), limit=self.SENDS_PER_HOUR)

    def messages_close(self, aid):
        ab = self._get_actor(aid)
        if ab is not None:
            try:
                ab.call(lambda b: b.close_chat_page(), timeout=30)
            except Exception:
                pass
        return self._ok()

    def message_selftest(self, aid):
        threading.Thread(target=self._message_selftest_worker,
                         args=(int(aid),), daemon=True).start()
        return self._ok(msg="正在打开并检测私信页…")

    def _message_selftest_worker(self, aid):
        try:
            ab = self._ensure_actor(aid)
            ok = ab.call(lambda b: prof.ensure_login(b.ctx), timeout=240)
            if not ok:
                self.pm.ask("info", "登录超时", "请先完成扫码登录再自检。",
                            ["知道了"])
                return
            sel = self._msg_sel()
            res = ab.call(lambda b: self._do_msg_selftest(b, sel))
            lines = [f"{'✅' if v else '❌'} {k}" for k, v in res.items()]
            miss = [k for k, v in res.items() if not v and k != "login_ok"]
            tail = ("\n\n❌ 的选择器请发回给开发者校准（可能小红书改版）。"
                    if miss else "\n\n全部命中，私信读写可用。")
            if not res.get("login_ok"):
                tail += "\n⚠️ 私信页显示未登录，请先确认登录状态。"
            self.pm.ask("info", "私信页选择器自检", "\n".join(lines) + tail,
                        ["知道了"])
        except Exception as e:
            self.pm.ask("info", "自检失败", f"{e.__class__.__name__}: {e}",
                        ["知道了"])
        finally:
            self.close_thread_conn()

    def _do_msg_selftest(self, ab, sel):
        """运行在浏览器线程内：私信页选择器自检。"""
        page = ab.ctx.new_page()
        try:
            try:
                page.goto(msg_mod.CHAT_URL, timeout=PAGE_GOTO_TIMEOUT)
            except Exception as e:
                if "interrupted by another navigation" not in str(e):
                    raise
            page.wait_for_timeout(2500)
            return msg_mod.selector_selftest(page, sel)
        finally:
            try:
                page.close()
            except Exception:
                pass

    def _get_engine(self):
        if self._engine is None:
            self._engine = SweepMonitor(
                slots=self._monitor_slots,
                get_cred=self._sweep_get_cred,
                fetch_convs=self._sweep_fetch_convs,
                lead_hook=self._sweep_lead_hook)
        return self._engine

    def _sweep_get_cred(self, aid):
        """纯数据钩子：给引擎该账号的凭证与代理；手动浏览器在用时跳过。"""
        if self._get_actor(aid) is not None:
            return {"skip": "手动浏览器使用中，本轮跳过"}
        a = acct.get_account(self.conn, aid)
        if not a:
            raise RuntimeError("账号不存在")
        return {"storage_state": prof.storage_state_path(aid),
                "proxy": prof.build_proxy(a),
                "skip": None}

    def _sweep_fetch_convs(self, aid, page):
        sel = self._msg_sel()
        if not msg_mod.is_logged_in(page, sel):
            raise RuntimeError("凭证失效，需重新扫码登录")
        return msg_mod.conversation_list(page, sel)

    def _sweep_lead_hook(self, aid, convs):
        """只读线索识别：会话预览命中意向词 -> 写入 leads 表 + 推送事件。"""
        try:
            keywords = leads_mod.get_keywords(self.conn)
        except Exception:
            return
        if not keywords:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for c in convs or []:
            uid = c.get("user_id") or ""
            if not uid:
                continue
            hits = leads_mod.detect_intent(c.get("preview") or "", keywords)
            if not hits:
                continue
            words_json = json.dumps(hits, ensure_ascii=False)
            # INSERT OR IGNORE + UPDATE：原子防重，不复活已处理状态
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO leads(account_id, user_id, nickname,"
                " preview, matched_words, status, detected_at, updated_at)"
                " VALUES(?,?,?,?,?,'new',?,?)",
                (aid, uid, c.get("nickname") or "",
                 c.get("preview") or "", words_json, now, now))
            is_new = cur.rowcount == 1
            self.conn.execute(
                "UPDATE leads SET nickname=?, preview=?, matched_words=?,"
                " updated_at=? WHERE account_id=? AND user_id=?",
                (c.get("nickname") or "", c.get("preview") or "",
                 words_json, now, aid, uid))
            if is_new:
                # 新线索事件 -> 全局 toast
                eng = self._engine
                if eng is not None:
                    a = acct.get_account(self.conn, aid)
                    nick = (a["nickname"] if a else "") or f"账号{aid}"
                    with eng._lock:
                        eng.events.append({
                            "t": now, "aid": aid, "kind": "lead",
                            "nickname": nick,
                            "text": f"🔥 {c.get('nickname') or '客户'}"
                                    f"（{ '、'.join(hits[:3]) }）"})
        try:
            self.conn.commit()
        except Exception:
            pass

    def _contacts_upsert(self, aid, uid, nickname, found, snippet):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        wechat = " / ".join(found["wechats"][:3])
        phone = " / ".join(found["phones"][:3])
        qq = " / ".join(found["qqs"][:3])
        self.conn.execute(
            "INSERT OR IGNORE INTO customer_contacts(account_id, user_id,"
            " nickname, wechat, phone, qq, snippet, status, detected_at,"
            " updated_at) VALUES(?,?,?,?,?,?,?,'new',?,?)",
            (aid, uid, nickname, wechat, phone, qq, snippet, now, now))
        self.conn.execute(
            "UPDATE customer_contacts SET nickname=?, wechat=?, phone=?,"
            " qq=?, snippet=?, updated_at=? WHERE account_id=? AND user_id=?",
            (nickname, wechat, phone, qq, snippet, now, aid, uid))
        self.conn.commit()

    def contacts_scan_start(self, aids):
        aids = [int(a) for a in (aids or [])]
        if not aids:
            return self._err("请选择账号")
        with self._scan_lock:
            if self._scan["running"]:
                return self._err("扫描进行中，请稍候")
            self._scan.update(running=True, accounts_done=0,
                              accounts_total=len(aids), convs_done=0,
                              convs_total=0, current="", found=0)
        threading.Thread(target=self._contacts_worker, args=(aids,),
                         daemon=True).start()
        return self._ok(msg="开始扫描…")

    def contacts_scan_status(self):
        with self._scan_lock:
            return self._ok(**dict(self._scan))

    def _contacts_worker(self, aids):
        conn = get_conn(self.db_path)
        try:
            for ai, aid in enumerate(aids):
                a = acct.get_account(conn, aid)
                if not a:
                    continue
                with self._scan_lock:
                    self._scan["accounts_done"] = ai
                    self._scan["current"] = a["nickname"]
                if self._get_actor(aid) is not None:
                    self._log(f"【{a['nickname']}】手动浏览器使用中，已跳过")
                    continue
                try:
                    with task_span("contacts", aid, "scan"):
                        n = self._scan_account_contacts(aid, a)
                    with self._scan_lock:
                        self._scan["found"] += n
                except Exception as e:
                    self._log(f"【{a['nickname']}】扫描失败：{e}"[:80])
                finally:
                    self._drop_actor(aid)
                with self._scan_lock:
                    self._scan["accounts_done"] = ai + 1
        finally:
            conn.close()
            self.close_thread_conn()
            with self._scan_lock:
                self._scan["running"] = False
                self._scan["current"] = ""

    def _scan_account_contacts(self, aid, a):
        """扫描单个账号全部会话的联系方式，返回新增/更新条数。

        为该账号起临时 Actor：全部页面操作在其专属线程内，扫完即回收。
        """
        ab = self._ensure_actor(aid)
        try:
            sel = self._msg_sel()

            def _scan(b):
                found_n = 0
                if not prof.ensure_login(b.ctx):
                    raise RuntimeError("扫码登录超时")
                page = b.ensure_chat_page(msg_mod.CHAT_URL)
                if not msg_mod.is_logged_in(page, sel):
                    raise RuntimeError("私信页未登录")
                convs = msg_mod.conversation_list(page, sel) or []
                with self._scan_lock:
                    self._scan["convs_total"] += len(convs)
                for c in convs:
                    uid = c.get("user_id") or ""
                    if not uid:
                        continue
                    try:
                        msg_mod.open_conversation(page, uid)
                        thread = msg_mod.read_thread(page, sel) or []
                    except Exception:
                        continue
                    text = "\n".join(m.get("text") or "" for m in thread)
                    found = contacts_mod.extract_contacts(text)
                    if found["phones"] or found["wechats"] or found["qqs"]:
                        snippet = contacts_mod.snippet_for(text, found)
                        self._contacts_upsert(aid, uid,
                                              c.get("nickname") or "",
                                              found, snippet)
                        found_n += 1
                    with self._scan_lock:
                        self._scan["convs_done"] += 1
                    page.wait_for_timeout(1200)
                return found_n

            return ab.call(_scan, timeout=1800)
        finally:
            self._drop_actor(aid)

    def contacts_list(self, status=""):
        sql = ("SELECT c.*, a.nickname AS account_name FROM customer_contacts c"
               " LEFT JOIN accounts a ON a.id=c.account_id")
        args = []
        if status:
            sql += " WHERE c.status=?"
            args.append(status)
        sql += " ORDER BY c.updated_at DESC LIMIT 1000"
        items = []
        for r in self.conn.execute(sql, args).fetchall():
            d = dict(r)
            d["status_cn"] = contacts_mod.STATUS_CN.get(d["status"],
                                                        d["status"])
            items.append(d)
        counts = {}
        for st in ("new", "added", "ignored"):
            counts[st] = self.conn.execute(
                "SELECT COUNT(*) c FROM customer_contacts WHERE status=?",
                (st,)).fetchone()["c"]
        return self._ok(items=items, counts=counts)

    def contact_set_status(self, cid, status):
        if status not in contacts_mod.STATUS_CN:
            return self._err("状态非法")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE customer_contacts SET status=?, updated_at=? WHERE id=?",
            (status, now, int(cid)))
        self.conn.commit()
        return self._ok()

    def contacts_export(self):
        import openpyxl
        r = self.contacts_list("")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "客户联系方式"
        headers = ["账号", "客户昵称", "微信号", "手机号", "QQ",
                   "来源消息", "发现时间", "状态"]
        ws.append(headers)
        for it in r["items"]:
            ws.append([it.get("account_name") or ("账号" + str(it["account_id"])),
                       it.get("nickname") or "", it.get("wechat") or "",
                       it.get("phone") or "", it.get("qq") or "",
                       it.get("snippet") or "", it.get("updated_at") or "",
                       it.get("status_cn") or ""])
        for col in range(1, 9):
            ws.column_dimensions[
                openpyxl.utils.get_column_letter(col)].width = [
                    14, 16, 22, 16, 14, 40, 18, 10][col - 1]
        bio = io.BytesIO()
        wb.save(bio)
        b64 = base64.b64encode(bio.getvalue()).decode("ascii")
        fname = ("客户联系方式_" +
                 datetime.now().strftime("%Y%m%d_%H%M%S") + ".xlsx")
        return self._ok(filename=fname, b64=b64)

    def _comment_sel(self):
        return load_selectors()["comment_page"]

    def comments_scan_start(self, aids):
        aids = [int(a) for a in (aids or [])]
        if not aids:
            return self._err("请选择账号")
        with self._scan_lock:
            if self._scan["running"]:
                return self._err("扫描进行中，请稍候")
            self._scan.update(running=True, accounts_done=0,
                              accounts_total=len(aids), convs_done=0,
                              convs_total=0, current="", found=0)
        threading.Thread(target=self._comments_worker, args=(aids,),
                         daemon=True).start()
        return self._ok(msg="开始扫描评论区…")

    def _comments_worker(self, aids):
        from . import comments as cmt_mod
        from . import contacts as contacts_mod
        from . import leads as leads_mod
        conn = get_conn(self.db_path)
        try:
            keywords = leads_mod.get_keywords(conn)
            for ai, aid in enumerate(aids):
                a = acct.get_account(conn, aid)
                if not a:
                    continue
                with self._scan_lock:
                    self._scan["accounts_done"] = ai
                    self._scan["current"] = a["nickname"]
                if self._get_actor(aid) is not None:
                    self._log(f"【{a['nickname']}】手动浏览器使用中，已跳过")
                    continue
                try:
                    with task_span("comments", aid, "scan"):
                        n = self._scan_account_comments(
                            aid, a, cmt_mod, contacts_mod, leads_mod,
                            keywords)
                    with self._scan_lock:
                        self._scan["found"] += n
                except Exception as e:
                    self._log(f"【{a['nickname']}】评论扫描失败：{e}"[:80])
                finally:
                    self._drop_actor(aid)
                with self._scan_lock:
                    self._scan["accounts_done"] = ai + 1
        finally:
            conn.close()
            self.close_thread_conn()
            with self._scan_lock:
                self._scan["running"] = False
                self._scan["current"] = ""

    def _scan_account_comments(self, aid, a, cmt_mod, contacts_mod,
                               leads_mod, keywords):
        """扫单个账号主页笔记的评论区，返回新增线索/联系方式条数。"""
        ab = self._ensure_actor(aid)
        try:
            sel = self._comment_sel()
            found_n = 0

            def _scan(b):
                nonlocal found_n
                if not prof.ensure_login(b.ctx):
                    raise RuntimeError("扫码登录超时")
                page = b.ctx.new_page()
                try:
                    uid = cmt_mod.get_own_user_id(page)
                    if not uid:
                        raise RuntimeError("拿不到账号 user_id")
                    notes = cmt_mod.list_profile_notes(page, uid, sel,
                                                       max_notes=20)
                    with self._scan_lock:
                        self._scan["convs_total"] += len(notes)
                    for nt in notes:
                        with self._scan_lock:
                            self._scan["current"] = (
                                f"{a['nickname']} · {nt['title'][:12]}")
                        try:
                            cmts = cmt_mod.read_note_comments(
                                page, nt["url"], sel)
                        except Exception:
                            continue
                        for c in cmts:
                            text = c.get("text") or ""
                            if not text:
                                continue
                            # 1) 联系方式
                            fc = contacts_mod.extract_contacts(text)
                            if fc["phones"] or fc["wechats"] or fc["qqs"]:
                                self._contacts_upsert(
                                    aid, "cmt:" + nt["note_id"],
                                    c.get("author") or "",
                                    fc, text[:60])
                                found_n += 1
                            # 2) 意向线索
                            hits = leads_mod.detect_intent(text, keywords)
                            if hits:
                                self._comment_lead_upsert(
                                    aid, nt, c, text, hits)
                                found_n += 1
                        with self._scan_lock:
                            self._scan["convs_done"] += 1
                        page.wait_for_timeout(1000)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

            ab.call(_scan, timeout=1800)
            return found_n
        finally:
            self._drop_actor(aid)

    def _comment_lead_upsert(self, aid, note, comment, text, hits):
        from datetime import datetime as _dt
        now = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        uid = "cmt:%s:%s" % (note["note_id"],
                             (comment.get("author") or "")[:20])
        words_json = json.dumps(hits, ensure_ascii=False)
        preview = "【评论@%s】%s" % (note["title"][:20], text[:80])
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO leads(account_id, user_id, nickname,"
            " preview, matched_words, status, detected_at, updated_at)"
            " VALUES(?,?,?,?,?,'new',?,?)",
            (aid, uid, comment.get("author") or "",
             preview, words_json, now, now))
        self.conn.execute(
            "UPDATE leads SET nickname=?, preview=?, matched_words=?,"
            " updated_at=? WHERE account_id=? AND user_id=?",
            (comment.get("author") or "", preview, words_json,
             now, aid, uid))
        self.conn.commit()

    def leads_list(self, status=""):
        sql = ("SELECT l.*, a.nickname AS account_name FROM leads l"
               " LEFT JOIN accounts a ON a.id=l.account_id")
        args = []
        if status:
            sql += " WHERE l.status=?"
            args.append(status)
        sql += " ORDER BY l.updated_at DESC LIMIT 500"
        cur = self.conn.execute(sql, args)
        items = []
        for r in cur.fetchall():
            d = dict(r)
            d["matched_words"] = json.loads(d["matched_words"] or "[]")
            d["status_cn"] = leads_mod.LEAD_STATUS_CN.get(d["status"],
                                                         d["status"])
            items.append(d)
        counts = {}
        for st in ("new", "contacted", "done", "ignored"):
            c = self.conn.execute(
                "SELECT COUNT(*) c FROM leads WHERE status=?", (st,)).fetchone()
            counts[st] = c["c"]
        return self._ok(items=items, counts=counts)

    def lead_set_status(self, lid, status):
        if status not in leads_mod.LEAD_STATUS_CN:
            return self._err("状态非法")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE leads SET status=?, updated_at=? WHERE id=?",
            (status, now, int(lid)))
        self.conn.commit()
        return self._ok()

    def lead_keywords_list(self):
        return self._ok(items=leads_mod.get_keywords(self.conn))

    def lead_keywords_save(self, words):
        n = leads_mod.save_keywords(self.conn, words)
        return self._ok(count=n)

    def monitor_enable(self, aid):
        aid = int(aid)
        a = acct.get_account(self.conn, aid)
        if not a:
            return self._err("账号不存在")
        if not prof.has_storage_state(aid):
            return self._err("该账号未同步监控凭证：请先启动浏览器扫码登录（登录成功后自动同步）")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT OR REPLACE INTO monitor_targets(account_id, nickname,"
            " created_at) VALUES(?,?,?)", (aid, a["nickname"], now))
        self.conn.commit()
        self._get_engine().add(aid, a["nickname"])
        return self._ok()

    def monitor_disable(self, aid):
        aid = int(aid)
        self.conn.execute("DELETE FROM monitor_targets WHERE account_id=?",
                          (aid,))
        self.conn.commit()
        if self._engine:
            self._engine.remove(aid)
        return self._ok()

    def monitor_stop_all(self):
        if self._engine:
            self._engine.clear()
            self._engine.stop()
        return self._ok()

    def monitor_status(self):
        if self._engine:
            return self._ok(**self._engine.get_status())
        return self._ok(running=False, slots=self._monitor_slots, total=0,
                        sweep_sec=0, accounts={}, nicknames={})

    def monitor_events(self, cursor=0):
        if self._engine:
            return self._ok(**self._engine.get_events(cursor))
        return self._ok(items=[], cursor=0)

    def monitor_start_all(self):
        """从数据库恢复监控名单并启动引擎。"""
        cur = self.conn.execute(
            "SELECT account_id, nickname FROM monitor_targets")
        rows = cur.fetchall()
        if not rows:
            return self._ok(started=False, count=0)
        eng = self._get_engine()
        for r in rows:
            eng.add(r["account_id"], r["nickname"] or "")
        return self._ok(started=True, count=len(rows))

    def monitor_configure(self, slots):
        try:
            slots = max(1, min(16, int(slots)))
        except (TypeError, ValueError):
            return self._err("槽位数必须是 1~16 的整数")
        if self._engine:
            self._engine.stop()
            self._engine = None
        self._monitor_slots = slots
        return self._ok(slots=slots)

    def account_sync_cred(self, aid):
        """从已登录的手动浏览器导出监控凭证。"""
        aid = int(aid)
        ab = self._get_actor(aid)
        if ab is None:
            return self._err("请先在账号页启动浏览器并完成扫码登录")
        try:
            path = ab.call(lambda b: prof.export_storage_state(b.ctx, aid),
                           timeout=60)
            return self._ok(path=path)
        except Exception as e:
            return self._err(f"导出失败: {e}")

    def schedules_list(self):
        return self._ok(items=sched.list_schedules(self.conn))

    def schedule_create(self, data):
        try:
            sid = sched.create_schedule(
                self.conn, int(data["draft_id"]),
                [int(a) for a in data.get("account_ids", [])],
                data.get("scheduled_at", ""), note=data.get("note", ""))
            return self._ok(id=sid)
        except (ValueError, KeyError) as e:
            return self._err(e)

    def schedule_cancel(self, sid):
        return self._ok(done=sched.set_status(self.conn, int(sid),
                                              "cancelled"))

    def schedule_delete(self, sid):
        return self._ok(done=sched.delete_schedule(self.conn, int(sid)))

    def start_reminder(self):
        if self._reminder_started:
            return
        self._reminder_started = True
        threading.Thread(target=self._reminder_loop, daemon=True).start()

    def _reminder_loop(self):
        time.sleep(20)
        last_fd_log = 0
        while True:
            try:
                conn = get_conn(self.db_path)
                for s in sched.due_schedules(conn):
                    ans = self.pm.ask(
                        "choice", "⏰ 发布时间到",
                        f"草稿【{s['draft_title'] or s['draft_id']}】\n"
                        f"{s['scheduled_at']} 的排期到了，"
                        f"共 {len(s['account_ids'])} 个账号。",
                        ["开始发布", "10分钟后提醒", "取消排期"])
                    if ans == "开始发布":
                        response = self.publish_start(s["draft_id"], s["account_ids"])
                        if response["ok"]:
                            sched.set_status(conn, s["id"], "done")
                        else:
                            sched.snooze(conn, s["id"], minutes=10)
                            self._log(response["error"])
                    elif ans == "取消排期":
                        sched.set_status(conn, s["id"], "cancelled")
                    else:
                        sched.snooze(conn, s["id"], minutes=10)
                conn.close()
                # 每小时记一次 fd 数：WAL 多连接 close 有缓慢泄漏，
                # 若持续增长超 200 则告警（v2.8 做 Database Worker 根治）
                now = time.time()
                if now - last_fd_log > 3600:
                    last_fd_log = now
                    nfd = self._fd_count()
                    log_event(task="sys", stage="health",
                              status="warn" if nfd > 200 else "ok",
                              msg=f"fd_count={nfd}")
            except Exception:
                pass
            time.sleep(20)

    @staticmethod
    def _fd_count():
        """当前进程打开的 fd 数（跨平台；失败返回 -1）。"""
        try:
            return len(os.listdir("/proc/self/fd"))
        except Exception:
            pass
        try:
            import subprocess
            out = subprocess.run(
                ["lsof", "-p", str(os.getpid())],
                capture_output=True, text=True, timeout=10).stdout
            return max(0, len(out.strip().splitlines()) - 1)
        except Exception:
            return -1

    def phrases_list(self, category=None):
        return self._ok(items=phr.list_phrases(self.conn,
                                               category=category or None))

    def phrase_save(self, data):
        try:
            data = data or {}
            if data.get("id"):
                done = phr.update_phrase(self.conn, int(data["id"]), data)
                return self._ok(done=done)
            pid = phr.create_phrase(self.conn, data.get("category", ""),
                                    data.get("title", ""),
                                    data.get("content", ""))
            return self._ok(id=pid)
        except ValueError as e:
            return self._err(e)

    def phrase_delete(self, pid):
        return self._ok(done=phr.delete_phrase(self.conn, int(pid)))

    def _ai_get(self, key, default=""):
        cur = self.conn.execute("SELECT value FROM app_settings WHERE key=?",
                                (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def _ai_set(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value) VALUES(?,?)",
            (key, value or ""))
        self.conn.commit()

    def ai_settings_get(self):
        key = self._ai_get("deepseek_key")
        masked = ("****" + key[-4:]) if len(key) > 4 else ""
        return self._ok(has_key=bool(key), masked=masked,
                        business_context=self._ai_get("business_context"))

    def ai_settings_save(self, api_key, business_context):
        if api_key is not None:
            self._ai_set("deepseek_key", api_key.strip())
        if business_context is not None:
            self._ai_set("business_context", business_context.strip())
        return self._ok(msg="已保存")

    def ai_settings_test(self):
        from . import ai_reply as ai_mod
        ok, msg = ai_mod.test_connection(self._ai_get("deepseek_key"))
        return self._ok(msg=msg) if ok else self._err(msg)

    def ai_draft_reply(self, aid, user_id):
        """为指定会话生成 AI 回复草稿（不发送）。"""
        from . import ai_reply as ai_mod
        aid = int(aid)
        ab, err = self._chat_actor_or_err(aid)
        if err:
            return err
        sel = self._msg_sel()

        def _read(b):
            page = b.chat_page
            if page is None or page.is_closed():
                raise RuntimeError("请先点击「开始监控」打开私信页")
            msg_mod.open_conversation(page, user_id)
            thread = msg_mod.read_thread(page, sel) or []
            # 取最近 10 条组装历史
            hist = []
            last_user_msg = ""
            for m in thread[-10:]:
                t = (m.get("text") or "").strip()
                if not t:
                    continue
                is_me = m.get("is_me")
                hist.append(("assistant" if is_me else "user", t))
                if not is_me:
                    last_user_msg = t
            return hist, last_user_msg

        try:
            hist, last_msg = ab.call(_read, timeout=60)
        except Exception as e:
            return self._err(str(e) or "读取会话失败")
        if not last_msg:
            return self._err("没有客户新消息")
        reply, err = ai_mod.generate_reply(
            self._ai_get("deepseek_key"),
            self._ai_get("business_context"),
            hist, last_msg)
        if err:
            return self._err(err)
        return self._ok(draft=reply)

    def records_list(self):
        return self._ok(items=rec_mod.list_records(self.conn))

    def system_logs(self, cursor=0, limit=200, only_errors=False):
        """系统日志：返回最近日志，报错标红由前端按 status/关键词渲染。"""
        from .log import get_logs
        items, new_cursor = get_logs(int(cursor or 0), int(limit or 200))
        if only_errors:
            items = [it for it in items
                     if ("status=failed" in it["line"]
                         or "status=warning" in it["line"]
                         or "error=" in it["line"])]
        return self._ok(items=items, cursor=new_cursor)

    def system_logs_export(self):
        """导出全部日志为文本文件（发给开发者定位问题）。"""
        from .log import LOG_PATH, LOG_DIR
        import glob
        lines = []
        # 历史轮转文件 + 当前文件，按时间顺序
        paths = sorted(glob.glob(os.path.join(LOG_DIR, "app.log*")))
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8",
                          errors="ignore") as f:
                    lines.append(f"===== {os.path.basename(p)} =====\n")
                    lines.append(f.read())
                    lines.append("\n")
            except OSError:
                pass
        if not lines:
            return self._err("暂无日志文件")
        fname = ("系统日志_" +
                 datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
        return self._ok(filename=fname, content="".join(lines))

    def record_screenshot(self, rid):
        """返回发布记录的截图（base64）。"""
        cur = self.conn.execute(
            "SELECT screenshot FROM publish_records WHERE id=?", (int(rid),))
        row = cur.fetchone()
        if not row or not row["screenshot"]:
            return self._err("该记录没有截图")
        path = row["screenshot"]
        # 安全：只允许 error_shots 目录下的文件
        ap = os.path.abspath(path)
        base = os.path.abspath(ERROR_SHOTS_DIR)
        if not ap.startswith(base) or not os.path.exists(ap):
            return self._err("截图文件不存在")
        import base64
        with open(ap, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return self._ok(b64=b64)

    def records_export_csv(self):
        import io as _io
        import csv as _csv
        rows = rec_mod.list_records(self.conn, limit=100000)
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(["id", "账号", "标题", "图片数", "状态", "时间"])
        for r in rows:
            w.writerow([r["id"], r["nickname"] or "", r["title_used"],
                        r["image_count"], r["status"], r["created_at"]])
        return self._ok(csv=buf.getvalue(), count=len(rows))

    def records_clear(self):
        rec_mod.clear_records(self.conn)
        return self._ok()

    def sensitive_check(self, text):
        return self._ok(hits=sens.check_with_db(text or "", self.conn))

    def custom_words_list(self):
        return self._ok(items=sens.get_custom_words(self.conn))

    def custom_words_save(self, words):
        words = [w.strip() for w in (words or []) if w.strip()]
        existing = set(sens.get_custom_words(self.conn))
        for w in words:
            if w not in existing:
                try:
                    sens.add_custom_word(self.conn, w)
                except ValueError:
                    pass
        for w in existing - set(words):
            sens.remove_custom_word(self.conn, w)
        self.conn.commit()
        return self._ok(count=len(words))

