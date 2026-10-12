/* 小红书矩阵工作台 - 前端逻辑（无外部依赖） */
const NAV = [
  { id: 'dashboard', name: '总览', ico: '🏠' },
  { id: 'drafts', name: '内容库', ico: '📝' },
  { id: 'publish', name: '发布', ico: '🚀' },
  { id: 'schedules', name: '排期', ico: '⏰' },
  { id: 'messages', name: '消息', ico: '✉️' },
  { id: 'leads', name: '线索', ico: '🔥' },
  { id: 'phrases', name: '话术库', ico: '💬' },
  { id: 'records', name: '发布记录', ico: '📊' },
  { id: 'grid', name: '浏览器群控', ico: '🖥️' },
  { id: 'cluster', name: '集群', ico: '🌐' },
  { id: 'collect', name: '关键词采集', ico: '🔍' },
  { id: 'logs', name: '系统日志', ico: '📋' },
  { id: 'settings', name: '设置', ico: '⚙️' },
];
const TITLES = { dashboard: '总览', accounts: '账号矩阵', drafts: '内容库', publish: '逐个批量发布',
  schedules: '发布排期', messages: '私信消息', leads: '线索中心', phrases: '导流话术库', records: '发布记录', grid: '浏览器群控', cluster: '集群', collect: '关键词采集', logs: '系统日志', settings: '设置' };
const STATUS_CN = { pending: '待发布', done: '已发布', cancelled: '已取消' };

let MOCK = false;
let state = { accounts: [], drafts: [], schedules: [], phrases: [],
  convs: [
    { user_id: 'a1b2c3d4e5f60718293a4b5c', nickname: '咨询客户A', preview: '请问这款还有货吗？', time_label: '10:20', unread: '2' },
    { user_id: 'b2c3d4e5f60718293a4b5c6d', nickname: '咨询客户B', preview: '地址发我一下', time_label: '昨天', unread: '' },
  ],
  thread: [
    { direction: 'incoming', text: '请问这款还有货吗？', has_img: false },
    { direction: 'outgoing', text: '有的，亲，详情可以看我主页简介～', has_img: false },
    { direction: 'incoming', text: '好的，谢谢', has_img: false },
  ],
  records: [], templates: [], selAccount: null, pubAccounts: new Set(),
  schAccounts: new Set(), phCat: '全部', logCursor: 0 };
let curSection = 'account_hub';

