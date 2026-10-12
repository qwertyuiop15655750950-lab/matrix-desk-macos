"""Smoke-test the unpacked extension against locally fulfilled synthetic pages.

Every browser request is intercepted; these tests never fetch a platform page.
"""
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from playwright.sync_api import sync_playwright
from app.browser_plugin_bridge import extension_launch_options, scan_with_plugin
from app import browser_plugin_bridge as bridge

HTML = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<style>.comment-list{height:140px;width:350px;overflow-y:auto}.comment-item{min-height:100px}</style>
<div class="comment-list">
 <article class="comment-item"><a class="author" href="/user/u1">同名用户</a><p class="comment-text content">价格多少 wx:WXabc_123</p></article>
 <article class="comment-item"><a class="author" href="/user/u2">同名用户</a><p class="comment-text content">咨询地址</p></article>
 <article class="comment-item"><span class="author">未提供标识</span><p class="comment-text content">价格优惠</p></article>
</div>
<script>
window.fixtureScrolls = 0;
document.querySelector('.comment-list').addEventListener('scroll', () => {
  window.fixtureScrolls++;
  if (window.fixtureAdded) return;
  window.fixtureAdded = true;
  const row = document.createElement('article');
  row.className = 'comment-item';
  row.innerHTML = '<a class="author" href="/user/u3">新用户</a><p class="comment-text content">价格新评论</p>';
  document.querySelector('.comment-list').appendChild(row);
});
</script></body></html>'''


class BrowserPluginIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.playwright = sync_playwright().start()
        cls.context = cls.playwright.chromium.launch_persistent_context(
            str(Path(cls.temp.name)/'profile'), headless=True,
            viewport={'width': 1000, 'height': 800}, service_workers='block',
            **extension_launch_options())
        cls.requests = []
        def fulfill(route):
            cls.requests.append(route.request.url)
            route.fulfill(status=200, content_type='text/html', body=HTML)
        cls.context.route('**/*', fulfill)

    @classmethod
    def tearDownClass(cls):
        cls.context.close()
        cls.playwright.stop()
        cls.temp.cleanup()

    def fixture(self, url):
        page = self.context.new_page()
        self.addCleanup(page.close)
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_function("document.documentElement.dataset.matrixCollector === 'ready'", timeout=10000)
        self.assertTrue(page.locator('#matrix-browser-collector').is_visible())
        return page

    def test_bridge_and_visible_panel_on_both_supported_platforms(self):
        for platform, url in [('douyin', 'https://www.douyin.com/video/123'),
                              ('xiaohongshu', 'https://www.xiaohongshu.com/explore/123')]:
            with self.subTest(platform=platform):
                page = self.fixture(url)
                result = scan_with_plugin(page, url, ['价格', '咨询'], platform=platform, scroll_times=0)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['read'], 3)
                self.assertEqual([row['user_id'] for row in result['items']], ['u1', 'u2', ''])
                self.assertEqual(result['items'][0]['contacts']['wechat'], 'WXabc_123')
                no_hits = scan_with_plugin(page, url, ['不存在的关键词'], platform=platform, scroll_times=0)
                self.assertTrue(no_hits['ok'], no_hits)
                self.assertEqual(no_hits['items'], [])
                self.assertEqual(no_hits['read'], 3)

    def test_scroll_wait_capture_and_dedup(self):
        url = 'https://www.douyin.com/video/456'
        page = self.fixture(url)
        result = scan_with_plugin(page, url, ['价格', '咨询'], scroll_times=2, interval_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['read'], 4)
        self.assertEqual(len(result['items']), 4)
        self.assertGreater(page.evaluate('window.fixtureScrolls'), 0)

    def test_visible_captcha_stops_before_scanning(self):
        url = 'https://www.douyin.com/video/gate'
        page = self.fixture(url)
        page.evaluate('''() => {const gate=document.createElement('div');gate.id='captcha_container';gate.textContent='请完成安全验证';document.body.appendChild(gate);}''')
        result = scan_with_plugin(page, url, ['价格'], scroll_times=5)
        self.assertFalse(result['ok'])
        self.assertTrue(result['blocked'])
        self.assertEqual(result['stop_reason'], 'captcha')
        self.assertEqual(page.evaluate('window.fixtureScrolls'), 0)

    def test_hidden_gate_and_warning_in_user_comment_do_not_block(self):
        url = 'https://www.xiaohongshu.com/explore/gate'
        page = self.fixture(url)
        page.evaluate('''() => {
            const hidden=document.createElement('div');hidden.className='captcha';hidden.style.display='none';hidden.textContent='安全验证';document.body.appendChild(hidden);
            document.querySelector('.comment-text').textContent='价格多少，我收到账号异常提醒，里面说与真人操作习惯不一致';
        }''')
        result = scan_with_plugin(page, url, ['价格','咨询'], platform='xiaohongshu', scroll_times=0)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result.get('blocked', False))
        self.assertEqual(result['read'], 3)

    def test_new_warning_preserves_partial_comments_and_stops_scrolling(self):
        url = 'https://www.douyin.com/video/partial-gate'
        page = self.fixture(url)
        page.evaluate('''() => {setTimeout(() => {
            const gate=document.createElement('div');gate.setAttribute('role','alert');gate.textContent='账号异常提醒：存在不常见的浏览行为，与真人操作习惯不一致';document.body.appendChild(gate);
        },700);}''')
        result = scan_with_plugin(page, url, ['价格','咨询'], scroll_times=5, interval_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['blocked'])
        self.assertTrue(result['requires_review'])
        self.assertEqual(result['stop_reason'], 'account_warning')
        self.assertEqual(len(result['items']), 3)
        self.assertIn('已保留', page.locator('#matrix-browser-collector').locator('#status').inner_text())

    def test_visible_login_dialog_stops_with_specific_reason(self):
        url = 'https://www.xiaohongshu.com/explore/login-gate'
        page = self.fixture(url)
        page.evaluate('''() => {const gate=document.createElement('div');gate.setAttribute('role','dialog');gate.textContent='扫码登录后查看全部评论';document.body.appendChild(gate);}''')
        result = scan_with_plugin(page, url, [], platform='xiaohongshu', collect_all=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stop_reason'], 'login_required')

    def test_rednote_aliases_load_plugin_and_keep_same_note_open(self):
        from app.comment_selectors import PLATFORM_HOSTS
        from app.batch_api import PROFILE_SCRIPT
        from app.builtin_crawler import collect
        for host in ['rednote.com','www.rednote.com']:
            with self.subTest(host=host):
                url=f'https://{host}/explore/alias-note'
                page=self.fixture(url)
                result=scan_with_plugin(page,'https://www.xiaohongshu.com/discovery/item/alias-note',[],platform='xiaohongshu',collect_all=True,scroll_times=0)
                self.assertTrue(result['ok'],result)
                browser_hosts=page.evaluate('globalThis.__matrixPlatformHosts')
                self.assertEqual(set(browser_hosts['xiaohongshu']),set(PLATFORM_HOSTS['xiaohongshu']))
                self.assertEqual(result['read'],3)
                self.assertEqual(page.url,url)
                page.evaluate('''() => {const work=document.createElement('a');work.href='/explore/second-note?xsec_token=ACCESS';document.body.appendChild(work);}''')
                found=page.evaluate(PROFILE_SCRIPT,{'platform':'xiaohongshu','hosts':sorted(PLATFORM_HOSTS['xiaohongshu'])})
                self.assertEqual(found['works'][0]['canonical'],'https://www.xiaohongshu.com/explore/second-note')
                self.assertTrue(found['works'][0]['url'].startswith(f'https://{host}/'))
                result=collect(page,url,[],collect_all=True,max_comments=3)
                self.assertTrue(result['ok'],result)
                self.assertEqual(result['read'],3)

    def test_rednote_access_warning_is_still_blocked(self):
        url='https://www.rednote.com/explore/warned-note'
        page=self.fixture(url)
        page.evaluate('''() => {const gate=document.createElement('div');gate.setAttribute('role','alert');gate.textContent='账号异常提醒';document.body.appendChild(gate);}''')
        result=scan_with_plugin(page,url,[],platform='xiaohongshu',collect_all=True)
        self.assertTrue(result['blocked'])
        self.assertEqual(result['stop_reason'],'account_warning')

    def test_visible_pause_resume_stop_returns_partial_result(self):
        url = 'https://www.douyin.com/video/789'
        page = self.fixture(url)
        page.evaluate('''() => {
            const panel = document.getElementById('matrix-browser-collector').shadowRoot;
            setTimeout(() => panel.getElementById('pause').click(), 450);
            setTimeout(() => {
                window.fixtureWasPaused = panel.getElementById('pause').textContent === '继续';
            }, 850);
            setTimeout(() => panel.getElementById('pause').click(), 1100);
            setTimeout(() => panel.getElementById('stop').click(), 1400);
        }''')
        result = scan_with_plugin(page, url, ['价格', '咨询'], scroll_times=5, interval_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['stopped'])
        self.assertTrue(page.evaluate('window.fixtureWasPaused'))
        self.assertEqual(result['read'], 3)
        self.assertEqual(len(result['items']), 3)
        self.assertIn('已停止', page.locator('#matrix-browser-collector').locator('#status').inner_text())

    def test_parent_comment_does_not_inherit_reply_or_mentioned_user(self):
        url = 'https://www.douyin.com/video/321'
        page = self.fixture(url)
        page.evaluate('''() => {
            document.querySelector('.comment-list').innerHTML = `
              <article class="comment-item">
                <span class="author">原评论作者</span>
                <p class="comment-text content">价格多少 <a href="/user/mentioned">提及用户</a></p>
                <article class="comment-item">
                  <a class="author" href="/user/reply-author">回复作者</a>
                  <p class="comment-text content">咨询地址</p>
                </article>
              </article>`;
        }''')
        result = scan_with_plugin(page, url, ['价格', '咨询'], scroll_times=0)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['read'], 2)
        self.assertEqual([(row['nickname'], row['user_id']) for row in result['items']],
                         [('原评论作者', ''), ('回复作者', 'reply-author')])

    def test_timeout_keeps_rows_captured_before_pause(self):
        url = 'https://www.douyin.com/video/654'
        page = self.fixture(url)
        page.evaluate('''() => {
            setTimeout(() => document.getElementById('matrix-browser-collector')
                .shadowRoot.getElementById('pause').click(), 450);
        }''')
        with patch.object(bridge, 'SCAN_TIMEOUT_MS', 800):
            result = scan_with_plugin(page, url, ['价格', '咨询'], scroll_times=5, interval_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['timed_out'])
        self.assertTrue(result['stopped'])
        self.assertEqual(result['read'], 3)
        self.assertEqual(len(result['items']), 3)
        self.assertIn('保留结果', page.locator('#matrix-browser-collector').locator('#status').inner_text())

    def test_paused_scan_stops_on_spa_page_change(self):
        url = 'https://www.douyin.com/video/987'
        page = self.fixture(url)
        page.evaluate('''() => {
            setTimeout(() => document.getElementById('matrix-browser-collector')
                .shadowRoot.getElementById('pause').click(), 450);
            setTimeout(() => history.pushState({}, '', '/video/other'), 700);
        }''')
        result = scan_with_plugin(page, url, ['价格', '咨询'], scroll_times=5, interval_ms=2000)
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['stopped'])
        self.assertEqual(result['stop_reason'], 'page_changed')
        self.assertEqual(result['sourceUrl'], url)
        self.assertEqual(result['read'], 3)

if __name__ == '__main__': unittest.main()
