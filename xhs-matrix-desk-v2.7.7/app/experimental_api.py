"""冻结的素材 API；默认关闭。"""
from datetime import datetime
from . import account_hub, materials
from . import profiles as prof
from .config import DATA_DIR

class ExperimentalAPI:
    def material_capture(self, platform, aid, selector='', engine='bb-browser'):
        if not self.enable_extras: return self._err('素材库和浏览计划已冻结；核心账号、发布、私信和评论采集仍可使用')
        try:
            if engine not in ('plugin', 'bb-browser'): raise ValueError('页面读取通道无效')
            if not isinstance(selector, str) or len(selector) > 500:
                raise ValueError('选择器需要是最多500个字符的文字')
            platform = account_hub.normalize_platform(platform)
            account = account_hub.get(self.conn, platform, self._browser_account_id(aid))
            if platform == 'xiaohongshu':
                def capture(browser):
                    from .bb_bridge import capture_page, bb_page
                    pages = [p for p in browser.ctx.pages if not p.is_closed()]
                    if not pages: raise ValueError('账号浏览器没有可读取的页面')
                    visible = [p for p in pages if p.evaluate('document.visibilityState') == 'visible']
                    page = visible[-1] if visible else pages[-1]
                    if engine == 'bb-browser':
                        with bb_page(page, platform, account['id'], prof.get_profile_dir(account['id'])) as target:
                            return target.capture()
                    return capture_page(page)
                snapshot = self._call_xhs_browser(account['id'], capture, timeout=90)
            else:
                pool = self._dy_browsers if platform == 'douyin' else self._channels_browsers
                snapshot = pool.capture(account, engine=engine)
            from .crawl_bridge import extract
            result = extract(snapshot, selector)
            saved = materials.archive(self.conn, platform, account['id'], result)
            return self._ok(**saved, title=result['title'], source_url=result['url'],
                            engine=engine, extraction_engine='crawl4ai')
        except Exception as e: return self._err(e)

    def material_list(self, query='', platform=''):
        if not self.enable_extras: return self._err('素材库和浏览计划已冻结；核心账号、发布、私信和评论采集仍可使用')
        try:
            platform = account_hub.normalize_platform(platform) if platform else ''
            return self._ok(items=materials.list_materials(self.conn, query, platform))
        except Exception as e: return self._err(e)

    def material_get(self, mid):
        if not self.enable_extras: return self._err('素材库和浏览计划已冻结；核心账号、发布、私信和评论采集仍可使用')
        try: return self._ok(item=materials.get_material(self.conn, mid))
        except Exception as e: return self._err(e)

    def material_to_draft(self, mid):
        if not self.enable_extras: return self._err('素材库和浏览计划已冻结；核心账号、发布、私信和评论采集仍可使用')
        try:
            item = materials.get_material(self.conn, mid)
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            title = item['title'].strip() or '页面素材草稿'
            body = item['markdown'] + '\n\n来源：' + item['source_url']
            with self.conn:
                cursor = self.conn.execute(
                    'INSERT INTO drafts(title,body,topics,mode,media_json,template,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                    (title, body, '', 'images', '[]', 'rice', now, now))
            return self._ok(id=cursor.lastrowid)
        except Exception as e: return self._err(e)

