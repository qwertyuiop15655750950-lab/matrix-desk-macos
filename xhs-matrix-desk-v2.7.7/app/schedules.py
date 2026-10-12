# -*- coding: utf-8 -*-
"""发布排期：草稿 + 账号 + 时间，到点提醒。只用标准库。"""
import json
from datetime import datetime, timedelta

from .db import _now

TIME_FMT = "%Y-%m-%d %H:%M"
STATUSES = ("pending", "done", "cancelled")


def parse_time(s):
    """解析 'YYYY-MM-DD HH:MM'（兼容带秒），返回 datetime；格式错误抛 ValueError。"""
    s = (s or "").strip()
    for fmt in (TIME_FMT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError("时间格式应为 YYYY-MM-DD HH:MM，例如 2026-10-08 09:00")


def create_schedule(conn, draft_id, account_ids, scheduled_at, note=""):
    dt = parse_time(scheduled_at)
    if not account_ids:
        raise ValueError("请至少选择一个账号")
    cur = conn.execute("SELECT id FROM drafts WHERE id=?", (draft_id,))
    if not cur.fetchone():
        raise ValueError("草稿不存在")
    cur = conn.execute(
        "INSERT INTO schedules(draft_id,account_ids,scheduled_at,status,note,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (draft_id, json.dumps(list(account_ids), ensure_ascii=False),
         dt.strftime(TIME_FMT), "pending", note or "", _now()))
    conn.commit()
    return cur.lastrowid


def get_schedule(conn, sid):
    cur = conn.execute("SELECT * FROM schedules WHERE id=?", (sid,))
    row = cur.fetchone()
    return dict(row) if row else None


def list_schedules(conn, status=None):
    q = ("SELECT s.*, d.title AS draft_title FROM schedules s "
         "LEFT JOIN drafts d ON s.draft_id=d.id")
    if status:
        q += " WHERE s.status=? ORDER BY s.scheduled_at"
        cur = conn.execute(q, (status,))
    else:
        q += " ORDER BY s.scheduled_at"
        cur = conn.execute(q)
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        try:
            d["account_ids"] = json.loads(d["account_ids"] or "[]")
        except ValueError:
            d["account_ids"] = []
        rows.append(d)
    return rows


def set_status(conn, sid, status):
    if status not in STATUSES:
        raise ValueError(f"未知状态: {status}")
    cur = conn.execute("UPDATE schedules SET status=? WHERE id=?",
                       (status, sid))
    conn.commit()
    return cur.rowcount > 0


def delete_schedule(conn, sid):
    cur = conn.execute("DELETE FROM schedules WHERE id=?", (sid,))
    conn.commit()
    return cur.rowcount > 0


def snooze(conn, sid, minutes=10):
    """稍后提醒：把时间推后 minutes 分钟，保持 pending。"""
    new_t = (datetime.now() + timedelta(minutes=minutes)).strftime(TIME_FMT)
    conn.execute("UPDATE schedules SET scheduled_at=? WHERE id=?",
                 (new_t, sid))
    conn.commit()
    return new_t


def due_schedules(conn, now=None):
    """到点的 pending 排期（scheduled_at <= now）。"""
    now_s = (now or datetime.now()).strftime(TIME_FMT)
    return [s for s in list_schedules(conn, status="pending")
            if s["scheduled_at"] <= now_s]
