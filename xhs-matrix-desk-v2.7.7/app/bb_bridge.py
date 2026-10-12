"""Task-local bb-browser 0.14.2 daemon attached to a selected account browser.

No CLI auto-discovery, cookie export, shared browser, or arbitrary public eval.
The Playwright page is used only to verify identity and pump its owning thread.
"""
from contextlib import contextmanager
import concurrent.futures
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

HOSTS = {
    'douyin': {'douyin.com', 'www.douyin.com', 'creator.douyin.com'},
    'xiaohongshu': {'xiaohongshu.com', 'www.xiaohongshu.com', 'creator.xiaohongshu.com'},
    'channels': {'channels.weixin.qq.com'},
}
PLUGIN_READY = ("document.documentElement.dataset.matrixCollector === 'ready' && "
                "!!document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start')")
MAX_HTML = 500000
CONTENT_PARAMETERS = {'id', 'video_id', 'note_id', 'objectid', 'post_id', 'q', 'keyword'}
CAPTURE_SCRIPT = r'''(() => {
  const allowed = new Set(['id','video_id','note_id','objectid','post_id','q','keyword']);
  const safeUrl = value => {
    const url = new URL(value, location.href);
    if (!['http:','https:'].includes(url.protocol) || url.username || url.password) return '';
    const params = new URLSearchParams();
    for (const [key,value] of url.searchParams) {
      if (allowed.has(key.toLowerCase()) && value.length <= 500) params.append(key,value);
    }
    const query = params.toString();
    return url.origin + url.pathname + (query ? '?' + query : '');
  };
  const clone = document.documentElement.cloneNode(true);
  // Inspect the original DOM while styles/hidden attributes still exist.
  // Removing these attributes first would expose hidden login/draft content.
  const originals = [document.documentElement, ...document.documentElement.querySelectorAll('*')];
  const copies = [clone, ...clone.querySelectorAll('*')];
  for (let i=0; i<originals.length; i++) {
    const original = originals[i], copy = copies[i], style = getComputedStyle(original);
    if (original.hasAttribute('hidden') || original.getAttribute('aria-hidden') === 'true'
        || style.display === 'none' || ['hidden','collapse'].includes(style.visibility)) {
      if (copy === clone) return {url:safeUrl(location.href),title:document.title.slice(0,500),html:'',truncated:false};
      copy.remove();
    }
  }
  clone.querySelectorAll('script,style,noscript,template,input,textarea,select,form,iframe,object,embed,meta,link,[contenteditable],#matrix-browser-collector').forEach(el=>el.remove());
  for (const el of [clone, ...clone.querySelectorAll('*')]) {
    for (const attr of [...el.attributes]) {
      if (!['class','id','role','aria-label','title','href'].includes(attr.name)) el.removeAttribute(attr.name);
    }
    if (el.hasAttribute('href')) {
      try {
        const link = safeUrl(el.getAttribute('href'));
        if (!link) el.removeAttribute('href');
        else el.setAttribute('href', link);
      } catch (_) { el.removeAttribute('href'); }
    }
  }
  const full = clone.outerHTML;
  return {url: safeUrl(location.href), title: document.title.slice(0,500),
          html: full.slice(0,500000), truncated: full.length > 500000};
})()'''


