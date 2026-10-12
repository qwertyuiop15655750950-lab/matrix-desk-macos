import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.browse_api import BrowseAPI
from app.db import get_conn
from app.warmup import create_plan, ensure, plan_detail


class Harness(BrowseAPI):
    def __init__(self, conn, ready):
        self.conn = conn
        self.ready = ready
        self._warmup = Mock()
        self._warmup.set_state.return_value = 'running'

    def warmup_targets(self):
        return {'ok': True, 'items': self.ready}

    def _ok(self, **values):
        return {'ok': True, **values}

    def _err(self, error):
        return {'ok': False, 'error': str(error)}


class BrowseGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = get_conn(str(Path(self.temp.name) / 'guard.db'))
        ensure(self.conn)
        self.pid = create_plan(self.conn, {'targets': [{'platform': 'douyin', 'id': 1}]})

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_unopened_target_does_not_start_or_change_plan_status(self):
        api = Harness(self.conn, [{'key': 'douyin:1', 'ready': False}])
        self.assertFalse(api.warmup_action(self.pid, 'start')['ok'])
        api._warmup.set_state.assert_not_called()
        self.assertEqual(plan_detail(self.conn, self.pid)['status'], 'draft')

    def test_retry_checks_readiness_before_clearing_error_records(self):
        api = Harness(self.conn, [{'key': 'xiaohongshu:1', 'ready': True}])
        self.assertFalse(api.warmup_action(self.pid, 'retry')['ok'])
        api._warmup.retry_errors.assert_not_called()
        api._warmup.set_state.assert_not_called()

    def test_selected_ready_target_can_start_despite_other_unopened_accounts(self):
        api = Harness(self.conn, [{'key': 'douyin:1', 'ready': True}, {'key': 'douyin:2', 'ready': False}])
        self.assertTrue(api.warmup_action(self.pid, 'start')['ok'])
        api._warmup.set_state.assert_called_once_with(self.pid, 'start')

    def test_pause_remains_available_without_open_browser(self):
        api = Harness(self.conn, [])
        self.assertTrue(api.warmup_action(self.pid, 'pause')['ok'])
        api._warmup.set_state.assert_called_once_with(self.pid, 'pause')
