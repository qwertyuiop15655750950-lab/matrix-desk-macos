# -*- coding: utf-8 -*-
"""矩阵工作台桌面 GUI（tkinter，深色主题）。
页签：账号 / 内容库 / 大字报 / 发布 / 话术库 / 发布记录 / 设置"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import accounts as acct
from . import phrases as phr
from . import poster as poster_mod
from . import profiles as prof
from . import publisher as pub
from . import records as rec_mod
from . import rewriter
from . import schedules as sched
from . import sensitive as sens
from .config import (APP_NAME, APP_VERSION, DATA_DIR, DB_PATH, MEDIA_DIR, POSTER_DIR,                     SELECTORS_PATH, ensure_dirs, find_cjk_font, load_selectors, PAGE_GOTO_TIMEOUT,)
from .db import get_conn, init_db
from .session import AccountBrowser
from .log import log_event

BG = "#1e2126"
BG2 = "#262b33"
FG = "#e8eaed"
MUTED = "#9aa0a6"
ACCENT = "#2f9e8f"
ACCENT_DARK = "#247a6f"


class UiBridge:
    """工作线程 -> 主线程 的对话框桥接（tkinter 对话框必须在主线程弹）。"""

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.resp = {}
        self.root.after(120, self._poll)

    def ask(self, kind, title="提示", msg=""):
        req_id = object()
        ev = threading.Event()
        self.q.put((req_id, kind, title, msg, ev))
        ev.wait()
        return self.resp.pop(req_id, None)

    def _poll(self):
        while True:
            try:
                req_id, kind, title, msg, ev = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                if kind == "confirm":
                    r = messagebox.askyesno(title, msg)
                elif kind == "choice3":
                    r = self._choice3_dialog(title, msg)
                elif kind == "info":
                    messagebox.showinfo(title, msg)
                    r = True
                elif kind == "warn":
                    messagebox.showwarning(title, msg)
                    r = True
                else:
                    messagebox.showerror(title, msg)
                    r = False
            except Exception:
                r = False
            self.resp[req_id] = r
            ev.set()
        self.root.after(120, self._poll)

    def _choice3_dialog(self, title, msg):
        """三选一对话框（排期到点提醒用），返回 go/later/cancel。"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=BG)
        ttk.Label(win, text=msg, wraplength=420,
                  font=("", 12)).pack(padx=18, pady=14)
        result = {"v": "later"}
        fr = ttk.Frame(win)
        fr.pack(pady=10)
        for text, val in [("开始发布", "go"), ("10分钟后提醒", "later"),
                          ("取消排期", "cancel")]:
            ttk.Button(fr, text=text,
                       command=lambda v=val: (result.__setitem__("v", v),
                                              win.destroy()),
                       style="Accent.TButton" if val == "go" else "TButton"
                       ).pack(side="left", padx=8)
        win.grab_set()
        self.root.wait_window(win)
        return result["v"]