COMMENT_JOB_SCRIPT = r'''(() => {
  const key = __KEY__;
  if (window.__matrixCrawlJob && !window.__matrixCrawlJob.finished) return {ok:false,error:'已有爬虫采集任务，请先停止当前任务'};
  if(document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start')?.disabled)return {ok:false,error:'插件采集正在执行，请先停止插件任务'};
  const old = document.getElementById('matrix-crawl-collector'); if (old) old.remove();
  const job = {key,url:location.href,paused:false,stopped:false,finished:false,reason:'',read:0};
  window.__matrixCrawlJob = job;
  const host=document.createElement('div'); host.id='matrix-crawl-collector';
  host.style.cssText='position:fixed;top:18px;right:18px;width:310px;z-index:2147483647';
  const root=host.attachShadow({mode:'open'});
  root.innerHTML='<style>*{box-sizing:border-box}.panel{font:13px/1.5 sans-serif;background:#172033;color:#fff;padding:14px;border-radius:12px;border:1px solid #546b86}button{padding:6px 10px;margin-right:8px;margin-top:8px;cursor:pointer}p{margin:8px 0;color:#c5d2e4}</style><section class="panel"><b>bb-browser + Crawl4AI 评论采集</b><p>请打开本页评论区。只读取已加载的评论，可随时暂停或停止。</p><p id="status" role="status">等候评论区</p><button id="pause">暂停</button><button id="stop">停止</button></section>';
  root.getElementById('pause').onclick=()=>{job.paused=!job.paused;root.getElementById('pause').textContent=job.paused?'继续':'暂停';};
  root.getElementById('stop').onclick=()=>{job.stopped=true;job.reason='user';};
  document.documentElement.appendChild(host);
  const pluginStart=document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start');
  if(pluginStart)pluginStart.disabled=true;
  return {ok:true};
})()'''
COMMENT_STATE_SCRIPT = r'''(() => {
  const job=window.__matrixCrawlJob;
  if (!job || job.key !== __KEY__) return {stopped:true,reason:'page_changed'};
  if (location.href !== job.url) {job.stopped=true;job.reason='page_changed';}
  return {paused:job.paused,stopped:job.stopped,reason:job.reason};
})()'''
COMMENT_SNAPSHOT_SCRIPT = r'''(() => {
  const cfg=__CONFIG__, allowed=new Set(__ATTRIBUTES__), job=window.__matrixCrawlJob;
  if(!job||job.key!==__KEY__||job.stopped||job.paused||location.href!==job.url)return {fragments:[],stopped:true};
  const visible=el=> {
    if (!el.getClientRects().length) return false;
    for (let parent=el;parent;parent=parent.parentElement) {
      const style=getComputedStyle(parent);
      if(parent.hidden||parent.getAttribute('aria-hidden')==='true'||style.display==='none'||['hidden','collapse'].includes(style.visibility))return false;
    }
    return true;
  };
  const fragments=[],identities=[];let length=0,truncated=false;
  for(const item of document.querySelectorAll(cfg.item)){
    if(!visible(item))continue;
    if(fragments.length>=100){truncated=true;break;}
    const clone=item.cloneNode(true), originals=[item,...item.querySelectorAll('*')],copies=[clone,...clone.querySelectorAll('*')];
    for(let index=1;index<originals.length;index++){
      if(!visible(originals[index]))copies[index].remove();
    }
    clone.querySelectorAll(cfg.item+', .reply-list, .reply-container, [data-e2e="comment-reply-list"], [data-e2e="comment-replies"]').forEach(el=>el.remove());
    clone.querySelectorAll('script,style,noscript,template,input,textarea,select,form,iframe,object,embed,[contenteditable],#matrix-browser-collector,#matrix-crawl-collector').forEach(el=>el.remove());
    for(const el of [clone,...clone.querySelectorAll('*')]){
      for(const attr of [...el.attributes])if(!allowed.has(attr.name))el.removeAttribute(attr.name);
      if(el.hasAttribute('href')){
        try{
          const url=new URL(el.getAttribute('href'),location.href);
          if(url.protocol!=='https:'||!__HOSTS__.includes(url.hostname)||url.username||url.password)el.removeAttribute('href');
          else el.setAttribute('href',url.origin+url.pathname);
        }catch(_){el.removeAttribute('href');}
      }
    }
    const html=clone.outerHTML;
    if(length+html.length>500000){truncated=true;break;}
    const firstOwn=(choices,excluded=null)=>{
      for(const selector of choices)for(const element of clone.querySelectorAll(selector)){
        if(element.closest(cfg.item)===clone&&(!excluded||!excluded.contains(element))&&element.textContent.trim())return element;
      }
      return null;
    };
    const textElement=firstOwn(cfg.text),authorElement=firstOwn(cfg.author,textElement);
    const authorLink=authorElement?([authorElement.closest('a[href]'),...authorElement.querySelectorAll('a[href]')].find(el=>el&&clone.contains(el)&&(!textElement||!textElement.contains(el)))):null;
    const commentId=__COMMENT_IDS__.map(name=>clone.getAttribute(name)||'').find(Boolean)||'';
    const identity=commentId?'id:'+commentId:JSON.stringify([authorLink?.getAttribute('href')||'',authorElement?.textContent.trim()||'',textElement?.textContent.trim()||'']);
    length+=html.length;fragments.push(html);identities.push(identity);
  }
  return {url:location.href,title:document.title.slice(0,500),fragments,identities,truncated};
})()'''
COMMENT_SCROLL_SCRIPT = r'''(() => {
  const cfg=__CONFIG__, job=window.__matrixCrawlJob;
  if(!job||job.key!==__KEY__||job.stopped||job.paused||location.href!==job.url)return false;
  const visible=el=>el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden';
  for(const panel of document.querySelectorAll(cfg.list+', '+cfg.item)){
    if(!visible(panel))continue;
    let current=panel;
    for(let depth=0;current&&depth<9;depth++,current=current.parentElement){
      if(current===document.body||current===document.documentElement)break;
      const style=getComputedStyle(current);
      if(current.scrollHeight>current.clientHeight+12&&/auto|scroll/.test(style.overflowY)){
        current.scrollBy({top:Math.max(320,Math.min(800,Math.round(current.clientHeight*.8))),behavior:'smooth'});return true;
      }
    }
  }
  if(__PLATFORM__==='xiaohongshu'&&document.scrollingElement&&document.scrollingElement.scrollHeight>innerHeight+12){
    window.scrollBy({top:Math.min(800,Math.round(innerHeight*.7)),behavior:'smooth'});return true;
  }
  return false;
})()'''
COMMENT_PROGRESS_SCRIPT = r'''(() => {
  const job=window.__matrixCrawlJob;
  if(!job||job.key!==__KEY__)return false;
  job.read=__READ__;job.finished=__FINISHED__;
  if(job.finished){const pluginStart=document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start');if(pluginStart)pluginStart.disabled=false;}
  const root=document.getElementById('matrix-crawl-collector')?.shadowRoot;
  if(root){root.getElementById('status').textContent=__TEXT__;if(job.finished){root.getElementById('pause').disabled=true;root.getElementById('stop').disabled=true;}}
  return true;
})()'''


