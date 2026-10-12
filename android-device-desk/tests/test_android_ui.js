/* node --test tests/test_android_ui.js: no phone, browser, or message is used. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'ui', 'android.js'), 'utf8');
const escape = value => String(value == null ? '' : value).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function deferred() { let resolve; const promise = new Promise(yes => resolve = yes); return { promise, resolve }; }
function runtime(overrides = {}) {
  const nodes = new Map(), calls = [], toasts = [], infos = [];
  const defaults = {
    android_status: { ok: true, adb_available: true, scrcpy_available: true },
    android_devices: { ok: true, items: [] },
    android_prepare: { ok: true, prepared_id: 'draft', succeeded: 1, items: [{ serial: 'phone-A', ok: true, preview: { b64: 'cG5n' } }] },
    android_send: { ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true }] },
    android_batch: { ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true }] },
    android_screenshot: { ok: true, b64: 'cG5n', width: 200, height: 400 },
    android_input_helper: { ok: true, installed: true, active: false },
    android_display_info: { ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true, width: 200, height: 400, rotation: 0, orientation: 'portrait' }] },
  };
  const context = vm.createContext({
    NAV: [], TITLES: {}, RENDER: {}, curSection: 'android_control',
    esc: escape, toast: (text, error) => toasts.push({ text, error }),
    showInfo: (title, text) => infos.push({ title, text }), copyText() {},
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, { innerHTML: '', value: '', disabled: false,
          getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 400 }) });
        return nodes.get(id);
      },
      querySelectorAll() { return []; },
    },
    setInterval() { throw new Error('Unexpected continuous screenshot polling'); },
    api: async (method, ...args) => {
      calls.push({ method, args });
      const override = overrides[method];
      if (typeof override === 'function') return override(...args);
      return override || defaults[method] || { ok: true };
    },
  });
  vm.runInContext(source, context);
  return { context, calls, toasts, infos, nodes,
    run: code => vm.runInContext(code, context),
    setup: () => vm.runInContext(`androidItems=[{serial:'phone-A',state:'device'}];androidSelected.add('phone-A');androidText='你好😀';androidTargets.set('phone-A',{input:{x:.2,y:.8},send:{x:.8,y:.8},chat_text:'联系人'});`, context),
  };
}

test('preparation passes Unicode to verified backend and never calls send automatically', async () => {
  const app = runtime(); app.setup();
  await app.run('androidPrepare()');
  assert.deepEqual(app.calls.map(call => call.method), ['android_prepare']);
  assert.equal(app.calls[0].args[1], '你好😀');
  assert.equal(app.run('androidPrepared.prepared_id'), 'draft');
  assert.match(app.nodes.get('page').innerHTML, /尚未发送/);
  assert.match(app.nodes.get('page').innerHTML, /发送已预览草稿/);
  await app.run('androidSend()');
  assert.deepEqual(app.calls.map(call => call.method), ['android_prepare', 'android_send']);
  assert.equal(app.run('androidPrepared'), null);
});

test('preparation failure is displayed and does not expose a sendable draft', async () => {
  const app = runtime({ android_prepare: { ok: true, prepared_id: '', succeeded: 0,
    items: [{ serial: 'phone-A', ok: false, error: '请先选择中文输入助手' }] } });
  app.setup(); await app.run('androidPrepare()');
  await assert.rejects(app.run('androidSend()'), /先填入/);
  assert.match(app.nodes.get('page').innerHTML, /请先选择中文输入助手/);
  assert.equal(app.calls.filter(call => call.method === 'android_send').length, 0);
});

test('simultaneous actions are refused and the busy flag releases on backend failure', async () => {
  const wait = deferred();
  const app = runtime({ android_batch: () => wait.promise }); app.setup();
  const first = app.run("androidBatch('home')");
  await assert.rejects(app.run("androidBatch('back')"), /正在执行/);
  wait.resolve({ ok: false, error: '设备断开' });
  await assert.rejects(first, /设备断开/);
  assert.equal(app.run('androidBusy'), false);
  assert.equal(app.calls.filter(call => call.method === 'android_batch').length, 1);
});

test('late status refresh cannot overwrite a different section', async () => {
  const wait = deferred();
  const app = runtime({ android_devices: () => wait.promise });
  const first = app.run('renderAndroid()');
  app.run("curSection='accounts'");
  app.context.document.getElementById('page').innerHTML = '其他页面';
  wait.resolve({ ok: true, items: [{ serial: 'phone-A', state: 'device' }] });
  await first;
  assert.equal(app.nodes.get('page').innerHTML, '其他页面');
});

test('late device failure does not open a modal over another section', async () => {
  const wait = deferred();
  const app = runtime({ android_batch: () => wait.promise }); app.setup();
  const first = app.run("androidBatch('home')");
  app.run("curSection='materials'");
  wait.resolve({ ok: true, succeeded: 0, items: [{ serial: 'phone-A', ok: false, error: '设备离线' }] });
  await first;
  assert.equal(app.infos.length, 0);
  assert.equal(app.toasts.length, 1);
});

test('warming and unauthorized devices are excluded from batch selection', () => {
  const app = runtime();
  app.run(`androidItems=[{serial:'phone-A',state:'device',busy:true},{serial:'phone-B',state:'unauthorized'},{serial:'phone-D',state:'device'}];['phone-A','phone-B','phone-D'].forEach(value=>androidSelected.add(value));`);
  assert.equal(JSON.stringify(app.run('androidSelectedOnline()')), JSON.stringify(['phone-D']));
});

test('helper installation and settings are explicit calls, never implicit in rendering', async () => {
  const app = runtime(); app.setup(); app.run('androidDraw()');
  assert.equal(app.calls.length, 0);
  await app.run("androidInputHelper('phone-A',true,false)");
  assert.equal(app.calls[0].method, 'android_input_helper');
  assert.equal(JSON.stringify(app.calls[0].args), JSON.stringify(['phone-A', true, false]));
  assert.match(app.nodes.get('page').innerHTML, /请在手机选择 ADB Keyboard/);
});

test('device labels, errors, and text are escaped and errors release the operation lock', async () => {
  const app = runtime({ android_prepare: { ok: false, error: '<secret>' } });
  app.setup(); app.run("androidItems[0].model='<script>bad</script>';androidDraw()");
  assert.match(app.nodes.get('page').innerHTML, /&lt;script&gt;/);
  assert.doesNotMatch(app.nodes.get('page').innerHTML, /<script>/);
  await assert.rejects(app.run('androidPrepare()'), /<secret>/);
  assert.equal(app.run('androidBusy'), false);
  assert.equal(app.run('androidPrepared'), null);
});

test('display checks only selected authorized devices and do not capture or mutate', async () => {
  const app = runtime();
  app.run(`androidItems=[{serial:'phone-A',state:'device',busy:true},{serial:'phone-B',state:'unauthorized'},{serial:'phone-D',state:'device'}];['phone-A','phone-B'].forEach(value=>androidSelected.add(value));androidPrepared={prepared_id:'original'};androidDraw();`);
  assert.equal(app.calls.length, 0);
  await app.run('androidCheckDisplays()');
  assert.deepEqual(app.calls.map(call => call.method), ['android_display_info']);
  assert.equal(JSON.stringify(app.calls[0].args), JSON.stringify([['phone-A']]));
  assert.equal(app.run('androidPrepared.prepared_id'), 'original');
  assert.match(app.nodes.get('page').innerHTML, /检查尺寸 \/ 横竖屏/);
  assert.match(app.nodes.get('page').innerHTML, /200 × 400/);
  assert.match(app.nodes.get('page').innerHTML, /竖屏 \/ 0°/);
});

test('different screen sizes show a per-device layout hint and escape unknown errors', async () => {
  const app = runtime({ android_display_info: { ok: true, succeeded: 2, items: [
    { serial: 'phone-A', ok: true, width: 200, height: 400, rotation: 0, orientation: 'portrait' },
    { serial: 'phone-D', ok: true, width: 400, height: 200, rotation: 90, orientation: 'landscape' },
    { serial: 'phone-E', ok: false, width: null, height: null, base_width: 1080, base_height: 2220, rotation: null, orientation: 'unknown', error: '<unknown>' },
  ] } });
  app.run(`androidItems=['phone-A','phone-D','phone-E'].map(serial=>({serial,state:'device'}));androidItems.forEach(item=>androidSelected.add(item.serial));`);
  await app.run('androidCheckDisplays()');
  const html = app.nodes.get('page').innerHTML;
  assert.match(html, /布局可能不同，请逐机核对点击位置/);
  assert.match(html, /横屏 \/ 90°/);
  assert.match(html, /基础尺寸 1080 × 2220/);
  assert.match(html, /&lt;unknown&gt;/);
  assert.doesNotMatch(html, /<unknown>/);
});

test('delayed display result does not overwrite a different page or newer device list', async () => {
  const wait = deferred();
  const app = runtime({ android_display_info: () => wait.promise }); app.setup();
  const first = app.run('androidCheckDisplays()');
  app.run("curSection='materials';androidRequest++");
  app.nodes.get('page').innerHTML = '素材页面';
  wait.resolve({ ok: true, succeeded: 1, items: [{ serial: 'phone-A', ok: true, width: 200, height: 400, orientation: 'portrait' }] });
  await first;
  assert.equal(app.nodes.get('page').innerHTML, '素材页面');
  assert.equal(app.run('androidDisplayResults.size'), 0);
  assert.equal(app.run('androidBusy'), false);
});

test('display check refuses empty selection and releases busy flag on failure', async () => {
  const app = runtime({ android_display_info: { ok: false, error: '连接断开' } });
  await assert.rejects(app.run('androidCheckDisplays()'), /先勾选/);
  assert.equal(app.calls.length, 0);
  app.setup();
  await assert.rejects(app.run('androidCheckDisplays()'), /连接断开/);
  assert.equal(app.run('androidBusy'), false);
});