/* ---------- 基础设施 ---------- */
function api(method, ...args) {
  if (MOCK) return mockApi(method, ...args);
  return window.pywebview.api[method](...args);
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function toast(msg, isErr) {
  const t = document.createElement('div');
  t.className = 'toast' + (isErr ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toast-root').appendChild(t);
  setTimeout(() => t.remove(), 2600);
}
let pendingModal = null;
function cancelPendingModal() {
  const pending = pendingModal;
  pendingModal = null;
  if (pending) pending.resolve(null);
}
function openModal(title, bodyHTML, okLabel) {
  cancelPendingModal();
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-mask"><div class="modal"><h3>${esc(title)}</h3>
    <div class="modal-body">${bodyHTML}</div>
    <div class="foot"><button class="btn" data-x="cancel">取消</button>
    <button class="btn primary" data-x="ok">${esc(okLabel || '保存')}</button></div></div></div>`;
  return new Promise(resolve => {
    const pending = { resolve };
    pendingModal = pending;
    const finish = data => {
      if (pendingModal !== pending) return;
      pendingModal = null; root.innerHTML = ''; resolve(data);
    };
    root.querySelector('[data-x=cancel]').onclick = () => finish(null);
    root.querySelector('[data-x=ok]').onclick = () => {
      const data = {};
      root.querySelectorAll('[name]').forEach(n => { data[n.name] = n.value; });
      finish(data);
    };
  });
}
function showInfo(title, msg) {
  cancelPendingModal();
  const root = document.getElementById('modal-root');
  root.innerHTML = `<div class="modal-mask"><div class="modal"><h3>${esc(title)}</h3>
    <pre class="check">${esc(msg)}</pre>
    <div class="foot"><button class="btn primary" data-x="ok">知道了</button></div></div></div>`;
  root.querySelector('[data-x=ok]').onclick = () => { root.innerHTML = ''; };
}
function copyText(t) {
  const done = () => toast('已复制：' + t);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).then(done).catch(() => fallbackCopy(t, done));
  } else fallbackCopy(t, done);
}
function fallbackCopy(t, done) {
  const ta = document.createElement('textarea');
  ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); }
  catch (e) { toast('复制失败，请手动复制', true); }
  ta.remove();
}
function downloadB64(filename, b64, mime) {
  const a = document.createElement('a');
  a.href = `data:${mime};base64,${b64}`; a.download = filename; a.click();
}
function download(filename, text) {
  const b = new Blob(["\ufeff" + text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 3000);
}

/* ---------- 导航 ---------- */
function renderNav() {
  document.getElementById('nav').innerHTML = NAV.map(
    n => `<div class="nav-item${n.id === curSection ? ' active' : ''}" data-s="${n.id}">
      <span class="ico">${n.ico}</span>${n.name}${n.id === 'messages'
        ? '<span class="badge pending" id="nav-msg-badge" style="margin-left:auto;display:none">0</span>' : ''}</div>`).join('');
  document.querySelectorAll('.nav-item').forEach(el =>
    el.onclick = () => showSection(el.dataset.s));
  document.getElementById('page-title').textContent = TITLES[curSection];
}
async function showSection(id) {
  if (id === 'accounts' || id === 'douyin') return hubNavigateLegacy(id);
  curSection = id;
  renderNav();
  document.getElementById('top-actions').innerHTML = '';
  await RENDER[id]();
}

/* ---------- 总览 ---------- */
async function renderDashboard() {
  const [accR, monR, recR, schR] = await Promise.all([
    api('accounts_list'), api('monitor_status'),
    api('records_list'), api('schedules_list'),
  ]);
  const accounts = accR.ok ? accR.items : [];
  const monAccs = (monR.ok && monR.accounts) || {};
  const monNicks = (monR.ok && monR.nicknames) || {};
  const records = recR.ok ? recR.items : [];
  const schedules = schR.ok ? schR.items : [];
  const today = new Date().toISOString().slice(0, 10);
  const todayPubs = records.filter(r => (r.created_at || '').startsWith(today)).length;
  const unread = Object.values(monAccs).reduce((n, a) => n + (a.unread || 0), 0);
  const monCount = Object.keys(monAccs).length;
  const pendingSch = schedules.filter(x => x.status === 'pending').length;
  const abnormal = Object.entries(monAccs)
    .filter(([, a]) => a.state === 'error' || a.state === 'paused')
    .map(([id, a]) => ({ id, nick: monNicks[id] || ('账号' + id), ...a }));
  const leadR = await api('leads_list', 'new');
  const leadNew = leadR.ok ? leadR.items.length : 0;
  const noProxy = accounts.filter(a => !a.proxy_host);
  const nextSch = schedules.filter(x => x.status === 'pending')
    .sort((a, b) => (a.scheduled_at || '').localeCompare(b.scheduled_at || '')).slice(0, 5);
  const recentRec = records.slice(0, 8);
  const go = (sec, label) =>
    `<button class="btn" onclick="showSection('${sec}')">${label}</button>`;

  document.getElementById('page').innerHTML = `
    <div class="cards">
      <div class="card"><div class="num">${accounts.length}<span style="font-size:14px;color:var(--muted)">/20</span></div><div class="lbl">账号总数</div></div>
      <div class="card"><div class="num">${monCount}</div><div class="lbl">监控中</div></div>
      <div class="card"><div class="num" style="color:${unread ? 'var(--warn)' : 'var(--accent)'}">${unread}</div><div class="lbl">未读私信</div></div>
      <div class="card"><div class="num">${todayPubs}</div><div class="lbl">今日已发布</div></div>
      <div class="card"><div class="num">${pendingSch}</div><div class="lbl">待执行排期</div></div>
      <div class="card"><div class="num" style="color:${abnormal.length ? 'var(--danger)' : 'var(--accent)'}">${abnormal.length}</div><div class="lbl">异常账号</div></div>
      <div class="card" style="cursor:pointer" onclick="showSection('leads')"><div class="num" style="color:${leadNew ? 'var(--warn)' : 'var(--accent)'}">${leadNew}</div><div class="lbl">待跟进线索 🔥</div></div>
    </div>
    <div class="toolbar">${go('publish', '🚀 去发布')} ${go('messages', '✉️ 去消息')}
      ${go('schedules', '⏰ 去排期')} ${go('accounts', '👤 账号管理')}</div>
    ${abnormal.length ? `<div class="section-sub" style="color:var(--danger)">⚠️ 需要处理的账号（${abnormal.length}）</div>
    <div class="tbl-wrap"><table><thead><tr><th>账号</th><th>状态</th><th>原因</th><th>上次检查</th></tr></thead>
    <tbody>${abnormal.map(a => `<tr><td><b>${esc(a.nick)}</b></td>
      <td>${a.state === 'error' ? '<span class="badge cancelled">异常</span>' : '<span class="badge pending">已暂停</span>'}</td>
      <td style="color:var(--muted)">${esc(a.error || '-')}</td>
      <td class="mono" style="color:var(--muted)">${esc(a.last_check || '-')}</td></tr>`).join('')}</tbody></table></div>` : ''}
    ${noProxy.length ? `<div class="hint" style="color:var(--warn);margin-top:10px">⚠️ ${noProxy.length} 个账号未配置代理：${esc(noProxy.slice(0, 5).map(a => a.nickname).join('、'))}${noProxy.length > 5 ? '…' : ''}</div>` : ''}
    <div style="display:flex;gap:14px;margin-top:18px;flex-wrap:wrap">
      <div style="flex:1;min-width:320px">
        <div class="section-sub">⏰ 即将到来的排期</div>
        <div class="tbl-wrap"><table><thead><tr><th>草稿</th><th>账号数</th><th>时间</th></tr></thead>
        <tbody>${nextSch.map(x => `<tr><td>${esc(x.draft_title || ('#' + x.draft_id))}</td>
          <td>${x.account_ids.length}</td><td class="mono">${esc(x.scheduled_at)}</td></tr>`).join('')
          || '<tr><td colspan="3" style="text-align:center;color:var(--muted)">暂无</td></tr>'}</tbody></table></div>
      </div>
      <div style="flex:1;min-width:320px">
        <div class="section-sub">📊 最近发布记录</div>
        <div class="tbl-wrap"><table><thead><tr><th>账号</th><th>标题</th><th>时间</th></tr></thead>
        <tbody>${recentRec.map(x => `<tr><td><b>${esc(x.nickname || '')}</b></td>
          <td>${esc(x.title_used)}</td><td class="mono" style="color:var(--muted)">${esc(x.created_at)}</td></tr>`).join('')
          || '<tr><td colspan="3" style="text-align:center;color:var(--muted)">暂无</td></tr>'}</tbody></table></div>
      </div>
    </div>`;
}

/* ---------- 账号 ---------- */
async function loadAccounts() {
  const r = await api('accounts_list');
  if (r.ok) state.accounts = r.items;
}
function acctStats() {
  const n = state.accounts.length;
  const px = state.accounts.filter(a => a.proxy_host).length;
  const g = new Set(state.accounts.map(a => a.group_name || '默认分组')).size;
  return `<div class="cards">
    <div class="card"><div class="num">${n}</div><div class="lbl">账号总数</div></div>
    <div class="card"><div class="num">${px}</div><div class="lbl">已配代理</div></div>
    <div class="card"><div class="num">${g}</div><div class="lbl">分组数</div></div></div>`;
}
async function renderAccounts() {
  if (curSection === 'account_hub') return renderAccountHub();
  return hubNavigateLegacy('accounts');
}
async function renderXhsAccountTools(target) {
  const selected = state.selAccount;
  await loadAccounts();
  if (!target.isConnected || curSection !== 'account_hub' || hubView !== 'settings' || state.selAccount !== selected) return;
  const rows = state.accounts.filter(a => a.id === selected).map(a => `<tr data-id="${a.id}" class="${state.selAccount === a.id ? 'sel' : ''}">
    <td class="mono">${a.id}</td><td><b>${esc(a.nickname)}</b></td><td class="mono">${esc(a.xhs_id)}</td>
    <td>${esc(a.group_name)}</td>
    <td>${a.proxy_host ? `<span class="badge proxy-ok">${esc(a.proxy_host)}:${a.proxy_port}</span>` : '<span class="badge proxy-no">未配置</span>'}</td>
    <td class="mono" style="color:var(--muted)">${esc(a.notes)}</td></tr>`).join('');
  target.innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="b-add">＋ 新增</button>
      <button class="btn" id="b-edit">编辑</button>
      <button class="btn danger" id="b-del">删除</button>
      <button class="btn" id="b-launch">▶ 启动浏览器</button>
      <button class="btn" id="b-queue">🔢 排队登录</button>
      <button class="btn" id="b-tobg">💤 全部转后台</button>
      <button class="btn" id="b-ipcheck">🛡️ IP 隔离检测</button>
      <button class="btn" id="b-areacode">📞 点开区号</button>
      <button class="btn" id="b-selftest">🔍 发布页自检</button>
      <button class="btn" id="b-synccred">🔑 同步监控凭证</button>
      <button class="btn" id="b-proxy">🌐 批量分配代理</button>
      <button class="btn" id="b-imp">导入</button>
      <button class="btn" id="b-exp">导出</button>
    </div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>ID</th><th>昵称</th><th>小红书ID</th><th>分组</th><th>代理</th><th>备注</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="6" style="text-align:center;color:var(--muted)">暂无账号，点击「新增」添加</td></tr>'}</tbody></table></div>
    <div class="hint">当前账号的登录会话保存在独立目录。排队登录、批量代理和完整导入导出作用于小红书账号；发布页自检用于检查页面变化。</div>`;
  document.querySelectorAll('#page tbody tr[data-id]').forEach(tr =>
    tr.onclick = () => { state.selAccount = +tr.dataset.id; renderAccounts(); });
  document.getElementById('b-add').onclick = acctForm;
  document.getElementById('b-edit').onclick = () => {
    const a = state.accounts.find(x => x.id === state.selAccount);
    if (!a) return toast('请先选择一个账号', true);
    acctForm(a);
  };
  document.getElementById('b-del').onclick = async () => {
    if (!state.selAccount) return toast('请先选择一个账号', true);
    const a = state.accounts.find(x => x.id === state.selAccount);
    if (!confirm(`删除账号【${a.nickname}】？`)) return;
    await api('account_delete', state.selAccount);
    state.selAccount = null; renderAccounts(); toast('已删除');
  };
  document.getElementById('b-launch').onclick = async () => {
    if (!state.selAccount) return toast('请先选择一个账号', true);
    const r = await api('browser_launch', state.selAccount);
    toast(r.msg || '正在启动…');
  };
  document.getElementById('b-areacode').onclick = async () => {
    if (!state.selAccount) return toast('请先选择一个账号', true);
    const r = await api('browser_click_areacode', state.selAccount);
    toast(r.msg || r.error, !r.ok);
  };
  document.getElementById('b-tobg').onclick = async () => {
    await api('browsers_close');
    toast('所有浏览器窗口已关闭，会话保留在本地，后台任务照常用 headless 跑');
  };
  document.getElementById('b-queue').onclick = async () => {
    const ids = state.accounts.map(a => a.id);
    if (!ids.length) return toast('还没有账号', true);
    const d = await openModal('排队登录', `
      <div class="form-grid">
        <label>登录哪些</label><select name="scope">
          <option value="all">全部账号（${ids.length} 个）</option>
          <option value="nologin">未登录过的账号</option>
        </select>
      </div>
      <div class="hint">一次只开一个浏览器，扫完码自动转后台再开下一个，内存扛得住。每个扫完会问你要不要继续。</div>`,
      '开始排队');
    if (!d) return;
    let aids = ids;
    if (d.scope === 'nologin') {
      // 简单起见：全部，未登录判断在后端做（已登录的秒过）
      aids = ids;
    }
    const r = await api('browser_login_queue', aids);
    toast(r.msg || r.error, !r.ok);
  };
  document.getElementById('b-ipcheck').onclick = async () => {
    toast('正在检测各账号出口 IP…');
    const r = await api('accounts_check_ips');
    if (!r.ok) return toast(r.error, true);
    const rows = r.items.map(x => `<tr>
      <td><b>${esc(x.nickname)}</b></td>
      <td class="mono">${esc(x.proxy || '直连')}</td>
      <td class="mono">${esc(x.egress_ip || '检测失败')}</td>
      <td>${x.dup ? `<span class="badge" style="background:var(--danger)">⚠️ 与 ${esc(x.dup_with || '')} 共用 IP</span>`
                  : '<span class="badge done">独立</span>'}</td></tr>`).join('');
    openModal('🛡️ IP 隔离检测' + (r.dup_count ? `（${r.dup_count} 个 IP 被共用！）` : '（全部独立）'),
      `<div class="tbl-wrap"><table><thead><tr>
        <th>账号</th><th>配置代理</th><th>实际出口 IP</th><th>隔离状态</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="4" style="text-align:center">没有运行中的浏览器</td></tr>'}</tbody></table></div>
      <div class="hint">只检测正在运行的浏览器。共用 IP 的账号建议换代理，避免关联。</div>`,
      '关闭');
  };
  document.getElementById('b-synccred').onclick = async () => {
    if (!state.selAccount) return toast('请先选择一个账号', true);
    const r = await api('account_sync_cred', state.selAccount);
    if (r.ok) toast('监控凭证已同步，该账号可加入轮转监控');
    else toast(r.error, true);
  };
  document.getElementById('b-selftest').onclick = async () => {
    if (!state.selAccount) return toast('请先选择一个账号', true);
    const r = await api('browser_selftest', state.selAccount);
    toast(r.msg || '检测中…');
  };
  document.getElementById('b-proxy').onclick = async () => {
    const groups = [...new Set(state.accounts.map(a => a.group_name || '默认分组'))];
    const d = await openModal('批量分配代理', `
      <div class="form-grid">
        <label>目标账号</label><select name="target">
          <option value="noproxy">未配置代理的账号</option>
          <option value="all">全部账号</option>
          ${groups.map(g => `<option value="g:${esc(g)}">分组：${esc(g)}</option>`).join('')}
        </select>
        <label>代理协议</label><select name="scheme">
          <option value="http">http</option><option value="socks5">socks5</option></select>
        <label>机场订阅</label>
        <div style="display:flex;gap:8px">
          <input name="suburl" placeholder="粘贴机场订阅链接" style="flex:1">
          <button class="btn" id="px-import" type="button">导入</button>
        </div>
      </div>
      <textarea name="text" style="min-height:150px" placeholder="一行一个代理：IP:端口 或 IP:端口:用户:密码"></textarea>
      <div class="hint">按行顺序一号一IP自动分配；代理行数少于账号数时，多出的账号保持不变。订阅只解析 http/socks5 节点，ss/vmess/trojan 需本地客户端转。</div>`, '开始分配');
    const impBtn = document.getElementById('px-import');
    if (impBtn) impBtn.onclick = async (e) => {
      e.preventDefault();
      const url = document.querySelector('[name=suburl]').value.trim();
      if (!url) return toast('先粘贴订阅链接', true);
      toast('正在拉取订阅…');
      const r = await api('proxies_import_sub', url);
      if (!r.ok) return toast(r.error, true);
      document.querySelector('[name=text]').value = r.text;
      toast(r.msg);
    };
    if (!d) return;
    let aids;
    if (d.target === 'noproxy') aids = state.accounts.filter(a => !a.proxy_host).map(a => a.id);
    else if (d.target === 'all') aids = state.accounts.map(a => a.id);
    else aids = state.accounts.filter(a => (a.group_name || '默认分组') === d.target.slice(2)).map(a => a.id);
    const r = await api('proxies_assign', aids, d.text, d.scheme);
    if (r.ok) { toast(r.msg); renderAccounts(); } else toast(r.error, true);
  };
  document.getElementById('b-imp').onclick = async () => {
    const d = await openModal('批量导入账号', `
      <div class="form-grid"><label>格式</label>
        <select name="fmt"><option value="json">JSON</option><option value="csv">CSV</option></select></div>
      <textarea name="text" style="min-height:160px" placeholder='粘贴 JSON 数组或 CSV 文本'></textarea>`);
    if (!d) return;
    const r = await api('accounts_import_text', d.text, d.fmt);
    if (r.ok) { toast(`导入完成：新增 ${r.added}，跳过 ${r.skipped}`); renderAccounts(); }
    else toast(r.error, true);
  };
  document.getElementById('b-exp').onclick = async () => {
    const r = await api('accounts_export');
    if (r.ok) { download('accounts.json', r.data); toast('已导出'); }
  };
}
async function acctForm(a) {
  a = a || {};
  const d = await openModal(a.id ? '编辑账号' : '新增账号', `
    <div class="form-grid">
      <label>昵称*</label><input type="text" name="nickname" value="${esc(a.nickname || '')}">
      <label>小红书ID</label><input type="text" name="xhs_id" value="${esc(a.xhs_id || '')}">
      <label>分组</label><input type="text" name="group_name" value="${esc(a.group_name || '默认分组')}">
      <label>代理协议</label><select name="proxy_scheme">
        <option value="http"${(a.proxy_scheme || 'http') === 'http' ? ' selected' : ''}>http</option>
        <option value="socks5"${a.proxy_scheme === 'socks5' ? ' selected' : ''}>socks5</option></select>
      <label>代理IP</label><input type="text" name="proxy_host" value="${esc(a.proxy_host || '')}" placeholder="美国住宅IP">
      <label>代理端口</label><input type="text" name="proxy_port" value="${a.proxy_port || ''}">
      <label>代理用户</label><input type="text" name="proxy_user" value="${esc(a.proxy_user || '')}">
      <label>代理密码</label><input type="text" name="proxy_pass" value="${esc(a.proxy_pass || '')}">
      <label>备注</label><input type="text" name="notes" value="${esc(a.notes || '')}">
    </div>
    <div class="hint">美国住宅 IP：一号一个、长期固定，不要用轮换 IP。</div>`);
  if (!d) return;
  const r = a.id ? await api('account_update', a.id, d) : await api('account_create', d);
  if (r.ok) { toast('已保存'); renderAccounts(); } else toast(r.error, true);
}

/* ---------- 内容库 ---------- */
async function loadDrafts() {
  const r = await api('drafts_list');
  if (r.ok) state.drafts = r.items;
}
async function loadTemplates() {
  const r = await api('poster_templates');
  if (r.ok) state.templates = r.items;
}
async function renderDrafts() {
  await loadDrafts(); await loadTemplates();
  const rows = state.drafts.map(d => `<tr data-id="${d.id}">
    <td class="mono">${d.id}</td><td><b>${esc(d.title)}</b></td>
    <td>${d.mode === 'poster' ? '<span class="badge pending">大字报</span>' : '<span class="badge done">图文</span>'}</td>
    <td style="color:var(--muted)">${esc(d.topics)}</td>
    <td class="mono" style="color:var(--muted)">${esc(d.updated_at)}</td></tr>`).join('');
  document.getElementById('page').innerHTML = `
    <div class="toolbar">
      <button class="btn primary" id="b-add">＋ 新增草稿</button>
      <button class="btn" id="b-edit">编辑</button>
      <button class="btn danger" id="b-del">删除</button>
    </div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>ID</th><th>标题</th><th>模式</th><th>话题</th><th>更新时间</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="5" style="text-align:center;color:var(--muted)">暂无草稿</td></tr>'}</tbody></table></div>
    <div class="hint">大字报模式：发布时使用小红书自带文字配图，并随机选择已加载的卡片。</div>`;
  let sel = null;
  document.querySelectorAll('#page tbody tr[data-id]').forEach(tr =>
    tr.onclick = () => { sel = +tr.dataset.id;
      document.querySelectorAll('#page tbody tr').forEach(x => x.classList.remove('sel'));
      tr.classList.add('sel'); });
  document.getElementById('b-add').onclick = () => draftForm();
  document.getElementById('b-edit').onclick = () => {
    const d = state.drafts.find(x => x.id === sel);
    if (!d) return toast('请先选择一条草稿', true);
    draftForm(d);
  };
  document.getElementById('b-del').onclick = async () => {
    if (!sel) return toast('请先选择一条草稿', true);
    if (!confirm('删除这条草稿？')) return;
    await api('draft_delete', sel); renderDrafts(); toast('已删除');
  };
}
async function draftForm(d) {
  d = d || { mode: 'images', template: 'rice' };
  let media = [];
  try { media = JSON.parse(d.media_json || '[]'); } catch (e) { media = []; }
  const tplOpts = state.templates.map(t =>
    `<option value="${t.id}"${d.template === t.id ? ' selected' : ''}>${esc(t.name)}</option>`).join('');
  const data = await openModal(d.id ? '编辑草稿' : '新增草稿', `
    <div class="form-grid">
      <label>标题*</label><input type="text" name="title" value="${esc(d.title || '')}">
      <label>话题</label><input type="text" name="topics" value="${esc(d.topics || '')}" placeholder="#探店 #美食">
      <label>模式</label><select name="mode">
        <option value="images"${d.mode !== 'poster' ? ' selected' : ''}>图文（自备图片/视频）</option>
        <option value="poster"${d.mode === 'poster' ? ' selected' : ''}>大字报（发布时自动生成文字图）</option></select>
      <label>模板</label><select name="template"><option value="xhs_builtin">小红书自带文字配图 · 随机卡片</option></select>
    </div>
    <label style="color:var(--muted);font-size:13px">封面大字（与正文分开）</label><textarea name="cover_text">${esc(d.cover_text||'')}</textarea>
    <label style="color:var(--muted);font-size:13px">正文</label>
    <textarea name="body" style="min-height:130px">${esc(d.body || '')}</textarea>
    <div style="margin-top:8px">
      <label style="color:var(--muted);font-size:13px">附件（图片/视频，每行一个路径；mp4 自动走视频发布）</label>
      <textarea name="media_text" style="min-height:56px">${esc(media.join('\n'))}</textarea>
      <button class="btn" type="button" id="f-pick" style="margin-top:6px">浏览选择文件</button>
    </div>
    <div class="hint">大字报模式：发布时使用小红书自带文字配图，并随机选择已加载的卡片。</div>`);
  if (!data) return;
  data.media = String(data.media_text || '').split('\n').map(x => x.trim()).filter(Boolean);
  delete data.media_text;
  const r = await api('draft_save', Object.assign({ id: d.id }, data));
  if (r.ok) { toast('已保存'); renderDrafts(); } else toast(r.error, true);
}

// 附件"浏览选择"按钮（弹窗内，事件委托）
document.getElementById('modal-root').addEventListener('click', async e => {
  if (e.target && e.target.id === 'f-pick') {
    const r = await api('pick_files');
    if (r.ok && r.paths && r.paths.length) {
      const ta = document.querySelector('#modal-root [name=media_text]');
      if (ta) ta.value = (ta.value.trim() ? ta.value.trim() + '\n' : '') + r.paths.join('\n');
    } else if (!r.ok) toast(r.error || '选择失败', true);
  }
});

/* ---------- 发布（队列模式：断点续发） ---------- */
let logTimer = null, queueTimer = null;
async function renderPublish() {
  await loadDrafts(); await loadAccounts();
  state.pubAccounts = new Set();
  const dOpts = state.drafts.map(d =>
    `<option value="${d.id}">[${d.id}] ${esc(d.title)}${d.mode === 'poster' ? '（大字报·自动排版）' : ''}</option>`).join('');
  const rows = state.accounts.map(a => `<tr data-id="${a.id}">
    <td><input type="checkbox" data-aid="${a.id}"></td><td><b>${esc(a.nickname)}</b></td>
    <td>${esc(a.group_name)}</td>
    <td>${a.proxy_host ? `<span class="badge proxy-ok">${esc(a.proxy_host)}</span>` : '<span class="badge proxy-no">未配置</span>'}</td></tr>`).join('');
  document.getElementById('page').innerHTML = `
    <div class="section-sub">发布队列</div>
    <div class="card" id="p-qcard"><div style="color:var(--muted)">选择文案和账号后，生成发布清单</div></div>
    <div class="section-sub">新建队列</div>
    <div class="section-sub" style="font-size:13px;color:var(--muted)">1. 选择草稿</div>
    <select id="p-draft" style="max-width:560px">${dOpts || '<option value="">暂无草稿，请先去内容库创建</option>'}</select>
    <div class="hint" id="p-hint"></div>
    <div class="section-sub" style="font-size:13px;color:var(--muted)">2. 选择账号（可多选）</div>
    <div class="tbl-wrap" style="max-height:260px;overflow-y:auto"><table><thead><tr>
      <th style="width:44px"></th><th>昵称</th><th>分组</th><th>代理</th>
    </tr></thead><tbody>${rows}</tbody></table></div>
    <div class="toolbar"><button class="btn primary" id="p-create">＋ 创建队列</button></div>
    <div class="section-sub">发布日志</div>
    <div class="log-box" id="p-log"><div style="color:var(--muted)">等待开始…</div></div>
    <div class="hint">省内存发布：一次只处理一个账号，复用已保存的登录目录，处理完关闭该账号浏览器，再处理下一个。登录失效会跳过并记录，重新扫码后可新建队列重试。发布使用所选草稿，中途暂停或重启可继续剩余账号。</div>`;
  const updHint = () => {
    const d = state.drafts.find(x => x.id === +document.getElementById('p-draft').value);
    document.getElementById('p-hint').textContent = d && d.mode === 'poster'
      ? '大字报模式：发布时使用小红书自带文字配图，并随机选择已加载的卡片。'
      : '图文模式：将使用草稿附件中的图片/视频（mp4 自动走视频发布页）。';
  };
  document.getElementById('p-draft').onchange = updHint; updHint();
  document.querySelectorAll('#page input[data-aid]').forEach(cb =>
    cb.onchange = () => { const id = +cb.dataset.aid;
      cb.checked ? state.pubAccounts.add(id) : state.pubAccounts.delete(id); });
  document.getElementById('p-create').onclick = async () => {
    const did = +document.getElementById('p-draft').value;
    if (!did) return toast('请先选择草稿', true);
    if (!state.pubAccounts.size) return toast('请至少选择一个账号', true);
    const r = await api('queue_create', did, [...state.pubAccounts]);
    if (r.ok) { toast('队列已创建'); loadQueueCard(); }
    else toast(r.error, true);
  };
  clearInterval(logTimer); clearInterval(queueTimer);
  logTimer = setInterval(async () => {
    if (curSection !== 'publish') return;
    const r = await api('publish_log', state.logCursor);
    if (r.ok && r.items.length) {
      state.logCursor = r.cursor;
      const box = document.getElementById('p-log');
      if (box) {
        r.items.forEach(it => {
          const div = document.createElement('div');
          div.innerHTML = `<span style="color:var(--muted)">${esc(it.t)}</span> ${esc(it.msg)}`;
          box.appendChild(div);
        });
        box.scrollTop = box.scrollHeight;
      }
    }
  }, 1500);
  queueTimer = setInterval(() => { if (curSection === 'publish') loadQueueCard(); }, 3000);
  loadQueueCard();
}

async function loadQueueCard() {
  const box = document.getElementById('p-qcard');
  if (!box) return;
  const r = await api('queue_get');
  const q = r.ok && r.queue;
  if (!q) {
    box.innerHTML = '<div style="color:var(--muted)">选择文案和账号后，生成发布清单</div>';
    return;
  }
  const pct = q.total ? Math.round(q.done / q.total * 100) : 0;
  const names = q.pending.slice(0, 6).map(id => esc(q.pending_names[String(id)] || id)).join('、');
  const more = q.pending.length > 6 ? ` 等 ${q.pending.length - 6} 个` : '';
  const stBadge = q.status === 'paused' ? '<span class="badge pending">已暂停</span>'
    : q.status === 'finished' ? '<span class="badge done">已处理完成</span>' : '<span class="badge done">进行中</span>';
  box.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center">
      <b>${esc(q.draft_title)}</b>${stBadge}</div>
    <div style="background:var(--bg2);border-radius:8px;height:10px;margin:10px 0;overflow:hidden">
      <div style="background:var(--accent);height:100%;width:${pct}%;transition:width .4s"></div></div>
    <div class="hint">已处理 ${q.done}/${q.total}（${pct}%）· 下一个：${names}${more}</div>
    <div class="toolbar" style="margin-bottom:0">
      <button class="btn primary" id="p-next" ${q.processing || !q.pending.length ? 'disabled' : ''}>处理下一个</button>
      <button class="btn" id="p-auto" ${q.processing || !q.pending.length ? 'disabled' : ''}>开始逐个发布</button>
      <button class="btn" id="p-pause">暂停</button>
      <button class="btn danger" id="p-cancel">取消队列</button>
    </div>
    <div class="queue-jobs"><table><thead><tr><th>文案</th><th>账号</th><th>状态</th></tr></thead><tbody>${(q.jobs||[]).map(job=>`<tr><td>${esc(job.title)}</td><td>${esc(job.nickname)}</td><td>${esc(({pending:'待发布',done:'已发布',skipped:'已跳过',review_required:'提交结果待核对'})[job.result]||job.result)}</td></tr>`).join('')}</tbody></table></div>`;
  document.getElementById('p-next').onclick = async () => {
    const r2 = await api('queue_process_next', false);
    if (!r2.ok) toast(r2.error, true);
  };
  document.getElementById('p-auto').onclick = async () => {
    if (!confirm('逐个发布所选草稿到剩余账号：完成一个后关闭该账号浏览器，再处理下一个。开始吗？')) return;
    const r2 = await api('queue_process_next', true);
    if (!r2.ok) toast(r2.error, true); else toast('连续处理中，可随时暂停');
  };
  document.getElementById('p-pause').onclick = async () => {
    await api('queue_pause'); toast('将在当前账号处理结束后暂停'); loadQueueCard(); };
  document.getElementById('p-cancel').onclick = async () => {
    if (!confirm('取消当前队列？已处理的不受影响。')) return;
    await api('queue_cancel'); toast('已取消'); loadQueueCard(); };
}

/* ---------- 排期 ---------- */
async function renderSchedules() {
  await loadDrafts(); await loadAccounts();
  state.schAccounts = new Set();
  const dOpts = state.drafts.map(d => `<option value="${d.id}">[${d.id}] ${esc(d.title)}</option>`).join('');
  const aRows = state.accounts.map(a => `<tr><td><input type="checkbox" data-aid="${a.id}"></td>
    <td><b>${esc(a.nickname)}</b></td><td>${esc(a.group_name)}</td></tr>`).join('');
  document.getElementById('page').innerHTML = `
    <div class="section-sub">新增排期</div>
    <div class="card"><div class="form-grid" style="grid-template-columns:70px 1fr 70px 1fr">
      <label>草稿</label><select id="s-draft">${dOpts}</select>
      <label>时间</label><input type="datetime-local" id="s-time">
      <label>备注</label><input type="text" id="s-note" placeholder="如：早高峰">
      <label></label><span></span>
    </div>
    <div class="hint">选择账号：</div>
    <div class="tbl-wrap" style="max-height:180px;overflow-y:auto"><table><tbody>${aRows}</tbody></table></div>
    <div class="toolbar"><button class="btn primary" id="s-add">＋ 新增排期</button></div></div>
    <div class="section-sub">排期列表</div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>ID</th><th>草稿</th><th>账号数</th><th>时间</th><th>状态</th><th>备注</th><th>操作</th>
    </tr></thead><tbody id="s-body"></tbody></table></div>
    <div class="hint">到点后弹窗提醒：开始发布 / 10 分钟后提醒 / 取消排期。</div>`;
  document.querySelectorAll('#page input[data-aid]').forEach(cb =>
    cb.onchange = () => { const id = +cb.dataset.aid;
      cb.checked ? state.schAccounts.add(id) : state.schAccounts.delete(id); });
  document.getElementById('s-add').onclick = async () => {
    const t = document.getElementById('s-time').value;
    if (!t) return toast('请选择时间', true);
    const r = await api('schedule_create', {
      draft_id: +document.getElementById('s-draft').value,
      account_ids: [...state.schAccounts],
      scheduled_at: t.replace('T', ' '),
      note: document.getElementById('s-note').value });
    if (r.ok) { toast('排期已创建，到点会弹窗提醒'); loadScheduleRows(); }
    else toast(r.error, true);
  };
  loadScheduleRows();
}
async function loadScheduleRows() {
  const r = await api('schedules_list');
  const body = document.getElementById('s-body');
  if (!r.ok || !body) return;
  body.innerHTML = r.items.map(s => `<tr>
    <td class="mono">${s.id}</td><td>${esc(s.draft_title || ('#' + s.draft_id))}</td>
    <td>${s.account_ids.length}</td><td class="mono">${esc(s.scheduled_at)}</td>
    <td><span class="badge ${s.status}">${STATUS_CN[s.status] || s.status}</span></td>
    <td style="color:var(--muted)">${esc(s.note)}</td>
    <td>${s.status === 'pending' ? `<button class="btn" data-queue="${s.id}" data-did="${s.draft_id}" data-aids='${JSON.stringify(s.account_ids)}'>建队列</button>
        <button class="btn" data-cancel="${s.id}">取消</button>` : ''}
        <button class="btn danger" data-del="${s.id}">删除</button></td></tr>`).join('')
    || '<tr><td colspan="7" style="text-align:center;color:var(--muted)">暂无排期</td></tr>';
  body.querySelectorAll('[data-cancel]').forEach(b => b.onclick = async () => {
    await api('schedule_cancel', +b.dataset.cancel); loadScheduleRows(); });
  body.querySelectorAll('[data-queue]').forEach(b => b.onclick = async () => {
    const r = await api('queue_create', +b.dataset.did, JSON.parse(b.dataset.aids));
    if (r.ok) {
      await api('schedule_cancel', +b.dataset.queue);
      toast('已生成发布队列，去发布页处理'); showSection('publish');
    } else toast(r.error, true); });
  body.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('删除这条排期？')) return;
    await api('schedule_delete', +b.dataset.del); loadScheduleRows(); });
}

/* ---------- 线索中心（只读识别） ---------- */
const LEAD_FILTERS = [['', '全部'], ['new', '待跟进'], ['contacted', '已联系'],
  ['done', '已成交'], ['ignored', '已忽略']];
let leadFilter = '', leadTab = 'pool';
async function renderLeads() {
  document.getElementById('page').innerHTML = `
    <div class="toolbar" style="margin-top:0">
      <button class="btn${leadTab === 'pool' ? ' primary' : ''}" id="lt-pool">🔥 线索池</button>
      <button class="btn${leadTab === 'contacts' ? ' primary' : ''}" id="lt-contacts">📇 联系方式</button>
    </div>
    <div id="lead-body"></div>`;
  document.getElementById('lt-pool').onclick = () => { leadTab = 'pool'; renderLeads(); };
  document.getElementById('lt-contacts').onclick = () => { leadTab = 'contacts'; renderLeads(); };
  if (leadTab === 'pool') renderLeadsPool(); else renderContacts();
}
async function renderLeadsPool() {
  const r = await api('leads_list', leadFilter);
  const items = r.ok ? r.items : [];
  const counts = r.ok ? r.counts : {};
  const kw = await api('lead_keywords_list');
  const words = kw.ok ? kw.items : [];
  document.getElementById('lead-body').innerHTML = `
    <div class="toolbar" style="margin-top:0">
      ${LEAD_FILTERS.map(([v, n]) => `<button class="btn${leadFilter === v ? ' primary' : ''}" data-lf="${v}">${n}${counts[v] !== undefined && v ? `(${counts[v]})` : ''}</button>`).join('')}
    </div>
    <div class="hint">只读识别：轮转监控时自动扫描私信预览，命中意向词即记为线索；不做任何自动发送，联系一律你人工来。</div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>账号</th><th>客户</th><th>最新消息</th><th>命中词</th><th>时间</th><th>状态</th><th>操作</th>
    </tr></thead><tbody>
    ${items.map(l => `<tr>
      <td><b>${esc(l.account_name || ('账号' + l.account_id))}</b></td>
      <td>${esc(l.nickname || '(未知)')}</td>
      <td style="max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(l.preview)}">${esc(l.preview)}</td>
      <td>${(l.matched_words || []).map(w => `<span class="badge pending">${esc(w)}</span>`).join(' ')}</td>
      <td class="mono" style="color:var(--muted)">${esc(l.updated_at)}</td>
      <td><span class="badge ${l.status === 'new' ? 'pending' : l.status === 'done' ? 'done' : ''}">${esc(l.status_cn)}</span></td>
      <td style="white-space:nowrap">
        <button class="btn" data-jump="${l.id}" data-aid="${l.account_id}" data-uid="${esc(l.user_id)}">查看会话</button>
        ${l.status === 'new' ? `<button class="btn" data-st="${l.id}|contacted">已联系</button>` : ''}
        ${l.status !== 'done' ? `<button class="btn" data-st="${l.id}|done">成交</button>` : ''}
        ${l.status !== 'ignored' ? `<button class="btn danger" data-st="${l.id}|ignored">忽略</button>` : ''}
      </td></tr>`).join('')
      || '<tr><td colspan="7" style="text-align:center;color:var(--muted)">暂无线索：把账号加入监控后自动识别</td></tr>'}
    </tbody></table></div>
    <div class="section-sub">意向关键词（一行一个）</div>
    <div class="card"><textarea id="l-kw" style="min-height:90px">${esc(words.join('\n'))}</textarea>
    <div class="toolbar" style="margin-bottom:0"><button class="btn primary" id="l-kw-save">保存关键词</button></div>
    <div class="hint">命中即标为线索；改完即时生效，下一轮监控开始使用。</div></div>`;
  document.querySelectorAll('[data-lf]').forEach(b => b.onclick = () => {
    leadFilter = b.dataset.lf; renderLeads(); });
  document.querySelectorAll('[data-st]').forEach(b => b.onclick = async () => {
    const [id, st] = b.dataset.st.split('|');
    await api('lead_set_status', +id, st); renderLeads(); });
  document.querySelectorAll('[data-jump]').forEach(b => b.onclick = () =>
    jumpToConv(+b.dataset.aid, b.dataset.uid));
  document.getElementById('l-kw-save').onclick = async () => {
    const ws = document.getElementById('l-kw').value.split('\n');
    const r2 = await api('lead_keywords_save', ws);
    if (r2.ok) toast(`已保存 ${r2.count} 个关键词`); else toast(r2.error, true);
  };
}

const CONTACT_FILTERS = [['', '全部'], ['new', '待添加'], ['added', '已添加'], ['ignored', '已忽略']];
let contactFilter = '', scanTimer = null;
async function renderContacts() {
  await loadAccounts();
  const r = await api('contacts_list', contactFilter);
  const items = r.ok ? r.items : [];
  const counts = r.ok ? r.counts : {};
  const acctOpts = `<option value="all">监控中的全部账号</option>` +
    state.accounts.map(a => `<option value="${a.id}">${esc(a.nickname)}</option>`).join('');
  const cell = (v) => v ? `<span class="mono">${esc(v)}</span>
    <button class="btn" style="padding:2px 8px;font-size:12px" onclick="copyText('${esc(v).replace(/'/g, "\\'")}')">复制</button>` : '<span style="color:var(--muted)">-</span>';
  document.getElementById('lead-body').innerHTML = `
    <div class="toolbar" style="margin-top:0">
      <select id="c-acct" style="max-width:220px">${acctOpts}</select>
      <button class="btn primary" id="c-scan">🔍 扫描联系方式</button>
      <button class="btn" id="c-scan-comments">💬 扫描评论区</button>
      <button class="btn" id="c-export">⬇ 导出 Excel</button>
      ${CONTACT_FILTERS.map(([v, n]) => `<button class="btn${contactFilter === v ? ' primary' : ''}" data-cf="${v}">${n}${counts[v] !== undefined && v ? `(${counts[v]})` : ''}</button>`).join('')}
    </div>
    <div class="hint">逐个打开选中账号的私信会话、全文扫描微信号/手机号/QQ；只读不发送。扫描较慢，可挂着跑。「扫描评论区」则扫该账号主页笔记的评论，联系方式与意向线索一起收。</div>
    <div class="card" id="c-progress" style="display:none"></div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>账号</th><th>客户</th><th>微信号</th><th>手机号</th><th>QQ</th><th>来源消息</th><th>状态</th><th>操作</th>
    </tr></thead><tbody>
    ${items.map(c => `<tr>
      <td><b>${esc(c.account_name || ('账号' + c.account_id))}</b></td>
      <td>${esc(c.nickname || '(未知)')}</td>
      <td>${cell(c.wechat)}</td><td>${cell(c.phone)}</td><td>${cell(c.qq)}</td>
      <td style="max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(c.snippet)}">${esc(c.snippet)}</td>
      <td><span class="badge ${c.status === 'new' ? 'pending' : c.status === 'added' ? 'done' : ''}">${esc(c.status_cn)}</span></td>
      <td style="white-space:nowrap">
        ${c.status === 'new' ? `<button class="btn" data-cst="${c.id}|added">已添加</button>` : ''}
        ${c.status !== 'ignored' ? `<button class="btn danger" data-cst="${c.id}|ignored">忽略</button>` : ''}
      </td></tr>`).join('')
      || '<tr><td colspan="8" style="text-align:center;color:var(--muted)">暂无数据：选择账号后点「扫描联系方式」</td></tr>'}
    </tbody></table></div>`;
  document.querySelectorAll('[data-cf]').forEach(b => b.onclick = () => {
    contactFilter = b.dataset.cf; renderContacts(); });
  document.querySelectorAll('[data-cst]').forEach(b => b.onclick = async () => {
    const [id, st] = b.dataset.cst.split('|');
    await api('contact_set_status', +id, st); renderContacts(); });
  document.getElementById('c-scan').onclick = async () => {
    const v = document.getElementById('c-acct').value;
    let aids;
    if (v === 'all') {
      const st = await api('monitor_status');
      aids = st.ok ? Object.keys(st.accounts).map(Number) : [];
      if (!aids.length) return toast('没有监控中的账号', true);
    } else aids = [+v];
    const r2 = await api('contacts_scan_start', aids);
    if (!r2.ok) return toast(r2.error, true);
    toast('开始扫描，可切换去别的页面，完成后回来导出');
    pollScan();
  };
  document.getElementById('c-scan-comments').onclick = async () => {
    const v = document.getElementById('c-acct').value;
    let aids;
    if (v === 'all') {
      const st = await api('monitor_status');
      aids = st.ok ? Object.keys(st.accounts).map(Number) : [];
      if (!aids.length) return toast('没有监控中的账号', true);
    } else aids = [+v];
    const r2 = await api('comments_scan_start', aids);
    if (!r2.ok) return toast(r2.error, true);
    toast('开始扫描评论区，可切换去别的页面，完成后回来查看');
    pollScan();
  };
  document.getElementById('c-export').onclick = async () => {
    const r2 = await api('contacts_export');
    if (r2.ok) {
      downloadB64(r2.filename, r2.b64,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
      toast('已导出 ' + r2.filename);
    } else toast(r2.error, true);
  };
  pollScan();
}
async function pollScan() {
  clearInterval(scanTimer);
  const box = () => document.getElementById('c-progress');
  scanTimer = setInterval(async () => {
    const b = box();
    if (!b || curSection !== 'leads') { clearInterval(scanTimer); return; }
    const r = await api('contacts_scan_status');
    if (!r.ok) return;
    if (!r.running && !b.dataset.done) {
      b.dataset.done = '1'; b.style.display = 'none';
      clearInterval(scanTimer);
      if (r.accounts_total) { toast(`扫描完成：${r.found} 条联系方式`); renderContacts(); }
      return;
    }
    if (r.running) {
      b.style.display = ''; delete b.dataset.done;
      b.innerHTML = `<div style="display:flex;justify-content:space-between">
        <b>正在扫描：${esc(r.current || '')}</b>
        <span class="mono">${r.accounts_done}/${r.accounts_total} 个账号 · ${r.convs_done}/${r.convs_total} 个会话 · 已找到 ${r.found} 条</span></div>
        <div style="background:var(--bg2);border-radius:8px;height:10px;margin-top:8px;overflow:hidden">
        <div style="background:var(--accent);height:100%;width:${r.accounts_total ? Math.round(r.accounts_done / r.accounts_total * 100) : 0}%"></div></div>`;
    }
  }, 2000);
}

async function jumpToConv(aid, userId) {
  showSection('messages');
  for (let i = 0; i < 20; i++) {
    await new Promise(r => setTimeout(r, 500));
    if (curSection !== 'messages') return;
    const sel = document.getElementById('m-acct');
    if (!sel) continue;
    sel.value = String(aid);
    msgState.aid = aid;
    await viewAccountConvs();
    for (let j = 0; j < 10; j++) {
      const c = msgState.convs.find(x => x.user_id === userId);
      if (c) { msgState.curConv = c; renderConvList(); loadThread(); return; }
      await new Promise(r => setTimeout(r, 1000));
    }
    return;
  }
}

/* ---------- 话术库 ---------- */
async function loadPhrases() {
  const r = await api('phrases_list');
  if (r.ok) state.phrases = r.items;
}
async function renderPhrases() {
  await loadPhrases();
  const cats = ['全部', ...new Set(state.phrases.map(p => p.category).filter(Boolean))];
  const list = state.phrases.filter(p => state.phCat === '全部' || p.category === state.phCat);
  document.getElementById('page').innerHTML = `
    <div class="toolbar"><button class="btn primary" id="ph-add">＋ 新增话术</button></div>
    <div class="chips">${cats.map(c =>
      `<div class="chip${c === state.phCat ? ' sel' : ''}" data-c="${esc(c)}">${esc(c)}</div>`).join('')}</div>
    <div class="phrase-grid">${list.map(p => `
      <div class="phrase-card"><span class="cat">${esc(p.category)}</span>
        <h4>${esc(p.title)}</h4><p>${esc(p.content)}</p>
        <div class="ops"><button class="btn" data-view="${p.id}">查看</button>
        <button class="btn" data-edit="${p.id}">编辑</button>
        <button class="btn danger" data-del="${p.id}">删除</button></div></div>`).join('')
      || '<div style="color:var(--muted)">暂无话术</div>'}</div>`;
  document.querySelectorAll('.chip').forEach(c => c.onclick = () => {
    state.phCat = c.dataset.c; renderPhrases(); });
  document.getElementById('ph-add').onclick = () => phraseForm();
  document.querySelectorAll('[data-view]').forEach(b => b.onclick = () => {
    const p = state.phrases.find(x => x.id === +b.dataset.view);
    showInfo(p.title, p.content); });
  document.querySelectorAll('[data-edit]').forEach(b => b.onclick = () => {
    const p = state.phrases.find(x => x.id === +b.dataset.edit);
    phraseForm(p); });
  document.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('删除这条话术？')) return;
    await api('phrase_delete', +b.dataset.del); renderPhrases(); });
}
async function phraseForm(p) {
  p = p || {};
  const d = await openModal(p.id ? '编辑话术' : '新增话术', `
    <div class="form-grid">
      <label>分类</label><input type="text" name="category" value="${esc(p.category || '私信回复')}">
      <label>标题</label><input type="text" name="title" value="${esc(p.title || '')}">
    </div>
    <label style="color:var(--muted);font-size:13px">内容</label>
    <textarea name="content" style="min-height:140px">${esc(p.content || '')}</textarea>
    <div class="hint">只写站内合规表达（如“看我主页简介”），不要出现微信号等导流字样。</div>`);
  if (!d) return;
  const r = await api('phrase_save', Object.assign({ id: p.id }, d));
  if (r.ok) { toast('已保存'); renderPhrases(); } else toast(r.error, true);
}

