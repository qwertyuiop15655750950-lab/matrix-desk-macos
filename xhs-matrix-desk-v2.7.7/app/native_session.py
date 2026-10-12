"""Local-only session handoff between the manual WebKit view and Chromium jobs.

Never expose credential state through the JS API or logs. Existing Chromium
profiles remain intact; this private snapshot only carries the chosen account.
"""
import json
import os
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

DOMAINS = {
    'xiaohongshu': ('rednote.com', 'xiaohongshu.com'),
    'douyin': ('douyin.com', 'snssdk.com', 'amemv.com'),
    'channels': ('weixin.qq.com', 'wechat.com'),
}
_lock = threading.RLock()


def trusted_host(platform, host):
    host = str(host or '').lower().lstrip('.')
    return any(host == base or host.endswith('.' + base) for base in DOMAINS.get(platform, ()))


def clean_state(platform, state):
    cookies = [dict(c) for c in state.get('cookies', []) if trusted_host(platform, c.get('domain'))]
    origins = [dict(o) for o in state.get('origins', [])
               if urlsplit(o.get('origin', '')).scheme == 'https'
               and trusted_host(platform, urlsplit(o.get('origin', '')).hostname)]
    return {'cookies': cookies, 'origins': origins}


def state_path(root, platform, aid):
    if platform not in DOMAINS or isinstance(aid, bool) or int(aid) <= 0:
        raise ValueError('账号无效')
    return Path(root) / 'native_sessions' / f'{platform}-{int(aid)}.json'


def save_state(root, platform, aid, state):
    path = state_path(root, platform, aid)
    with _lock:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.session-')
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(clean_state(platform, state), handle, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    return path


def load_state(root, platform, aid):
    path = state_path(root, platform, aid)
    with _lock:
        if not path.exists(): return None, None
        # Avoid stale handoffs when the native view has not changed.
        version = f'{path.stat().st_mtime_ns}-{path.stat().st_size}'
        return clean_state(platform, json.loads(path.read_text())), version


def storage_script(origins, version):
    payload = json.dumps({o['origin']: o.get('localStorage', []) for o in origins}, ensure_ascii=False)
    revision = json.dumps(str(version))
    return """(() => { try {
      const all = %s, entries = all[location.origin];
      if (!entries || localStorage.getItem('_matrixNativeSeed') === %s) return;
      for (const e of entries) if(e.name !== '_matrixNativeSeed') localStorage.setItem(e.name, e.value);
      localStorage.setItem('_matrixNativeSeed', %s);
    } catch (_) {} })();""" % (payload, revision, revision)


def apply_state(context, root, platform, aid):
    state, version = load_state(root, platform, aid)
    if state is None or getattr(context, '_matrix_native_version', None) == version: return False
    # Cookie clearing is limited to this platform, including logout handoffs.
    for cookie in context.cookies():
        if trusted_host(platform, cookie.get('domain')):
            context.clear_cookies(name=cookie['name'], domain=cookie['domain'], path=cookie['path'])
    if state['cookies']: context.add_cookies(state['cookies'])
    script = storage_script(state['origins'], version)
    context.add_init_script(script)
    for page in context.pages:
        if not page.is_closed() and trusted_host(platform, urlsplit(page.url).hostname):
            page.evaluate(script)
    context._matrix_native_version = version
    return True
