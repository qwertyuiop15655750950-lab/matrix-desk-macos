/* Run: node --test tests/test_workspace_ui.js
 * macOS bundled runtime: venv/lib/python3.12/site-packages/playwright/driver/node --test tests/test_workspace_ui.js
 * Uses the actual UI scripts and modal implementation, a small DOM adapter and in-memory APIs.
 * No network requests, browser launch, database writes or downloads occur.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appRoot = path.resolve(__dirname, '..');
const decode = value => String(value).replace(/&(amp|lt|gt|quot|#39);/g, (_, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" }[key]));
const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

class Element {
  constructor(document, tag, attrs = {}, parent = null) {
    this.document = document; this.tagName = tag; this.attrs = attrs; this.parent = parent;
    this.children = []; this.isConnected = true; this.dataset = {};
    this.value = attrs.value || ''; this.disabled = 'disabled' in attrs; this.checked = 'checked' in attrs;
    this.readOnly = false; this.textContent = ''; this._html = '';
    for (const [key, value] of Object.entries(attrs)) if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    document.elements.add(this);
  }
  get id() { return this.attrs.id; }
  get name() { return this.attrs.name; }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    const detach = element => { element.isConnected = false; element.children.forEach(detach); };
    this.children.forEach(detach); this.children = []; this._html = String(html);
    const stack = [this], tokens = this._html.match(/<[^>]+>|[^<]+/g) || [];
    for (const token of tokens) {
      if (token.startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      if (token.startsWith('<')) {
        const start = token.match(/^<([\w-]+)([^>]*)>/); if (!start) continue;
        const attrs = {}, pattern = /([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
        let match; while ((match = pattern.exec(start[2]))) attrs[match[1]] = decode(match[2] ?? match[3] ?? match[4] ?? '');
        const parent = stack[stack.length - 1], child = new Element(this.document, start[1], attrs, parent);
        parent.children.push(child);
        if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(start[1])) stack.push(child);
      } else {
        const node = stack[stack.length - 1]; node.textContent += decode(token);
        if (node.tagName === 'textarea') node.value += decode(token);
      }
    }
    for (const node of this.document.elements) if (node.isConnected && node.tagName === 'select') {
      const options = node.children.filter(child => child.tagName === 'option');
      const selected = options.find(child => 'selected' in child.attrs) || options[0];
      if (selected) node.value = selected.attrs.value ?? selected.textContent;
    }
  }
  querySelectorAll(selector) { return this.document.querySelectorAll(selector).filter(element => { for (let parent = element.parent; parent; parent = parent.parent) if (parent === this) return true; return false; }); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class Document {
  constructor() {
    this.elements = new Set(); this.listeners = {};
    new Element(this, 'div', { id: 'page' }); new Element(this, 'div', { id: 'modal-root' });
  }
  getElementById(id) { return [...this.elements].find(element => element.isConnected && element.id === id) || null; }
  matches(element, selector) {
    if (selector.startsWith('#')) return element.id === selector.slice(1);
    if (selector.startsWith('.')) return (element.attrs.class || '').split(/\s+/).includes(selector.slice(1));
    const attr = selector.match(/^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
    return attr ? attr[1] in element.attrs && (attr[2] == null || element.attrs[attr[1]] === attr[2]) : element.tagName === selector;
  }
  querySelectorAll(selector) {
    const parts = selector.trim().split(/\s+/), last = parts.pop();
    return [...this.elements].filter(element => {
      if (!element.isConnected || !this.matches(element, last)) return false;
      let parent = element.parent;
      for (let index = parts.length - 1; index >= 0; index--) {
        while (parent && !this.matches(parent, parts[index])) parent = parent.parent;
        if (!parent) return false; parent = parent.parent;
      }
      return true;
    });
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) { (this.listeners[type] ||= []).push(listener); }
  async change(input) { await Promise.all((this.listeners.change || []).map(listener => listener({ target: input }))); }
}

function runtime(overrides = {}, accounts = null) {
  const document = new Document(), calls = [], toasts = [], infos = [], downloads = [];
  const timers = new Map(); let timerId = 0;
  const items = accounts || [
    { id: 1, key: 'xiaohongshu:1', platform: 'xiaohongshu', nickname: '<小红书甲>', running: false },
    { id: 2, key: 'xiaohongshu:2', platform: 'xiaohongshu', nickname: '小红书乙', running: true },
    { id: 1, key: 'douyin:1', platform: 'douyin', nickname: '抖音甲', running: false },
    { id: 2, key: 'douyin:2', platform: 'douyin', nickname: '抖音乙', running: false },
    { id: 1, key: 'channels:1', platform: 'channels', nickname: '视频号甲', running: false },
  ];
  const defaults = {
    hub_accounts: (platform='') => ({ ok: true, items: items.filter(item=>!platform||item.platform===platform).map(item => ({ ...item })), counts: { xiaohongshu: 2, douyin: 2, channels: 1 } }),
    metrics_get: () => ({ok:true,config:{},items:[],identities:[]}),
    metrics_save: () => ({ok:true}),metrics_run:()=>({ok:true,id:25}),
    dy_accounts: () => ({ ok: true, items: items.filter(item => item.platform === 'douyin') }),
    dy_comments: () => ({ ok: true, items: [] }), dy_leads: () => ({ ok: true, items: [] }),
    dy_keywords: () => ({ ok: true, items: ['咨询'] }), hub_collected: () => ({ ok: true, items: [] }),
    hub_open: async (platform, id) => { await Promise.resolve(); const item = items.find(item => item.platform === platform && item.id === id); item.running = true; return { ok: true }; },
    hub_collect: () => ({ ok: true, read: 5, fetched: 2, added: 1, missing_ids: 1 }),
    dy_scan_video: () => ({ ok: true, read: 5, fetched: 2, added: 1, duplicates: 1 }),
    dy_export: () => ({ ok: true, csv: 'nickname,content\r\n', count: 0 }),
    tool_status: () => ({ ok: true, tools: { 'bb-browser': { available: true, version: 'test' }, crawl4ai: { available: true, version: 'test' } } }),
    material_list: () => ({ ok: true, items: [] }),
    material_capture: () => ({ ok: true, id: 4, added: 1, title: '当前页正文', source_url: 'https://www.douyin.com/video/example' }),
    material_get: id => ({ ok: true, item: { id, platform: 'douyin', account_id: 1, title: '正文', source_url: 'https://www.douyin.com/video/example', markdown: 'paragraph', links: [] } }),
    material_to_draft: () => ({ ok: true, id: 31 }),
    warmup_targets: () => ({ ok: true, items: [
      { kind: 'browser', key: 'douyin:1', platform: 'douyin', id: 1, name: '抖音账号', ready: true },
      { kind: 'browser', key: 'xiaohongshu:1', platform: 'xiaohongshu', id: 1, name: '小红书账号', ready: false },
      { kind: 'android', key: 'android:TEST-DEVICE', serial: 'TEST-DEVICE', name: '测试手机', ready: true },
    ] }),
    warmup_list: () => ({ ok: true, items: [] }),
    warmup_create: () => ({ ok: true, id: 15 }),
    warmup_action: () => ({ ok: true }),
    batch_list: () => ({ok:true,items:[]}),
    batch_preview: () => ({ok:true,items:[{platform:'douyin',kind:'profile',canonical:'https://www.douyin.com/user/test'}],duplicates:0,rejected:0}),
    batch_create: () => ({ok:true,id:21}),
    batch_action: () => ({ok:true,status:'paused'}),
    batch_export: () => ({ok:true,csv:'content\n',count:0}),
  };
  const context = vm.createContext({
    document, window: {}, URL, NAV: [{ id: 'dashboard' }, { id: 'accounts' }, { id: 'drafts' }], TITLES: {}, RENDER: { drafts: async () => { document.getElementById('page').innerHTML = 'drafts view'; } }, curSection: 'account_hub', esc: escape,
    toast: (message, error) => toasts.push({ message, error }), download: (name, text) => downloads.push({ name, text }), copyText: () => {},
    setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; }, clearTimeout: id => timers.delete(id),
    api: async (method, ...args) => { calls.push({ method, args }); const handler = overrides[method] || defaults[method]; if (!handler) throw Error(`Unexpected API ${method}`); return await handler(...args); },
  });
  const app = fs.readFileSync(path.join(appRoot, 'app/ui/app.js'), 'utf8');
  const mockAPI = context.api;
  vm.runInContext(app.slice(app.indexOf('function api('), app.indexOf('/* ---------- 导航 ---------- */')), context);
  context.api = mockAPI;
  context.toast = (message, error) => toasts.push({ message, error });
  context.download = (name, text) => downloads.push({ name, text });
  context.copyText = () => {};
  const actualInfo = context.showInfo;
  context.showInfo = (title, message) => { infos.push({ title, message }); actualInfo(title, message); };
  for (const filename of ['douyin.js', 'account_hub.js', 'account_metrics.js', 'materials.js', 'warmup.js', 'batch_collect.js']) vm.runInContext(fs.readFileSync(path.join(appRoot, 'app/ui', filename), 'utf8'), context, { filename });
  const run = code => vm.runInContext(code, context);
  context.showSection = async section => { context.curSection = section; await context.RENDER[section](); };
  const click = async element => { assert.ok(element && element.isConnected, 'control must exist'); if (!element.disabled && element.onclick) await element.onclick(); };
  const close = async () => { const button = document.querySelector('#modal-root [data-x="cancel"]') || document.querySelector('#modal-root [data-x="ok"]'); await click(button); };
  const fill = fields => { for (const [name, value] of Object.entries(fields)) { const input = document.querySelector(`#modal-root [name="${name}"]`); assert.ok(input, name); input.value = value; } };
  return { document, context, calls, toasts, infos, downloads, items, run, click, close, fill, overrides, timers };
}