/* ---------- 集群 ---------- */
let clusterTimer = null;
async function renderCluster() {
  document.getElementById('page').innerHTML = `
    <div class="toolbar">
      <button class="btn" id="c-ref">🔄 刷新</button>
      <span class="hint">主控模式：查看在线 worker。Worker 启动：python run.py --worker --master http://主控IP:8901 --name worker1</span>
    </div>
    <div id="c-box"></div>`;
  document.getElementById('c-ref').onclick = () => loadCluster();
  clearInterval(clusterTimer);
  clusterTimer = setInterval(() => { if (curSection === 'cluster') loadCluster(); }, 10000);
  loadCluster();
}
async function loadCluster() {
  const box = document.getElementById('c-box');
  if (!box) return;
  const r = await api('cluster_status');
  const workers = r.ok ? r.workers : [];
  const total = workers.reduce((s, w) => s + (w.accounts || 0), 0);
  box.innerHTML = `
    <div class="cards">
      <div class="card"><div class="num">${workers.length}</div><div class="lbl">在线 Worker</div></div>
      <div class="card"><div class="num">${total}</div><div class="lbl">集群账号总数</div></div>
    </div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>Worker</th><th>账号数</th><th>负载</th><th>最后心跳</th>
    </tr></thead><tbody>
    ${workers.map(w => `<tr>
      <td><b>${esc(w.name)}</b></td>
      <td>${w.accounts || 0}</td>
      <td>${esc(String(w.load || ''))}</td>
      <td>${new Date(w.last_seen * 1000).toLocaleTimeString()}</td>
    </tr>`).join('') || '<tr><td colspan="4" style="text-align:center;color:var(--muted)">暂无在线 worker</td></tr>'}
    </tbody></table></div>`;
}

