"""Read-only macOS LAN/system-proxy discovery, without external traffic.

A listening local port is only a candidate: no HTTP request or CONNECT is sent,
so the result does not claim HTTP proxy support or working internet access.
"""
import ipaddress
import platform
import re
import socket
import subprocess
import tempfile

SCUTIL = '/usr/sbin/scutil'
IFCONFIG = '/sbin/ifconfig'
MAX_OUTPUT = 64 * 1024
COMMON_PORTS = (7890, 7897, 8080, 6152)
LAN_NETWORKS = tuple(ipaddress.ip_network(value) for value in
                     ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def _read_command(command):
    # Callers cannot supply a shell, arbitrary executable, or extra arguments.
    if tuple(command) not in {(SCUTIL, '--proxy'), (IFCONFIG, '-a')}:
        raise ValueError('不支持此系统检测命令')
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(list(command), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.DEVNULL, shell=False, timeout=3,
                env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LC_ALL': 'C'})
            if result.returncode:
                raise RuntimeError('系统网络检测命令失败')
            if output.tell() > MAX_OUTPUT:
                raise RuntimeError('系统网络检测输出超过限制')
            output.seek(0)
            return output.read(MAX_OUTPUT).decode('utf-8', 'replace')
    except FileNotFoundError:
        raise RuntimeError('系统网络检测工具缺失') from None
    except subprocess.TimeoutExpired:
        raise RuntimeError('系统网络检测超时') from None
    except OSError:
        raise RuntimeError('无法读取系统网络信息') from None


def _host(value):
    if not isinstance(value, str): return ''
    value = value.strip().lower()
    if not value or len(value) > 253: return ''
    if value.startswith('[') and value.endswith(']'): value = value[1:-1]
    try: return str(ipaddress.ip_address(value))
    except ValueError: pass
    # A system hostname is displayed, never resolved or connected remotely.
    value = value.rstrip('.')
    if all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
           for label in value.split('.')):
        return value
    return ''


def _port(value):
    if isinstance(value, bool): return 0
    if not isinstance(value, (str, int)) or not re.fullmatch(r'\d{1,5}', str(value).strip()): return 0
    value = int(value)
    return value if 1 <= value <= 65535 else 0


def _parse_proxies(output):
    values = {}
    # Scoped interface dictionaries are not global system proxy settings.
    depth = 0
    for line in output.splitlines():
        stripped = line.strip()
        if depth == 1:
            match = re.fullmatch(r'(HTTPEnable|HTTPProxy|HTTPPort|HTTPSEnable|HTTPSProxy|HTTPSPort)\s*:\s*(.*?)', stripped)
            if match: values[match.group(1)] = match.group(2)
        if stripped == '}': depth = max(0, depth - 1)
        elif re.search(r'<(?:dictionary|array)>\s+\{$', stripped): depth += 1
    proxies = {}
    for prefix, key in [('HTTP', 'system_http'), ('HTTPS', 'system_https')]:
        host, port = _host(values.get(prefix + 'Proxy')), _port(values.get(prefix + 'Port'))
        enabled = values.get(prefix + 'Enable') == '1' and bool(host and port)
        proxies[key] = {'host': host if enabled else '', 'port': port if enabled else 0,
                        'enabled': enabled}
    return proxies


def _parse_addresses(output):
    addresses = set()
    interface, usable, current = '', False, set()
    def finish():
        if usable: addresses.update(current)
    for line in output.splitlines():
        header = re.match(r'^([A-Za-z0-9_.:-]+):\s+flags=\d+<([^>]*)>', line)
        if header:
            finish()
            interface, flags = header.groups()
            usable = ('UP' in flags.split(',') and not interface.startswith(
                ('utun', 'tun', 'tap', 'ppp', 'ipsec', 'lo', 'wg', 'gif', 'stf')))
            current = set()
            continue
        if line.strip() == 'status: inactive': usable = False
        if not usable or not interface:
            continue
        match = re.match(r'^\s+inet\s+(\d+\.\d+\.\d+\.\d+)\b', line)
        if not match: continue
        try: address = ipaddress.ip_address(match.group(1))
        except ValueError: continue
        if any(address in network for network in LAN_NETWORKS): current.add(str(address))
    finish()
    return sorted(addresses, key=ipaddress.ip_address)


def _loopback(host):
    host = _host(host)
    if host == 'localhost' or host == '0.0.0.0': return '127.0.0.1'
    if host == '::': return '::1'
    try:
        address = ipaddress.ip_address(host)
        return str(address) if address.is_loopback else None
    except ValueError: return None


def _listening(host, port):
    # Never resolve hostnames or connect to LAN/WAN addresses. No payload sent.
    host, port = _loopback(host), _port(port)
    if not host or not port: return False
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.15)
            return probe.connect_ex((host, port)) == 0
    except OSError:
        return False


def status():
    result = {'ok': True, 'computer_addresses': [],
              'system_http': {'host': '', 'port': 0, 'enabled': False},
              'system_https': {'host': '', 'port': 0, 'enabled': False},
              'local_proxy_candidates': []}
    if platform.system() != 'Darwin':
        return {**result, 'ok': False, 'error': '当前只支持读取 macOS 系统网络设置'}
    errors = []
    try: result.update(_parse_proxies(_read_command((SCUTIL, '--proxy'))))
    except (RuntimeError, ValueError) as error: errors.append('系统代理：' + str(error))
    try: result['computer_addresses'] = _parse_addresses(_read_command((IFCONFIG, '-a')))
    except (RuntimeError, ValueError) as error: errors.append('局域网地址：' + str(error))

    candidates = {}
    for key in ('system_http', 'system_https'):
        proxy = result[key]
        host = _loopback(proxy['host']) if proxy['enabled'] else None
        if host:
            candidates.setdefault((host, proxy['port']), []).append(key)
    for port in COMMON_PORTS:
        candidates.setdefault(('127.0.0.1', port), []).append('common_port')
    for (host, port), sources in candidates.items():
        if _listening(host, port):
            result['local_proxy_candidates'].append({'host': host, 'port': port,
                'source': ','.join(sources), 'listening': True, 'http_support': 'unverified'})
    if errors: result.update(ok=False, error='；'.join(errors))
    return result