def _safe_content_url(value):
    parsed = urlparse(str(value or ''))
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        return ''
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query)
                       if key.lower() in CONTENT_PARAMETERS and len(value) <= 500])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', query, ''))


def capture_page(page):
    """Read the current Playwright page through the same bounded DOM cleaner."""
    result = page.evaluate(CAPTURE_SCRIPT)
    if not isinstance(result, dict): raise ValueError('页面内容格式无效')
    return {**result, 'html': str(result.get('html') or '')[:MAX_HTML], 'engine': 'plugin'}


def debug_launch_args():
    """An ephemeral loopback CDP port, never a shared default 9222 endpoint."""
    return ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0']


def _runtime():
    configured = os.environ.get('MATRIX_BB_BROWSER_DIR')
    folder = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2]/'dev-tools'/'bb-browser'
    daemon, cli = folder/'dist'/'daemon.js', folder/'dist'/'cli.js'
    if not daemon.is_file() or not cli.is_file():
        raise ValueError('bb-browser 未安装，请设置 MATRIX_BB_BROWSER_DIR 或保留同级 dev-tools/bb-browser')
    candidates = [os.environ.get('MATRIX_NODE_BIN'), shutil.which('node'),
                  str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')]
    candidates.extend(str(p) for p in (Path(__file__).resolve().parents[1]/'venv/lib').glob('python*/site-packages/playwright/driver/node'))
    node = next((value for value in candidates if value and Path(value).is_file() and os.access(value, os.X_OK)), None)
    if not node: raise ValueError('bb-browser 缺少 Node.js 运行环境')
    return folder.resolve(), node, daemon.resolve(), cli.resolve()


def status():
    try:
        folder, node, _, cli = _runtime()
        version = subprocess.run([node, str(cli), '--version'], capture_output=True, text=True,
                                 timeout=5, check=True).stdout.strip()
        return {'available': True, 'engine': 'bb-browser', 'version': version, 'path': str(folder)}
    except Exception as error:
        return {'available': False, 'engine': 'bb-browser', 'version': '',
                'error': str(error) if isinstance(error, ValueError) else 'bb-browser 版本检查失败'}


def _json_request(url, payload=None, token=None, timeout=5):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer '+token
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers=headers)
    # System proxy settings must never redirect local CDP/daemon traffic.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        body = response.read(3000000)
        return json.loads(body)