/* ---------- 浏览器群控 ---------- */
let gridTimer = null;
async function renderGrid() {
  document.getElementById('page').innerHTML = `
    <div class="toolbar">
      <button class="btn" id="gr-ref">🔄 刷新</button>
      <span class="hint">每 5 秒自动刷新截图。只显示正在运行的浏览器；没启动的不在里面。</span>
    </div>
    <div id="gr-box" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px"></div>`;
  document.getElementById('gr-ref').onclick = () => loadGrid();
  clearInterval(gridTimer);
  gridTimer = setInterval(() => { if (curSection === 'grid') loadGrid(); }, 5000);
  loadGrid();
}
async function loadGrid() {
  const box = document.getElementById('gr-box');
  if (!box) return;
  const r = await api('browsers_grid');
  const items = r.ok ? r.items : [];
  if (!items.length) {
    box.innerHTML = '<div style="color:var(--muted);padding:40px;text-align:center">没有运行中的浏览器<br>去「账号管理」点「启动浏览器」</div>';
    return;
  }
  box.innerHTML = items.map(x => `
    <div class="card" style="padding:10px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <b>${esc(x.nickname)}</b>
        <span class="mono" style="font-size:11px;color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.url || '')}</span>
      </div>
      ${x.b64 ? `<img src="data:image/png;base64,${x.b64}" style="width:100%;border-radius:8px;border:1px solid var(--border)">`
              : '<div style="padding:40px;text-align:center;color:var(--muted)">截图失败</div>'}
    </div>`).join('');
}

