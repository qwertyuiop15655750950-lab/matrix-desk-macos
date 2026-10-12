"""独立 Android 工具的 API。"""
import threading

class AndroidAPI:
    def _android_controller(self):
        with self._android_lock:
            if self._android is None:
                from .android_control import AndroidController
                self._android = AndroidController()
            return self._android

    def _live_controller(self):
        with self._android_lock:
            if getattr(self, '_live', None) is None:
                from .android_live import LivePhone
                self._live = LivePhone(self._android_controller())
            return self._live

    def android_live_start(self, serial):
        try: return self._ok(**self._live_controller().start(serial))
        except ImportError: return self._err('缺少实时视频解码组件 av，请安装设备工具的 requirements-live.txt')
        except Exception as error: return self._err(error)

    def android_live_frame(self, token, after=0):
        try: return self._ok(**self._live_controller().frame(token, after))
        except Exception as error: return self._err(error)

    def android_live_input(self, token, action, params):
        try: return self._ok(**self._live_controller().input(token, action, params))
        except Exception as error: return self._err(error)

    def android_live_paste_clipboard(self, token, width, height):
        """Explicit user click: read current Mac text clipboard, paste to selected phone only."""
        try:
            live = self._live_controller()
            with live.lock:
                live._require(token)
                if not live.latest or (width, height) != (live.latest['width'], live.latest['height']):
                    raise ValueError('手机画面尚未就绪或尺寸已变化，请重新操作')
                from AppKit import NSPasteboard, NSPasteboardTypeString
                text = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
                if not text: raise ValueError('电脑剪贴板没有文字，请先复制文字')
                return self._ok(**live.input(token, 'paste', {'width': width, 'height': height, 'text': str(text)}))
        except ImportError: return self._err('当前系统不支持直接读取剪贴板，请在手机画面或文字框中按粘贴快捷键')
        except Exception as error: return self._err(error)

    def android_live_stop(self, token):
        try: return self._ok(**self._live_controller().stop(token))
        except Exception as error: return self._err(error)

    def android_status(self):
        try: return self._ok(**self._android_controller().status())
        except Exception as e: return self._err(e)

    def android_devices(self):
        try: return self._ok(items=self._android_controller().devices())
        except Exception as e: return self._err(e)

    def android_display_info(self, serials):
        try: return self._ok(**self._android_controller().batch_display_info(serials))
        except Exception as e: return self._err(e)

    def android_screenshot(self, serial, max_size=900):
        try:
            if isinstance(max_size, bool) or not isinstance(max_size, int) or not 240 <= max_size <= 900:
                raise ValueError('预览尺寸需为 240–900 像素')
            return self._ok(**self._android_controller().screenshot(serial, max_size=max_size))
        except Exception as e: return self._err(e)

    def android_mirror(self, serial):
        try: return self._ok(**self._android_controller().mirror(serial))
        except Exception as e: return self._err(e)

    def android_close_mirror(self, serial):
        try: return self._ok(**self._android_controller().close_mirror(serial))
        except Exception as e: return self._err(e)

    def android_batch(self, serials, action, params):
        try: return self._ok(**self._android_controller().batch_action(serials, action, params))
        except Exception as e: return self._err(e)

    def android_prepare(self, serials, text, targets):
        try: return self._ok(**self._android_controller().prepare(serials, text, targets))
        except Exception as e: return self._err(e)

    def android_send(self, prepared_id):
        try: return self._ok(**self._android_controller().send(prepared_id))
        except Exception as e: return self._err(e)

    def android_input_helper(self, serial, install=False, open_settings=False):
        try: return self._ok(**self._android_controller().input_helper(serial, install, open_settings))
        except Exception as e: return self._err(e)

    def _android_network_controller(self):
        with self._android_lock:
            if self._android_network is None:
                from .android_network import AndroidNetwork
                self._android_network = AndroidNetwork(self._android_controller(), self.db_path)
            return self._android_network

    def host_network_status(self):
        try:
            from .host_network import status
            return self._ok(**status())
        except Exception as e: return self._err(e)

    def android_network_status(self, serials=None):
        try: return self._ok(**self._android_network_controller().status(serials))
        except Exception as e: return self._err(e)

    def android_network_apply(self, serials, host, port, transport='lan'):
        try: return self._ok(**self._android_network_controller().apply_proxy(serials, host, port, transport))
        except Exception as e: return self._err(e)

    def android_network_restore(self, serials):
        try: return self._ok(**self._android_network_controller().restore_proxy(serials))
        except Exception as e: return self._err(e)

    def android_network_settings(self, serials):
        try: return self._ok(**self._android_network_controller().open_settings(serials))
        except Exception as e: return self._err(e)

    def android_network_check_exit(self, serials):
        try: return self._ok(**self._android_network_controller().check_exit(serials))
        except Exception as e: return self._err(e)