def _selected_endpoint(page, profile_dir):
    try:
        lines = (Path(profile_dir)/'DevToolsActivePort').read_text().splitlines()
        port = int(lines[0])
        browser_path = lines[1]
        if not 1 <= port <= 65535 or port == 9222 or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9-]+', browser_path):
            raise ValueError('invalid endpoint')
        session = page.context.new_cdp_session(page)
        try: target = session.send('Target.getTargetInfo')['targetInfo']['targetId']
        finally: session.detach()
        base = f'http://127.0.0.1:{port}'
        version = _json_request(base+'/json/version')
        websocket = urlparse(version['webSocketDebuggerUrl'])
        if websocket.hostname not in {'127.0.0.1','localhost'} or websocket.port != port or websocket.path != browser_path:
            raise ValueError('browser mismatch')
        targets = _json_request(base+'/json/list')
        if not any(row.get('id') == target and row.get('type') == 'page' for row in targets):
            raise ValueError('account mismatch')
        return port, target
    except Exception:
        raise ValueError('该账号浏览器未启用独立 bb 通道或档案不匹配，请关闭并重新打开该账号浏览器') from None


class BBPage:
    def __init__(self, page, platform, account_id, profile_dir):
        if platform not in HOSTS: raise ValueError('bb-browser 平台无效')
        self.page, self.platform, self.account_id = page, platform, int(account_id)
        self.profile_dir = str(profile_dir)
        self.process = None
        self.home = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.token = secrets.token_hex(24)
        self.base = None
        self.target_id = None
        self._poll_key = None
        self._crawl_key = None

    def start(self):
        folder, node, daemon, _ = _runtime()
        cdp_port, self.target_id = _selected_endpoint(self.page, self.profile_dir)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.home = tempfile.TemporaryDirectory(prefix=f'matrix-bb-{self.platform}-{self.account_id}-')
        environment = os.environ.copy()
        environment['BB_BROWSER_HOME'] = self.home.name
        self.base = f'http://127.0.0.1:{port}'
        self.process = subprocess.Popen([node, str(daemon), '--no-chrome', '--host', '127.0.0.1',
            '--port', str(port), '--cdp-host', '127.0.0.1', '--cdp-port', str(cdp_port), '--token', self.token],
            cwd=str(folder), env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            if self.process.poll() is not None: break
            try:
                result = self._request('/status', timeout=1)
                if result.get('cdpConnected') and result.get('cdpPort') == cdp_port:
                    if any(tab.get('targetId') == self.target_id for tab in result.get('tabs', [])):
                        return self
            except Exception:
                pass
            self.page.wait_for_timeout(100)
        raise ValueError('bb-browser 独立通道启动失败，请检查本地运行环境后重试')

    def _request(self, path, payload=None, timeout=25):
        future = self.executor.submit(_json_request, self.base+path, payload, self.token, timeout)
        while not future.done():
            self.page.wait_for_timeout(30)
        return future.result()

    def _command(self, method, **parameters):
        result = self._request('/command', {'method': method, 'tabId': self.target_id, **parameters})
        if result.get('error') or 'result' not in result:
            # Do not echo daemon scripts, auth tokens, or page content in errors.
            raise ValueError('bb-browser 当前标签页操作失败，请检查该账号页面后重试')
        return result['result']

    def _eval(self, expression):
        return self._command('eval', script=expression).get('result')

    @property
    def url(self): return self._eval('location.href')

    def goto(self, url, wait_until='domcontentloaded', timeout=30000):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS[self.platform] or parsed.username or parsed.password:
            raise ValueError('请使用当前账号所属平台的 HTTPS 地址')
        self._command('goto', url=url)
        deadline = time.monotonic()+timeout/1000
        while time.monotonic() < deadline:
            if self._eval("document.readyState !== 'loading'"): return
            self.page.wait_for_timeout(100)
        raise ValueError('bb-browser 页面加载超时')

    def wait_for_function(self, expression, timeout=15000):
        if expression != PLUGIN_READY: raise ValueError('仅允许等待本地采集插件就绪')
        deadline = time.monotonic()+timeout/1000
        while time.monotonic() < deadline:
            if self._eval(expression): return True
            self.page.wait_for_timeout(100)
        raise ValueError('浏览器采集插件未就绪')

    def wait_for_timeout(self, milliseconds):
        self.page.wait_for_timeout(min(max(int(milliseconds),0),10000))

    def scroll(self, pixels=480):
        pixels = int(pixels)
        if not -1000 <= pixels <= 1000: raise ValueError('单次滚动幅度应在 -1000 到 1000 像素之间')
        return self._eval(f'window.scrollBy(0,{pixels}); true')

    def evaluate(self, expression, arg=None):
        from .browser_plugin_bridge import SCAN_SCRIPT
        if expression != SCAN_SCRIPT:
            match = re.fullmatch(r'(?:\(\)\s*=>\s*)?window\.scrollBy\(0,\s*(-?\d+)\)\s*;?', expression.strip())
            if match: return self.scroll(int(match.group(1)))
            raise ValueError('bb-browser 仅允许插件协议或受控滚动操作')
        options = dict(arg)
        key = '__matrix_bb_'+secrets.token_hex(16)
        self._poll_key = key
        # The installed daemon has a fixed 30-second command deadline. Start the
        # plugin Promise in-page and poll its bounded result instead of keeping
        # one daemon HTTP command open through a long pause.
        starter = ("(() => { const key="+json.dumps(key)+"; window[key]={done:false}; "
                   "Promise.resolve(("+SCAN_SCRIPT+")("+json.dumps(options)+"))"
                   ".then(result=>window[key]={done:true,result},()=>window[key]={done:true,error:true}); return true; })()")
        self._eval(starter)
        deadline = time.monotonic()+(options['timeoutMs']+options.get('stopGraceMs',2000))/1000+10
        try:
            while time.monotonic() < deadline:
                state = self._eval('window['+json.dumps(key)+']')
                if isinstance(state, dict) and state.get('done'):
                    if state.get('error'): raise ValueError('插件采集失败')
                    return state.get('result')
                self.page.wait_for_timeout(100)
            self._eval("window.postMessage("+json.dumps({'type':'MATRIX_PLUGIN_CONTROL','requestId':options['requestId'],
                'action':'stop','reason':'timeout'})+",location.origin); true")
            raise ValueError('bb-browser 插件采集等待超时')
        finally:
            try: self._eval('delete window['+json.dumps(key)+']; true')
            except Exception: pass
            self._poll_key = None

    def start_comment_job(self):
        """Attach a visible pause/stop control, without running the plugin scan."""
        from .comment_selectors import comment_source_url
        comment_source_url(self.url, self.platform)
        self._crawl_key = secrets.token_hex(16)
        result = self._eval(COMMENT_JOB_SCRIPT.replace('__KEY__', json.dumps(self._crawl_key)))
        if not isinstance(result, dict) or not result.get('ok'):
            self._crawl_key = None
            raise ValueError((result or {}).get('error', '爬虫采集面板启动失败'))
        return result

    def _comment_script(self, script):
        from .comment_selectors import COMMENT_SELECTORS, PLATFORM_HOSTS, PUBLIC_COMMENT_ATTRIBUTES, COMMENT_ID_ATTRIBUTES
        if not self._crawl_key: raise ValueError('请先启动评论爬虫任务')
        return (script.replace('__KEY__', json.dumps(self._crawl_key))
                .replace('__CONFIG__', json.dumps(COMMENT_SELECTORS[self.platform], ensure_ascii=False))
                .replace('__ATTRIBUTES__', json.dumps(sorted(PUBLIC_COMMENT_ATTRIBUTES)))
                .replace('__COMMENT_IDS__', json.dumps(COMMENT_ID_ATTRIBUTES))
                .replace('__HOSTS__', json.dumps(sorted(PLATFORM_HOSTS[self.platform])))
                .replace('__PLATFORM__', json.dumps(self.platform)))

    def comment_state(self):
        result = self._eval(self._comment_script(COMMENT_STATE_SCRIPT))
        if not isinstance(result, dict): raise ValueError('爬虫采集状态无效')
        return result

    def comment_snapshot(self):
        if self.comment_state().get('stopped'): return {'fragments': [], 'stopped': True}
        result = self._eval(self._comment_script(COMMENT_SNAPSHOT_SCRIPT))
        if not isinstance(result, dict) or not isinstance(result.get('fragments'), list):
            raise ValueError('评论区快照格式无效')
        fragments = result['fragments']
        if len(fragments) > 100 or any(not isinstance(row, str) for row in fragments):
            raise ValueError('评论区快照超出限制')
        if sum(map(len, fragments)) > MAX_HTML: raise ValueError('评论区快照超出大小限制')
        from .comment_selectors import comment_source_url
        return {**result, 'url': comment_source_url(result.get('url'), self.platform), 'engine': 'bb-browser'}

    def scroll_comments(self):
        return self._eval(self._comment_script(COMMENT_SCROLL_SCRIPT)) is True

    def comment_progress(self, read, text, finished=False):
        read = min(100, max(0, int(read)))
        script = (self._comment_script(COMMENT_PROGRESS_SCRIPT)
                  .replace('__READ__', str(read)).replace('__TEXT__', json.dumps(str(text)[:300], ensure_ascii=False))
                  .replace('__FINISHED__', 'true' if finished else 'false'))
        return self._eval(script)

    def snapshot(self):
        result = self._command('snap', interactive=False, compact=True, maxDepth=7)
        text = str((result.get('snapshotData') or {}).get('snapshot') or '')
        # Do not propagate form values / credential fields into the workstation.
        text = '\n'.join(line for line in text.splitlines()
                         if not re.search(r'input|textbox|textarea|password|matrix-browser-collector', line, re.I))[:20000]
        safe_url = _safe_content_url(result.get('url'))
        return {'url': safe_url, 'title': result.get('title',''), 'snapshot': text,
                'engine':'bb-browser','platform':self.platform,'account_id':self.account_id}

    def capture(self):
        result = self._eval(CAPTURE_SCRIPT)
        if not isinstance(result, dict): raise ValueError('bb-browser 页面内容格式无效')
        return {**result, 'html':str(result.get('html') or '')[:MAX_HTML], 'engine':'bb-browser',
                'platform':self.platform,'account_id':self.account_id}

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try: _json_request(self.base+'/shutdown', {}, self.token, timeout=2)
            except Exception: pass
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=2)
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.home is not None: self.home.cleanup()


@contextmanager
def bb_page(page, platform, account_id, profile_dir):
    adapter = BBPage(page, platform, account_id, profile_dir)
    try:
        adapter.start()
        yield adapter
    finally:
        adapter.close()