/* ---------- 系统日志 ---------- */
let logCursor = 0, sysLogTimer = null, logOnlyErrors = false;
async function renderLogs() {
  document.getElementById('page').innerHTML = `
    <div class="toolbar">
      <button class="btn ${logOnlyErrors ? '' : 'primary'}" id="g-all">全部</button>
      <button class="btn ${logOnlyErrors ? 'primary' : ''}" id="g-err">只看报错</button>
      <button class="btn" id="g-exp">⬇ 导出日志</button>
      <button class="btn" id="g-ref">刷新</button>
      <span class="hint">报错标红，自动滚动到最新。有问题点「导出日志」发给开发者。</span>
    </div>
    <div class="card" id="g-box" style="height:60vh;overflow-y:auto;font-family:monospace;font-size:12px;line-height:1.7"></div>`;
  document.getElementById('g-all').onclick = () => { logOnlyErrors = false; logCursor = 0; renderLogs(); };
  document.getElementById('g-err').onclick = () => { logOnlyErrors = true; logCursor = 0; renderLogs(); };
  document.getElementById('g-ref').onclick = () => { logCursor = 0; loadLogs(true); };
  document.getElementById('g-exp').onclick = async () => {
    const r = await api('system_logs_export');
    if (r.ok) { download(r.filename, r.content); toast('已导出 ' + r.filename); }
    else toast(r.error, true);
  };
  clearInterval(sysLogTimer);
  sysLogTimer = setInterval(() => { if (curSection === 'logs') loadLogs(false); }, 3000);
  loadLogs(true);
}
async function loadLogs(clear) {
  const box = document.getElementById('g-box');
  if (!box) return;
  if (clear) { box.innerHTML = ''; logCursor = 0; }
  const r = await api('system_logs', logCursor, 200, logOnlyErrors);
  if (!r.ok) return;
  logCursor = r.cursor;
  for (const it of r.items) {
    const line = esc(it.line);
    const isErr = line.includes('status=failed') || line.includes('error=');
    const isWarn = line.includes('status=warning');
    const color = isErr ? 'var(--danger)' : isWarn ? '#f0a832' : 'var(--muted)';
    const div = document.createElement('div');
    div.style.color = color;
    if (isErr) div.style.fontWeight = 'bold';
    div.textContent = it.line;
    box.appendChild(div);
  }
  box.scrollTop = box.scrollHeight;
}

