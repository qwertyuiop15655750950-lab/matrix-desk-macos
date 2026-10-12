# -*- coding: utf-8 -*-
"""账号矩阵管理：增删改查、分组、批量导入导出、代理配置。只用标准库。"""
import csv
import io
import json

from .db import _now

# 账号可写字段（固定顺序）
_ACCOUNT_FIELDS = (
    "nickname",
    "xhs_id",
    "group_name",
    "proxy_scheme",
    "proxy_host",
    "proxy_port",
    "proxy_user",
    "proxy_pass",
    "notes",
    "status",
)

# 导出 JSON 的字段：全部字段除 id/时间
_EXPORT_JSON_FIELDS = _ACCOUNT_FIELDS

# 导出/导入 CSV 的表头
_CSV_HEADER = (
    "nickname",
    "xhs_id",
    "group_name",
    "proxy_scheme",
    "proxy_host",
    "proxy_port",
    "proxy_user",
    "proxy_pass",
    "notes",
)


def _coerce_port(value):
    """把端口值转为 0~65535 的整数；非法则抛 ValueError。"""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return 0
    if isinstance(value, bool):
        raise ValueError("代理端口必须是 0~65535 的整数")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("代理端口必须是 0~65535 的整数")
        value = int(value)
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("代理端口必须是 0~65535 的整数")
    if not 0 <= port <= 65535:
        raise ValueError("代理端口必须在 0~65535 之间")
    return port


def _nickname_exists(conn, nickname):
    row = conn.execute(
        "SELECT 1 FROM accounts WHERE nickname = ? LIMIT 1", (nickname,)
    ).fetchone()
    return row is not None


