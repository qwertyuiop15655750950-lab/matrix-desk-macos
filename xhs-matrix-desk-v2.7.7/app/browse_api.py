"""浏览计划 API；只有日常浏览，不自动互动。"""
from . import account_hub

class BrowseAPI:
    def warmup_targets(self):
        try:
            rows = self.hub_accounts()['items']
            items = [{'kind': 'browser', 'key': r['key'], 'platform': r['platform'], 'id': r['id'],
                      'name': r['nickname'], 'ready': r['running']} for r in rows]
            return self._ok(items=items)
        except Exception as e: return self._err(e)

    def warmup_create(self, data):
        try:
            from .warmup import create_plan
            for target in data.get('targets', []):
                if target.get('kind', 'browser') == 'browser':
                    account_hub.get(self.conn, target.get('platform'), target.get('id'))
                else:
                    raise ValueError('Android 已独立，请在手机工具中操作')
            return self._ok(id=create_plan(self.conn, data))
        except Exception as e: return self._err(e)

    def warmup_list(self):
        try:
            from .warmup import list_plans
            return self._ok(items=list_plans(self.conn))
        except Exception as e: return self._err(e)

    def warmup_action(self, pid, action):
        try:
            if action in ('start', 'retry'):
                from .warmup import plan_detail, target_key
                plan = plan_detail(self.conn, pid)
                targets = self.warmup_targets()
                if not targets.get('ok'):
                    raise ValueError(targets.get('error') or '无法检查账号浏览器状态')
                ready = {row['key'] for row in targets['items'] if row['ready']}
                if any(target_key(target) not in ready for target in plan['targets']):
                    raise ValueError('请先打开全部所选账号的浏览器，并人工检查登录和平台提醒后再开始')
            if action == 'retry':
                self._warmup.retry_errors(pid)
                action = 'start'
            return self._ok(status=self._warmup.set_state(pid, action))
        except Exception as e: return self._err(e)

    def _run_browse_target(self, target, seconds, engine, control, progress):
        if target['kind'] != 'browser': raise ValueError('Android 已独立；本计划只支持浏览器账号')
        from .warmup import BrowseStopped
        from .comment_selectors import PLATFORM_HOSTS
        from urllib.parse import urlsplit
        import random
        account = account_hub.get(self.conn, target['platform'], self._browser_account_id(target['id']))
        def browse(page):
            from .page_guard import check_page
            hosts = PLATFORM_HOSTS.get(target['platform'], {'channels.weixin.qq.com'})
            initial = urlsplit(page.url)
            if initial.scheme != 'https' or initial.hostname not in hosts:
                raise ValueError('请先在账号浏览器打开相应平台内容页')
            while control.active_seconds < seconds:
                check_page(page)
                current = urlsplit(page.url)
                if current.hostname not in hosts or any(word in page.title().lower() for word in ('登录','login','sign in')):
                    raise ValueError('页面已离开平台内容页或需要重新登录，浏览计划已停止')
                control.wait_active(min(random.uniform(5,20), seconds-control.active_seconds))
                progress(min(seconds,control.active_seconds))
                if control.cancelled(): raise BrowseStopped('浏览计划已停止')
                if control.active_seconds >= seconds: break
                check_page(page)
                pixels = random.randint(400,800)
                if hasattr(page, 'mouse'):
                    viewport = page.viewport_size or {'width':1280,'height':850}
                    page.mouse.move(viewport['width']*.45,viewport['height']*.5)
                    page.mouse.wheel(0,pixels)
                else:
                    page.evaluate('(pixels) => window.scrollBy(0,pixels)', pixels)
            return min(seconds,control.active_seconds)
        try:
            if target['platform']=='xiaohongshu':
                def selected(browser):
                    pages=[p for p in browser.ctx.pages if not p.is_closed()]
                    if not pages: raise ValueError('账号页面已关闭')
                    visible=[p for p in pages if p.evaluate('document.visibilityState')=='visible']
                    return browse(visible[-1] if visible else pages[-1])
                return self._call_xhs_browser(account['id'], selected, timeout=seconds+86400)
            pool=self._dy_browsers if target['platform']=='douyin' else self._channels_browsers
            return pool.perform(account,browse,timeout=seconds+86400,require_open=True)
        finally:
            self.close_thread_conn()

