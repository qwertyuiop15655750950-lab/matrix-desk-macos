import json
import tempfile
import sqlite3
import threading
import queue
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
from app.webapp import Api
from app import account_hub

class AccountHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.api=Api(str(Path(self.tmp.name)/'test.db'))
    def tearDown(self):
        self.api.close_thread_conn();self.tmp.cleanup()
    def test_multiplatform_import_idempotent_and_local_only(self):
        text='platform,nickname,group_name,notes\n小红书,同名账号,一组,扫码\n抖音,同名账号,一组,扫码\n视频号,同名账号,一组,扫码\n'
        with patch.object(self.api._dy_browsers,'open') as dy_open, patch.object(self.api._channels_browsers,'open') as ch_open:
            self.assertEqual(self.api.hub_import(text)['added'],3)
            self.assertEqual(self.api.hub_import(text)['skipped'],3)
            dy_open.assert_not_called();ch_open.assert_not_called()
        result=self.api.hub_accounts()
        self.assertTrue(result['ok'])
        self.assertEqual(result['counts'],{'xiaohongshu':1,'douyin':1,'channels':1})
        self.assertEqual(len({r['key'] for r in result['items']}),3)
        self.assertTrue(all(not r['running'] for r in result['items']))
    def test_invalid_import_is_atomic(self):
        rows=[{'platform':'douyin','nickname':'有效'},{'platform':'invalid','nickname':'无效'}]
        self.assertFalse(self.api.hub_import(json.dumps(rows))['ok'])
        self.assertEqual(self.api.hub_accounts()['items'],[])
    def test_edit_reuses_channel_profile_id(self):
        aid=self.api.hub_save('channels',{'nickname':'视频号A','notes':'扫码'})['id']
        self.assertEqual(self.api.hub_save('视频号',{'id':aid,'nickname':'视频号B','notes':'更新'})['id'],aid)
        self.assertEqual(self.api.hub_accounts('channels')['items'][0]['nickname'],'视频号B')
    def test_open_dispatches_to_chosen_platform(self):
        dy_aid=self.api.hub_save('douyin',{'nickname':'抖音'})['id']
        ch_aid=self.api.hub_save('channels',{'nickname':'视频号'})['id']
        with patch.object(self.api._dy_browsers,'open') as dy_open, patch.object(self.api._channels_browsers,'open') as ch_open:
            self.assertTrue(self.api.hub_open('channels',ch_aid)['ok'])
            self.assertTrue(self.api.hub_open('douyin',dy_aid)['ok'])
            self.assertEqual(ch_open.call_args.args[0]['nickname'],'视频号')
            self.assertEqual(dy_open.call_args.args[0]['nickname'],'抖音')
        self.assertFalse(self.api.hub_open('channels',9999)['ok'])
    def test_xhs_hub_open_uses_rednote_home_without_changing_account(self):
        aid=self.api.hub_save('xiaohongshu',{'nickname':'海外账号'})['id']
        before=account_hub.get(self.api.conn,'xiaohongshu',aid)
        page=Mock(url='about:blank')
        browser=SimpleNamespace(ctx=SimpleNamespace(pages=[page]))
        with patch.object(self.api,'_ensure_actor') as actor:
            actor.return_value.call.side_effect=lambda fn, **kw: fn(browser)
            result=self.api.hub_open('xiaohongshu',aid)
        self.assertTrue(result['ok'],result)
        self.assertEqual(page.goto.call_args.args[0],'https://www.rednote.com')
        self.assertEqual(account_hub.get(self.api.conn,'xiaohongshu',aid),before)
        page.bring_to_front.assert_called_once()

    def test_show_existing_xhs_browser_preserves_current_page(self):
        aid=self.api.hub_save('xiaohongshu',{'nickname':'海外账号'})['id']
        page=Mock(url='https://www.rednote.com/discovery/item/current-note')
        browser=SimpleNamespace(ctx=SimpleNamespace(pages=[page]))
        with patch.object(self.api,'_ensure_actor') as actor:
            actor.return_value.call.side_effect=lambda fn, **kw: fn(browser)
            self.assertTrue(self.api.hub_open('xiaohongshu',aid)['ok'])
        page.goto.assert_not_called()
        page.bring_to_front.assert_called_once()
    def test_saved_xhs_proxy_fields_survive_hub_edit(self):
        aid=self.api.account_create({'nickname':'小红书','proxy_host':'proxy.local','proxy_port':1234})['id']
        self.api.hub_save('xiaohongshu',{'id':aid,'nickname':'改名','notes':'只更新备注'})
        row=account_hub.get(self.api.conn,'xiaohongshu',aid)
        self.assertEqual((row['proxy_host'],row['proxy_port']),('proxy.local',1234))

    def test_xhs_plugin_uses_selected_actor_and_archives(self):
        from types import SimpleNamespace
        aid=self.api.hub_save('xiaohongshu',{'nickname':'采集账号'})['id']
        page=SimpleNamespace(url='https://www.xiaohongshu.com/explore/test')
        browser=SimpleNamespace(ctx=SimpleNamespace(pages=[page]))
        item={'nickname':'客户','user_id':'user-id','text':'多少钱','keyword':'多少钱','contacts':{'wechat':'','phone':'','qq':''}}
        with patch.object(self.api,'_ensure_actor') as actor, patch('app.browser_plugin_bridge.scan_with_plugin', return_value={'ok':True,'items':[item]}) as scan:
            actor.return_value.call.side_effect=lambda fn,**kw: fn(browser)
            result=self.api.hub_collect('xiaohongshu',aid,page.url)
            self.assertTrue(result['ok']);self.assertEqual(result['added'],1)
            actor.assert_called_once_with(aid)
            self.assertEqual(scan.call_args.args[0],page)
            self.assertEqual(scan.call_args.kwargs['platform'],'xiaohongshu')
        self.assertEqual(len(self.api.hub_collected()['items']),1)
    def test_channels_collect_does_not_open_or_scan(self):
        aid=self.api.hub_save('channels',{'nickname':'视频号'})['id']
        with patch.object(self.api._channels_browsers,'open') as opened:
            self.assertFalse(self.api.hub_collect('channels',aid,'https://channels.weixin.qq.com/')['ok'])
            opened.assert_not_called()

    def test_xhs_capacity_matches_existing_account_page(self):
        self.api.MAX_ACCOUNTS=1
        self.assertTrue(self.api.hub_save('xiaohongshu',{'nickname':'账号1'})['ok'])
        self.assertFalse(self.api.hub_save('xiaohongshu',{'nickname':'账号2'})['ok'])
        rows=[{'platform':'channels','nickname':'视频号1'},{'platform':'xiaohongshu','nickname':'账号2'}]
        self.assertFalse(self.api.hub_import(json.dumps(rows))['ok'])
        self.assertEqual(self.api.hub_accounts()['counts']['channels'],0)

    def concurrent_writes(self, first_operation, second_operation):
        """Deterministically expose stale reads without timing assumptions.

        First writer pauses after observing capacity. A BEGIN IMMEDIATE trace
        releases it before the second writer can read. For the old unlocked
        path, the second count query captures the stale count, then releases the
        first writer and returns that old count only after its commit.
        """
        first_read = threading.Event()
        release_first = threading.Event()
        first_done = threading.Event()
        outcomes = queue.Queue()
        database = str(Path(self.tmp.name)/'test.db')
        class CountCursor:
            def __init__(self, row): self.row = row
            def fetchone(self): return self.row
        class CoordinatedConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if sql.strip().upper() == 'SELECT COUNT(*) FROM ACCOUNTS' and not self.count_seen:
                    self.count_seen = True
                    row = cursor.fetchone()
                    if self.role == 'first':
                        first_read.set()
                        if not release_first.wait(5): raise AssertionError('first writer not released')
                    else:
                        release_first.set()
                        if not first_done.wait(5): raise AssertionError('first writer did not finish')
                    return CountCursor(row)
                return cursor
        def worker(role, operation):
            conn = sqlite3.connect(database, timeout=5, factory=CoordinatedConnection)
            conn.row_factory = sqlite3.Row
            conn.role, conn.count_seen = role, False
            if role == 'second':
                conn.set_trace_callback(lambda sql: release_first.set()
                    if sql.strip().upper() == 'BEGIN IMMEDIATE' else None)
            try:
                outcomes.put((role, operation(conn)))
            except Exception as error:
                outcomes.put((role, error))
            finally:
                conn.close()
                if role == 'first': first_done.set()
        first = threading.Thread(target=worker, args=('first', first_operation))
        second = threading.Thread(target=worker, args=('second', second_operation))
        first.start()
        try:
            self.assertTrue(first_read.wait(5), 'first operation did not check capacity')
            second.start()
            first.join(5); second.join(5)
            self.assertFalse(first.is_alive(), 'first writer did not complete')
            self.assertFalse(second.is_alive(), 'second writer did not complete')
            return dict(outcomes.get_nowait() for _ in range(2))
        finally:
            release_first.set()
            first.join(5)
            if second.ident is not None: second.join(5)

    def test_parallel_duplicate_imports_are_idempotent(self):
        text = json.dumps([{'platform':'xiaohongshu','nickname':'同一个账号'}])
        outcomes = self.concurrent_writes(
            lambda conn: account_hub.import_accounts(conn, text),
            lambda conn: account_hub.import_accounts(conn, text))
        self.assertEqual(outcomes['first'], {'added':1,'skipped':0})
        self.assertEqual(outcomes['second'], {'added':0,'skipped':1})
        self.assertEqual(len(account_hub.list_accounts(self.api.conn, 'xiaohongshu')), 1)

    def test_parallel_import_capacity_rejects_entire_second_batch(self):
        first = json.dumps([{'platform':'channels','nickname':'视频号一'},
                            {'platform':'xiaohongshu','nickname':'小红书一'}])
        second = json.dumps([{'platform':'channels','nickname':'视频号二'},
                             {'platform':'xiaohongshu','nickname':'小红书二'}])
        outcomes = self.concurrent_writes(
            lambda conn: account_hub.import_accounts(conn, first, max_xhs=1),
            lambda conn: account_hub.import_accounts(conn, second, max_xhs=1))
        self.assertEqual(outcomes['first'], {'added':2,'skipped':0})
        self.assertIsInstance(outcomes['second'], ValueError)
        self.assertIn('超过原版上限1个', str(outcomes['second']))
        self.assertEqual({row['nickname'] for row in account_hub.list_accounts(self.api.conn)},
                         {'视频号一','小红书一'})

    def test_parallel_single_xhs_creations_respect_capacity(self):
        outcomes = self.concurrent_writes(
            lambda conn: account_hub.save(conn, 'xiaohongshu', {'nickname':'账号一'}, max_xhs=1),
            lambda conn: account_hub.save(conn, 'xiaohongshu', {'nickname':'账号二'}, max_xhs=1))
        self.assertIsInstance(outcomes['first'], int)
        self.assertIsInstance(outcomes['second'], ValueError)
        self.assertIn('已达到原版上限1个', str(outcomes['second']))
        self.assertEqual([row['nickname'] for row in account_hub.list_accounts(self.api.conn, 'xiaohongshu')], ['账号一'])

    def test_original_account_page_and_hub_share_atomic_capacity_limit(self):
        self.api.MAX_ACCOUNTS = 1
        def original_account_create(conn):
            # Bind the same instrumented connection to Api's worker-thread
            # property, so this exercises the actual original-page method.
            self.api._conn_local.c = conn
            return self.api.account_create({'nickname':'原账号页新建',
                'proxy_scheme':'socks5','proxy_host':'proxy.local','proxy_port':1234,
                'proxy_user':'proxy-user','proxy_pass':'test-password'})
        outcomes = self.concurrent_writes(
            lambda conn: account_hub.save(conn, 'xiaohongshu', {'nickname':'账号中心新建'}, max_xhs=1),
            original_account_create)
        self.assertIsInstance(outcomes['first'], int)
        self.assertEqual(outcomes['second'], {'ok':False,'error':'已达上限 1 个账号，删掉不用的再添加'})
        self.assertEqual([row['nickname'] for row in account_hub.list_accounts(self.api.conn, 'xiaohongshu')],
                         ['账号中心新建'])

    def test_xhs_edit_rejects_other_platform_legacy_row(self):
        cursor = self.api.conn.execute(
            "INSERT INTO accounts(nickname,platform) VALUES(?,?)", ('旧抖音账号','douyin'))
        self.api.conn.commit()
        result = self.api.hub_save('xiaohongshu', {'id':cursor.lastrowid,'nickname':'不应改名'})
        self.assertFalse(result['ok'])
        self.assertIn('不属于小红书', result['error'])
        row = self.api.conn.execute('SELECT nickname,platform FROM accounts WHERE id=?',
                                    (cursor.lastrowid,)).fetchone()
        self.assertEqual((row['nickname'],row['platform']), ('旧抖音账号','douyin'))

if __name__=='__main__': unittest.main()
