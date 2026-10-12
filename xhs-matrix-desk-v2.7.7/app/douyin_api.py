'抖音及跨平台账号 API。'
import io
from . import account_hub
from . import dy_workspace as douyin
from . import profiles as prof
from .config import load_selectors, PAGE_GOTO_TIMEOUT

class DouyinAPI:
    @staticmethod
    def _browser_account_id(aid):
        if isinstance(aid, bool) or not isinstance(aid, (int, str)):
            raise ValueError('账号编号无效')
        if isinstance(aid, str) and not aid.strip().isdigit():
            raise ValueError('账号编号无效')
        aid = int(aid)
        if aid <= 0: raise ValueError('账号编号无效')
        return aid

    @staticmethod
    def _collection_url(platform, url):
        from urllib.parse import urlsplit
        from .browser_plugin_bridge import HOSTS
        if not isinstance(url, str) or len(url) > 4096:
            raise ValueError('请使用所选平台的完整 HTTPS 视频或笔记地址')
        url = url.strip()
        parsed = urlsplit(url)
        if (platform not in HOSTS or parsed.scheme != 'https' or parsed.hostname not in HOSTS[platform]
                or parsed.username or parsed.password):
            raise ValueError('请使用所选平台的完整 HTTPS 视频或笔记地址')
        return url

    def hub_accounts(self, platform=''):
        try:
            rows = account_hub.list_accounts(self.conn, platform)
            dy_running = self._dy_browsers.running_ids()
            channels_running = self._channels_browsers.running_ids()
            identities={r['account_id']:dict(r) for r in self.conn.execute('SELECT * FROM metric_identities')}
            for row in rows:
                if row['platform']=='xiaohongshu':row['identity']=identities.get(row['id'],{})
                row['running'] = (bool(self._get_actor(row['id'])) if row['platform'] == 'xiaohongshu'
                                  else row['id'] in (dy_running if row['platform'] == 'douyin' else channels_running))
            counts = {p: sum(r['platform']==p for r in account_hub.list_accounts(self.conn)) for p in account_hub.PLATFORMS}
            return self._ok(items=rows, counts=counts)
        except Exception as e: return self._err(e)

    def hub_save(self, platform, data):
        try: return self._ok(id=account_hub.save(self.conn, platform, data or {}, self.MAX_ACCOUNTS))
        except Exception as e: return self._err(e)

    def _hub_browser_view(self, platform, aid, callback):
        platform = account_hub.normalize_platform(platform)
        account = account_hub.get(self.conn, platform, self._browser_account_id(aid))
        if platform == 'xiaohongshu':
            return self._call_xhs_browser(account['id'], lambda browser: callback(browser.ctx), timeout=35)
        pool = self._dy_browsers if platform == 'douyin' else self._channels_browsers
        return pool.perform(account, lambda page: callback(page.context), timeout=35, focus=False)

    def hub_browser_previews(self, platform, aid, token=''):
        try:
            from .browser_workspace import capture
            return self._ok(**self._hub_browser_view(platform, aid, lambda ctx: capture(ctx, token)))
        except Exception as error:
            return self._err(error)

    def hub_browser_input(self, platform, aid, token, frame, action, params):
        try:
            from .browser_workspace import manual_input
            return self._ok(**self._hub_browser_view(platform, aid,
                lambda ctx: manual_input(ctx, token, frame, action, params)))
        except Exception as error:
            return self._err(error)

    def hub_browser_focus(self, platform, aid, token, expanded=True):
        try:
            if not isinstance(token, str) or len(token) != 32:
                return self._err('预览已失效，请刷新后重新选择')
            def focus(context):
                page = next((page for page in context.pages if not page.is_closed()
                             and getattr(page, '_matrix_preview_token', None) == token), None)
                if page is None:
                    raise ValueError('该页面已关闭，请刷新总览')
                page.bring_to_front()
                session = context.new_cdp_session(page)
                try:
                    window = session.send('Browser.getWindowForTarget')
                    session.send('Browser.setWindowBounds', {'windowId': window['windowId'],
                                 'bounds': {'windowState': 'maximized' if expanded else 'normal'}})
                finally:
                    session.detach()
                return {'focused': True}
            return self._ok(**self._hub_browser_view(platform, aid, focus))
        except Exception as error:
            return self._err(error)

    def hub_import(self, text):
        try: return self._ok(**account_hub.import_accounts(self.conn, text, self.MAX_ACCOUNTS))
        except Exception as e: return self._err(e)

    def hub_open(self, platform, aid):
        try:
            platform = account_hub.normalize_platform(platform)
            account = account_hub.get(self.conn, platform, aid)
            if platform == 'xiaohongshu':
                def show_browser(browser):
                    page = browser.ctx.pages[0] if browser.ctx.pages else browser.ctx.new_page()
                    if page.url in ('about:blank',''):
                        page.goto(load_selectors()['xhs_home'], wait_until='domcontentloaded', timeout=PAGE_GOTO_TIMEOUT)
                    page.bring_to_front()
                self._call_xhs_browser(account['id'], show_browser, timeout=100, require_open=False)
                self.identity_watch(account['id'])
            else:
                pool = self._dy_browsers if platform == 'douyin' else self._channels_browsers
                pool.open(account)
            return self._ok(msg='浏览器已打开，请自行扫码登录')
        except Exception as e: return self._err(e)

    def hub_collect(self, platform, aid, url, engine='plugin'):
        try:
            if engine not in ('plugin', 'builtin', 'bb-browser', 'crawl4ai'): return self._err('采集通道无效')
            platform = account_hub.normalize_platform(platform)
            account = account_hub.get(self.conn, platform, self._browser_account_id(aid))
            if platform == 'douyin': return self.dy_scan_video(account['id'], url, engine)
            from .plugins import resolve_comments, collection_metadata
            selection = resolve_comments(engine)
            engine = selection['engine']
            if platform != 'xiaohongshu': return self._err('视频号当前支持扫码登录，评论采集还需要页面校准')
            url = self._collection_url(platform, url)
            from .browser_plugin_bridge import scan_with_plugin
            from .collector import archive_users
            words = douyin.keywords(self.conn)
            def scan(browser):
                page = browser.ctx.pages[0] if browser.ctx.pages else browser.ctx.new_page()
                if engine == 'builtin':
                    from .builtin_crawler import collect
                    return collect(page, url, words, collect_all=False)
                if engine == 'crawl4ai':
                    pages = [candidate for candidate in browser.ctx.pages if not candidate.is_closed()]
                    if not pages: raise ValueError('账号浏览器已关闭，请先重新打开')
                    page = pages[-1]
                    for candidate in reversed(pages):
                        if candidate.evaluate('document.visibilityState') == 'visible':
                            page = candidate; break
                if engine in ('bb-browser', 'crawl4ai'):
                    from .bb_bridge import bb_page
                    with bb_page(page, platform, account['id'], prof.get_profile_dir(account['id'])) as target:
                        if engine == 'crawl4ai':
                            from .comment_crawler import scan_comments
                            return scan_comments(target, url, words, platform='xiaohongshu')
                        return scan_with_plugin(target, url, words, platform='xiaohongshu')
                return scan_with_plugin(page, url, words, platform='xiaohongshu')
            result = self._call_xhs_browser(account['id'], scan, timeout=180, require_open=engine == 'crawl4ai')
            if not result.get('ok'): return self._err(result.get('error','插件采集失败'))
            source = result.get('sourceUrl', url) if engine == 'crawl4ai' else url
            added = sum(archive_users(self.conn, platform, row.get('keyword',''), str(source).strip(), [row]) for row in result['items'])
            return self._ok(fetched=len(result['items']), added=added,
                            stopped=bool(result.get('stopped')), timed_out=bool(result.get('timed_out')),
                            stop_reason=result.get('stop_reason',''), truncated=bool(result.get('truncated')),
                            **collection_metadata(result, selection), extraction_engine=result.get('extraction_engine',''),
                            read=result.get('read',len(result['items'])),
                            missing_ids=sum(not row.get('user_id') for row in result['items']))
        except Exception as e: return self._err(e)

    def hub_collected(self, platform='xiaohongshu'):
        try:
            platform = account_hub.normalize_platform(platform)
            rows = self.conn.execute('SELECT * FROM collected_users WHERE platform=? ORDER BY id DESC', (platform,)).fetchall()
            return self._ok(items=[dict(row) for row in rows])
        except Exception as e: return self._err(e)

    def dy_accounts(self):
        return self._ok(items=douyin.accounts(self.conn))

    def dy_save_account(self, data):
        try: return self._ok(id=douyin.save_account(self.conn, data))
        except Exception as e: return self._err(e)

    def dy_browser(self, aid):
        try:
            douyin_account = douyin.require_account(self.conn, aid)
            self._dy_browsers.open(douyin_account)
            return self._ok()
        except Exception as e: return self._err(e)

    def dy_scan_video(self, aid, url, engine='plugin'):
        try:
            if engine not in ('plugin', 'builtin', 'bb-browser', 'crawl4ai'): return self._err('采集通道无效')
            aid = self._browser_account_id(aid)
            account = douyin.require_account(self.conn, aid)
            url = self._collection_url('douyin', url)
            from .plugins import resolve_comments, collection_metadata
            selection = resolve_comments(engine)
            engine = selection['engine']
            if engine == 'plugin':
                result = self._dy_browsers.scan(account, str(url).strip(), douyin.keywords(self.conn))
            else:
                result = self._dy_browsers.scan(account, str(url).strip(), douyin.keywords(self.conn), engine=engine)
            if not result.get('ok'): return self._err(result.get('error', '采集失败'))
            rows = [{'comment_id':r.get('comment_id',''), 'user_id':r.get('user_id',''), 'nickname':r.get('nickname',''), 'content':r['text']} for r in result.get('items',[])]
            source = result.get('sourceUrl', url) if engine == 'crawl4ai' else url
            return self._ok(**douyin.ingest(self.conn, int(aid), str(source).strip(), 'video', rows),
                            fetched=len(rows), read=result.get('read',len(rows)),
                            stopped=bool(result.get('stopped')), timed_out=bool(result.get('timed_out')),
                            stop_reason=result.get('stop_reason',''), truncated=bool(result.get('truncated')),
                            **collection_metadata(result, selection), extraction_engine=result.get('extraction_engine',''))
        except Exception as e: return self._err(e)

    def tool_status(self):
        return self._ok(tools={'builtin':{'available':True,'engine':'builtin','version':'1.1'}})

    def dy_keywords(self, words=None):
        try:
            from .comment_intent import BEAUTY_PRESETS
            return self._ok(items=douyin.keywords(self.conn, words), presets=BEAUTY_PRESETS)
        except Exception as e: return self._err(e)

    def dy_import(self, aid, source, kind, text):
        try: return self._ok(**douyin.ingest(self.conn, int(aid), source, kind, douyin.parse_rows(text)))
        except Exception as e: return self._err(e)

    def dy_comments(self, aid=None, query='', matched_only=False):
        try:
            from .comment_intent import review_hint
            return self._ok(items=[{**row, **review_hint(row['content'])} for row in douyin.comments(self.conn, aid, query, matched_only)])
        except Exception as e: return self._err(e)

    def dy_leads(self, status='', aid=None):
        try: return self._ok(items=douyin.leads(self.conn, status, aid))
        except Exception as e: return self._err(e)

    def dy_update_lead(self, lid, status, notes):
        try:
            douyin.update_lead(self.conn, lid, status, notes)
            return self._ok()
        except Exception as e: return self._err(e)

    def dy_refilter(self):
        try: return self._ok(matched=douyin.refilter(self.conn))
        except Exception as e: return self._err(e)

    def dy_export(self, kind, aid=None, query='', matched_only=False, status=''):
        try:
            rows = douyin.leads(self.conn, status, aid) if kind == 'leads' else douyin.comments(self.conn, aid, query, matched_only)
            fields = ['account_name','user_id','nickname','status','notes','updated_at'] if kind == 'leads' else ['account_name','source_id','source_type','comment_id','user_id','nickname','content','matched_words','created_at']
            output = io.StringIO()
            import csv
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v) for k,v in row.items()})
            return self._ok(csv=output.getvalue(), count=len(rows))
        except Exception as e: return self._err(e)

