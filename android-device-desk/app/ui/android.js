/* Android 设备按需截图/投屏。没有持续截图轮询，不自动打开任何投屏。 */
const androidNavPosition = NAV.findIndex(item => item.id === 'grid');
NAV.splice(androidNavPosition < 0 ? NAV.length : androidNavPosition + 1, 0,
  { id: 'android_control', name: '手机群控', ico: '📱' });
TITLES.android_control = 'Android 手机群控';

let androidItems = [], androidStatus = {}, androidBusy = false, androidRequest = 0;
let androidCurrent = '', androidShot = null, androidShotRequest = 0;
let androidMarkMode = 'tap', androidTapPoint = null, androidText = '';
let androidPrepared = null, androidProgress = '';
const androidSelected = new Set(), androidTargets = new Map();
const androidHelpers = new Map();
const androidDisplayResults = new Map();

async function androidCall(method, ...args) {
  const result = await api(method, ...args);
  if (!result || !result.ok) throw new Error(result && result.error || '手机操作失败');
  return result;
}
function androidGuard(callback) {
  return async () => { try { await callback(); } catch (error) { toast(error.message || '手机操作失败', true); } };
}
function androidOnline(serial) { return androidItems.some(item => item.serial === serial && item.state === 'device'); }
function androidSelectedOnline() { return androidItems.filter(item => item.state === 'device' && !item.busy && androidSelected.has(item.serial)).map(item => item.serial); }
function androidSelectedAuthorized() { return androidItems.filter(item => item.state === 'device' && androidSelected.has(item.serial)).map(item => item.serial); }
function androidPointLabel(point) { return point ? `${(point.x * 100).toFixed(1)}%, ${(point.y * 100).toFixed(1)}%` : '未标记'; }
function androidStateLabel(state) { return ({ device: '已连接并授权', unauthorized: '请在手机允许 USB 调试', offline: '离线' })[state] || state; }
function androidInvalidateDraft() { androidPrepared = null; }

async function renderAndroid() {
  const request = ++androidRequest;
  const [status, listed] = await Promise.allSettled([androidCall('android_status'), androidCall('android_devices')]);
  if (request !== androidRequest || curSection !== 'android_control') return;
  androidStatus = status.status === 'fulfilled' ? (status.value.status || status.value) : { error: status.reason.message };
  if (listed.status === 'rejected') throw listed.reason;
  androidItems = listed.value.items || [];
  const online = new Set(androidItems.filter(item => item.state === 'device').map(item => item.serial));
  for (const serial of androidSelected) if (!online.has(serial)) androidSelected.delete(serial);
  for (const serial of androidDisplayResults.keys()) if (!online.has(serial)) androidDisplayResults.delete(serial);
  if (!online.has(androidCurrent)) { androidCurrent = ''; androidShot = null; }
  androidDraw();
}

