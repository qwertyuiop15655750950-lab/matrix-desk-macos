"""Reversible, per-device Android HTTP proxy settings; never verifies exit IP."""
from contextlib import contextmanager
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import shlex
import sqlite3


PROXY_KEYS = ('http_proxy', 'global_http_proxy_host', 'global_http_proxy_port',
    'global_http_proxy_exclusion_list', 'global_proxy_pac_url')
USB_PROXY_PORT = 17890
EXIT_URL = 'https://api.ipify.org'


class AndroidNetwork:
    def __init__(self, controller, db_path):
        self.controller = controller
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS android_network_snapshots (
                serial TEXT PRIMARY KEY, original_json TEXT NOT NULL,
                applied_json TEXT NOT NULL DEFAULT '{}', reverse_json TEXT,
                updated_at TEXT NOT NULL)''')

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _saved(self, serial):
        with self._connection() as conn:
            row = conn.execute('SELECT * FROM android_network_snapshots WHERE serial=?', (serial,)).fetchone()
        if not row:
            return None
        return {'original': json.loads(row['original_json']),
            'applied': json.loads(row['applied_json']),
            'reverse': json.loads(row['reverse_json']) if row['reverse_json'] else None}

    def _save(self, serial, saved):
        with self._connection() as conn:
            conn.execute('''INSERT INTO android_network_snapshots
                (serial,original_json,applied_json,reverse_json,updated_at) VALUES (?,?,?,?,?)
                ON CONFLICT(serial) DO UPDATE SET original_json=excluded.original_json,
                applied_json=excluded.applied_json,reverse_json=excluded.reverse_json,
                updated_at=excluded.updated_at''', (serial,
                json.dumps(saved['original'], ensure_ascii=False),
                json.dumps(saved.get('applied', {}), ensure_ascii=False),
                json.dumps(saved['reverse']) if saved.get('reverse') else None,
                datetime.now(timezone.utc).isoformat(timespec='seconds')))

    def _forget(self, serial):
        with self._connection() as conn:
            conn.execute('DELETE FROM android_network_snapshots WHERE serial=?', (serial,))

    @staticmethod
    def _port(port):
        if isinstance(port, bool) or not isinstance(port, (str, int)) or not re.fullmatch(r'[0-9]{1,5}', str(port)):
            raise ValueError('代理端口需为 1 到 65535 的整数，请使用 HTTP 或 Mixed 端口')
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('代理端口需为 1 到 65535 的整数')
        return port

    @staticmethod
    def _host(host):
        if not isinstance(host, str):
            raise ValueError('请填写电脑的局域网地址，不含协议、账号或密码')
        host = host.strip()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise ValueError('地址不含 http://、SOCKS、账号密码或其他命令字符')
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
                raise ValueError('代理地址无效')
            if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
                raise ValueError('局域网模式不能使用手机的本机地址，请填写电脑局域网地址')
            # Numeric malformed addresses should not be interpreted as names.
            if re.fullmatch(r'[0-9.]+', host):
                raise ValueError('代理 IP 地址无效')
        else:
            mapped = getattr(address, 'ipv4_mapped', None)
            if address.is_loopback or address.is_unspecified or address.is_multicast or (mapped and (mapped.is_loopback or mapped.is_unspecified)):
                raise ValueError('局域网模式不能使用手机本机或未指定地址，请填写电脑局域网地址')
            host = str(address)
        return host

    def _read_settings(self, serial):
        # `settings get` returns "null" both for absence and the literal value;
        # list preserves this distinction so restore can delete absent keys.
        output = self.controller._adb(serial, 'shell', 'settings', 'list', 'global', max_output=2 * 1024 * 1024).decode('utf-8', 'replace')
        result = {key: None for key in PROXY_KEYS}
        for line in output.splitlines():
            key, separator, value = line.partition('=')
            if separator and key in result:
                if len(value) > 4096:
                    raise RuntimeError('手机原代理配置过长，未修改设置')
                result[key] = value
        return result

    @staticmethod
    def _public_settings(values):
        # Original PAC URLs and exclusion values stay in the local snapshot;
        # the UI needs only an address summary, never subscription-like URLs.
        host = values.get('global_http_proxy_host') or ''
        port = values.get('global_http_proxy_port') or ''
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                host = ''
        if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
            port = ''
        return {'host': host, 'port': port,
                'configured': bool(host and port and port != '0')}

    def _write_settings(self, serial, values):
        for key in PROXY_KEYS:
            value = values[key]
            if value is None:
                self.controller._adb(serial, 'shell', 'settings', 'delete', 'global', key)
            else:
                # adb shell joins argv remotely; quote values from the device as
                # well as new values. Keys and command sequence are fixed.
                self.controller._adb(serial, 'shell', 'settings', 'put', 'global', key, shlex.quote(value))
        if self._read_settings(serial) != values:
            raise RuntimeError('手机未保留完整代理设置，请检查系统权限')

    def _reverse_map(self, serial):
        output = self.controller._adb(serial, 'reverse', '--list', max_output=256 * 1024).decode('utf-8', 'replace')
        return {parts[-2]: parts[-1] for line in output.splitlines()
            if len(parts := line.split()) >= 2 and re.fullmatch(r'tcp:[0-9]+', parts[-2])}

    def _remove_owned_reverse(self, serial, reverse):
        if not reverse or not reverse.get('owned'):
            return
        current = self._reverse_map(serial).get(reverse['local'])
        if current is None:
            return
        if current != reverse['remote']:
            raise RuntimeError('USB 转发已被其他任务修改，已保留该映射；未删除其他任务的转发')
        self.controller._adb(serial, 'reverse', '--remove', reverse['local'])

    def _batch(self, serials, action):
        serials = self.controller._serials(serials)
        items = []
        for serial in serials:
            try:
                with self.controller._operation(serial, 'network'):
                    result = action(serial)
                items.append({'serial': serial, 'ok': True, 'verified': False, **result})
            except Exception as exc:
                items.append({'serial': serial, 'ok': False, 'verified': False, 'error': str(exc)[:600]})
        return {'items': items, 'succeeded': sum(item['ok'] for item in items), 'total': len(items)}

    def status(self, serials=None):
        devices = self.controller.devices()
        if serials is not None:
            wanted = self.controller._serials(serials) if serials else []
            found = {item['serial']: item for item in devices}
            devices = [found.get(serial, {'serial': serial, 'state': 'disconnected', 'model': ''}) for serial in wanted]
        items = []
        for device in devices:
            serial = device['serial']
            saved = self._saved(serial)
            item = {**device, 'saved_original': bool(saved),
                'transport': (saved or {}).get('applied', {}).get('transport', ''),
                'verified': False, 'settings': None}
            try:
                with self.controller._operation(serial, 'network'):
                    item['settings'] = self._public_settings(self._read_settings(serial))
                    if item['transport'] == 'usb':
                        expected = f"tcp:{saved['applied']['port']}"
                        item['tunnel_ready'] = self._reverse_map(serial).get(f'tcp:{USB_PROXY_PORT}') == expected
                        if not item['tunnel_ready']:
                            item['note'] = 'USB 转发已失效，可能发生了断线或重连；请重新应用代理或恢复原设置'
                item['ok'] = True
            except Exception as exc:
                item.update(ok=False, error=str(exc)[:300])
            items.append(item)
        return {'items': items, 'succeeded': sum(item['ok'] for item in items), 'total': len(items)}

    def apply_proxy(self, serials, host, port, transport='lan'):
        port = self._port(port)
        if transport not in ('lan', 'usb'):
            raise ValueError('请选择 USB 或局域网共享方式；不支持 SOCKS 系统代理')
        host = self._host(host) if transport == 'lan' else '127.0.0.1'
        return self._batch(serials, lambda serial: self._apply_one(serial, host, port, transport))

    def _apply_one(self, serial, host, port, transport):
        before = self._read_settings(serial)
        previous = self._saved(serial)
        saved = previous or {'original': before, 'applied': {}, 'reverse': None}
        reverse = {'local': f'tcp:{USB_PROXY_PORT}', 'remote': f'tcp:{port}', 'owned': True}
        current_reverse = None
        if transport == 'usb':
            current_reverse = self._reverse_map(serial).get(reverse['local'])
            if current_reverse is not None and current_reverse != reverse['remote']:
                raise ValueError(f'手机 USB 端口 {USB_PROXY_PORT} 已被其他转发占用，请先恢复或改用局域网')
        # Save baseline before any global setting mutation, retaining the first
        # snapshot across repeated applies and process restarts.
        self._save(serial, saved)
        created_reverse = False
        settings_changed = False
        try:
            if transport == 'usb' and current_reverse is None:
                self.controller._adb(serial, 'reverse', '--no-rebind', reverse['local'], reverse['remote'])
                created_reverse = True
                saved = {**saved, 'reverse': reverse}
                self._save(serial, saved)
                if self._reverse_map(serial).get(reverse['local']) != reverse['remote']:
                    raise RuntimeError('USB 转发未建立，未应用代理')
            proxy_port = USB_PROXY_PORT if transport == 'usb' else port
            proxy_address = f'[{host}]' if ':' in host else host
            wanted = {'http_proxy': f'{proxy_address}:{proxy_port}',
                'global_http_proxy_host': host, 'global_http_proxy_port': str(proxy_port),
                'global_http_proxy_exclusion_list': '', 'global_proxy_pac_url': ''}
            settings_changed = True
            self._write_settings(serial, wanted)
            saved = {**saved, 'applied': {'host': host, 'port': port, 'transport': transport}}
            self._save(serial, saved)
            return {'settings': self._public_settings(wanted), 'transport': transport,
                'configured': True, 'computer_port': port, 'note': '系统 HTTP 代理已配置；手机实际出口尚未验证'}
        except Exception as exc:
            rollback_error = None
            try:
                if settings_changed:
                    self._write_settings(serial, before)
                if created_reverse:
                    self._remove_owned_reverse(serial, reverse)
                if previous:
                    self._save(serial, previous)
                else:
                    self._forget(serial)
            except Exception as rollback:
                rollback_error = rollback
            if rollback_error:
                raise RuntimeError(f'{exc}；自动回滚未完成，请点击恢复原设置：{rollback_error}') from exc
            raise RuntimeError(f'{exc}；已回到本次操作前的设置') from exc

    def restore_proxy(self, serials):
        return self._batch(serials, self._restore_one)

    def _restore_one(self, serial):
        saved = self._saved(serial)
        if not saved:
            return {'restored': False, 'settings': self._public_settings(self._read_settings(serial)), 'note': '没有本工作台保存的原设置，未修改手机'}
        self._write_settings(serial, saved['original'])
        self._remove_owned_reverse(serial, saved.get('reverse'))
        self._forget(serial)
        return {'restored': True, 'settings': self._public_settings(saved['original']), 'note': '已恢复原设置和原有键的缺失状态'}

    def _open(self, serial, action, *, url=None):
        args = ['shell', 'am', 'start', '-a', action]
        if url:
            args += ['-c', 'android.intent.category.BROWSABLE', '-d', url]
        output = self.controller._adb(serial, *args, max_output=65536).decode('utf-8', 'replace')
        if re.search(r'Error:|Exception|unable to resolve', output, re.IGNORECASE):
            raise RuntimeError('手机无法打开该页面，请检查系统浏览器或设置应用')
        return {'opened': True, 'verified': False, **({'url': url} if url else {})}

    def open_settings(self, serials):
        return self._batch(serials, lambda serial: self._open(serial, 'android.settings.VPN_SETTINGS'))

    def check_exit(self, serials):
        return self._batch(serials, lambda serial: self._open(serial, 'android.intent.action.VIEW', url=EXIT_URL))
