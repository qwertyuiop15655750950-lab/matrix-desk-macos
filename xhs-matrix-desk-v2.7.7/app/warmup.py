"""可暂停的本地多天浏览计划。浏览动作由账号/手机适配器执行。"""
import concurrent.futures
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import json
import threading
import time
from .db import get_conn, _now

LOCAL_TZ = ZoneInfo('Asia/Shanghai')


class BrowseStopped(Exception):
    pass


def ensure(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS warmup_plans(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
      targets_json TEXT NOT NULL, days INTEGER NOT NULL, minutes INTEGER NOT NULL,
      start_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
      concurrency INTEGER NOT NULL, engine TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
      note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS warmup_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL,
      target_key TEXT NOT NULL, day_index INTEGER NOT NULL,
      status TEXT NOT NULL, elapsed_seconds REAL NOT NULL DEFAULT 0,
      error TEXT NOT NULL DEFAULT '', started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(plan_id,target_key,day_index));
    ''')


def target_key(target):
    if target.get('kind') == 'android':
        serial = target.get('serial')
        if not isinstance(serial, str) or not serial or len(serial) > 128:
            raise ValueError('手机设备标识无效')
        return 'android:' + serial
    if target.get('kind', 'browser') != 'browser':
        raise ValueError('计划目标类型无效')
    if target.get('platform') not in ('xiaohongshu', 'douyin', 'channels'):
        raise ValueError('计划平台无效')
    if isinstance(target.get('id'), bool) or int(target.get('id', 0)) <= 0:
        raise ValueError('计划账号无效')
    return target['platform'] + ':' + str(int(target['id']))


def _integer(value, low, high, label):
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise ValueError(label + '必须是整数')
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise ValueError(label + '必须是整数') from None
    if not low <= number <= high:
        raise ValueError(f'{label}范围为{low}–{high}')
    return number


def create_plan(conn, data, today=None):
    if not isinstance(data, dict): raise ValueError('计划参数无效')
    today = today or datetime.now(LOCAL_TZ).date()
    name = str(data.get('name') or '账号浏览计划').strip()[:100]
    targets = data.get('targets')
    if not isinstance(targets, list) or not 1 <= len(targets) <= 100:
        raise ValueError('请选择1–100个账号或手机')
    clean, seen = [], set()
    for target in targets:
        if not isinstance(target, dict): raise ValueError('计划目标格式无效')
        key = target_key(target)
        if key in seen: continue
        seen.add(key)
        clean.append({'kind': 'android', 'serial': target['serial']} if target.get('kind') == 'android'
                     else {'kind': 'browser', 'platform': target['platform'], 'id': int(target['id'])})
    days = _integer(data.get('days', 3), 1, 30, '计划天数')
    minutes = _integer(data.get('minutes', 10), 1, 60, '每天每账号分钟数')
    concurrency = _integer(data.get('concurrency', 1), 1, 3, '同时执行数量')
    try:
        start = date.fromisoformat(data.get('start_date') or today.isoformat())
        start_time = datetime.strptime(data.get('start_time', '09:00'), '%H:%M').strftime('%H:%M')
        end_time = datetime.strptime(data.get('end_time', '21:00'), '%H:%M').strftime('%H:%M')
    except (TypeError, ValueError): raise ValueError('请填写有效日期和每日时间窗口') from None
    if start < today: raise ValueError('开始日期不能早于今天')
    if start_time >= end_time: raise ValueError('每日结束时间须晚于开始时间')
    if (datetime.strptime(end_time, '%H:%M') - datetime.strptime(start_time, '%H:%M')).total_seconds() < minutes * 60:
        raise ValueError('每日时间窗口短于单账号浏览时长')
    window_seconds = (datetime.strptime(end_time, '%H:%M') - datetime.strptime(start_time, '%H:%M')).total_seconds()
    if ((len(clean) + concurrency - 1) // concurrency) * minutes * 60 > window_seconds:
        raise ValueError('每日时间窗口不足以完成全部目标，请延长窗口、减少目标或增加同时执行数量')
    engine = data.get('engine', 'builtin')
    if engine not in ('plugin', 'builtin', 'bb-browser'): raise ValueError('浏览通道无效')
    now = _now()
    with conn:
        cursor = conn.execute('''INSERT INTO warmup_plans
            (name,targets_json,days,minutes,start_date,start_time,end_time,concurrency,engine,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (name, json.dumps(clean, ensure_ascii=False), days, minutes,
            start.isoformat(), start_time, end_time, concurrency, engine, now, now))
    return cursor.lastrowid


def plan_detail(conn, pid):
    row = conn.execute('SELECT * FROM warmup_plans WHERE id=?', (int(pid),)).fetchone()
    if not row: raise ValueError('浏览计划不存在')
    item = dict(row)
    item['targets'] = json.loads(item.pop('targets_json'))
    item['runs'] = [dict(r) for r in conn.execute('SELECT * FROM warmup_runs WHERE plan_id=? ORDER BY day_index,target_key', (item['id'],))]
    item['done'] = sum(r['status'] == 'done' for r in item['runs'])
    item['total'] = len(item['targets']) * item['days']
    return item


def list_plans(conn):
    return [plan_detail(conn, row['id']) for row in conn.execute('SELECT id FROM warmup_plans ORDER BY id DESC')]


class BrowseControl:
    def __init__(self, stop, pause, shutdown, deadline=None):
        self.stop = stop
        self.pause = pause
        self.shutdown = shutdown
        self.deadline = deadline
        self.active_seconds = 0.0

    def cancelled(self):
        return self.stop.is_set() or self.shutdown.is_set() or (self.deadline is not None and time.time() >= self.deadline)

    def wait_active(self, seconds):
        remaining = max(0, float(seconds))
        while remaining > 0:
            if self.cancelled(): raise BrowseStopped('计划已停止或每日时间窗口已结束')
            if self.pause.is_set():
                self.stop.wait(0.2)
                continue
            started = time.monotonic()
            self.stop.wait(min(0.2, remaining))
            if not self.pause.is_set():
                elapsed = time.monotonic() - started
                remaining -= elapsed
                self.active_seconds += elapsed
        if self.cancelled(): raise BrowseStopped('计划已停止或每日时间窗口已结束')
        return float(seconds)


class WarmupService:
    """只在应用运行并由用户启动计划后调度；重启后恢复为暂停。"""
    def __init__(self, db_path, runner):
        self.db_path, self.runner = db_path, runner
        self.lock = threading.RLock()
        self.shutdown = threading.Event()
        self.controls, self.active, self.targets_busy = {}, {}, set()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix='browse-plan')
        self.thread = None
        conn = get_conn(db_path)
        try:
            ensure(conn)
            with conn:
                conn.execute("UPDATE warmup_plans SET status='paused',note='应用已重启，请打开对应浏览器或连接手机后继续',updated_at=? WHERE status='running'", (_now(),))
                conn.execute("UPDATE warmup_runs SET status='stopped',error='应用上次执行中断',updated_at=? WHERE status='running'", (_now(),))
        finally: conn.close()

    def set_state(self, pid, action, start_scheduler=True):
        if action not in ('start', 'pause', 'stop'): raise ValueError('计划操作无效')
        conn = get_conn(self.db_path)
        try:
            with self.lock:
                item = plan_detail(conn, pid)
                control = self.controls.setdefault(item['id'], (threading.Event(), threading.Event()))
                stop, pause = control
                if action == 'start':
                    if item['status'] == 'finished': raise ValueError('该计划已结束，请创建新计划')
                    if stop.is_set() and any(key[0] == item['id'] for key in self.active):
                        raise ValueError('上一轮任务正在停止，请稍后继续')
                    stop.clear(); pause.clear(); status = 'running'
                elif action == 'pause':
                    pause.set(); status = 'paused'
                else:
                    stop.set(); status = 'stopped'
                with conn:
                    conn.execute('UPDATE warmup_plans SET status=?,note=?,updated_at=? WHERE id=?',
                                 (status, '', _now(), item['id']))
                if action == 'start' and start_scheduler and (self.thread is None or not self.thread.is_alive()):
                    self.thread = threading.Thread(target=self._loop, daemon=True, name='browse-scheduler')
                    self.thread.start()
                return status
        finally: conn.close()

    def _loop(self):
        while not self.shutdown.is_set():
            try: self.tick()
            except Exception: pass
            self.shutdown.wait(2)

    def tick(self, now=None):
        now = now or datetime.now(LOCAL_TZ)
        if now.tzinfo is None: now = now.replace(tzinfo=LOCAL_TZ)
        conn = get_conn(self.db_path)
        try:
            with self.lock:
                for pid in list(self.active):
                    if self.active[pid].done():
                        self.active.pop(pid)
                        self.targets_busy.discard(pid[1])
                if self.shutdown.is_set(): return
                rows = conn.execute("SELECT id FROM warmup_plans WHERE status='running' ORDER BY id").fetchall()
                for row in rows:
                    plan = plan_detail(conn, row['id'])
                    day_index = (now.date() - date.fromisoformat(plan['start_date'])).days
                    if day_index >= plan['days'] or plan['done'] == plan['total']:
                        with conn: conn.execute("UPDATE warmup_plans SET status='finished',note=?,updated_at=? WHERE id=?",
                            (f"计划结束，完成{plan['done']}/{plan['total']}个每日任务", _now(), plan['id']))
                        continue
                    if day_index < 0 or not plan['start_time'] <= now.strftime('%H:%M') < plan['end_time']: continue
                    existing = {r['target_key']: r for r in plan['runs'] if r['day_index'] == day_index}
                    active_count = sum(key[0] == plan['id'] for key in self.active)
                    for target in plan['targets']:
                        key = target_key(target)
                        record = existing.get(key)
                        # A failed device waits for an explicit stop/start rather
                        # than repeatedly hitting an offline account every tick.
                        if record and record['status'] in ('done', 'error'): continue
                        if key in self.targets_busy or active_count >= plan['concurrency'] or len(self.active) >= 3: continue
                        stop, pause = self.controls.setdefault(plan['id'], (threading.Event(), threading.Event()))
                        if stop.is_set() or pause.is_set(): continue
                        now_s = _now()
                        elapsed = float(record['elapsed_seconds']) if record else 0
                        with conn:
                            conn.execute('''INSERT INTO warmup_runs(plan_id,target_key,day_index,status,elapsed_seconds,started_at,updated_at)
                                VALUES(?,?,?,'running',?,?,?) ON CONFLICT(plan_id,target_key,day_index)
                                DO UPDATE SET status='running',error='',updated_at=excluded.updated_at''',
                                (plan['id'], key, day_index, elapsed, now_s, now_s))
                        deadline = datetime.combine(now.date(), datetime.strptime(plan['end_time'], '%H:%M').time(), tzinfo=LOCAL_TZ).timestamp()
                        control = BrowseControl(stop, pause, self.shutdown, deadline)
                        task_key = (plan['id'], key, day_index)
                        self.targets_busy.add(key)
                        self.active[task_key] = self.executor.submit(self._execute, task_key, target, plan, control, elapsed)
                        active_count += 1
        finally: conn.close()

    def _execute(self, task_key, target, plan, control, elapsed):
        conn = get_conn(self.db_path)
        current = elapsed
        def progress(seconds):
            nonlocal current
            current = min(plan['minutes'] * 60, elapsed + max(0, float(seconds)))
            # Browser callbacks execute on the account's actor thread. Never
            # carry this worker's SQLite connection across that boundary.
            progress_conn = get_conn(self.db_path)
            try:
                with progress_conn: progress_conn.execute('UPDATE warmup_runs SET elapsed_seconds=?,updated_at=? WHERE plan_id=? AND target_key=? AND day_index=?',
                                                          (current, _now(), *task_key))
            finally: progress_conn.close()
        status, error = 'done', ''
        try:
            remaining = max(0, plan['minutes'] * 60 - elapsed)
            result = self.runner(target, remaining, plan['engine'], control, progress) if remaining > 0 else 0
            if control.cancelled(): raise BrowseStopped('计划已停止')
            if result is not None: progress(result)
            if current < plan['minutes'] * 60 - 0.5: raise RuntimeError('浏览任务未达到指定时长')
        except BrowseStopped as exc:
            progress(max(current - elapsed, control.active_seconds))
            status, error = ('done', '') if current >= plan['minutes'] * 60 - 0.5 else ('stopped', str(exc))
        except Exception as exc:
            progress(max(current - elapsed, control.active_seconds))
            status, error = 'error', str(exc)[:500]
        finally:
            with self.lock:
                with conn:
                    conn.execute('UPDATE warmup_runs SET status=?,elapsed_seconds=?,error=?,updated_at=? WHERE plan_id=? AND target_key=? AND day_index=?',
                                 (status, current, error, _now(), *task_key))
                    if status == 'error':
                        paused = conn.execute("UPDATE warmup_plans SET status='paused',note=?,updated_at=? WHERE id=? AND status='running'",
                                              ('浏览任务失败，计划已暂停；请人工检查账号后再重试：' + error, _now(), task_key[0])).rowcount
                        if paused:
                            stop, pause = self.controls.setdefault(task_key[0], (threading.Event(), threading.Event()))
                            stop.set()
                            pause.clear()
            conn.close()

    def retry_errors(self, pid):
        conn = get_conn(self.db_path)
        try:
            plan_detail(conn, pid)
            with conn: conn.execute("UPDATE warmup_runs SET status='stopped',error='' WHERE plan_id=? AND status='error'", (int(pid),))
        finally: conn.close()

    def close(self):
        self.shutdown.set()
        with self.lock:
            for stop, pause in self.controls.values(): stop.set(); pause.clear()
        if self.thread: self.thread.join(timeout=3)
        self.executor.shutdown(wait=False, cancel_futures=True)
