"""Selected-account API flows, with local DBs and no platform requests."""
from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import bb_bridge, crawl_bridge
from app.webapp import Api


class FixturePage:
    url = 'https://www.xiaohongshu.com/explore/local-note?note_id=fixture'

    def __init__(self, title='本地正文', visible=True):
        self._title, self.visible = title, visible
        self.operations = []

    def is_closed(self): return False
    def title(self): return self._title
    def evaluate(self, expression, arg=None):
        self.operations.append((expression, arg))
        from app.page_guard import SCRIPT as GUARD_SCRIPT
        if expression == GUARD_SCRIPT: return None
        if expression == 'globalThis.__matrixReadAccessGate()': return {'blocked':False}
        if expression == 'document.visibilityState':
            return 'visible' if self.visible else 'hidden'
        if expression == bb_bridge.CAPTURE_SCRIPT:
            return {'url': self.url, 'title': self._title,
                    'html': '<main><h1>本地标题</h1><p>已登录当前页面的中文正文。</p>'
                            '<a href="https://example.test/article?id=7">资料</a></main>'}
        raise AssertionError('Unexpected browser operation')


class ImmediateActor:
    def __init__(self, *pages):
        self.browser = SimpleNamespace(ctx=SimpleNamespace(pages=list(pages)))

    def call(self, fn, **kw): return fn(self.browser)
    def is_dead(self): return False


