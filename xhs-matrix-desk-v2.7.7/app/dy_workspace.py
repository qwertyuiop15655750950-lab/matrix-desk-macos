"""抖音账号、评论及线索；通过账号独立浏览器读取页面。"""
import csv
import hashlib
import io
import json
import queue
import concurrent.futures
import threading
import time
import logging
import urllib.parse
from pathlib import Path
from .config import DATA_DIR
from .db import _now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dy_accounts(id INTEGER PRIMARY KEY, nickname TEXT NOT NULL, open_id TEXT DEFAULT '', group_name TEXT DEFAULT '', notes TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS dy_comments(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, source_id TEXT NOT NULL, source_type TEXT NOT NULL, comment_id TEXT NOT NULL, user_id TEXT DEFAULT '', nickname TEXT DEFAULT '', content TEXT NOT NULL, matched_words TEXT DEFAULT '[]', created_at TEXT, UNIQUE(account_id,source_id,comment_id));
CREATE TABLE IF NOT EXISTS dy_leads(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, user_id TEXT NOT NULL, nickname TEXT DEFAULT '', status TEXT DEFAULT 'new', notes TEXT DEFAULT '', updated_at TEXT, UNIQUE(account_id,user_id));
'''
STATUSES = {'new','contacted','won','ignored'}
SOURCE_TYPES = {'video','live','manual'}

def ensure(conn):
    conn.executescript(SCHEMA)

def accounts(conn):
    return [dict(r) for r in conn.execute('SELECT * FROM dy_accounts ORDER BY id DESC')]

def save_account(conn,data):
    name=str(data.get('nickname','')).strip()
    if not name: raise ValueError('请填写账号名称')
    values=(name,str(data.get('open_id','')).strip(),str(data.get('group_name','')).strip(),str(data.get('notes','')).strip())
    aid=data.get('id')
    with conn:
        if aid:
            c=conn.execute('UPDATE dy_accounts SET nickname=?,open_id=?,group_name=?,notes=? WHERE id=?',(*values,int(aid)))
            if not c.rowcount: raise ValueError('账号不存在')
        else:
            aid=conn.execute('INSERT INTO dy_accounts(nickname,open_id,group_name,notes,created_at) VALUES(?,?,?,?,?)',(*values,_now())).lastrowid
    return aid

def require_account(conn,aid):
    row=conn.execute('SELECT * FROM dy_accounts WHERE id=?',(int(aid),)).fetchone()
    if not row: raise ValueError('请选择有效抖音账号')
    return dict(row)

def keywords(conn,words=None):
    if words is not None:
        clean=list(dict.fromkeys(w.strip() for w in words if w.strip()))
        if len(clean)>200: raise ValueError('关键词最多200个')
        with conn: conn.execute("INSERT OR REPLACE INTO app_settings(key,value) VALUES('dy_keywords',?)",(json.dumps(clean,ensure_ascii=False),))
    r=conn.execute("SELECT value FROM app_settings WHERE key='dy_keywords'").fetchone()
    return json.loads(r[0]) if r else ['多少钱','价格','怎么买','地址','咨询']

def parse_rows(text):
    text=text.lstrip('\ufeff').strip()
    if not text: raise ValueError('请提供评论数据')
    if text[0] in '[{':
        obj=json.loads(text)
        if isinstance(obj,dict): obj=obj.get('data',obj); obj=obj.get('list',[]) if isinstance(obj,dict) else obj
        if not isinstance(obj,list): raise ValueError('JSON需要是评论数组或含 data/list 的评论数据')
        rows=obj
    else: rows=list(csv.DictReader(io.StringIO(text)))
    if len(rows)>10000: raise ValueError('一次最多导入10000条评论')
    result=[]
    for i,r in enumerate(rows):
        if not isinstance(r,dict): raise ValueError(f'第{i+1}条评论格式错误')
        content=str(r.get('content',r.get('评论内容','')) or '').strip()
        if not content: raise ValueError(f'第{i+1}条缺少content/评论内容')
        result.append({'content':content,'comment_id':str(r.get('comment_id','') or '').strip(), 'user_id':str(r.get('comment_user_id',r.get('user_id',r.get('用户ID',''))) or '').strip(),'nickname':str(r.get('nickname',r.get('昵称','')) or '').strip()})
    return result

def ingest(conn,aid,source,kind,rows):
    require_account(conn,aid)
    source=str(source).strip()
    if not source: raise ValueError('请填写来源视频ID或来源标记')
    if kind not in SOURCE_TYPES: raise ValueError('来源类型无效')
    words=keywords(conn); added=duplicate=hits=0
    with conn:
        for row in rows:
            matched=[w for w in words if w.casefold() in row['content'].casefold()]
            cid=row['comment_id'] or hashlib.sha256((row['user_id']+'\0'+row['nickname']+'\0'+row['content']).encode()).hexdigest()
            cursor=conn.execute('INSERT OR IGNORE INTO dy_comments(account_id,source_id,source_type,comment_id,user_id,nickname,content,matched_words,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(aid,source,kind,cid,row['user_id'],row['nickname'],row['content'],json.dumps(matched,ensure_ascii=False),_now()))
            if not cursor.rowcount: duplicate+=1; continue
            added+=1
            if matched:
                hits+=1
                if row['user_id']:
                    conn.execute('INSERT INTO dy_leads(account_id,user_id,nickname,updated_at) VALUES(?,?,?,?) ON CONFLICT(account_id,user_id) DO UPDATE SET nickname=CASE WHEN excluded.nickname!=\'\' THEN excluded.nickname ELSE dy_leads.nickname END,updated_at=excluded.updated_at',(aid,row['user_id'],row['nickname'],_now()))
    return {'added':added,'duplicates':duplicate,'matched':hits}

def comments(conn,aid=None,query='',matched_only=False):
    sql='SELECT c.*,a.nickname account_name FROM dy_comments c JOIN dy_accounts a ON a.id=c.account_id WHERE 1=1'; params=[]
    if aid: sql+=' AND c.account_id=?'; params.append(int(aid))
    if query: sql+=' AND (c.content LIKE ? OR c.nickname LIKE ? OR c.user_id LIKE ?)'; params += ['%'+query+'%']*3
    if matched_only: sql+=" AND c.matched_words!='[]'"
    return [dict(r) for r in conn.execute(sql+' ORDER BY c.id DESC',params)]

def leads(conn,status='',aid=None):
    if status and status not in STATUSES: raise ValueError('跟进状态无效')
    sql='SELECT l.*,a.nickname account_name,(SELECT count(*) FROM dy_comments c WHERE c.account_id=l.account_id AND c.user_id=l.user_id) comment_count FROM dy_leads l JOIN dy_accounts a ON a.id=l.account_id'
    conditions=[]; params=[]
    if status: conditions.append('l.status=?'); params.append(status)
    if aid: conditions.append('l.account_id=?'); params.append(int(aid))
    where=' WHERE '+' AND '.join(conditions) if conditions else ''
    return [dict(r) for r in conn.execute(sql+where+' ORDER BY l.updated_at DESC',params)]

def update_lead(conn,lid,status,notes):
    if status not in STATUSES: raise ValueError('跟进状态无效')
    with conn:
        c=conn.execute('UPDATE dy_leads SET status=?,notes=?,updated_at=? WHERE id=?',(status,str(notes),_now(),int(lid)))
        if not c.rowcount: raise ValueError('线索不存在')

def refilter(conn):
    words=keywords(conn); hits=0
    with conn:
        for r in conn.execute('SELECT * FROM dy_comments').fetchall():
            matches=[w for w in words if w.casefold() in r['content'].casefold()]
            conn.execute('UPDATE dy_comments SET matched_words=? WHERE id=?',(json.dumps(matches,ensure_ascii=False),r['id']))
            if matches:
                hits+=1
                if r['user_id']: conn.execute('INSERT OR IGNORE INTO dy_leads(account_id,user_id,nickname,updated_at) VALUES(?,?,?,?)',(r['account_id'],r['user_id'],r['nickname'],_now()))
    return hits

class BrowserPool:
    """Each account owns a persistent browser and its Playwright worker thread."""
    PLATFORMS = {
        'douyin': ('抖音', 'douyin_profiles', 'https://www.douyin.com/'),
        'channels': ('视频号', 'channels_profiles', 'https://channels.weixin.qq.com/'),
    }

    def __init__(self, platform='douyin'):
        if platform not in self.PLATFORMS:
            raise ValueError('不支持的浏览器平台')
        self.platform = platform
        self.label, self.profile_folder, self.start_url = self.PLATFORMS[platform]
        self.sessions = {}
        self.lock = threading.Lock()
        self._closing = False

    def _account_id(self, account):
        try:
            aid = int(account['id'])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f'请选择有效{self.label}账号') from None
        if aid <= 0:
            raise ValueError(f'请选择有效{self.label}账号')
        return aid

    def _run(self, aid, session):
        error = None
        stop = session.setdefault('stop', threading.Event())
        try:
            from playwright.sync_api import sync_playwright
            if stop.is_set():
                return
            with sync_playwright() as pw:
                ctx = None
                try:
                    folder = Path(DATA_DIR) / self.profile_folder / str(aid)
                    folder.mkdir(parents=True, exist_ok=True)
                    from .browser_plugin_bridge import extension_launch_options
                    plugin_options = extension_launch_options() if self.platform == 'douyin' else {}
                    session['profile_dir'] = str(folder)
                    if stop.is_set():
                        return
                    ctx = pw.chromium.launch_persistent_context(
                        str(folder), headless=False,
                        no_viewport=True, **plugin_options)
                    from .native_session import apply_state
                    apply_state(ctx, DATA_DIR, self.platform, aid)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    if stop.is_set():
                        return
                    page.goto(self.start_url,
                              wait_until='domcontentloaded', timeout=30000)
                    session['ready'].set()
                    while not stop.is_set():
                        pages = [candidate for candidate in ctx.pages if not candidate.is_closed()]
                        if not pages:
                            break
                        if page.is_closed():
                            page = pages[0]
                        try:
                            if session['focus'].is_set():
                                # Preserve the tab the user is browsing, rather
                                # than always focusing/capturing the launch tab.
                                visibility_checked = False
                                visible_page = None
                                for candidate in reversed(pages):
                                    if not hasattr(candidate, 'evaluate'):
                                        continue
                                    visibility_checked = True
                                    try:
                                        if candidate.evaluate("document.visibilityState === 'visible'") is True:
                                            visible_page = candidate
                                            break
                                    except Exception:
                                        continue
                                if visibility_checked:
                                    page = visible_page or pages[-1]
                                page.bring_to_front()
                                session['focus'].clear()
                        except Exception:
                            if page.is_closed():
                                continue
                            raise
                        try:
                            fn, future = session['tasks'].get_nowait()
                        except queue.Empty:
                            pass
                        else:
                            execute = False
                            with self.lock:
                                if self._closing:
                                    if not future.done():
                                        future.set_exception(ValueError(f'{self.label}浏览器正在关闭'))
                                elif not future.done() and future.set_running_or_notify_cancel():
                                    session['active_future'] = future
                                    execute = True
                            if execute:
                                try:
                                    apply_state(ctx, DATA_DIR, self.platform, aid)
                                    result = fn(page)
                                except Exception as task_error:
                                    with self.lock:
                                        if not future.done():
                                            future.set_exception(task_error)
                                else:
                                    with self.lock:
                                        if not future.done():
                                            future.set_result(result)
                                finally:
                                    with self.lock:
                                        if session.get('active_future') is future:
                                            session['active_future'] = None
                        if stop.is_set():
                            break
                        try:
                            page.wait_for_timeout(300)
                        except Exception:
                            # Closing this tab doesn't close the account's other
                            # tabs. Choose a surviving page on the next iteration.
                            if page.is_closed():
                                continue
                            raise
                finally:
                    # Release the profile while Playwright is still alive, before
                    # exposing a closed session that can be reopened.
                    if ctx is not None:
                        try:
                            ctx.close()
                        except Exception:
                            pass
        except Exception as exc:
            error = exc
        finally:
            pending = []
            with self.lock:
                session['closed'] = True
                session['error'] = error
                session['ready'].set()
                while True:
                    try:
                        pending.append(session['tasks'].get_nowait()[1])
                    except queue.Empty:
                        break
            for future in pending:
                with self.lock:
                    if not future.done():
                        future.set_exception(ValueError(f'{self.label}浏览器已关闭，请重新打开后操作'))

    def open(self, account):
        aid = self._account_id(account)
        with self.lock:
            if self._closing:
                raise ValueError(f'{self.label}浏览器工作台正在关闭，无法新建会话')
            session = self.sessions.get(aid)
            if not session or session['closed'] or not session['thread'].is_alive():
                session = {'focus': threading.Event(),
                           'ready': threading.Event(), 'tasks': queue.Queue(),
                           'closed': False, 'error': None, 'scan_future': None,
                           'active_future': None, 'stop': threading.Event()}
                thread = threading.Thread(target=self._run,
                                          args=(aid, session), daemon=True)
                session['thread'] = thread
                self.sessions[aid] = session
                thread.start()
            session['focus'].set()
        # Repeated opens also wait for startup, rather than reporting a pending
        # browser as already ready.
        if not session['ready'].wait(40):
            raise ValueError(f'{self.label}浏览器启动超时，请稍后重试')
        if self._closing:
            raise ValueError(f'{self.label}浏览器工作台正在关闭')
        if session['closed'] or session['error']:
            raise ValueError(f'{self.label}浏览器启动失败或已关闭，请检查浏览器组件和网络后重试')
        return session

    def running_ids(self):
        """Return only successfully started, currently open account browsers."""
        with self.lock:
            return sorted(aid for aid, session in self.sessions.items()
                          if not session['closed'] and not session['error']
                          and not self._closing
                          and session['ready'].is_set()
                          and session['thread'].is_alive())

    def perform(self, account, fn, timeout=180, require_open=True, focus=True):
        aid = self._account_id(account)
        if require_open:
            with self.lock:
                if self._closing:
                    raise ValueError(f'{self.label}浏览器工作台正在关闭')
                session = self.sessions.get(aid)
                if (not session or session['closed'] or not session['ready'].is_set()
                        or not session['thread'].is_alive()):
                    raise ValueError(f'请先打开所选{self.label}账号浏览器并完成扫码登录')
                if focus:
                    session['focus'].set()
        else:
            session = self.open(account)
        future = concurrent.futures.Future()
        with self.lock:
            if self._closing:
                raise ValueError(f'{self.label}浏览器工作台正在关闭')
            if session['closed']:
                raise ValueError(f'{self.label}浏览器已关闭，请重新打开后操作')
            pending = session.get('scan_future')
            if pending is not None and not pending.done():
                raise ValueError('该账号已有采集任务或浏览任务，请先停止当前任务')
            session['scan_future'] = future
            session['tasks'].put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ValueError('浏览器任务超时，请检查该账号浏览器面板后重试') from None

    def close_all(self, timeout=5):
        """Request worker-owned cleanup and wait for a bounded shared deadline.

        Playwright contexts are never touched from the calling UI thread. An
        executing callback cannot safely be forcibly interrupted; its waiter
        fails now, and its worker closes the context when that callback returns.
        """
        timeout = min(30.0, max(0.0, float(timeout)))
        deadline = time.monotonic() + timeout
        with self.lock:
            self._closing = True
            sessions = list(self.sessions.items())
            for aid, session in sessions:
                session.setdefault('stop', threading.Event()).set()
                session['focus'].clear()
                session['ready'].set()
                futures = []
                while True:
                    try:
                        futures.append(session['tasks'].get_nowait()[1])
                    except queue.Empty:
                        break
                for key in ('scan_future', 'active_future'):
                    if session.get(key) is not None:
                        futures.append(session[key])
                for future in futures:
                    if not future.done():
                        future.set_exception(ValueError(f'{self.label}浏览器工作台已关闭，任务已停止等待'))
        current_thread = threading.current_thread()
        for aid, session in sessions:
            thread = session['thread']
            if thread is not current_thread and thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        pending = [aid for aid, session in sessions if session['thread'].is_alive()]
        if pending:
            logging.warning('%s浏览器关闭等待已达上限；账号 %s 的执行中回调结束后由所属线程清理，未强杀外部进程', self.label, pending)
        return {'closed': [aid for aid, session in sessions if not session['thread'].is_alive()],
                'pending': pending}

    def scan(self, account, url, words, engine='plugin'):
        if self.platform != 'douyin':
            raise ValueError('视频评论采集仅支持抖音账号')
        parsed = urllib.parse.urlparse(url)
        if (parsed.scheme != 'https'
                or parsed.hostname not in {'www.douyin.com', 'douyin.com'}
                or parsed.username or parsed.password):
            raise ValueError('请填写 https://www.douyin.com/ 下的完整视频地址')
        if not words:
            raise ValueError('请先设置至少一个关键词')
        if engine not in {'plugin', 'builtin', 'bb-browser', 'crawl4ai'}: raise ValueError('浏览器采集引擎无效')
        from .browser_plugin_bridge import scan_with_plugin
        def scan_page(page):
            if engine == 'builtin':
                from .builtin_crawler import collect
                return collect(page, url, words, collect_all=False)
            if engine == 'plugin':
                return scan_with_plugin(page, url, words, platform='douyin')
            from .bb_bridge import bb_page
            profile = str(Path(DATA_DIR)/self.profile_folder/str(self._account_id(account)))
            with bb_page(page, self.platform, account['id'], profile) as adapter:
                if engine == 'crawl4ai':
                    from .comment_crawler import scan_comments
                    return scan_comments(adapter, url, words, platform='douyin')
                result = scan_with_plugin(adapter, url, words, platform='douyin')
                if result.get('ok'): result['snapshot'] = adapter.snapshot()
                result['engine'] = engine
                return result
        return self.perform(account, scan_page, timeout=180, require_open=engine == 'crawl4ai')

    def capture(self, account, engine='bb-browser', require_open=True):
        if engine not in {'plugin', 'bb-browser'}: raise ValueError('页面内容整理引擎无效')
        def capture_page(page):
            from .bb_bridge import bb_page, capture_page as capture_current_page
            if engine == 'plugin':
                return {**capture_current_page(page), 'platform':self.platform, 'account_id':account['id']}

            profile = str(Path(DATA_DIR)/self.profile_folder/str(self._account_id(account)))
            with bb_page(page, self.platform, account['id'], profile) as adapter:
                captured = adapter.capture()
                captured['snapshot'] = adapter.snapshot()
                return captured
        return self.perform(account, capture_page, timeout=60, require_open=require_open)
