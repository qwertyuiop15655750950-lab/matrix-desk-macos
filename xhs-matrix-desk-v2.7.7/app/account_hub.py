"""三平台账号档案。批量导入只创建本地记录，登录由用户扫码完成。"""
import csv
import io
import json
from .db import _now
from . import accounts as xhs
from . import dy_workspace as dy
PLATFORMS={'xiaohongshu':'小红书','douyin':'抖音','channels':'视频号'}
ALIASES={**{k:k for k in PLATFORMS},**{v:k for k,v in PLATFORMS.items()},'wechat_channels':'channels'}

def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS channels_accounts(id INTEGER PRIMARY KEY,nickname TEXT NOT NULL,group_name TEXT DEFAULT \'\',notes TEXT DEFAULT \'\',created_at TEXT,updated_at TEXT)')
    conn.commit()

def normalize_platform(value):
    platform=ALIASES.get(str(value or '').strip())
    if not platform: raise ValueError('平台请填写小红书、抖音或视频号')
    return platform

def list_accounts(conn,platform=''):
    requested=normalize_platform(platform) if platform else None
    rows=[]
    tables={'xiaohongshu':'accounts','douyin':'dy_accounts','channels':'channels_accounts'}
    for key,table in tables.items():
        if requested and key!=requested: continue
        sql=f'SELECT id,nickname,group_name,notes FROM {table}'
        if key=='xiaohongshu': sql+=" WHERE platform='xiaohongshu' OR platform='' OR platform IS NULL"
        for row in conn.execute(sql+' ORDER BY id DESC'):
            rows.append({**dict(row),'platform':key,'key':f'{key}:{row["id"]}'})
    return rows

def save(conn,platform,data,max_xhs=20):
    platform=normalize_platform(platform)
    data={k:data[k] for k in ('id','nickname','group_name','notes') if k in data}
    name=str(data.get('nickname','') or '').strip()
    if not name: raise ValueError('请填写账号名称')
    data['nickname']=name
    if platform=='xiaohongshu':
        with conn:
            # Reserve the write lock before reading capacity or validating the
            # platform. Another connection must wait until this write commits.
            conn.execute('BEGIN IMMEDIATE')
            if data.get('id'):
                aid=int(data['id'])
                get(conn,platform,aid)
                if not xhs.update_account(conn,aid,data): raise ValueError('账号不存在')
                return aid
            if conn.execute('SELECT count(*) FROM accounts').fetchone()[0] >= max_xhs:
                raise ValueError(f'小红书账号已达到原版上限{max_xhs}个')
            return xhs.create_account(conn,data)
    if platform=='douyin': return dy.save_account(conn,data)
    with conn:
        if data.get('id'):
            aid=int(data['id'])
            cursor=conn.execute('UPDATE channels_accounts SET nickname=?,group_name=?,notes=?,updated_at=? WHERE id=?',(name,str(data.get('group_name','') or ''),str(data.get('notes','') or ''),_now(),aid))
            if not cursor.rowcount: raise ValueError('账号不存在')
        else:
            aid=conn.execute('INSERT INTO channels_accounts(nickname,group_name,notes,created_at,updated_at) VALUES(?,?,?,?,?)',(name,str(data.get('group_name','') or ''),str(data.get('notes','') or ''),_now(),_now())).lastrowid
    return aid

def get(conn,platform,aid):
    platform=normalize_platform(platform)
    row=conn.execute('SELECT * FROM '+{'xiaohongshu':'accounts','douyin':'dy_accounts','channels':'channels_accounts'}[platform]+' WHERE id=?',(int(aid),)).fetchone()
    if not row: raise ValueError('账号不存在')
    if platform=='xiaohongshu' and row['platform'] not in ('xiaohongshu','',None): raise ValueError('此账号不属于小红书')
    return dict(row)

def import_accounts(conn,text,max_xhs=20):
    text=str(text or '').lstrip('\ufeff').strip()
    if not text: raise ValueError('请提供CSV或JSON账号数据')
    if text[0] in '[{':
        rows=json.loads(text)
        if not isinstance(rows,list): raise ValueError('JSON需要是账号数组')
    else: rows=list(csv.DictReader(io.StringIO(text)))
    if not rows: raise ValueError('文件没有账号记录')
    if len(rows)>5000: raise ValueError('单次最多导入5000条账号记录')
    clean=[]
    for i,row in enumerate(rows,1):
        if not isinstance(row,dict): raise ValueError(f'第{i}条账号格式错误')
        platform=normalize_platform(row.get('platform',row.get('平台')))
        name=str(row.get('nickname',row.get('账号名称','')) or '').strip()
        if not name: raise ValueError(f'第{i}条缺少账号名称')
        clean.append((platform,name,str(row.get('group_name',row.get('分组','')) or ''),str(row.get('notes',row.get('备注','')) or '')))
    added=skipped=0
    with conn:
        # Existing names, capacity, and inserts share one serialized transaction.
        # A deferred transaction cannot protect these initial reads from another
        # SQLite connection importing the same names or filling the last slot.
        conn.execute('BEGIN IMMEDIATE')
        existing={(r['platform'],r['nickname']) for r in list_accounts(conn)}
        new_xhs={(p,n) for p,n,_,_ in clean if p=='xiaohongshu'} - existing
        if conn.execute('SELECT count(*) FROM accounts').fetchone()[0] + len(new_xhs) > max_xhs:
            raise ValueError(f'导入后小红书账号将超过原版上限{max_xhs}个；本次未导入任何账号')
        for platform,name,group,notes in clean:
            if (platform,name) in existing: skipped+=1; continue
            if platform=='xiaohongshu':
                conn.execute('INSERT INTO accounts(nickname,group_name,notes,created_at,updated_at,platform) VALUES(?,?,?,?,?,?)',(name,group,notes,_now(),_now(),platform))
            elif platform=='douyin':
                conn.execute('INSERT INTO dy_accounts(nickname,group_name,notes,created_at) VALUES(?,?,?,?)',(name,group,notes,_now()))
            else:
                conn.execute('INSERT INTO channels_accounts(nickname,group_name,notes,created_at,updated_at) VALUES(?,?,?,?,?)',(name,group,notes,_now(),_now()))
            existing.add((platform,name));added+=1
    return {'added':added,'skipped':skipped}
