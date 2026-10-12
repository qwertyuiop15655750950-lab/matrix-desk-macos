/* 统一账号工作台：平台与账号联合标识，复用原有操作和登录目录。 */
for (let i = NAV.length - 1; i >= 0; i--) if (['accounts', 'douyin'].includes(NAV[i].id)) NAV.splice(i, 1);
NAV.splice(1, 0, { id: 'account_hub', name: '账号工作台', ico: '👥' });
TITLES.account_hub = '账号工作台';

const HUB_PLATFORMS = { xiaohongshu: '小红书', douyin: '抖音', channels: '视频号' };
let hubPlatform = '', hubItems = [], hubCounts = {}, hubBusy = false, hubProgress = '';
let hubCollecting = false, hubCollectProgress = '';
let hubRenderRequest = 0;
let hubSearch = '', hubGroup = '', hubFocus = '', hubView = 'accounts', hubPaneRequest = 0;
const hubSelected = new Set();

function hubKey(account) { return String(account.key || `${account.platform}:${account.id}`); }
function hubVisibleItems() {
  const query = hubSearch.trim().toLocaleLowerCase();
  return hubItems.filter(a => (!hubPlatform || a.platform === hubPlatform)
    && (!hubGroup || (a.group_name || '默认分组') === hubGroup)
    && (!query || [a.nickname, a.id, a.group_name, a.notes, a.identity?.user_id, a.identity?.public_id, HUB_PLATFORMS[a.platform]].join(' ').toLocaleLowerCase().includes(query)));
}
function hubFocusedAccount() { return hubItems.find(a => hubKey(a) === hubFocus); }
async function hubNavigateLegacy(section) {
  hubPlatform = section === 'douyin' ? 'douyin' : 'xiaohongshu';
  hubSearch = ''; hubGroup = ''; hubFocus = ''; hubView = 'accounts';
  if (section === 'douyin' && dyTab !== 'accounts') hubView = dyTab;
  return showSection('account_hub');
}
async function hubShowView(view, key = '') {
  const account = hubItems.find(a => hubKey(a) === key);
  hubFocus = account ? hubKey(account) : '';
  hubView = view;
  if (account && account.platform === 'xiaohongshu') state.selAccount = Number(account.id);
  if (view === 'comments' || view === 'leads') {
    dyTab = view; dyAccount = account ? String(account.id) : ''; dyQuery = ''; dyMatched = false;
  }
  return hubDraw();
}
async function hubRenderPane() {
  const target = document.getElementById('hub-pane'), request = ++hubPaneRequest;
  if (!target || hubView === 'accounts') return;
  const view = hubView, focus = hubFocus;
  target.innerHTML = '<div class="hint" role="status">正在加载…</div>';
  try {
    if (view === 'settings') await renderXhsAccountTools(target);
    else if (view === 'metrics') await renderAccountMetrics(target);
    else await dyRenderView();
  } catch (error) {
    if (request !== hubPaneRequest || curSection !== 'account_hub' || hubView !== view || hubFocus !== focus || !target.isConnected) return;
    target.innerHTML = `<div class="hint">加载失败：${esc(error.message)}</div><button class="btn" id="hub-pane-retry">重新加载</button>`;
    document.getElementById('hub-pane-retry').onclick = hubGuard(hubRenderPane);
  }
}
async function hubAccountAction(action, key) {
  const account = hubItems.find(a => hubKey(a) === key);
  if (!account) throw new Error('账号已不存在，请刷新列表');
  if (action === 'collect') return hubCollectModal(account);
  if (action === 'identity') { await hubCall('identity_sync',account.id); toast('主页标识已保存'); return renderAccountHub(); }
  if (action === 'comments' || action === 'leads' || action === 'settings') return hubShowView(action, key);
  if (account.platform !== 'xiaohongshu') return hubShowView('accounts', key);
  if (action === 'publish') {
    await showSection('publish');
    if (curSection !== 'publish') return;
    const checkbox = Array.from(document.querySelectorAll('[data-aid]')).find(input => Number(input.dataset.aid) === Number(account.id));
    if (checkbox) { checkbox.checked = true; state.pubAccounts.add(Number(account.id)); }
    if (typeof renderPublishFocus === 'function') await renderPublishFocus();
  } else if (action === 'messages') {
    await showSection('messages');
    if (curSection !== 'messages') return;
    const select = document.getElementById('m-acct');
    if (select) select.value = String(account.id);
    if (typeof messageSelectAccount === 'function') await messageSelectAccount(Number(account.id));
  }
}
function hubPaneHeader() {
  const account = hubFocusedAccount();
  if (!account && hubView === 'accounts') return '';
  const actions = account ? (account.platform === 'xiaohongshu'
    ? [['settings', '账号设置'], ['publish', '发布'], ['messages', '消息'], ['collect', '采集评论'], ['identity', '同步账号标识']]
    : account.platform === 'douyin' ? [['comments', '评论归档'], ['leads', '线索跟进'], ['collect', '采集评论']] : [])
    : hubView === 'metrics' ? [] : [['comments', '抖音评论归档'], ['leads', '抖音线索跟进']];
  return `<div class="hub-workspace card"><div class="hub-workspace-title"><b>${account ? esc(HUB_PLATFORMS[account.platform] + ' · ' + account.nickname + ' (#' + Number(account.id) + ')') : hubView === 'metrics' ? '小红书数据监测' : '抖音 · 全部账号'}</b><button class="btn" id="hub-back">收起工作区</button></div>
    <div class="toolbar">${account ? `<button class="btn" data-hub-open="${esc(hubKey(account))}" ${workspaceBrowserBusy() ? 'disabled' : ''}>打开浏览器 / 扫码</button><button class="btn" data-hub-edit="${esc(hubKey(account))}">编辑档案</button>` : ''}
      ${actions.map(([action, label]) => `<button class="btn ${hubView === action ? 'primary' : ''}" data-hub-action="${action}" data-hub-account="${esc(account ? hubKey(account) : '')}">${label}</button>`).join('')}</div>
    ${account && account.platform === 'channels' ? '<div class="hint">视频号当前支持独立浏览器扫码登录和账号档案管理。</div>' : ''}
    <div id="hub-pane"></div></div>`;
}
function hubWaitingItems() { return hubItems.filter(a => hubSelected.has(hubKey(a)) && !a.running); }
async function hubCall(method, ...args) {
  const result = await api(method, ...args);
  if (!result || !result.ok) throw new Error((result && result.error) || '操作失败');
  return result;
}
function hubGuard(callback) {
  return async () => {
    try { await callback(); }
    catch (error) { toast(error.message || '操作失败', true); }
  };
}
function hubCSV(rows) {
  return rows.map(row => row.map(value => {
    let text = String(value == null ? '' : value);
    if (/^[\s]*[=+\-@]/.test(text)) text = "'" + text;
    return `"${text.replace(/"/g, '""')}"`;
  }).join(',')).join('\r\n');
}
function hubUpdateSelection() {
  const visible = hubVisibleItems();
  const selectedVisible = visible.filter(a => hubSelected.has(hubKey(a))).length;
  const selectAll = document.getElementById('hub-select-all');
  if (selectAll) {
    selectAll.checked = visible.length > 0 && selectedVisible === visible.length;
    selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visible.length;
  }
  const summary = document.getElementById('hub-selection');
  if (summary) summary.textContent = `已选 ${hubSelected.size} 个 · ${hubWaitingItems().length} 个待打开（切换平台保留勾选）`;
  const batch = document.getElementById('hub-batch');
  if (batch) batch.disabled = hubBusy || hubCollecting || workspaceBrowserBusy() || !hubWaitingItems().length;
  const progress = document.getElementById('hub-progress');
  if (progress) progress.textContent = hubProgress;
}