/* ---------- 发布记录 ---------- */
async function renderRecords() {
  const r = await api('records_list');
  const rows = (r.ok ? r.items : []).map(x => `<tr>
    <td class="mono">${x.id}</td><td><b>${esc(x.nickname || '')}</b></td>
    <td>${esc(x.title_used)}</td><td>${x.image_count}</td>
    <td><span class="badge done">${esc(x.status)}</span></td>
    <td>${x.screenshot ? `<button class="btn" data-shot="${x.id}">📷 查看截图</button>` : '<span style="color:var(--muted)">-</span>'}</td>
    <td class="mono" style="color:var(--muted)">${esc(x.created_at)}</td></tr>`).join('');
  document.getElementById('page').innerHTML = `
    <div class="toolbar">
      <button class="btn" id="r-exp">导出CSV</button>
      <button class="btn danger" id="r-clear">清空记录</button>
      <button class="btn" id="r-ref">刷新</button>
    </div>
    <div class="tbl-wrap"><table><thead><tr>
      <th>ID</th><th>账号</th><th>标题</th><th>图片数</th><th>状态</th><th>截图</th><th>时间</th>
    </tr></thead><tbody>${rows || '<tr><td colspan="7" style="text-align:center;color:var(--muted)">暂无记录</td></tr>'}</tbody></table></div>`;
  document.querySelectorAll('[data-shot]').forEach(b => b.onclick = async () => {
    const r2 = await api('record_screenshot', +b.dataset.shot);
    if (!r2.ok) return toast(r2.error, true);
    openModal('发布截图', `<img src="data:image/png;base64,${r2.b64}" style="max-width:100%;border-radius:8px">`, '关闭');
  });
  document.getElementById('r-exp').onclick = async () => {
    const r2 = await api('records_export_csv');
    if (r2.ok) { download('发布记录.csv', r2.csv); toast(`已导出 ${r2.count} 条`); }
  };
  document.getElementById('r-clear').onclick = async () => {
    if (!confirm('清空全部发布记录？（账号和草稿不受影响）')) return;
    await api('records_clear'); renderRecords(); };
  document.getElementById('r-ref').onclick = renderRecords;
}

/* ---------- 设置 ---------- */
async function renderSettings() {
  const r = await api('app_info');
  const info = r.ok ? r : {};
  const w = await api('custom_words_list');
  const words = w.ok ? w.items : [];
  document.getElementById('page').innerHTML = `
    <div class="section-sub">环境信息</div>
    <div class="card"><div class="kv">
      <div class="k">版本</div><div>v${esc(info.version || '')}</div>
      <div class="k">数据目录</div><div class="mono">${esc(info.data_dir || '')}</div>
      <div class="k">中文字体</div><div class="mono">${esc(info.font || '')}</div>
      <div class="k">选择器配置</div><div class="mono">${esc(info.selectors_path || '')}</div>
    </div></div>
    <div class="toolbar" style="margin-top:14px">
      <button class="btn" id="s-close">关闭所有浏览器</button>
      <button class="btn" id="s-demo">敏感词自查演示</button>
    </div>
    <div class="section-sub">自定义敏感词（一行一个）</div>
    <textarea id="s-words" style="min-height:110px">${esc(words.join('\n'))}</textarea>
    <div class="toolbar"><button class="btn primary" id="s-save">保存</button></div>
    <div class="section-sub">🤖 AI 回复（DeepSeek）</div>
    <div class="card"><div class="form-grid">
      <label>API Key</label>
      <div style="display:flex;gap:8px">
        <input id="s-aikey" type="password" placeholder="sk-..." style="flex:1">
        <button class="btn" id="s-aitest">测试连接</button>
      </div>
      <label>业务设定</label>
      <textarea id="s-aibiz" style="min-height:80px" placeholder="例：我们是杭州的科技美肤门店，主做面部护理，客单价300-800，加微信送体验券…"></textarea>
    </div>
    <div class="toolbar"><button class="btn primary" id="s-aisave">保存 AI 设置</button></div>
    <div class="hint" id="s-aiinfo">Key 只存本机。私信页点「✨ AI 回复」自动生成草稿填入输入框，检查后点发送。</div></div>
    <div class="hint">账号、代理等敏感信息只存本机数据库，请勿把数据目录发给他人。</div>`;
  document.getElementById('s-close').onclick = async () => {
    await api('browsers_close'); toast('已关闭所有浏览器'); };
  document.getElementById('s-demo').onclick = async () => {
    const r2 = await api('sensitive_check', '加微信私聊我，最低价全网第一，vx详谈，点击跳转站外下单');
    const hits = (r2.ok ? r2.hits : []).map(h => `${h.word} x${h.count}`).join('、') || '无命中';
    showInfo('敏感词自查演示', '示例文本：加微信私聊我，最低价全网第一，vx详谈，点击跳转站外下单\n\n命中：' + hits); };
  document.getElementById('s-save').onclick = async () => {
    const ws = document.getElementById('s-words').value.split('\n');
    const r2 = await api('custom_words_save', ws);
    if (r2.ok) toast(`已保存 ${r2.count} 个`); else toast(r2.error, true); };
  // AI 设置
  const aiCfg = await api('ai_settings_get');
  if (aiCfg.ok) {
    if (aiCfg.masked) document.getElementById('s-aiinfo').textContent =
      `Key 已配置 (${aiCfg.masked})。私信页点「✨ AI 回复」自动生成草稿填入输入框，检查后点发送。`;
    document.getElementById('s-aibiz').value = aiCfg.business_context || '';
  }
  document.getElementById('s-aisave').onclick = async () => {
    const k = document.getElementById('s-aikey').value;
    const b = document.getElementById('s-aibiz').value;
    const r2 = await api('ai_settings_save', k || null, b);
    if (r2.ok) { toast('AI 设置已保存'); document.getElementById('s-aikey').value = ''; }
    else toast(r2.error, true); };
  document.getElementById('s-aitest').onclick = async () => {
    toast('测试中…');
    const r2 = await api('ai_settings_test');
    toast(r2.msg || r2.error, !r2.ok); };
}

let msgTimer = null;
const msgState = { aid: null, monitoring: false, convs: [], curConv: null, thread: [] };

async function renderMessages() {
  await loadAccounts(); await loadPhrases();
  msgState.monitoring = false; msgState.curConv = null; msgState.thread = [];
  clearInterval(msgTimer);
  const opts = state.accounts.map(a =>
    `<option value="${a.id}">${esc(a.nickname)}</option>`).join('');
  document.getElementById('page').innerHTML = `
    <div class="toolbar" style="margin-top:0">
      <select id="m-acct" style="max-width:240px">${opts || '<option value="">暂无账号</option>'}</select>
      <button class="btn primary" id="m-add">➕ 加入监控</button>
      <button class="btn" id="m-stopall">■ 停止全部</button>
      <select id="m-group" style="max-width:150px;width:150px"></select>
      <button class="btn" id="m-add-group">本组全部加入</button>
      <button class="btn" id="m-selftest">🔍 消息页自检</button>
      <span class="hint" id="m-quota"></span>
    </div>
    <div class="hint">全账号轮转监控：分片 headless 浏览器逐账号轮查（凭证+专属代理），只读；有新私信自动弹窗。手动打开某账号浏览器时，该账号本轮自动跳过。</div>
    <div class="card" style="margin-top:4px"><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
      <span id="m-engine" class="hint">引擎未启动</span>
      <span class="hint">槽位</span>
      <select id="m-slots" style="max-width:80px;width:80px">
        <option value="4">4</option><option value="6" selected>6</option>
        <option value="8">8</option><option value="10">10</option>
        <option value="16">16</option></select>
      <button class="btn" id="m-apply-slots">应用</button>
    </div></div>
    <div class="cards" id="m-status" style="grid-template-columns:repeat(auto-fill,minmax(230px,1fr));margin-top:12px"></div>
    <div class="hint">回复风控：逐条人工确认发送；每账号每小时最多 20 条；同一会话防重发；发送前过敏感词；不做自动回复。</div>
    <div style="display:flex;gap:14px;margin-top:12px;min-height:430px">
      <div class="tbl-wrap" style="width:330px;flex-shrink:0;max-height:560px;overflow-y:auto" id="m-convs">
        <div style="padding:20px;color:var(--muted);text-align:center">点击上方账号卡片的「查看会话」</div></div>
      <div class="card" style="flex:1;display:flex;flex-direction:column;min-width:0">
        <div id="m-thread" style="flex:1;overflow-y:auto;min-height:300px;max-height:480px;padding:6px">
          <div style="color:var(--muted);text-align:center;padding-top:60px">选择左侧会话查看消息</div></div>
        <div style="display:flex;gap:8px;margin-top:10px">
          <select id="m-phrase" style="max-width:190px">
            <option value="">插入话术…</option>
            ${state.phrases.map(p => `<option value="${p.id}">[${esc(p.category)}] ${esc(p.title)}</option>`).join('')}
          </select>
          <input type="text" id="m-reply" placeholder="输入回复内容…" style="flex:1">
          <button class="btn" id="m-ai">✨ AI 回复</button>
          <button class="btn primary" id="m-send">发送</button>
        </div>
      </div>
    </div>`;
  document.getElementById('m-add').onclick = async () => {
    const aid = +document.getElementById('m-acct').value;
    if (!aid) return toast('请先选择账号', true);
    const r = await api('monitor_enable', aid);
    if (r.ok) { toast('已加入监控'); refreshMonitorStatus(); }
    else toast(r.error, true);
  };
  const groups = [...new Set(state.accounts.map(a => a.group_name || '默认分组'))];
  document.getElementById('m-group').innerHTML =
    groups.map(g => `<option value="${esc(g)}">${esc(g)}</option>`).join('');
  document.getElementById('m-add-group').onclick = async () => {
    const g = document.getElementById('m-group').value;
    const list = state.accounts.filter(a => (a.group_name || '默认分组') === g);
    let okN = 0, fail = [];
    for (const a of list) {
      const r = await api('monitor_enable', a.id);
      r.ok ? okN++ : fail.push(a.nickname);
    }
    toast(`分组「${g}」：${okN} 个加入监控${fail.length ? '，失败：' + fail.join('、') : ''}`,
      fail.length > 0);
    refreshMonitorStatus();
  };
  document.getElementById('m-stopall').onclick = async () => {
    await api('monitor_stop_all');
    toast('已停止全部监控'); refreshMonitorStatus();
  };
  document.getElementById('m-apply-slots').onclick = async () => {
    const v = +document.getElementById('m-slots').value;
    const r = await api('monitor_configure', v);
    if (r.ok) { toast(`已设为 ${r.slots} 槽位`); refreshMonitorStatus(); }
    else toast(r.error, true);
  };
  document.getElementById('m-selftest').onclick = async () => {
    const aid = +document.getElementById('m-acct').value;
    if (!aid) return toast('请先选择账号', true);
    const r = await api('message_selftest', aid);
    toast(r.msg || '检测中…');
  };
  document.getElementById('m-phrase').onchange = (e) => {
    const p = state.phrases.find(x => x.id === +e.target.value);
    if (p) {
      const box = document.getElementById('m-reply');
      box.value = (box.value ? box.value + '\n' : '') + p.content;
      box.focus();
    }
    e.target.value = '';
  };
  document.getElementById('m-send').onclick = sendReply;
  document.getElementById('m-ai').onclick = async () => {
    const uid = msgState.curConv && msgState.curConv.user_id;
    if (!msgState.aid || !uid) return toast('请先选择会话', true);
    toast('AI 生成中…');
    const r = await api('ai_draft_reply', msgState.aid, uid);
    if (r.ok) {
      document.getElementById('m-reply').value = r.draft;
      toast('草稿已填入，检查后点发送');
    } else toast(r.error, true);
  };
  document.getElementById('m-reply').addEventListener('keydown', e => {
    if (e.key === 'Enter') sendReply(); });
  clearInterval(msgTimer);
  msgTimer = setInterval(refreshMonitorStatus, 10000);
  refreshMonitorStatus();
}

