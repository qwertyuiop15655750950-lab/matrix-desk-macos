# -*- coding: utf-8 -*-
"""发布记录查询与导出。只用标准库。"""
import csv


def list_records(conn, limit=500):
    cur = conn.execute(
        "SELECT r.id, a.nickname, r.title_used, r.image_count, r.status,"
        " r.screenshot, r.created_at FROM publish_records r "
        "LEFT JOIN accounts a "
        "ON r.account_id=a.id ORDER BY r.id DESC LIMIT ?", (limit,))
    return [dict(r) for r in cur.fetchall()]


def export_records_csv(conn, path):
    rows = list_records(conn, limit=100000)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "账号", "标题", "图片数", "状态", "时间"])
        for r in rows:
            w.writerow([r["id"], r["nickname"] or "", r["title_used"],
                        r["image_count"], r["status"], r["created_at"]])
    return len(rows)


def clear_records(conn):
    conn.execute("DELETE FROM publish_records")
    conn.commit()