async function renderAccountHub() {
  const request = ++hubRenderRequest;
  const result = await hubCall('hub_accounts', '');
  if (request !== hubRenderRequest) return;
  hubItems = result.items || [];
  hubCounts = result.counts || {};
  const existing = new Set(hubItems.map(hubKey));
  for (const key of hubSelected) if (!existing.has(key)) hubSelected.delete(key);
  if (curSection !== 'account_hub') return;
  if (!existing.has(hubFocus)) { hubFocus = ''; if (hubView === 'settings') hubView = 'accounts'; }
  return hubDraw();
}

function hubDraw() {
  if (typeof browserDeskStop === 'function') browserDeskStop();
  if (curSection !== 'account_hub') return;
  if (hubView === 'metrics' && typeof metricsRememberDraft === 'function') metricsRememberDraft();
  const visible = hubVisibleItems();
  const browserBusy = hubBusy || hubCollecting || workspaceBrowserBusy();
  const groups = [...new Set(hubItems.map(a => a.group_name || '默认分组'))].sort();
  document.getElementById('page').innerHTML = `
    <div class="account-summary">${hubItems.length} 个账号 <span>· ${hubItems.filter(a => a.running).length} 个浏览器已打开</span></div>
    <div class="toolbar" style="margin-top:12px">
      ${[['', '全部平台'], ...Object.entries(HUB_PLATFORMS)].map(([platform, label]) => `<button class="btn ${hubPlatform === platform ? 'primary' : ''}" data-hub-platform="${platform}">${esc(label)}</button>`).join('')}
      <button class="btn" id="hub-browser-desk">浏览器工作区</button>
      <button class="btn" id="hub-refresh" ${hubBusy ? 'disabled' : ''}>刷新状态</button>
    </div>
    <div class="toolbar hub-filters">
      <input type="text" id="hub-search" aria-label="搜索账号" placeholder="搜索账号名、备注或编号" value="${esc(hubSearch)}">
      <select id="hub-group" aria-label="筛选分组"><option value="">全部分组</option>${groups.map(group => `<option value="${esc(group)}" ${hubGroup === group ? 'selected' : ''}>${esc(group)}</option>`).join('')}</select>
      <span class="hint">显示 ${visible.length} / ${hubItems.length} 个账号</span>
    </div>
    ${hubPaneHeader()}
    <div class="toolbar">
      <button class="btn primary" id="hub-add" ${hubBusy ? 'disabled' : ''}>新增账号</button>
      <button class="btn" id="hub-import" ${hubBusy ? 'disabled' : ''}>导入账号</button>
      <details class="inline-more"><summary>更多</summary><div class="more-actions">
      <button class="btn" id="hub-template">下载导入模板</button>
      <button class="btn" id="hub-export">导出当前列表</button>
      <button class="btn" id="hub-xhs-archive" hidden>小红书采集归档</button>
      <button class="btn" id="hub-dy-archive" hidden>抖音采集归档</button>
      <button class="btn" id="hub-metrics">数据监测</button>
      </div></details>
      <button class="btn" id="hub-publish-selected">去批量发布</button>
    </div>
    <div class="toolbar">
      <button class="btn primary" id="hub-batch">打开下一批（最多3个）</button>
      <button class="btn" id="hub-clear" ${hubBusy ? 'disabled' : ''}>取消选择</button>
      <span class="hint" id="hub-selection"></span>
    </div>
    <div class="hint" id="hub-progress" role="status" style="margin-bottom:10px"></div>
    <div class="hint" id="hub-collection-progress" role="status" style="margin-bottom:10px">${esc(hubCollectProgress)}</div>
    <div class="hint" id="hub-material-progress" role="status">${esc(typeof materialCaptureProgress === 'undefined' ? '' : materialCaptureProgress)}</div>
    ${browserBusy ? '<div class="hint" role="status">浏览器任务执行中，完成后可继续操作。</div>' : ''}
    <div class="tbl-wrap hub-account-table"><table><thead><tr>
      <th><input type="checkbox" id="hub-select-all" aria-label="全选当前筛选账号" ${hubBusy || !visible.length ? 'disabled' : ''}></th>
      <th>平台</th><th>账号名称</th><th>分组</th><th>备注</th><th>浏览器状态</th><th>操作</th>
    </tr></thead><tbody>${visible.map(a => `<tr class="${hubFocus === hubKey(a) ? 'sel' : ''}">
      <td><input type="checkbox" class="hub-row-select" data-hub-key="${esc(hubKey(a))}" aria-label="选择${esc(a.nickname)}" ${hubSelected.has(hubKey(a)) ? 'checked' : ''} ${hubBusy ? 'disabled' : ''}></td>
      <td>${esc(HUB_PLATFORMS[a.platform] || a.platform)}</td><td><b>${esc(a.nickname)}</b><div class="hint">本机账号 #${Number(a.id)}</div>${a.identity && a.identity.user_id ? `<div class="hint">${a.identity.public_id ? '小红书号：' + esc(a.identity.public_id) : '主页 ID：' + esc(a.identity.user_id)}</div>` : ''}</td>
      <td>${esc(a.group_name || '—')}</td><td style="white-space:pre-wrap;max-width:240px">${esc(a.notes || '—')}</td>
      <td><span class="badge ${a.running ? 'done' : 'pending'}">${a.running ? '浏览器已打开' : '未打开'}</span></td>
      <td><button class="btn" data-hub-open="${esc(hubKey(a))}" ${browserBusy ? 'disabled' : ''}>${a.running ? '显示浏览器' : '打开浏览器 / 扫码'}</button> <button class="btn" data-hub-workspace="${esc(hubKey(a))}">账号详情</button> <details class="inline-more row-more"><summary>更多</summary><div class="more-actions"><button class="btn" data-hub-edit="${esc(hubKey(a))}" ${hubBusy ? 'disabled' : ''}>编辑</button>

        ${a.platform === 'xiaohongshu' || a.platform === 'douyin' ? `<button class="btn" data-hub-collect="${esc(hubKey(a))}" ${browserBusy ? 'disabled' : ''}>采集评论</button>` : ''}
        ${window.matrixExtrasEnabled===false?'':`<button class="btn" data-hub-material="${esc(hubKey(a))}" ${browserBusy ? 'disabled' : ''}>提取当前页素材</button>`}</div></details></td>
    </tr>`).join('') || '<tr><td colspan="7" style="padding:30px;text-align:center;color:var(--muted)">暂无账号，可新增账号或批量导入</td></tr>'}</tbody></table></div>
    <details class="secondary-info"><summary>登录与打开说明</summary><p class="hint">登录状态保存在本机，失效后重新扫码。浏览器已打开不代表仍然登录。每批最多打开 3 个，已打开的账号自动跳过；批量发布一次使用一个账号。视频号扫码入口尚未真机验证。</p></details>`;

  document.querySelectorAll('[data-hub-platform]').forEach(button => button.onclick = () => {
    hubPlatform = button.dataset.hubPlatform;
    hubFocus = ''; hubView = 'accounts';
    return hubDraw();
  });
  const search = document.getElementById('hub-search');
  search.oninput = () => {
    hubSearch = search.value;
    const cursor = search.selectionStart;
    hubDraw();
    const next = document.getElementById('hub-search');
    if (next && typeof next.focus === 'function') { next.focus(); next.setSelectionRange(cursor, cursor); }
  };
  document.getElementById('hub-group').onchange = event => { hubGroup = event.target.value; return hubDraw(); };
  const back = document.getElementById('hub-back');
  if (back) back.onclick = () => { hubFocus = ''; hubView = 'accounts'; return hubDraw(); };
  document.querySelectorAll('[data-hub-workspace]').forEach(button => button.onclick = hubGuard(() => {
    const account = hubItems.find(a => hubKey(a) === button.dataset.hubWorkspace);
    return hubShowView(account.platform === 'douyin' ? 'comments' : account.platform === 'xiaohongshu' ? 'settings' : 'accounts', hubKey(account));
  }));
  document.querySelectorAll('[data-hub-action]').forEach(button => button.onclick = hubGuard(() => hubAccountAction(button.dataset.hubAction, button.dataset.hubAccount)));
  document.querySelectorAll('.hub-row-select').forEach(input => input.onchange = () => {
    if (input.checked) hubSelected.add(input.dataset.hubKey);
    else hubSelected.delete(input.dataset.hubKey);
    hubUpdateSelection();
  });
  document.getElementById('hub-select-all').onchange = event => {
    for (const account of visible) {
      if (event.target.checked) hubSelected.add(hubKey(account));
      else hubSelected.delete(hubKey(account));
    }
    hubDraw();
  };
  document.getElementById('hub-clear').onclick = () => { hubSelected.clear(); hubDraw(); };
  document.getElementById('hub-browser-desk').onclick = hubGuard(() => browserDeskOpen());
  document.getElementById('hub-refresh').onclick = hubGuard(renderAccountHub);
  document.getElementById('hub-add').onclick = hubGuard(() => hubAccountModal());
  document.getElementById('hub-import').onclick = hubGuard(hubImportModal);
  document.getElementById('hub-template').onclick = () => download('多平台账号导入模板.csv', 'platform,nickname,group_name,notes\r\n');
  document.getElementById('hub-export').onclick = () => {
    download('多平台账号.csv', hubCSV([['platform', 'nickname', 'group_name', 'notes'], ...visible.map(a => [a.platform, a.nickname, a.group_name, a.notes])]));
    toast(`已导出 ${visible.length} 个账号档案`);
  };
  document.getElementById('hub-xhs-archive').onclick = hubGuard(hubArchiveModal);
  document.getElementById('hub-metrics').onclick = hubGuard(() => hubShowView('metrics'));
  document.getElementById('hub-publish-selected').onclick = hubGuard(async () => {
    const selected = hubItems.filter(a => hubSelected.has(hubKey(a)) && a.platform === 'xiaohongshu');
    if (!selected.length) throw new Error('请先勾选要发布的小红书账号');
    await showSection('publish');
    if (curSection !== 'publish') return;
    selected.forEach(a => { const input = Array.from(document.querySelectorAll('[data-aid]')).find(input => Number(input.dataset.aid) === Number(a.id)); if (input) { input.checked = true; state.pubAccounts.add(Number(a.id)); } });
    if (typeof renderPublishFocus === 'function') await renderPublishFocus();
    toast(`已选 ${selected.length} 个小红书账号，请选择文案`);
  });
  document.getElementById('hub-dy-archive').onclick = hubGuard(() => hubShowView('comments'));
  document.getElementById('hub-batch').onclick = hubGuard(() => hubOpenAccounts(hubWaitingItems().slice(0, 3)));
  document.querySelectorAll('[data-hub-edit]').forEach(button => button.onclick = hubGuard(() => hubAccountModal(hubItems.find(a => hubKey(a) === button.dataset.hubEdit))));
  document.querySelectorAll('[data-hub-open]').forEach(button => button.onclick = hubGuard(() => typeof browserDeskOpen === 'function' ? browserDeskOpen(button.dataset.hubOpen) : hubOpenAccounts([hubItems.find(a => hubKey(a) === button.dataset.hubOpen)])));
  document.querySelectorAll('[data-hub-collect]').forEach(button => button.onclick = hubGuard(() => hubCollectModal(hubItems.find(a => hubKey(a) === button.dataset.hubCollect))));
  document.querySelectorAll('[data-hub-material]').forEach(button => button.onclick = hubGuard(() => materialCaptureModal(hubItems.find(a => hubKey(a) === button.dataset.hubMaterial))));
  hubUpdateSelection();
  return hubRenderPane();
}

