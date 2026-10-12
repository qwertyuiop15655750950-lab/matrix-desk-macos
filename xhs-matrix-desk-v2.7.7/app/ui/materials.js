/* 当前页面正文提取与素材归档：只读浏览器，不导航或自动发布。 */
const materialsNavIndex = NAV.findIndex(item => item.id === 'drafts');
NAV.splice(materialsNavIndex < 0 ? NAV.length : materialsNavIndex + 1, 0, { id: 'materials', name: '素材库', ico: '📚' });
TITLES.materials = '素材库';

let materialsQuery = '', materialsPlatform = '', materialsRequest = 0, materialCaptureProgress = '';
let materialDetailRequest = 0;
const materialDraftTasks = new Set();

async function materialsCall(method, ...args) {
  const result = await api(method, ...args);
  if (!result || !result.ok) throw new Error((result && result.error) || '操作失败');
  return result;
}
function materialsGuard(callback) {
  return async () => { try { await callback(); } catch (error) { toast(error.message || '操作失败', true); } };
}
function materialEngine(item) {
  return item.engine || item.extraction_engine || (item.metadata && item.metadata.engine) || '';
}
function materialToolStatus(status) {
  const tools = status.tools || {};
  return `<div class="cards">${[['bb-browser', '浏览器操作'], ['crawl4ai', '正文提取']].map(([name, label]) => {
    const tool = tools[name] || {};
    const state = tool.available === true ? '可用' : tool.available === false ? '不可用' : '状态未知';
    const reason = tool.available === false ? (tool.error || '工具尚未就绪，请完成安装后重试') : tool.error || '';
    return `<div class="card"><div style="font-weight:600">${label} · ${esc(name)}</div><div class="lbl">${state}${tool.version ? ' · ' + esc(tool.version) : ''}</div>${reason ? `<div class="hint">${esc(reason)}</div>` : ''}</div>`;
  }).join('')}</div>`;
}
function materialLinkText(link) {
  if (typeof link === 'string') return link;
  if (!link || typeof link !== 'object') return '';
  return [link.text || link.label || '', link.href || link.url || ''].filter(Boolean).join(' · ');
}
function materialExportItem(item) {
  const fields = ['id', 'platform', 'account_id', 'title', 'source_url', 'created_at', 'text_length', 'character_count', 'engine', 'engine_version', 'extraction_engine', 'metadata', 'selector', 'markdown', 'links'];
  return Object.fromEntries(fields.filter(field => item[field] !== undefined).map(field => [field, item[field]]));
}
function materialMarkdown(item) {
  return `# ${item.title || '未提供标题'}\n\n来源：${item.source_url || ''}\n平台：${HUB_PLATFORMS[item.platform] || item.platform || ''}\n归档时间：${item.created_at || ''}\n\n${item.markdown || ''}\n`;
}

