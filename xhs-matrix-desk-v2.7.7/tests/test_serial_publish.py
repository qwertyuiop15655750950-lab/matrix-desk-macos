import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from app.webapp import Api
from app import publish_runtime as runtime


class SerialPublishTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.api=Api(str(Path(self.tmp.name)/'desk.db'))
    def tearDown(self):
        self.api.metrics_close();self.api._batch.close();self.api._warmup.close();self.api.close_thread_conn();self.tmp.cleanup()
    def test_repeated_start_is_rejected_before_thread_launch_and_error_releases(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        def job():entered.set();release.wait(2)
        self.assertTrue(runtime.start(self.api,job)['ok']);self.assertTrue(entered.wait(1))
        second=runtime.start(self.api,lambda:self.fail('duplicate job started'))
        self.assertFalse(second['ok']);release.set()
        # A following job must wait for the reservation to release.
        for _ in range(100):
            if not self.api._publish_busy:break
            finished.wait(.01)
        self.assertFalse(self.api._publish_busy)
        with patch('app.publish_runtime.threading.Thread') as thread:
            thread.return_value.start.side_effect=RuntimeError('cannot start')
            with self.assertRaises(RuntimeError):runtime.start(self.api,job)
        self.assertFalse(self.api._publish_busy)
    def test_actor_is_closed_before_saved_profile_flow_and_reservation_released(self):
        events=[]
        actor=Mock();actor.heartbeat.return_value={'current_task':None};actor.is_dead.return_value=False
        actor.stop.side_effect=lambda:events.append('close');actor.is_alive.return_value=False
        self.api._actors[1]=actor
        def publish(*args):
            events.append('publish');self.assertIsNone(self.api._get_actor(1))
            self.assertFalse(self.api.hub_open('xiaohongshu',1)['ok'])
            return 'done'
        self.assertEqual(runtime.publish_one(self.api,publish,None,None,{},1,1,0),'done')
        self.assertEqual(events,['close','publish']);self.assertEqual(self.api._browser_jobs,{})
    def test_busy_browser_and_still_alive_actor_are_never_removed_or_published(self):
        actor=Mock();actor.is_dead.return_value=False;self.api._actors[1]=actor
        actor.heartbeat.return_value={'current_task':'collect'}
        callback=Mock()
        with self.assertRaises(ValueError):runtime.publish_one(self.api,callback,None,None,{},1,1,0)
        actor.stop.assert_not_called();callback.assert_not_called()
        actor.heartbeat.return_value={'current_task':None};actor.is_alive.return_value=True
        with self.assertRaises(ValueError):runtime.publish_one(self.api,callback,None,None,{},1,1,0)
        self.assertIs(self.api._actors[1],actor);callback.assert_not_called()
        self.assertEqual(self.api._browser_jobs,{})
    def test_temporary_browser_always_closes_if_login_is_expired(self):
        aid=self.api.account_create({'nickname':'测试'})['id'];ctx=Mock()
        draft={'title':'标题','body':'正文','topics':'','mode':'image','media_json':'[]'}
        with patch('app.xhs_api.prof.launch_account',return_value=(ctx,'directory')),patch('app.xhs_api.prof.ensure_login',return_value=False):
            self.assertEqual(self.api._publish_one(None,self.api.conn,draft,1,aid,0),'skipped')
        ctx.close.assert_called_once()
    def test_auto_queue_pauses_when_a_worker_makes_no_progress(self):
        self.api.conn.execute("INSERT INTO drafts(title,body,mode,created_at,updated_at) VALUES('草稿','正文','image','now','now')")
        self.api.conn.commit();qid=self.api.queue_create(1,[1])['id']
        with patch.object(self.api,'_publish_worker') as worker:self.api._queue_worker_inner(qid,True)
        self.assertEqual(worker.call_count,1);self.assertEqual(self.api._queue_row(qid)['status'],'paused')

    def test_failure_closes_context_and_does_not_hold_next_account_reservation(self):
        aid=self.api.account_create({'nickname':'测试'})['id'];ctx=Mock();draft={'title':'标题','body':'正文','topics':'','mode':'image','media_json':'[]'}
        with patch('app.xhs_api.prof.launch_account',return_value=(ctx,'directory')),patch('app.xhs_api.prof.ensure_login',return_value=True),patch('app.xhs_api.pub.prepare_page',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):self.api._publish_one(None,self.api.conn,draft,1,aid,0)
        ctx.close.assert_called_once();self.assertEqual(self.api._browser_jobs,{})

if __name__=='__main__':unittest.main()