async function hubCollectModal(account) {
  if (!account) return;
  if (hubBusy || hubCollecting || workspaceBrowserBusy()) throw new Error('已有浏览器任务正在执行，请等待完成');
  if (!['xiaohongshu', 'douyin'].includes(account.platform)) throw new Error('视频号目前支持扫码登录，采集仍需页面校准');
  const section = curSection;
  const domain = account.platform === 'xiaohongshu' ? 'xiaohongshu.com' : 'douyin.com';
  const label = HUB_PLATFORMS[account.platform];
  const keywords = (await hubCall('dy_keywords')).items || [];
  if (curSection !== section) return;
  const data = await workspaceModal(`${label}采集评论 · ${account.nickname}`, `
    <div class="hint">先使用此账号的独立浏览器扫码登录，再粘贴浏览器地址栏里的完整${label}内容链接。自有采集器直接读取同一账号页面；原插件通道继续保留。可在浏览器浮动面板里暂停、继续或停止。页面结构尚未真机校准，结果以实际加载内容为准。外部工具缺失时会改用基础插件，结果会显示实际通道。</div>
    <label>完整内容链接<input type="url" name="url" placeholder="https://www.${domain}/${account.platform === 'xiaohongshu' ? 'explore' : 'video'}/…"></label>
    <label>采集通道<select name="engine">${workspaceEngineOptions('builtin', true)}</select></label>
    <label>匹配关键词（每行一个，小红书与抖音共用）<textarea name="keywords" rows="5">${esc(keywords.join('\n'))}</textarea></label>
    <div class="hint">请设置至少一个关键词。仅保存页面实际提供的昵称和用户标识。</div>`, '开始采集');
  if (!data) return;
  if (hubBusy || hubCollecting || workspaceBrowserBusy()) throw new Error('已有浏览器任务正在执行，请等待完成');
  const url = workspaceContentURL(data.url, account.platform);
  const engine = workspaceEngine(data.engine, 'plugin', true);
  const words = data.keywords.split(/\r?\n/).map(word => word.trim()).filter(Boolean);
  if (!words.length) throw new Error('请设置至少一个采集关键词');
  const task = { type: 'collect' };
  window.matrixWorkspaceBrowserTask = task;
  hubCollecting = true;
  hubCollectProgress = `正在通过${workspaceEngineName(engine)}采集：${label} · ${account.nickname}。可在浏览器面板暂停或停止。`;
  hubDraw();
  let result;
  try {
    await hubCall('dy_keywords', words);
    result = await hubCall('hub_collect', account.platform, account.id, url, engine);
    hubCollectProgress = `采集${result.timed_out?'超时停止，已保存当前结果':result.stopped?'已停止，保留当前结果':'完成'}：已读取 ${result.read ?? result.fetched} 条评论，关键词命中 ${result.fetched} 条，新增归档 ${result.added} 条。`;
  } catch (error) {
    hubCollectProgress = `采集未完成：${error.message}`;
    throw error;
  } finally {
    hubCollecting = false;
    if (window.matrixWorkspaceBrowserTask === task) window.matrixWorkspaceBrowserTask = null;
    workspaceRefreshBrowserControls();
  }
  const missing = Number.isFinite(result.missing_ids) ? `\n其中 ${result.missing_ids} 条未提供用户ID。` : '';
  workspaceResult('采集结果', `${hubCollectProgress}${missing}${result.engine ? '\n采集通道：' + workspaceEngineName(result.engine) : ''}${workspaceCollectionNotes(result)}\n页面结构、登录状态和加载范围会影响结果；0 条也可能表示选择器未匹配到评论。`, section);
  try { await renderAccountHub(); }
  catch (error) { toast(`采集结果已保存，账号状态刷新失败：${error.message}`, true); }
}

