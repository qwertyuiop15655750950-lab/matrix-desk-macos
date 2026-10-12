import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from xml.sax.saxutils import escape
from playwright.sync_api import sync_playwright
from app.draft_docx import parse_docx, import_entries
from app.db import init_db
from app.webapp import Api
from app.xhs_poster import prepare_builtin_poster, PosterGenerationError
from app.publisher import fill_publish_form, click_publish


class WordImportTests(unittest.TestCase):
    def test_four_fields_remain_separate_and_reimport_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'content.docx'
            paragraphs=['01｜标题示例','封面大字报：','我的封面原文','发布标题：我的发布标题',
                '正文：我的正文不改写','正文第二行','话题：#原话题 #另一个话题']
            xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join(
                f'<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>' for text in paragraphs)+'</w:body></w:document>'
            with ZipFile(path,'w') as archive: archive.writestr('word/document.xml',xml)
            rows=parse_docx(path)
            self.assertEqual(rows,[{'title':'我的发布标题','cover_text':'我的封面原文','body':'我的正文不改写\n正文第二行','topics':'#原话题 #另一个话题'}])
            conn=init_db(':memory:');self.addCleanup(conn.close)
            self.assertEqual(import_entries(conn,rows,'now')['added'],1)
            self.assertEqual(import_entries(conn,rows,'now')['duplicates'],1)
            self.assertEqual(conn.execute('SELECT cover_text,body,template FROM drafts').fetchone()[:],
                ('我的封面原文','我的正文不改写\n正文第二行','xhs_builtin'))


class MultiPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.api=Api(str(Path(self.tmp.name)/'test.db'))
        self.accounts=[self.api.account_create({'nickname':f'测试{i}'})['id'] for i in range(2)]
        self.drafts=[self.api.draft_save({'title':f'文案{i}','body':'正文','cover_text':'封面','mode':'poster'})['id'] for i in range(3)]

    def tearDown(self):
        self.api.metrics_close();self.api._batch.close();self.api._warmup.close();self.api.close_thread_conn();self.tmp.cleanup()

    def test_round_robin_and_same_account_multiple_posts_have_distinct_progress(self):
        qid=self.api.queue_create_many(self.drafts,self.accounts)['id']
        q=self.api.queue_get()['queue']
        self.assertEqual([job['aid'] for job in q['jobs']],[self.accounts[0],self.accounts[1],self.accounts[0]])
        self.assertEqual(q['total'],3)
        seen=[]
        def publish(did,aids,queue_id=None,queue_key=None):
            seen.append((did,aids[0]));self.api._queue_mark(queue_id,queue_key,'done')
        with patch.object(self.api,'_publish_worker',side_effect=publish): self.api._queue_worker_inner(qid,True)
        self.assertEqual(seen,list(zip(self.drafts,[self.accounts[0],self.accounts[1],self.accounts[0]])))
        q=self.api.queue_get()['queue'];self.assertEqual(q['status'],'finished');self.assertEqual(q['done'],3)

    def test_uncertain_submission_pauses_and_is_never_retried(self):
        qid=self.api.queue_create_many(self.drafts,self.accounts)['id'];calls=[]
        def publish(did,aids,queue_id=None,queue_key=None):
            calls.append(did);self.api._queue_mark(queue_id,queue_key,'review_required')
        with patch.object(self.api,'_publish_worker',side_effect=publish):self.api._queue_worker_inner(qid,True)
        self.assertEqual(calls,self.drafts[:1]);q=self.api.queue_get()['queue']
        self.assertEqual(q['status'],'paused');self.assertEqual(q['done'],1)
        self.assertEqual(len(q['pending']),2)

    def test_pre_submit_block_is_pending_and_queue_pauses(self):
        qid=self.api.queue_create_many(self.drafts,self.accounts)['id']
        key=self.api.queue_get()['queue']['pending'][0]
        self.api._queue_mark(qid,key,'blocked: 模板图片未加载')
        q=self.api.queue_get()['queue'];self.assertEqual(q['done'],0);self.assertEqual(q['status'],'paused')


class NativePosterFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()

    def page(self, broken=False):
        page=self.browser.new_page();self.addCleanup(page.close)
        image='broken' if broken else 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
        html='''<html><body><button onclick="this.hidden=true;document.querySelector('textarea').hidden=false;document.querySelector('#gen').hidden=false">文字配图</button>
<textarea hidden></textarea><button id="gen" hidden onclick="window.original=document.querySelector('textarea').value;document.querySelector('textarea').hidden=true;this.hidden=true;document.querySelector('#cards').hidden=false;window.generations=(window.generations||0)+1">生成图片</button>
<div id="cards" hidden><div><img src="IMAGE"><span onclick="window.chosen='基础'">基础</span></div>
<div><img src="broken"><span onclick="window.chosen='提问'">提问</span></div>
<button onclick="document.querySelector('#cards').hidden=true;document.querySelector('#form').hidden=false;window.nexts=(window.nexts||0)+1">下一步</button></div>
<div id="form" hidden><input placeholder="标题"><textarea placeholder="正文"></textarea><button onclick="window.submits=(window.submits||0)+1">发布</button></div></body></html>'''.replace('IMAGE',image)
        page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html;charset=utf-8',body=html))
        page.goto('https://creator.rednote.com/publish')
        return page

    def test_native_card_generation_preserves_cover_and_excludes_broken_templates(self):
        page=self.page();result=prepare_builtin_poster(page,'原封面文字',timeout=2000,choose=lambda choices:choices[-1])
        self.assertEqual(result['template'],'基础');self.assertEqual(result['available_templates'],1)
        self.assertEqual(page.evaluate('window.original'),'原封面文字');self.assertEqual(page.evaluate('window.nexts'),1)
        result=fill_publish_form(page,'原发布标题','原正文','#原话题',[],selectors={
            'publish_title':'input[placeholder="标题"]','publish_content':'textarea[placeholder="正文"]'})
        self.assertTrue(result['ok'],result)
        self.assertEqual(page.locator('input').input_value(),'原发布标题')
        self.assertEqual(page.locator('textarea[placeholder="正文"]').input_value(),'原正文\n\n#原话题')
        self.assertIsNone(page.evaluate('window.submits'))

    def test_no_loaded_templates_never_clicks_next_or_regenerates(self):
        page=self.page(broken=True)
        with self.assertRaises(PosterGenerationError):prepare_builtin_poster(page,'原封面',timeout=300)
        self.assertEqual(page.evaluate('window.generations'),1);self.assertIsNone(page.evaluate('window.nexts'))

    def test_no_success_evidence_is_review_required_not_success(self):
        page=self.page();prepare_builtin_poster(page,'原封面',timeout=2000)
        with patch.object(page,'wait_for_function',side_effect=TimeoutError):
            ok,message=click_publish(page,selectors={'publish_button':'button:text-is("发布")'})
        self.assertFalse(ok);self.assertTrue(message.startswith('待核对'));self.assertEqual(page.evaluate('window.submits'),1)


if __name__=='__main__':unittest.main()