test('Douyin cannot overwrite a different section after either async fetch', async () => {
  for (const method of ['dy_accounts', 'dy_comments']) {
    const pending = deferred(), ui = runtime({ [method]: () => pending.promise });
    ui.run("curSection='douyin';dyTab='comments'");
    const render = ui.run('renderDouyin()'); await settle();
    ui.context.curSection = 'dashboard'; ui.document.getElementById('page').innerHTML = '<div>current dashboard</div>';
    pending.resolve({ ok: true, items: [] }); await render;
    assert.equal(ui.document.getElementById('page').innerHTML, '<div>current dashboard</div>');
  }
});

test('newest account responses win and old hub data cannot clear selections', async () => {
  const old = deferred(); let count = 0;
  const ui = runtime({ hub_accounts: () => ++count === 1 ? old.promise : { ok: true, items: [{ id: 1, key: 'douyin:1', platform: 'douyin', nickname: 'new', running: true }], counts: {} } });
  const stale = ui.run('renderAccountHub()'); await ui.run('renderAccountHub()');
  ui.run("hubSelected.add('douyin:1')"); old.resolve({ ok: true, items: [], counts: {} }); await stale;
  assert.equal(ui.run('hubSelected.size'), 1); assert.match(ui.document.getElementById('page').innerHTML, /new/);
  const dyOld = deferred(); let dyCount = 0;
  ui.overrides.dy_accounts = () => ++dyCount === 1 ? dyOld.promise : { ok: true, items: [{ id: 9, nickname: 'latest account' }] };
  ui.run("curSection='douyin';dyTab='accounts'"); const oldRender = ui.run('renderDouyin()'); await ui.run('renderDouyin()');
  dyOld.resolve({ ok: true, items: [{ id: 8, nickname: 'stale account' }] }); await oldRender;
  assert.match(ui.document.getElementById('dy-content').innerHTML, /latest account/);
  assert.doesNotMatch(ui.document.getElementById('dy-content').innerHTML, /stale account/);
});

test('collect navigation alias works and initial load errors offer a handled retry', async () => {
  const ui = runtime(); ui.context.curSection = 'collect'; await ui.context.RENDER.collect();
  assert.ok(ui.document.getElementById('dy-browser-scan'));
  ui.overrides.dy_accounts = () => { throw Error('offline bridge'); }; await ui.context.RENDER.collect();
  assert.ok(ui.document.getElementById('dy-retry')); assert.match(ui.toasts.at(-1).message, /offline bridge/);
  ui.context.curSection = 'account_hub'; ui.overrides.hub_accounts = () => ({ ok: false, error: '<failed>' });
  await ui.context.RENDER.account_hub(); assert.ok(ui.document.getElementById('hub-retry')); assert.match(ui.document.getElementById('page').innerHTML, /&lt;failed&gt;/);
});

test('batch skips open accounts, opens at most three sequentially and retains cross-platform selections', async () => {
  let active = 0, maximum = 0; const ui = runtime();
  ui.overrides.hub_open = async (platform, id) => { active++; maximum = Math.max(maximum, active); await settle(); ui.items.find(item => item.platform === platform && item.id === id).running = true; active--; return { ok: true }; };
  await ui.run('renderAccountHub()'); ui.run("hubItems.forEach(a=>hubSelected.add(hubKey(a)));hubPlatform='douyin';hubDraw()");
  await ui.click(ui.document.getElementById('hub-batch'));
  const opened = ui.calls.filter(call => call.method === 'hub_open');
  assert.equal(opened.length, 3); assert.equal(maximum, 1); assert.equal(ui.run('hubSelected.size'), 5); assert.equal(ui.run('hubWaitingItems().length'), 1);
  assert.ok(!opened.some(call => call.args[0] === 'xiaohongshu' && call.args[1] === 2));
  ui.run("hubPlatform='';hubDraw()"); assert.match(ui.document.getElementById('page').innerHTML, /&lt;小红书甲&gt;/);
  assert.match(ui.document.getElementById('hub-progress').textContent, /3\/3/);
});