async function hubArchiveModal() {
  const section = curSection;
  const result = await hubCall('hub_collected', 'xiaohongshu');
  if (curSection !== section) return;
  const fields = ['nickname', 'user_id', 'keyword', 'source_url', 'comment_text', 'created_at'];
  const rows = (result.items || []).map(row => Object.fromEntries(fields.map(field => [field, row[field] == null ? '' : row[field]])));
  const closed = workspaceModal(`小红书采集归档（${rows.length}条）`, `
    <div class="toolbar"><button class="btn" id="hub-archive-csv">导出 CSV</button><button class="btn" id="hub-archive-json">导出 JSON</button></div>
    <div class="tbl-wrap"><table><thead><tr><th>昵称 / 用户ID</th><th>关键词</th><th>评论</th><th>来源</th><th>归档时间</th></tr></thead><tbody>
    ${rows.map(row => `<tr><td>${esc(row.nickname || '未提供昵称')}<br><small>${esc(row.user_id || '未提供ID')}</small></td><td>${esc(row.keyword)}</td>
      <td style="white-space:pre-wrap;min-width:160px;max-width:280px">${esc(row.comment_text)}</td>
      <td style="word-break:break-all;max-width:180px">${esc(row.source_url)}</td><td>${esc(row.created_at)}</td></tr>`).join('') || '<tr><td colspan="5" style="padding:24px;text-align:center">暂无归档，请先通过账号的「采集评论」入口采集</td></tr>'}
    </tbody></table></div>`, '关闭');
  const csvButton = document.getElementById('hub-archive-csv'), jsonButton = document.getElementById('hub-archive-json');
  if (csvButton) csvButton.onclick = () => download('小红书评论归档.csv', hubCSV([fields, ...rows.map(row => fields.map(field => row[field]))]));
  if (jsonButton) jsonButton.onclick = () => download('小红书评论归档.json', JSON.stringify(rows, null, 2));
  await closed;
}

