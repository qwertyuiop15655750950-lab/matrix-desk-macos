"""The bb visible-comment / real offline Crawl4AI pipeline on local fixtures."""
from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from app import bb_bridge, crawl_bridge
from app.comment_crawler import scan_comments
from app.webapp import Api


ROWS = [{'comment_id': 'c1', 'user_id': 'real-user', 'nickname': '原作者', 'content': 'Price 价格，微信 example_1'},
        {'comment_id': 'c2', 'user_id': '', 'nickname': '无ID用户', 'content': '价格多少'},
        {'comment_id': 'c3', 'user_id': 'other', 'nickname': '观众', 'content': '好视频'}]
FRAGMENT = '<article class="comment-item" data-comment-id="c1"><a class="author" href="/user/real-user">原作者</a><p class="comment-text">价格多少</p></article>'


class FakeAdapter:
    url = 'https://www.douyin.com/video/fixture'
    def __init__(self, state='normal'):
        self.mode, self.captured, self.scrolls = state, 0, 0
        self.clock, self.progress, self.waits = 0.0, [], []
    def goto(self, url, **kw): self.url = url
    def start_comment_job(self): pass
    def comment_state(self):
        if self.mode == 'stop' and self.captured: return {'stopped': True, 'reason': 'user'}
        if self.mode == 'changed' and self.captured: return {'stopped': True, 'reason': 'page_changed'}
        return {'stopped': False, 'paused': False}
    def comment_snapshot(self):
        self.captured += 1
        return {'url': self.url, 'title': '评论', 'fragments': [FRAGMENT]}
    def comment_progress(self, read, text, finished=False): self.progress.append((read, text, finished))
    def scroll_comments(self): self.scrolls += 1; return self.scrolls <= 1
    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds); self.clock += milliseconds / 1000