class MatrixDeskApp:
    def __init__(self, root, db_path=None):
        self.root = root
        self.db_path = db_path or DB_PATH
        ensure_dirs()
        init_db(self.db_path)
        self.conn = get_conn(self.db_path)
        phr.seed_default_phrases(self.conn)
        self.bridge = UiBridge(root)
        # 每账号一个专属浏览器线程；Playwright 对象只活在 Actor 线程内，
        # 外部一律走 actor.call()，禁止跨线程拿 ctx/page（规则 2）
        self._actors = {}
        self._actors_lock = threading.Lock()
        self._style()
        root.title(f"{APP_NAME} v{APP_VERSION}")
        root.geometry("1080x720")
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)
        self.tabs = {}
        for name, builder in [
            ("账号", self._build_accounts),
            ("内容库", self._build_drafts),
            ("大字报", self._build_poster),
            ("发布", self._build_publish),
            ("排期", self._build_schedules),
            ("话术库", self._build_phrases),
            ("发布记录", self._build_records),
            ("设置", self._build_settings),
        ]:
            f = ttk.Frame(self.nb, style="TFrame")
            self.nb.add(f, text=name)
            self.tabs[name] = f
            builder(f)
        threading.Thread(target=self._reminder_loop, daemon=True).start()

    def _style(self):
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=FG, font=("", 11))
        s.configure("Muted.TLabel", foreground=MUTED, font=("", 10))
        s.configure("TButton", background=BG2, foreground=FG, font=("", 11),
                    padding=6)
        s.map("TButton", background=[("active", ACCENT_DARK)])
        s.configure("Accent.TButton", background=ACCENT, foreground="white",
                    font=("", 11, "bold"), padding=8)
        s.map("Accent.TButton", background=[("active", ACCENT_DARK)])
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", background=BG2, foreground=FG,
                    font=("", 12), padding=(14, 8))
        s.map("TNotebook.Tab", background=[("selected", ACCENT)],
              foreground=[("selected", "white")])
        s.configure("Treeview", background=BG2, foreground=FG,
                    fieldbackground=BG2, font=("", 11), rowheight=26)
        s.configure("Treeview.Heading", background=BG2, foreground=FG,
                    font=("", 11, "bold"))
        s.configure("TEntry", fieldbackground=BG2, foreground=FG)
        s.configure("TCombobox", fieldbackground=BG2, foreground=FG)

    def _tree(self, parent, columns, widths=None):
        tree = ttk.Treeview(parent, columns=columns, show="headings",
                            selectmode="extended")
        for i, c in enumerate(columns):
            tree.heading(c, text=c)
            tree.column(c, width=(widths[i] if widths else 120),
                        anchor="center")
        tree.pack(fill="both", expand=True, padx=6, pady=6)
        return tree

    def _btnrow(self, parent, buttons):
        fr = ttk.Frame(parent)
        fr.pack(fill="x", padx=6, pady=4)
        for text, cmd, accent in buttons:
            ttk.Button(fr, text=text, command=cmd,
                       style="Accent.TButton" if accent else "TButton"
                       ).pack(side="left", padx=4)
        return fr

    # ---------- 账号 ----------
    def _build_accounts(self, f):
        self.acct_tree = self._tree(
            f, ["id", "昵称", "小红书ID", "分组", "代理", "备注"],
            [50, 150, 150, 120, 200, 220])
        self._btnrow(f, [
            ("新增", self._acct_add, True),
            ("编辑", self._acct_edit, False),
            ("删除", self._acct_del, False),
            ("启动浏览器", self._acct_launch, False),
            ("发布页自检", self._acct_selftest, False),
            ("导入", self._acct_import, False),
            ("导出", self._acct_export, False),
            ("刷新", self._acct_refresh, False),
        ])
        self._acct_refresh()

    def _acct_refresh(self):
        for r in self.acct_tree.get_children():
            self.acct_tree.delete(r)
        for a in acct.list_accounts(self.conn):
            proxy = f"{a['proxy_host']}:{a['proxy_port']}" if a[
                "proxy_host"] else "未配置"
            self.acct_tree.insert("", "end", values=(
                a["id"], a["nickname"], a["xhs_id"], a["group_name"], proxy,
                a["notes"]))

    def _acct_selected(self):
        sel = self.acct_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个账号")
            return None
        return int(self.acct_tree.item(sel[0])["values"][0])

    def _acct_form(self, data=None):
        data = data or {}
        win = tk.Toplevel(self.root)
        win.title("账号信息")
        win.configure(bg=BG)
        fields = [("nickname", "昵称*", ""), ("xhs_id", "小红书ID", ""),
                  ("group_name", "分组", "默认分组"),
                  ("proxy_scheme", "代理协议(http/socks5)", "http"),
                  ("proxy_host", "代理IP(美国住宅IP)", ""),
                  ("proxy_port", "代理端口", ""),
                  ("proxy_user", "代理用户名", ""),
                  ("proxy_pass", "代理密码", ""),
                  ("notes", "备注", "")]
        vars_ = {}
        for i, (k, label, default) in enumerate(fields):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="e",
                                           padx=8, pady=4)
            v = tk.StringVar(value=str(data.get(k, default) or default))
            ttk.Entry(win, textvariable=v, width=36).grid(
                row=i, column=1, padx=8, pady=4)
            vars_[k] = v
        result = {}

        def ok():
            try:
                d = {k: v.get().strip() for k, v in vars_.items()}
                d["proxy_port"] = int(d["proxy_port"] or 0)
                result.update(d)
                win.destroy()
            except ValueError:
                messagebox.showerror("错误", "代理端口必须是数字",
                                     parent=win)

        ttk.Button(win, text="保存", command=ok,
                   style="Accent.TButton").grid(row=len(fields), column=0,
                                               columnspan=2, pady=10)
        win.grab_set()
        self.root.wait_window(win)
        return result or None

    def _acct_add(self):
        d = self._acct_form()
        if d:
            try:
                acct.create_account(self.conn, d)
                self._acct_refresh()
            except ValueError as e:
                messagebox.showerror("错误", str(e))

    def _acct_edit(self):
        aid = self._acct_selected()
        if aid is None:
            return
        d = self._acct_form(acct.get_account(self.conn, aid))
        if d and acct.update_account(self.conn, aid, d):
            self._acct_refresh()

    def _acct_del(self):
        aid = self._acct_selected()
        if aid is None:
            return
        a = acct.get_account(self.conn, aid)
        if messagebox.askyesno("确认", f"删除账号【{a['nickname']}】？\n"
                                      "浏览器 profile 目录将保留，可手动清理。"):
            acct.delete_account(self.conn, aid)
            self._acct_refresh()

    def _acct_launch(self):
        aid = self._acct_selected()
        if aid is None:
            return
        threading.Thread(target=self._launch_worker, args=(aid,),
                         daemon=True).start()
        messagebox.showinfo("提示", "正在启动浏览器，请稍候…\n"
                                    "如未登录请在打开的浏览器中扫码。")

    def _get_actor(self, aid):
        """取该账号的浏览器线程（没有或已死则返回 None，不创建）。"""
        with self._actors_lock:
            ab = self._actors.get(int(aid))
        return ab if ab is not None and not ab.is_dead() else None

    def _ensure_actor(self, aid):
        """取或创建该账号的浏览器线程。已死的 Actor 自动重建。"""
        aid = int(aid)
        with self._actors_lock:
            ab = self._actors.get(aid)
            if ab is not None:
                if ab.is_dead():
                    self._actors.pop(aid, None)
                    log_event(task="browser", aid=aid, stage="actor",
                              status="rebuilt", msg="浏览器已死，自动重建")
                else:
                    return ab
            conn = get_conn(self.db_path)
            try:
                a = acct.get_account(conn, aid)
            finally:
                conn.close()
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

    def _close_actors(self):
        with self._actors_lock:
            items = list(self._actors.items())
            self._actors.clear()
        for aid, ab in items:
            try:
                ab.stop()
            except Exception:
                pass

    def _on_close(self):
        self._close_actors()
        try:
            self.conn.close()
        except Exception:
            pass
        self.root.destroy()

    def _launch_worker(self, aid):
        conn = get_conn(self.db_path)
        try:
            a = acct.get_account(conn, aid)
        finally:
            conn.close()
        if not a:
            return
        try:
            ab = self._ensure_actor(aid)
            ok = ab.call(lambda b: prof.ensure_login(b.ctx), timeout=300)
            self.bridge.ask("info" if ok else "warn", "登录状态",
                            "登录成功，会话已保存，浏览器保持运行。\n"
                            "可到“账号”页做发布页自检；用完请到“设置”页关闭。" if ok
                            else "登录超时，请重试。")
        except Exception as e:
            self.bridge.ask("error", "启动失败",
                            f"{e.__class__.__name__}: {e}")

    def _selftest_worker(self, aid):
        conn = get_conn(self.db_path)
        try:
            a = acct.get_account(conn, aid)
        finally:
            conn.close()
        if not a:
            return
        try:
            ab = self._ensure_actor(aid)

            def _flow(b):
                if not prof.ensure_login(b.ctx):
                    raise RuntimeError("LOGIN_TIMEOUT")
                page = b.ctx.new_page()
                try:
                    try:
                        page.goto(pub.publish_page_url("image"),
                                  timeout=PAGE_GOTO_TIMEOUT)
                    except Exception as e:
                        if "interrupted by another navigation" not in str(e):
                            raise
                    return pub.selector_selftest(page)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass

            res = ab.call(_flow, timeout=300)
            lines = [f"{'✅' if v else '❌'} {k}: {v}"
                     for k, v in res.items()]
            self.bridge.ask("info", "选择器自检结果",
                            "在当前发布页检测：\n\n" + "\n".join(lines) +
                            "\n\n❌ 表示该选择器在真站未命中，\n"
                            "请把结果发回给开发者校准。")
        except RuntimeError as e:
            if "LOGIN_TIMEOUT" in str(e):
                self.bridge.ask("warn", "登录超时", "请先完成扫码登录再自检。")
            else:
                self.bridge.ask("error", "自检失败",
                                f"{e.__class__.__name__}: {e}")
        except Exception as e:
            self.bridge.ask("error", "自检失败",
                            f"{e.__class__.__name__}: {e}")

    def _acct_selftest(self):
        aid = self._acct_selected()
        if aid is None:
            return
        threading.Thread(target=self._selftest_worker, args=(aid,),
                         daemon=True).start()
        messagebox.showinfo("提示", "正在打开并检测发布页，请稍候…")

    def _acct_import(self):
        p = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("CSV", "*.csv")])
        if not p:
            return
        fmt = "json" if p.endswith(".json") else "csv"
        with open(p, encoding="utf-8") as f:
            r = acct.import_accounts(self.conn, f.read(), fmt)
        messagebox.showinfo(
            "导入完成",
            f"新增 {r['added']} 个，跳过 {r['skipped']} 个\n" +
            ("\n".join(r["errors"][:5]) if r["errors"] else ""))
        self._acct_refresh()

    def _acct_export(self):
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        with open(p, "w", encoding="utf-8") as f:
            f.write(acct.export_accounts(self.conn, "json"))
        messagebox.showinfo("提示", f"已导出到 {p}")

    # ---------- 内容库 ----------
    def _build_drafts(self, f):
        self.draft_tree = self._tree(
            f, ["id", "标题", "模式", "话题", "更新时间"],
            [50, 300, 90, 300, 160])
        self._btnrow(f, [
            ("新增草稿", self._draft_add, True),
            ("编辑", self._draft_edit, False),
            ("删除", self._draft_del, False),
            ("刷新", self._draft_refresh, False),
        ])
        self._draft_refresh()

    def _draft_refresh(self):
        for r in self.draft_tree.get_children():
            self.draft_tree.delete(r)
        cur = self.conn.execute(
            "SELECT id, title, mode, topics, updated_at FROM drafts ORDER BY id")
        for row in cur.fetchall():
            mode = "大字报" if row["mode"] == "poster" else "图文"
            self.draft_tree.insert("", "end", values=(
                row["id"], row["title"], mode, row["topics"],
                row["updated_at"]))

    def _draft_selected(self):
        sel = self.draft_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一条草稿")
            return None
        return int(self.draft_tree.item(sel[0])["values"][0])

    def _draft_get(self, did):
        cur = self.conn.execute("SELECT * FROM drafts WHERE id=?", (did,))
        row = cur.fetchone()
        return dict(row) if row else None

    def _draft_form(self, data=None):
        data = data or {"mode": "images", "template": "rice"}
        win = tk.Toplevel(self.root)
        win.title("草稿")
        win.configure(bg=BG)
        win.geometry("640x560")
        vars_ = {
            "title": tk.StringVar(value=data.get("title", "")),
            "topics": tk.StringVar(value=data.get("topics", "")),
            "mode": tk.StringVar(value=data.get("mode", "images")),
            "template": tk.StringVar(value=data.get("template", "rice") or "rice"),
        }
        ttk.Label(win, text="标题").pack(anchor="w", padx=8)
        ttk.Entry(win, textvariable=vars_["title"], width=70).pack(
            padx=8, pady=2, fill="x")
        ttk.Label(win, text="正文").pack(anchor="w", padx=8)
        body_txt = tk.Text(win, height=12, bg=BG2, fg=FG,
                           insertbackground=FG, font=("", 11))
        body_txt.pack(padx=8, pady=2, fill="both", expand=True)
        body_txt.insert("1.0", data.get("body", ""))
        ttk.Label(win, text="话题（空格分隔，如 #探店 #美食）").pack(
            anchor="w", padx=8)
        ttk.Entry(win, textvariable=vars_["topics"], width=70).pack(
            padx=8, pady=2, fill="x")
        fr = ttk.Frame(win)
        fr.pack(fill="x", padx=8, pady=4)
        ttk.Label(fr, text="模式").pack(side="left")
        ttk.Combobox(fr, textvariable=vars_["mode"], width=10,
                     values=["images", "poster"], state="readonly").pack(
            side="left", padx=4)
        ttk.Label(fr, text="大字报模板").pack(side="left", padx=(12, 0))
        ttk.Combobox(fr, textvariable=vars_["template"], width=14,
                     values=[t["id"] for t in poster_mod.list_templates()],
                     state="readonly").pack(side="left", padx=4)
        media = {"paths": json.loads(data.get("media_json", "[]") or "[]")}
        mlab = ttk.Label(win, text=f"图片/视频附件: {len(media['paths'])} 个",
                         style="Muted.TLabel")
        mlab.pack(anchor="w", padx=8)

        def pick_media():
            ps = filedialog.askopenfilenames(filetypes=[
                ("媒体", "*.png *.jpg *.jpeg *.mp4 *.mov")])
            if ps:
                media["paths"] = list(ps)
                mlab.config(text=f"图片/视频附件: {len(media['paths'])} 个")

        ttk.Button(fr, text="选择附件", command=pick_media).pack(
            side="left", padx=12)
        result = {}

        def ok():
            if not vars_["title"].get().strip():
                messagebox.showerror("错误", "标题不能为空", parent=win)
                return
            result.update({
                "title": vars_["title"].get().strip(),
                "body": body_txt.get("1.0", "end").strip(),
                "topics": vars_["topics"].get().strip(),
                "mode": vars_["mode"].get(),
                "template": vars_["template"].get(),
                "media_json": json.dumps(media["paths"],
                                         ensure_ascii=False),
            })
            win.destroy()

        ttk.Button(win, text="保存", command=ok,
                   style="Accent.TButton").pack(pady=8)
        win.grab_set()
        self.root.wait_window(win)
        return result or None

    def _draft_add(self):
        d = self._draft_form()
        if d:
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.conn.execute(
                "INSERT INTO drafts(title,body,topics,mode,media_json,template,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (d["title"], d["body"], d["topics"], d["mode"],
                 d["media_json"], d["template"], now, now))
            self.conn.commit()
            self._draft_refresh()

    def _draft_edit(self):
        did = self._draft_selected()
        if did is None:
            return
        d = self._draft_form(self._draft_get(did))
        if d:
            from datetime import datetime
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.conn.execute(
                "UPDATE drafts SET title=?,body=?,topics=?,mode=?,"
                "media_json=?,template=?,updated_at=? WHERE id=?",
                (d["title"], d["body"], d["topics"], d["mode"],
                 d["media_json"], d["template"], now, did))
            self.conn.commit()
            self._draft_refresh()

    def _draft_del(self):
        did = self._draft_selected()
        if did is None:
            return
        if messagebox.askyesno("确认", "删除这条草稿？"):
            self.conn.execute("DELETE FROM drafts WHERE id=?", (did,))
            self.conn.commit()
            self._draft_refresh()

    # ---------- 大字报 ----------
    def _build_poster(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="模板").pack(side="left")
        self.poster_tpl = tk.StringVar(value="rice - 米白深青")
        ttk.Combobox(top, textvariable=self.poster_tpl, width=16,
                     values=[f"{t['id']} - {t['name']}"
                             for t in poster_mod.list_templates()],
                     state="readonly").pack(side="left", padx=6)
        ttk.Label(top, text="标题（可选）").pack(side="left", padx=(12, 0))
        self.poster_title = tk.StringVar()
        ttk.Entry(top, textvariable=self.poster_title, width=30).pack(
            side="left", padx=6)
        ttk.Label(f, text="正文（你出文字，软件排版生成 1080x1440 图片）").pack(
            anchor="w", padx=8)
        self.poster_text = tk.Text(f, height=14, bg=BG2, fg=FG,
                                   insertbackground=FG, font=("", 12))
        self.poster_text.pack(padx=8, pady=4, fill="both", expand=True)
        self.poster_status = ttk.Label(f, text="", style="Muted.TLabel")
        self.poster_status.pack(anchor="w", padx=8)
        self._btnrow(f, [
            ("生成大字报", self._poster_generate, True),
            ("打开输出目录", self._poster_open_dir, False),
        ])

    def _poster_generate(self):
        text = self.poster_text.get("1.0", "end").strip()
        tpl = self.poster_tpl.get().split(" - ")[0]
        try:
            paths = poster_mod.generate_poster(
                text, template=tpl, title=self.poster_title.get(),
                out_dir=POSTER_DIR, basename="dzb")
            self.poster_status.config(
                text=f"已生成 {len(paths)} 张：{paths[0]}")
            messagebox.showinfo("完成",
                                f"生成 {len(paths)} 张大字报图片\n{paths[0]}")
        except (ValueError, RuntimeError) as e:
            messagebox.showerror("错误", str(e))

    def _poster_open_dir(self):
        import subprocess
        import sys
        p = POSTER_DIR
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", p])
            elif sys.platform.startswith("win"):
                os.startfile(p)  # noqa: S606
            else:
                subprocess.run(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ---------- 发布 ----------
    def _build_publish(self, f):
        ttk.Label(f, text="1. 选择草稿", font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(6, 0))
        self.pub_draft = tk.StringVar()
        self.pub_draft_box = ttk.Combobox(f, textvariable=self.pub_draft,
                                          width=80, state="readonly")
        self.pub_draft_box.pack(padx=8, pady=4, fill="x")
        ttk.Label(f, text="2. 选择要发布的账号（可多选）",
                  font=("", 12, "bold")).pack(anchor="w", padx=8)
        self.pub_acct_tree = self._tree(
            f, ["id", "昵称", "分组", "代理"], [60, 200, 150, 250])
        btn_fr = ttk.Frame(f)
        btn_fr.pack(fill="x", padx=6, pady=4)
        ttk.Button(btn_fr, text="刷新列表",
                   command=self._pub_refresh).pack(side="left", padx=4)
        ttk.Button(btn_fr, text="开始半自动批量发布",
                   command=self._publish_start,
                   style="Accent.TButton").pack(side="left", padx=4)
        self.pub_log = tk.Text(f, height=8, bg=BG2, fg=FG, font=("", 10),
                               state="disabled")
        self.pub_log.pack(padx=8, pady=4, fill="both", expand=True)
        self._pub_refresh()

    def _pub_refresh(self):
        for r in self.pub_acct_tree.get_children():
            self.pub_acct_tree.delete(r)
        for a in acct.list_accounts(self.conn):
            proxy = f"{a['proxy_host']}:{a['proxy_port']}" if a[
                "proxy_host"] else "未配置"
            self.pub_acct_tree.insert("", "end", values=(
                a["id"], a["nickname"], a["group_name"], proxy))
        cur = self.conn.execute(
            "SELECT id, title, mode FROM drafts ORDER BY id DESC")
        rows = cur.fetchall()
        self._draft_map = {f"[{r['id']}] {r['title']}"
                           f"({'大字报' if r['mode'] == 'poster' else '图文'})":
                           r["id"] for r in rows}
        self.pub_draft_box["values"] = list(self._draft_map.keys())
        if self._draft_map:
            self.pub_draft.set(list(self._draft_map.keys())[0])

    def _pub_log(self, msg):
        self.pub_log.config(state="normal")
        self.pub_log.insert("end", msg + "\n")
        self.pub_log.see("end")
        self.pub_log.config(state="disabled")

    def _publish_start(self):
        key = self.pub_draft.get()
        if not key or key not in self._draft_map:
            messagebox.showwarning("提示", "请先选择草稿")
            return
        sel = self.pub_acct_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个账号")
            return
        aids = [int(self.pub_acct_tree.item(s)["values"][0]) for s in sel]
        did = self._draft_map[key]
        if not messagebox.askyesno(
                "确认",
                f"将为 {len(aids)} 个账号依次填充发布内容，\n"
                f"每个账号都由你人工点击发布。开始吗？"):
            return
        threading.Thread(target=self._publish_worker, args=(did, aids),
                         daemon=True).start()

    def _publish_worker(self, did, aids):
        from datetime import datetime
        conn = get_conn(self.db_path)
        cur = conn.execute("SELECT * FROM drafts WHERE id=?", (did,))
        row = cur.fetchone()
        if not row:
            self.root.after(0, self._pub_log, "草稿不存在，发布终止")
            conn.close()
            return
        draft = dict(row)
        self.root.after(0, self._pub_log,
                        f"开始：草稿【{draft['title']}】→ {len(aids)} 个账号")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                for i, aid in enumerate(aids):
                    try:
                        a = acct.get_account(conn, aid)
                        if not a:
                            continue
                        var = rewriter.rewrite_variation(
                            draft["title"], draft["body"], draft["topics"],
                            seed=i)
                        hits = sens.check_with_db(
                            var["title"] + "\n" + var["body"], conn)
                        if hits:
                            words = "、".join(
                                {h["word"] for h in hits})
                            go = self.bridge.ask(
                                "confirm", "敏感词提醒",
                                f"账号【{a['nickname']}】的内容命中敏感词：{words}\n"
                                f"继续发布该账号吗？")
                            if not go:
                                self.root.after(
                                    0, self._pub_log,
                                    f"跳过【{a['nickname']}】（敏感词）")
                                continue
                        if draft["mode"] == "poster":
                            kind = "image"
                            try:
                                imgs = poster_mod.generate_poster(
                                    var["body"], template=draft["template"] or
                                    "rice", title=var["title"],
                                    out_dir=POSTER_DIR,
                                    basename=f"a{aid}_d{did}")
                            except (ValueError, RuntimeError) as e:
                                self.bridge.ask("error", "大字报生成失败",
                                                str(e))
                                continue
                        else:
                            imgs = json.loads(draft["media_json"] or "[]")
                            kind = ("video" if any(
                                str(m).lower().endswith((".mp4", ".mov"))
                                for m in imgs) else "image")
                        self.root.after(0, self._pub_log,
                                        f"正在处理【{a['nickname']}】…")
                        ab = self._get_actor(aid)
                        if ab is not None:
                            # 手动浏览器：页面操作走该账号专属 Actor 线程
                            def _flow(b):
                                if not prof.ensure_login(b.ctx):
                                    raise RuntimeError("LOGIN_TIMEOUT")
                                page = pub.prepare_page(b.ctx, kind=kind)
                                return pub.fill_publish_form(
                                    page, var["title"], var["body"],
                                    var["topics"], imgs)
                            try:
                                res = ab.call(_flow, timeout=300)
                            except RuntimeError as e:
                                if "LOGIN_TIMEOUT" in str(e):
                                    self.bridge.ask(
                                        "warn", "登录超时",
                                        f"【{a['nickname']}】扫码登录超时，已跳过")
                                    continue
                                self.root.after(
                                    0, self._pub_log,
                                    f"【{a['nickname']}】处理异常已跳过：{e}"[:60])
                                log_event(task="publish", aid=aid, stage="fill",
                                          status="failed", error=e)
                                continue
                            except Exception as e:
                                self.root.after(
                                    0, self._pub_log,
                                    f"【{a['nickname']}】处理异常已跳过：{e}"[:60])
                                log_event(task="publish", aid=aid, stage="fill",
                                          status="failed", error=e)
                                continue
                        else:
                            # 临时代理浏览器：本线程内创建、使用、关闭
                            ctx, _ = prof.launch_account(pw, a, headless=True)
                            try:
                                if not prof.ensure_login(ctx):
                                    self.bridge.ask(
                                        "warn", "登录超时",
                                        f"【{a['nickname']}】扫码登录超时，已跳过")
                                    continue
                                page = pub.prepare_page(ctx, kind=kind)
                                res = pub.fill_publish_form(
                                    page, var["title"], var["body"],
                                    var["topics"], imgs)
                            finally:
                                try:
                                    ctx.close()
                                except Exception:
                                    pass
                        if res["warnings"]:
                            self.root.after(
                                0, self._pub_log,
                                f"【{a['nickname']}】警告: "
                                f"{'; '.join(res['warnings'])}")
                        if not res["ok"]:
                            self.bridge.ask(
                                "warn", "填充异常",
                                f"【{a['nickname']}】表单填充不完整，"
                                f"请检查浏览器页面后手动补填。")
                        done = self.bridge.ask(
                            "confirm", "人工确认发布",
                            f"已为【{a['nickname']}】填好发布内容。\n"
                            f"请在浏览器中检查并点击“发布”。\n\n"
                            f"点“是”= 已发布（记入发布记录）\n"
                            f"点“否”= 跳过该账号")
                        if done:
                            now = datetime.now().strftime(
                                "%Y-%m-%d %H:%M:%S")
                            conn.execute(
                                "INSERT INTO publish_records(account_id,"
                                "draft_id,title_used,image_count,status,"
                                "created_at) VALUES(?,?,?,?,?,?)",
                                (aid, did, var["title"], len(imgs),
                                 "done", now))
                            conn.commit()
                            self.root.after(0, self._pub_log,
                                            f"【{a['nickname']}】已记录发布")
                        else:
                            self.root.after(0, self._pub_log,
                                            f"【{a['nickname']}】已跳过")

                    except Exception as e:
                        self.root.after(
                            0, self._pub_log,
                            f"【账号{aid}】处理异常已跳过")
                        log_event(task="publish", aid=aid,
                                  stage="gui_fill", status="failed",
                                  error=e)
                        continue
        except Exception as e:
            self.bridge.ask("error", "发布流程出错",
                            f"{e.__class__.__name__}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
        self.root.after(0, self._pub_log, "批量发布流程结束")
        self.bridge.ask("info", "完成", "批量发布流程结束")

    # ---------- 话术库 ----------
    def _build_phrases(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=4)
        ttk.Label(top, text="分类筛选").pack(side="left")
        self.ph_cat = tk.StringVar(value="全部")
        self.ph_cat_box = ttk.Combobox(top, textvariable=self.ph_cat,
                                       width=14, state="readonly")
        self.ph_cat_box.pack(side="left", padx=6)
        self.ph_cat_box.bind("<<ComboboxSelected>>",
                             lambda e: self._ph_refresh())
        self.ph_tree = self._tree(f, ["id", "分类", "标题"],
                                  [50, 120, 400])
        self.ph_tree.bind("<Double-1>", lambda e: self._ph_view())
        self._btnrow(f, [
            ("查看", self._ph_view, False),
            ("新增", self._ph_add, True),
            ("编辑", self._ph_edit, False),
            ("删除", self._ph_del, False),
            ("刷新", self._ph_refresh, False),
        ])
        self._ph_refresh()

    def _ph_refresh(self):
        cats = ["全部"] + sorted(
            {r["category"] for r in phr.list_phrases(self.conn)
             if r["category"]})
        self.ph_cat_box["values"] = cats
        if self.ph_cat.get() not in cats:
            self.ph_cat.set("全部")
        for r in self.ph_tree.get_children():
            self.ph_tree.delete(r)
        cat = None if self.ph_cat.get() == "全部" else self.ph_cat.get()
        for p in phr.list_phrases(self.conn, category=cat):
            self.ph_tree.insert("", "end",
                                values=(p["id"], p["category"], p["title"]))

    def _ph_selected(self):
        sel = self.ph_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一条话术")
            return None
        return int(self.ph_tree.item(sel[0])["values"][0])

    def _ph_form(self, data=None):
        data = data or {}
        win = tk.Toplevel(self.root)
        win.title("话术")
        win.configure(bg=BG)
        win.geometry("560x420")
        ttk.Label(win, text="分类").pack(anchor="w", padx=8)
        cat_v = tk.StringVar(value=data.get("category", "私信回复"))
        ttk.Entry(win, textvariable=cat_v, width=40).pack(
            padx=8, pady=2, fill="x")
        ttk.Label(win, text="标题").pack(anchor="w", padx=8)
        title_v = tk.StringVar(value=data.get("title", ""))
        ttk.Entry(win, textvariable=title_v, width=40).pack(
            padx=8, pady=2, fill="x")
        ttk.Label(win, text="内容").pack(anchor="w", padx=8)
        txt = tk.Text(win, height=12, bg=BG2, fg=FG, insertbackground=FG,
                      font=("", 11))
        txt.pack(padx=8, pady=2, fill="both", expand=True)
        txt.insert("1.0", data.get("content", ""))
        result = {}

        def ok():
            if not title_v.get().strip() or not txt.get(
                    "1.0", "end").strip():
                messagebox.showerror("错误", "标题和内容不能为空",
                                     parent=win)
                return
            result.update({"category": cat_v.get().strip(),
                           "title": title_v.get().strip(),
                           "content": txt.get("1.0", "end").strip()})
            win.destroy()

        ttk.Button(win, text="保存", command=ok,
                   style="Accent.TButton").pack(pady=8)
        win.grab_set()
        self.root.wait_window(win)
        return result or None

    def _ph_view(self):
        pid = self._ph_selected()
        if pid is None:
            return
        p = phr.get_phrase(self.conn, pid)
        win = tk.Toplevel(self.root)
        win.title(p["title"])
        win.configure(bg=BG)
        txt = tk.Text(win, height=16, width=60, bg=BG2, fg=FG,
                      font=("", 12))
        txt.pack(padx=10, pady=10, fill="both", expand=True)
        txt.insert("1.0", p["content"])
        txt.config(state="disabled")

    def _ph_add(self):
        d = self._ph_form()
        if d:
            try:
                phr.create_phrase(self.conn, d["category"], d["title"],
                                  d["content"])
                self._ph_refresh()
            except ValueError as e:
                messagebox.showerror("错误", str(e))

    def _ph_edit(self):
        pid = self._ph_selected()
        if pid is None:
            return
        d = self._ph_form(phr.get_phrase(self.conn, pid))
        if d and phr.update_phrase(self.conn, pid, d):
            self._ph_refresh()

    def _ph_del(self):
        pid = self._ph_selected()
        if pid is None:
            return
        if messagebox.askyesno("确认", "删除这条话术？"):
            phr.delete_phrase(self.conn, pid)
            self._ph_refresh()

    # ---------- 发布记录 ----------
    def _build_records(self, f):
        self.rec_tree = self._tree(
            f, ["id", "账号", "标题", "图片数", "状态", "时间"],
            [50, 150, 320, 70, 80, 160])
        self._btnrow(f, [("刷新", self._rec_refresh, False),
                         ("导出CSV", self._rec_export, False),
                         ("清空记录", self._rec_clear, False)])
        self._rec_refresh()

    def _rec_refresh(self):
        for r in self.rec_tree.get_children():
            self.rec_tree.delete(r)
        for row in rec_mod.list_records(self.conn):
            self.rec_tree.insert("", "end", values=(
                row["id"], row["nickname"], row["title_used"],
                row["image_count"], row["status"], row["created_at"]))

    def _rec_export(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv",
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        n = rec_mod.export_records_csv(self.conn, p)
        messagebox.showinfo("提示", f"已导出 {n} 条记录到 {p}")

    def _rec_clear(self):
        if messagebox.askyesno("确认", "清空全部发布记录？（账号和草稿不受影响）"):
            rec_mod.clear_records(self.conn)
            self._rec_refresh()

    # ---------- 设置 ----------
    def _build_settings(self, f):
        ttk.Label(f, text="环境信息", font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(8, 0))
        font = find_cjk_font() or "未找到"
        info = (f"数据目录: {DATA_DIR}\n"
                f"中文字体: {font}\n"
                f"选择器配置: {SELECTORS_PATH}")
        ttk.Label(f, text=info, style="Muted.TLabel").pack(
            anchor="w", padx=8, pady=4)
        self._btnrow(f, [
            ("关闭所有浏览器", self._close_browsers_btn, False),
            ("敏感词自查演示", self._sens_demo, False),
        ])
        ttk.Label(f, text="自定义敏感词（一行一个）",
                  font=("", 12, "bold")).pack(anchor="w", padx=8,
                                              pady=(8, 0))
        self.cw_text = tk.Text(f, height=6, bg=BG2, fg=FG,
                               insertbackground=FG, font=("", 11))
        self.cw_text.pack(padx=8, pady=4, fill="x")
        self._cw_refresh()
        self._btnrow(f, [("保存自定义敏感词", self._cw_save, True)])

    def _cw_refresh(self):
        words = sens.get_custom_words(self.conn)
        self.cw_text.delete("1.0", "end")
        self.cw_text.insert("1.0", "\n".join(words))

    def _cw_save(self):
        words = [w.strip() for w in
                 self.cw_text.get("1.0", "end").splitlines() if w.strip()]
        cur = self.conn.execute("SELECT word FROM custom_words")
        existing = {r["word"] for r in cur.fetchall()}
        for w in words:
            if w not in existing:
                try:
                    sens.add_custom_word(self.conn, w)
                except ValueError:
                    pass
        for w in existing - set(words):
            sens.remove_custom_word(self.conn, w)
        self.conn.commit()
        messagebox.showinfo("提示", f"已保存 {len(words)} 个自定义敏感词")

    def _sens_demo(self):
        sample = ("加微信私聊我，最低价全网第一，vx详谈，"
                  "点击跳转站外下单")
        hits = sens.check_with_db(sample, self.conn)
        msg = "\n".join(
            f"{h['word']}（{sens.CATEGORIES.get(h['category'], h['category'])}）"
            f" x{h['count']}" for h in hits) or "无命中"
        messagebox.showinfo("敏感词自查演示",
                            f"示例文本：{sample}\n\n命中：\n{msg}")

    def _close_browsers_btn(self):
        self._close_actors()
        messagebox.showinfo("提示", "已关闭所有账号浏览器。")

    def _sel_selftest(self):
        messagebox.showinfo(
            "提示",
            "请到“账号”页使用“发布页自检”按钮。")

    # ---------- 排期 ----------
    def _build_schedules(self, f):
        ttk.Label(f, text="新增排期", font=("", 12, "bold")).pack(
            anchor="w", padx=8, pady=(6, 0))
        top = ttk.Frame(f)
        top.pack(fill="x", padx=8, pady=4)
        ttk.Label(top, text="草稿").pack(side="left")
        self.sch_draft = tk.StringVar()
        self.sch_draft_box = ttk.Combobox(top, textvariable=self.sch_draft,
                                          width=38, state="readonly")
        self.sch_draft_box.pack(side="left", padx=6)
        ttk.Label(top, text="时间").pack(side="left", padx=(10, 0))
        from datetime import datetime, timedelta
        default_t = (datetime.now() + timedelta(days=1)).strftime(
            "%Y-%m-%d 09:00")
        self.sch_time = tk.StringVar(value=default_t)
        ttk.Entry(top, textvariable=self.sch_time, width=17).pack(
            side="left", padx=6)
        ttk.Label(top, text="备注").pack(side="left", padx=(10, 0))
        self.sch_note = tk.StringVar()
        ttk.Entry(top, textvariable=self.sch_note, width=18).pack(
            side="left", padx=6)
        ttk.Label(f, text="选择账号（可多选）").pack(anchor="w", padx=8)
        self.sch_acct_tree = self._tree(f, ["id", "昵称", "分组"],
                                        [60, 200, 150])
        self.sch_tree = self._tree(f, ["id", "草稿", "账号数", "时间",
                                       "状态", "备注"],
                                   [50, 280, 70, 150, 80, 200])
        self._btnrow(f, [
            ("新增排期", self._sch_add, True),
            ("取消排期", self._sch_cancel, False),
            ("删除", self._sch_del, False),
            ("刷新", self._sch_refresh, False),
        ])
        self._sch_refresh()

    def _sch_refresh(self):
        cur = self.conn.execute(
            "SELECT id, title FROM drafts ORDER BY id DESC")
        rows = cur.fetchall()
        self._sch_draft_map = {f"[{r['id']}] {r['title']}": r["id"]
                               for r in rows}
        self.sch_draft_box["values"] = list(self._sch_draft_map.keys())
        if self._sch_draft_map:
            self.sch_draft.set(list(self._sch_draft_map.keys())[0])
        for r in self.sch_acct_tree.get_children():
            self.sch_acct_tree.delete(r)
        for a in acct.list_accounts(self.conn):
            self.sch_acct_tree.insert("", "end", values=(
                a["id"], a["nickname"], a["group_name"]))
        for r in self.sch_tree.get_children():
            self.sch_tree.delete(r)
        for s in sched.list_schedules(self.conn):
            status_cn = {"pending": "待发布", "done": "已发布",
                         "cancelled": "已取消"}.get(s["status"], s["status"])
            self.sch_tree.insert("", "end", values=(
                s["id"], s["draft_title"] or "", len(s["account_ids"]),
                s["scheduled_at"], status_cn, s["note"]))

    def _sch_add(self):
        key = self.sch_draft.get()
        if not key or key not in self._sch_draft_map:
            messagebox.showwarning("提示", "请先选择草稿")
            return
        sel = self.sch_acct_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请至少选择一个账号")
            return
        aids = [int(self.sch_acct_tree.item(x)["values"][0]) for x in sel]
        try:
            sid = sched.create_schedule(
                self.conn, self._sch_draft_map[key], aids,
                self.sch_time.get(), note=self.sch_note.get())
        except ValueError as e:
            messagebox.showerror("错误", str(e))
            return
        messagebox.showinfo("提示", f"排期已创建（#{sid}），到点会弹窗提醒")
        self._sch_refresh()

    def _sch_selected(self):
        sel = self.sch_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一条排期")
            return None
        return int(self.sch_tree.item(sel[0])["values"][0])

    def _sch_cancel(self):
        sid = self._sch_selected()
        if sid is None:
            return
        sched.set_status(self.conn, sid, "cancelled")
        self._sch_refresh()

    def _sch_del(self):
        sid = self._sch_selected()
        if sid is None:
            return
        if messagebox.askyesno("确认", "删除这条排期？"):
            sched.delete_schedule(self.conn, sid)
            self._sch_refresh()

    def _reminder_loop(self):
        """后台线程：每 20 秒检查到点排期，弹窗提醒。"""
        import time
        time.sleep(20)
        while True:
            try:
                conn = get_conn(self.db_path)
                for s in sched.due_schedules(conn):
                    choice = self.bridge.ask(
                        "choice3", "⏰ 发布时间到",
                        f"草稿【{s['draft_title'] or s['draft_id']}】\n"
                        f"{s['scheduled_at']} 的排期到了，"
                        f"共 {len(s['account_ids'])} 个账号。")
                    if choice == "go":
                        sched.set_status(conn, s["id"], "done")
                        did, aids = s["draft_id"], s["account_ids"]
                        threading.Thread(
                            target=self._publish_worker,
                            args=(did, aids), daemon=True).start()
                    elif choice == "cancel":
                        sched.set_status(conn, s["id"], "cancelled")
                    else:
                        sched.snooze(conn, s["id"], minutes=10)
                conn.close()
            except Exception:
                pass
            time.sleep(20)



def main(db_path=None):
    ensure_dirs()
    root = tk.Tk()
    MatrixDeskApp(root, db_path=db_path)
    root.mainloop()


if __name__ == "__main__":
    main()
