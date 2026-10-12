import json
import tempfile
import unittest
import threading
import concurrent.futures
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch, Mock
from app.webapp import Api
from app import dy_workspace as dy
from app import collector
from app import browser_plugin_bridge as plugin_bridge

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.api=Api(str(Path(self.tmp.name)/'test.db'))
        self.aid=self.api.dy_save_account({'nickname':'测试账号','open_id':'authorized-id'})['id']
    def tearDown(self):
        self.api.close_thread_conn(); self.tmp.cleanup()
    def sample(self):
        return json.dumps([{'comment_id':'c1','user_id':'u1','nickname':'同名用户','content':'价格多少'}, {'comment_id':'c2','user_id':'u2','nickname':'同名用户','content':'咨询地址'}, {'comment_id':'c3','content':'多少钱'}],ensure_ascii=False)
    def test_dedup_status_and_missing_user(self):
        r=self.api.dy_import(self.aid,'video1','video',self.sample())
        self.assertEqual((r['added'],r['matched']),(3,3))
        self.assertEqual(len(self.api.dy_leads()['items']),2)
        lead=self.api.dy_leads()['items'][0]
        self.api.dy_update_lead(lead['id'],'contacted','已联系')
        r=self.api.dy_import(self.aid,'video1','video',self.sample())
        self.assertEqual((r['added'],r['duplicates']),(0,3))
        self.api.dy_keywords(['价格']); self.api.dy_refilter()
        found=[l for l in self.api.dy_leads()['items'] if l['id']==lead['id']][0]
        self.assertEqual((found['status'],found['notes']),('contacted','已联系'))
        self.assertEqual(len(self.api.dy_comments(None,'',True)['items']),1)
    def test_same_source_distinct_accounts(self):
        other=self.api.dy_save_account({'nickname':'第二个'})['id']
        for aid in [self.aid,other]: self.assertEqual(self.api.dy_import(aid,'s','video',self.sample())['added'],3)
        self.assertEqual(len(self.api.dy_comments()['items']),6)
    def test_csv_and_atomic_invalid_import(self):
        valid='comment_id,user_id,nickname,content\nc1,u1,张三,"价格,地址"\n'
        self.assertEqual(self.api.dy_import(self.aid,'s','manual',valid)['added'],1)
        invalid=json.dumps([{'content':'价格'},{'nickname':'缺少内容'}])
        self.assertFalse(self.api.dy_import(self.aid,'bad','manual',invalid)['ok'])
        self.assertEqual(len(self.api.dy_comments()['items']),1)
        self.assertFalse(self.api.dy_import(999,'s','manual',valid)['ok'])
        self.assertFalse(self.api.dy_update_lead(999,'bad','')['ok'])
    def test_browser_scan_archives_distinct_ids_and_keeps_missing_id_comment(self):
        result = {'ok': True, 'items': [
            {'user_id': 'u1', 'nickname': '同名用户', 'text': '价格多少'},
            {'user_id': 'u2', 'nickname': '同名用户', 'text': '咨询地址'},
            {'user_id': '', 'nickname': '未提供ID', 'text': '多少钱'}]}
        with patch.object(self.api._dy_browsers, 'scan', return_value=result) as scan:
            first = self.api.dy_scan_video(self.aid, 'https://www.douyin.com/video/123')
            self.assertTrue(first['ok'])
            self.assertEqual((first['fetched'], first['added'], first['matched']), (3, 3, 3))
            self.assertEqual(scan.call_args.args[0]['id'], self.aid)
            self.assertEqual(len(self.api.dy_leads()['items']), 2)
            second = self.api.dy_scan_video(self.aid, 'https://www.douyin.com/video/123')
            self.assertEqual((second['added'], second['duplicates']), (0, 3))
        self.assertFalse(hasattr(self.api, 'dy_collect'))
        self.assertFalse(hasattr(dy, 'collect'))

    def test_browser_error_does_not_write_comments(self):
        with patch.object(self.api._dy_browsers, 'scan', return_value={
                'ok': False, 'error': '需要打开评论区', 'items': []}):
            result = self.api.dy_scan_video(self.aid, 'https://www.douyin.com/video/123')
        self.assertFalse(result['ok'])
        self.assertEqual(self.api.dy_comments()['items'], [])

    def test_export_respects_filter_and_prevents_formula(self):
        text=json.dumps([{'user_id':'u','nickname':'=SUM(1,2)','content':'价格'}])
        self.api.dy_import(self.aid,'source','manual',text)
        self.assertIn("'=SUM",self.api.dy_export('comments')['csv'])
        self.assertEqual(self.api.dy_export('comments',None,'不存在')['count'],0)
    def test_legacy_archive_count(self):
        item={'user_id':'user','nickname':'名字','text':'价格','contacts':{'wechat':'','phone':'','qq':''}}
        self.assertEqual(collector.archive_users(self.api.conn,'douyin','价格','url',[item]),1)
        self.assertEqual(collector.archive_users(self.api.conn,'douyin','价格','url',[item]),0)
        self.assertEqual(collector.archive_users(self.api.conn,'douyin','价格','url',[{**item,'user_id':''}]),0)
    def test_browser_url_validation(self):
        pool = dy.BrowserPool()
        with patch.object(pool, 'open') as opened:
            for url in ['https://example.com', 'http://www.douyin.com/video/123',
                        'https://www.douyin.com.evil.test/video/123',
                        'https://name@www.douyin.com/video/123']:
                with self.assertRaises(ValueError):
                    pool.scan({'id': 1}, url, ['价格'])
            with self.assertRaises(ValueError):
                pool.scan({'id': 1}, 'https://www.douyin.com/video/123', [])
            opened.assert_not_called()