def create_account(conn, data):
    """新增账号，返回新 id。

    nickname 必填，为空抛 ValueError("账号昵称不能为空")；
    proxy_port 须为 0~65535 的整数，否则抛 ValueError。
    """
    data = dict(data or {})
    nickname = str(data.get("nickname", "") or "").strip()
    if not nickname:
        raise ValueError("账号昵称不能为空")
    port = _coerce_port(data.get("proxy_port", 0))
    now = _now()
    cur = conn.execute(
        """INSERT INTO accounts
           (nickname, xhs_id, group_name, proxy_scheme, proxy_host, proxy_port,
            proxy_user, proxy_pass, notes, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            nickname,
            str(data.get("xhs_id", "") or ""),
            str(data.get("group_name", "") or "默认分组"),
            str(data.get("proxy_scheme", "") or "http"),
            str(data.get("proxy_host", "") or "").strip(),
            port,
            str(data.get("proxy_user", "") or ""),
            str(data.get("proxy_pass", "") or ""),
            str(data.get("notes", "") or ""),
            str(data.get("status", "") or "active"),
            now,
            now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_account(conn, account_id, data):
    """更新账号；账号不存在返回 False，否则返回 True。"""
    if get_account(conn, account_id) is None:
        return False
    data = dict(data or {})
    sets, vals = [], []
    for field in _ACCOUNT_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "nickname":
            value = str(value or "").strip()
            if not value:
                raise ValueError("账号昵称不能为空")
        elif field == "proxy_port":
            value = _coerce_port(value)
        elif field == "proxy_host":
            value = "" if value is None else str(value).strip()
        else:
            value = "" if value is None else str(value)
        sets.append("%s = ?" % field)
        vals.append(value)
    if not sets:
        return True
    vals.append(_now())
    vals.append(account_id)
    conn.execute(
        "UPDATE accounts SET %s, updated_at = ? WHERE id = ?"
        % ", ".join(sets),
        vals,
    )
    conn.commit()
    return True


def delete_account(conn, account_id):
    """删除账号；账号不存在返回 False，否则返回 True。"""
    cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    conn.commit()
    return cur.rowcount > 0


def get_account(conn, account_id):
    """取单个账号 dict；不存在返回 None。"""
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_accounts(conn, group_name=None, keyword=None):
    """账号列表，按 id 排序。

    group_name：按分组精确过滤；keyword：模糊匹配 nickname/xhs_id/notes。
    """
    where, params = [], []
    if group_name:
        where.append("group_name = ?")
        params.append(group_name)
    if keyword:
        kw = (
            str(keyword)
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        where.append(
            "(nickname LIKE ? ESCAPE '\\' OR xhs_id LIKE ? ESCAPE '\\' "
            "OR notes LIKE ? ESCAPE '\\')"
        )
        pattern = "%%%s%%" % kw
        params.extend([pattern, pattern, pattern])
    sql = "SELECT * FROM accounts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_groups(conn):
    """去重、非空、排序后的分组名列表。"""
    rows = conn.execute(
        "SELECT DISTINCT group_name FROM accounts "
        "WHERE group_name IS NOT NULL AND group_name != '' "
        "ORDER BY group_name"
    ).fetchall()
    return [r[0] for r in rows]


def export_accounts(conn, fmt="json"):
    """导出账号。

    fmt='json'：返回 JSON 字符串（ensure_ascii=False, indent=2，
    含全部字段除 id/created_at/updated_at）；
    fmt='csv'：返回 CSV 字符串（含表头）。
    """
    rows = list_accounts(conn)
    if fmt == "json":
        items = [{k: r[k] for k in _EXPORT_JSON_FIELDS} for r in rows]
        return json.dumps(items, ensure_ascii=False, indent=2)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_CSV_HEADER)
        for r in rows:
            writer.writerow([r[k] for k in _CSV_HEADER])
        return buf.getvalue()
    raise ValueError("不支持的导出格式：%s（仅支持 json/csv）" % fmt)


def import_accounts(conn, text, fmt="json"):
    """批量导入账号，返回 {'added', 'skipped', 'errors'}。

    逐条写入，单条失败不中断；昵称已存在的记为 skipped。
    """
    result = {"added": 0, "skipped": 0, "errors": []}
    if fmt == "json":
        try:
            items = json.loads(text)
        except ValueError as exc:
            result["errors"].append("JSON 解析失败：%s" % exc)
            return result
        if not isinstance(items, list):
            result["errors"].append("JSON 顶层必须是数组")
            return result
        for i, item in enumerate(items, 1):
            label = "第%d条" % i
            if not isinstance(item, dict):
                result["skipped"] += 1
                result["errors"].append("%s：非对象数据，已跳过" % label)
                continue
            _import_one(conn, item, label, result)
    elif fmt == "csv":
        try:
            rows = list(csv.DictReader(io.StringIO(text)))
        except Exception as exc:  # pragma: no cover - 防御性分支
            result["errors"].append("CSV 解析失败：%s" % exc)
            return result
        for i, row in enumerate(rows, 2):  # 第 1 行为表头
            _import_one(conn, row, "第%d行" % i, result)
    else:
        raise ValueError("不支持的导入格式：%s（仅支持 json/csv）" % fmt)
    return result


def _import_one(conn, item, label, result):
    nickname = str(item.get("nickname", "") or "").strip()
    if nickname and _nickname_exists(conn, nickname):
        result["skipped"] += 1
        return
    try:
        create_account(conn, item)
    except ValueError as exc:
        result["errors"].append("%s：%s" % (label, exc))
    except Exception as exc:  # pragma: no cover - 防御性分支
        result["errors"].append("%s：导入失败：%s" % (label, exc))
    else:
        result["added"] += 1


def proxy_dict(account):
    """账号代理转为 Playwright 可用的代理配置；无代理返回 None。

    port 为 0 时 server 不带端口。
    """
    account = account or {}
    host = str(account.get("proxy_host", "") or "").strip()
    if not host:
        return None
    scheme = str(account.get("proxy_scheme", "") or "http").strip() or "http"
    try:
        port = int(account.get("proxy_port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    server = "%s://%s" % (scheme, host)
    if port:
        server += ":%d" % port
    return {
        "server": server,
        "username": account.get("proxy_user") or None,
        "password": account.get("proxy_pass") or None,
    }


def validate_proxy(account):
    """校验代理配置：host 非空但 port 非法时抛 ValueError。"""
    account = account or {}
    host = str(account.get("proxy_host", "") or "").strip()
    if not host:
        return
    _coerce_port(account.get("proxy_port", 0))