class MaterialApiTests(unittest.TestCase):
    def setUp(self):
        selection = patch('app.plugins.resolve_comments', side_effect=lambda engine: {'requested_engine':engine,'engine':engine,'degraded':False,'warning':''})
        selection.start(); self.addCleanup(selection.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.api = Api(str(Path(self.tmp.name) / 'api.db'), enable_extras=True)
        self.xhs = self.api.hub_save('xiaohongshu', {'nickname': '账号一'})['id']
        self.other = self.api.hub_save('xiaohongshu', {'nickname': '账号二'})['id']
        self.dy = self.api.hub_save('douyin', {'nickname': '抖音账号'})['id']
        self.page = FixturePage()
        self.api._actors[self.xhs] = ImmediateActor(self.page)
        self.api._actors[self.other] = ImmediateActor(FixturePage('另一个账号'))
        self.result = {'url': self.page.url, 'title': '本地标题', 'markdown': '本地正文',
                       'links': [], 'metadata': {}, 'engine': 'crawl4ai', 'version': 'test'}
        self.scan = {'ok': True, 'items': [{'nickname': '访客', 'user_id': 'profile-id',
                    'text': '多少钱', 'keyword': '多少钱', 'contacts': {}}]}

    def tearDown(self):
        self.api._warmup.close()
        self.api.close_thread_conn()
        self.tmp.cleanup()

    def test_dy_and_xhs_forward_selected_engine(self):
        with patch.object(self.api._dy_browsers, 'scan', return_value=self.scan) as scanned:
            response = self.api.hub_collect('douyin', self.dy, 'https://www.douyin.com/video/fixture', 'bb-browser')
            self.assertTrue(response['ok'], response)
            self.assertEqual(scanned.call_args.kwargs, {'engine': 'bb-browser'})
        adapter = object()
        with patch('app.bb_bridge.bb_page', return_value=nullcontext(adapter)) as opened, patch(
                'app.browser_plugin_bridge.scan_with_plugin', return_value=self.scan) as scanned:
            response = self.api.hub_collect('xiaohongshu', self.xhs, self.page.url, 'bb-browser')
        self.assertTrue(response['ok'], response)
        self.assertIs(scanned.call_args.args[0], adapter)
        self.assertEqual(scanned.call_args.kwargs['platform'], 'xiaohongshu')
        self.assertEqual(opened.call_args.args[1:3], ('xiaohongshu', self.xhs))
        with patch.object(self.api._dy_browsers, 'capture', return_value={}) as captured, patch(
                'app.crawl_bridge.extract', return_value=self.result):
            response = self.api.material_capture('douyin', self.dy, 'main', 'plugin')
        self.assertTrue(response['ok'], response)
        self.assertEqual(captured.call_args.kwargs, {'engine': 'plugin'})

    def test_missing_tools_and_invalid_inputs_never_archive_or_enqueue(self):
        missing = str(Path(self.tmp.name) / 'missing-tool')
        with patch.dict(os.environ, {'MATRIX_BB_BROWSER_DIR': missing}):
            response = self.api.material_capture('xiaohongshu', self.xhs)
        self.assertFalse(response['ok'])
        self.assertIn('未安装', response['error'])
        with patch.dict(os.environ, {'MATRIX_CRAWL4AI_PYTHON': missing}):
            response = self.api.material_capture('xiaohongshu', self.xhs, engine='plugin')
        self.assertFalse(response['ok'])
        self.assertIn('尚未安装', response['error'])
        self.assertEqual(self.api.material_list()['items'], [])
        with patch.object(self.api, '_call_xhs_browser') as submitted, patch.object(
                self.api._dy_browsers, 'scan') as scanned:
            for aid, selector, engine in [(True, '', 'plugin'), (self.xhs, [], 'plugin'),
                                          (self.xhs, 'a' * 501, 'plugin'), (self.xhs, '', 'invalid')]:
                self.assertFalse(self.api.material_capture('xiaohongshu', aid, selector, engine)['ok'])
            for url in [None, {}, 'file:///private', 'https://other.test/',
                        'https://secret@www.xiaohongshu.com/explore/a']:
                self.assertFalse(self.api.hub_collect('xiaohongshu', self.xhs, url)['ok'])
                self.assertFalse(self.api.dy_scan_video(self.dy, url)['ok'])
            submitted.assert_not_called()
            scanned.assert_not_called()

    def test_tool_status_error_is_api_response(self):
        with patch('app.bb_bridge.status', side_effect=RuntimeError('工具检查失败')):
            self.assertTrue(self.api.tool_status()['tools']['builtin']['available'])

    def test_capture_requires_open_browser_and_surfaces_old_cdp_session_error(self):
        self.api._actors.pop(self.xhs)
        with patch.object(self.api, '_ensure_actor') as opened:
            response = self.api.material_capture('xiaohongshu', self.xhs, engine='plugin')
            self.assertFalse(response['ok'])
            self.assertIn('请先打开', response['error'])
            opened.assert_not_called()
        self.api._actors[self.xhs] = ImmediateActor(self.page)
        error = '该账号浏览器未启用独立 bb 通道或档案不匹配，请关闭并重新打开该账号浏览器'
        with patch('app.bb_bridge._runtime', return_value=(Path('.'), '/unused', Path('daemon'), Path('cli'))), patch('app.bb_bridge._selected_endpoint', side_effect=ValueError(error)), patch(
                'app.crawl_bridge.extract') as extracted:
            response = self.api.material_capture('xiaohongshu', self.xhs)
        self.assertEqual(response, {'ok': False, 'error': error})
        extracted.assert_not_called()
        self.assertEqual(self.api.material_list()['items'], [])

    def test_current_visible_page_real_crawl_dedup_and_draft_without_publish(self):
        if not crawl_bridge._python_path().is_file():
            self.skipTest('Optional Crawl4AI runtime is not installed')
        hidden = FixturePage('不应读取的后台页', visible=False)
        self.api._actors[self.xhs] = ImmediateActor(hidden, self.page)
        actual_extract = crawl_bridge.extract
        def offline_extract(snapshot, selector):
            return actual_extract(snapshot, selector, state_dir=Path(self.tmp.name) / 'crawl-state')
        # Playwright capture must work even if bb-browser is unavailable.
        with patch.dict(os.environ, {'MATRIX_BB_BROWSER_DIR': self.tmp.name}), patch(
                'app.crawl_bridge.extract', side_effect=offline_extract):
            first = self.api.material_capture('xiaohongshu', self.xhs, 'main', 'plugin')
            second = self.api.material_capture('xiaohongshu', self.xhs, 'main', 'plugin')
        self.assertTrue(first['ok'], first)
        self.assertTrue(first['added'])
        self.assertTrue(second['ok'], second)
        self.assertFalse(second['added'])
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(first['source_url'], self.page.url)
        self.assertFalse(any(expr == bb_bridge.CAPTURE_SCRIPT for expr, _ in hidden.operations))
        item = self.api.material_get(first['id'])['item']
        self.assertIn('已登录当前页面', item['markdown'])
        self.assertEqual(item['engine'], 'crawl4ai')
        self.assertTrue(item['engine_version'])
        with patch('app.xhs_api.pub.click_publish') as published:
            draft = self.api.material_to_draft(first['id'])
            published.assert_not_called()
        self.assertTrue(draft['ok'], draft)
        row = self.api.conn.execute('SELECT * FROM drafts WHERE id=?', (draft['id'],)).fetchone()
        self.assertIn(self.page.url, row['body'])
        for table in ['publish_records', 'publish_queue']:
            self.assertEqual(self.api.conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_warmup_conflicts_reject_same_account_but_allow_another(self):
        entered, release = threading.Event(), threading.Event()
        class Control:
            active_seconds = 0
            def cancelled(self): return False
            def wait_active(self, seconds):
                entered.set()
                if not release.wait(5): raise AssertionError('Test runner did not release dwell')
                self.active_seconds += seconds
                return seconds
        outcomes = []
        def run():
            try:
                outcomes.append(self.api._run_browse_target(
                    {'kind': 'browser', 'platform': 'xiaohongshu', 'id': self.xhs},
                    1, 'plugin', Control(), lambda elapsed: None))
            except Exception as error: outcomes.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with patch('app.crawl_bridge.extract', return_value=self.result), patch(
                    'app.browser_plugin_bridge.scan_with_plugin', return_value=self.scan) as scanned:
                for response in [self.api.material_capture('xiaohongshu', self.xhs, engine='plugin'),
                                 self.api.hub_collect('xiaohongshu', self.xhs, self.page.url)]:
                    self.assertFalse(response['ok'])
                    self.assertIn('正在执行', response['error'])
                scanned.assert_not_called()
                other = self.api.material_capture('xiaohongshu', self.other, engine='plugin')
                self.assertTrue(other['ok'], other)
        finally:
            release.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [1])
        self.assertEqual(self.api._browser_jobs, {})

    def test_queued_timeout_callback_is_cancelled_before_browser_effects(self):
        class QueuedActor:
            def call(self, fn, **kw):
                self.callback = fn
                raise TimeoutError('浏览器任务等待超时')
            def is_dead(self): return False
        actor = QueuedActor()
        self.api._actors[self.xhs] = actor
        with patch('app.bb_bridge.capture_page') as captured:
            response = self.api.material_capture('xiaohongshu', self.xhs, engine='plugin')
            self.assertFalse(response['ok'])
            with self.assertRaisesRegex(ValueError, '已取消'):
                actor.callback(ImmediateActor(self.page).browser)
            captured.assert_not_called()
        self.assertEqual(self.api._browser_jobs, {})
        self.assertEqual(self.api.material_list()['items'], [])

    def test_running_timeout_keeps_account_busy_until_callback_finishes(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        errors = []
        class RunningActor:
            def is_dead(self): return False
            def call(inner, fn, **kw):
                def execute():
                    try: fn(ImmediateActor(self.page).browser)
                    except Exception as error: errors.append(error)
                    finally: done.set()
                inner.thread = threading.Thread(target=execute)
                inner.thread.start()
                if not entered.wait(5): raise AssertionError('Callback did not start')
                raise TimeoutError('浏览器任务等待超时')
        actor = RunningActor()
        self.api._actors[self.xhs] = actor
        def blocked_capture(page):
            entered.set()
            if not release.wait(5): raise AssertionError('Capture was not released')
            return {'url': page.url, 'html': '<p>本地正文</p>'}
        try:
            with patch('app.bb_bridge.capture_page', side_effect=blocked_capture), patch(
                    'app.crawl_bridge.extract') as extracted:
                self.assertFalse(self.api.material_capture('xiaohongshu', self.xhs, engine='plugin')['ok'])
                response = self.api.hub_collect('xiaohongshu', self.xhs, self.page.url)
                self.assertFalse(response['ok'])
                self.assertIn('正在执行', response['error'])
                extracted.assert_not_called()
                release.set()
                self.assertTrue(done.wait(5))
        finally:
            release.set()
            actor.thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.api._browser_jobs, {})
        self.assertEqual(self.api.material_list()['items'], [])


if __name__ == '__main__': unittest.main()
