/* 手机 HTTP 代理配置。读取候选不会自动配置，出口检查只打开手机浏览器。 */
const androidNetworkNav = NAV.findIndex(item => item.id === 'android_control');
NAV.splice(androidNetworkNav < 0 ? NAV.length : androidNetworkNav + 1, 0,
  { id: 'android_network', name: '手机网络', ico: '🌐' });
TITLES.android_network = '手机网络';
let networkItems = [], networkHostInfo = {}, networkBusy = false, networkRequest = 0;
let networkTransport = 'usb', networkHost = '', networkPort = '', networkProgress = '';
const networkSelected = new Set();

async function networkCall(method, ...args) {
  const result = await api(method, ...args);
  if (!result || !result.ok) throw new Error(result && result.error || '网络操作失败');
  return result;
}
function networkGuard(callback) {
  return async () => { try { await callback(); } catch (error) { toast(error.message || '网络操作失败', true); } };
}
function networkSerials() {
  return networkItems.filter(item => item.state === 'device' && !item.busy && networkSelected.has(item.serial)).map(item => item.serial);
}
function networkState(state) {
  return ({ device: '已连接并授权', unauthorized: '请在手机允许 USB 调试', offline: '离线', disconnected: '未连接' })[state] || state;
}
async function renderAndroidNetwork() {
  if (networkBusy) throw new Error('已有网络操作正在执行，请等待完成');
  const request = ++networkRequest;
  const [phones, host] = await Promise.allSettled([networkCall('android_network_status'), api('host_network_status')]);
  if (request !== networkRequest || curSection !== 'android_network') return;
  if (phones.status === 'rejected') throw phones.reason;
  networkItems = phones.value.items || [];
  networkHostInfo = host.status === 'fulfilled' && host.value ? host.value : { error: host.status === 'rejected' ? host.reason.message : '电脑检测失败' };
  const online = new Set(networkItems.filter(item => item.state === 'device' && !item.busy).map(item => item.serial));
  for (const serial of networkSelected) if (!online.has(serial)) networkSelected.delete(serial);
  networkDraw();
}
function networkDraw() {
  if (curSection !== 'android_network') return;
  const serials = networkSerials(), busy = networkBusy ? 'disabled' : '';
  const candidates = networkHostInfo.local_proxy_candidates || [];
  const addresses = networkHostInfo.computer_addresses || [];
  document.getElementById('page').innerHTML = `
    <div class="hint">手机可通过 USB 使用电脑现有的 HTTP 代理。USB 方式适合电脑代理只监听 127.0.0.1 的情况；手机需保持 USB 连接。电脑代理软件须已连接可用节点。</div>
    <div class="hint" style="margin-top:8px">Shadowrocket、Clash 等软件可提供 HTTP 代理端口。系统 HTTP 代理只覆盖支持代理的应用，无法保证抖音、小红书或微信全部流量经节点；完整应用流量需要手机上的 VPN 客户端，并在手机首次确认 VPN 授权。节点名称、延迟和本页配置结果均不能证明手机的实际出口。</div>
    <div class="toolbar" style="margin-top:12px"><button class="btn" id="network-refresh" ${busy}>刷新设备与电脑候选</button><button class="btn" id="network-select-all" ${busy}>选择已授权空闲手机</button><button class="btn" id="network-clear" ${busy}>清空勾选</button><span class="hint">已选 ${serials.length} 台</span></div>
    <div class="tbl-wrap"><table><thead><tr><th>选择</th><th>手机</th><th>状态</th><th>系统 HTTP 代理</th><th>恢复</th></tr></thead><tbody>
      ${networkItems.map(item => `<tr><td><input type="checkbox" data-network-select="${esc(item.serial)}" ${networkSelected.has(item.serial) ? 'checked' : ''} ${networkBusy || item.busy || item.state !== 'device' ? 'disabled' : ''}></td><td>${esc(item.model || 'Android')}<br><small>${esc(item.serial)}</small></td><td>${esc(networkState(item.state))}${item.busy ? '<br><small>手机任务执行中</small>' : ''}${item.error ? `<br><small>${esc(item.error)}</small>` : ''}${item.note ? `<br><small>${esc(item.note)}</small>` : ''}</td><td>${item.settings && item.settings.configured ? esc(item.settings.host) + ':' + esc(item.settings.port) : '未读取到已配置地址'}<br><small>出口尚未验证${item.transport ? ' · ' + (item.transport === 'usb' ? 'USB' : '局域网') : ''}</small></td><td>${item.saved_original ? '已保存原设置，可恢复' : '没有原设置快照'}</td></tr>`).join('') || '<tr><td colspan="5" style="padding:24px;text-align:center">未检测到手机。USB 连接后开启调试并在手机授权，再点击刷新。</td></tr>'}
    </tbody></table></div>
    <div style="margin-top:18px;border:1px solid var(--border);border-radius:12px;padding:14px"><b>配置选中手机</b>
      <label>连接方式<select id="network-transport" ${busy}><option value="usb" ${networkTransport === 'usb' ? 'selected' : ''}>USB 使用电脑本机 HTTP 代理</option><option value="lan" ${networkTransport === 'lan' ? 'selected' : ''}>同一局域网共享 HTTP 代理</option></select></label>
      <label>电脑 HTTP 代理端口<input id="network-port" inputmode="numeric" value="${esc(networkPort)}" placeholder="例如 Shadowrocket 的 1082；使用 HTTP 端口" ${busy}></label>
      <div class="hint">检测到的本机端口只是候选，尚未验证 HTTP 支持和节点出口；点击候选只填写表单，再点击「应用代理配置」才会操作手机。</div>
      <div class="toolbar" style="margin-top:8px">${candidates.map((item, index) => `<button class="btn" data-network-port="${index}" ${busy}>使用候选 ${esc(item.host)}:${esc(item.port)}</button>`).join('') || '<span class="hint">未检测到本机监听候选，可在代理软件设置中查看 HTTP 端口并手动填写。</span>'}</div>
      ${networkTransport === 'lan' ? `<label>电脑局域网地址<input id="network-host" value="${esc(networkHost)}" placeholder="使用检测到的电脑局域网地址，不含协议或密码" ${busy}></label><div class="toolbar">${addresses.map((host, index) => `<button class="btn" data-network-host="${index}" ${busy}>使用电脑地址 ${esc(host)}</button>`).join('')}</div><div class="hint">手机与电脑须在同一局域网，电脑代理须允许局域网访问。工作台不会自动打开代理共享权限。LAN 模式不能填写 127.0.0.1 / localhost。</div>` : '<div class="hint" style="margin-top:8px">USB 在所选手机建立单个 17890 端口转发；如已有其他任务占用该端口，将报错。拔掉 USB 后此代理无法继续使用，请点击恢复原设置。</div>'}
      ${networkHostInfo.error ? `<div class="hint">电脑候选检测：${esc(networkHostInfo.error)}</div>` : ''}
      <div class="toolbar" style="margin-top:12px"><button class="btn primary" id="network-apply" ${networkBusy || !serials.length ? 'disabled' : ''}>应用代理配置（${serials.length} 台）</button><button class="btn" id="network-restore" ${networkBusy || !serials.length ? 'disabled' : ''}>恢复原设置</button><button class="btn" id="network-settings" ${networkBusy || !serials.length ? 'disabled' : ''}>打开手机 VPN 设置</button><button class="btn" id="network-exit" ${networkBusy || !serials.length ? 'disabled' : ''}>手机浏览器查看出口 IP</button></div>
      <div class="hint" id="network-progress" role="status">${esc(networkProgress)}</div>
    </div>`;
  document.getElementById('network-refresh').onclick = networkGuard(renderAndroidNetwork);
  document.getElementById('network-select-all').onclick = () => { if (networkBusy) return; networkItems.filter(item => item.state === 'device' && !item.busy).forEach(item => networkSelected.add(item.serial)); networkDraw(); };
  document.getElementById('network-clear').onclick = () => { if (networkBusy) return; networkSelected.clear(); networkDraw(); };
  document.querySelectorAll('[data-network-select]').forEach(input => input.onchange = () => { if (networkBusy) return; if (input.checked && networkItems.some(item => item.serial === input.dataset.networkSelect && item.state === 'device' && !item.busy)) networkSelected.add(input.dataset.networkSelect); else networkSelected.delete(input.dataset.networkSelect); networkDraw(); });
  document.getElementById('network-transport').onchange = event => { if (networkBusy) return; networkTransport = event.target.value; networkDraw(); };
  document.getElementById('network-port').oninput = event => { if (!networkBusy) networkPort = event.target.value; };
  const host = document.getElementById('network-host');
  if (host) host.oninput = event => { if (!networkBusy) networkHost = event.target.value; };
  document.querySelectorAll('[data-network-port]').forEach(button => button.onclick = () => { if (networkBusy) return; const candidate = candidates[Number(button.dataset.networkPort)]; if (candidate) { networkPort = String(candidate.port); networkDraw(); } });
  document.querySelectorAll('[data-network-host]').forEach(button => button.onclick = () => { if (networkBusy) return; networkHost = addresses[Number(button.dataset.networkHost)] || ''; networkDraw(); });
  document.getElementById('network-apply').onclick = networkGuard(() => networkAct('apply'));
  document.getElementById('network-restore').onclick = networkGuard(() => networkAct('restore'));
  document.getElementById('network-settings').onclick = networkGuard(() => networkAct('settings'));
  document.getElementById('network-exit').onclick = networkGuard(() => networkAct('exit'));
}
async function networkAct(action) {
  if (networkBusy) throw new Error('已有网络操作正在执行，请等待完成');
  const serials = networkSerials();
  if (!serials.length) throw new Error('请先勾选已授权空闲手机');
  const methods = { apply: 'android_network_apply', restore: 'android_network_restore', settings: 'android_network_settings', exit: 'android_network_check_exit' };
  if (!methods[action]) throw new Error('网络操作无效');
  let args = [serials];
  if (action === 'apply') {
    if (!/^[0-9]{1,5}$/.test(networkPort) || Number(networkPort) < 1 || Number(networkPort) > 65535) throw new Error('请先选择候选或填写 1 到 65535 的电脑 HTTP 代理端口');
    if (networkTransport === 'lan' && !networkHost.trim()) throw new Error('请填写电脑局域网地址');
    args = [serials, networkTransport === 'usb' ? '127.0.0.1' : networkHost.trim(), Number(networkPort), networkTransport];
  }
  networkBusy = true; ++networkRequest; networkDraw();
  try {
    const result = await networkCall(methods[action], ...args);
    const items = result.items || [];
    const success = result.succeeded ?? items.filter(item => item.ok).length;
    const labels = { apply: '系统 HTTP 代理已配置，实际出口尚未验证', restore: '恢复操作已完成', settings: '已打开手机 VPN 设置；请在手机检查客户端和授权', exit: '已打开手机浏览器，请在手机查看出口 IP；工作台尚未验证出口' };
    networkProgress = `${success}/${serials.length} 台：${labels[action]}。` + items.filter(item => !item.ok).map(item => `\n${item.serial}：${item.error}`).join('');
    toast(`${success}/${serials.length} 台网络操作完成${action === 'apply' || action === 'exit' ? '，出口尚未验证' : ''}`, success !== serials.length);
  } finally { networkBusy = false; networkDraw(); }
  // A successful write is still successful if the following read-only refresh
  // fails. Keep the operation result and label the refresh failure separately.
  if (curSection === 'android_network') {
    try { await renderAndroidNetwork(); }
    catch (error) { networkProgress += `\n状态刷新失败：${error.message}`; networkDraw(); }
  }
}
RENDER.android_network = async () => {
  try { await renderAndroidNetwork(); }
  catch (error) {
    if (curSection !== 'android_network') return;
    document.getElementById('page').innerHTML = `<div class="hint">手机网络读取失败：${esc(error.message)}</div><button class="btn" id="network-retry">重试</button>`;
    document.getElementById('network-retry').onclick = RENDER.android_network;
  }
};
