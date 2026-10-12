"""In-memory Android transport tests; no device, VPN or external traffic."""
import json
import shlex
import tempfile
import unittest
from pathlib import Path

from app.android_control import AndroidController
from app.android_network import AndroidNetwork, PROXY_KEYS, USB_PROXY_PORT, EXIT_URL


class Transport:
    def __init__(self):
        self.states = {'phone-A': 'device', 'phone-B': 'unauthorized', 'phone-C': 'device'}
        self.settings = {serial: {} for serial in self.states}
        self.reverse = {serial: {} for serial in self.states}
        self.calls = []
        self.failed_serials = set()
        self.fail_put = None
        self.fail_once = True

    def run(self, args, **kwargs):
        args = list(map(str, args)); self.calls.append(args)
        if args[1:] == ['devices', '-l']:
            return ('List of devices attached\n' + '\n'.join(f'{serial}\t{state} model:Fixture' for serial, state in self.states.items())).encode()
        assert args[1] == '-s', 'Device command must target a selected serial'
        serial, command = args[2], args[3:]
        assert self.states[serial] == 'device', 'Unauthorized device command'
        if serial in self.failed_serials:
            raise RuntimeError('fixture device failed')
        if command == ['shell', 'settings', 'list', 'global']:
            return '\n'.join(f'{key}={value}' for key, value in self.settings[serial].items()).encode()
        if command[:4] == ['shell', 'settings', 'delete', 'global']:
            self.settings[serial].pop(command[4], None); return b''
        if command[:4] == ['shell', 'settings', 'put', 'global']:
            if self.fail_put == command[4]:
                if self.fail_once: self.fail_put = None
                raise RuntimeError('fixture rejected put')
            value = shlex.split(command[5]); assert len(value) == 1
            self.settings[serial][command[4]] = value[0]; return b''
        if command == ['reverse', '--list']:
            return '\n'.join(f'UsbFfs {local} {remote}' for local, remote in self.reverse[serial].items()).encode()
        if command[:2] == ['reverse', '--no-rebind']:
            assert command[2] not in self.reverse[serial], 'Must never rebind an existing reverse port'
            self.reverse[serial][command[2]] = command[3]; return b''
        if command[:2] == ['reverse', '--remove']:
            self.reverse[serial].pop(command[2], None); return b''
        if command[:3] == ['shell', 'am', 'start']:
            return b'Starting: Intent fixture'
        raise AssertionError(f'Unexpected command {command}')

    def mutations(self):
        return [call for call in self.calls if call[1:2] == ['-s'] and
                (call[3:7] in (['shell', 'settings', 'put', 'global'], ['shell', 'settings', 'delete', 'global']) or
                 call[3:5] in (['reverse', '--no-rebind'], ['reverse', '--remove']))]


class AndroidNetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / 'network.db'
        self.controller = AndroidController(adb_path=Path(self.directory.name) / 'adb', scrcpy_path=Path(self.directory.name) / 'scrcpy')
        self.transport = Transport(); self.controller._run = self.transport.run
        self.network = AndroidNetwork(self.controller, self.db_path)

    def test_status_and_exit_settings_never_claim_a_foreign_exit(self):
        self.transport.settings['phone-A'] = {'global_http_proxy_host': '127.0.0.1',
            'global_http_proxy_port': '17890', 'global_proxy_pac_url': 'https://secret.test/sub?key=credential'}
        result = self.network.status(['phone-A', 'phone-B', 'missing'])
        self.assertEqual(result['total'], 3); self.assertEqual(result['succeeded'], 1)
        self.assertNotIn('credential', json.dumps(result)); self.assertNotIn('global_proxy_pac_url', json.dumps(result))
        unsafe_summary = self.network._public_settings({'global_http_proxy_host': 'user:secret', 'global_http_proxy_port': '1082'})
        self.assertEqual(unsafe_summary['host'], '')
        self.assertNotIn('secret', json.dumps(unsafe_summary))
        self.assertTrue(all(item['verified'] is False for item in result['items']))
        self.assertEqual(self.transport.mutations(), [])
        for method in (self.network.open_settings, self.network.check_exit):
            opened = method(['phone-A'])['items'][0]
            self.assertTrue(opened['opened']); self.assertFalse(opened['verified'])
        commands = [call[3:] for call in self.transport.calls if call[3:6] == ['shell', 'am', 'start']]
        self.assertEqual(commands[0], ['shell', 'am', 'start', '-a', 'android.settings.VPN_SETTINGS'])
        self.assertEqual(commands[1][-2:], ['-d', EXIT_URL])
        self.assertEqual(self.transport.mutations(), [])

    def test_lan_apply_and_restore_preserve_absence_literal_null_and_quoted_values_across_restart(self):
        original = {'http_proxy': 'null', 'global_http_proxy_host': 'old.proxy',
            'global_http_proxy_exclusion_list': "a,b;$(x)'quoted", 'global_proxy_pac_url': 'https://private.test/path?secret=keep'}
        self.transport.settings['phone-A'] = dict(original)
        result = self.network.apply_proxy(['phone-A'], '192.168.2.115', '1082', 'lan')
        self.assertEqual(result['succeeded'], 1); self.assertTrue(result['items'][0]['configured'])
        self.assertFalse(result['items'][0]['verified'])
        self.assertEqual(self.transport.settings['phone-A']['http_proxy'], '192.168.2.115:1082')
        self.assertNotIn('secret', json.dumps(result))
        second = AndroidNetwork(self.controller, self.db_path)
        second.apply_proxy(['phone-A'], '192.168.2.116', 8080, 'lan')
        self.assertEqual(second._saved('phone-A')['original'], {key: original.get(key) for key in PROXY_KEYS})
        restored = second.restore_proxy(['phone-A'])['items'][0]
        self.assertTrue(restored['restored']); self.assertEqual(self.transport.settings['phone-A'], original)
        self.assertIsNone(second._saved('phone-A'))
        self.assertNotIn('secret', json.dumps(restored))
        self.assertNotIn('global_http_proxy_port', self.transport.settings['phone-A'])

    def test_usb_maps_only_owned_port_and_keeps_unrelated_mapping(self):
        self.transport.reverse['phone-A'] = {'tcp:9000': 'tcp:9001'}
        applied = self.network.apply_proxy(['phone-A'], '127.0.0.1', 1082, 'usb')['items'][0]
        self.assertTrue(applied['ok']); self.assertEqual(applied['computer_port'], 1082)
        self.assertEqual(self.transport.settings['phone-A']['http_proxy'], f'127.0.0.1:{USB_PROXY_PORT}')
        self.assertEqual(self.transport.reverse['phone-A'], {'tcp:9000': 'tcp:9001', f'tcp:{USB_PROXY_PORT}': 'tcp:1082'})
        self.network.restore_proxy(['phone-A'])
        self.assertEqual(self.transport.reverse['phone-A'], {'tcp:9000': 'tcp:9001'})
        self.assertEqual(self.transport.settings['phone-A'], {})
        self.assertFalse(any('--remove-all' in call for call in self.transport.calls))

    def test_usb_reconnect_reports_lost_tunnel_without_silently_reapplying(self):
        self.network.apply_proxy(['phone-A'], '', 1082, 'usb')
        self.assertTrue(self.network.status(['phone-A'])['items'][0]['tunnel_ready'])
        self.transport.reverse['phone-A'].clear()
        mutations = len(self.transport.mutations())
        status = self.network.status(['phone-A'])['items'][0]
        self.assertFalse(status['tunnel_ready'])
        self.assertFalse(status['verified'])
        self.assertIn('转发已失效', status['note'])
        self.assertEqual(len(self.transport.mutations()), mutations)

    def test_changed_tunnel_destination_is_reported_without_overwriting_it(self):
        self.network.apply_proxy(['phone-A'], '', 1082, 'usb')
        self.transport.reverse['phone-A'][f'tcp:{USB_PROXY_PORT}'] = 'tcp:9000'
        status = self.network.status(['phone-A'])['items'][0]
        self.assertFalse(status['tunnel_ready'])
        self.assertEqual(self.transport.reverse['phone-A'][f'tcp:{USB_PROXY_PORT}'], 'tcp:9000')

    def test_identical_preexisting_usb_mapping_is_never_claimed_or_removed(self):
        mapping = {f'tcp:{USB_PROXY_PORT}': 'tcp:1082'}
        self.transport.reverse['phone-A'] = dict(mapping)
        self.network.apply_proxy(['phone-A'], '', 1082, 'usb')
        self.assertIsNone(self.network._saved('phone-A')['reverse'])
        self.network.restore_proxy(['phone-A'])
        self.assertEqual(self.transport.reverse['phone-A'], mapping)
        self.assertFalse(any(call[3:5] == ['reverse', '--remove'] for call in self.transport.calls))

    def test_conflicting_usb_mapping_blocks_mutation_and_changed_owned_map_is_preserved(self):
        self.transport.reverse['phone-A'][f'tcp:{USB_PROXY_PORT}'] = 'tcp:9999'
        conflict = self.network.apply_proxy(['phone-A'], '', 1082, 'usb')['items'][0]
        self.assertFalse(conflict['ok']); self.assertIn('占用', conflict['error'])
        self.assertEqual(self.transport.mutations(), []); self.assertIsNone(self.network._saved('phone-A'))
        self.transport.reverse['phone-A'].clear()
        self.network.apply_proxy(['phone-A'], '', 1082, 'usb')
        self.transport.reverse['phone-A'][f'tcp:{USB_PROXY_PORT}'] = 'tcp:9999'
        result = self.network.restore_proxy(['phone-A'])['items'][0]
        self.assertFalse(result['ok']); self.assertIn('其他任务', result['error'])
        self.assertEqual(self.transport.reverse['phone-A'][f'tcp:{USB_PROXY_PORT}'], 'tcp:9999')
        self.assertIsNotNone(self.network._saved('phone-A'))

    def test_partial_setting_failure_rolls_back_and_removes_created_reverse(self):
        self.transport.settings['phone-A'] = {'http_proxy': 'old.proxy:9000'}
        self.transport.fail_put = 'global_http_proxy_host'
        failed = self.network.apply_proxy(['phone-A'], '', 1082, 'usb')['items'][0]
        self.assertFalse(failed['ok']); self.assertIn('回到本次操作前', failed['error'])
        self.assertEqual(self.transport.settings['phone-A'], {'http_proxy': 'old.proxy:9000'})
        self.assertEqual(self.transport.reverse['phone-A'], {}); self.assertIsNone(self.network._saved('phone-A'))

    def test_failed_rollback_keeps_persistent_recovery_snapshot(self):
        self.transport.settings['phone-A'] = {'http_proxy': 'original:9000', 'global_http_proxy_host': 'original'}
        self.transport.fail_put = 'global_http_proxy_host'; self.transport.fail_once = False
        result = self.network.apply_proxy(['phone-A'], '', 1082, 'usb')['items'][0]
        self.assertFalse(result['ok']); self.assertIn('回滚未完成', result['error'])
        snapshot = AndroidNetwork(self.controller, self.db_path)._saved('phone-A')
        self.assertEqual(snapshot['original']['http_proxy'], 'original:9000')
        self.assertTrue(snapshot['reverse']['owned'])
        self.transport.fail_put = None
        restored = self.network.restore_proxy(['phone-A'])['items'][0]
        self.assertTrue(restored['ok']); self.assertEqual(self.transport.settings['phone-A'], {'http_proxy': 'original:9000', 'global_http_proxy_host': 'original'})
        self.assertEqual(self.transport.reverse['phone-A'], {})

    def test_single_device_failure_and_unauthorized_device_do_not_block_selected_authorized_device(self):
        self.transport.failed_serials.add('phone-A')
        result = self.network.apply_proxy(['phone-A', 'phone-B', 'phone-C'], '192.168.2.115', 1082)
        self.assertEqual(result['succeeded'], 1); self.assertEqual(result['total'], 3)
        self.assertEqual([item['ok'] for item in result['items']], [False, False, True])
        commands = [call for call in self.transport.calls if call[1:2] == ['-s']]
        self.assertFalse(any(call[2] == 'phone-B' for call in commands))
        self.assertTrue(all(call[2] in ('phone-A', 'phone-C') for call in commands))
        self.assertEqual(self.transport.settings['phone-C']['http_proxy'], '192.168.2.115:1082')

    def test_busy_device_is_rejected_without_queued_mutation(self):
        with self.controller._operation('phone-A', 'browse'):
            result = self.network.apply_proxy(['phone-A', 'phone-C'], '192.168.2.115', 1082)
        self.assertFalse(result['items'][0]['ok']); self.assertTrue(result['items'][1]['ok'])
        self.assertFalse(any(call[1:3] == ['-s', 'phone-A'] for call in self.transport.calls))

    def test_invalid_hosts_ports_and_serials_are_rejected_before_device_command(self):
        for host in ['127.0.0.1', 'localhost', '0.0.0.0', '::1', '::', '::ffff:127.0.0.1', 'https://host', 'user:secret@host', 'host;touch /tmp/x', '999.1.2.3']:
            with self.assertRaises(ValueError): self.network.apply_proxy(['phone-A'], host, 1082)
        for port in [False, 0, 65536, '1082;pwd', 1082.1, [], None]:
            with self.assertRaises(ValueError): self.network.apply_proxy(['phone-A'], '192.168.2.115', port)
        for serials in [[], 'phone-A', ['phone-A;echo x'], [''], [None]]:
            with self.assertRaises(ValueError): self.network.apply_proxy(serials, '192.168.2.115', 1082)
        with self.assertRaises(ValueError): self.network.apply_proxy(['phone-A'], '192.168.2.115', 1082, 'socks')
        self.assertEqual(self.transport.calls, [])

    def test_empty_device_status_and_restore_without_snapshot_are_read_only(self):
        self.transport.states = {}
        self.assertEqual(self.network.status()['items'], [])
        self.transport.states = {'phone-A': 'device'}
        result = self.network.restore_proxy(['phone-A'])['items'][0]
        self.assertFalse(result['restored']); self.assertEqual(self.transport.mutations(), [])


if __name__ == '__main__': unittest.main()
