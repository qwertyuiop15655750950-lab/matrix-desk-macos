/* Network UI tests use a local DOM adapter; no network or phone operations. */
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
      if (selected) node.value = selected.attrs.value || selected.textContent;
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

function runtime(overrides = {}) {
  const document = new Document(), calls = [], toasts = [];
  const items = [
    { serial: 'phone-A', model: '<Android>', state: 'device', busy: false, saved_original: true, verified: false, settings: { host: '127.0.0.1', port: '17890', configured: true } },
    { serial: 'phone-B', state: 'unauthorized', busy: false, saved_original: false },
    { serial: 'phone-C', state: 'device', busy: true, saved_original: false },
    { serial: 'phone-D', state: 'device', busy: false, saved_original: false },
  ];
  const defaults = {
    android_network_status: () => ({ ok: true, items: items.map(item => ({ ...item })) }),
    host_network_status: () => ({ ok: true, computer_addresses: ['192.168.2.115'], local_proxy_candidates: [{ host: '127.0.0.1', port: 1082, listening: true, http_support: 'unverified' }] }),
    android_network_apply: serials => ({ ok: true, succeeded: serials.length, items: serials.map(serial => ({ serial, ok: true, configured: true, verified: false })) }),
    android_network_restore: serials => ({ ok: true, succeeded: serials.length, items: serials.map(serial => ({ serial, ok: true, restored: true, verified: false })) }),
    android_network_settings: serials => ({ ok: true, succeeded: serials.length, items: serials.map(serial => ({ serial, ok: true, opened: true, verified: false })) }),
    android_network_check_exit: serials => ({ ok: true, succeeded: serials.length, items: serials.map(serial => ({ serial, ok: true, opened: true, verified: false })) }),
  };
  const context = vm.createContext({ document, NAV: [{ id: 'android_control' }], TITLES: {}, RENDER: {}, curSection: 'android_network', esc: escape,
    toast: (message, error) => toasts.push({ message, error }),
    api: async (method, ...args) => { calls.push({ method, args: JSON.parse(JSON.stringify(args)) }); const handler = overrides[method] || defaults[method]; if (!handler) throw Error(`Unexpected API ${method}`); return await handler(...args); },
  });
  vm.runInContext(fs.readFileSync(path.join(appRoot, 'app/ui/android_network.js'), 'utf8'), context, { filename: 'android_network.js' });
  const run = code => vm.runInContext(code, context);
  const click = async element => { assert.ok(element && element.isConnected, 'control must exist'); if (!element.disabled && element.onclick) await element.onclick(); };
  const select = serial => { const input = document.querySelector(`[data-network-select="${serial}"]`); input.checked = true; input.onchange(); };
  const type = (id, value) => { const input = document.getElementById(id); input.value = value; input.oninput({ target: input }); };
  return { document, context, calls, toasts, run, click, select, type, overrides, items };
}

test('opening network page reads candidates without choosing a phone, applying a proxy or claiming an exit', async () => {
  const ui = runtime(); await ui.run('renderAndroidNetwork()');
  assert.deepEqual(ui.calls.map(call => call.method).sort(), ['android_network_status', 'host_network_status']);
  assert.equal(ui.document.getElementById('network-transport').value, 'usb');
  assert.equal(ui.document.getElementById('network-port').value, '');
  assert.equal(ui.run('networkSelected.size'), 0);
  assert.equal(ui.document.getElementById('network-apply').disabled, true);
  assert.match(ui.document.getElementById('page').innerHTML, /出口尚未验证/);
  assert.match(ui.document.getElementById('page').innerHTML, /&lt;Android&gt;/);
  assert.doesNotMatch(ui.document.getElementById('page').innerHTML, /global_proxy_pac_url|连接成功|国外出口已验证/);
});

test('choosing a candidate only fills the form and explicit apply targets selected authorized idle phones', async () => {
  const ui = runtime(); await ui.run('renderAndroidNetwork()');
  await ui.click(ui.document.querySelector('[data-network-port="0"]'));
  assert.equal(ui.document.getElementById('network-port').value, '1082'); assert.equal(ui.calls.length, 2);
  ui.select('phone-A'); ui.select('phone-B'); ui.select('phone-C');
  await ui.click(ui.document.getElementById('network-apply'));
  const writes = ui.calls.filter(call => call.method === 'android_network_apply');
  assert.equal(writes.length, 1); assert.deepEqual(writes[0].args, [['phone-A'], '127.0.0.1', 1082, 'usb']);
  assert.match(ui.document.getElementById('network-progress').textContent, /1\/1 台.*出口尚未验证/);
  assert.equal(ui.run('networkBusy'), false);
});

test('manual HTTP port is required and LAN mode sends explicitly supplied computer address', async () => {
  const ui = runtime(); await ui.run('renderAndroidNetwork()'); ui.select('phone-A');
  await ui.click(ui.document.getElementById('network-apply'));
  assert.match(ui.toasts.at(-1).message, /端口/); assert.equal(ui.calls.filter(call => call.method === 'android_network_apply').length, 0);
  const transport = ui.document.getElementById('network-transport'); transport.value = 'lan'; transport.onchange({ target: transport });
  ui.type('network-port', '1082'); await ui.click(ui.document.getElementById('network-apply'));
  assert.match(ui.toasts.at(-1).message, /局域网地址/);
  await ui.click(ui.document.querySelector('[data-network-host="0"]'));
  await ui.click(ui.document.getElementById('network-apply'));
  assert.deepEqual(ui.calls.find(call => call.method === 'android_network_apply').args, [['phone-A'], '192.168.2.115', 1082, 'lan']);
});

