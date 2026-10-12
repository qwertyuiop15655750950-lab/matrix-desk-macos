"""Local content archive, separated by platform and account."""
import hashlib
import json
from urllib.parse import urlsplit
from .db import _now

PLATFORMS = {'xiaohongshu', 'douyin', 'channels'}


def ensure(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS matrix_materials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        account_id INTEGER NOT NULL,
        source_url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        markdown TEXT NOT NULL,
        links_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        engine TEXT NOT NULL DEFAULT 'crawl4ai',
        engine_version TEXT NOT NULL DEFAULT '',
        selector TEXT NOT NULL DEFAULT '',
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(platform, account_id, source_url, content_hash)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS matrix_materials_lookup ON matrix_materials(platform, account_id, id)')
    conn.commit()


def _platform(value, optional=False):
    if optional and value == '':
        return value
    if value not in PLATFORMS:
        raise ValueError('素材平台无效')
    return value


def archive(conn, platform, aid, result):
    """Keep changed versions, skip identical content for the same account URL."""
    platform = _platform(platform)
    if isinstance(aid, bool):
        raise ValueError('账号 ID 无效')
    aid = int(aid)
    if aid <= 0 or not isinstance(result, dict):
        raise ValueError('素材归档参数无效')
    markdown = result.get('markdown')
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('素材正文为空，未归档')
    source_url = result.get('url')
    try:
        parsed = urlsplit(source_url)
        valid_url = (isinstance(source_url, str) and len(source_url) <= 4096
            and parsed.scheme in ('http', 'https') and parsed.hostname
            and not parsed.username and not parsed.password)
    except (ValueError, TypeError):
        valid_url = False
    if not valid_url:
        raise ValueError('素材来源网址无效')
    links = result.get('links', [])
    metadata = result.get('metadata', {})
    if not isinstance(links, list) or not isinstance(metadata, dict):
        raise ValueError('素材链接或元数据格式无效')
    title = str(result.get('title') or '')[:1000]
    links_json = json.dumps(links, ensure_ascii=False, sort_keys=True)
    metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    canonical = json.dumps({'title': title, 'markdown': markdown,
        'links': links}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    content_hash = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    now = _now()
    with conn:
        cursor = conn.execute('''INSERT OR IGNORE INTO matrix_materials
            (platform,account_id,source_url,title,markdown,links_json,metadata_json,
             engine,engine_version,selector,content_hash,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (platform, aid, source_url, title,
            markdown, links_json, metadata_json, str(result.get('engine') or 'crawl4ai'),
            str(result.get('version') or ''), str(result.get('selector') or ''),
            content_hash, now, now))
        added = cursor.rowcount == 1
        row = conn.execute('''SELECT id FROM matrix_materials
            WHERE platform=? AND account_id=? AND source_url=? AND content_hash=?''',
            (platform, aid, source_url, content_hash)).fetchone()
    return {'id': row[0], 'added': added}


def list_materials(conn, query='', platform='', limit=200):
    platform = _platform(platform, optional=True)
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError('素材查询最多 500 个字符')
    limit = max(1, min(int(limit), 1000))
    where = []
    args = []
    if platform:
        where.append('platform=?')
        args.append(platform)
    if query.strip():
        pattern = '%' + query.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        where.append("(title LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\' OR markdown LIKE ? ESCAPE '\\')")
        args.extend([pattern] * 3)
    sql = '''SELECT id,platform,account_id,source_url,title,content_hash,
        engine,engine_version,selector,created_at,updated_at,
        length(markdown) AS character_count FROM matrix_materials'''
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    cursor = conn.execute(sql, [*args, limit])
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def get_material(conn, mid):
    cursor = conn.execute('SELECT * FROM matrix_materials WHERE id=?', (int(mid),))
    row = cursor.fetchone()
    if row is None:
        raise ValueError('素材不存在')
    result = dict(zip((column[0] for column in cursor.description), row))
    result['links'] = json.loads(result.pop('links_json'))
    result['metadata'] = json.loads(result.pop('metadata_json'))
    return result
