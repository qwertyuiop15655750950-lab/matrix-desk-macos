"""Host network discovery stays local and never claims a proxy protocol."""
import socket
import subprocess
import unittest
from unittest.mock import patch

from app import host_network as network

PROXIES = '''<dictionary> {
  HTTPEnable : 1
  HTTPProxy : localhost
  HTTPPort : 7890
  HTTPSEnable : 1
  HTTPSProxy : 127.0.0.1
  HTTPSPort : 7890
  ProxyAutoConfigEnable : 1
  ProxyAutoConfigURLString : https://user:password-secret@private.test/proxy.pac?subscription=private-secret{
  __SCOPED__ : <dictionary> {
    en0 : <dictionary> {
      HTTPEnable : 1
      HTTPProxy : secret-remote.test
      HTTPPort : 9999
    }
  }
}
'''
INTERFACES = '''lo0: flags=8049<UP,LOOPBACK,RUNNING> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
utun0: flags=8051<UP,POINTOPOINT,RUNNING> mtu 1380
    inet 10.1.2.3 --> 10.1.2.3 netmask 0xffffffff
en0: flags=8863<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> mtu 1500
    inet6 fe80::123%en0 prefixlen 64
    inet 192.168.50.9 netmask 0xffffff00 broadcast 192.168.50.255
    status: active
en1: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 10.20.30.40 netmask 0xffffff00
    status: active
bridge0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 192.168.50.9 netmask 0xffffff00
en2: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 172.31.9.8 netmask 0xffff0000
    status: active
en3: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 172.32.9.8 netmask 0xffff0000
en4: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 169.254.1.2 netmask 0xffff0000
en5: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 100.64.1.2 netmask 0xffff0000
en6: flags=8862<BROADCAST,RUNNING> mtu 1500
    inet 192.168.99.1 netmask 0xffffff00
en7: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 10.99.1.1 netmask 0xffffff00
    status: inactive
ppp0: flags=8051<UP,POINTOPOINT,RUNNING> mtu 1500
    inet 192.168.200.1 netmask 0xffffff00
'''


