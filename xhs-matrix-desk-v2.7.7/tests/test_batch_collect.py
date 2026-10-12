import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from app.share_links import parse_shares, classify, OfficialRedirect
from app.batch_collect import BatchCollector
from app.webapp import Api
from app.comment_intent import review_hint


class ShareTests(unittest.TestCase):
    def test_actual_share_formats_mixed_platforms_dedup_and_access_parameters(self):
        text = '''5.84 QXM:/ 40岁亲测 https://v.douyin.com/mHzY-UE1y8g/ 复制此链接！
        31 【小学开学对比】 https://www.xiaohongshu.com/discovery/item/6a976f63000000002802906a?xsec_token=ACCESS&source=webshare
        https://www.xiaohongshu.com/explore/6a976f63000000002802906a?xsec_token=OTHER
        https://www.douyin.com/user/MS4wLjABAAAAexample
        https://www.xiaohongshu.com/user/profile/6a976f63'''
        parsed = parse_shares(text)
        self.assertEqual(len(parsed['items']), 4)
        self.assertEqual(parsed['duplicates'], 1)
        self.assertIn('xsec_token=ACCESS', parsed['items'][1]['url'])
        self.assertNotIn('ACCESS', parsed['items'][1]['canonical'])
        self.assertEqual([r['kind'] for r in parsed['items']], ['share','content','profile','profile'])

    def test_foreign_hosts_credentials_ports_and_unsupported_paths_rejected(self):
        for url in ['https://www.douyin.com.attacker.test/video/1','http://www.douyin.com/video/1','https://u:p@www.douyin.com/video/1','https://www.xiaohongshu.com:8888/explore/1','https://www.douyin.com/']:
            with self.subTest(url=url), self.assertRaises(ValueError): classify(url)
        with self.assertRaises(ValueError): parse_shares('没有链接')

    def test_rednote_share_aliases_deduplicate_without_losing_navigation_url(self):
        from app.comment_crawler import _same_content
        text='https://www.rednote.com/discovery/item/note1?xsec_token=ACCESS\nhttps://www.xiaohongshu.com/explore/note1\nhttps://rednote.com/user/profile/user1'
        result=parse_shares(text)
        self.assertEqual(len(result['items']),2)
        self.assertEqual(result['duplicates'],1)
        self.assertEqual(result['items'][0]['platform'],'xiaohongshu')
        self.assertIn('rednote.com',result['items'][0]['url'])
        self.assertIn('xsec_token=ACCESS',result['items'][0]['url'])
        self.assertEqual(result['items'][0]['canonical'],'https://www.xiaohongshu.com/explore/note1')
        self.assertTrue(_same_content('https://rednote.com/explore/note1','https://www.xiaohongshu.com/discovery/item/note1','xiaohongshu'))
        for url in ['https://rednote.com.evil.test/explore/note1','https://evilrednote.com/explore/note1','https://user@rednote.com/explore/note1','https://rednote.com:8888/explore/note1']:
            with self.subTest(url=url),self.assertRaises(ValueError):classify(url)

    def test_short_redirect_blocks_unexpected_host_before_follow(self):
        with self.assertRaises(ValueError):
            OfficialRedirect().redirect_request(None,None,302,'',{},'https://127.0.0.1/private')

    def test_review_tags_do_not_treat_question_as_private_invitation(self):
        self.assertFalse(review_hint('多少钱，我想做')['request_candidate'])
        self.assertTrue(review_hint('请私信我报价')['request_candidate'])
        self.assertFalse(review_hint('不要私信我，谢谢')['request_candidate'])


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.api=Api(str(Path(self.temp.name)/'test.db'))
        self.account=self.api.hub_save('douyin',{'nickname':'测试登录账号'})['id']
    def tearDown(self):
        self.api._batch.close(); self.api._warmup.close(); self.api.close_thread_conn(); self.temp.cleanup()
    def await_done(self):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if self.api._batch.thread is None: return
            time.sleep(.01)
        self.fail('worker did not finish')
    def create(self,text='https://www.douyin.com/video/1'):
        response=self.api.batch_create(text,{'douyin':self.account},['价格'],20,1000)
        self.assertTrue(response['ok'],response)
        return response['id']
    def test_preview_never_exposes_access_query_and_creation_does_not_start(self):
        preview=self.api.batch_preview('https://www.xiaohongshu.com/explore/abc?xsec_token=SECRET')
        self.assertNotIn('SECRET',json.dumps(preview))
        bid=self.create()
        self.assertEqual(self.api.batch_list()['items'][0]['status'],'draft')
        self.assertIsNone(self.api._batch.thread)
    def test_all_comments_saved_even_without_keyword_or_public_id_and_dedup(self):
        rows=[{'text':'没有关键词','nickname':'甲','user_id':''},{'text':'价格多少','nickname':'乙','user_id':'public-id','comment_id':'c1'}]
        self.api._batch.runner=lambda source,config:{'ok':True,'items':rows+rows,'read':2,'truncated':True}
        bid=self.create()
        self.api.batch_action(bid,'start');self.await_done()
        saved=self.api._batch.results(bid)
        self.assertEqual(len(saved),2)
        self.assertEqual(saved[0]['matched_words'],'[]')
        self.assertEqual(saved[0]['user_id'],'')
        self.assertTrue(self.api.batch_list()['items'][0]['sources'][0]['result']['truncated'])
        self.assertNotIn('xsec_token',self.api.batch_export(bid)['csv'])

    def test_access_gate_pauses_batch_preserves_partial_rows_and_resumes_same_source(self):
        calls=[]
        def blocked(source, config):
            calls.append(source['canonical'])
            return {'ok':False,'blocked':True,'requires_review':True,'stop_reason':'captcha','error':'请人工处理验证','items':[{'text':'已读评论','user_id':'u1','comment_id':'c1'}]}
        self.api._batch.runner=blocked
        bid=self.create('https://www.douyin.com/video/1\nhttps://www.douyin.com/video/2')
        self.api.batch_action(bid,'start');self.await_done()
        batch=self.api.batch_list()['items'][0]
        self.assertEqual(batch['status'],'paused')
        self.assertEqual(len(calls),1)
        self.assertEqual(batch['comments'],1)
        self.assertEqual([row['status'] for row in batch['sources']],['pending','pending'])
        self.assertTrue(batch['sources'][0]['result']['requires_review'])
        self.api._batch.runner=lambda source, config: {'ok':True,'items':[{'text':'已读评论','user_id':'u1','comment_id':'c1'}]}
        self.api.batch_action(bid,'start');self.await_done()
        batch=self.api.batch_list()['items'][0]
        self.assertEqual(batch['status'],'done')
        self.assertEqual(batch['comments'],2)
        self.assertEqual(batch['sources'][0]['error'],'')

    def test_other_queued_batch_on_same_account_is_also_paused(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def blocked(source, config):
            calls.append(source['canonical']);entered.set();release.wait(2)
            return {'ok':False,'blocked':True,'items':[],'error':'访问频繁','stop_reason':'rate_limited'}
        self.api._batch.runner=blocked
        first=self.create();second=self.create('https://www.douyin.com/video/2')
        self.api.batch_action(first,'start');self.assertTrue(entered.wait(1))
        self.api.batch_action(second,'start');release.set();self.await_done()
        self.assertEqual(len(calls),1)
        self.assertTrue(all(row['status']=='paused' for row in self.api.batch_list()['items']))

    def test_profile_gate_is_reported_before_extracting_work_links(self):
        from types import SimpleNamespace
        from app.page_guard import PageBlocked
        from unittest.mock import Mock
        page=SimpleNamespace(goto=Mock())
        self.api._dy_browsers.perform=lambda account, run, **kwargs: run(page)
        result={'blocked':True,'stop_reason':'login_required','error':'需要登录','requires_review':True}
        with patch('app.page_guard.check_page',side_effect=PageBlocked(result)):
            response=self.api._run_batch_source({'platform':'douyin','kind':'profile','url':'https://www.douyin.com/user/profile-id','canonical':'https://www.douyin.com/user/profile-id'}, {'accounts':{'douyin':self.account},'works':20})
        self.assertTrue(response['blocked'])
        self.assertNotIn('works',response)
    def test_profile_expands_20_works_failure_continues_and_is_not_empty_success(self):
        calls=[]
        def runner(source,config):
            calls.append(source['canonical'])
            if source['kind']=='profile':
                return {'ok':True,'works':[{'url':f'https://www.douyin.com/video/{i}','canonical':f'https://www.douyin.com/video/{i}'} for i in range(30)]}
            if source['canonical'].endswith('/0'):return {'ok':False,'error':'作品不可见'}
            return {'ok':True,'items':[{'text':'测试','user_id':'','nickname':'甲'}]}
        self.api._batch.runner=runner
        bid=self.create('https://www.douyin.com/user/profile-id')
        self.api.batch_action(bid,'start');self.await_done()
        batch=self.api.batch_list()['items'][0]
        self.assertEqual(len(batch['sources']),21)
        self.assertEqual(batch['comments'],19)
        self.assertEqual(batch['status'],'done_with_errors')
        self.assertEqual(len(calls),21)
    def test_pause_prevents_next_source_and_preserves_current_result(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def runner(source,config):
            calls.append(source['canonical']);entered.set();release.wait(2)
            return {'ok':True,'items':[{'text':'已读取','nickname':'甲'}]}
        self.api._batch.runner=runner
        bid=self.create('https://www.douyin.com/video/1\nhttps://www.douyin.com/video/2')
        self.api.batch_action(bid,'start');self.assertTrue(entered.wait(1))
        self.api.batch_action(bid,'pause');release.set();self.await_done()
        batch=self.api.batch_list()['items'][0]
        self.assertEqual(batch['status'],'paused');self.assertEqual(batch['comments'],1)
        self.assertEqual(len(calls),1)
    def test_core_has_no_android_api_and_materials_are_frozen(self):
        self.assertFalse(hasattr(self.api,'android_devices'))
        self.assertFalse(self.api.material_list()['ok'])
        self.assertEqual(self.api.MAX_ACCOUNTS,20)
        self.assertTrue(self.api.app_info()['features']['warmup'])
    def test_browser_panel_stop_pauses_following_works_and_retains_partial_comments(self):
        calls=[]
        def runner(source,config):
            calls.append(source['canonical'])
            return {'ok':True,'stopped':True,'items':[{'text':'部分结果','nickname':'甲'}]}
        self.api._batch.runner=runner
        bid=self.create('https://www.douyin.com/video/1\nhttps://www.douyin.com/video/2')
        self.api.batch_action(bid,'start');self.await_done()
        batch=self.api.batch_list()['items'][0]
        self.assertEqual((batch['status'],batch['comments'],len(calls)),('paused',1,1))


class BuiltinCrawlerTests(unittest.TestCase):
    def test_local_visible_comments_without_external_projects(self):
        from playwright.sync_api import sync_playwright
        from app.builtin_crawler import collect
        html='''<html><head><title>本地评论</title></head><body><main class="comment-list"><article class="comment-item" data-comment-id="a"><a class="author" href="/user/u1">甲</a><p class="comment-text">价格多少</p></article><article class="comment-item"><span class="author">乙</span><p class="comment-text">没有关键词也保留</p></article></main></body></html>'''
        with patch.dict(os.environ,{'MATRIX_BB_BROWSER_DIR':'/missing','MATRIX_CRAWL4AI_PYTHON':'/missing'}),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                url='https://www.douyin.com/video/123';page.goto(url)
                result=collect(page,url,['价格'],collect_all=True)
                self.assertTrue(result['ok'],result)
                self.assertEqual(len(result['items']),2)
                self.assertEqual(result['items'][0]['comment_id'],'a')
                self.assertEqual(result['items'][1]['user_id'],'')
                self.assertEqual(result['engine'],'builtin')
            finally:browser.close()
