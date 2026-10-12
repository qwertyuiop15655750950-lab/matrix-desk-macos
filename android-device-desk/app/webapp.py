"""独立手机工具：不加载桌面账号、发布、私信或浏览器模块。"""
from pathlib import Path
import threading
from .android_api import AndroidAPI

class Api(AndroidAPI):
    def __init__(self, db_path=None):
        self.db_path = str(db_path or Path(__file__).resolve().parents[1] / 'data/device-network.db')
        self._android = None
        self._android_network = None
        self._android_lock = threading.RLock()
    def _ok(self, **values):
        return {'ok': True, **values}
    def _err(self, error):
        return {'ok': False, 'error': str(error)}
    def close(self):
        if getattr(self, '_live', None) is not None: self._live.stop()
        if self._android is not None: self._android.close()

def main():
    import webview
    api = Api()
    webview.create_window('Android 设备工作台', str(Path(__file__).parent / 'ui/index.html'), js_api=api, width=1240, height=820)
    try: webview.start(debug=False)
    finally: api.close()
