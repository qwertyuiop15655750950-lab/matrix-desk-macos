"""macOS native account browsers, attached inside the existing app window.

Remote pages have no pywebview bridge. Each account owns a persistent WebKit
website data store. Native mouse, keyboard, clipboard and file dialogs are used.
"""
import concurrent.futures
import json
import math
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from .native_session import trusted_host, clean_state, save_state, storage_script


def profile_identifier(root, platform, aid):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'matrix-native:{Path(root).resolve()}:{platform}:{int(aid)}'))


def validate_rect(rect):
    if not isinstance(rect, dict): raise ValueError('网页区域无效')
    result = {}
    for key in ('x', 'y', 'width', 'height'):
        value = rect.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('网页区域无效')
        result[key] = float(value)
    if result['width'] <= 0 or result['height'] <= 0: raise ValueError('网页区域尺寸无效')
    result['visible'] = bool(rect.get('visible', True))
    return result


def available():
    if sys.platform != 'darwin': return False
    try:
        import WebKit
        return hasattr(WebKit.WKWebsiteDataStore, 'dataStoreForIdentifier_')
    except ImportError:
        return False


# Keep Cocoa imports out of non-macOS tests / servers.
if sys.platform == 'darwin':
    import AppKit
    import Foundation
    import WebKit
    import objc
    from PyObjCTools import AppHelper

    class MatrixNativeDelegate(Foundation.NSObject):
        def initWithOwner_key_(self, owner, key):
            self = objc.super(MatrixNativeDelegate, self).init()
            if self is not None: self.owner, self.key = owner, key
            return self

        def webView_didFinishNavigation_(self, view, navigation):
            self.owner._did_finish(self.key, view)

        def webView_didFailProvisionalNavigation_withError_(self, view, navigation, error):
            if error.code() != -999:
                self.owner._error(self.key, f'网页加载失败：{error.localizedDescription()}')

        def webView_didFailNavigation_withError_(self, view, navigation, error):
            self.webView_didFailProvisionalNavigation_withError_(view, navigation, error)

        def webViewWebContentProcessDidTerminate_(self, view):
            self.owner._error(self.key, '网页进程已退出，请点击刷新重新加载')

        def webView_decidePolicyForNavigationAction_decisionHandler_(self, view, action, handler):
            target = str(action.request().URL().absoluteString())
            parsed = urlsplit(target)
            # Subframes still load platform media/auth resources normally.
            main_frame = action.targetFrame() is None or action.targetFrame().isMainFrame()
            allow = parsed.scheme == 'https' and not parsed.username and not parsed.password
            if main_frame: allow = allow and trusted_host(self.key[0], parsed.hostname)
            # Local pages are only allowed in the explicit offline QA harness.
            if self.owner.test_mode and parsed.scheme == 'http' and parsed.hostname == '127.0.0.1': allow = True
            if not allow and main_frame:
                self.owner._error(self.key, '该链接不属于此账号的平台，未在账号页面中打开')
            handler(WebKit.WKNavigationActionPolicyAllow if allow else WebKit.WKNavigationActionPolicyCancel)

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(self, view, config, action, features):
            # Keep target=_blank / window.open in the same account, never in a
            # shared system browser. Native navigation history provides Back.
            view.loadRequest_(action.request())
            return None

        def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(self, view, params, frame, handler):
            panel = AppKit.NSOpenPanel.openPanel()
            panel.setCanChooseFiles_(True)
            panel.setCanChooseDirectories_(bool(params.allowsDirectories()))
            panel.setAllowsMultipleSelection_(bool(params.allowsMultipleSelection()))
            panel.beginSheetModalForWindow_completionHandler_(view.window(),
                lambda result: handler(panel.URLs() if result == AppKit.NSModalResponseOK else None))

        def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(self, view, message, frame, handler):
            alert = AppKit.NSAlert.alloc().init(); alert.setMessageText_(str(message)); alert.addButtonWithTitle_('确定')
            alert.beginSheetModalForWindow_completionHandler_(view.window(), lambda result: handler())

        def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(self, view, message, frame, handler):
            alert = AppKit.NSAlert.alloc().init(); alert.setMessageText_(str(message))
            alert.addButtonWithTitle_('确定'); alert.addButtonWithTitle_('取消')
            alert.beginSheetModalForWindow_completionHandler_(view.window(), lambda result: handler(result == AppKit.NSAlertFirstButtonReturn))