class HostNetworkTests(unittest.TestCase):
    def test_parses_only_global_enabled_http_settings_without_pac_or_credentials(self):
        result = network._parse_proxies(PROXIES)
        self.assertEqual(result, {
            'system_http': {'host': 'localhost', 'port': 7890, 'enabled': True},
            'system_https': {'host': '127.0.0.1', 'port': 7890, 'enabled': True}})
        self.assertNotIn('secret', str(result))
        disabled = PROXIES.replace('HTTPEnable : 1', 'HTTPEnable : 0')
        self.assertEqual(network._parse_proxies(disabled)['system_http'],
                         {'host': '', 'port': 0, 'enabled': False})

    def test_lan_addresses_exclude_vpn_inactive_public_and_non_rfc1918(self):
        self.assertEqual(network._parse_addresses(INTERFACES),
                         ['10.20.30.40', '172.31.9.8', '192.168.50.9'])

    def test_input_normalization_never_turns_credentials_into_proxy_host(self):
        for value in ['https://user:secret@example.test/', 'user:secret@localhost',
                      'localhost/path', 'localhost?token=secret', 'bad host', None, []]:
            self.assertEqual(network._host(value), '')
        for value in [True, 0, 65536, '7890;curl private.test', 7890.0, [], None]:
            self.assertEqual(network._port(value), 0)
        self.assertEqual(network._host(' LOCALHOST. '), 'localhost')
        self.assertEqual(network._host('[::1]'), '::1')
        self.assertEqual(network._port(' 7890 '), 7890)
        malformed = PROXIES.replace('HTTPPort : 7890', 'HTTPPort : 99999')
        self.assertFalse(network._parse_proxies(malformed)['system_http']['enabled'])

    def test_no_remote_or_dns_probe_even_when_system_proxy_is_remote(self):
        proxy = PROXIES.replace('localhost', 'proxy.private.test').replace('127.0.0.1', '192.168.1.1')
        with patch('app.host_network.platform.system', return_value='Darwin'), patch(
                'app.host_network._read_command', side_effect=[proxy, INTERFACES]), patch(
                'app.host_network._listening', return_value=False) as probed:
            result = network.status()
        self.assertTrue(result['ok'])
        self.assertTrue(result['system_http']['enabled'])
        self.assertEqual(result['local_proxy_candidates'], [])
        self.assertEqual(probed.call_args_list, [((('127.0.0.1', port)),) for port in network.COMMON_PORTS])
        with patch('app.host_network.socket.socket') as opened:
            for host in ['proxy.private.test', '192.168.1.1', '8.8.8.8', 'example.test']:
                self.assertFalse(network._listening(host, 7890))
            opened.assert_not_called()

    def test_local_tcp_listener_is_unverified_and_receives_no_payload(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(('127.0.0.1', 0)); server.listen(2); server.settimeout(2)
            port = server.getsockname()[1]
            proxy = PROXIES.replace('7890', str(port))
            with patch('app.host_network.platform.system', return_value='Darwin'), patch(
                    'app.host_network._read_command', side_effect=[proxy, INTERFACES]), patch(
                    'app.host_network.COMMON_PORTS', (port,)):
                result = network.status()
            connection, _ = server.accept()
            with connection:
                connection.settimeout(2)
                self.assertEqual(connection.recv(1024), b'')
        self.assertEqual(result['local_proxy_candidates'], [{
            'host': '127.0.0.1', 'port': port, 'source': 'system_http,system_https,common_port',
            'listening': True, 'http_support': 'unverified'}])

    def test_partial_tool_failure_keeps_other_results_but_reports_not_ok(self):
        with patch('app.host_network.platform.system', return_value='Darwin'), patch(
                'app.host_network._read_command', side_effect=[RuntimeError('系统网络检测工具缺失'), INTERFACES]), patch(
                'app.host_network._listening', return_value=False):
            result = network.status()
        self.assertFalse(result['ok'])
        self.assertIn('缺失', result['error'])
        self.assertEqual(result['computer_addresses'], ['10.20.30.40', '172.31.9.8', '192.168.50.9'])
        self.assertFalse(result['system_http']['enabled'])

    def test_unsupported_system_does_not_run_commands_or_probe(self):
        with patch('app.host_network.platform.system', return_value='Linux'), patch(
                'app.host_network._read_command') as command, patch('app.host_network._listening') as probed:
            result = network.status()
        self.assertFalse(result['ok'])
        command.assert_not_called(); probed.assert_not_called()

    def test_system_commands_are_fixed_bounded_and_errors_do_not_echo_output(self):
        def output_command(command, **options):
            self.assertEqual(command, [network.SCUTIL, '--proxy'])
            self.assertFalse(options['shell'])
            self.assertEqual(options['timeout'], 3)
            self.assertIs(options['stderr'], subprocess.DEVNULL)
            options['stdout'].write(b'x' * (network.MAX_OUTPUT + 1))
            return subprocess.CompletedProcess(command, 0)
        with patch('app.host_network.subprocess.run', side_effect=output_command):
            with self.assertRaisesRegex(RuntimeError, '超过限制'):
                network._read_command((network.SCUTIL, '--proxy'))
        with patch('app.host_network.subprocess.run') as command:
            with self.assertRaises(ValueError): network._read_command(('/bin/sh', '-c', 'anything'))
            command.assert_not_called()
        with patch('app.host_network.subprocess.run', side_effect=FileNotFoundError('password-secret')):
            with self.assertRaisesRegex(RuntimeError, '工具缺失') as error:
                network._read_command((network.IFCONFIG, '-a'))
        self.assertNotIn('secret', str(error.exception))


if __name__ == '__main__': unittest.main()