test('one failed browser does not stop later batch entries or strand busy state', async () => {
  const ui = runtime({ hub_open: (platform, id) => platform === 'douyin' && id === 1 ? { ok: false, error: 'launch failed' } : { ok: true } });
  await ui.run('renderAccountHub()'); ui.run('hubItems.forEach(a=>hubSelected.add(hubKey(a)));hubDraw()');
  await ui.click(ui.document.getElementById('hub-batch'));
  assert.equal(ui.calls.filter(call => call.method === 'hub_open').length, 3);
  assert.equal(ui.run('hubBusy'), false); assert.equal(ui.run('workspaceBrowserBusy()'), false);
  assert.match(ui.infos.at(-1).message, /2\/3/); assert.match(ui.infos.at(-1).message, /launch failed/);
});

test('hub task blocks duplicate Douyin tasks across navigation and releases recreated controls', async () => {
  const pending = deferred(), ui = runtime({ hub_open: () => pending.promise });
  await ui.run('renderAccountHub()'); const opening = ui.run('hubOpenAccounts([hubItems[0]])'); await settle();
  ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()');
  assert.equal(ui.document.getElementById('dy-browser-scan').disabled, true);
  await ui.document.getElementById('dy-browser-scan').onclick();
  assert.equal(ui.calls.filter(call => call.method === 'dy_scan_video').length, 0);
  await ui.run('renderDouyin()'); assert.equal(ui.document.getElementById('dy-browser-scan').disabled, true);
  await assert.rejects(ui.run('hubOpenAccounts([hubItems[0]])'), /任务/);
  pending.resolve({ ok: true }); await opening;
  assert.equal(ui.document.getElementById('dy-browser-scan').disabled, false);
  assert.ok(ui.document.getElementById('dy-content'));
});

test('Douyin collection blocks hub opens and navigation does not replace a pending dialog', async () => {
  const pending = deferred(), ui = runtime({ dy_scan_video: () => pending.promise });
  ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()');
  const scan = ui.document.getElementById('dy-browser-scan').onclick(); await settle();
  ui.fill({ aid: '1', url: 'https://www.douyin.com/video/example' }); await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await settle();
  ui.context.curSection = 'account_hub'; await ui.run('renderAccountHub()'); ui.run('hubSelected.add(hubKey(hubItems[0]));hubDraw()');
  assert.equal(ui.document.getElementById('hub-batch').disabled, true); await assert.rejects(ui.run('hubOpenAccounts([hubItems[0]])'), /任务/);
  const importDialog = ui.run('hubImportModal()'); await settle(); const textarea = ui.document.getElementById('hub-import-text');
  pending.resolve({ ok: true, read: 8, fetched: 2, added: 1, duplicates: 1 }); await scan;
  assert.equal(ui.document.getElementById('hub-import-text'), textarea); assert.equal(ui.document.getElementById('hub-batch').disabled, false);
  assert.match(ui.toasts.map(toast => toast.message).join('\n'), /采集完成/);
  await ui.close(); await importDialog;
});

test('hub reserves collection while saving keywords, and refresh failure preserves successful result', async () => {
  const pending = deferred(), ui = runtime();
  ui.overrides.dy_keywords = (...args) => args.length ? pending.promise : { ok: true, items: ['咨询'] };
  await ui.run('renderAccountHub()'); const scan = ui.run('hubCollectModal(hubItems[0])'); await settle();
  ui.fill({ url: 'https://www.xiaohongshu.com/explore/example', keywords: '咨询' }); await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await settle();
  assert.equal(ui.run('workspaceBrowserBusy()'), true); await assert.rejects(ui.run('hubOpenAccounts([hubItems[0]])'), /任务/);
  ui.overrides.hub_accounts = () => { throw Error('refresh unavailable'); }; pending.resolve({ ok: true, items: ['咨询'] }); await scan;
  assert.match(ui.run('hubCollectProgress'), /采集完成/); assert.doesNotMatch(ui.run('hubCollectProgress'), /未完成/);
  assert.match(ui.infos.at(-1).message, /新增归档 1 条/); assert.match(ui.toasts.at(-1).message, /已保存.*刷新失败/);
  assert.equal(ui.run('workspaceBrowserBusy()'), false);
});

test('content URL validation rejects other platforms, credentials and insecure links', () => {
  const ui = runtime();
  for (const url of ['http://www.douyin.com/video/1', 'https://evil.invalid/douyin.com', 'https://www.xiaohongshu.com/explore/1', 'https://user@www.douyin.com/video/1', 'https://www.douyin.com:9999/video/1']) {
    ui.context.url = url; assert.throws(() => ui.run("workspaceContentURL(url,'douyin')"), /HTTPS/);
  }
  assert.equal(ui.run("workspaceContentURL('https://www.douyin.com/video/1','douyin')"), 'https://www.douyin.com/video/1');
  for(const host of ['rednote.com','www.rednote.com']) {
    ui.context.url=`https://${host}/explore/1`;
    assert.equal(ui.run("workspaceContentURL(url,'xiaohongshu')"),ui.context.url);
    assert.throws(()=>ui.run("workspaceContentURL(url,'douyin')"),/HTTPS/);
  }
  ui.context.url='https://www.rednote.com.evil.test/explore/1';
  assert.throws(()=>ui.run("workspaceContentURL(url,'xiaohongshu')"),/HTTPS/);
});

