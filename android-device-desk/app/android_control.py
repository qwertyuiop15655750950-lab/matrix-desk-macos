"""On-demand Android control through adb and at most two scrcpy windows."""
import base64
from contextlib import contextmanager
import io
import math
import os
from pathlib import Path
import re
import shlex
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from xml.etree import ElementTree

APP_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = APP_DIR.parent / 'dev-tools'
SERIAL_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
BOUNDS_PATTERN = re.compile(r'^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$')
ASCII_PATTERN = re.compile(r'^[A-Za-z0-9 .,!?@:_/()\-]+$')


class AndroidController:
    def __init__(self, adb_path=None, scrcpy_path=None, *, clock=time.monotonic):
        self.adb = Path(adb_path or os.environ.get('MATRIX_ADB_PATH') or
            TOOLS_DIR / 'platform-tools' / ('adb.exe' if os.name == 'nt' else 'adb'))
        if scrcpy_path or os.environ.get('MATRIX_SCRCPY_PATH'):
            self.scrcpy = Path(scrcpy_path or os.environ['MATRIX_SCRCPY_PATH'])
        else:
            candidates = sorted(TOOLS_DIR.glob('scrcpy-*/scrcpy'))
            self.scrcpy = candidates[-1] if candidates else TOOLS_DIR / 'scrcpy' / 'scrcpy'
        self.clock = clock
        self._lock = threading.RLock()
        self._device_locks = {}
        self._mirrors = {}
        self._prepared = {}
        self._reservations = {}
        self.keyboard_apk = TOOLS_DIR / 'adbkeyboard' / 'ADBKeyboard.apk'

    @staticmethod
    def _serial(serial):
        if not isinstance(serial, str) or not SERIAL_PATTERN.fullmatch(serial):
            raise ValueError('设备序号无效，请从已检测设备中选择')
        return serial

    @staticmethod
    def _point(value):
        if not isinstance(value, dict):
            raise ValueError('请先在截图上标记位置')
        coords = []
        for key in ('x', 'y'):
            number = value.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError('点击坐标必须在 0 到 1 之间')
            coords.append(float(number))
        return tuple(coords)

    def _device_lock(self, serial):
        with self._lock:
            return self._device_locks.setdefault(serial, threading.RLock())

    @contextmanager
    def _operation(self, serial, kind):
        """Reject occupied devices now, rather than queuing a delayed action."""
        serial = self._serial(serial)
        token = object()
        with self._lock:
            if serial in self._reservations:
                raise ValueError('此手机正在执行任务，请先停止原任务')
            self._reservations[serial] = (token, kind)
        lock = self._device_lock(serial)
        acquired = lock.acquire(blocking=False)
        try:
            if not acquired:
                raise ValueError('此手机正在获取截图或操作，请稍后重试')
            yield
        finally:
            if acquired:
                lock.release()
            with self._lock:
                if self._reservations.get(serial, (None,))[0] is token:
                    self._reservations.pop(serial, None)

    def _run(self, args, timeout=20, max_output=20 * 1024 * 1024):
        # Capture images to a temporary file so no video stream or unbounded
        # in-memory output accumulates while the device is transferring a frame.
        try:
            with tempfile.TemporaryFile() as output:
                proc = subprocess.run([str(part) for part in args], shell=False,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE,
                    timeout=timeout, env={**os.environ, 'ADB': str(self.adb)})
                if proc.returncode:
                    raise RuntimeError('设备命令失败，请检查手机授权、连接与操作权限')
                size = output.tell()
                if size > max_output:
                    raise RuntimeError('设备返回数据超过限制')
                output.seek(0)
                return output.read()
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('设备操作超时，请检查连接后重试') from exc
        except OSError as exc:
            raise RuntimeError('Android 工具无法启动，请检查安装路径') from exc

    def status(self):
        versions = {}
        errors = []
        for key, path, flag in [('adb', self.adb, 'version'), ('scrcpy', self.scrcpy, '--version')]:
            if not path.is_file():
                errors.append(f'{key} 未安装')
                continue
            try:
                output = self._run([path, flag], timeout=10, max_output=65536).decode('utf-8', 'replace')
                lines = output.splitlines()
                versions[key] = next((line for line in lines if line.startswith('Version ')), lines[0] if lines else '')
            except Exception as exc:
                errors.append(f'{key}: {exc}')
        return {'adb_available': 'adb' in versions, 'scrcpy_available': 'scrcpy' in versions,
            'versions': versions, 'error': '；'.join(errors), 'max_mirrors': 2,
            'draft_input': 'ascii-or-adbkeyboard', 'keyboard_helper_available': self.keyboard_apk.is_file(),
            'draft_input_note': '中文和 emoji 需安装中文输入助手并在手机上选择为当前输入法；系统不会自动切换输入法。'}

    def devices(self):
        # Global discovery has no target yet. Every subsequent device command
        # contains an explicit -s serial and rechecks this discovery result.
        output = self._run([self.adb, 'devices', '-l'], max_output=256 * 1024).decode('utf-8', 'replace')
        with self._lock:
            self._mirrors = {key: proc for key, proc in self._mirrors.items() if proc.poll() is None}
            mirrored = set(self._mirrors)
            reserved = {key: value[1] for key, value in self._reservations.items()}
        items = []
        for line in output.splitlines():
            parts = line.strip().split()
            if len(parts) < 2 or parts[0] == 'List' or not SERIAL_PATTERN.fullmatch(parts[0]):
                continue
            fields = dict(part.split(':', 1) for part in parts[2:] if ':' in part)
            items.append({'serial': parts[0], 'state': parts[1],
                'model': fields.get('model', '').replace('_', ' '),
                'transport_id': fields.get('transport_id', ''), 'mirroring': parts[0] in mirrored,
                'busy': parts[0] in reserved, 'task': reserved.get(parts[0], '')})
        return items

    def _online(self, serial):
        serial = self._serial(serial)
        device = next((item for item in self.devices() if item['serial'] == serial), None)
        if device is None:
            raise ValueError('设备未连接，请刷新设备列表')
        if device['state'] == 'unauthorized':
            raise ValueError('请在手机上允许此电脑进行 USB 调试')
        if device['state'] != 'device':
            raise ValueError('设备离线或尚未就绪，无法操作')
        return device

    def _adb(self, serial, *args, **kwargs):
        self._online(serial)
        return self._run([self.adb, '-s', serial, *args], **kwargs)

    @staticmethod
    def _display_rotation(output, *, default_display=False):
        if not default_display:
            values = re.findall(r'\bSurfaceOrientation\s*:\s*([0-3])\b', output)
        else:
            # Only the built-in/default display is relevant to phone input.
            # Do not borrow a connected secondary display's rotation.
            lines = [line for line in output.splitlines() if
                re.search(r'\bdisplayId\s*(?:=\s*)?0\b|\bmDisplayId\s*=\s*0\b|\bmDefaultDisplayInfo\s*=', line)]
            values = [value for line in lines for value in
                re.findall(r'\brotation\s*(?:=\s*)?([0-3])\b', line)]
        unique = set(values)
        return int(next(iter(unique))) if len(unique) == 1 else None

    def display_info(self, serial):
        """Read dimensions and direction without opening a video or a frame."""
        serial = self._serial(serial)
        lock = self._device_lock(serial)
        if not lock.acquire(blocking=False):
            raise ValueError('此手机正在执行设备命令，请稍后重试尺寸检查')
        try:
            output = self._adb(serial, 'shell', 'wm', 'size', timeout=5,
                max_output=16384).decode('utf-8', 'replace')
            physical = re.search(r'^\s*Physical size:\s*(\d+)x(\d+)\s*$', output, re.M)
            override = re.search(r'^\s*Override size:\s*(\d+)x(\d+)\s*$', output, re.M)
            size = override or physical
            if size is None:
                raise RuntimeError('无法读取手机屏幕尺寸，请逐机核对点击位置')
            base_width, base_height = map(int, size.groups())
            if not 0 < base_width <= 8000 or not 0 < base_height <= 8000:
                raise RuntimeError('手机返回的屏幕尺寸异常')
            physical_width, physical_height = map(int, physical.groups()) if physical else (None, None)
            rotation = None
            try:
                input_info = self._adb(serial, 'shell', 'dumpsys', 'input', timeout=5,
                    max_output=256 * 1024).decode('utf-8', 'replace')
                rotation = self._display_rotation(input_info)
            except RuntimeError:
                pass
            if rotation is None:
                try:
                    display = self._adb(serial, 'shell', 'dumpsys', 'display', timeout=5,
                        max_output=256 * 1024).decode('utf-8', 'replace')
                    rotation = self._display_rotation(display, default_display=True)
                except RuntimeError:
                    pass
            width, height = (None, None) if rotation is None else (
                (base_height, base_width) if rotation % 2 else (base_width, base_height))
            orientation = ('unknown' if rotation is None else 'portrait' if width < height else
                'landscape' if width > height else 'square')
            return {'serial': serial, 'ok': rotation is not None, 'width': width, 'height': height,
                'base_width': base_width, 'base_height': base_height,
                'physical_width': physical_width, 'physical_height': physical_height,
                'rotation': rotation * 90 if rotation is not None else None,
                'orientation': orientation,
                'error': '' if rotation is not None else '已读取基础尺寸，无法确认当前横竖屏，请逐机核对点击位置'}
        finally:
            lock.release()

    def batch_display_info(self, serials):
        items = []
        for serial in self._serials(serials):
            try:
                items.append(self.display_info(serial))
            except Exception as exc:
                items.append({'serial': serial, 'ok': False, 'width': None, 'height': None,
                    'rotation': None, 'orientation': 'unknown', 'error': str(exc)[:300]})
        return {'items': items, 'succeeded': sum(item['ok'] for item in items), 'total': len(items)}

    @staticmethod
    def _png_size(raw):
        if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
            raise RuntimeError('手机未返回有效截图，可能受到应用保护限制')
        width, height = struct.unpack('>II', raw[16:24])
        if not 0 < width <= 8000 or not 0 < height <= 8000 or width * height > 24_000_000:
            raise RuntimeError('手机截图尺寸异常')
        return width, height

    def _capture(self, serial):
        raw = self._adb(serial, 'exec-out', 'screencap', '-p', timeout=20)
        return raw, self._png_size(raw)

    def screenshot(self, serial, *, max_size=900):
        serial = self._serial(serial)
        with self._device_lock(serial):
            raw, (width, height) = self._capture(serial)
            # Return a single downscaled PNG. Keep no frame history in memory.
            try:
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as image:
                    image.thumbnail((max_size, max_size))
                    output = io.BytesIO()
                    image.save(output, format='PNG', optimize=True)
                    raw = output.getvalue()
                    preview_width, preview_height = image.size
            except ImportError:
                preview_width, preview_height = width, height
            return {'b64': base64.b64encode(raw).decode('ascii'),
                'width': preview_width, 'height': preview_height,
                'device_width': width, 'device_height': height}

    def mirror(self, serial):
        serial = self._serial(serial)
        self._online(serial)
        if not self.scrcpy.is_file():
            raise RuntimeError('scrcpy 未安装')
        with self._lock:
            self._mirrors = {key: proc for key, proc in self._mirrors.items() if proc.poll() is None}
            if serial in self._mirrors:
                return {'serial': serial, 'started': False, 'already_open': True}
            if len(self._mirrors) >= 2:
                raise ValueError('最多同时投屏 2 台手机，请先关闭一个投屏窗口')
            try:
                proc = subprocess.Popen([str(self.scrcpy), '--serial', serial,
                    '--no-audio', '--max-fps=30', '--max-size=800', '--video-bit-rate=2M',
                    '--no-clipboard-autosync', '--window-title', '矩阵手机 ' + serial],
                    shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, cwd=str(self.scrcpy.parent),
                    env={**os.environ, 'ADB': str(self.adb)})
            except OSError as exc:
                raise RuntimeError('投屏工具启动失败') from exc
            if proc.poll() is not None:
                raise RuntimeError('手机投屏启动失败，请检查设备支持与权限')
            self._mirrors[serial] = proc
            return {'serial': serial, 'started': True, 'already_open': False}

    def close_mirror(self, serial):
        serial = self._serial(serial)
        with self._lock:
            proc = self._mirrors.pop(serial, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        return {'serial': serial, 'closed': proc is not None}

    def close(self):
        with self._lock:
            serials = list(self._mirrors)
            self._prepared.clear()
        for serial in serials:
            self.close_mirror(serial)

    def _serials(self, serials):
        if not isinstance(serials, list) or not 1 <= len(serials) <= 50:
            raise ValueError('请选择 1 到 50 台手机')
        return list(dict.fromkeys(self._serial(value) for value in serials))

    def _action(self, serial, action, params):
        if action in ('back', 'home'):
            self._adb(serial, 'shell', 'input', 'keyevent', '4' if action == 'back' else '3')
            return
        _, (width, height) = self._capture(serial)
        if action == 'tap':
            x, y = self._point(params)
            self._adb(serial, 'shell', 'input', 'tap', str(round(x * (width - 1))), str(round(y * (height - 1))))
        elif action == 'swipe':
            directions = {'up': (.5, .75, .5, .35), 'down': (.5, .35, .5, .75),
                'left': (.75, .5, .25, .5), 'right': (.25, .5, .75, .5)}
            points = directions[params.get('direction', 'up')]
            duration = params.get('duration', 500)
            self._adb(serial, 'shell', 'input', 'swipe',
                *[str(round(value * (width - 1 if index % 2 == 0 else height - 1))) for index, value in enumerate(points)], str(duration))

    def batch_action(self, serials, action, params=None):
        serials = self._serials(serials)
        params = {} if params is None else params
        if action not in ('tap', 'swipe', 'back', 'home') or not isinstance(params, dict):
            raise ValueError('仅支持点击、滑动、返回和主页')
        if action == 'tap':
            self._point(params)
        if action == 'swipe':
            duration = params.get('duration', 500)
            if (params.get('direction', 'up') not in ('up', 'down', 'left', 'right')
                    or isinstance(duration, bool) or not isinstance(duration, int) or not 100 <= duration <= 2000):
                raise ValueError('滑动方向无效，时长需为 100 到 2000 毫秒')
        items = []
        for serial in serials:
            try:
                with self._operation(serial, 'action'):
                    self._action(serial, action, params)
                items.append({'serial': serial, 'ok': True, 'error': ''})
            except Exception as exc:
                items.append({'serial': serial, 'ok': False, 'error': str(exc)[:300]})
        return {'items': items, 'succeeded': sum(item['ok'] for item in items), 'total': len(items)}

    def browse_session(self, serial, seconds, control, progress):
        """Stay on the user's current content page, with occasional upward swipes."""
        serial = self._serial(serial)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= 7200:
            raise ValueError('单次手机浏览时长需为 1 到 7200 秒')
        from .browse_control import BrowseStopped
        token = object()
        with self._lock:
            if serial in self._reservations:
                raise ValueError('此手机正在执行任务，请稍后重试')
            self._reservations[serial] = (token, 'warmup')
        try:
            self._online(serial)
            elapsed = 0.0
            swipes = 0
            while elapsed < seconds:
                if control.cancelled():
                    raise BrowseStopped('手机浏览任务已停止')
                waited = control.wait_active(min(10.0, seconds - elapsed))
                if not isinstance(waited, (int, float)) or not math.isfinite(waited) or waited <= 0:
                    raise RuntimeError('手机浏览任务未继续计时')
                elapsed = min(float(seconds), elapsed + waited)
                progress(elapsed)
                self._online(serial)
                if elapsed < seconds and swipes < 120:
                    with self._device_lock(serial):
                        if control.cancelled():
                            raise BrowseStopped('手机浏览任务已停止')
                        self._action(serial, 'swipe', {'direction': 'up', 'duration': 500})
                    swipes += 1
            return elapsed
        finally:
            with self._lock:
                if self._reservations.get(serial, (None,))[0] is token:
                    self._reservations.pop(serial, None)

    def _helper_status(self, serial):
        installed = bool(self._adb(serial, 'shell', 'pm', 'path', 'com.android.adbkeyboard', max_output=65536).strip())
        current = self._adb(serial, 'shell', 'settings', 'get', 'secure', 'default_input_method', max_output=65536).decode('utf-8', 'replace').strip()
        active = installed and current in ('com.android.adbkeyboard/.AdbIME', 'com.android.adbkeyboard/com.android.adbkeyboard.AdbIME')
        return {'serial': serial, 'installed': installed, 'active': active,
            'helper_available': self.keyboard_apk.is_file(), 'input_method': current,
            'note': '请在手机输入法设置中人工启用并选择 ADB Keyboard；操作完成后可切回常用输入法。'}

    def input_helper(self, serial, install=False, open_settings=False):
        serial = self._serial(serial)
        if not isinstance(install, bool) or not isinstance(open_settings, bool):
            raise ValueError('输入助手操作参数无效')
        with self._operation(serial, 'input-helper'):
            self._online(serial)
            if install:
                if not self.keyboard_apk.is_file():
                    raise ValueError('中文输入助手 APK 尚未下载')
                output = self._adb(serial, 'install', '-r', str(self.keyboard_apk), timeout=120, max_output=65536)
                if b'Success' not in output:
                    raise RuntimeError('输入助手安装未成功，请在手机检查安装提示')
            if open_settings:
                self._adb(serial, 'shell', 'am', 'start', '-a', 'android.settings.INPUT_METHOD_SETTINGS', max_output=65536)
            return self._helper_status(serial)

    def _xml(self, serial):
        path = '/data/local/tmp/matrix-ui-preview.xml'
        self._adb(serial, 'shell', 'uiautomator', 'dump', path, timeout=20, max_output=65536)
        try:
            output = self._adb(serial, 'exec-out', 'cat', path, timeout=20,
                max_output=4 * 1024 * 1024).decode('utf-8', 'replace')
        finally:
            try:
                self._adb(serial, 'shell', 'rm', path, timeout=10, max_output=65536)
            except Exception:
                pass
        start = output.find('<hierarchy')
        end = output.rfind('</hierarchy>')
        if start < 0 or end < 0:
            raise RuntimeError('此应用没有可验证的界面结构，请通过投屏人工操作')
        try:
            return ElementTree.fromstring(output[start:end + len('</hierarchy>')])
        except ElementTree.ParseError as exc:
            raise RuntimeError('无法验证手机界面，请通过投屏人工操作') from exc

    @staticmethod
    def _bounds(node):
        match = BOUNDS_PATTERN.fullmatch(node.get('bounds', ''))
        if not match:
            return None
        left, top, right, bottom = map(int, match.groups())
        return (left, top, right, bottom) if right > left and bottom > top else None

    def _target_node(self, xml, point, size, kind):
        x, y = self._point(point)
        width, height = size
        px, py = round(x * (width - 1)), round(y * (height - 1))
        matches = []
        for node in xml.iter('node'):
            bounds = self._bounds(node)
            if not bounds or node.get('enabled') != 'true' or node.get('visible-to-user') == 'false' or not bounds[0] <= px < bounds[2] or not bounds[1] <= py < bounds[3]:
                continue
            editable = node.get('editable') == 'true' or 'EditText' in node.get('class', '')
            label = (node.get('text', '') or node.get('content-desc', '')).strip().lower()
            if kind == 'input' and editable and node.get('password') != 'true':
                matches.append(node)
            if kind == 'send' and not editable and node.get('clickable') == 'true' and label in ('发送', 'send', '发送消息', 'send message'):
                matches.append(node)
        if len(matches) != 1:
            raise ValueError('无法唯一验证输入框或发送按钮，请调整标记或通过投屏人工发送')
        return matches[0]

    def _chat_node(self, xml, text, package, height):
        if not isinstance(text, str) or not text.strip() or len(text) > 100:
            raise ValueError('请填写聊天页顶部显示的完整聊天名称')
        matches = [node for node in xml.iter('node') if
            (node.get('text', '') or node.get('content-desc', '')).strip() == text.strip()
            and node.get('package') == package and self._bounds(node)
            and self._bounds(node)[1] < height * .35 and 'EditText' not in node.get('class', '')]
        if len(matches) != 1:
            raise ValueError('无法唯一验证当前聊天名称，请打开目标聊天或通过投屏人工发送')
        return matches[0]

    def _signature(self, node):
        return {key: node.get(key, '') for key in ('package', 'class', 'resource-id',
            'bounds', 'text', 'content-desc', 'focused', 'enabled', 'clickable')}

    def _identity(self, node):
        return tuple(node.get(key, '') for key in ('package', 'class', 'resource-id', 'bounds'))

    def prepare(self, serials, text, targets):
        serials = self._serials(serials)
        if (not isinstance(text, str) or not 1 <= len(text) <= 500
                or any(ord(char) < 32 and char not in ('\n', '\t') for char in text)):
            raise ValueError('草稿文案需为 1 到 500 个字符，不支持控制字符')
        requires_helper = not ASCII_PATTERN.fullmatch(text)
        if not isinstance(targets, dict):
            raise ValueError('请为各手机分别标记输入框、发送按钮和聊天名称')
        draft = {'created': self.clock(), 'devices': []}
        items = []
        for serial in serials:
            try:
                target = targets.get(serial)
                if not isinstance(target, dict):
                    raise ValueError('该手机尚未标记输入框和发送按钮')
                ix, iy = self._point(target.get('input'))
                sx, sy = self._point(target.get('send'))
                target = {'input': {'x': ix, 'y': iy}, 'send': {'x': sx, 'y': sy},
                    'chat_text': target.get('chat_text', '')}
                with self._operation(serial, 'prepare'):
                    if requires_helper and not self._helper_status(serial)['active']:
                        raise ValueError('请先安装中文输入助手并在手机上选择 ADB Keyboard 为当前输入法；未填稿、未发送')
                    _, size = self._capture(serial)
                    xml = self._xml(serial)
                    field = self._target_node(xml, target['input'], size, 'input')
                    package = field.get('package', '')
                    if not package:
                        raise ValueError('无法验证输入框所属应用')
                    chat = self._chat_node(xml, target.get('chat_text'), package, size[1])
                    if field.get('text', ''):
                        raise ValueError('输入框已有内容，请先在手机清空，避免覆盖原草稿')
                    self._action(serial, 'tap', target['input'])
                    focus_xml = self._xml(serial)
                    focused = self._target_node(focus_xml, target['input'], size, 'input')
                    focus_chat = self._chat_node(focus_xml, target.get('chat_text'), package, size[1])
                    if (focused.get('focused') != 'true' or focused.get('text', '')
                            or self._identity(focused) != self._identity(field)
                            or self._signature(focus_chat) != self._signature(chat)):
                        raise ValueError('输入框未稳定聚焦，未填入草稿')
                    if requires_helper:
                        encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
                        self._adb(serial, 'shell', 'am', 'broadcast', '-a', 'ADB_INPUT_B64',
                            '-p', 'com.android.adbkeyboard', '--es', 'msg', encoded, max_output=65536)
                    else:
                        self._adb(serial, 'shell', 'input', 'text', shlex.quote(text.replace(' ', '%s')))
                    after = self._xml(serial)
                    field = self._target_node(after, target['input'], size, 'input')
                    send_button = self._target_node(after, target['send'], size, 'send')
                    current_chat = self._chat_node(after, target.get('chat_text'), package, size[1])
                    if (field.get('text', '') != text or field.get('focused') != 'true'
                            or send_button.get('package') != package
                            or self._signature(current_chat) != self._signature(chat)):
                        raise ValueError('草稿或聊天界面无法验证，请在手机检查；未执行发送')
                    preview = self.screenshot(serial, max_size=400)
                    draft['devices'].append({'serial': serial, 'target': target, 'size': size,
                        'field': self._signature(field), 'send': self._signature(send_button),
                        'chat': self._signature(current_chat)})
                    items.append({'serial': serial, 'ok': True, 'error': '', 'preview': preview})
            except Exception as exc:
                items.append({'serial': serial, 'ok': False, 'error': str(exc)[:300]})
        prepared_id = uuid.uuid4().hex if draft['devices'] else ''
        if prepared_id:
            with self._lock:
                draft['created'] = self.clock()
                self._prepared = {key: value for key, value in self._prepared.items()
                    if self.clock() - value['created'] <= 300}
                self._prepared[prepared_id] = draft
        return {'prepared_id': prepared_id, 'items': items,
            'expires_in': 300, 'succeeded': len(draft['devices']), 'total': len(items)}

    def send(self, prepared_id):
        if not isinstance(prepared_id, str):
            raise ValueError('草稿预览记录无效')
        with self._lock:
            draft = self._prepared.pop(prepared_id, None)
        if not draft:
            raise ValueError('没有可发送的已预览草稿，请重新填稿并预览')
        if self.clock() - draft['created'] > 300:
            raise ValueError('草稿预览已过期，请重新填稿并预览')
        items = []
        for item in draft['devices']:
            serial = item['serial']
            try:
                with self._operation(serial, 'send'):
                    _, size = self._capture(serial)
                    if size != item['size']:
                        raise ValueError('手机方向或屏幕尺寸已变化，已阻止发送')
                    xml = self._xml(serial)
                    target = item['target']
                    field = self._target_node(xml, target['input'], size, 'input')
                    send_button = self._target_node(xml, target['send'], size, 'send')
                    chat = self._chat_node(xml, target['chat_text'], field.get('package', ''), size[1])
                    if (self._signature(field) != item['field'] or self._signature(send_button) != item['send']
                            or self._signature(chat) != item['chat']):
                        raise ValueError('聊天页、输入内容或发送按钮已变化，已阻止发送')
                    bounds = self._bounds(send_button)
                    self._adb(serial, 'shell', 'input', 'tap',
                        str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2))
                items.append({'serial': serial, 'ok': True, 'error': '', 'clicked': True,
                    'note': '已点击经过验证的发送按钮；请在手机确认实际送达状态'})
            except Exception as exc:
                items.append({'serial': serial, 'ok': False, 'error': str(exc)[:300], 'clicked': False})
        return {'items': items, 'succeeded': sum(item['ok'] for item in items), 'total': len(items)}
