"""Deterministic warmup scheduling tests: temporary SQLite, manual executor, no sleeps or scheduler threads."""
import concurrent.futures
from datetime import datetime, timedelta
import os
import tempfile
import unittest
from unittest.mock import patch

from app import warmup
from app.db import get_conn


NOW = datetime(2040, 5, 6, 10, 0, tzinfo=warmup.LOCAL_TZ)


class ManualExecutor:
    def __init__(self):
        self.jobs = []

    def submit(self, function, *args):
        future = concurrent.futures.Future()
        self.jobs.append((future, function, args))
        return future

    def run(self, index):
        future, function, args = self.jobs[index]
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(function(*args))
        except BaseException as exc:
            future.set_exception(exc)
            raise

    def shutdown(self, wait=False, cancel_futures=False):
        if cancel_futures:
            for future, _, _ in self.jobs:
                if not future.done():
                    future.cancel()


class FakeEvent:
    def __init__(self, initial=False, on_wait=None):
        self.value = initial
        self.on_wait = on_wait

    def is_set(self):
        return self.value

    def set(self):
        self.value = True

    def clear(self):
        self.value = False

    def wait(self, duration):
        if self.on_wait:
            self.on_wait(duration)
        return self.value


class WarmupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, 'warmup-test.sqlite')
        self.clock = patch('app.warmup.time.time', return_value=NOW.timestamp())
        self.clock.start()
        self.calls = []
        self.service = self.make_service(self.complete)

    def tearDown(self):
        self.service.close()
        self.clock.stop()
        self.directory.cleanup()

    def make_service(self, runner):
        service = warmup.WarmupService(self.path, runner)
        service.executor.shutdown(wait=False, cancel_futures=True)
        service.executor = ManualExecutor()
        return service

    def complete(self, target, remaining, engine, control, progress):
        self.calls.append((target, remaining, engine))
        control.active_seconds = remaining
        progress(remaining)
        return remaining

    def plan(self, **changes):
        data = {
            'name': 'deterministic plan', 'targets': [{'platform': 'douyin', 'id': 1}],
            'days': 2, 'minutes': 1, 'start_date': NOW.date().isoformat(),
            'start_time': '10:00', 'end_time': '11:00', 'concurrency': 1, 'engine': 'plugin',
        }
        data.update(changes)
        conn = get_conn(self.path)
        try:
            return warmup.create_plan(conn, data, today=NOW.date())
        finally:
            conn.close()

    def detail(self, pid):
        conn = get_conn(self.path)
        try:
            return warmup.plan_detail(conn, pid)
        finally:
            conn.close()

    def start(self, pid):
        self.service.set_state(pid, 'start', start_scheduler=False)
        self.assertIsNone(self.service.thread)

    def test_creation_requires_manual_start_and_daily_tasks_do_not_repeat(self):
        pid = self.plan()
        self.service.tick(NOW)
        self.assertEqual(self.service.executor.jobs, [])
        self.start(pid)
        self.service.tick(NOW - timedelta(seconds=1))
        self.assertEqual(self.service.executor.jobs, [])
        self.service.tick(NOW)
        self.assertEqual(len(self.service.executor.jobs), 1)
        self.service.executor.run(0)
        self.service.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(self.service.executor.jobs), 1)
        self.service.tick(NOW + timedelta(days=1))
        self.assertEqual(len(self.service.executor.jobs), 2)
        self.service.executor.run(1)
        self.service.tick(NOW + timedelta(days=1, minutes=1))
        detail = self.detail(pid)
        self.assertEqual(detail['status'], 'finished')
        self.assertEqual(detail['done'], 2)
        self.assertEqual([row['day_index'] for row in detail['runs']], [0, 1])
        self.assertTrue(all(row['elapsed_seconds'] == 60 for row in detail['runs']))

    def test_global_limit_and_same_target_are_isolated_across_plans(self):
        plans = [
            self.plan(targets=[{'platform': 'douyin', 'id': 1}], days=1),
            self.plan(targets=[{'platform': 'douyin', 'id': 1}], days=1),
            self.plan(targets=[{'platform': 'xiaohongshu', 'id': 1}], days=1),
            self.plan(targets=[{'platform': 'channels', 'id': 1}], days=1),
            self.plan(targets=[{'kind': 'android', 'serial': 'TEST-DEVICE'}], days=1),
        ]
        for pid in plans:
            self.start(pid)
        self.service.tick(NOW)
        self.assertEqual(len(self.service.active), 3)
        self.assertEqual(self.service.targets_busy, {'douyin:1', 'xiaohongshu:1', 'channels:1'})
        self.assertEqual(self.detail(plans[1])['runs'], [])
        self.assertEqual(self.detail(plans[-1])['runs'], [])
        self.service.executor.run(0)
        self.service.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(self.service.active), 3)
        self.assertEqual(self.detail(plans[1])['runs'][0]['status'], 'running')

    def test_per_plan_concurrency_is_respected(self):
        pid = self.plan(targets=[{'platform': 'douyin', 'id': value} for value in range(1, 5)], concurrency=2)
        self.start(pid)
        self.service.tick(NOW)
        self.assertEqual(len(self.service.active), 2)
        self.assertEqual(len(self.detail(pid)['runs']), 2)

    def test_pause_stop_and_resume_never_spawn_an_extra_task(self):
        pid = self.plan()
        self.start(pid)
        self.service.tick(NOW)
        control = self.service.executor.jobs[0][2][3]
        self.service.set_state(pid, 'pause', start_scheduler=False)
        self.assertTrue(control.pause.is_set())
        self.service.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(self.service.executor.jobs), 1)
        self.start(pid)
        self.assertFalse(control.pause.is_set())
        self.service.tick(NOW + timedelta(minutes=2))
        self.assertEqual(len(self.service.executor.jobs), 1)
        self.service.set_state(pid, 'stop', start_scheduler=False)
        self.assertTrue(control.cancelled())
        with self.assertRaisesRegex(ValueError, '正在停止'):
            self.start(pid)

    def test_restart_pauses_plan_and_preserves_checkpoint_until_manual_resume(self):
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW)
        conn = get_conn(self.path)
        try:
            with conn:
                conn.execute('UPDATE warmup_runs SET elapsed_seconds=12 WHERE plan_id=?', (pid,))
        finally:
            conn.close()
        self.service.close()
        self.service = self.make_service(self.complete)
        detail = self.detail(pid)
        self.assertEqual(detail['status'], 'paused')
        self.assertEqual(detail['runs'][0]['status'], 'stopped')
        self.assertEqual(detail['runs'][0]['elapsed_seconds'], 12)
        self.service.tick(NOW)
        self.assertEqual(self.service.executor.jobs, [])
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        self.assertEqual(self.calls[-1][1], 48)
        self.assertEqual(self.detail(pid)['runs'][0]['elapsed_seconds'], 60)

    def test_error_requires_explicit_retry_instead_of_automatic_repeated_launch(self):
        def fail(target, remaining, engine, control, progress):
            progress(4)
            control.active_seconds = 4
            raise RuntimeError('device offline')
        self.service.runner = fail
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        self.service.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(self.service.executor.jobs), 1)
        self.assertEqual(self.detail(pid)['runs'][0]['status'], 'error')
        self.assertEqual(self.detail(pid)['status'], 'paused')
        self.assertIn('device offline', self.detail(pid)['note'])
        self.assertTrue(self.service.controls[pid][0].is_set())
        self.service.retry_errors(pid)
        self.service.runner = self.complete
        self.start(pid)
        self.service.tick(NOW + timedelta(minutes=2))
        self.service.executor.run(1)
        self.assertEqual(self.calls[-1][1], 56)
        self.assertEqual(self.detail(pid)['runs'][0]['status'], 'done')

    def test_none_return_cannot_fabricate_a_full_minute(self):
        def partial(target, remaining, engine, control, progress):
            progress(5)
            control.active_seconds = 5
        self.service.runner = partial
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        row = self.detail(pid)['runs'][0]
        self.assertEqual(row['elapsed_seconds'], 5)
        self.assertEqual(row['status'], 'error')

    def test_failed_target_stops_other_active_targets_and_never_starts_next(self):
        def fail_or_cancel(target, remaining, engine, control, progress):
            if target['id'] == 1:
                raise RuntimeError('账号状态异常')
            self.assertTrue(control.cancelled())
            raise warmup.BrowseStopped('同计划其他账号失败，已停止')
        self.service.runner = fail_or_cancel
        pid = self.plan(targets=[{'platform': 'douyin', 'id': value} for value in (1, 2, 3)], concurrency=2)
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        self.service.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(self.service.executor.jobs), 2)
        self.service.executor.run(1)
        self.assertEqual(self.detail(pid)['status'], 'paused')
        self.assertEqual([row['status'] for row in self.detail(pid)['runs']], ['error', 'stopped'])

    def test_stop_preserves_last_measured_wait_without_a_progress_callback(self):
        def interrupted(target, remaining, engine, control, progress):
            control.active_seconds = 7.5
            control.stop.set()
            raise warmup.BrowseStopped('stopped during active wait')
        self.service.runner = interrupted
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        self.assertEqual(self.detail(pid)['runs'][0]['elapsed_seconds'], 7.5)
        self.assertEqual(self.detail(pid)['runs'][0]['status'], 'stopped')
        self.service.tick(NOW + timedelta(minutes=1))
        self.service.runner = self.complete
        self.start(pid)
        self.service.tick(NOW + timedelta(minutes=2))
        self.service.executor.run(1)
        self.assertEqual(self.calls[-1][1], 52.5)
        self.assertEqual(self.detail(pid)['runs'][0]['elapsed_seconds'], 60)

    def test_expired_plan_reports_incomplete_days_without_backfilling(self):
        pid = self.plan(days=2)
        self.start(pid)
        self.service.tick(NOW + timedelta(days=2))
        detail = self.detail(pid)
        self.assertEqual(detail['status'], 'finished')
        self.assertIn('0/2', detail['note'])
        self.assertEqual(detail['runs'], [])
        self.assertEqual(self.service.executor.jobs, [])

    def test_finishing_at_stop_keeps_the_completed_daily_task(self):
        def complete_then_stop(target, remaining, engine, control, progress):
            control.active_seconds = remaining
            control.stop.set()
            raise warmup.BrowseStopped('window closed at completion')
        self.service.runner = complete_then_stop
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW)
        self.service.executor.run(0)
        row = self.detail(pid)['runs'][0]
        self.assertEqual(row['status'], 'done')
        self.assertEqual(row['elapsed_seconds'], 60)

    def test_naive_scheduler_time_uses_shanghai_window_and_deadline(self):
        pid = self.plan(days=1)
        self.start(pid)
        self.service.tick(NOW.replace(tzinfo=None))
        control = self.service.executor.jobs[0][2][3]
        expected = NOW.replace(hour=11).timestamp()
        self.assertEqual(control.deadline, expected)
        self.assertFalse(control.cancelled())

    def test_pause_time_is_excluded_and_paused_control_stops_at_deadline(self):
        value = [0.0]
        pause = FakeEvent(True)
        waits = [0]
        def advance(duration):
            value[0] += duration
            waits[0] += 1
            if waits[0] == 2:
                pause.clear()
        stop = FakeEvent(on_wait=advance)
        control = warmup.BrowseControl(stop, pause, FakeEvent(), deadline=2)
        with patch('app.warmup.time.time', side_effect=lambda: value[0]), patch('app.warmup.time.monotonic', side_effect=lambda: value[0]):
            control.wait_active(0.5)
        self.assertAlmostEqual(control.active_seconds, 0.5)
        self.assertAlmostEqual(value[0], 0.9)
        pause.set(); value[0] = 0.0
        stop.on_wait = lambda duration: value.__setitem__(0, value[0] + duration)
        timed = warmup.BrowseControl(stop, pause, FakeEvent(), deadline=0.5)
        with patch('app.warmup.time.time', side_effect=lambda: value[0]), patch('app.warmup.time.monotonic', side_effect=lambda: value[0]):
            with self.assertRaises(warmup.BrowseStopped):
                timed.wait_active(1)
        self.assertEqual(timed.active_seconds, 0)


if __name__ == '__main__':
    unittest.main()