class CommentCrawlerTests(unittest.TestCase):
    def test_matching_dedup_contacts_and_real_missing_identity(self):
        page = FakeAdapter()
        extracted = {'rows': ROWS + [ROWS[0]], 'engine': 'crawl4ai', 'version': '0.9.4'}
        with patch('app.crawl_bridge.extract_comments', return_value=extracted) as extract:
            result = scan_comments(page, page.url, ['PRICE', '价格'], scroll_times=1)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['read'], 3)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['items'][0]['keyword'], 'PRICE')
        self.assertEqual(result['items'][0]['contacts']['wechat'], 'example_1')
        self.assertEqual(result['items'][1]['user_id'], '')
        self.assertEqual(result['items'][0]['comment_id'], 'c1')
        self.assertEqual(result['browser_engine'], 'bb-browser')
        self.assertEqual(result['extraction_engine'], 'crawl4ai')
        self.assertEqual(extract.call_args.args[0]['html'].count(FRAGMENT), 1)
        self.assertTrue(page.progress[-1][-1])

    def test_tracking_changes_preserve_drawer_and_wrong_redirect_rejected(self):
        page = FakeAdapter()
        page.url += '?token=PRIVATE&video_id=7'
        with patch.object(page, 'goto', wraps=page.goto) as navigated, patch(
                'app.crawl_bridge.extract_comments', return_value={'rows': ROWS, 'engine':'crawl4ai'}):
            result = scan_comments(page, 'https://douyin.com/video/fixture?video_id=7&token=DIFFERENT', ['价格'], scroll_times=0)
        navigated.assert_not_called()
        self.assertNotIn('PRIVATE', result['sourceUrl'])
        self.assertEqual(result['sourceUrl'], 'https://www.douyin.com/video/fixture?video_id=7')
        with patch.object(page, 'goto', side_effect=lambda *args, **kw: setattr(page, 'url', 'https://www.douyin.com/video/wrong')), patch(
                'app.crawl_bridge.extract_comments') as extracted, patch.object(page, 'start_comment_job') as started:
            with self.assertRaisesRegex(ValueError, '不是指定内容'):
                scan_comments(page, 'https://www.douyin.com/video/requested', ['价格'])
        extracted.assert_not_called(); started.assert_not_called()

    def test_pause_waits_before_capture_and_stable_identity_does_not_stop_early(self):
        page = FakeAdapter()
        states = iter([{'paused':True}, {'paused':False}])
        state_calls = []
        def state():
            result = next(states, {'paused':False})
            state_calls.append(result); return result
        snapshots = iter([{'url':page.url, 'fragments':[FRAGMENT], 'identities':['id:c1']},
                          {'url':page.url, 'fragments':[FRAGMENT+'<!--like-count-changed-->'], 'identities':['id:c1']}])
        with patch.object(page, 'comment_state', side_effect=state), patch.object(
                page, 'comment_snapshot', side_effect=lambda: next(snapshots)), patch(
                'app.crawl_bridge.extract_comments', return_value={'rows': ROWS, 'engine':'crawl4ai'}) as extracted:
            result = scan_comments(page, page.url, ['价格'], max_comments=1, scroll_times=1)
        self.assertTrue(result['ok'])
        self.assertTrue(state_calls[0]['paused'])
        self.assertEqual(page.waits[0],250)
        self.assertEqual(page.scrolls,1)
        self.assertNotIn('like-count-changed', extracted.call_args.args[0]['html'])

    def test_user_stop_and_navigation_preserve_partial_and_no_new_page_capture(self):
        for mode in ['stop', 'changed']:
            page = FakeAdapter(mode)
            with patch('app.crawl_bridge.extract_comments', return_value={'rows': ROWS, 'engine': 'crawl4ai'}):
                result = scan_comments(page, page.url, ['价格'])
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['stopped'])
            self.assertFalse(result['timed_out'])
            self.assertEqual(page.captured, 1)
            self.assertEqual(result['stop_reason'], 'user' if mode == 'stop' else 'page_changed')
            self.assertEqual(result['read'], 3)

    def test_timeout_retains_offline_rows_and_finishes_control(self):
        page = FakeAdapter()
        with patch('app.comment_crawler.time.monotonic', side_effect=lambda: page.clock), patch(
                'app.crawl_bridge.extract_comments', return_value={'rows': ROWS, 'engine': 'crawl4ai'}):
            result = scan_comments(page, page.url, ['价格'], timeout_seconds=40.1)
        self.assertTrue(result['timed_out'])
        self.assertTrue(result['stopped'])
        self.assertEqual(result['read'], 3)
        self.assertEqual(page.captured, 1)
        self.assertTrue(page.progress[-1][-1])

    def test_invalid_platform_words_and_empty_extraction_are_explicit(self):
        page = FakeAdapter()
        with patch('app.crawl_bridge.extract_comments') as extracted:
            for url, platform, words in [('https://foreign.test/', 'douyin', ['价格']),
                                         (page.url, 'channels', ['价格']), (page.url, 'douyin', []),
                                         (page.url, 'douyin', [1])]:
                with self.assertRaises(ValueError): scan_comments(page, url, words, platform)
            extracted.assert_not_called()
        with patch('app.crawl_bridge.extract_comments', return_value={'rows': [], 'engine': 'crawl4ai'}):
            result = scan_comments(page, page.url, ['价格'], scroll_times=0)
        self.assertFalse(result['ok'])
        self.assertIn('选择器', result['error'])

    def test_real_bb_dynamic_dom_and_crawl_chain_keeps_author_scope_and_loaded_only(self):
        if not crawl_bridge._python_path().is_file(): self.skipTest('Optional Crawl4AI not installed')
        from app import bb_bridge
        try: bb_bridge._runtime()
        except (ValueError, OSError): self.skipTest('Optional development bb-browser not installed')
        body = '''<!doctype html><html><head><meta charset="utf-8"><title>本地动态评论</title>
        <style>.hidden-comment{display:none}</style></head><body>
        <main class="comment-list" data-e2e="comment-list" style="height:180px;overflow-y:auto">
        <article class="comment-item" data-comment-id="parent"><span class="author">没有公开ID的作者</span>
        <p class="comment-text">价格多少 <a href="/user/mention">提及用户</a></p>
        <article class="comment-item" data-comment-id="reply"><a class="author" href="/user/reply-user?token=LINK_SECRET">回复者</a><p class="comment-text">价格回复</p></article></article>
        <article class="comment-item hidden-comment"><a class="author" href="/user/hidden">隐藏作者</a><p class="comment-text">价格 HIDDEN_SECRET</p></article>
        <div style="height:1500px"></div></main>
        <input value="INPUT_SECRET"><script>window.unpublished='STATE_SECRET';
        const list=document.querySelector('main');let added=false;
        list.addEventListener('scroll',()=>{if(added)return;added=true;
        const row=document.createElement('article');row.className='comment-item';row.dataset.commentId='dynamic';
        row.innerHTML='<a class="author" href="/user/dynamic-user?token=LINK_SECRET">动态用户</a><p class="comment-text">价格动态加载</p>';
        list.prepend(row);});</script></body></html>'''
        actual_extract = crawl_bridge.extract_comments
        captured = []
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(folder, headless=True,
                        channel='chromium', args=bb_bridge.debug_launch_args())
            try:
                context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=body))
                page = context.pages[0]
                page.goto('https://www.douyin.com/video/dynamic?token=URL_SECRET')
                def extract(snapshot, platform, **kwargs):
                    captured.append(snapshot)
                    return actual_extract(snapshot, platform, state_dir=Path(folder)/'crawl-state', **kwargs)
                with bb_bridge.bb_page(page, 'douyin', 7, folder) as adapter, patch(
                        'app.crawl_bridge.extract_comments', side_effect=extract):
                    result = scan_comments(adapter, page.url, ['价格'], scroll_times=1, interval_ms=2000)
                self.assertTrue(result['ok'], result)
                by_id = {row['comment_id']:row for row in result['items']}
                self.assertEqual(set(by_id), {'parent','reply','dynamic'})
                self.assertEqual(by_id['parent']['user_id'], '')
                self.assertEqual(by_id['parent']['nickname'], '没有公开ID的作者')
                self.assertEqual(by_id['reply']['user_id'], 'reply-user')
                self.assertEqual(by_id['dynamic']['user_id'], 'dynamic-user')
                self.assertNotIn('价格回复', by_id['parent']['text'])
                self.assertNotIn('SECRET', captured[0]['html'])
                self.assertNotIn('SECRET', result['sourceUrl'])
                self.assertEqual(result['read'], 3)
                self.assertFalse(page.is_closed())
                self.assertTrue(page.evaluate("document.getElementById('matrix-crawl-collector').shadowRoot.getElementById('stop').disabled"))
            finally: context.close()


    def test_real_xhs_chain_and_crawler_pause_stop_controls(self):
        if not crawl_bridge._python_path().is_file(): self.skipTest('Optional Crawl4AI not installed')
        from app import bb_bridge
        try: bb_bridge._runtime()
        except (ValueError, OSError): self.skipTest('Optional development bb-browser not installed')
        body = '<html><head><meta charset="utf-8"></head><body><main class="comments-container" style="height:100px;overflow:auto">' \
               '<article class="comment-item" data-comment-id="xhs-one"><div class="author"><a class="name" href="/user/profile/xhs-user?token=SECRET">小红书作者</a></div><div class="content"><span class="note-text">价格咨询</span></div></article><div style="height:1800px"></div></main></body></html>'
        actual_extract = crawl_bridge.extract_comments
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(folder, headless=True,
                        channel='chromium', args=bb_bridge.debug_launch_args())
            try:
                context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=body))
                page = context.pages[0]
                page.goto('https://www.xiaohongshu.com/explore/fixture')
                with bb_bridge.bb_page(page, 'xiaohongshu', 8, folder) as adapter:
                    original_progress = adapter.comment_progress
                    scheduled = False
                    def progress(read, text, finished=False):
                        nonlocal scheduled
                        result = original_progress(read, text, finished)
                        if read and not scheduled:
                            scheduled = True
                            page.evaluate("""() => {
                                const root=document.getElementById('matrix-crawl-collector').shadowRoot;
                                root.getElementById('pause').click();
                                window.fixturePaused=root.getElementById('pause').textContent==='继续';
                                setTimeout(()=>root.getElementById('stop').click(),450);
                            }""")
                        return result
                    with patch.object(adapter, 'comment_progress', side_effect=progress), patch(
                            'app.crawl_bridge.extract_comments', side_effect=lambda snapshot, platform, **kw:
                                actual_extract(snapshot, platform, state_dir=Path(folder)/'crawl-state', **kw)):
                        result = scan_comments(adapter, page.url, ['价格'], 'xiaohongshu')
                    self.assertTrue(result['ok'], result)
                    self.assertTrue(result['stopped'])
                    self.assertEqual(result['stop_reason'], 'user')
                    self.assertEqual(result['read'], 1)
                    self.assertEqual(result['items'][0]['user_id'], 'xhs-user')
                    self.assertEqual(result['items'][0]['nickname'], '小红书作者')
                    self.assertTrue(page.evaluate('window.fixturePaused'))
            finally: context.close()


