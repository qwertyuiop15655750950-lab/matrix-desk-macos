"""Actual offline Crawl4AI CSS extraction from public, loaded comment fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app import crawl_bridge
from app.comment_selectors import comment_source_url


class CrawlCommentValidationTests(unittest.TestCase):
    def test_exact_platform_hosts_and_https_are_required(self):
        for platform, url in (
            ('douyin', 'https://www.douyin.com.attacker.test/video/1'),
            ('douyin', 'https://creator.douyin.com/'),
            ('douyin', 'http://www.douyin.com/video/1'),
            ('douyin', 'https://user:pass@www.douyin.com/video/1'),
            ('douyin', 'https://www.douyin.com:8443/video/1'),
            ('douyin', 'https://www.xiaohongshu.com/explore/1'),
            ('channels', 'https://channels.weixin.qq.com/'),
        ):
            with self.subTest(platform=platform, url=url), patch.object(crawl_bridge, '_invoke') as invoked:
                with self.assertRaises(ValueError):
                    crawl_bridge.extract_comments({'url': url, 'html': '<p>正文</p>'}, platform)
                invoked.assert_not_called()

    def test_size_selector_and_count_bounds_precede_runtime(self):
        base = {'url': 'https://www.douyin.com/video/1', 'html': '<p>正文</p>'}
        with patch.object(crawl_bridge, '_invoke') as invoked:
            for count in (0, 1001, True, 1.5, '10'):
                with self.assertRaises(ValueError):
                    crawl_bridge.extract_comments(base, 'douyin', max_comments=count)
            with self.assertRaisesRegex(ValueError, '500'):
                crawl_bridge.extract_comments(base, 'douyin', '[' * 501)
            with self.assertRaisesRegex(ValueError, '5MB'):
                crawl_bridge.extract_comments({**base, 'html': '中' * (crawl_bridge.MAX_HTML_BYTES // 3 + 1)}, 'douyin')
            invoked.assert_not_called()

    def test_credentials_and_private_url_state_are_not_forwarded(self):
        source = 'https://www.douyin.com/video/1?modal_id=42&access_token=secret#private'
        public = 'https://www.douyin.com/video/1?modal_id=42'
        result = {'rows': [], 'source_url': public, 'platform': 'douyin', 'engine': 'crawl4ai', 'version': 'test'}
        with patch.object(crawl_bridge, '_invoke', return_value=result) as invoked:
            crawl_bridge.extract_comments({'url': source, 'html': '<p>公开正文</p>',
                'cookies': [{'value': 'COOKIE_SECRET'}], 'token': 'TOKEN_SECRET',
                'headers': {'Authorization': 'Bearer SECRET'}, 'title': '私密窗口标题'}, 'douyin')
        request = invoked.call_args.args[0]
        self.assertEqual(set(request['snapshot']), {'url', 'html'})
        self.assertEqual(request['snapshot']['url'], public)
        self.assertNotIn('secret', json.dumps(request).lower())

    def test_worker_output_is_validated_instead_of_assumed(self):
        valid = {'rows': [], 'source_url': 'https://www.douyin.com/video/1',
            'platform': 'douyin', 'engine': 'crawl4ai', 'version': 'test'}
        for override in ({'engine': 'other'}, {'platform': 'xiaohongshu'},
                         {'source_url': 'https://example.test/'},
                         {'rows': [{'comment_id': '', 'user_id': '', 'nickname': '', 'content': ''}]},
                         {'rows': [{'comment_id': '', 'user_id': '', 'nickname': '', 'content': '正文', 'token': 'secret'}]}):
            with patch.object(crawl_bridge, '_invoke', return_value={**valid, **override}):
                with self.assertRaisesRegex(RuntimeError, '评论结构'):
                    crawl_bridge.extract_comments({'url': valid['source_url'], 'html': '<p>正文</p>'}, 'douyin')


class CrawlCommentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.python = crawl_bridge._python_path()
        if not cls.python.is_file():
            raise unittest.SkipTest('Optional Crawl4AI runtime is not installed')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'offline-state'

    def tearDown(self):
        self.tmp.cleanup()

    def extract(self, html, platform='douyin', selector='', **kwargs):
        url = ('https://www.douyin.com/video/fixture' if platform == 'douyin'
               else 'https://www.xiaohongshu.com/explore/fixture')
        return crawl_bridge.extract_comments({'url': url, 'html': html}, platform,
            selector, python_path=self.python, state_dir=self.state, **kwargs)

    def test_actual_strategy_reads_public_identity_content_and_time(self):
        html = '''<div data-e2e="comment-list">
          <article data-e2e="comment-item" data-comment-id="c1">
            <a data-e2e="comment-author" href="/user/u1?token=secret">同名用户</a>
            <p data-e2e="comment-content">价格多少？</p>
            <time datetime="2026-10-09T13:30:00+08:00">今天</time>
          </article>
          <article class="comment-item" data-cid="c2"><span class="author" data-uid="u2">同名用户</span>
            <p class="comment-text">咨询地址</p><span class="comment-time">2分钟前</span></article>
        </div>'''
        result = self.extract(html)
        self.assertEqual(result['rows'], [
            {'comment_id': 'c1', 'user_id': 'u1', 'nickname': '同名用户', 'content': '价格多少？',
             'created_at': '2026-10-09T13:30:00+08:00'},
            {'comment_id': 'c2', 'user_id': 'u2', 'nickname': '同名用户', 'content': '咨询地址', 'created_at': '2分钟前'}])
        self.assertEqual(result['engine'], 'crawl4ai')
        self.assertEqual(result['version'], '0.9.4')
        self.assertEqual(result['matched_nodes'], 2)
        self.assertNotIn('secret', json.dumps(result))

    def test_parent_never_inherits_nested_reply_or_mentioned_user(self):
        html = '''<article class="comment-item" data-comment-id="parent">
          <span class="author">原评论作者</span>
          <p class="comment-text">价格多少 <a class="author" href="/user/mention">提及用户</a></p>
          <article class="comment-item" data-comment-id="reply">
            <a class="author" href="/user/replier">回复作者</a><p class="comment-text">咨询地址</p>
          </article></article>'''
        rows = self.extract(html)['rows']
        self.assertEqual([(row['nickname'], row['user_id'], row['comment_id']) for row in rows],
                         [('原评论作者', '', 'parent'), ('回复作者', 'replier', 'reply')])
        self.assertNotIn('咨询地址', rows[0]['content'])
        self.assertNotIn('回复作者', rows[0]['content'])
        self.assertIn('提及用户', rows[0]['content'])

    def test_wrapper_and_missing_author_do_not_steal_child_authors(self):
        html = '''<div class="parent-comment"><article class="comment-item">
          <p class="content">无ID的父评论</p>
          <article class="comment-item"><a class="author" href="/user/profile/replier">回复者</a>
            <div class="content"><span class="note-text">小红书回复</span></div></article>
        </article></div>'''
        rows = self.extract(html, 'xiaohongshu')['rows']
        self.assertEqual([(row['nickname'], row['user_id']) for row in rows], [('', ''), ('回复者', 'replier')])
        self.assertEqual([row['content'] for row in rows], ['无ID的父评论', '小红书回复'])

    def test_only_author_link_identity_is_accepted_and_names_never_become_ids(self):
        html = '''<article class="comment-item"><span class="author">名字不是ID</span>
            <p class="comment-text">咨询 <a href="/user/mentioned">提及</a></p></article>
          <article class="comment-item"><a class="author" href="https://www.douyin.com.attacker.test/user/bad">外站作者</a>
            <p class="comment-text">价格</p></article>
          <article class="comment-item"><a class="author" href="https://user:pass@www.douyin.com/user/bad">带凭据</a>
            <p class="comment-text">地址</p></article>
          <article class="comment-item"><a class="author" href="/user/bad%2Fid">路径错误</a>
            <p class="comment-text">怎么买</p></article>'''
        rows = self.extract(html)['rows']
        self.assertEqual([row['user_id'] for row in rows], ['', '', '', ''])
        self.assertEqual(rows[0]['nickname'], '名字不是ID')

    def test_forms_hidden_state_editors_and_scripts_are_removed(self):
        html = '''<article class="comment-item" data-secret="ATTR_SECRET">
          <span class="author">公开作者</span><p class="comment-text">公开内容
            <script>SCRIPT_SECRET</script><input value="INPUT_SECRET"><span hidden>HIDDEN_SECRET</span>
            <span aria-hidden="true">ARIA_SECRET</span><span style="visibility: hidden">STYLE_SECRET</span>
            <span contenteditable="false">DRAFT_SECRET</span><form>FORM_SECRET</form>
          </p></article><div hidden><article class="comment-item"><p class="comment-text">ROW_SECRET</p></article></div>
          <aside id="matrix-browser-collector"><article class="comment-item"><p class="comment-text">PLUGIN_SECRET</p></article></aside>'''
        result = self.extract(html)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['rows'][0]['content'], '公开内容')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_duplicate_rows_are_removed_and_limit_applies_after_dedup(self):
        row = '<article class="comment-item" data-comment-id="c1"><a class="author" href="/user/u1">作者</a><p class="comment-text">价格</p></article>'
        second = '<article class="comment-item" data-comment-id="c2"><span class="author">作者</span><p class="comment-text">地址</p></article>'
        result = self.extract(row + row + second, max_comments=2)
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual(result['matched_nodes'], 3)
        self.assertEqual(len(self.extract(row + row + second, max_comments=1)['rows']), 1)

    def test_explicit_scope_and_malformed_selector_never_fall_back_to_page(self):
        html = '''<article class="comment-item selected"><span class="author">所选</span><p class="comment-text">所选正文</p></article>
        <article class="comment-item"><span class="author">其他</span><p class="comment-text">范围之外</p></article>'''
        self.assertEqual([row['content'] for row in self.extract(html, selector='.selected')['rows']], ['所选正文'])
        missing = self.extract(html, selector='.missing')
        self.assertEqual(missing['rows'], [])
        self.assertEqual(missing['matched_nodes'], 0)
        with self.assertRaisesRegex(RuntimeError, '选择器无效'):
            self.extract(html, selector='[broken')

    def test_real_css_strategy_runs_with_network_and_browser_launch_denied(self):
        code = '''import sys,json,subprocess,socket
sys.path.insert(0,sys.argv[1]);import crawl_worker as worker
worker.disable_network()
def forbidden(*a,**k): raise AssertionError('network/browser/process forbidden')
subprocess.Popen=forbidden
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
original=JsonCssExtractionStrategy.extract;calls=[]
def checked(self,url,html,*a,**k):
    assert 'data-matrix-comment-row' in html
    assert 'PRIVATE_ATTRIBUTE' not in html and 'MENTION_TOKEN' not in html
    calls.append(True);return original(self,url,html,*a,**k)
JsonCssExtractionStrategy.extract=checked
result=worker.process({'operation':'extract_comments','platform':'douyin','snapshot':{
 'url':'https://www.douyin.com/video/1','html':'<article class="comment-item" data-secret="PRIVATE_ATTRIBUTE"><a class="author" href="/user/u1?token=MENTION_TOKEN">作者</a><p class="comment-text">正文</p><img src="https://example.test/tracker.png"></article>'}})
assert calls==[True]
try: socket.create_connection(('example.test',443))
except RuntimeError: pass
else: raise AssertionError('network guard missing')
print(json.dumps(result,ensure_ascii=False))
'''
        env = dict(os.environ, CRAWL4_AI_BASE_DIRECTORY=str(self.state), LITELLM_LOCAL_MODEL_COST_MAP='True')
        proc = subprocess.run([str(self.python), '-c', code, str(crawl_bridge.WORKER.parent)],
            capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['rows'][0]['user_id'], 'u1')
        self.assertFalse(list(self.state.rglob('*.db')))

    def test_dynamic_loaded_dom_is_read_without_replaying_scripts(self):
        # All browser requests are fulfilled locally. The worker receives the
        # rendered DOM after JavaScript adds a row, not script/hydration data.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(service_workers='block')
                requested = []
                fixture = '''<div class="comment-list"></div><script>
                    setTimeout(()=>{document.querySelector('.comment-list').innerHTML=
                    '<article class="comment-item" data-comment-id="dynamic"><a class="author" href="/user/loaded">动态作者</a><p class="comment-text">滚动加载后的评论</p></article>';},10);
                    window.hydrationPrivate='SCRIPT_SECRET';</script>'''
                def fulfill(route):
                    requested.append(route.request.url)
                    route.fulfill(status=200, content_type='text/html; charset=utf-8', body=fixture)
                page.route('**/*', fulfill)
                page.goto('https://www.douyin.com/video/dynamic')
                page.wait_for_selector('.comment-item')
                result = self.extract(page.content())
                self.assertEqual(result['rows'], [{'comment_id': 'dynamic', 'user_id': 'loaded',
                    'nickname': '动态作者', 'content': '滚动加载后的评论'}])
                self.assertNotIn('SCRIPT_SECRET', json.dumps(result))
                self.assertEqual(requested, ['https://www.douyin.com/video/dynamic'])
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