async function refreshMonitorStatus() {
  if (curSection !== 'messages') return;
  const r = await api('monitor_status');
  const box = document.getElementById('m-status');
  if (!box || !r.ok) return;
  const eng = document.getElementById('m-engine');
  if (eng) eng.textContent = r.running
    ? `轮转引擎运行中 · ${r.slots} 槽位 · ${r.total} 个账号 · 单轮全量约 ${r.sweep_sec} 秒`
    : '轮转引擎未启动（把账号加入监控后启动）';
  const accs = r.accounts || {};
  const ids = Object.keys(accs);
  box.innerHTML = ids.map(id => {
    const st = accs[id];
    const nick = (r.nicknames && r.nicknames[id]) || ('账号' + id);
    const badge = st.state === 'running' ? '<span class="badge done">监控中</span>'
      : st.state === 'error' ? '<span class="badge cancelled">异常</span>'
      : '<span class="badge pending">等待中</span>';
    return `<div class="card"><div style="display:flex;justify-content:space-between;align-items:center">
      <b>${esc(nick)}</b>${badge}</div>
      <div class="num" style="font-size:22px;margin-top:6px">${st.unread || 0}</div>
      <div class="lbl">未读私信 · 上次检查 ${esc(st.last_check || '-')}</div>
      ${st.error ? `<div class="hint" style="color:var(--danger)">${esc(st.error)}</div>` : ''}
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn" data-view="${id}">查看会话</button>
        <button class="btn" data-relogin="${id}">重新登录</button>
        <button class="btn danger" data-rm="${id}">移除</button></div></div>`;
  }).join('') || '<div style="color:var(--muted)">暂无监控中的账号，把账号加入监控后开始轮询</div>';
  box.querySelectorAll('[data-view]').forEach(b => b.onclick = () => {
    document.getElementById('m-acct').value = b.dataset.view;
    msgState.aid = +b.dataset.view;
    viewAccountConvs();
  });
  box.querySelectorAll('[data-relogin]').forEach(b => b.onclick = async () => {
    const r = await api('browser_launch', +b.dataset.relogin);
    toast(r.msg || '正在启动浏览器，请扫码登录…');
  });
  box.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => {
    await api('monitor_disable', +b.dataset.rm);
    refreshMonitorStatus();
  });
}

async function viewAccountConvs() {
  if (!msgState.aid) return;
  const r = await api('messages_conversations', msgState.aid);
  if (!r.ok && r.error && r.error.indexOf('请先') >= 0) {
    toast('正在为该账号打开私信页…（如需扫码请在浏览器中完成）');
    await api('messages_open', msgState.aid);
    // 轮询等就绪：扫码登录可能要几分钟，每 10 秒重试一次，最多 5 分钟
    let tries = 0;
    const timer = setInterval(async () => {
      if (curSection !== 'messages' || ++tries > 30) {
        clearInterval(timer);
        if (tries > 30) toast('私信页仍未就绪，请确认浏览器中已登录', true);
        return;
      }
      const r2 = await api('messages_conversations', msgState.aid);
      if (r2.ok) {
        clearInterval(timer);
        msgState.convs = r2.items; msgState.curConv = null; renderConvList();
        document.getElementById('m-thread').innerHTML =
          '<div style="color:var(--muted);text-align:center;padding-top:60px">选择左侧会话查看消息</div>';
      }
      // 仍未就绪则继续等（不弹 toast 刷屏）
    }, 10000);
    return;
  }
  if (r.ok) {
    msgState.convs = r.items; msgState.curConv = null; renderConvList();
    document.getElementById('m-thread').innerHTML =
      '<div style="color:var(--muted);text-align:center;padding-top:60px">选择左侧会话查看消息</div>';
    const q = await api('messages_send_quota', msgState.aid);
    if (q.ok) document.getElementById('m-quota').textContent =
      `本小时可发送 ${q.limit - q.used} 条`;
  } else toast(r.error, true);
}

async function pollConvs() {
  if (curSection !== 'messages' || !msgState.monitoring || !msgState.aid) return;
  const r = await api('messages_conversations', msgState.aid);
  if (r.ok) { msgState.convs = r.items; renderConvList(); }
}

function renderConvList() {
  const box = document.getElementById('m-convs');
  if (!box) return;
  box.innerHTML = msgState.convs.map(c => `
    <div class="nav-item${msgState.curConv && msgState.curConv.user_id === c.user_id ? ' active' : ''}"
         data-uid="${esc(c.user_id)}" style="margin:4px">
      <div style="flex:1;min-width:0">
        <div style="font-weight:600">${esc(c.nickname || '(未知)')}
          ${c.unread ? `<span class="badge pending" style="margin-left:6px">${esc(c.unread)}</span>` : ''}</div>
        <div style="color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(c.preview)}</div>
      </div>
      <div class="mono" style="font-size:11px">${esc(c.time_label)}</div>
    </div>`).join('') || '<div style="padding:20px;color:var(--muted);text-align:center">暂无会话</div>';
  box.querySelectorAll('[data-uid]').forEach(el => el.onclick = () => {
    const c = msgState.convs.find(x => x.user_id === el.dataset.uid);
    if (c) { msgState.curConv = c; renderConvList(); loadThread(); }
  });
}

async function loadThread() {
  const c = msgState.curConv;
  if (!c || !msgState.aid) return;
  const r = await api('messages_thread', msgState.aid, c.user_id);
  if (!r.ok) return toast(r.error, true);
  msgState.thread = r.items;
  const box = document.getElementById('m-thread');
  box.innerHTML = msgState.thread.map(m => `
    <div style="display:flex;justify-content:${m.direction === 'outgoing' ? 'flex-end' : 'flex-start'};margin:6px 0">
      <div style="max-width:75%;background:${m.direction === 'outgoing' ? 'var(--accent-bg)' : 'var(--card2)'};
           border:1px solid var(--line);border-radius:10px;padding:9px 13px;font-size:13.5px;line-height:1.6">
        ${esc(m.text)}${m.has_img && !m.text ? '<span style="color:var(--muted)">[图片]</span>' : ''}</div>
    </div>`).join('') || '<div style="color:var(--muted);text-align:center">暂无消息</div>';
  box.scrollTop = box.scrollHeight;
}

async function sendReply() {
  const text = document.getElementById('m-reply').value.trim();
  if (!text) return toast('请输入回复内容', true);
  if (!msgState.curConv || !msgState.aid) return toast('请先选择账号和会话', true);
  const a = state.accounts.find(x => x.id === msgState.aid);
  const sc = await api('sensitive_check', text);
  const hits = sc.ok ? sc.hits : [];
  const q = await api('messages_send_quota', msgState.aid);
  const d = await openModal('确认发送', `
    <div class="kv"><div class="k">发送账号</div><div>${esc(a ? a.nickname : '')}</div>
      <div class="k">收件人</div><div>${esc(msgState.curConv.nickname)}</div>
      <div class="k">本小时</div><div>已发送 ${q.ok ? q.used : '?'} / ${q.ok ? q.limit : '?'}</div></div>
    <pre class="check">${esc(text)}</pre>
    ${hits.length ? `<div class="hint" style="color:var(--warn)">⚠️ 命中敏感词：${esc(hits.map(h => h.word).join('、'))}，建议修改后再发。</div>` : ''}`,
    '确认发送');
  if (!d) return;
  const r = await api('messages_send', msgState.aid, msgState.curConv.user_id,
                      msgState.curConv.nickname, text);
  if (r.ok) {
    toast('已发送'); document.getElementById('m-reply').value = '';
    setTimeout(loadThread, 1200);
  } else toast(r.error, true);
}

const RENDER = { dashboard: renderDashboard, accounts: renderAccounts, drafts: renderDrafts, publish: renderPublish,
  schedules: renderSchedules, messages: renderMessages, leads: renderLeads, phrases: renderPhrases, records: renderRecords, grid: renderGrid, cluster: renderCluster, logs: renderLogs, settings: renderSettings };

/* ---------- 弹窗轮询（工作线程 -> 前端） ---------- */
setInterval(async () => {
  try {
    const r = await api('prompt_poll');
    const p = r.ok && r.prompt;
    if (!p || document.getElementById('prompt-root').innerHTML) return;
    const root = document.getElementById('prompt-root');
    root.innerHTML = `<div class="modal-mask"><div class="modal"><h3>${esc(p.title)}</h3>
      <pre class="check">${esc(p.msg)}</pre>
      <div class="foot">${p.options.map((o, i) =>
        `<button class="btn${i === 0 ? ' primary' : ''}" data-a="${esc(o)}">${esc(o)}</button>`).join('')}</div></div></div>`;
    root.querySelectorAll('[data-a]').forEach(b => b.onclick = async () => {
      await api('prompt_answer', p.id, b.dataset.a);
      root.innerHTML = '';
    });
  } catch (e) { /* 后台线程未就绪时忽略 */ }
}, 800);