class CommentCrawlerApiTests(unittest.TestCase):
    def setUp(self):
        selection = patch('app.plugins.resolve_comments', side_effect=lambda engine: {'requested_engine':engine,'engine':engine,'degraded':False,'warning':''})
        selection.start(); self.addCleanup(selection.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.api = Api(str(Path(self.tmp.name)/'fixture.db'))
        self.dy = self.api.hub_save('douyin', {'nickname':'抖音测试'})['id']
        self.xhs = self.api.hub_save('xiaohongshu', {'nickname':'小红书测试'})['id']
        self.result = {'ok': True, 'items':[{'comment_id':'real-comment', 'user_id':'real-user', 'nickname':'用户', 'text':'多少钱', 'keyword':'多少钱', 'contacts':{'wechat':'','phone':'','qq':''}}],
                       'read':3, 'sourceUrl':'https://www.douyin.com/video/fixture', 'stopped':True, 'timed_out':True, 'extraction_engine':'crawl4ai'}
    def tearDown(self):
        self.api.close_thread_conn(); self.tmp.cleanup()
    def test_dy_engine_forward_real_id_dedup_and_partial(self):
        with patch.object(self.api._dy_browsers, 'scan', return_value=self.result) as scanned:
            first = self.api.dy_scan_video(self.dy, 'https://www.douyin.com/video/fixture', 'crawl4ai')
            second = self.api.dy_scan_video(self.dy, 'https://www.douyin.com/video/fixture', 'crawl4ai')
        self.assertTrue(first['ok'], first)
        self.assertTrue(first['timed_out'])
        self.assertEqual(first['extraction_engine'], 'crawl4ai')
        self.assertEqual(scanned.call_args.kwargs, {'engine':'crawl4ai'})
        self.assertEqual((first['added'], second['duplicates']), (1, 1))
        row = self.api.dy_comments(self.dy)['items'][0]
        self.assertEqual(row['comment_id'], 'real-comment')
    def test_pool_crawler_requires_open_profile_and_uses_new_pipeline(self):
        from app.dy_workspace import BrowserPool
        pool = BrowserPool('douyin')
        page = FakeAdapter()
        def perform(account, fn, **kwargs): return fn(page)
        with patch.object(pool, 'perform', side_effect=perform) as submitted, patch(
                'app.bb_bridge.bb_page', return_value=nullcontext(page)) as attached, patch(
                'app.comment_crawler.scan_comments', return_value=self.result) as scanned:
            result = pool.scan({'id':self.dy}, page.url, ['价格'], 'crawl4ai')
        self.assertEqual(result, self.result)
        self.assertTrue(submitted.call_args.kwargs['require_open'])
        self.assertEqual(attached.call_args.args[1:3], ('douyin', self.dy))
        self.assertEqual(scanned.call_args.kwargs, {'platform':'douyin'})

    def test_xhs_dispatch_selects_visible_tab_and_preserves_partial_source(self):
        from types import SimpleNamespace
        hidden, visible = FakeAdapter(), FakeAdapter()
        for page, state in [(hidden,'hidden'),(visible,'visible')]:
            page.is_closed = lambda: False
            page.evaluate = lambda expression, value=state: value
        class Actor:
            def is_dead(self): return False
            def call(inner, fn, **kwargs): return fn(SimpleNamespace(ctx=SimpleNamespace(pages=[visible,hidden])))
        self.api._actors[self.xhs] = Actor()
        result_data = dict(self.result, sourceUrl='https://www.xiaohongshu.com/explore/fixture')
        with patch('app.bb_bridge.bb_page', return_value=nullcontext(visible)) as attached, patch(
                'app.comment_crawler.scan_comments', return_value=result_data):
            result = self.api.hub_collect('xiaohongshu', self.xhs, 'https://www.xiaohongshu.com/explore/fixture?token=PRIVATE', 'crawl4ai')
        self.assertTrue(result['ok'], result)
        self.assertIs(attached.call_args.args[0], visible)
        self.assertTrue(result['timed_out'])
        archived = self.api.hub_collected()['items'][0]
        self.assertEqual(archived['source_url'], result_data['sourceUrl'])
        self.assertNotIn('PRIVATE', archived['source_url'])

    def test_wrong_target_real_pipeline_never_archives(self):
        from types import SimpleNamespace
        page = FakeAdapter()
        page.url = 'https://www.xiaohongshu.com/explore/home'
        page.is_closed = lambda: False
        page.evaluate = lambda expression: 'visible'
        page.goto = lambda *args, **kwargs: setattr(page, 'url', 'https://www.xiaohongshu.com/explore/wrong-note')
        class Actor:
            def is_dead(self): return False
            def call(inner, fn, **kwargs): return fn(SimpleNamespace(ctx=SimpleNamespace(pages=[page])))
        self.api._actors[self.xhs] = Actor()
        with patch('app.bb_bridge.bb_page', return_value=nullcontext(page)), patch(
                'app.crawl_bridge.extract_comments') as extracted:
            result = self.api.hub_collect('xiaohongshu', self.xhs, 'https://www.xiaohongshu.com/explore/requested', 'crawl4ai')
        self.assertFalse(result['ok'])
        self.assertIn('不是指定内容', result['error'])
        extracted.assert_not_called()
        self.assertEqual(self.api.hub_collected()['items'], [])
        self.assertEqual(self.api._browser_jobs, {})

    def test_new_xhs_crawler_requires_existing_browser(self):
        with patch.object(self.api, '_ensure_actor') as opened:
            result = self.api.hub_collect('xiaohongshu', self.xhs, 'https://www.xiaohongshu.com/explore/fixture', 'crawl4ai')
        self.assertFalse(result['ok'])
        self.assertIn('请先打开', result['error'])
        opened.assert_not_called()


if __name__ == '__main__': unittest.main()
