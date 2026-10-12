"""Actual bb daemon + two account-owned Chromium profiles on local fixtures."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from app import bb_bridge as bb
from app.browser_plugin_bridge import extension_launch_options, scan_with_plugin

HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>本地测试页</title><script>window.fixture='script-secret';</script></head><body>
<main><article class="comment-item"><a class="author" href="/user/fixture-user?access_token=link-secret">客户</a>
<p class="comment-text">价格多少</p></article><p>本地可见正文</p></main>
<input value="input-secret"><input type="password" value="password-secret"><textarea>textarea-secret</textarea>
<form>form-secret</form></body></html>'''


class BBBridgeTests(unittest.TestCase):
    def setUp(self):
        if self._testMethodName in ('test_selected_profiles_daemon_cleanup_plugin_capture_and_no_account_mix','test_bb_plugin_pause_and_stop_preserve_results'):
            try: bb._runtime()
            except (ValueError, OSError): self.skipTest('Optional development bb-browser runtime is not installed')

    def test_status_and_missing_explicit_install(self):
        present = bb.status()
        self.assertIsInstance(present['available'], bool)
        if present['available']: self.assertEqual(present['version'], '0.14.2')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'MATRIX_BB_BROWSER_DIR':folder}):
            result = bb.status()
            self.assertFalse(result['available'])
            self.assertIn('未安装', result['error'])

    def test_eval_surface_rejects_arbitrary_script(self):
        adapter = bb.BBPage(None, 'douyin', 1, '/unused')
        try:
            with self.assertRaisesRegex(ValueError, '仅允许'):
                adapter.evaluate('document.cookie')
            with self.assertRaisesRegex(ValueError, '幅度'):
                adapter.scroll(5000)
        finally: adapter.close()

    def test_capture_removes_hidden_dom_before_styles_and_keeps_only_content_parameters(self):
        html = '''<html><head><meta charset="utf-8"><title>公开内容</title><style>.offscreen {display:none}</style></head><body>
        <main><p>公开正文</p><p hidden>HIDDEN_SECRET</p><p aria-hidden="true">ARIA_SECRET</p>
        <p style="display: none">DISPLAY_SECRET</p><p style="visibility:hidden">VISIBILITY_SECRET</p>
        <section class="offscreen"><p>CSS_SECRET</p></section>
        <a href="/watch?video_id=123&amp;ObjectId=456&amp;access_token=LINK_SECRET&amp;code=CODE_SECRET#fragment">内容链接</a>
        </main></body></html>'''
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel='chromium')
            try:
                page = browser.new_page()
                page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=html))
                page.goto('https://www.douyin.com/watch?note_id=note-7&id=8&q=%E5%85%AC%E5%BC%80&token=URL_SECRET&code=CODE_SECRET#fragment')
                result = bb.capture_page(page)
                self.assertIn('公开正文', result['html'])
                self.assertNotIn('SECRET', result['html'])
                self.assertNotIn('fragment', result['html'])
                self.assertIn('video_id=123&amp;ObjectId=456', result['html'])
                self.assertIn('note_id=note-7', result['url'])
                self.assertIn('id=8', result['url'])
                self.assertNotIn('SECRET', result['url'])
                self.assertNotIn('code=', result['url'])
                self.assertNotIn('#', result['url'])
                self.assertEqual(bb._safe_content_url(page.url), result['url'])
                page.evaluate('document.body.hidden = true')
                self.assertNotIn('公开正文', bb.capture_page(page)['html'])
            finally: browser.close()

    def test_selected_profiles_daemon_cleanup_plugin_capture_and_no_account_mix(self):
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as playwright:
            contexts = []
            try:
                profiles, pages = [], []
                for account_id in [1, 2]:
                    profile = Path(folder)/f'account-{account_id}'
                    options = extension_launch_options()
                    options['args'].extend(bb.debug_launch_args())
                    context = playwright.chromium.launch_persistent_context(str(profile), headless=True, **options)
                    contexts.append(context)
                    context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',body=HTML))
                    page = context.pages[0]
                    page.goto(f'https://www.douyin.com/video/{account_id}', wait_until='domcontentloaded')
                    page.wait_for_function("document.documentElement.dataset.matrixCollector === 'ready'")
                    page.evaluate('(id) => document.querySelector("main > p").textContent = "账号正文 " + id', account_id)
                    profiles.append(profile); pages.append(page)
                # Passing another account's profile must fail before any daemon
                # starts, even though both browser endpoints are live.
                with self.assertRaisesRegex(ValueError, '不匹配'):
                    with bb.bb_page(pages[0], 'douyin', 1, profiles[1]): pass
                ports = []
                homes = []
                for account_id, page, profile in zip([1,2],pages,profiles):
                    with bb.bb_page(page, 'douyin', account_id, profile) as adapter:
                        process, home = adapter.process, adapter.home.name
                        homes.append(home); ports.append(adapter.base)
                        snapshot = adapter.snapshot()
                        self.assertEqual(snapshot['engine'], 'bb-browser')
                        self.assertIn(f'账号正文 {account_id}', snapshot['snapshot'])
                        self.assertNotIn('password-secret', snapshot['snapshot'])
                        captured = adapter.capture()
                        self.assertIn(f'账号正文 {account_id}', captured['html'])
                        self.assertNotIn(f'账号正文 {3-account_id}', captured['html'])
                        for value in ['script-secret','input-secret','password-secret','textarea-secret','form-secret',
                                      'matrix-browser-collector','link-secret']:
                            self.assertNotIn(value, captured['html'])
                        result = scan_with_plugin(adapter, page.url, ['价格'], scroll_times=0)
                        self.assertTrue(result['ok'], result)
                        self.assertTrue(result['items'], {**result, 'visible_comment':page.locator('.comment-text').inner_text()})
                        self.assertEqual(result['items'][0]['user_id'],'fixture-user')
                        adapter.evaluate('window.scrollBy(0,400)')
                        if account_id == 1:
                            adapter.goto('https://www.douyin.com/video/navigated')
                            adapter.wait_for_function(bb.PLUGIN_READY)
                            self.assertEqual(adapter.url, 'https://www.douyin.com/video/navigated')
                            self.assertIn('价格多少', adapter.capture()['html'])
                    self.assertIsNotNone(process.poll())
                    self.assertFalse(Path(home).exists())
                    self.assertFalse(page.is_closed())
                    self.assertEqual(page.title(), '本地测试页')
                self.assertNotEqual(ports[0],ports[1])
                self.assertNotEqual(homes[0],homes[1])
            finally:
                for context in contexts: context.close()

    def test_bb_plugin_pause_and_stop_preserve_results(self):
        body = HTML.replace('<main>', '<main class="comment-list" style="height:80px;overflow-y:auto">')
        body = body.replace('</main>', '<div style="height:800px">正文</div></main>')
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as playwright:
            options = extension_launch_options()
            options['args'].extend(bb.debug_launch_args())
            context = playwright.chromium.launch_persistent_context(folder, headless=True, **options)
            try:
                context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html',body=body))
                page = context.pages[0]
                page.goto('https://www.douyin.com/video/pause',wait_until='domcontentloaded')
                page.wait_for_function(bb.PLUGIN_READY)
                with bb.bb_page(page, 'douyin', 9, folder) as adapter:
                    page.evaluate('''() => {
                      const root=document.getElementById('matrix-browser-collector').shadowRoot;
                      setTimeout(()=>root.getElementById('pause').click(), 450);
                      setTimeout(()=>window.wasPaused=root.getElementById('pause').textContent==='继续',650);
                      setTimeout(()=>root.getElementById('stop').click(), 900);
                    }''')
                    result = scan_with_plugin(adapter, page.url, ['价格'], scroll_times=5, interval_ms=2000)
                    self.assertTrue(result['ok'], result)
                    self.assertTrue(result['stopped'], result)
                    self.assertTrue(page.evaluate('window.wasPaused'))
                    self.assertEqual(len(result['items']),1)
            finally: context.close()

if __name__ == '__main__': unittest.main()