/* ---------- 启动 ---------- */
let msgEventCursor = 0;
async function init() {
  try {
    const info = await api('app_info');
    if (!info.ok) throw new Error(info.error || '读取应用状态失败');
    window.matrixExtrasEnabled = !!info.features?.extras;
    document.getElementById('side-foot').textContent = `v${info.version} · 账号工作台 / 发布 / 消息 / 采集`;
    if (!info.features?.extras) {
      for (let i = NAV.length - 1; i >= 0; i--) if (['materials'].includes(NAV[i].id)) NAV.splice(i, 1);
    }
  } catch (error) { toast(error.message, true); }
  renderNav();
  showSection('account_hub');
  // 全局新消息提醒：不在消息页也能收到
  setInterval(async () => {
    try {
      const r = await api('monitor_events', msgEventCursor);
      if (r.ok && r.items.length) {
        msgEventCursor = r.cursor;
        r.items.forEach(ev => toast(`\u2709\uFE0F ${ev.nickname}：${ev.text}`));
      }
      const st = await api('monitor_status');
      if (st.ok) {
        const total = Object.values(st.accounts || {})
          .reduce((n, a) => n + (a.unread || 0), 0);
        const b = document.getElementById('nav-msg-badge');
        if (b) { b.textContent = total; b.style.display = total ? '' : 'none'; }
      }
    } catch (e) {}
  }, 15000);
}
let _inited = false;
function tryInit() {
  if (_inited || !window.pywebview || !window.pywebview.api) return;
  _inited = true;
  MOCK = false;
  setTimeout(init, 0);
}
window.addEventListener('pywebviewready', tryInit);
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', tryInit);
else tryInit();

/* ---------- Mock（无 pywebview 环境下演示/截图用） ---------- */
const _mockDB = {
  accounts: [
    { id: 1, nickname: '演示账号A', xhs_id: 'demo_a', group_name: '美食组', proxy_scheme: 'http', proxy_host: '192.0.2.10', proxy_port: 8080, proxy_user: 'demo', proxy_pass: 'demo', notes: '演示数据' },
    { id: 2, nickname: '演示账号B', xhs_id: 'demo_b', group_name: '美食组', proxy_scheme: 'http', proxy_host: '192.0.2.11', proxy_port: 8080, proxy_user: 'demo', proxy_pass: 'demo', notes: '演示数据' },
    { id: 3, nickname: '演示账号C', xhs_id: 'demo_c', group_name: '美妆组', proxy_scheme: '', proxy_host: '', proxy_port: 0, proxy_user: '', proxy_pass: '', notes: '演示数据' },
  ],
  drafts: [
    { id: 1, title: '老街面馆探店实测', body: '今天去探了一家面馆…', topics: '#探店 #美食', mode: 'poster', template: 'rice', updated_at: '2026-10-07 10:00' },
    { id: 2, title: '图文草稿示例', body: '图文正文示例', topics: '#种草', mode: 'images', template: '', updated_at: '2026-10-07 10:00' },
  ],
  schedules: [
    { id: 1, draft_id: 1, draft_title: '老街面馆探店实测', account_ids: [1, 2], scheduled_at: '2026-10-08 09:00', status: 'pending', note: '早高峰演示' },
  ],
  phrases: [
    { id: 1, category: '私信回复', title: '咨询回复模板', content: '谢谢咨询！详情可以看我主页简介，款式都在橱窗里～' },
    { id: 2, category: '评论区引导', title: '评论引导', content: '同问的姐妹可以点我头像看置顶笔记，有完整攻略。' },
    { id: 3, category: '主页简介', title: '简介模板', content: '本地探店｜每周三更｜合作请私信' },
  ],
  convs: [
    { user_id: 'a1b2c3d4e5f60718293a4b5c', nickname: '咨询客户A', preview: '请问这款还有货吗？', time_label: '10:20', unread: '2' },
    { user_id: 'b2c3d4e5f60718293a4b5c6d', nickname: '咨询客户B', preview: '地址发我一下', time_label: '昨天', unread: '' },
  ],
  thread: [
    { direction: 'incoming', text: '请问这款还有货吗？', has_img: false },
    { direction: 'outgoing', text: '有的，亲，详情可以看我主页简介～', has_img: false },
    { direction: 'incoming', text: '好的，谢谢', has_img: false },
  ],
  records: [
    { id: 1, nickname: '演示账号A', title_used: '老街面馆探店实测', image_count: 2, status: 'done', created_at: '2026-10-07 15:00' },
  ],
  templates: [
    { id: 'rice', name: '米白深青' }, { id: 'classic', name: '经典黄黑' },
    { id: 'dark', name: '暗夜高级' }, { id: 'morandi', name: '莫兰迪灰粉' }, { id: 'mint', name: '清新薄荷' },
  ],
};
let _mockLog = [];
let _mockEventsSent = false;
async function mockApi(method, ...args) {
  const ok = (d) => Object.assign({ ok: true }, d);
  switch (method) {
    case 'accounts_list': return ok({ items: _mockDB.accounts });
    case 'account_create': { const a = Object.assign({ id: Date.now() % 100000 }, args[0]); _mockDB.accounts.push(a); return ok({ id: a.id }); }
    case 'account_update': { const i = _mockDB.accounts.findIndex(x => x.id === +args[0]); if (i >= 0) Object.assign(_mockDB.accounts[i], args[1]); return ok({ done: i >= 0 }); }
    case 'account_delete': _mockDB.accounts = _mockDB.accounts.filter(x => x.id !== +args[0]); return ok({ done: true });
    case 'accounts_export': return ok({ data: JSON.stringify(_mockDB.accounts, null, 2) });
    case 'accounts_import_text': return ok({ added: 1, skipped: 0, errors: [] });
    case 'browser_launch': case 'browser_selftest': return ok({ msg: '（演示模式）' });
    case 'browsers_close': case 'browsers_status': return ok({ running: [] });
    case 'drafts_list': return ok({ items: _mockDB.drafts });
    case 'draft_save': { const d = args[0]; if (d.id) { const i = _mockDB.drafts.findIndex(x => x.id === +d.id); Object.assign(_mockDB.drafts[i], d); } else _mockDB.drafts.push(Object.assign({ id: Date.now() % 100000, updated_at: 'now' }, d)); return ok({}); }
    case 'draft_delete': _mockDB.drafts = _mockDB.drafts.filter(x => x.id !== +args[0]); return ok({});
    case 'pick_files': return ok({ paths: ['/演示/图片1.png'] });
    case 'poster_templates': return ok({ items: _mockDB.templates });
    case 'publish_start': _mockLog = [{ t: '19:00:00', msg: '（演示模式）发布流程已启动' }]; return ok({});
    case 'publish_log': { const items = _mockLog.slice(args[0] || 0); return ok({ items, cursor: _mockLog.length }); }
    case 'schedules_list': return ok({ items: _mockDB.schedules });
    case 'schedule_create': { const s = Object.assign({ id: Date.now() % 100000, status: 'pending', draft_title: '演示草稿' }, args[0]); _mockDB.schedules.push(s); return ok({ id: s.id }); }
    case 'schedule_cancel': { const s = _mockDB.schedules.find(x => x.id === +args[0]); if (s) s.status = 'cancelled'; return ok({ done: true }); }
    case 'schedule_delete': _mockDB.schedules = _mockDB.schedules.filter(x => x.id !== +args[0]); return ok({ done: true });
    case 'phrases_list': return ok({ items: _mockDB.phrases });
    case 'phrase_save': { const d = args[0]; if (d.id) { const i = _mockDB.phrases.findIndex(x => x.id === +d.id); Object.assign(_mockDB.phrases[i], d); } else _mockDB.phrases.push(Object.assign({ id: Date.now() % 100000 }, d)); return ok({}); }
    case 'phrase_delete': _mockDB.phrases = _mockDB.phrases.filter(x => x.id !== +args[0]); return ok({ done: true });
    case 'messages_open': return ok({ msg: '（演示模式）' });
    case 'messages_conversations': return ok({ items: _mockDB.convs });
    case 'messages_thread': return ok({ items: _mockDB.thread });
    case 'messages_send': return ok({ msg: '已发送（演示）' });
    case 'messages_send_quota': return ok({ used: 2, limit: 20 });
    case 'messages_close': return ok({});
    case 'monitor_enable': case 'monitor_disable': case 'monitor_stop_all': return ok({});
    case 'queue_create': return ok({ id: 1 });
    case 'queue_get': return ok({ queue: null });
    case 'queue_process_next': return ok({ msg: 'ok' });
    case 'queue_pause': case 'queue_cancel': return ok({});
    case 'proxies_assign': return ok({ assigned: 1, msg: '已分配 1 个' });
    case 'leads_list': return ok({ items: [
      { id: 1, account_id: 1, account_name: '演示账号A', user_id: 'a1b2c3d4e5f60718293a4b5c',
        nickname: '咨询客户A', preview: '请问加盟多少钱？', matched_words: ['加盟', '多少钱'],
        status: 'new', status_cn: '待跟进', updated_at: '2026-10-07 20:40' } ],
      counts: { new: 1, contacted: 0, done: 0, ignored: 0 } });
    case 'lead_set_status': return ok({});
    case 'lead_keywords_list': return ok({ items: ['价格', '加盟'] });
    case 'lead_keywords_save': return ok({ count: 2 });
    case 'contacts_list': return ok({ items: [
      { id: 1, account_id: 1, account_name: '演示账号A', user_id: 'a1b2c3d4e5f60718293a4b5c',
        nickname: '咨询客户A', wechat: 'wx123456', phone: '13800001111', qq: '',
        snippet: '我微信 wx123456', status: 'new', status_cn: '待添加',
        updated_at: '2026-10-07 20:55' } ],
      counts: { new: 1, added: 0, ignored: 0 } });
    case 'contact_set_status': return ok({});
    case 'contacts_scan_start': return ok({ msg: 'ok' });
    case 'contacts_scan_status': return ok({ running: false, accounts_done: 0,
      accounts_total: 0, convs_done: 0, convs_total: 0, current: '', found: 0 });
    case 'contacts_export': return ok({ filename: 'demo.xlsx', b64: '' });
    case 'monitor_start_all': return ok({ started: true, count: 1 });
    case 'monitor_status': return ok({ running: true, slots: 6, total: 1, sweep_sec: 42,
      accounts: { '1': { state: 'running', unread: 2, last_check: '19:00:00', error: '' } },
      nicknames: { '1': '演示账号A' } });
    case 'monitor_configure': return ok({ slots: args[0] });
    case 'account_sync_cred': return ok({ path: '/演示/credential.json' });
    case 'monitor_events': {
      if (_mockEventsSent) return ok({ items: [], cursor: 1 });
      _mockEventsSent = true;
      return ok({ items: [{ t: '19:00', aid: 1, nickname: '演示账号A',
        text: '「咨询客户A」发来 1 条新私信' }], cursor: 1 });
    }
    case 'records_list': return ok({ items: _mockDB.records });
    case 'records_export_csv': return ok({ csv: 'id,账号\n1,演示账号A', count: 1 });
    case 'records_clear': _mockDB.records = []; return ok({});
    case 'sensitive_check': return ok({ hits: [] });
    case 'custom_words_list': return ok({ items: [] });
    case 'custom_words_save': return ok({ count: args[0].length });
    case 'prompt_poll': return ok({ prompt: null });
    case 'prompt_answer': return ok({ done: true });
    case 'message_selftest': return ok({ msg: '演示模式' });
    case 'app_info': return ok({ version: '1.3', data_dir: '/演示/data', font: '演示字体', selectors_path: '/演示/selectors.json' });
    default: return { ok: false, error: '未知接口 ' + method };
  }
}