for (const platform of ['hub', 'douyin']) {
  test(`${platform} file import keeps the newest file, blocks early submit and survives cancel`, async () => {
    const ui = runtime(); let importing;
    if (platform === 'hub') importing = ui.run('hubImportModal()');
    else { ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()'); importing = ui.document.getElementById('dy-import').onclick(); }
    await settle();
    const input = ui.document.getElementById(platform === 'hub' ? 'hub-import-file' : 'dy-file');
    const textarea = ui.document.getElementById(platform === 'hub' ? 'hub-import-text' : 'dy-import-text');
    const ok = ui.document.querySelector('#modal-root [data-x="ok"]'), old = deferred(), latest = deferred();
    input.files = [{ name: 'old.csv', size: 20, text: () => old.promise }]; const oldRead = ui.document.change(input);
    input.files = [{ name: 'latest.csv', size: 20, text: () => latest.promise }]; const newRead = ui.document.change(input);
    assert.equal(ok.disabled, true); assert.equal(textarea.readOnly, true);
    await ui.click(ok); assert.ok(ui.document.getElementById(textarea.id));
    old.resolve('old data'); await oldRead; assert.equal(textarea.value, ''); assert.equal(ok.disabled, true);
    latest.resolve('latest data'); await newRead; assert.equal(textarea.value, 'latest data'); assert.equal(ok.disabled, false);
    const canceled = deferred(); input.files = [{ name: 'canceled.csv', size: 20, text: () => canceled.promise }]; const canceledRead = ui.document.change(input);
    await ui.close(); await importing; canceled.resolve('late data'); await canceledRead;
    assert.equal(textarea.isConnected, false); assert.equal(textarea.value, '');
    assert.equal(ui.calls.filter(call => ['hub_import', 'dy_import'].includes(call.method)).length, 0);
  });
}

test('failed and oversized file reads clear old content and remain recoverable', async () => {
  const ui = runtime(); const importing = ui.run('hubImportModal()'); await settle();
  const input = ui.document.getElementById('hub-import-file'), textarea = ui.document.getElementById('hub-import-text');
  textarea.value = 'old content'; input.files = [{ name: 'broken.csv', size: 20, text: async () => { throw Error('read denied'); } }]; await ui.document.change(input);
  assert.equal(textarea.value, ''); assert.equal(textarea.readOnly, false); assert.equal(ui.document.querySelector('#modal-root [data-x="ok"]').disabled, false);
  assert.match(ui.toasts.at(-1).message, /read denied/);
  textarea.value = 'old content'; input.files = [{ name: 'big.csv', size: 11 * 1024 * 1024, text: () => { throw Error('must not read'); } }]; await ui.document.change(input);
  assert.equal(textarea.value, ''); assert.match(ui.toasts.at(-1).message, /10 MB/); await ui.close(); await importing;
});

test('another dialog cannot orphan an active import', async () => {
  const ui = runtime(); const importing = ui.run('hubImportModal()'); await settle();
  const original = ui.document.getElementById('hub-import-text'); await assert.rejects(ui.run('hubAccountModal()'), /关闭当前弹窗/);
  assert.equal(ui.document.getElementById('hub-import-text'), original); await ui.close(); await importing;
});

test('CSV export protects formulas and archives export only supplied fields without fabricating IDs', async () => {
  const row = { nickname: '=name', user_id: '', keyword: '咨询', source_url: 'https://www.xiaohongshu.com/explore/example', comment_text: '  +formula,"quoted"\nline', created_at: '2026-10-08', internal: 'excluded' };
  const ui = runtime({ hub_collected: () => ({ ok: true, items: [row] }) });
  assert.equal(ui.run("hubCSV([['a,b','a\"b','=1+1','  @x']])"), '"a,b","a""b","\'=1+1","\'  @x"');
  const archive = ui.run('hubArchiveModal()'); await settle();
  await ui.click(ui.document.getElementById('hub-archive-csv')); await ui.click(ui.document.getElementById('hub-archive-json'));
  assert.match(ui.downloads[0].text, /'=name/); assert.match(ui.downloads[0].text, /'  \+formula/);
  const json = JSON.parse(ui.downloads[1].text); assert.equal(json[0].user_id, ''); assert.equal(json[0].comment_text, row.comment_text); assert.equal('internal' in json[0], false);
  await ui.close(); await archive;
});

test('comment export uses current filter arguments and malformed stored words do not break the page', async () => {
  const ui = runtime({ dy_comments: () => ({ ok: true, items: [{ nickname: '<safe>', content: 'example', matched_words: '{broken' }] }) });
  ui.run("curSection='douyin';dyTab='comments';dyAccount='1';dyQuery='咨询';dyMatched=true"); await ui.run('renderDouyin()');
  assert.match(ui.document.getElementById('dy-content').innerHTML, /&lt;safe&gt;/); await ui.click(ui.document.getElementById('dy-export'));
  assert.deepEqual(ui.calls.find(call => call.method === 'dy_export').args, ['comments', '1', '咨询', true]); assert.equal(ui.downloads[0].name, '抖音评论.csv');
});

test('late failed navigation requests cannot replace a newer successful view', async () => {
  for (const module of ['hub', 'douyin']) {
    const pending = deferred(); let count = 0;
    const method = module === 'hub' ? 'hub_accounts' : 'dy_accounts';
    const ui = runtime({ [method]: () => ++count === 1 ? pending.promise : { ok: true, items: [{ id: 1, key: 'douyin:1', platform: 'douyin', nickname: 'new success', running: false }], counts: {} } });
    if (module === 'douyin') ui.run("curSection='douyin';dyTab='accounts'");
    const renderer = module === 'hub' ? 'hubRenderView()' : 'dyRenderView()';
    const stale = ui.run(renderer); await ui.run(renderer);
    pending.reject(Error('old failure')); await stale;
    const rendered = module === 'hub' ? ui.document.getElementById('page') : ui.document.getElementById('dy-content');
    assert.match(rendered.innerHTML, /new success/); assert.equal(ui.document.getElementById(module === 'hub' ? 'hub-retry' : 'dy-retry'), null);
  }
});

test('legacy modal replacement cancels the old Promise and stale controls cannot close the new modal', async () => {
  const ui = runtime();
  const first = ui.context.openModal('first', '<input name="value">');
  const oldCancel = ui.document.querySelector('#modal-root [data-x="cancel"]');
  const second = ui.context.openModal('second', '<input name="value" value="new">');
  assert.equal(await first, null);
  oldCancel.onclick(); assert.ok(ui.document.querySelector('#modal-root [name="value"]'));
  await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); assert.equal((await second).value, 'new');
});

test('legacy showInfo replacement resolves workspace dialog and releases its lock', async () => {
  const ui = runtime(); const importing = ui.run('hubImportModal()'); await settle();
  ui.context.showInfo('legacy result', 'completed'); await importing;
  assert.equal(ui.context.window.matrixWorkspaceModalBusy, false); assert.equal(ui.document.getElementById('hub-import-text'), null);
  assert.equal(ui.calls.filter(call => call.method === 'hub_import').length, 0); await ui.close();
  const next = ui.run('hubImportModal()'); await settle(); assert.ok(ui.document.getElementById('hub-import-text')); await ui.close(); await next;
});

