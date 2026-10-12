import base64
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
import zlib

from app.android_control import AndroidController
from app.browse_control import BrowseStopped


def png(width=200, height=400):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    pixels = (b'\0' * (width + 1)) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


class Transport:
    """Only an in-memory Android fixture; no executable or device is invoked."""
    def __init__(self):
        self.states = {'phone-A': 'device', 'phone-B': 'unauthorized', 'phone-C': 'offline', 'phone-D': 'device'}
        self.fields = {serial: '' for serial in self.states}
        self.focused = {serial: False for serial in self.states}
        self.titles = {serial: '联系人' for serial in self.states}
        self.methods = {serial: 'normal.keyboard/.IME' for serial in self.states}
        self.installed = {serial: False for serial in self.states}
        self.calls = []
        self.fail_actions = set()
        self.sizes = {serial: 'Physical size: 200x400\n' for serial in self.states}
        self.rotations = {serial: 'SurfaceOrientation: 0\n' for serial in self.states}
        self.displays = {serial: '' for serial in self.states}

    def xml(self, serial):
        root = ElementTree.Element('hierarchy', {'rotation': '0'})
        def node(**attrs):
            return ElementTree.SubElement(root, 'node', {'package': 'fixture.chat', 'enabled': 'true', 'clickable': 'false', 'focused': 'false', **attrs})
        node(**{'class': 'android.widget.TextView', 'text': self.titles[serial], 'bounds': '[10,10][160,50]', 'resource-id': 'fixture.chat:id/title'})
        node(**{'class': 'android.widget.EditText', 'text': self.fields[serial], 'bounds': '[10,300][150,350]', 'resource-id': 'fixture.chat:id/input', 'focused': str(self.focused[serial]).lower()})
        node(**{'class': 'android.widget.Button', 'text': '发送', 'bounds': '[155,300][195,350]', 'resource-id': 'fixture.chat:id/send', 'clickable': 'true'})
        return ElementTree.tostring(root, encoding='utf-8')

    def run(self, args, **kwargs):
        args = list(map(str, args))
        self.calls.append(args)
        if args[1:] == ['devices', '-l']:
            return ('List of devices attached\n' + '\n'.join(f'{serial}\t{state} model:Test_Phone transport_id:{index}' for index, (serial, state) in enumerate(self.states.items(), 1))).encode()
        if args[1:] in (['version'], ['--version']):
            return b'test-version\n'
        if args[1] != '-s':
            raise AssertionError('Device command missing -s')
        serial, command = args[2], args[3:]
        if serial not in self.states or self.states[serial] != 'device':
            raise AssertionError('Unauthorized device command')
        if serial in self.fail_actions and command[:2] == ['shell', 'input']:
            raise RuntimeError('fixture device failed')
        if command == ['exec-out', 'screencap', '-p']:
            return png()
        if command == ['shell', 'wm', 'size']:
            return self.sizes[serial].encode()
        if command == ['shell', 'dumpsys', 'input']:
            return self.rotations[serial].encode()
        if command == ['shell', 'dumpsys', 'display']:
            return self.displays[serial].encode()
        if command[:3] == ['shell', 'uiautomator', 'dump']:
            return b'UI dumped'
        if command[:2] == ['exec-out', 'cat']:
            return self.xml(serial)
        if command[:2] == ['shell', 'rm']:
            return b''
        if command[:3] == ['shell', 'input', 'tap']:
            if int(command[3]) < 150:
                self.focused[serial] = True
            return b''
        if command[:3] == ['shell', 'input', 'text']:
            self.fields[serial] = command[3].strip("'").replace('%s', ' ')
            return b''
        if command[:3] == ['shell', 'am', 'broadcast']:
            self.fields[serial] = base64.b64decode(command[-1]).decode('utf-8')
            return b'Broadcast completed: result=0'
        if command[:3] == ['shell', 'pm', 'path']:
            return b'package:/data/app/keyboard/base.apk' if self.installed[serial] else b''
        if command[:4] == ['shell', 'settings', 'get', 'secure']:
            return self.methods[serial].encode()
        if command[:2] == ['install', '-r']:
            self.installed[serial] = True
            return b'Success'
        if command[:3] == ['shell', 'am', 'start']:
            return b'Starting settings'
        if command[:3] in (['shell', 'input', 'keyevent'], ['shell', 'input', 'swipe']):
            return b''
        raise AssertionError(f'Unexpected fixture command {command}')


class FakeProcess:
    def __init__(self):
        self.code = None
        self.terminated = False
    def poll(self):
        return self.code
    def terminate(self):
        self.terminated = True
        self.code = 0
    def wait(self, timeout=None):
        return self.code
    def kill(self):
        self.code = -9


class AndroidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.adb, self.scrcpy = root / 'adb', root / 'scrcpy'
        self.adb.touch(); self.scrcpy.touch()
        self.now = 100.0
        self.controller = AndroidController(self.adb, self.scrcpy, clock=lambda: self.now)
        self.controller.keyboard_apk = root / 'ADBKeyboard.apk'
        self.controller.keyboard_apk.touch()
        self.transport = Transport()
        self.controller._run = self.transport.run
        self.target = {'input': {'x': .35, 'y': .8}, 'send': {'x': .85, 'y': .8}, 'chat_text': '联系人'}

    def tearDown(self):
        self.controller.close()
        self.tmp.cleanup()

    def device_commands(self):
        return [args for args in self.transport.calls if args[1] == '-s']

    def send_taps(self):
        return [args for args in self.device_commands() if args[3:6] == ['shell', 'input', 'tap'] and int(args[6]) >= 150]

    def prepare(self, text='Hello friend'):
        return self.controller.prepare(['phone-A'], text, {'phone-A': self.target})

    def test_every_targeted_command_has_selected_serial_and_offline_is_refused(self):
        listed = self.controller.devices()
        self.assertEqual(len(listed), 4)
        self.assertEqual(listed[0]['model'], 'Test Phone')
        result = self.controller.batch_action(['phone-A', 'phone-B', 'phone-C', 'phone-D'], 'home')
        self.assertEqual([item['ok'] for item in result['items']], [True, False, False, True])
        self.assertIn('USB', result['items'][1]['error'])
        self.assertIn('离线', result['items'][2]['error'])
        self.assertEqual([args[2] for args in self.device_commands()], ['phone-A', 'phone-D'])
        with self.assertRaisesRegex(ValueError, '序号'):
            self.controller.screenshot('phone-A; reboot')

    def test_normalized_taps_are_bounded_and_failed_device_does_not_stop_batch(self):
        self.transport.fail_actions.add('phone-A')
        result = self.controller.batch_action(['phone-A', 'phone-D'], 'tap', {'x': .5, 'y': .5})
        self.assertEqual(result['succeeded'], 1)
        self.assertFalse(result['items'][0]['ok'])
        self.assertTrue(result['items'][1]['ok'])
        for point in ({'x': 1.1, 'y': .5}, {'x': float('nan'), 'y': .5}, {'x': True, 'y': .5}):
            with self.assertRaises(ValueError):
                self.controller.batch_action(['phone-A'], 'tap', point)
        with self.assertRaises(ValueError):
            self.controller.batch_action(['phone-A'], 'shell', {'command': 'anything'})

    def test_screenshot_is_single_png_and_keeps_no_frame_history(self):
        shot = self.controller.screenshot('phone-A')
        self.assertEqual((shot['width'], shot['height']), (200, 400))
        self.assertTrue(base64.b64decode(shot['b64']).startswith(b'\x89PNG'))
        self.assertFalse(hasattr(self.controller, '_screenshots'))

    def test_display_check_reads_selected_authorized_phones_without_frames_or_input(self):
        result = self.controller.batch_display_info(['phone-A', 'phone-B', 'phone-C', 'phone-D', 'phone-A'])
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(result['items'][0]['orientation'], 'portrait')
        self.assertEqual((result['items'][0]['width'], result['items'][0]['height']), (200, 400))
        self.assertEqual({args[2] for args in self.device_commands()}, {'phone-A', 'phone-D'})
        self.assertTrue(all(args[3:] in (['shell', 'wm', 'size'], ['shell', 'dumpsys', 'input'])
            for args in self.device_commands()))
        self.assertEqual(self.controller._reservations, {})

    def test_display_override_and_rotation_describe_current_dimensions(self):
        self.transport.sizes['phone-A'] = 'Physical size: 1440x2960\nOverride size: 1080x2220\n'
        self.transport.rotations['phone-A'] = 'SurfaceOrientation: 1\n'
        result = self.controller.display_info('phone-A')
        self.assertEqual((result['physical_width'], result['physical_height']), (1440, 2960))
        self.assertEqual((result['base_width'], result['base_height']), (1080, 2220))
        self.assertEqual((result['width'], result['height']), (2220, 1080))
        self.assertEqual((result['rotation'], result['orientation']), (90, 'landscape'))

    def test_display_fallback_only_accepts_consistent_default_display_rotation(self):
        self.transport.rotations['phone-A'] = 'No surface direction provided\n'
        self.transport.displays['phone-A'] = 'mDisplayInfo=DisplayInfo{displayId 1, rotation 3}\n' \
            'mDisplayInfo=DisplayInfo{displayId 0, rotation 2}\n'
        result = self.controller.display_info('phone-A')
        self.assertTrue(result['ok'])
        self.assertEqual(result['rotation'], 180)
        self.transport.displays['phone-A'] += 'mOverrideDisplayInfo=DisplayInfo{displayId 0, rotation 1}\n'
        unknown = self.controller.display_info('phone-A')
        self.assertFalse(unknown['ok'])
        self.assertIsNone(unknown['width'])
        self.assertIsNone(unknown['height'])
        self.assertIsNone(unknown['rotation'])
        self.assertEqual(unknown['orientation'], 'unknown')
        self.assertEqual((unknown['base_width'], unknown['base_height']), (200, 400))

    def test_display_bad_size_fails_one_phone_and_continues_batch(self):
        self.transport.sizes['phone-A'] = 'Physical size: 0x400\n'
        result = self.controller.batch_display_info(['phone-A', 'phone-D'])
        self.assertFalse(result['items'][0]['ok'])
        self.assertIn('尺寸异常', result['items'][0]['error'])
        self.assertTrue(result['items'][1]['ok'])

    def test_display_check_ignores_mutation_reservation_but_never_queues_on_device_lock(self):
        self.controller._reservations['phone-A'] = (object(), 'warmup')
        self.assertTrue(self.controller.display_info('phone-A')['ok'])
        locked, release = threading.Event(), threading.Event()
        def hold_device():
            with self.controller._device_lock('phone-A'):
                locked.set()
                release.wait(2)
        thread = threading.Thread(target=hold_device)
        thread.start()
        self.assertTrue(locked.wait(1))
        previous = len(self.transport.calls)
        try:
            with self.assertRaisesRegex(ValueError, '设备命令'):
                self.controller.display_info('phone-A')
            self.assertEqual(len(self.transport.calls), previous)
        finally:
            release.set()
            thread.join(2)

    def test_preparation_never_sends_and_explicit_send_is_one_time(self):
        prepared = self.prepare()
        self.assertEqual(prepared['succeeded'], 1)
        self.assertTrue(prepared['prepared_id'])
        self.assertEqual(self.transport.fields['phone-A'], 'Hello friend')
        self.assertEqual(self.send_taps(), [])
        self.assertIn('preview', prepared['items'][0])
        result = self.controller.send(prepared['prepared_id'])
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(len(self.send_taps()), 1)
        self.assertIn('确认实际送达', result['items'][0]['note'])
        with self.assertRaises(ValueError):
            self.controller.send(prepared['prepared_id'])

    def test_changed_chat_text_or_focus_blocks_send(self):
        for change in ('title', 'draft', 'focus'):
            self.transport.fields['phone-A'] = ''
            self.transport.focused['phone-A'] = False
            self.transport.titles['phone-A'] = '联系人'
            prepared = self.prepare()
            if change == 'title': self.transport.titles['phone-A'] = '另一个聊天'
            if change == 'draft': self.transport.fields['phone-A'] = 'changed'
            if change == 'focus': self.transport.focused['phone-A'] = False
            result = self.controller.send(prepared['prepared_id'])
            self.assertEqual(result['succeeded'], 0)
        self.assertEqual(self.send_taps(), [])

    def test_preview_ttl_blocks_send_without_any_tap(self):
        prepared = self.prepare()
        self.now += 301
        with self.assertRaisesRegex(ValueError, '过期'):
            self.controller.send(prepared['prepared_id'])
        self.assertEqual(self.send_taps(), [])

    def test_existing_draft_is_not_overwritten(self):
        self.transport.fields['phone-A'] = 'original'
        prepared = self.prepare()
        self.assertEqual(prepared['prepared_id'], '')
        self.assertIn('已有内容', prepared['items'][0]['error'])
        self.assertEqual(self.transport.fields['phone-A'], 'original')

    def test_chinese_requires_active_helper_without_fake_typing_or_send(self):
        prepared = self.prepare('你好，朋友😀')
        self.assertEqual(prepared['succeeded'], 0)
        self.assertIn('当前输入法', prepared['items'][0]['error'])
        self.assertFalse(any(args[3:6] == ['shell', 'input', 'text'] for args in self.device_commands()))
        self.assertFalse(any(args[3:6] == ['shell', 'am', 'broadcast'] for args in self.device_commands()))
        self.assertEqual(self.send_taps(), [])

    def test_chinese_emoji_broadcast_is_utf8_b64_and_still_only_prepares(self):
        self.transport.installed['phone-A'] = True
        self.transport.methods['phone-A'] = 'com.android.adbkeyboard/.AdbIME'
        text = '你好，朋友😀\n欢迎咨询'
        result = self.prepare(text)
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(self.transport.fields['phone-A'], text)
        broadcast = next(args for args in self.device_commands() if args[3:6] == ['shell', 'am', 'broadcast'])
        self.assertIn('com.android.adbkeyboard', broadcast)
        self.assertEqual(base64.b64decode(broadcast[-1]).decode('utf-8'), text)
        self.assertEqual(self.send_taps(), [])

    def test_helper_install_only_on_explicit_flag_and_never_switches_ime(self):
        first = self.controller.input_helper('phone-A')
        self.assertFalse(first['installed'])
        self.assertFalse(any(args[3] == 'install' for args in self.device_commands()))
        installed = self.controller.input_helper('phone-A', install=True, open_settings=True)
        self.assertTrue(installed['installed'])
        self.assertFalse(installed['active'])
        commands = [args[3:] for args in self.device_commands()]
        self.assertTrue(any(command[:2] == ['install', '-r'] for command in commands))
        self.assertTrue(any('android.settings.INPUT_METHOD_SETTINGS' in command for command in commands))
        self.assertFalse(any('ime' in command for command in commands))

    def test_mirror_limit_and_cleanup_touch_only_owned_processes(self):
        self.transport.states['phone-E'] = 'device'
        procs = [FakeProcess(), FakeProcess()]
        with patch('app.android_control.subprocess.Popen', side_effect=procs) as popen:
            self.controller.mirror('phone-A'); self.controller.mirror('phone-D')
            with self.assertRaisesRegex(ValueError, '2 台'):
                self.controller.mirror('phone-E')
            self.assertEqual(popen.call_count, 2)
            command = popen.call_args_list[0].args[0]
            self.assertIn('--no-audio', command)
            self.assertIn('--max-size=800', command)
            self.assertIn('--max-fps=30', command)
            self.assertEqual(command[command.index('--serial') + 1], 'phone-A')
            self.assertFalse(popen.call_args.kwargs['shell'])
            self.controller.close()
        self.assertTrue(all(proc.terminated for proc in procs))
        self.assertFalse(any('kill-server' in args for args in self.transport.calls))

    def test_browsing_counts_active_time_and_reserves_without_blocking_read(self):
        progress, waits = [], []
        owner = self
        class Control:
            def cancelled(self): return False
            def wait_active(self, seconds):
                waits.append(seconds)
                result = owner.controller.batch_action(['phone-A', 'phone-D'], 'home')
                owner.assertFalse(result['items'][0]['ok'])
                owner.assertTrue(result['items'][1]['ok'])
                owner.assertTrue(owner.controller.screenshot('phone-A')['b64'])
                return seconds
        elapsed = self.controller.browse_session('phone-A', 25, Control(), progress.append)
        self.assertEqual(elapsed, 25)
        self.assertEqual(progress, [10, 20, 25])
        self.assertEqual(waits, [10, 10, 5])
        self.assertEqual(self.controller._reservations, {})

    def test_stop_uses_scheduler_exception_and_releases_reservation(self):
        class Stopped:
            def cancelled(self): return True
            def wait_active(self, seconds): raise AssertionError('wait after stopped')
        with self.assertRaises(BrowseStopped):
            self.controller.browse_session('phone-A', 20, Stopped(), lambda value: None)
        self.assertEqual(self.controller._reservations, {})
        self.assertEqual(self.controller.batch_action(['phone-A'], 'home')['succeeded'], 1)

    def test_paused_wall_clock_is_not_added_to_active_seconds(self):
        owner, progress = self, []
        class PausedControl:
            def cancelled(self): return False
            def wait_active(self, seconds):
                # Model a pause/resume taking a minute of wall time while only
                # five seconds of actual active waiting have completed.
                owner.now += 65
                return min(5, seconds)
        elapsed = self.controller.browse_session('phone-A', 10, PausedControl(), progress.append)
        self.assertEqual(elapsed, 10)
        self.assertEqual(progress, [5, 10])
        self.assertEqual(self.now, 230)

    def test_reserved_phone_refuses_prepare_and_send_immediately(self):
        prepared = self.prepare()
        self.transport.fields['phone-A'] = ''
        with self.controller._operation('phone-A', 'warmup'):
            failed = self.prepare()
            self.assertEqual(failed['succeeded'], 0)
            self.assertIn('正在执行', failed['items'][0]['error'])
            sent = self.controller.send(prepared['prepared_id'])
            self.assertEqual(sent['succeeded'], 0)
            self.assertIn('正在执行', sent['items'][0]['error'])
        self.assertEqual(self.send_taps(), [])

    def test_device_disconnect_aborts_browse_after_reporting_elapsed(self):
        transport = self.transport
        progress = []
        class Control:
            def cancelled(self): return False
            def wait_active(self, seconds):
                transport.states['phone-A'] = 'offline'
                return seconds
        with self.assertRaisesRegex(ValueError, '离线'):
            self.controller.browse_session('phone-A', 20, Control(), progress.append)
        self.assertEqual(progress, [10])
        self.assertEqual(self.controller._reservations, {})


if __name__ == '__main__':
    unittest.main()
