"""Small API boundary for the native, manually operated account browser."""
from . import account_hub, profiles
from .config import DATA_DIR, load_selectors

class NativeBrowserAPI:
    def native_browser_status(self):
        try:
            manager = getattr(self, '_native_browser', None)
            return self._ok(**(manager.status() if manager else {'available': False, 'items': []}))
        except Exception as error: return self._err(error)

    def _native_manager(self):
        manager = getattr(self, '_native_browser', None)
        if manager is None: raise ValueError('此入口只在桌面工作台中可用，请打开桌面程序')
        return manager

    def _native_import(self, platform, aid):
        """Only the Python side sees state. Never return it to UI JavaScript."""
        account = account_hub.get(self.conn, platform, aid)
        if platform == 'xiaohongshu' and self._get_actor(aid):
            return self._call_xhs_browser(aid, lambda browser: browser.ctx.storage_state(), timeout=30)
        if platform != 'xiaohongshu':
            pool = self._dy_browsers if platform == 'douyin' else self._channels_browsers
            if aid in pool.running_ids():
                return pool.perform(account, lambda page: page.context.storage_state(), timeout=30, focus=False)
        # Read the saved profile in a temporary background context; no platform
        # navigation or login is started and no extra browser window is opened.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if platform == 'xiaohongshu':
                context, _ = profiles.launch_account(pw, account, headless=True)
            else:
                from pathlib import Path
                from .dy_workspace import BrowserPool
                folder = Path(DATA_DIR) / BrowserPool.PLATFORMS[platform][1] / str(aid)
                folder.mkdir(parents=True, exist_ok=True)
                context = pw.chromium.launch_persistent_context(str(folder), headless=True)
            try: return context.storage_state()
            finally: context.close()

    def native_browser_select(self, platform, aid, rect, epoch):
        try:
            platform = account_hub.normalize_platform(platform)
            aid = self._browser_account_id(aid)
            account = account_hub.get(self.conn, platform, aid)
            if profiles.build_proxy(account):
                raise ValueError('此账号配置了独立代理，内嵌网页暂不支持该配置，请使用原浏览器')
            with self._browser_jobs_lock:
                if ('xiaohongshu', aid) in self._browser_jobs and platform == 'xiaohongshu':
                    raise ValueError('此账号正在执行任务，请等待完成后打开')
            start = {'xiaohongshu': load_selectors()['xhs_home'], 'douyin': 'https://www.douyin.com/',
                     'channels': 'https://channels.weixin.qq.com/'}[platform]
            return self._ok(**self._native_manager().select(platform, aid, rect, epoch, start))
        except Exception as error: return self._err(error)
        finally: self.close_thread_conn()

    def native_browser_layout(self, epoch, revision, rect):
        try: return self._ok(**self._native_manager().layout(epoch, revision, rect))
        except Exception as error: return self._err(error)

    def native_browser_overview(self, epoch, slots):
        try: return self._ok(**self._native_manager().overview(epoch, slots))
        except Exception as error: return self._err(error)

    def native_browser_overview_layout(self, epoch, revision, slots):
        try: return self._ok(**self._native_manager().overview_layout(epoch, revision, slots))
        except Exception as error: return self._err(error)

    def native_browser_command(self, epoch, command, url=''):
        try: return self._ok(**self._native_manager().command(epoch, command, url))
        except Exception as error: return self._err(error)

    def native_browser_hide(self, epoch):
        try: return self._ok(**self._native_manager().hide(epoch))
        except Exception as error: return self._err(error)