test('timeout partial results remain successful and explicitly report that they were saved in both UIs', async () => {
  for (const module of ['hub', 'douyin']) {
    const result = { ok: true, timed_out: true, stopped: true, read: 8, fetched: 2, added: 1, duplicates: 1 };
    const ui = runtime({ hub_collect: () => result, dy_scan_video: () => result }); let scan;
    if (module === 'hub') { await ui.run('renderAccountHub()'); scan = ui.run('hubCollectModal(hubItems[0])'); }
    else { ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()'); scan = ui.document.getElementById('dy-browser-scan').onclick(); }
    await settle(); ui.fill(module === 'hub' ? { url: 'https://www.xiaohongshu.com/explore/example', keywords: '咨询' } : { aid: '1', url: 'https://www.douyin.com/video/example' });
    await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await scan;
    assert.match(ui.infos.at(-1).message, /采集超时停止，已保存当前结果/); assert.match(ui.infos.at(-1).message, /新增(?:归档)? 1 条/);
    assert.equal(ui.run('workspaceBrowserBusy()'), false); assert.ok(!ui.toasts.some(toast => toast.error));
  }
});

test('failed Douyin collection releases recreated controls and shows the error in persistent progress', async () => {
  const pending = deferred(), ui = runtime({ dy_scan_video: () => pending.promise });
  ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()');
  const scan = ui.document.getElementById('dy-browser-scan').onclick(); await settle();
  ui.fill({ aid: '1', url: 'https://www.douyin.com/video/example' }); await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await settle();
  await ui.run('renderDouyin()'); assert.equal(ui.document.getElementById('dy-browser-scan').disabled, true);
  pending.reject(Error('plugin unavailable')); await scan;
  assert.equal(ui.document.getElementById('dy-browser-scan').disabled, false); assert.match(ui.document.getElementById('dy-scan-progress').textContent, /采集未完成：plugin unavailable/);
  assert.match(ui.toasts.at(-1).message, /plugin unavailable/); assert.equal(ui.run('workspaceBrowserBusy()'), false);
});

test('both comment collection UIs use the self-owned crawler and pass selected channels', async () => {
  for (const module of ['hub', 'douyin']) for (const chosen of [null, 'plugin']) {
    const ui = runtime(); let scan;
    if (module === 'hub') { await ui.run('renderAccountHub()'); scan = ui.run('hubCollectModal(hubItems[0])'); }
    else { ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()'); scan = ui.document.getElementById('dy-browser-scan').onclick(); }
    await settle(); const selector = ui.document.querySelector('#modal-root [name="engine"]'); assert.equal(selector.value, 'builtin');
    assert.match(selector.children.map(option => option.textContent).join(' '), /自有评论采集器/);
    ui.fill(module === 'hub' ? { url: 'https://www.xiaohongshu.com/explore/example', keywords: '咨询' } : { aid: '1', url: 'https://www.douyin.com/video/example' });
    if (chosen) ui.fill({ engine: chosen }); await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await scan;
    const call = ui.calls.find(call => call.method === (module === 'hub' ? 'hub_collect' : 'dy_scan_video'));
    assert.equal(call.args.at(-1), chosen || 'builtin');
  }
});

test('all three platforms expose current-page capture, default bb-browser and preserve duplicate material IDs', async () => {
  for (const platform of ['xiaohongshu', 'douyin', 'channels']) {
    let count = 0;
    const ui = runtime({ material_capture: () => ({ ok: true, id: 4, added: ++count === 1, title: '已打开的页面', source_url: 'https://example.invalid/current' }) });
    await ui.run('renderAccountHub()');
    for (const chosen of [null, 'plugin']) {
      const capture = ui.document.querySelector(`[data-hub-material="${platform}:1"]`).onclick(); await settle();
      assert.equal(ui.document.querySelector('#modal-root [name="engine"]').value, 'bb-browser');
      ui.fill({ selector: '.article-body' }); if (chosen) ui.fill({ engine: chosen });
      await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await capture;
      assert.equal(ui.context.curSection, 'account_hub'); assert.equal(ui.calls.filter(call => call.method === 'hub_open').length, 0);
      const call = ui.calls.filter(call => call.method === 'material_capture').at(-1);
      assert.deepEqual(call.args, [platform, 1, '.article-body', chosen || 'bb-browser']);
      assert.match(ui.infos.at(-1).message, /素材编号：4/); await ui.close();
    }
    assert.match(ui.infos.at(-1).message, /未重复创建素材/); assert.equal(ui.run('workspaceBrowserBusy()'), false);
  }
});

test('Crawl4AI comment engine reports failure without silently retrying a plugin', async () => {
  for (const module of ['hub', 'douyin']) {
    const method = module === 'hub' ? 'hub_collect' : 'dy_scan_video';
    const ui = runtime({ [method]: () => ({ ok: false, error: 'Crawl4AI 不可用' }) });
    let scan;
    if (module === 'hub') { await ui.run('renderAccountHub()'); scan = ui.run('hubCollectModal(hubItems[0])').catch(error => ui.toasts.push({message:error.message})); }
    else { ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()'); scan = ui.document.getElementById('dy-browser-scan').onclick(); }
    await settle();
    ui.fill(module === 'hub' ? { url: 'https://www.xiaohongshu.com/explore/example', keywords: '咨询', engine: 'crawl4ai' } : { aid: '1', url: 'https://www.douyin.com/video/example', engine: 'crawl4ai' });
    await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await scan;
    const calls = ui.calls.filter(call => call.method === method);
    assert.equal(calls.length, 1); assert.equal(calls[0].args.at(-1), 'crawl4ai');
    assert.equal(ui.run('workspaceBrowserBusy()'), false);
    assert.match(ui.toasts.at(-1).message, /Crawl4AI 不可用/);
  }
  const ui = runtime();
  assert.doesNotMatch(ui.run("workspaceEngineOptions('bb-browser')"), /value="crawl4ai"/);
  assert.throws(() => ui.run("workspaceEngine('crawl4ai','bb-browser')"), /支持/);
  assert.equal(ui.run("workspaceEngineName('crawl4ai')"), 'bb-browser + Crawl4AI 评论爬虫');
});

test('both comment collection results name crawler engine and disclose limits and warnings', async () => {
  for (const module of ['hub', 'douyin']) {
    const method = module === 'hub' ? 'hub_collect' : 'dy_scan_video';
    const ui = runtime({ [method]: () => ({ ok: true, read: 30, fetched: 8, added: 7, duplicates: 1, engine: 'crawl4ai', extraction_engine: 'crawl4ai', truncated: true, warning: '页面操作已结束，保留先前已截取的评论。' }) });
    let scan;
    if (module === 'hub') { await ui.run('renderAccountHub()'); scan = ui.run('hubCollectModal(hubItems[0])'); }
    else { ui.run("curSection='douyin';dyTab='comments'"); await ui.run('renderDouyin()'); scan = ui.document.getElementById('dy-browser-scan').onclick(); }
    await settle(); ui.fill(module === 'hub' ? { url: 'https://www.xiaohongshu.com/explore/example', keywords: '咨询', engine: 'crawl4ai' } : { aid: '1', url: 'https://www.douyin.com/video/example', engine: 'crawl4ai' });
    await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await scan;
    assert.match(ui.infos.at(-1).message, /bb-browser \+ Crawl4AI 评论爬虫/);
    assert.match(ui.infos.at(-1).message, /读取上限.*部分评论/);
    assert.match(ui.infos.at(-1).message, /保留先前已截取的评论/);
  }
});

test('material library filters source data and shows unavailable tools without hiding saved materials', async () => {
  const ui = runtime({ tool_status: () => ({ ok: true, tools: { 'bb-browser': { available: false, error: '工具 <未安装>' }, crawl4ai: { available: true, version: 'test' } } }), material_list: () => ({ ok: true, items: [{ id: 4, platform: 'channels', account_id: 1, title: '<保存的标题>', source_url: 'https://example.invalid/current', character_count: 120, engine: 'crawl4ai' }] }) });
  ui.context.curSection = 'materials'; await ui.context.RENDER.materials();
  assert.match(ui.document.getElementById('page').innerHTML, /不可用/); assert.match(ui.document.getElementById('page').innerHTML, /&lt;未安装&gt;/); assert.match(ui.document.getElementById('page').innerHTML, /&lt;保存的标题&gt;/);
  assert.ok(ui.document.querySelector('[data-material-detail="4"]'));
  ui.document.getElementById('materials-query').value = '关键词'; ui.document.getElementById('materials-platform').value = 'channels'; await ui.click(ui.document.getElementById('materials-search'));
  assert.deepEqual(ui.calls.filter(call => call.method === 'material_list').at(-1).args, ['关键词', 'channels']);
  assert.match(ui.document.getElementById('page').innerHTML, /120/);
  assert.ok(ui.run("NAV.some(item=>item.id==='materials')")); assert.equal(ui.run("NAV.findIndex(item=>item.id==='materials')"), ui.run("NAV.findIndex(item=>item.id==='drafts')") + 1);
});

test('material detail renders untrusted text safely, exports its content and creates one editable draft', async () => {
  const pending = deferred();
  const item = { id: 4, platform: 'douyin', account_id: 1, title: '<标题>', source_url: 'javascript:alert(1)', markdown: '<script>alert(1)</script>\n正文 & 引号', links: [{ url: 'javascript:alert(2)', text: '<img src=x onerror=alert(3)>' }], engine: 'crawl4ai', metadata: { selector: '.article' }, raw_html: '<unsafe>' };
  const ui = runtime({ material_get: () => ({ ok: true, item }), material_to_draft: () => pending.promise }); ui.context.curSection = 'materials';
  const detail = ui.run('materialDetail(4)'); await settle();
  const modal = ui.document.getElementById('modal-root'); assert.match(modal.innerHTML, /&lt;script&gt;/); assert.equal(ui.document.querySelector('#modal-root script'), null); assert.equal(ui.document.querySelector('#modal-root img'), null);
  assert.equal(ui.document.getElementById('material-body').textContent, item.markdown);
  await ui.click(ui.document.getElementById('material-export-md')); await ui.click(ui.document.getElementById('material-export-json'));
  assert.ok(ui.downloads[0].text.includes(item.markdown)); const json = JSON.parse(ui.downloads[1].text); assert.equal(json.markdown, item.markdown); assert.deepEqual(json.links, item.links); assert.equal('raw_html' in json, false);
  const draftButton = ui.document.getElementById('material-detail-draft'), creating = draftButton.onclick(); await settle(); assert.equal(draftButton.disabled, true); await draftButton.onclick();
  assert.equal(ui.calls.filter(call => call.method === 'material_to_draft').length, 1); pending.resolve({ ok: true, id: 31 }); await creating; await detail;
  assert.equal(ui.context.curSection, 'drafts'); assert.equal(ui.document.getElementById('page').innerHTML, 'drafts view'); assert.equal(ui.document.querySelector('#modal-root .modal-mask'), null);
  assert.match(ui.toasts.at(-1).message, /草稿 #31/);
});

test('missing capture tools surface backend error and release the shared task without fallback', async () => {
  for (const error of ['bb-browser 未安装', 'Crawl4AI 不可用']) {
    const ui = runtime({ material_capture: () => ({ ok: false, error }) }); await ui.run('renderAccountHub()');
    const capture = ui.document.querySelector('[data-hub-material="douyin:1"]').onclick(); await settle();
    await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await capture;
    const calls = ui.calls.filter(call => call.method === 'material_capture'); assert.equal(calls.length, 1); assert.equal(calls[0].args.at(-1), 'bb-browser');
    assert.match(ui.toasts.at(-1).message, new RegExp(error)); assert.equal(ui.run('workspaceBrowserBusy()'), false); assert.equal(ui.document.querySelector('[data-hub-material="douyin:1"]').disabled, false);
    assert.equal(ui.infos.length, 0);
  }
});

test('material capture finishes in the current library view without restoring a previous account page', async () => {
  const pending = deferred(); let saved = false;
  const ui = runtime({ material_capture: () => pending.promise, material_list: () => ({ ok: true, items: saved ? [{ id: 4, title: '新归档', platform: 'channels', account_id: 1 }] : [] }) });
  await ui.run('renderAccountHub()'); const capture = ui.document.querySelector('[data-hub-material="channels:1"]').onclick(); await settle();
  await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await settle(); ui.context.curSection = 'materials'; await ui.run('renderMaterials()');
  assert.match(ui.document.getElementById('material-capture-progress').innerHTML || ui.document.getElementById('material-capture-progress').textContent, /正在提取/);
  saved = true; pending.resolve({ ok: true, id: 4, added: true, title: '新归档', source_url: 'https://example.invalid/current' }); await capture;
  assert.equal(ui.context.curSection, 'materials'); assert.match(ui.document.getElementById('page').innerHTML, /新归档/); assert.equal(ui.infos.length, 0); assert.equal(ui.run('workspaceBrowserBusy()'), false);
});

test('failed material-to-draft restores its button and keeps the detail open for retry', async () => {
  const ui = runtime({ material_to_draft: () => ({ ok: false, error: 'draft database unavailable' }) }); ui.context.curSection = 'materials';
  const detail = ui.run('materialDetail(4)'); await settle(); const button = ui.document.getElementById('material-detail-draft'); await button.onclick();
  assert.equal(button.disabled, false); assert.equal(ui.run('materialDraftTasks.size'), 0); assert.ok(ui.document.getElementById('material-body')); assert.equal(ui.context.curSection, 'materials');
  assert.match(ui.toasts.at(-1).message, /draft database unavailable/); await ui.close(); await detail;
});

test('delayed material details never open after navigating to another page', async () => {
  const pending = deferred(), ui = runtime({ material_get: () => pending.promise }); ui.context.curSection = 'materials';
  const detail = ui.run('materialDetail(4)'); ui.context.curSection = 'account_hub';
  pending.resolve({ ok: true, item: { id: 4, title: 'late', markdown: 'late text' } }); await detail;
  assert.equal(ui.document.querySelector('#modal-root .modal-mask'), null);
});

function warmupPlan(changes = {}) {
  return { id: 15, name: '自定义计划', start_date: '2040-05-06', days: 7, minutes: 12, start_time: '11:25', end_time: '17:30', concurrency: 2, engine: 'plugin', status: 'draft', done: 0, total: 7, note: '', targets: [{ kind: 'browser', platform: 'douyin', id: 1 }], runs: [], ...changes };
}

test('warmup creation uses only checked targets, accepts custom configuration and never starts automatically', async () => {
  const ui = runtime(); ui.context.curSection = 'warmup'; await ui.context.RENDER.warmup();
  const creating = ui.document.getElementById('warmup-create').onclick(); await settle();
  const boxes = ui.document.querySelectorAll('[data-warmup-select]'); assert.equal(boxes.length, 3); assert.ok(boxes.every(box => !box.checked && box.name === undefined));
  boxes[0].checked = true; boxes[0].onchange(); boxes[0].checked = false; boxes[0].onchange();
  boxes[1].checked = true; boxes[1].onchange(); boxes[2].checked = true; boxes[2].onchange();
  ui.fill({ name: '自己的计划', days: '7', minutes: '12', start_date: '2040-05-06', start_time: '11:25', end_time: '17:30', concurrency: '2', engine: 'plugin' });
  await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await creating;
  const payload = ui.calls.find(call => call.method === 'warmup_create').args[0];
  assert.deepEqual(JSON.parse(JSON.stringify(payload)), { name: '自己的计划', days: '7', minutes: '12', start_date: '2040-05-06', start_time: '11:25', end_time: '17:30', concurrency: '2', engine: 'plugin', targets: [{ kind: 'browser', platform: 'xiaohongshu', id: 1 }, { kind: 'android', serial: 'TEST-DEVICE' }] });
  assert.equal(ui.calls.filter(call => call.method === 'warmup_action').length, 0); assert.match(ui.toasts.at(-1).message, /已创建.*检查后可开始/);
});

test('warmup refuses an empty selection instead of interpreting every form field as a selected target', async () => {
  const ui = runtime(); ui.context.curSection = 'warmup'; await ui.context.RENDER.warmup();
  const creating = ui.document.getElementById('warmup-create').onclick(); await settle();
  await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await creating;
  assert.equal(ui.calls.filter(call => call.method === 'warmup_create').length, 0); assert.match(ui.toasts.at(-1).message, /至少勾选/);
  assert.equal(ui.context.window.matrixWorkspaceModalBusy, false);
});

test('warmup cancellation and legacy replacement release the modal without creating or starting anything', async () => {
  const ui = runtime(); ui.context.curSection = 'warmup';
  const canceled = ui.run('warmupCreateModal()'); await settle(); await ui.close(); await canceled;
  assert.equal(ui.context.window.matrixWorkspaceModalBusy, false);
  const replaced = ui.run('warmupCreateModal()'); await settle(); ui.context.showInfo('legacy notification', 'completed'); await replaced;
  assert.equal(ui.context.window.matrixWorkspaceModalBusy, false); await ui.close();
  const next = ui.run('warmupCreateModal()'); await settle(); assert.ok(ui.document.querySelector('[data-warmup-select]')); await ui.close(); await next;
  assert.equal(ui.calls.filter(call => ['warmup_create', 'warmup_action'].includes(call.method)).length, 0);
});

test('warmup pause and retry pass exact IDs/actions and serialize actions across a polling render', async () => {
  const pending = deferred(); let plan = warmupPlan({ status: 'running', runs: [{ status: 'error', target_key: 'douyin:1', day_index: 0, elapsed_seconds: 4, error: 'offline' }] });
  const ui = runtime({ warmup_list: () => ({ ok: true, items: [plan] }), warmup_action: (id, action) => action === 'pause' ? pending.promise : { ok: true } });
  ui.context.curSection = 'warmup'; await ui.context.RENDER.warmup();
  const pause = ui.document.querySelector('[data-warmup-action="pause"]'), pausing = pause.onclick(); await settle();
  await ui.run('renderWarmup()'); assert.ok(ui.document.querySelectorAll('[data-warmup-action]').every(button => button.disabled));
  await ui.document.querySelector('[data-warmup-action="retry"]').onclick(); assert.equal(ui.calls.filter(call => call.method === 'warmup_action').length, 1);
  plan = { ...plan, status: 'paused' }; pending.resolve({ ok: true }); await pausing;
  assert.deepEqual(ui.calls.filter(call => call.method === 'warmup_action')[0].args, [15, 'pause']);
  assert.equal(ui.document.querySelector('[data-warmup-action="pause"]').disabled, true); assert.equal(ui.document.querySelector('[data-warmup-action="retry"]').disabled, false);
  await ui.click(ui.document.querySelector('[data-warmup-action="retry"]'));
  assert.deepEqual(ui.calls.filter(call => call.method === 'warmup_action').at(-1).args, [15, 'retry']); assert.equal(ui.run('warmupActionTasks.size'), 0);
});

test('warmup backend errors do not announce success and controls recover for retry', async () => {
  const plan = warmupPlan({ status: 'running' });
  const ui = runtime({ warmup_list: () => ({ ok: true, items: [plan] }), warmup_action: () => ({ ok: false, error: 'device unavailable' }), warmup_create: () => ({ ok: false, error: 'invalid window' }) });
  ui.context.curSection = 'warmup'; await ui.context.RENDER.warmup();
  await ui.click(ui.document.querySelector('[data-warmup-action="pause"]')); assert.equal(ui.document.querySelector('[data-warmup-action="pause"]').disabled, false); assert.equal(ui.run('warmupActionTasks.size'), 0);
  assert.match(ui.toasts.at(-1).message, /device unavailable/);
  const creating = ui.document.getElementById('warmup-create').onclick(); await settle(); const box = ui.document.querySelector('[data-warmup-select]'); box.checked = true; box.onchange();
  await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]')); await creating;
  assert.match(ui.toasts.at(-1).message, /invalid window/); assert.ok(!ui.toasts.some(toast => /已创建/.test(toast.message))); assert.equal(ui.context.window.matrixWorkspaceModalBusy, false);
});

test('late warmup renders, creation inputs and details cannot override another navigation', async () => {
  const pending = deferred(), ui = runtime({ warmup_list: () => pending.promise }); ui.context.curSection = 'warmup';
  const rendered = ui.context.RENDER.warmup(); await settle(); ui.context.curSection = 'materials'; ui.document.getElementById('page').innerHTML = 'current materials';
  pending.resolve({ ok: true, items: [warmupPlan()] }); await rendered; assert.equal(ui.document.getElementById('page').innerHTML, 'current materials'); assert.equal(ui.timers.size, 0);
  const targetFetch = deferred(); ui.overrides.warmup_targets = () => targetFetch.promise; ui.context.curSection = 'warmup'; const creating = ui.run('warmupCreateModal()'); await settle(); ui.context.curSection = 'materials';
  targetFetch.resolve({ ok: true, items: [{ kind: 'browser', platform: 'douyin', id: 1 }] }); await creating; assert.equal(ui.document.querySelector('#modal-root .modal-mask'), null);
  const detailFetch = deferred(); ui.overrides.warmup_list = () => detailFetch.promise; ui.context.curSection = 'warmup'; const detail = ui.run('warmupDetail(15)'); ui.context.curSection = 'account_hub';
  detailFetch.resolve({ ok: true, items: [warmupPlan()] }); await detail; assert.equal(ui.infos.length, 0);
});

test('warmup initial load failure is handled, and stale failure cannot replace a fresh successful view', async () => {
  const ui = runtime({ warmup_list: () => ({ ok: false, error: 'plan database unavailable' }) }); ui.context.curSection = 'warmup'; await ui.context.RENDER.warmup();
  assert.ok(ui.document.getElementById('warmup-retry')); assert.match(ui.toasts.at(-1).message, /plan database unavailable/);
  const pending = deferred(); let count = 0;
  ui.overrides.warmup_list = () => ++count === 1 ? pending.promise : { ok: true, items: [warmupPlan()] };
  const stale = ui.context.RENDER.warmup(); await ui.context.RENDER.warmup(); pending.reject(Error('old failure')); await stale;
  assert.equal(ui.document.getElementById('warmup-retry'), null); assert.match(ui.document.getElementById('page').innerHTML, /自定义计划/);
});


test('batch import selects platform account and creates a draft with 20 works; never starts automatically', async () => {
 const ui=runtime();ui.context.curSection='batch_collect';await ui.context.RENDER.batch_collect();
 const creating=ui.document.getElementById('batch-create').onclick();await settle();
 ui.fill({douyin:'1',text:'分享文案 https://www.douyin.com/user/test',works:'20',comments:'1000',words:'价格\n预约'});
 await ui.click(ui.document.querySelector('#modal-root [data-x="ok"]'));await creating;
 const created=ui.calls.find(call=>call.method==='batch_create');
 assert.deepEqual(JSON.parse(JSON.stringify(created.args)),['分享文案 https://www.douyin.com/user/test',{douyin:1},['价格','预约'],20,1000]);
 assert.equal(ui.calls.filter(call=>call.method==='batch_action').length,0);
});

test('late batch refresh does not overwrite a different section', async () => {
 const pending=deferred(),ui=runtime({batch_list:()=>pending.promise});ui.context.curSection='batch_collect';
 const rendering=ui.context.RENDER.batch_collect();await settle();ui.context.curSection='account_hub';ui.document.getElementById('page').innerHTML='current account page';
 pending.resolve({ok:true,items:[]});await rendering;
 assert.equal(ui.document.getElementById('page').innerHTML,'current account page');
});

test('beauty preset only edits keyword draft until user saves it', async () => {
 const ui=runtime({dy_keywords:()=>({ok:true,items:['原词'],presets:[{name:'通用美业',words:['美甲','护肤','价格']}]})});
 ui.run("curSection='douyin';dyTab='comments'");await ui.run('renderDouyin()');
 const editing=ui.document.getElementById('dy-kw').onclick();await settle();
 const select=ui.document.getElementById('dy-keyword-preset');select.value='0';select.onchange();
 assert.equal(ui.document.querySelector('[name="words"]').value,'美甲\n护肤\n价格');
 await ui.close();await editing;
 assert.equal(ui.calls.filter(call=>call.method==='dy_keywords'&&call.args.length).length,0);
});

test('one account entry handles legacy routes, while search retains cross-platform selection', async () => {
 const ui=runtime();
 assert.deepEqual(Array.from(ui.context.NAV.filter(n=>['accounts','douyin','account_hub'].includes(n.id)),n=>n.id),['account_hub']);
 await ui.context.RENDER.accounts();assert.equal(ui.context.curSection,'account_hub');assert.equal(ui.run('hubPlatform'),'xiaohongshu');
 ui.run("dyTab='comments'");await ui.context.RENDER.douyin();assert.ok(ui.document.getElementById('dy-content'));
 ui.items[0].group_name='门店';ui.items[2].group_name='门店';
 await ui.run('renderAccountHub()');
 ui.run("hubSelected.add('xiaohongshu:1');hubSelected.add('douyin:1');hubSearch='抖音';hubGroup='门店';hubDraw()");
 assert.deepEqual(Array.from(ui.run('hubVisibleItems()'),a=>a.key),['douyin:1']);assert.equal(ui.run('hubSelected.size'),2);
});

test('selected Douyin workspace keeps account scope for comments, leads and export', async () => {
 const ui=runtime();await ui.run('renderAccountHub()');await ui.click(ui.document.querySelector('[data-hub-workspace="douyin:2"]'));
 assert.equal(ui.context.curSection,'account_hub');assert.deepEqual(ui.calls.filter(c=>c.method==='dy_comments').at(-1).args,['2','',false]);
 await ui.click(ui.document.querySelector('[data-hub-action="leads"]'));
 assert.deepEqual(ui.calls.filter(c=>c.method==='dy_leads').at(-1).args,['','2']);
 await ui.click(ui.document.getElementById('dy-lead-export'));assert.deepEqual(ui.calls.filter(c=>c.method==='dy_export').at(-1).args,['leads','2','',false,'']);
});

test('late embedded comments cannot overwrite another selected account or metrics pane', async () => {
 const slow=deferred();let count=0;const ui=runtime({dy_comments:()=>++count===1?slow.promise:{ok:true,items:[]}});
 await ui.run('renderAccountHub()');const first=ui.run("hubShowView('comments','douyin:1')");await settle();await ui.run("hubShowView('comments','douyin:2')");
 slow.resolve({ok:true,items:[{content:'stale account comment'}]});await first;
 assert.doesNotMatch(ui.document.getElementById('dy-content').innerHTML,/stale account comment/);
 await ui.run("hubShowView('metrics')");assert.ok(ui.document.getElementById('metrics-observer'));
});

test('saving monitor settings records chosen observer without starting collection', async () => {
 const ui=runtime();await ui.run('renderAccountHub()');await ui.click(ui.document.getElementById('hub-metrics'));
 ui.document.getElementById('metrics-observer').value='2';ui.document.getElementById('metrics-targets').value='https://rednote.com/user/profile/person';
 ui.document.getElementById('metrics-works').value='20';ui.document.getElementById('metrics-comments').value='300';
 await ui.click(ui.document.getElementById('metrics-save'));const config=ui.calls.find(c=>c.method==='metrics_save').args[0];
 assert.equal(config.observer_id,'2');assert.equal(config.works,20);assert.equal(config.comments,300);assert.equal(config.daily,false);
 assert.ok(!ui.calls.some(c=>['metrics_run','batch_action','hub_open','publish_start'].includes(c.method)));
});

test('bulk publish shortcut selects only XHS accounts without publishing immediately', async () => {
 const ui=runtime();ui.context.state={pubAccounts:new Set()};ui.context.RENDER.publish=async()=>{ui.context.state.pubAccounts=new Set();ui.document.getElementById('page').innerHTML='<input data-aid="1"><input data-aid="2">';};
 await ui.run('renderAccountHub()');ui.run("hubSelected.add('xiaohongshu:2');hubSelected.add('douyin:1')");await ui.click(ui.document.getElementById('hub-publish-selected'));
 assert.equal(ui.context.curSection,'publish');assert.deepEqual(Array.from(ui.context.state.pubAccounts),[2]);assert.ok(!ui.calls.some(c=>['publish_start','queue_create'].includes(c.method)));
});
