import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import crawl_bridge


class CrawlBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'state'

    def tearDown(self):
        self.tmp.cleanup()

    def fake_worker(self, source):
        worker = Path(self.tmp.name) / 'fake_worker.py'
        worker.write_text(source, encoding='utf-8')
        return patch.object(crawl_bridge, 'WORKER', worker)

    def test_missing_dependency_is_explicit_without_creating_browser(self):
        status = crawl_bridge.status(python_path=self.state / 'missing-python')
        self.assertFalse(status['available'])
        self.assertEqual(status['version'], '')
        self.assertIn('尚未安装', status['error'])
        with self.assertRaisesRegex(RuntimeError, '尚未安装'):
            crawl_bridge.extract({'url': 'https://example.test/', 'html': '<p>正文</p>'},
                python_path=self.state / 'missing-python')

    def test_input_caps_apply_before_starting_runtime(self):
        with patch.object(crawl_bridge, '_invoke') as invoked:
            with self.assertRaisesRegex(ValueError, '5MB'):
                crawl_bridge.extract({'html': '中' * (crawl_bridge.MAX_HTML_BYTES // 3 + 1)})
            with self.assertRaisesRegex(ValueError, '500'):
                crawl_bridge.extract({'url': 'https://example.test/', 'html': '<p>正文</p>'}, selector='x' * 501)
            with self.assertRaisesRegex(ValueError, '关键词'):
                crawl_bridge.extract({'url': 'https://example.test/', 'html': '<p>正文</p>'}, keywords='多少钱')
            with self.assertRaisesRegex(ValueError, '页面网址'):
                crawl_bridge.extract({'url': 'file:///private', 'html': '<p>正文</p>'})
            invoked.assert_not_called()

    def test_credential_fields_are_not_sent_to_worker(self):
        result = {'url': 'https://example.test/', 'title': '', 'markdown': '正文',
            'links': [], 'engine': 'crawl4ai'}
        with patch.object(crawl_bridge, '_invoke', return_value=result) as invoked:
            crawl_bridge.extract({'url': result['url'], 'html': '<p>正文</p>',
                'cookies': [{'name': 'secret'}], 'token': 'private', 'title': ''})
            request = invoked.call_args.args[0]
        self.assertEqual(set(request['snapshot']), {'url', 'title', 'html'})

    def test_timeout_kills_worker_and_reports_failure(self):
        with self.fake_worker('import time\ntime.sleep(30)\n'):
            with self.assertRaisesRegex(RuntimeError, '超时'):
                crawl_bridge._invoke({'operation': 'status'}, python_path=sys.executable,
                    state_dir=self.state, timeout=0.1)

    def test_oversized_stdout_is_stopped_without_exposing_it(self):
        with self.fake_worker('import sys\nsys.stdout.write("private" * 20000)\n'):
            with patch.object(crawl_bridge, 'MAX_OUTPUT_BYTES', 1000):
                with self.assertRaisesRegex(RuntimeError, '输出超过限制'):
                    crawl_bridge._invoke({'operation': 'status'}, python_path=sys.executable,
                        state_dir=self.state)

    def test_logs_never_leak_into_error(self):
        source = 'import sys\nsys.stderr.write("private-page-secret")\nsys.stdout.write("not JSON")\n'
        with self.fake_worker(source):
            with self.assertRaises(RuntimeError) as raised:
                crawl_bridge._invoke({'operation': 'status'}, python_path=sys.executable,
                    state_dir=self.state)
        self.assertNotIn('private', str(raised.exception))

    def test_worker_payload_and_valid_json_result(self):
        source = '''import json,sys,os
request=json.load(sys.stdin)
assert request['operation']=='status'
assert os.environ['CRAWL4_AI_BASE_DIRECTORY'].endswith('state')
print(json.dumps({'ok':True,'result':{'version':'unit-test'}}))
'''
        with self.fake_worker(source):
            status = crawl_bridge.status(python_path=sys.executable, state_dir=self.state)
        self.assertEqual(status, {'available': True, 'version': 'unit-test', 'error': ''})


class Crawl4AIIntegrationTests(unittest.TestCase):
    """Uses the actual optional runtime, without network or browser fixtures."""
    @classmethod
    def setUpClass(cls):
        cls.python = crawl_bridge._python_path()
        if not cls.python.is_file():
            raise unittest.SkipTest('Optional Crawl4AI environment is not installed')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'offline-state'

    def tearDown(self):
        self.tmp.cleanup()

    def extract(self, html, selector='', keywords=None):
        return crawl_bridge.extract({'url': 'https://www.douyin.com/video/fixture',
            'title': '已登录页面', 'html': html}, selector, keywords=keywords,
            python_path=self.python, state_dir=self.state)

    def test_actual_processors_clean_dom_scope_links_and_unicode(self):
        html = '''<!doctype html><html lang="zh-CN"><head><title>网页标题</title>
        <meta name="description" content="本地说明"></head><body><nav>页外导航</nav>
        <main class="entry"><h1>真实标题</h1><p>中文正文，价格多少钱？</p>
        <a href="/user/author">原作者</a><a href="https://example.test/info">参考</a>
        <a href="javascript:alert(1)">危险链接</a><a href="https://user:pass@example.test/">凭据链接</a>
        <script>window.secret = 'SCRIPT_SECRET';</script>
        <form><p>FORM_SECRET</p><input value="TOKEN_SECRET"></form>
        <textarea>INPUT_SECRET</textarea><div contenteditable="true">DRAFT_SECRET</div>
        <div hidden>HIDDEN_SECRET</div><aside id="matrix-browser-collector">PLUGIN_SECRET</aside>
        <div style="display: none">STYLE_SECRET</div></main><p>页外文字</p></body></html>'''
        result = self.extract(html, 'main', ['多少钱', '未命中'])
        self.assertEqual(result['engine'], 'crawl4ai')
        self.assertTrue(result['version'])
        self.assertIn('# 真实标题', result['markdown'])
        self.assertIn('中文正文', result['markdown'])
        self.assertEqual(result['matched_keywords'], ['多少钱'])
        self.assertNotIn('页外', result['markdown'])
        self.assertNotIn('SECRET', result['markdown'])
        self.assertNotIn('pass@', result['markdown'])
        self.assertEqual(result['metadata']['description'], '本地说明')
        self.assertEqual(result['links'], [
            {'url': 'https://www.douyin.com/user/author', 'text': '原作者'},
            {'url': 'https://example.test/info', 'text': '参考'}])
        self.assertFalse(list(self.state.rglob('*.db')))

    def test_no_keyword_match_keeps_full_content(self):
        result = self.extract('<main><p>没有关键词也保留正文。</p></main>', keywords=['未命中'])
        self.assertIn('没有关键词也保留正文', result['markdown'])
        self.assertEqual(result['matched_keywords'], [])

    def test_missing_selector_is_failure_instead_of_full_page_fallback(self):
        with self.assertRaisesRegex(RuntimeError, '没有匹配'):
            self.extract('<main><p>真实正文</p></main>', '.missing')
        with self.assertRaisesRegex(RuntimeError, '选择器无效'):
            self.extract('<main><p>真实正文</p></main>', '[broken')

    def test_title_only_or_removed_forms_are_not_success(self):
        with self.assertRaisesRegex(RuntimeError, '没有可归档'):
            self.extract('<html><head><title>仅有标题</title></head><body><form>输入</form></body></html>')

    def test_nested_selector_does_not_duplicate_or_take_tail(self):
        result = self.extract('<main class="entry"><p class="entry">唯一正文</p></main>尾部不归档', '.entry')
        self.assertEqual(result['markdown'].count('唯一正文'), 1)
        self.assertNotIn('尾部不归档', result['markdown'])

    def test_worker_enforces_no_network_and_no_browser_spawn(self):
        # Load the actual worker under the optional interpreter. Deny network
        # and Popen before dependency import, then process HTML with an image,
        # proving the offline path neither launches Chromium nor fetches media.
        code = '''import importlib.util,json,subprocess,sys,socket
spec=importlib.util.spec_from_file_location('offline_worker',sys.argv[1])
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
worker.disable_network()
def no_process(*args,**kwargs): raise AssertionError('browser/process spawn forbidden')
subprocess.Popen=no_process
try: socket.create_connection(('example.test',443))
except RuntimeError: pass
else: raise AssertionError('network guard missing')
print(json.dumps(worker.process({'operation':'extract','snapshot':{'url':'https://example.test/','html':'<p>离线提取正文</p><img src="https://example.test/tracker.png">'}}),ensure_ascii=False))
'''
        env = dict(__import__('os').environ,
            CRAWL4_AI_BASE_DIRECTORY=str(self.state), LITELLM_LOCAL_MODEL_COST_MAP='True')
        proc = subprocess.run([str(self.python), '-c', code, str(crawl_bridge.WORKER)],
            capture_output=True, text=True, timeout=20, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result['markdown'], '离线提取正文')
        self.assertEqual(result['links'], [])


if __name__ == '__main__':
    unittest.main()