class NativeBrowser:
    MAX_VIEWS = 4

    def __init__(self, window, root, importer, test_mode=False):
        self.window, self.root, self.importer = window, Path(root), importer
        self.test_mode = bool(test_mode)
        self.lock = threading.RLock()
        self.epoch = -1
        self.layout_revision = -1
        self.selected = None
        self.views = {}  # Cocoa objects only touched from main thread.
        self.summary = {}
        self.closed = False

    def _host(self):
        from webview.platforms.cocoa import BrowserView
        host = BrowserView.instances.get(self.window.uid)
        if host is None: raise ValueError('主窗口尚未准备好')
        return host

    def _main(self, fn, timeout=20):
        if not available(): raise ValueError('内嵌账号浏览器需要 macOS 14 或以上')
        if Foundation.NSThread.isMainThread(): return fn()
        future = concurrent.futures.Future()
        def execute():
            if not future.set_running_or_notify_cancel(): return
            try: future.set_result(fn())
            except Exception as error: future.set_exception(error)
        AppHelper.callAfter(execute)
        try: return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ValueError('网页窗口响应超时，请重新选择账号') from None

    def _intent(self, epoch, key):
        if isinstance(epoch, bool) or not isinstance(epoch, int): raise ValueError('操作编号无效')
        with self.lock:
            if self.closed or epoch < self.epoch: return False
            self.epoch, self.selected, self.layout_revision = epoch, key, -1
            return True

    def _current(self, epoch, key):
        with self.lock: return not self.closed and epoch == self.epoch and key == self.selected

    def status(self):
        def read():
            with self.lock:
                for key, item in self.views.items():
                    view = item['view']
                    if view.title(): self.summary[key]['title'] = str(view.title())
                    if view.URL(): self.summary[key]['url'] = str(view.URL().absoluteString())
                return {'available': available(), 'selected': list(self.selected) if self.selected else None,
                        'items': [dict(item) for item in self.summary.values()]}
        return self._main(read) if available() else read()

    def select(self, platform, aid, rect, epoch, start_url):
        key, rect = (platform, int(aid)), validate_rect(rect)
        if not self._intent(epoch, key): return {'stale': True}
        # Hide the previous account immediately, even while reading its peer's
        # saved Chromium login. A late import can never show the wrong account.
        self._main(lambda: self._hide_views())
        marker = self.root / 'native_profiles' / (profile_identifier(self.root, *key) + '.initialized')
        state = None
        if not marker.exists() and not self._main(lambda: key in self.views):
            state = clean_state(platform, self.importer(platform, int(aid)))
        if not self._current(epoch, key): return {'stale': True}
        def show():
            if not self._current(epoch, key): return {'stale': True}
            if key not in self.views: self._create(key, start_url, state, marker)
            item = self.views[key]; item['used'] = time.monotonic()
            self._layout(key, rect)
            self._evict()
            return {'key': f'{platform}:{aid}', 'embedded': True}
        return self._main(show)

    def _create(self, key, start_url, state, marker):
        identifier = Foundation.NSUUID.alloc().initWithUUIDString_(profile_identifier(self.root, *key))
        store = WebKit.WKWebsiteDataStore.dataStoreForIdentifier_(identifier)
        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(store)
        config.setPreferences_(WebKit.WKPreferences.alloc().init())
        config.preferences().setJavaScriptCanOpenWindowsAutomatically_(True)
        if state and state['origins']:
            script = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                storage_script(state['origins'], 'first-import'), WebKit.WKUserScriptInjectionTimeAtDocumentStart, True)
            config.userContentController().addUserScript_(script)
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(AppKit.NSMakeRect(0, 0, 1, 1), config)
        delegate = MatrixNativeDelegate.alloc().initWithOwner_key_(self, key)
        view.setNavigationDelegate_(delegate); view.setUIDelegate_(delegate)
        view.setAllowsBackForwardNavigationGestures_(True)
        view.setHidden_(True)
        host = self._host()
        if not hasattr(self, 'container'):
            container = AppKit.NSView.alloc().initWithFrame_(host.webview.frame())
            host.window.setContentView_(container)
            host.webview.setFrame_(container.bounds())
            host.webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
            container.addSubview_(host.webview)
            self.container = container
        self.container.addSubview_(view)
        item = {'view': view, 'delegate': delegate, 'store': store, 'used': time.monotonic(),
                'origins': (state or {}).get('origins', []), 'starting': True}
        self.views[key] = item
        with self.lock: self.summary[key] = {'platform': key[0], 'id': key[1], 'title': '正在打开…', 'url': start_url, 'error': ''}
        def load():
            if self.closed or self.views.get(key) is not item: return
            marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            marker.write_text('1\n'); marker.chmod(0o600)
            item['starting'] = False
            view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(start_url)))
        cookies = (state or {}).get('cookies', [])
        if not cookies: load(); return
        remaining = [len(cookies)]
        def done():
            remaining[0] -= 1
            if remaining[0] == 0: load()
        for cookie in cookies:
            props = {Foundation.NSHTTPCookieName: cookie['name'], Foundation.NSHTTPCookieValue: cookie['value'],
                     Foundation.NSHTTPCookieDomain: cookie['domain'], Foundation.NSHTTPCookiePath: cookie.get('path', '/')}
            if cookie.get('secure'): props[Foundation.NSHTTPCookieSecure] = 'TRUE'
            if cookie.get('httpOnly'): props['HttpOnly'] = 'TRUE'
            if cookie.get('expires', -1) > 0:
                props[Foundation.NSHTTPCookieExpires] = Foundation.NSDate.dateWithTimeIntervalSince1970_(cookie['expires'])
            native_cookie = Foundation.NSHTTPCookie.cookieWithProperties_(props)
            if native_cookie is None: done()
            else: store.httpCookieStore().setCookie_completionHandler_(native_cookie, done)

    def _hide_views(self):
        for item in self.views.values(): item['view'].setHidden_(True)

    def _layout(self, key, rect):
        self._hide_views()
        self._place(key, rect, 1.0)

    def _place(self, key, rect, zoom):
        item = self.views.get(key)
        if item is None or not rect['visible']: return
        host = getattr(self, 'container', self._host().webview)
        bounds = host.bounds()
        x, y = max(0, rect['x']), max(0, rect['y'])
        width = min(rect['width'], bounds.size.width - x)
        height = min(rect['height'], bounds.size.height - y)
        if width < 20 or height < 20: return
        # Main pywebview WKWebView uses top-left web coordinates.
        origin_y = y if host.isFlipped() else bounds.size.height - y - height
        item['view'].setPageZoom_(zoom)
        item['view'].setFrame_(AppKit.NSMakeRect(x, origin_y, width, height))
        item['view'].setHidden_(False)

    def overview(self, epoch, slots):
        if not self._intent(epoch, None): return {'stale': True}
        return self.overview_layout(epoch, 0, slots)

    def overview_layout(self, epoch, revision, slots):
        if not isinstance(slots, list) or len(slots) > self.MAX_VIEWS:
            raise ValueError('总览最多显示四个已打开账号')
        validated = []
        for slot in slots:
            key = (slot['platform'], int(slot['id']))
            if key in [k for k, _ in validated]: raise ValueError('总览账号重复')
            validated.append((key, validate_rect(slot['rect'])))
        with self.lock:
            if not self._current(epoch, None) or revision < self.layout_revision: return {'stale': True}
            self.layout_revision = revision
        def show():
            if not self._current(epoch, None) or revision != self.layout_revision: return {'stale': True}
            if any(key not in self.views for key, _ in validated):
                raise ValueError('账号已释放，请重新打开')
            self._hide_views()
            for key, rect in validated: self._place(key, rect, 0.65)
            return {'positioned': True, 'count': len(validated)}
        return self._main(show)

    def layout(self, epoch, revision, rect):
        rect = validate_rect(rect)
        with self.lock:
            key = self.selected
            if epoch != self.epoch or key is None or revision < self.layout_revision: return {'stale': True}
            self.layout_revision = revision
        def update():
            with self.lock:
                if not self._current(epoch, key) or revision != self.layout_revision: return {'stale': True}
            self._layout(key, rect)
            return {'positioned': True}
        return self._main(update)

    def command(self, epoch, command, url=''):
        with self.lock: key = self.selected
        if not key or not self._current(epoch, key): raise ValueError('账号已切换，请重新操作')
        if command == 'navigate':
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.username or parsed.password or not trusted_host(key[0], parsed.hostname):
                raise ValueError('请输入该平台的完整 HTTPS 网址')
        def perform():
            if not self._current(epoch, key) or key not in self.views: raise ValueError('账号已切换')
            view = self.views[key]['view']
            if command == 'back': view.goBack()
            elif command == 'forward': view.goForward()
            elif command == 'reload': view.reload()
            elif command == 'navigate': view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(url)))
            else: raise ValueError('浏览器操作无效')
            return {'accepted': True}
        return self._main(perform)

    def _error(self, key, message):
        with self.lock:
            if key in self.summary: self.summary[key]['error'] = message

    def _did_finish(self, key, view):
        with self.lock:
            if key in self.summary:
                self.summary[key].update(title=str(view.title() or '页面'), url=str(view.URL().absoluteString()), error='')
        item = self.views.get(key)
        # The seed script's per-origin marker prevents repeat token writes.
        self._snapshot(key)

    def _snapshot(self, key, complete=None):
        item = self.views.get(key)
        if item is None or item['starting']:
            if complete: complete()
            return
        view, store = item['view'], item['store']
        item['snapshot_revision'] = item.get('snapshot_revision', 0) + 1
        revision = item['snapshot_revision']
        def cookies_ready(native_cookies):
            cookies = []
            for cookie in native_cookies:
                if not trusted_host(key[0], str(cookie.domain())): continue
                expires = cookie.expiresDate()
                cookies.append({'name': str(cookie.name()), 'value': str(cookie.value()), 'domain': str(cookie.domain()),
                                'path': str(cookie.path()), 'secure': bool(cookie.isSecure()), 'httpOnly': bool(cookie.isHTTPOnly()),
                                'expires': float(expires.timeIntervalSince1970()) if expires else -1, 'sameSite': 'Lax'})
            def storage_ready(value, error):
                origins = item['origins'][:]
                if not error and isinstance(value, dict) and trusted_host(key[0], urlsplit(value.get('origin', '')).hostname):
                    origins = [o for o in origins if o['origin'] != value['origin']] + [value]
                    item['origins'] = origins
                try:
                    if item.get('snapshot_revision') == revision:
                        save_state(self.root, *key, {'cookies': cookies, 'origins': origins})
                except Exception:
                    self._error(key, '登录状态交接保存失败，请保留当前页面并重试')
                finally:
                    if complete: complete()
            view.evaluateJavaScript_completionHandler_(
                "({origin:location.origin,localStorage:Object.keys(localStorage).filter(k=>k!=='_matrixNativeSeed').map(k=>({name:k,value:localStorage.getItem(k)}))})", storage_ready)
        store.httpCookieStore().getAllCookies_(cookies_ready)

    def hide(self, epoch):
        if not self._intent(epoch, None): return {'stale': True}
        completed = threading.Event()
        def conceal():
            if not self._current(epoch, None): completed.set(); return
            self._hide_views()
            keys = list(self.views)
            if not keys: completed.set(); return
            remaining = [len(keys)]
            def done():
                remaining[0] -= 1
                if remaining[0] == 0: completed.set()
            for key in keys: self._snapshot(key, done)
        self._main(conceal)
        # JS API worker can wait; the Cocoa main loop must remain responsive.
        if not Foundation.NSThread.isMainThread() and not completed.wait(8):
            raise ValueError('登录状态保存仍在进行，请稍后重试')
        return {'hidden': True}

    def _evict(self):
        if len(self.views) <= self.MAX_VIEWS: return
        key = min((key for key in self.views if key != self.selected), key=lambda key: self.views[key]['used'])
        item = self.views[key]
        def remove():
            if self.views.get(key) is not item or key == self.selected: return
            item['view'].stopLoading(); item['view'].setNavigationDelegate_(None); item['view'].setUIDelegate_(None)
            item['view'].removeFromSuperview(); self.views.pop(key)
            with self.lock: self.summary.pop(key, None)
        self._snapshot(key, remove)

    def close(self):
        with self.lock: self.closed = True; self.selected = None
        def destroy():
            for item in self.views.values():
                item['view'].stopLoading(); item['view'].setNavigationDelegate_(None); item['view'].setUIDelegate_(None)
                item['view'].removeFromSuperview()
            self.views.clear(); self.summary.clear()
        self._main(destroy)
