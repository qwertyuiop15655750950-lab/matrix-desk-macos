"""One selected phone, scrcpy 5.0.1 video + direct control; no recording or clipboard sync.
Protocol reference: Genymobile/scrcpy v5.0.1 app/src/control_msg.c and doc/develop.md.
"""
import base64
import io
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import threading
import time


class LivePhone:
    def __init__(self, controller):
        self.controller = controller
        self.lock = threading.RLock()
        self.serial = self.token = ''
        self.latest = None
        self.last_poll = 0
        self.error = ''
        self.video = self.control = self.process = self.port = self.remote = None
        self.thread = None
        self.stop_event = threading.Event()
        self.sequence = 0
        self.touching = False
        self.touch_point = None
        self.touch_time = 0

    def start(self, serial):
        import av  # Optional: absent decoder gives a clear error; device tools remain usable.
        self.controller._serial(serial)
        self.controller._online(serial)
        server = self.controller.scrcpy.parent / 'scrcpy-server'
        if not server.is_file():
            raise ValueError('缺少 scrcpy-server，无法在工作台内实时投屏')
        version = self.controller._run([self.controller.scrcpy, '--version'], max_output=65536).decode('utf-8', 'replace').splitlines()[0].split()[1]
        if version != '5.0.1':
            raise ValueError('实时控制目前适配 scrcpy 5.0.1；当前版本不匹配，请使用原投屏工具')
        with self.lock:
            self.stop()
            with self.controller._lock:
                if serial in self.controller._reservations:
                    raise ValueError('此手机正在执行任务，请先停止原任务')
                token = secrets.token_hex(16)
                self.controller._reservations[serial] = (token, 'live-control')
            self.serial, self.token = serial, token
            self.stop_event = threading.Event()
            self.sequence = 0
            self.latest, self.error = None, ''
            self.last_poll = time.monotonic()
            scid = secrets.randbelow(0x7fffffff)
            self.remote = f'/data/local/tmp/matrix-live-{scid:08x}.jar'
            try:
                self.controller._adb(serial, 'push', str(server), self.remote)
                output = self.controller._adb(serial, 'forward', 'tcp:0', f'localabstract:scrcpy_{scid:08x}')
                self.port = int(output.strip())
                self.process = subprocess.Popen([str(self.controller.adb), '-s', serial, 'shell',
                    f'CLASSPATH={self.remote}', 'app_process', '/', 'com.genymobile.scrcpy.Server', version,
                    f'scid={scid:08x}', 'tunnel_forward=true', 'audio=false', 'control=true', 'raw_stream=true',
                    'send_dummy_byte=true', 'max_size=720', 'max_fps=24', 'video_bit_rate=2000000',
                    'clipboard_autosync=false', 'power_on=false', 'cleanup=false', 'log_level=error'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env={**os.environ, 'ADB': str(self.controller.adb)})
                deadline = time.monotonic()+8
                while time.monotonic()<deadline:
                    stream = None
                    try:
                        stream = socket.create_connection(('127.0.0.1', self.port), timeout=.4)
                        if stream.recv(1) != b'\x00':
                            raise OSError('server not ready')
                        self.video = stream
                        break
                    except OSError:
                        if stream: stream.close()
                        if self.process.poll() is not None: break
                        time.sleep(.1)
                if self.video is None:
                    raise RuntimeError('手机视频连接失败，请检查 USB 授权和设备兼容性')
                self.video.settimeout(.5)
                self.control = socket.create_connection(('127.0.0.1', self.port), timeout=1)
                self.control.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.control.settimeout(.5)
                self.last_poll = time.monotonic()
                self.thread = threading.Thread(target=self._read, args=(token, self.video, self.stop_event), daemon=True)
                self.thread.start()
                return {'serial': serial, 'token': token, 'max_fps': 24, 'display_fps': 10}
            except Exception:
                self.stop()
                raise

    def _read(self, token, video, stopped):
        import av
        decoder = av.CodecContext.create('h264', 'r')
        decoder.thread_count = 1
        last_encode = 0
        try:
            while not stopped.is_set():
                if time.monotonic()-self.last_poll > 15:
                    raise RuntimeError('实时预览闲置，连接已停止，请重新打开')
                # Release a held touch if the UI disappears or pointer-up was lost.
                with self.lock:
                    if token != self.token: return
                    if self.touching and time.monotonic()-self.touch_time > 2:
                        self._release_touch()
                try: chunk = video.recv(65536)
                except socket.timeout: continue
                if not chunk: raise RuntimeError('手机视频连接已断开，请检查连接后重新打开')
                for packet in decoder.parse(chunk):
                    for frame in decoder.decode(packet):
                        now = time.monotonic()
                        if now-last_encode < .075: continue
                        last_encode = now
                        image = frame.to_image()
                        output = io.BytesIO()
                        image.save(output, format='JPEG', quality=70)
                        with self.lock:
                            if token != self.token: return
                            self.sequence += 1
                            self.latest = {'sequence': self.sequence, 'width': frame.width, 'height': frame.height,
                                           'b64': base64.b64encode(output.getvalue()).decode(), 'time': now}
        except Exception as error:
            if not stopped.is_set():
                with self.lock:
                    if token == self.token: self.error = str(error)[:180]
        finally:
            # Close only this generation; a new phone must never be disconnected here.
            with self.lock:
                if token == self.token:
                    reason = self.error
                    self.stop()
                    self.error = reason

    def frame(self, token, after=0):
        with self.lock:
            self._require(token)
            self.last_poll = time.monotonic()
            if self.error: raise ValueError(self.error)
            if not self.latest: return {'ready': False}
            return {'ready': True, **{k:v for k,v in self.latest.items() if k not in ('time','b64')},
                    'b64': self.latest['b64'] if self.latest['sequence'] != after else '',
                    'frame_age_ms': round((time.monotonic()-self.latest['time'])*1000)}

    def _require(self, token):
        if not self.token or token != self.token:
            raise ValueError(self.error or '投屏连接已关闭，请重新选择手机')

    def input(self, token, action, params):
        with self.lock:
            self._require(token)
            if not isinstance(params, dict): raise ValueError('操作参数无效')
            if not self.latest or time.monotonic()-self.latest['time'] > 3:
                raise ValueError('画面尚未就绪或已过期，请检查手机连接')
            if params.get('width') != self.latest['width'] or params.get('height') != self.latest['height']:
                raise ValueError('手机画面尺寸发生变化，请重新操作')
            self.last_poll = time.monotonic()
            if action in ('down', 'move', 'up', 'cancel'):
                x, y = self.controller._point(params)
                if action=='down' and self.touching: raise ValueError('上一次触摸尚未结束')
                if action!='down' and not self.touching: return {'accepted': False}
                if action=='down': self.touching=True
                self.touch_point=(x,y,self.latest['width'],self.latest['height'])
                self.touch_time=time.monotonic()
                self._touch({'down':0,'up':1,'move':2,'cancel':3}[action], *self.touch_point)
                if action in ('up','cancel'): self.touching=False
            elif action == 'paste':
                text=params.get('text')
                if not isinstance(text,str) or not text or len(text.encode('utf-8'))>65536:
                    raise ValueError('粘贴文字为空或超过 64 KB')
                self._release_touch()
                data=text.encode('utf-8')
                # scrcpy SET_CLIPBOARD, sequence=0, paste=true. No clipboard autosync.
                self.control.sendall(struct.pack('>BQBI',9,0,1,len(data))+data)
            elif action in ('home','back'):

                self._release_touch()
                key = 3 if action=='home' else 4
                self.control.sendall(struct.pack('>BBIII',0,0,key,0,0)+struct.pack('>BBIII',0,1,key,0,0))
            else: raise ValueError('仅支持手动触摸、返回、主页和文字粘贴')
            return {'accepted': True}

    def _touch(self, action, x, y, width, height):
        # Generic finger (-2) in the scrcpy protocol, coords refer to encoded video size.
        self.control.sendall(struct.pack('>BBQiiHHHII',2,action,0xfffffffffffffffe,
            round(x*(width-1)),round(y*(height-1)),width,height,65535 if action in (0,2) else 0,0,0))

    def _release_touch(self):
        if self.touching and self.touch_point and self.control:
            try:
                x,y,width,height=self.touch_point
                if self.latest: width,height=self.latest['width'],self.latest['height']
                self._touch(1,x,y,width,height)
            except OSError: pass
        self.touching=False

    def stop(self, token=None):
        with self.lock:
            if token is not None and token != self.token: return {'stopped': False}
            self._release_touch()
            self.stop_event.set()
            self.token = ''
            for stream in (self.video,self.control):
                if stream:
                    try: stream.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                    stream.close()
            self.video=self.control=None
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired: self.process.kill();self.process.wait(timeout=2)
            self.process=None
            if self.serial:
                if self.port:
                    try: self.controller._adb(self.serial,'forward','--remove',f'tcp:{self.port}',timeout=3)
                    except Exception: pass
                if self.remote:
                    try: self.controller._adb(self.serial,'shell','rm',self.remote,timeout=3)
                    except Exception: pass
                with self.controller._lock:
                    reservation=self.controller._reservations.get(self.serial)
                    if reservation and reservation[1]=='live-control':self.controller._reservations.pop(self.serial,None)
            self.serial='';self.port=self.remote=None;self.latest=None
            return {'stopped': True}