async function hubAccountModal(account = {}) {
  const data = await workspaceModal(account.id ? '编辑账号档案' : '新增浏览器账号', `
    ${account.id ? `<input type="hidden" name="platform" value="${esc(account.platform)}"><div class="hint">平台：${esc(HUB_PLATFORMS[account.platform])}</div>` : `<label>平台<select name="platform">${Object.entries(HUB_PLATFORMS).map(([platform, label]) => `<option value="${platform}" ${platform === (hubPlatform || 'xiaohongshu') ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>`}
    <label>账号名称<input type="text" name="nickname" value="${esc(account.nickname || '')}" placeholder="用于区分扫码登录的账号"></label>
    <label>分组<input type="text" name="group_name" value="${esc(account.group_name || '')}"></label>
    <label>备注<textarea name="notes">${esc(account.notes || '')}</textarea></label>
    <div class="hint">此处添加本机账号档案。保存后打开浏览器，用已有平台账号扫码登录。</div>`);
  if (!data) return;
  if (!data.nickname.trim()) throw new Error('请填写账号名称');
  await hubCall('hub_save', data.platform, {
    ...(account.id ? { id: account.id } : {}),
    nickname: data.nickname.trim(), group_name: data.group_name.trim(), notes: data.notes,
  });
  toast('账号档案已保存');
  try { await renderAccountHub(); }
  catch (error) { toast(`账号已保存，页面刷新失败：${error.message}`, true); }
}

async function hubImportModal() {
  const section = curSection;
  const data = await workspaceModal('批量导入账号档案', `
    <div class="hint">CSV 表头：platform,nickname,group_name,notes<br>platform 填 xiaohongshu（小红书）、douyin（抖音）或 channels（视频号）。JSON 使用相同字段组成数组。同平台同名称会跳过；导入后仍需逐个扫码。</div>
    <label>选择 CSV / JSON 文件<input type="file" id="hub-import-file" accept=".csv,.json,text/csv,application/json"></label>
    <div class="hint" id="hub-file-status" role="status"></div>
    <label>或粘贴内容<textarea name="text" id="hub-import-text" rows="9" placeholder="platform,nickname,group_name,notes"></textarea></label>`, '导入档案');
  if (!data) return;
  if (!data.text.trim()) throw new Error('请选择文件或粘贴导入内容');
  const result = await hubCall('hub_import', data.text);
  workspaceResult('导入结果', `新增 ${result.added} 个账号档案，跳过 ${result.skipped} 个重复记录。\n打开浏览器后，请使用相应账号手动扫码登录。`, section);
  try { await renderAccountHub(); }
  catch (error) { toast(`账号已导入，页面刷新失败：${error.message}`, true); }
}

async function hubOpenAccounts(accounts) {
  if (hubBusy || hubCollecting || workspaceBrowserBusy()) throw new Error('已有浏览器任务正在执行，请等待完成');
  const section = curSection;
  const batch = accounts.filter(Boolean).slice(0, 3);
  if (!batch.length) { toast('请勾选未打开的账号'); return; }
  hubBusy = true;
  const task = { type: 'open' };
  window.matrixWorkspaceBrowserTask = task;
  let opened = 0;
  const failures = [];
  try {
    for (let index = 0; index < batch.length; index++) {
      const account = batch[index];
      hubProgress = `正在打开 ${index + 1}/${batch.length}：${HUB_PLATFORMS[account.platform]} · ${account.nickname}`;
      hubDraw();
      try {
        await hubCall('hub_open', account.platform, account.id);
        opened++;
        account.running = true;
        const current = hubItems.find(item => hubKey(item) === hubKey(account));
        if (current) current.running = true;
      } catch (error) {
        failures.push(`${account.nickname}：${error.message}`);
      }
      try { await renderAccountHub(); }
      catch (error) { failures.push(`状态刷新失败：${error.message}`); }
    }
  } finally {
    hubBusy = false;
    if (window.matrixWorkspaceBrowserTask === task) window.matrixWorkspaceBrowserTask = null;
    hubProgress = `本批已打开 ${opened}/${batch.length} 个浏览器。请在浏览器中检查登录状态，首次登录手动扫码。`;
    workspaceRefreshBrowserControls();
  }
  if (failures.length) workspaceResult('浏览器打开结果', `${hubProgress}\n\n${failures.join('\n')}`, section);
  else toast(`已打开 ${opened} 个浏览器`);
}

document.addEventListener('change', async event => {
  if (event.target.id === 'hub-import-file') await workspaceReadImportFile(event.target, 'hub-import-text', 'hub-file-status');
});

async function hubRenderView() {
  const request = hubRenderRequest + 1;
  try { await renderAccountHub(); }
  catch (error) {
    if (request !== hubRenderRequest) return;
    if (curSection === 'account_hub') {
      document.getElementById('page').innerHTML = `<div class="hint">页面加载失败：${esc(error.message)}</div><button class="btn" id="hub-retry">重新加载</button>`;
      document.getElementById('hub-retry').onclick = hubRenderView;
    }
    toast(error.message, true);
  }
}
RENDER.account_hub = hubRenderView;

// 兼容旧链接；侧边栏只展示一个账号入口。
RENDER.accounts = () => hubNavigateLegacy('accounts');
RENDER.douyin = () => hubNavigateLegacy('douyin');