function androidDraw() {
  if (curSection !== 'android_control') return;
  const serials = androidSelectedOnline(), target = androidTargets.get(androidCurrent) || {};
  const busy = androidBusy ? 'disabled' : '';
  const shots = androidPrepared && androidPrepared.items || [];
  const displayResults = androidItems.map(item => androidDisplayResults.get(item.serial)).filter(Boolean);
  const displayMismatch = new Set(displayResults.filter(item => item.ok).map(item => `${item.width}x${item.height}:${item.orientation}`)).size > 1;
  document.getElementById('page').innerHTML = `
    <div id="phone-wall"></div><details class="device-tools"><summary>设备工具与批量操作</summary><div class="hint">手机开启 USB 调试并在手机上允许此电脑后，点击刷新检测。设备列表不加载视频；截图只在点击时获取。投屏最多同时打开 2 台，800 像素、30fps、无音频。</div>
    <div class="hint" style="margin-top:8px">工具：adb ${androidStatus.adb_available ? '可用' : '未就绪'} · scrcpy ${androidStatus.scrcpy_available ? '可用' : '未就绪'}${androidStatus.error ? ' · ' + esc(androidStatus.error) : ''}。设备控制和聊天界面仍需连接真机验证。</div>
    <div class="toolbar" style="margin-top:12px"><button class="btn" id="android-refresh" ${busy}>刷新设备</button>
      <button class="btn" id="android-select-all" ${busy}>选择已授权设备</button><button class="btn" id="android-clear" ${busy}>清空勾选</button>
      <button class="btn" id="android-display-check" ${androidBusy || !androidSelectedAuthorized().length ? 'disabled' : ''}>检查尺寸 / 横竖屏</button>
      <span class="hint">已选 ${serials.length} 台</span></div>
    <div class="tbl-wrap"><table><thead><tr><th>选择</th><th>设备</th><th>状态</th><th>投屏</th><th>操作</th></tr></thead><tbody>
      ${androidItems.map(item => `<tr><td><input type="checkbox" data-android-select="${esc(item.serial)}" ${androidSelected.has(item.serial) ? 'checked' : ''} ${androidBusy || item.busy || item.state !== 'device' ? 'disabled' : ''}></td>
        <td><b>${esc(item.model || 'Android')}</b><br><small>${esc(item.serial)}</small></td><td>${esc(androidStateLabel(item.state))}${item.busy ? '<br><small>手机任务执行中</small>' : ''}</td>
        <td>${item.mirroring ? '窗口已打开' : '未打开'}</td><td><button class="btn" data-android-shot="${esc(item.serial)}" ${androidBusy || item.state !== 'device' ? 'disabled' : ''}>截图 / 标记</button>
          <button class="btn" data-android-mirror="${esc(item.serial)}" ${androidBusy || item.state !== 'device' ? 'disabled' : ''}>按需投屏</button>
          <button class="btn" data-android-close="${esc(item.serial)}" ${busy}>关闭投屏</button>
          <button class="btn" data-android-helper-install="${esc(item.serial)}" ${androidBusy || item.busy || item.state !== 'device' ? 'disabled' : ''}>安装中文输入助手</button>
          <button class="btn" data-android-helper-settings="${esc(item.serial)}" ${androidBusy || item.busy || item.state !== 'device' ? 'disabled' : ''}>手机输入法设置</button>
          <button class="btn" data-android-helper-check="${esc(item.serial)}" ${androidBusy || item.busy || item.state !== 'device' ? 'disabled' : ''}>检查中文输入</button>
          ${androidHelpers.has(item.serial) ? `<div class="hint">${androidHelpers.get(item.serial).active ? '中文输入助手已启用' : androidHelpers.get(item.serial).installed ? '已安装，请在手机选择 ADB Keyboard' : '中文输入助手未安装'}</div>` : ''}</td></tr>`).join('') || '<tr><td colspan="5" style="padding:24px;text-align:center">未检测到手机，请用 USB 连接并开启、授权调试。</td></tr>'}
    </tbody></table></div>
    ${displayResults.length ? `<div style="margin-top:12px"><b>上次尺寸检查（不会自动更新）</b>
      <div class="tbl-wrap"><table><thead><tr><th>设备序号</th><th>当前尺寸</th><th>横竖屏 / 旋转</th><th>结果</th></tr></thead><tbody>${displayResults.map(item => `<tr><td>${esc(item.serial)}</td><td>${item.ok ? esc(`${item.width} × ${item.height}`) : item.base_width && item.base_height ? `基础尺寸 ${esc(`${item.base_width} × ${item.base_height}`)}` : '未知'}</td><td>${esc(({ portrait: '竖屏', landscape: '横屏', square: '方屏', unknown: '方向未知' })[item.orientation] || '方向未知')}${item.rotation == null ? '' : ` / ${esc(item.rotation)}°`}</td><td>${item.ok ? '已读取' : esc(item.error || '未能检查')}</td></tr>`).join('')}</tbody></table></div>
      <div class="hint">${displayMismatch ? '布局可能不同，请逐机核对点击位置。' : '请逐机核对点击位置；屏幕尺寸相同也可能有不同的应用布局。'}${displayResults.some(item => !item.ok) ? ' 部分设备未能确认当前尺寸或方向，请重试检查。' : ''} 此检查不打开视频流，也不获取截图。</div></div>` : ''}
    <div class="toolbar" style="margin-top:12px">${[['up','上滑'],['down','下滑'],['left','左滑'],['right','右滑']].map(([direction,label]) => `<button class="btn" data-android-swipe="${direction}" ${androidBusy || !serials.length ? 'disabled' : ''}>${label}</button>`).join('')}
      <button class="btn" id="android-back" ${androidBusy || !serials.length ? 'disabled' : ''}>批量返回</button><button class="btn" id="android-home" ${androidBusy || !serials.length ? 'disabled' : ''}>批量主页</button>
      <button class="btn" id="android-tap" ${androidBusy || !serials.length || !androidTapPoint ? 'disabled' : ''}>批量点击已标记位置</button>
      <span class="hint">点击位置：${esc(androidPointLabel(androidTapPoint))} · 按比例应用于勾选手机</span></div>
    <div class="hint" id="android-progress" role="status">${esc(androidProgress)}</div>
    ${androidShot ? `<div style="margin-top:16px;border:1px solid var(--border);border-radius:12px;padding:14px">
      <b>当前截图：${esc(androidCurrent)}</b><div class="toolbar" style="margin-top:10px">${[['tap','标记批量点击'],['input','标记此机输入框'],['send','标记此机发送按钮']].map(([mode,label]) => `<button class="btn ${mode === androidMarkMode ? 'primary' : ''}" data-android-mark="${mode}" ${busy}>${label}</button>`).join('')}</div>
      <img id="android-shot-image" src="data:image/png;base64,${esc(androidShot.b64)}" alt="${esc(androidCurrent)} 手机截图，可点击标记位置" style="display:block;max-width:min(100%,360px);max-height:540px;cursor:crosshair;border:1px solid var(--border)">
      <div class="hint" style="margin-top:8px">输入框 ${esc(androidPointLabel(target.input))} · 发送按钮 ${esc(androidPointLabel(target.send))}。聊天草稿请先在手机点开输入框、保持键盘状态，再取截图标记，以免布局变化。</div>
      <label>此机聊天页顶部完整名称<input id="android-chat-text" type="text" maxlength="100" value="${esc(target.chat_text || '')}" placeholder="例如联系人或群聊名称" ${busy}></label>
    </div>` : '<div class="hint" style="margin-top:12px">点某台手机的「截图 / 标记」后，可标记点击位置与该设备的聊天输入框。</div>'}
    <div style="margin-top:18px;border:1px solid var(--border);border-radius:12px;padding:14px"><b>批量聊天草稿与预览</b>
      <div class="hint">先在每台手机人工打开目标聊天并分别标记输入框、发送按钮和聊天名称。填稿不会发送。只有界面结构、聊天名称、输入内容和按钮均可验证的手机，才支持点击已预览草稿的发送按钮。</div>
      <label>文案<textarea id="android-draft-text" rows="3" maxlength="500" ${busy}>${esc(androidText)}</textarea></label>
      <div class="hint">中文和 emoji 需先点击此机「安装中文输入助手」，再在手机输入法设置中启用并选择 ADB Keyboard 为当前输入法。系统不会自动切换输入法。输入助手与聊天界面仍待真机验证；不支持的应用可通过投屏人工输入和发送。</div>
      <div class="toolbar" style="margin-top:10px"><button class="btn primary" id="android-prepare" ${androidBusy || !serials.length ? 'disabled' : ''}>填入草稿并取预览</button><button class="btn" id="android-copy" ${busy}>复制文案供人工输入</button></div>
      ${androidPrepared ? `<div class="hint">以下是刚填入的草稿截图。请逐台核对聊天对象和文案；预览 5 分钟后失效。</div>
        <div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:12px">${shots.map(item => `<div style="max-width:220px"><b>${esc(item.serial)}</b><div class="hint">${item.ok ? '草稿已验证，尚未发送' : esc(item.error)}</div>${item.ok && item.preview ? `<img src="data:image/png;base64,${esc(item.preview.b64)}" alt="${esc(item.serial)} 已准备草稿预览" style="max-width:200px;max-height:320px">` : ''}</div>`).join('')}</div>
        <button class="btn primary" id="android-send" style="margin-top:12px" ${androidBusy || !androidPrepared.prepared_id ? 'disabled' : ''}>发送已预览草稿（${shots.filter(item => item.ok).length} 台）</button>` : ''}
    </div></details>`;
  document.getElementById('android-refresh').onclick = androidGuard(renderAndroid);
  document.getElementById('android-select-all').onclick = () => { if (androidBusy) return; androidItems.filter(item => item.state === 'device' && !item.busy).forEach(item => androidSelected.add(item.serial)); androidDraw(); };
  document.getElementById('android-clear').onclick = () => { if (androidBusy) return; androidSelected.clear(); androidDraw(); };
  document.getElementById('android-display-check').onclick = androidGuard(androidCheckDisplays);
  document.querySelectorAll('[data-android-select]').forEach(input => input.onchange = () => { if (androidBusy) return; if (input.checked && androidOnline(input.dataset.androidSelect)) androidSelected.add(input.dataset.androidSelect); else androidSelected.delete(input.dataset.androidSelect); androidDraw(); });
  document.querySelectorAll('[data-android-shot]').forEach(button => button.onclick = androidGuard(() => androidLoadShot(button.dataset.androidShot)));
  document.querySelectorAll('[data-android-mirror]').forEach(button => button.onclick = androidGuard(() => androidMirror(button.dataset.androidMirror)));
  document.querySelectorAll('[data-android-close]').forEach(button => button.onclick = androidGuard(() => androidCloseMirror(button.dataset.androidClose)));
  document.querySelectorAll('[data-android-helper-install]').forEach(button => button.onclick = androidGuard(() => androidInputHelper(button.dataset.androidHelperInstall, true, false)));
  document.querySelectorAll('[data-android-helper-settings]').forEach(button => button.onclick = androidGuard(() => androidInputHelper(button.dataset.androidHelperSettings, false, true)));
  document.querySelectorAll('[data-android-helper-check]').forEach(button => button.onclick = androidGuard(() => androidInputHelper(button.dataset.androidHelperCheck, false, false)));
  document.querySelectorAll('[data-android-swipe]').forEach(button => button.onclick = androidGuard(() => androidBatch('swipe', { direction: button.dataset.androidSwipe, duration: 500 })));
  document.getElementById('android-back').onclick = androidGuard(() => androidBatch('back'));
  document.getElementById('android-home').onclick = androidGuard(() => androidBatch('home'));
  document.getElementById('android-tap').onclick = androidGuard(() => androidBatch('tap', androidTapPoint));
  document.querySelectorAll('[data-android-mark]').forEach(button => button.onclick = () => { if (androidBusy) return; androidMarkMode = button.dataset.androidMark; androidDraw(); });
  const image = document.getElementById('android-shot-image');
  if (image) image.onclick = event => {
    if (androidBusy) return;
    const rect = image.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const point = { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
    if (androidMarkMode === 'tap') androidTapPoint = point;
    else { const current = androidTargets.get(androidCurrent) || {}; androidTargets.set(androidCurrent, { ...current, [androidMarkMode]: point }); androidInvalidateDraft(); }
    androidDraw();
  };
  const chat = document.getElementById('android-chat-text');
  if (chat) chat.oninput = () => { const current = androidTargets.get(androidCurrent) || {}; androidTargets.set(androidCurrent, { ...current, chat_text: chat.value }); androidInvalidateDraft(); const send = document.getElementById('android-send'); if (send) send.disabled = true; };
  document.getElementById('android-draft-text').oninput = event => { androidText = event.target.value; androidInvalidateDraft(); const send = document.getElementById('android-send'); if (send) send.disabled = true; };
  document.getElementById('android-copy').onclick = () => copyText(androidText);
  document.getElementById('android-prepare').onclick = androidGuard(androidPrepare);
  const send = document.getElementById('android-send');
  if (send) send.onclick = androidGuard(androidSend);
  if (typeof wallMount === 'function') wallMount();
}

async function androidRun(callback) {
  if (androidBusy) throw new Error('已有手机操作正在执行，请等待完成');
  androidBusy = true;
  androidDraw();
  try { return await callback(); }
  finally { androidBusy = false; androidDraw(); }
}
async function androidLoadShot(serial) {
  if (!androidOnline(serial)) throw new Error('请从已授权在线设备中选择');
  const request = ++androidShotRequest;
  await androidRun(async () => {
    const result = await androidCall('android_screenshot', serial);
    if (request !== androidShotRequest) return;
    androidCurrent = serial; androidShot = result;
  });
}
async function androidMirror(serial) {
  await androidRun(async () => {
    const result = await androidCall('android_mirror', serial);
    const item = androidItems.find(item => item.serial === serial);
    if (item) item.mirroring = true;
    toast(result.already_open ? '此机投屏窗口已打开' : '投屏进程已启动，请查看窗口；如窗口退出请刷新状态');
  });
}
async function androidCloseMirror(serial) {
  await androidRun(async () => {
    await androidCall('android_close_mirror', serial);
    const item = androidItems.find(item => item.serial === serial);
    if (item) item.mirroring = false;
  });
}
async function androidInputHelper(serial, install, openSettings) {
  if (!androidOnline(serial)) throw new Error('请从已授权在线设备中选择');
  await androidRun(async () => {
    const result = await androidCall('android_input_helper', serial, install, openSettings);
    androidHelpers.set(serial, result);
    toast(result.active ? '中文输入助手已启用，可填入中文草稿' : result.installed ? '助手已安装，请在手机输入法设置中选择 ADB Keyboard' : '请先安装中文输入助手');
  });
}
async function androidBatch(action, params = {}) {
  const section = curSection;
  const serials = androidSelectedOnline();
  if (!serials.length) throw new Error('请先勾选已授权设备');
  androidInvalidateDraft();
  await androidRun(async () => {
    const result = await androidCall('android_batch', serials, action, params);
    androidProgress = `操作完成 ${result.succeeded ?? (result.items || []).filter(item => item.ok).length}/${serials.length} 台。`;
    const failed = (result.items || []).filter(item => !item.ok);
    if (failed.length && curSection === section) showInfo('手机操作结果', failed.map(item => `${item.serial}：${item.error}`).join('\n'));
    else if (failed.length) toast(`${androidProgress} 部分设备失败，返回手机群控查看。`, true);
    else toast(androidProgress);
  });
}
async function androidCheckDisplays() {
  const serials = androidSelectedAuthorized(), request = androidRequest;
  if (!serials.length) throw new Error('请先勾选已授权设备');
  await androidRun(async () => {
    const result = await androidCall('android_display_info', serials);
    if (request !== androidRequest) return;
    for (const item of result.items || []) {
      if (serials.includes(item.serial)) androidDisplayResults.set(item.serial, item);
    }
    androidProgress = `已确认 ${result.succeeded ?? (result.items || []).filter(item => item.ok).length}/${serials.length} 台的当前尺寸与方向。`;
  });
}
async function androidPrepare() {
  const serials = androidSelectedOnline();
  if (!serials.length) throw new Error('请先勾选已授权设备');
  if (!androidText.length || androidText.length > 500 || /[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(androidText)) throw new Error('草稿文案需为 1 到 500 个字符，不支持控制字符');
  const targets = {};
  for (const serial of serials) {
    const target = androidTargets.get(serial);
    if (!target || !target.input || !target.send || !target.chat_text) throw new Error(`${serial}：请先标记输入框、发送按钮和聊天名称`);
    targets[serial] = { ...target };
  }
  androidInvalidateDraft();
  await androidRun(async () => {
    const result = await androidCall('android_prepare', serials, androidText, targets);
    androidPrepared = result;
    androidProgress = `已验证 ${result.succeeded ?? (result.items || []).filter(item => item.ok).length}/${serials.length} 台草稿，尚未发送。请核对下面的预览。`;
  });
}
async function androidSend() {
  const section = curSection;
  if (!androidPrepared || !androidPrepared.prepared_id) throw new Error('请先填入并检查草稿预览');
  const preparedId = androidPrepared.prepared_id;
  // This explicit button is the only UI path that calls the send API. Preparing
  // a draft, selecting a device, and viewing a screenshot never call it.
  await androidRun(async () => {
    androidPrepared = null;
    const result = await androidCall('android_send', preparedId);
    androidProgress = `已点击 ${result.succeeded ?? (result.items || []).filter(item => item.ok).length} 台经过验证的发送按钮，请在手机确认实际送达状态。`;
    const failed = (result.items || []).filter(item => !item.ok);
    if (curSection === section) showInfo('发送操作结果', androidProgress + (failed.length ? '\n\n' + failed.map(item => `${item.serial}：${item.error}`).join('\n') : ''));
    else toast(androidProgress, failed.length > 0);
  });
}

RENDER.android_control = async () => {
  try { await renderAndroid(); }
  catch (error) {
    if (curSection !== 'android_control') return;
    document.getElementById('page').innerHTML = `<div class="hint">设备读取失败：${esc(error.message)}</div><button class="btn" id="android-retry">重试</button>`;
    document.getElementById('android-retry').onclick = RENDER.android_control;
  }
};
