import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from app.webapp import Api
from app import account_metrics as metrics
from app.share_links import classify
from app.page_guard import PageBlocked


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.api=Api(str(Path(self.tmp.name)/'desk.db'))
        self.aid=self.api.account_create({'nickname':'专用观察账号'})['id']
    def tearDown(self):
        self.api.metrics_close();self.api._batch.close();self.api._warmup.close();self.api.close_thread_conn();self.tmp.cleanup()
    def record(self,batch,value,date,approx=False):
        source=classify('https://rednote.com/user/profile/person')
        metrics.record(self.api.conn,source,{'accounts':{'xiaohongshu':self.aid}},batch,{'values':{'followers':{'value':value,'raw':str(value or ''),'approximate':approx}},'name':'目标'})
        self.api.conn.execute('UPDATE metric_snapshots SET sampled_at=? WHERE batch_id=?',(date,batch));self.api.conn.commit()
    def test_missing_counts_do_not_become_zero_and_rounded_counts_are_marked(self):
        self.assertIsNone(metrics.count_value('点赞')['value'])
        self.assertEqual(metrics.count_value('0')['value'],0)
        self.assertEqual(metrics.count_value('共 1,234 条评论')['value'],1234)
        self.assertEqual(metrics.count_value('1.2万')['value'],12000)
        self.assertTrue(metrics.count_value('1.2万')['approximate'])
    def test_cross_day_comparison_ignores_same_day_and_keeps_missing_baseline(self):
        self.record(1,100,'2026-10-07 09:00:00',True)
        self.record(2,110,'2026-10-10 08:00:00')
        self.record(3,120,'2026-10-10 09:00:00')
        row=metrics.dashboard(self.api.conn)[0]
        self.assertEqual((row['changes']['followers'],row['comparison_day'],row['day_gap']),(20,'2026-10-07',3))
        self.assertTrue(row['change_approximate']['followers'])
        self.record(4,None,'2026-10-11 09:00:00')
        self.assertIsNone(metrics.dashboard(self.api.conn)[0]['changes']['followers'])
    def test_owner_identity_keeps_public_handle_separate_and_preserves_account_data(self):
        before=self.api.accounts_list()['items']
        metrics.save_identity(self.api.conn,self.aid,'https://rednote.com/user/profile/internal1',{'public_id':'小红书号：public_7'})
        row=self.api.metrics_get()['identities'][0]
        self.assertEqual((row['user_id'],row['public_id']),('internal1','public_7'))
        self.assertEqual(before,self.api.accounts_list()['items'])
        metrics.save_identity(self.api.conn,self.aid,'https://rednote.com/user/profile/internal2')
        self.assertEqual(self.api.metrics_get()['identities'][0]['public_id'],'')
    def test_plan_is_saved_without_starting_and_run_requires_open_observer(self):
        saved=self.api.metrics_save({'observer_id':self.aid,'targets':'https://rednote.com/user/profile/person','works':20,'comments':1000})
        self.assertTrue(saved['ok'],saved)
        self.assertFalse(self.api.metrics_get()['config']['daily'])
        self.assertEqual(self.api.batch_list()['items'],[])
        self.assertFalse(self.api.metrics_run()['ok'])
        self.assertIsNone(self.api._metrics_thread)
    def test_targets_reject_other_platforms_and_content_urls(self):
        for link in ['https://douyin.com/user/person','https://rednote.com/explore/note','https://rednote.com.attacker.test/user/profile/person']:
            self.assertFalse(self.api.metrics_save({'observer_id':self.aid,'targets':link})['ok'])
    def test_page_mismatch_and_access_gate_do_not_write_metrics(self):
        page=Mock(url='https://rednote.com/explore/other')
        with patch('app.page_guard.check_page'):
            with self.assertRaises(ValueError):metrics.read_metrics(page,classify('https://rednote.com/explore/target'))
        page.evaluate.assert_not_called()
        with patch('app.page_guard.check_page',side_effect=PageBlocked({'error':'需要验证','blocked':True})):
            with self.assertRaises(PageBlocked):metrics.read_metrics(page,classify('https://rednote.com/explore/other'))
        self.assertEqual(self.api.metrics_get()['items'],[])
    def test_daily_plan_never_restarts_paused_or_failed_batch_on_a_new_day(self):
        from datetime import datetime
        self.assertTrue(self.api.metrics_save({'observer_id':self.aid,'targets':'https://rednote.com/user/profile/person'})['ok'])
        batch=self.api.batch_create('https://rednote.com/user/profile/person',{'xiaohongshu':self.aid},[])['id']
        config=self.api.metrics_get()['config'];config['daily']=True
        self.api.conn.execute("UPDATE metric_settings SET config=?,last_day='2026-10-09',last_batch=?",(json.dumps(config),batch))
        for state in ['paused','stopped','done_with_errors','running']:
            self.api.conn.execute('UPDATE collect_batches SET status=? WHERE id=?',(state,batch));self.api.conn.commit()
            with patch.object(self.api,'metrics_run') as run:
                self.api._metrics_tick(datetime(2026,10,10,12,0));run.assert_not_called()

    def test_comments_survive_access_gate_discovered_during_metric_read(self):
        from types import SimpleNamespace
        page=Mock(url='https://rednote.com/explore/note')
        browser=SimpleNamespace(ctx=SimpleNamespace(pages=[page]));page.is_closed.return_value=False
        entry=classify(page.url)
        rows=[{'text':'已经读取的评论','comment_id':'c'}]
        with patch.object(self.api,'_call_xhs_browser',side_effect=lambda aid,fn,**kwargs:fn(browser)),patch('app.builtin_crawler.collect',return_value={'ok':True,'read':1,'items':rows}),patch.object(self.api,'_read_batch_metrics',side_effect=PageBlocked({'blocked':True,'error':'出现验证'})):
            result=self.api._run_batch_source(entry,{'accounts':{'xiaohongshu':self.aid},'words':[],'comments':100})
        self.assertTrue(result['blocked']);self.assertEqual(result['items'],rows);self.assertEqual(result['read'],1)

    def test_csv_protects_formula_and_does_not_export_access_tokens(self):
        self.record(1,5,'2026-10-10 09:00:00')
        self.api.conn.execute("UPDATE metric_snapshots SET name='=1+1'");self.api.conn.commit()
        exported=self.api.metrics_export()
        self.assertTrue(exported['ok'],exported)
        self.assertIn("'=1+1",exported['csv'])
        self.assertNotIn('xsec_token',exported['csv'])
    def test_scoped_douyin_leads_and_export_match_account_filter(self):
        ids=[self.api.dy_save_account({'nickname':name})['id'] for name in ['甲','乙']]
        for aid in ids:self.api.dy_import(aid,'note','video',json.dumps([{'user_id':'same','content':'价格'}]))
        self.assertEqual(len(self.api.dy_leads()['items']),2)
        rows=self.api.dy_leads('',ids[1])['items']
        self.assertEqual([row['account_id'] for row in rows],[ids[1]])
        self.assertEqual(self.api.dy_export('leads',ids[1])['count'],1)
        self.assertEqual(self.api.dy_leads('won',ids[1])['items'],[])

if __name__=='__main__':unittest.main()