test('restore, VPN settings and exit check occur only on their explicit buttons and exit remains unverified', async () => {
  const ui = runtime(); await ui.run('renderAndroidNetwork()'); ui.select('phone-A'); ui.select('phone-D');
  for (const [button, method] of [['network-restore', 'android_network_restore'], ['network-settings', 'android_network_settings'], ['network-exit', 'android_network_check_exit']]) {
    const before = ui.calls.filter(call => call.method === method).length;
    await ui.click(ui.document.getElementById(button));
    const writes = ui.calls.filter(call => call.method === method); assert.equal(writes.length, before + 1);
    assert.deepEqual(writes.at(-1).args, [['phone-A', 'phone-D']]);
  }
  assert.equal(ui.calls.filter(call => call.method === 'android_network_apply').length, 0);
  assert.match(ui.document.getElementById('network-progress').textContent, /请在手机查看出口 IP.*尚未验证出口/);
});

test('operation lock rejects duplicate dispatch and stale handlers cannot change the form or selection', async () => {
  const pending = deferred(), ui = runtime({ android_network_apply: () => pending.promise });
  await ui.run('renderAndroidNetwork()'); ui.select('phone-A'); ui.type('network-port', '1082');
  const oldClear = ui.document.getElementById('network-clear'), oldCandidate = ui.document.querySelector('[data-network-port="0"]');
  const operation = ui.run("networkAct('apply')"); await settle();
  assert.equal(ui.run('networkBusy'), true); assert.equal(ui.document.getElementById('network-restore').disabled, true);
  await assert.rejects(ui.run("networkAct('apply')"), /正在执行/);
  oldClear.onclick(); oldCandidate.onclick();
  assert.equal(ui.run('networkSelected.size'), 1); assert.equal(ui.run('networkPort'), '1082');
  pending.resolve({ ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true, verified: false }] }); await operation;
  assert.equal(ui.calls.filter(call => call.method === 'android_network_apply').length, 1); assert.equal(ui.run('networkBusy'), false);
});

test('late initial fetch and completed operation cannot overwrite another page', async () => {
  const pending = deferred(), ui = runtime({ android_network_status: () => pending.promise });
  const render = ui.run('renderAndroidNetwork()'); await settle();
  ui.context.curSection = 'dashboard'; ui.document.getElementById('page').innerHTML = '<div>dashboard</div>';
  pending.resolve({ ok: true, items: [] }); await render;
  assert.equal(ui.document.getElementById('page').innerHTML, '<div>dashboard</div>');
  const action = deferred(), second = runtime({ android_network_apply: () => action.promise });
  await second.run('renderAndroidNetwork()'); second.select('phone-A'); second.type('network-port', '1082');
  const operation = second.run("networkAct('apply')"); await settle();
  second.context.curSection = 'dashboard'; second.document.getElementById('page').innerHTML = '<div>dashboard</div>';
  action.resolve({ ok: true, succeeded: 1, items: [{ ok: true }] }); await operation;
  assert.equal(second.document.getElementById('page').innerHTML, '<div>dashboard</div>');
  assert.match(second.run('networkProgress'), /出口尚未验证/);
});

test('newest refresh wins without stale data clearing selection', async () => {
  const pending = deferred(); let count = 0;
  const ui = runtime({ android_network_status: () => ++count === 1 ? pending.promise : { ok: true, items: [{ serial: 'latest', state: 'device', model: 'latest phone' }] } });
  const old = ui.run('renderAndroidNetwork()'); await ui.run('renderAndroidNetwork()'); ui.select('latest');
  pending.resolve({ ok: true, items: [] }); await old;
  assert.equal(ui.run('networkSelected.size'), 1); assert.match(ui.document.getElementById('page').innerHTML, /latest phone/);
});

test('failures release operation lock and successful write retains its result when status refresh fails', async () => {
  const ui = runtime({ android_network_apply: () => ({ ok: false, error: 'fixture failed' }) });
  await ui.run('renderAndroidNetwork()'); ui.select('phone-A'); ui.type('network-port', '1082');
  await ui.click(ui.document.getElementById('network-apply'));
  assert.equal(ui.run('networkBusy'), false); assert.match(ui.toasts.at(-1).message, /fixture failed/);
  assert.equal(ui.calls.filter(call => call.method === 'android_network_apply').length, 1);
  ui.overrides.android_network_apply = () => ({ ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true }] });
  ui.overrides.android_network_status = () => ({ ok: false, error: 'refresh offline' });
  await ui.click(ui.document.getElementById('network-apply'));
  assert.match(ui.document.getElementById('network-progress').textContent, /系统 HTTP 代理已配置.*出口尚未验证/);
  assert.match(ui.document.getElementById('network-progress').textContent, /状态刷新失败：refresh offline/);
});

test('initial error has a handled retry and host discovery failure still permits manual configuration', async () => {
  const ui = runtime({ android_network_status: () => ({ ok: false, error: '<device missing>' }) });
  await ui.context.RENDER.android_network();
  assert.match(ui.document.getElementById('page').innerHTML, /&lt;device missing&gt;/);
  assert.ok(ui.document.getElementById('network-retry'));
  ui.overrides.android_network_status = () => ({ ok: true, items: [{ serial: 'phone-A', state: 'device' }] });
  ui.overrides.host_network_status = () => ({ ok: false, error: 'host probe unavailable', computer_addresses: [], local_proxy_candidates: [] });
  await ui.click(ui.document.getElementById('network-retry'));
  assert.match(ui.document.getElementById('page').innerHTML, /host probe unavailable/);
  ui.select('phone-A'); ui.type('network-port', '1082'); await ui.click(ui.document.getElementById('network-apply'));
  assert.equal(ui.calls.filter(call => call.method === 'android_network_apply').length, 1);
});