async function renderMaterials() {
  const request = ++materialsRequest, section = curSection;
  if (section !== 'materials') return;
  const [listed, status] = await Promise.allSettled([
    materialsCall('material_list', materialsQuery, materialsPlatform), materialsCall('tool_status'),
  ]);
  if (request !== materialsRequest || curSection !== section) return;
  if (listed.status === 'rejected') throw listed.reason;
  const items = listed.value.items || [];
  document.getElementById('page').innerHTML = `
    ${status.status === 'fulfilled' ? materialToolStatus(status.value) : `<div class="hint">工具状态读取失败：${esc(status.reason.message)}</div>`}
    <div class="hint">在「多平台账号」打开浏览器，扫码后手动进入目标页面，再点击「提取当前页素材」。正文归档后可查看、导出或转为草稿。</div>
    <div class="toolbar" style="margin-top:12px">
      <input type="text" id="materials-query" value="${esc(materialsQuery)}" placeholder="搜索标题或正文">
      <select id="materials-platform"><option value="">全部平台</option>${Object.entries(HUB_PLATFORMS).map(([platform, label]) => `<option value="${platform}" ${materialsPlatform === platform ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select>
      <button class="btn primary" id="materials-search">查询</button><button class="btn" id="materials-refresh">刷新</button>
      <button class="btn" id="materials-accounts">去多平台账号</button><span class="hint">${items.length} 条素材</span>
    </div>
    <div class="hint" id="material-capture-progress" role="status">${esc(materialCaptureProgress)}</div>
    <div class="tbl-wrap"><table><thead><tr><th>标题</th><th>平台 / 账号</th><th>来源页面</th><th>正文长度</th><th>归档时间</th><th>提取方式</th><th>操作</th></tr></thead><tbody>
      ${items.map(item => `<tr><td><b>${esc(item.title || '未提供标题')}</b></td><td>${esc(HUB_PLATFORMS[item.platform] || item.platform)}<br><small>账号 ${esc(item.account_id)}</small></td>
        <td style="word-break:break-all;max-width:260px">${esc(item.source_url || '')}</td><td>${esc(item.text_length ?? item.character_count ?? '未提供')}</td><td>${esc(item.created_at || '')}</td><td>${esc(materialEngine(item) || '未提供')}</td>
        <td><button class="btn" data-material-detail="${esc(item.id)}">查看 / 导出</button><button class="btn" data-material-draft="${esc(item.id)}" ${materialDraftTasks.has(String(item.id)) ? 'disabled' : ''}>转为草稿</button></td></tr>`).join('') || '<tr><td colspan="7" style="padding:30px;text-align:center;color:var(--muted)">暂无素材，请先在账号浏览器中提取当前页正文</td></tr>'}
    </tbody></table></div>`;
  const filter = materialsGuard(async () => {
    materialsQuery = document.getElementById('materials-query').value.trim();
    materialsPlatform = document.getElementById('materials-platform').value;
    await renderMaterials();
  });
  document.getElementById('materials-search').onclick = filter;
  document.getElementById('materials-query').onkeydown = event => { if (event.key === 'Enter') filter(); };
  document.getElementById('materials-refresh').onclick = materialsGuard(renderMaterials);
  document.getElementById('materials-accounts').onclick = materialsGuard(() => showSection('account_hub'));
  document.querySelectorAll('[data-material-detail]').forEach(button => button.onclick = materialsGuard(() => materialDetail(button.dataset.materialDetail)));
  document.querySelectorAll('[data-material-draft]').forEach(button => button.onclick = materialsGuard(() => materialToDraft(button.dataset.materialDraft)));
}

async function materialsRenderView() {
  const request = materialsRequest + 1;
  try { await renderMaterials(); }
  catch (error) {
    if (request !== materialsRequest || curSection !== 'materials') return;
    document.getElementById('page').innerHTML = `<div class="hint">素材库加载失败：${esc(error.message)}</div><button class="btn" id="materials-retry">重新加载</button>`;
    document.getElementById('materials-retry').onclick = materialsRenderView;
    toast(error.message, true);
  }
}

async function materialCaptureModal(account) {
  if (!account || !account.id || !HUB_PLATFORMS[account.platform]) throw new Error('请先选择有效账号');
  if (workspaceBrowserBusy()) throw new Error('已有浏览器任务正在执行，请等待完成');
  const section = curSection;
  const data = await workspaceModal(`提取当前页素材 · ${account.nickname || HUB_PLATFORMS[account.platform]}`, `
    <div class="hint">先打开此账号的浏览器并扫码登录，手动进入需要归档的内容页面。这里只读取当前页面，不切换网址、不发布内容。视频号真实页面提取仍待验证。</div>
    <label>读取方式<select name="engine">${workspaceEngineOptions('bb-browser')}</select></label>
    <label>正文区域（可选 CSS）<input type="text" name="selector" placeholder="留空提取默认正文"></label>
    <div class="hint">选择正文区域可以排除菜单和侧栏。所选工具不可用时会提示错误，请选择已安装的读取方式重试。</div>`, '提取并归档');
  if (!data) return;
  if (workspaceBrowserBusy()) throw new Error('已有浏览器任务正在执行，请等待完成');
  const engine = workspaceEngine(data.engine, 'bb-browser'), task = { type: 'material' };
  window.matrixWorkspaceBrowserTask = task;
  materialCaptureProgress = `正在提取当前页正文：${HUB_PLATFORMS[account.platform]} · ${account.nickname || account.id}`;
  workspaceRefreshBrowserControls();
  toast(materialCaptureProgress);
  let result;
  try {
    result = await materialsCall('material_capture', account.platform, account.id, String(data.selector || '').trim(), engine);
    materialCaptureProgress = result.added ? `素材已保存：${result.title || '未提供标题'}` : '该内容已存在，未重复创建素材';
  } catch (error) {
    materialCaptureProgress = `素材提取未完成：${error.message}`;
    throw error;
  } finally {
    if (window.matrixWorkspaceBrowserTask === task) window.matrixWorkspaceBrowserTask = null;
    workspaceRefreshBrowserControls();
  }
  workspaceResult('素材归档结果', `${materialCaptureProgress}\n素材编号：${result.id}\n来源：${result.source_url || ''}${result.engine ? '\n读取方式：' + workspaceEngineName(result.engine) : ''}${result.extraction_engine ? '\n正文提取：' + result.extraction_engine : ''}\n可在「素材库」查看、导出或转为草稿。`, section);
  if (curSection === 'materials') {
    try { await renderMaterials(); }
    catch (error) { toast(`素材已归档，页面刷新失败：${error.message}`, true); }
  }
}

async function materialDetail(id) {
  if (workspaceModalOpen()) throw new Error('请先关闭当前弹窗');
  const section = curSection, request = ++materialDetailRequest;
  const result = await materialsCall('material_get', Number(id));
  if (request !== materialDetailRequest || curSection !== section) return;
  if (workspaceModalOpen()) throw new Error('请先关闭当前弹窗');
  const item = result.item;
  if (!item) throw new Error('素材不存在或已删除');
  const links = Array.isArray(item.links) ? item.links.map(materialLinkText).filter(Boolean) : [];
  const closed = workspaceModal(item.title || '素材详情', `
    <div class="hint">${esc(HUB_PLATFORMS[item.platform] || item.platform)} · 账号 ${esc(item.account_id)} · ${esc(item.created_at || '')}${materialEngine(item) ? ' · ' + esc(materialEngine(item)) : ''}</div>
    <div class="hint" style="word-break:break-all">来源：${esc(item.source_url || '')}</div>
    <div class="toolbar"><button class="btn" id="material-export-md">导出 Markdown</button><button class="btn" id="material-export-json">导出 JSON</button><button class="btn primary" id="material-detail-draft" ${materialDraftTasks.has(String(id)) ? 'disabled' : ''}>转为草稿</button></div>
    <pre id="material-body" style="white-space:pre-wrap;word-break:break-word;max-height:50vh;overflow:auto">${esc(item.markdown || '')}</pre>
    ${links.length ? `<div class="hint">页面链接（${links.length}）</div><pre style="white-space:pre-wrap;word-break:break-all">${esc(links.join('\n'))}</pre>` : ''}`, '关闭');
  const mask = document.querySelector('#modal-root .modal-mask');
  const md = document.getElementById('material-export-md'), json = document.getElementById('material-export-json'), draft = document.getElementById('material-detail-draft');
  const filename = Number.isInteger(Number(id)) ? `素材-${Number(id)}` : '素材导出';
  if (md) md.onclick = () => download(filename + '.md', materialMarkdown(item));
  if (json) json.onclick = () => download(filename + '.json', JSON.stringify(materialExportItem(item), null, 2));
  if (draft) draft.onclick = materialsGuard(() => materialToDraft(id, mask));
  await closed;
}

async function materialToDraft(id, mask = null) {
  const key = String(id), section = curSection;
  if (materialDraftTasks.has(key)) return;
  materialDraftTasks.add(key);
  const buttons = [...document.querySelectorAll('[data-material-draft]')].filter(button => button.dataset.materialDraft === key);
  if (mask && mask.isConnected) { const button = mask.querySelector('#material-detail-draft'); if (button) buttons.push(button); }
  buttons.forEach(button => button.disabled = true);
  try {
    const result = await materialsCall('material_to_draft', Number(id));
    toast(`已创建草稿 #${result.id}，请在内容库中检查和编辑`);
    if (mask && mask.isConnected) { const cancel = mask.querySelector('[data-x="cancel"]'); if (cancel) cancel.onclick(); }
    if (curSection === section) {
      try { await showSection('drafts'); }
      catch (error) { toast(`草稿已创建，内容库打开失败：${error.message}`, true); }
    }
  } finally {
    materialDraftTasks.delete(key);
    buttons.forEach(button => { if (button.isConnected) button.disabled = false; });
  }
}

RENDER.materials = materialsRenderView;
