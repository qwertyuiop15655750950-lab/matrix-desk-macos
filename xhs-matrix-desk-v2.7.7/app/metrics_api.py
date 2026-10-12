"""专门观察账号复用公开采集批次，保存指标快照及本机每日计划。"""
import csv
import io
import json
import threading
from datetime import datetime
from . import account_hub, account_metrics as metrics
from .share_links import parse_shares


class MetricsAPI:
    def metrics_setup(self):
        metrics.ensure(self.conn)
        self._identity_lock=threading.Lock()
        self._identity_watches=set()
        self._metrics_lock=threading.RLock()
        self._metrics_stop=threading.Event()
        self._metrics_thread=None
        row=self.conn.execute('SELECT config FROM metric_settings WHERE id=1').fetchone()
        if row and json.loads(row['config']).get('daily'):self._start_metrics_plan()

    def _start_metrics_plan(self):
        with self._metrics_lock:
            if self._metrics_thread is not None and self._metrics_thread.is_alive():return
            self._metrics_thread=threading.Thread(target=self._metrics_loop,daemon=True,name='public-metrics-plan')
            self._metrics_thread.start()

    def metrics_close(self):
        self._metrics_stop.set()
        if self._metrics_thread is not None:self._metrics_thread.join(timeout=2)

    def metrics_get(self):
        try:
            settings=self.conn.execute('SELECT * FROM metric_settings WHERE id=1').fetchone()
            note=settings['note'] if settings else ''
            if settings and settings['last_batch']:
                batch=self.conn.execute('SELECT status,note FROM collect_batches WHERE id=?',(settings['last_batch'],)).fetchone()
                if batch:note={'running':'正在监测','done':'监测完成','done_with_errors':'监测完成，部分来源失败','paused':'监测已暂停','stopped':'监测已停止'}.get(batch['status'],batch['status'])+'；'+batch['note']
            return self._ok(config=json.loads(settings['config']) if settings else {},
                            note=note,last_batch=settings['last_batch'] if settings else None,
                            identities=[dict(row) for row in self.conn.execute('SELECT i.*,a.nickname FROM metric_identities i JOIN accounts a ON a.id=i.account_id')],
                            items=metrics.dashboard(self.conn))
        except Exception as error: return self._err(error)

    def metrics_save(self, config):
        try:
            aid=self._browser_account_id(config.get('observer_id'))
            account_hub.get(self.conn,'xiaohongshu',aid)
            entries=parse_shares(str(config.get('targets') or ''))['items']
            if any(entry['platform']!='xiaohongshu' or entry['kind']!='profile' for entry in entries):
                raise ValueError('请填写小红书或 REDnote 主页链接，每行一个')
            works=config.get('works',20); comments=config.get('comments',1000)
            if any(isinstance(value,bool) or not isinstance(value,int) for value in (works,comments)):raise ValueError('作品数和评论上限须为整数')
            if not 1<=works<=50 or not 1<=comments<=1000: raise ValueError('作品数 1–50，评论上限 1–1000')
            clock=str(config.get('time') or '09:00')
            datetime.strptime(clock,'%H:%M')
            saved={'observer_id':aid,'targets':'\n'.join(row['url'] for row in entries),'works':works,'comments':comments,'daily':config.get('daily') is True,'time':clock}
            with self._metrics_lock,self.conn:
                self.conn.execute("INSERT INTO metric_settings(id,config) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config,last_day='',note=''",(json.dumps(saved,ensure_ascii=False),))
            if saved['daily']:self._start_metrics_plan()
            return self._ok()
        except Exception as error: return self._err(error)

    def metrics_run(self):
        try:
            with self._metrics_lock:
                settings=self.conn.execute('SELECT * FROM metric_settings WHERE id=1').fetchone()
                if not settings: raise ValueError('请先保存监测配置')
                config=json.loads(settings['config']);aid=config['observer_id']
                if self._get_actor(aid) is None: raise ValueError('请先打开专门监测账号的浏览器并扫码登录')
                for row in self.conn.execute("SELECT config FROM collect_batches WHERE status='running'"):
                    if json.loads(row['config']).get('accounts',{}).get('xiaohongshu')==aid:
                        raise ValueError('监测账号已有采集批次，请等待完成或暂停后重试')
                result=self.batch_create(config['targets'],{'xiaohongshu':aid},[],config['works'],config['comments'])
                if not result['ok']: return result
                started=self.batch_action(result['id'],'start')
                if not started['ok']: return started
                with self.conn:
                    self.conn.execute("UPDATE metric_settings SET last_day=?,last_batch=?,note='正在逐个读取主页与作品，进度见批量采集' WHERE id=1",(datetime.now().date().isoformat(),result['id']))
                return self._ok(id=result['id'])
        except Exception as error: return self._err(error)

    def _metrics_tick(self, now):
        row=self.conn.execute('SELECT * FROM metric_settings WHERE id=1').fetchone()
        if not row:return
        config=json.loads(row['config'])
        if not config.get('daily'):return
        if row['last_day']==now.date().isoformat() or now.strftime('%H:%M')<config['time']:return
        if row['last_batch']:
            previous=self.conn.execute('SELECT status FROM collect_batches WHERE id=?',(row['last_batch'],)).fetchone()
            if previous and previous['status'] not in ('done','draft'):
                # Do not repeat requests on a new day after a verification gate,
                # interrupted run, user stop, or a previous incomplete batch.
                with self.conn:self.conn.execute("UPDATE metric_settings SET note='上次监测未完整结束，请检查批次状态后手动恢复' WHERE id=1")
                return
        result=self.metrics_run()
        if not result['ok']:
            with self.conn:self.conn.execute('UPDATE metric_settings SET note=? WHERE id=1',(result['error'],))

    def _metrics_loop(self):
        try:
            while not self._metrics_stop.wait(30):
                try:self._metrics_tick(datetime.now())
                except Exception as error:
                    self._log(f'数据监测计划检查失败：{error}')
        finally:self.close_thread_conn()

    def identity_watch(self, aid):
        aid=int(aid)
        with self._identity_lock:
            if aid in self._identity_watches:return
            self._identity_watches.add(aid)
        def watch():
            try:
                # Finite post-login observation, no persistent extra browser.
                for _ in range(60):
                    if self._metrics_stop.is_set() or self._get_actor(aid) is None:return
                    result=self.identity_sync(aid)
                    if result.get('ok') or result.get('blocked'):return
                    if self._metrics_stop.wait(3):return
            finally:
                with self._identity_lock:self._identity_watches.discard(aid)
                self.close_thread_conn()
        threading.Thread(target=watch,daemon=True,name=f'account-id-{aid}').start()

    def identity_sync(self, aid):
        try:
            aid=self._browser_account_id(aid)
            account_hub.get(self.conn,'xiaohongshu',aid)
            def read(browser):
                from .page_guard import check_page
                from .share_links import classify
                for page in browser.ctx.pages:
                    if page.is_closed():continue
                    from urllib.parse import urlsplit
                    from .comment_selectors import PLATFORM_HOSTS
                    if urlsplit(page.url).hostname not in PLATFORM_HOSTS['xiaohongshu']:continue
                    own=page.evaluate(metrics.IDENTITY_SCRIPT)
                    if not own:continue
                    check_page(page)
                    entry=classify(own['profile_url'])
                    details=None
                    # Read optional public handle only if already on the owner's page.
                    try:
                        if classify(page.url)['canonical']==entry['canonical']:details=metrics.read_metrics(page,entry)
                    except ValueError:pass
                    return own['profile_url'],details
                raise ValueError('未找到当前登录账号的「我」主页入口；请完成登录后重试')
            profile,details=self._call_xhs_browser(aid,read,timeout=10,require_open=True)
            metrics.save_identity(self.conn,aid,profile,details)
            return self._ok()
        except Exception as error:
            from .page_guard import PageBlocked
            if isinstance(error,PageBlocked):return self._err(error) | {'blocked':True}
            return self._err(error)

    def metrics_export(self):
        try:
            rows=metrics.dashboard(self.conn)
            fields=['platform','kind','canonical','name','title','sampled_at','comparison_day','metric','value','raw','approximate','change']
            output=io.StringIO();writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
            count=0
            for row in rows:
                for name,value in row['metrics'].items():
                    item={key:row.get(key,'') for key in fields[:7]}
                    item.update(comparison_day=row['comparison_day'],metric=name,value=value['value'],raw=value['raw'],approximate=value['approximate'],change=row['changes'][name])
                    writer.writerow({key:"'"+str(value) if str(value).lstrip().startswith(('=','+','-','@')) else value for key,value in item.items()});count+=1
            return self._ok(csv=output.getvalue(),count=count)
        except Exception as error:return self._err(error)
