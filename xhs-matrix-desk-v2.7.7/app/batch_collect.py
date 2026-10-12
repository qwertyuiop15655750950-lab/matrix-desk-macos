"""可暂停的公开主页/近期作品/评论批次，不发送消息，不启动外部爬虫项目。"""
import hashlib
import json
import threading
from .db import get_conn, _now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS collect_batches(id INTEGER PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collect_sources(id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, platform TEXT NOT NULL, kind TEXT NOT NULL, url TEXT NOT NULL, canonical TEXT NOT NULL, owner_profile TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', UNIQUE(batch_id,canonical));
CREATE TABLE IF NOT EXISTS batch_comments(id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, platform TEXT NOT NULL, account_id INTEGER NOT NULL, source_url TEXT NOT NULL, comment_key TEXT NOT NULL, comment_id TEXT NOT NULL DEFAULT '', user_id TEXT NOT NULL DEFAULT '', nickname TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, matched_words TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(batch_id,platform,account_id,source_url,comment_key));
'''


class BatchCollector:
    def __init__(self, db_path, runner):
        self.db_path, self.runner = db_path, runner
        self.lock = threading.RLock()
        self.thread = None
        self.shutdown = threading.Event()
        conn = get_conn(db_path)
        try:
            conn.executescript(SCHEMA)
            if 'owner_profile' not in {row[1] for row in conn.execute('PRAGMA table_info(collect_sources)')}:
                conn.execute("ALTER TABLE collect_sources ADD COLUMN owner_profile TEXT NOT NULL DEFAULT ''")
            from .account_metrics import ensure
            ensure(conn)
            with conn:
                conn.execute("UPDATE collect_batches SET status='paused',note='应用重启，请检查登录后继续' WHERE status='running'")
                conn.execute("UPDATE collect_sources SET status='error',error='上次执行中断；请新建批次重试此链接' WHERE status='running'")
        finally:
            conn.close()

    def create(self, entries, accounts, words, works=20, comments=1000):
        if not isinstance(works, int) or isinstance(works, bool) or not 1 <= works <= 50:
            raise ValueError('每个主页作品数须为 1–50')
        if not isinstance(comments, int) or isinstance(comments, bool) or not 1 <= comments <= 1000:
            raise ValueError('单作品评论上限须为 1–1000')
        if not isinstance(words, list) or any(not isinstance(w, str) for w in words) or len(words) > 200:
            raise ValueError('标记关键词须为文字列表，最多 200 个')
        config = {'accounts': accounts, 'words': [w.strip()[:100] for w in words if w.strip()], 'works': works, 'comments': comments}
        conn = get_conn(self.db_path)
        try:
            with conn:
                bid = conn.execute('INSERT INTO collect_batches(config,status,created_at) VALUES(?,?,?)', (json.dumps(config, ensure_ascii=False), 'draft', _now())).lastrowid
                for entry in entries:
                    conn.execute('INSERT OR IGNORE INTO collect_sources(batch_id,platform,kind,url,canonical) VALUES(?,?,?,?,?)', (bid, entry['platform'], entry['kind'], entry['url'], entry['canonical']))
            return bid
        finally:
            conn.close()

    def action(self, bid, action):
        if action not in ('start', 'pause', 'stop'):
            raise ValueError('批次操作无效')
        with self.lock:
            conn = get_conn(self.db_path)
            try:
                row = conn.execute('SELECT status FROM collect_batches WHERE id=?', (int(bid),)).fetchone()
                if not row:
                    raise ValueError('批次不存在')
                if action == 'start' and row['status'] in ('done', 'done_with_errors'):
                    raise ValueError('批次已结束，失败链接请新建批次重试')
                state = {'start':'running', 'pause':'paused', 'stop':'stopped'}[action]
                with conn:
                    conn.execute('UPDATE collect_batches SET status=?,note=? WHERE id=?', (state, '' if action == 'start' else '当前作品结束后暂停后续作品；可在浏览器面板立即停止当前采集', bid))
                if action == 'start' and (self.thread is None or not self.thread.is_alive()):
                    self.thread = threading.Thread(target=self._loop, daemon=True, name='comment-batch')
                    self.thread.start()
                return state
            finally:
                conn.close()

    def _loop(self):
        conn = get_conn(self.db_path)
        try:
            while not self.shutdown.is_set():
                with self.lock:
                    batch = conn.execute("SELECT * FROM collect_batches WHERE status='running' ORDER BY id LIMIT 1").fetchone()
                    if not batch:
                        self.thread = None
                        return
                    source = conn.execute("SELECT * FROM collect_sources WHERE batch_id=? AND status='pending' ORDER BY id LIMIT 1", (batch['id'],)).fetchone()
                    if not source:
                        errors = conn.execute("SELECT count(*) FROM collect_sources WHERE batch_id=? AND status='error'", (batch['id'],)).fetchone()[0]
                        with conn:
                            conn.execute('UPDATE collect_batches SET status=?,note=? WHERE id=?', ('done_with_errors' if errors else 'done', f'失败来源 {errors} 个；评论只代表实际加载的公开内容', batch['id']))
                        continue
                    config = json.loads(batch['config'])
                    with conn:
                        conn.execute("UPDATE collect_sources SET status='running' WHERE id=?", (source['id'],))
                try:
                    result = self.runner(dict(source), config)
                    if not result.get('ok') and not result.get('blocked'):
                        raise ValueError(result.get('error') or '采集未成功')
                    with conn:
                        if result.get('works') is not None and not result.get('blocked'):
                            for work in result['works'][:config['works']]:
                                total = conn.execute('SELECT count(*) FROM collect_sources WHERE batch_id=?', (batch['id'],)).fetchone()[0]
                                if total >= 100:
                                    result['truncated'] = True
                                    result['warning'] = '本批次来源达到 100 个上限，请分批继续'
                                    break
                                conn.execute('INSERT OR IGNORE INTO collect_sources(batch_id,platform,kind,url,canonical,owner_profile) VALUES(?,?,?,?,?,?)', (batch['id'], source['platform'], 'content', work['url'], work['canonical'],source['canonical']))
                        for row in result.get('items', []):
                            text = str(row.get('text') or '').strip()
                            if not text:
                                continue
                            cid = str(row.get('comment_id') or '')
                            key = cid or hashlib.sha256(json.dumps([row.get('user_id',''), row.get('nickname',''),text], ensure_ascii=False).encode()).hexdigest()
                            matches = [w for w in config['words'] if w.casefold() in text.casefold()]
                            conn.execute('INSERT OR IGNORE INTO batch_comments(batch_id,platform,account_id,source_url,comment_key,comment_id,user_id,nickname,content,matched_words,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (batch['id'], source['platform'], int(config['accounts'][source['platform']]), result.get('source_url', source['canonical']), key, cid, str(row.get('user_id') or ''), str(row.get('nickname') or ''), text, json.dumps(matches, ensure_ascii=False), _now()))
                        if result.get('ok') and not result.get('blocked') and result.get('metrics'):
                            from .account_metrics import record
                            record(conn,dict(source),config,batch['id'],result['metrics'])
                        metadata = {k:v for k,v in result.items() if k not in ('items','works')}
                        if result.get('blocked'):
                            note = str(result.get('error') or '页面访问异常，需要人工检查')[:500]
                            conn.execute("UPDATE collect_sources SET status='pending',result=?,error=? WHERE id=?", (json.dumps(metadata, ensure_ascii=False), note, source['id']))
                            # Pause every currently running batch using this
                            # account; another queued batch must not repeat it.
                            for candidate in conn.execute("SELECT id,config FROM collect_batches WHERE status='running'").fetchall():
                                accounts = json.loads(candidate['config'])['accounts']
                                if accounts.get(source['platform']) == config['accounts'][source['platform']]:
                                    conn.execute("UPDATE collect_batches SET status='paused',note=? WHERE id=?", (note + '；已读评论已保留，需人工处理后继续', candidate['id']))
                        else:
                            conn.execute("UPDATE collect_sources SET status='done',result=?,error='' WHERE id=?", (json.dumps(metadata, ensure_ascii=False), source['id']))
                        if result.get('stopped') and not result.get('blocked'):
                            conn.execute("UPDATE collect_batches SET status='paused',note='浏览器面板已停止，部分评论已保留；后续作品暂停' WHERE id=? AND status='running'", (batch['id'],))
                except Exception as error:
                    with conn:
                        conn.execute("UPDATE collect_sources SET status='error',error=? WHERE id=?", (str(error)[:500], source['id']))
        finally:
            conn.close()

    def list(self):
        conn = get_conn(self.db_path)
        try:
            items = []
            for batch in conn.execute('SELECT id,status,note,created_at FROM collect_batches ORDER BY id DESC LIMIT 50'):
                row = dict(batch)
                row['sources'] = [{**dict(s), 'result':json.loads(s['result'])} for s in conn.execute('SELECT id,platform,kind,canonical,status,result,error FROM collect_sources WHERE batch_id=? ORDER BY id', (batch['id'],))]
                row['comments'] = conn.execute('SELECT count(*) FROM batch_comments WHERE batch_id=?', (batch['id'],)).fetchone()[0]
                items.append(row)
            return items
        finally:
            conn.close()

    def results(self, bid):
        conn = get_conn(self.db_path)
        try:
            return [dict(r) for r in conn.execute('SELECT * FROM batch_comments WHERE batch_id=? ORDER BY id', (int(bid),))]
        finally:
            conn.close()

    def close(self):
        self.shutdown.set()