class PluginBridgeTests(unittest.TestCase):
    def page(self, result):
        page = Mock()
        page.url = 'https://www.douyin.com/video/123'
        from app.page_guard import SCRIPT as GUARD_SCRIPT
        def evaluate(script, *args):
            if script == GUARD_SCRIPT: return None
            if script == 'globalThis.__matrixReadAccessGate()': return {'blocked':False}
            return result
        page.evaluate.side_effect = evaluate
        return page

    def test_bridge_matches_protocol_and_preserves_open_comment_page(self):
        page = self.page({'ok': True, 'items': [
            {'nickname': '同名用户', 'user_id': '', 'text': '价格多少', 'keyword': '价格'}],
                         'read': 3, 'stopped': True})
        result = plugin_bridge.scan_with_plugin(page, page.url, ['价格', '价格'], interval_ms=1000)
        self.assertTrue(result['ok'])
        self.assertTrue(result['stopped'])
        self.assertEqual(result['items'][0]['user_id'], '')
        self.assertEqual(result['items'][0]['contacts'], {'wechat': '', 'phone': '', 'qq': ''})
        page.goto.assert_not_called()
        page.wait_for_function.assert_called_once()
        script, options = page.evaluate.call_args.args
        self.assertIn('MATRIX_PLUGIN_SCAN', script)
        self.assertIn('MATRIX_PLUGIN_RESULT', script)
        self.assertIn('MATRIX_PLUGIN_CONTROL', script)
        self.assertTrue(options['requestId'])
        self.assertEqual(options['keywords'], ['价格'])
        self.assertEqual(options['intervalMs'], 2000)

    def test_bridge_plugin_error_and_unready_page_are_not_empty_success(self):
        page = self.page({'ok': False, 'items': [], 'error': '评论选择器需要校准'})
        result = plugin_bridge.scan_with_plugin(page, page.url, ['价格'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], '评论选择器需要校准')
        page.wait_for_function.side_effect = RuntimeError('extension missing')
        self.assertFalse(plugin_bridge.scan_with_plugin(page, page.url, ['价格'])['ok'])

    def test_bridge_rejects_wrong_platform_before_navigation(self):
        page = self.page({})
        for url, platform in [('https://example.com/video/123', 'douyin'),
                              ('https://www.douyin.com/video/123', 'xiaohongshu'),
                              ('https://channels.weixin.qq.com/', 'channels')]:
            self.assertFalse(plugin_bridge.scan_with_plugin(page, url, ['价格'], platform=platform)['ok'])
        page.goto.assert_not_called()
        page.evaluate.assert_not_called()

    def test_bridge_rejects_malformed_keywords_and_redirect(self):
        page = self.page({'ok': True, 'items': []})
        for words in ['价格', {'价格': True}, ['价格', {'invalid': 'word'}]]:
            self.assertFalse(plugin_bridge.scan_with_plugin(page, page.url, words)['ok'])
        page.evaluate.assert_not_called()
        page.url = 'https://example.com/redirected'
        self.assertFalse(plugin_bridge.scan_with_plugin(page,
                         'https://www.douyin.com/video/123', ['价格'])['ok'])
        page.evaluate.assert_not_called()

    def test_bridge_ignores_malformed_rows_and_identifiers(self):
        page = self.page({'ok': True, 'items': [
            {'text': {'content': '价格'}, 'user_id': 'bad'},
            {'text': '价格', 'user_id': 'invalid user', 'nickname': {'name': '名字'}}]})
        result = plugin_bridge.scan_with_plugin(page, page.url, ['价格'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual((result['items'][0]['user_id'], result['items'][0]['nickname']), ('', ''))

    def test_extension_launch_options_load_only_local_extension(self):
        options = plugin_bridge.extension_launch_options()
        self.assertEqual(options['channel'], 'chromium')
        self.assertEqual(options['ignore_default_args'], ['--disable-extensions'])
        self.assertEqual(options['args'], [f'--disable-extensions-except={plugin_bridge.PLUGIN_DIR}',
                                           f'--load-extension={plugin_bridge.PLUGIN_DIR}'])


class CommentField:
    def __init__(self, text='', href=None):
        self.text, self.href = text, href
        self.first = self
    def inner_text(self, **kwargs): return self.text
    def get_attribute(self, name, **kwargs): return self.href if name == 'href' else None


class CommentItem:
    def __init__(self, nickname, text, href=None):
        self.nickname, self.text, self.href = nickname, text, href
    def locator(self, selector):
        from app.douyin import SELECTORS
        return (CommentField(self.text) if selector == SELECTORS['comment']['text']
                else CommentField(self.nickname, self.href))


class CommentPage:
    url = 'https://www.douyin.com/video/123'
    def __init__(self, snapshots):
        self.snapshots, self.index, self.hovered = snapshots, 0, False
        self.mouse = SimpleNamespace(wheel=self.wheel)
        self.first = self
        self.navigation = []
    def goto(self, url, **kwargs): self.navigation.append(url); self.url = url
    def wait_for_timeout(self, milliseconds): pass
    def wait_for_selector(self, selector, **kwargs):
        if not self.snapshots or not self.snapshots[0]: raise TimeoutError('no comments')
    def locator(self, selector): return self
    def all(self): return self.snapshots[min(self.index, len(self.snapshots)-1)]
    def is_visible(self, **kwargs): return True
    def hover(self, **kwargs): self.hovered = True
    def wheel(self, x, y):
        if not self.hovered: raise AssertionError('must scroll comment panel')
        self.index += 1


class BrowserCollectionTests(unittest.TestCase):
    def test_capture_before_scroll_dedup_and_missing_identifiers(self):
        first = CommentItem('同名用户', 'PRICE 价格', '/user/user-one')
        second = CommentItem('同名用户', '咨询地址', '/user/user-two')
        missing = CommentItem('缺少标识', '价格多少')
        external = CommentItem('外部链接', '价格多少', 'https://example.com/user/fake')
        page = CommentPage([[first], [first, second, missing, external], [second]])
        result = collector.scan_video_comments(page, page.url, ['price', '咨询', '价格'],
                                               platform='douyin', scroll_times=2)
        self.assertTrue(result['ok'])
        self.assertEqual(result['read'], 4)
        self.assertEqual([r['user_id'] for r in result['items']], ['user-one', 'user-two', '', ''])
        self.assertEqual(result['items'][0]['keyword'], 'price')
        self.assertEqual(page.navigation, [])
        self.assertTrue(page.hovered)

    def test_absent_comment_panel_reports_error(self):
        result = collector.scan_video_comments(CommentPage([]), CommentPage.url,
                                               ['价格'], platform='douyin')
        self.assertFalse(result['ok'])
        self.assertIn('页面选择器可能需要校准', result['error'])

    def test_profile_link_must_belong_to_platform(self):
        self.assertEqual(collector._profile_user_id('/user/u1', CommentPage.url, 'douyin'), 'u1')
        self.assertEqual(collector._profile_user_id('/user/profile/x1',
                         'https://www.xiaohongshu.com/explore/123', 'xiaohongshu'), 'x1')
        for href in ['https://example.com/user/u1', 'javascript:/user/u1',
                     '/user/u%2Fother', '/user/u%20other', '/user/']:
            self.assertEqual(collector._profile_user_id(href, CommentPage.url, 'douyin'), '')

    def test_browser_reuses_account_session_and_scans_on_its_worker(self):
        contexts, folders, worker_ids = [], [], []
        class Page:
            def goto(self, *args, **kwargs): pass
            def is_closed(self): return False
            def bring_to_front(self): pass
            def wait_for_timeout(self, ms): threading.Event().wait(0.005)
        class Context:
            def __init__(self): self.pages = [Page()]
            def close(self): self.pages.clear()
        def launch(folder, **kwargs):
            folders.append(folder)
            context = Context()
            contexts.append(context)
            return context
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        class Playwright:
            def __enter__(self): return playwright
            def __exit__(self, *args): pass
        def scan(page, url, words, **kwargs):
            worker_ids.append(threading.get_ident())
            self.assertIs(page, contexts[0].pages[0])
            self.assertEqual(kwargs['platform'], 'douyin')
            return {'ok': True, 'items': []}
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', return_value=Playwright()), \
                patch.object(plugin_bridge, 'scan_with_plugin', side_effect=scan):
            pool = dy.BrowserPool()
            try:
                first = pool.open({'id': 1})
                self.assertIs(pool.open({'id': 1}), first)
                second = pool.open({'id': 2})
                self.assertIsNot(first, second)
                self.assertEqual(len(folders), 2)
                self.assertNotEqual(folders[0], folders[1])
                result = pool.scan({'id': 1}, CommentPage.url, ['价格'])
                self.assertTrue(result['ok'])
                self.assertEqual(worker_ids, [first['thread'].ident])
                self.assertNotEqual(worker_ids[0], threading.get_ident())
            finally:
                for context in contexts: context.close()
                for session in pool.sessions.values(): session['thread'].join(timeout=1)
                self.assertTrue(all(s['closed'] for s in pool.sessions.values()))

    def test_channels_and_douyin_same_id_use_separate_profiles(self):
        folders, contexts, urls = [], [], []
        class Page:
            def goto(self, url, **kwargs): urls.append(url)
            def is_closed(self): return False
            def bring_to_front(self): pass
            def wait_for_timeout(self, ms): threading.Event().wait(0.005)
        class Context:
            def __init__(self): self.pages = [Page()]
            def close(self): self.pages.clear()
        def launch(folder, **kwargs):
            folders.append(folder)
            context = Context()
            contexts.append(context)
            return context
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        class Playwright:
            def __enter__(self): return playwright
            def __exit__(self, *args): pass
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', return_value=Playwright()):
            douyin_pool = dy.BrowserPool()
            channels_pool = dy.BrowserPool('channels')
            pools = [douyin_pool, channels_pool]
            try:
                for pool in pools:
                    self.assertEqual(pool.running_ids(), [])
                    pool.open({'id': 7})
                    self.assertEqual(pool.running_ids(), [7])
                self.assertEqual(folders, [str(Path(temp)/'douyin_profiles'/'7'),
                                          str(Path(temp)/'channels_profiles'/'7')])
                self.assertEqual(urls, ['https://www.douyin.com/', 'https://channels.weixin.qq.com/'])
                with self.assertRaisesRegex(ValueError, '仅支持抖音'):
                    channels_pool.scan({'id': 7}, CommentPage.url, ['价格'])
                with self.assertRaisesRegex(ValueError, '不支持'):
                    dy.BrowserPool('unknown')
            finally:
                for context in contexts: context.close()
                for pool in pools:
                    for session in pool.sessions.values(): session['thread'].join(timeout=1)
                    self.assertEqual(pool.running_ids(), [])

    def test_perform_and_capture_require_an_existing_selected_browser(self):
        pool = dy.BrowserPool()
        with patch.object(pool, 'open') as opened:
            with self.assertRaisesRegex(ValueError, '请先打开'):
                pool.perform({'id':1}, lambda page: 'never')
            with self.assertRaisesRegex(ValueError, '请先打开'):
                pool.capture({'id':1})
            opened.assert_not_called()

    def test_same_account_concurrent_scan_is_rejected(self):
        pool = dy.BrowserPool()
        pending = concurrent.futures.Future()
        session = {'closed': False, 'scan_future': pending, 'tasks': __import__('queue').Queue()}
        with patch.object(pool, 'open', return_value=session):
            with self.assertRaisesRegex(ValueError, '已有采集任务'):
                pool.scan({'id': 1}, CommentPage.url, ['价格'])
        self.assertTrue(session['tasks'].empty())
        self.assertIs(session['scan_future'], pending)

    def test_closed_tab_keeps_other_tab_and_cleanup_precedes_playwright_stop(self):
        order = []
        surviving = threading.Event()
        contexts = []
        class Page:
            def __init__(self, first=False): self.closed = False; self.first = first
            def goto(self, *args, **kwargs): pass
            def is_closed(self): return self.closed
            def bring_to_front(self): pass
            def wait_for_timeout(self, ms):
                if self.first:
                    self.closed = True
                    raise RuntimeError('tab closed')
                surviving.set()
                threading.Event().wait(0.005)
        class Context:
            def __init__(self): self.pages = [Page(first=True), Page()]; self.closed = False
            def close(self):
                self.closed = True
                self.pages.clear()
                order.append('context_close')
        def launch(*args, **kwargs):
            context = Context(); contexts.append(context); return context
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        class Playwright:
            def __enter__(self): return playwright
            def __exit__(self, *args): order.append('playwright_stop')
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', return_value=Playwright()):
            pool = dy.BrowserPool()
            session = pool.open({'id': 1})
            try:
                self.assertTrue(surviving.wait(1))
                self.assertFalse(contexts[0].closed)
                self.assertEqual(pool.running_ids(), [1])
            finally:
                contexts[0].pages.clear()
                session['thread'].join(timeout=1)
            self.assertFalse(session['thread'].is_alive())
            self.assertEqual(order, ['context_close', 'playwright_stop'])

    def test_failed_browser_launch_never_uses_another_profile(self):
        calls = []
        def launch(folder, **kwargs):
            calls.append(folder)
            raise RuntimeError('browser component unavailable')
        context = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
        class Playwright:
            def __enter__(self): return context
            def __exit__(self, *args): pass
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', return_value=Playwright()):
            pool = dy.BrowserPool()
            for aid in [1, 2]:
                with self.assertRaisesRegex(ValueError, '浏览器启动失败'):
                    pool.open({'id': aid})
            self.assertEqual(calls, [str(Path(temp)/'douyin_profiles'/'1'),
                                     str(Path(temp)/'douyin_profiles'/'2')])
            self.assertTrue(all(s['closed'] for s in pool.sessions.values()))

    def test_close_all_cleans_each_context_on_worker_before_playwright_stop(self):
        order = []
        class Page:
            def goto(self, *args, **kwargs): pass
            def is_closed(self): return False
            def bring_to_front(self): pass
            def wait_for_timeout(self, ms): threading.Event().wait(.005)
        class Context:
            def __init__(self): self.pages = [Page()]
            def close(self):
                order.append(('context', threading.get_ident()))
                self.pages.clear()
        class Playwright:
            def __enter__(self):
                return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a, **kw: Context()))
            def __exit__(self, *args): order.append(('playwright', threading.get_ident()))
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', side_effect=lambda: Playwright()):
            pool = dy.BrowserPool()
            first = pool.open({'id': 1})
            second = pool.open({'id': 2})
            result = pool.close_all(timeout=1)
            self.assertEqual(result, {'closed': [1, 2], 'pending': []})
            for session in (first, second):
                self.assertFalse(session['thread'].is_alive())
                self.assertTrue(session['closed'])
                own_order = [event for event, ident in order if ident == session['thread'].ident]
                self.assertEqual(own_order, ['context', 'playwright'])
            self.assertEqual(pool.running_ids(), [])
            with self.assertRaisesRegex(ValueError, '正在关闭'):
                pool.open({'id': 3})
            self.assertEqual(pool.close_all(timeout=0)['pending'], [])

    def test_close_all_bounds_wait_and_fails_active_and_queued_waiters(self):
        entered, release = threading.Event(), threading.Event()
        cleanup = []
        class Page:
            def goto(self, *args, **kwargs): pass
            def is_closed(self): return False
            def bring_to_front(self): pass
            def wait_for_timeout(self, ms): threading.Event().wait(.005)
        class Context:
            def __init__(self): self.pages = [Page()]
            def close(self): cleanup.append('context'); self.pages.clear()
        class Playwright:
            def __enter__(self):
                return SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=lambda *a, **kw: Context()))
            def __exit__(self, *args): cleanup.append('playwright')
        with tempfile.TemporaryDirectory() as temp, patch.object(dy, 'DATA_DIR', temp), \
                patch('playwright.sync_api.sync_playwright', return_value=Playwright()):
            pool = dy.BrowserPool()
            session = pool.open({'id': 1})
            running = concurrent.futures.Future()
            def busy(page):
                entered.set()
                release.wait(2)
                return 'late callback result'
            session['tasks'].put((busy, running))
            queued = concurrent.futures.Future()
            try:
                self.assertTrue(entered.wait(1))
                session['tasks'].put((lambda page: self.fail('queued task ran after close'), queued))
                with patch.object(dy.logging, 'warning') as warning:
                    result = pool.close_all(timeout=.01)
                self.assertEqual(result, {'closed': [], 'pending': [1]})
                warning.assert_called_once()
                self.assertTrue(session['thread'].is_alive())
                for future in (running, queued):
                    with self.assertRaisesRegex(ValueError, '工作台已关闭'):
                        future.result(timeout=.1)
                with self.assertRaisesRegex(ValueError, '正在关闭'):
                    pool.perform({'id':1}, lambda page: 'never')
            finally:
                release.set()
                session['thread'].join(timeout=1)
            self.assertFalse(session['thread'].is_alive())
            self.assertEqual(cleanup, ['context', 'playwright'])

if __name__ == '__main__': unittest.main()
