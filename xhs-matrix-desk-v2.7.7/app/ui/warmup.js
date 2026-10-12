/* 多天浏览计划：用户创建并启动，按账号/设备记录实际浏览时间。 */
NAV.splice(2, 0, {id:'warmup', name:'浏览养号计划', ico:'📅'});
TITLES.warmup = '自定义浏览计划';
const WARMUP_STATUS = {draft:'待启动',running:'执行中',paused:'已暂停',stopped:'已停止',finished:'已结束'};
const WARMUP_RUN_STATUS = {running:'浏览中',done:'已完成',stopped:'已中断',error:'失败'};
let warmupTargets = [], warmupPlans = [], warmupRequest = 0, warmupTimer = null;
let warmupCreateRequest = 0, warmupDetailRequest = 0;
const warmupActionTasks = new Set();

async function warmupCall(method, ...args) {
  const result = await api(method, ...args);
  if (!result || !result.ok) throw new Error(result?.error || '浏览计划操作失败');
  return result;
}
function warmupGuard(fn) { return async () => { try { await fn(); } catch(error) { toast(error.message || '操作失败', true); } }; }
function warmupTargetName(target) {
  const key = target.kind === 'android' ? `android:${target.serial}` : `${target.platform}:${target.id}`;
  return warmupTargets.find(t => t.key === key)?.name || key;
}
function warmupApplyActionBusy() {
  document.querySelectorAll('[data-warmup-action]').forEach(button => {
    const plan = warmupPlans.find(item => String(item.id) === button.dataset.warmupId);
    const action = button.dataset.warmupAction;
    button.disabled = warmupActionTasks.has(button.dataset.warmupId) || !plan ||
      (action === 'start' && plan.status === 'running') ||
      (action === 'pause' && plan.status !== 'running') ||
      (action === 'stop' && ['stopped', 'draft'].includes(plan.status));
  });
}
async function warmupAction(id, action) {
  const key = String(id);
  if (warmupActionTasks.has(key)) return;
  warmupActionTasks.add(key);
  warmupApplyActionBusy();
  try {
    await warmupCall('warmup_action', Number(id), action);
    try { await renderWarmup(); }
    catch (error) { toast(`计划操作已完成，列表刷新失败：${error.message}`, true); }
  } finally {
    warmupActionTasks.delete(key);
    warmupApplyActionBusy();
  }
}
async function renderWarmup() {
  clearTimeout(warmupTimer);
  warmupTimer = null;
  const request = ++warmupRequest;
  if (curSection !== 'warmup') return;
  const [targets, plans] = await Promise.all([warmupCall('warmup_targets'), warmupCall('warmup_list')]);
  if (request !== warmupRequest || curSection !== 'warmup') return;
  warmupTargets = targets.items || []; warmupPlans = plans.items || [];
  document.getElementById('page').innerHTML = `
    <div class="hint">可自定义天数、每天每账号时长、执行时间窗口及同时执行数量。先在浏览器或手机打开平台内容页，再开始计划；计划会停留和滚动当前页面。</div>
    <div class="toolbar" style="margin-top:12px"><button class="btn primary" id="warmup-create">新建浏览计划</button><button class="btn" id="warmup-refresh">刷新账号与计划</button></div>
    <div class="hint">开始前须打开所选账号浏览器，并人工检查登录和平台提醒。任务失败后整个计划暂停。收到异常浏览提醒时请停止自动操作；随机滑动或点赞也不能保证账号不受限制。</div>
    <div class="tbl-wrap" style="margin-top:12px"><table><thead><tr><th>计划</th><th>配置</th><th>状态 / 进度</th><th>操作</th></tr></thead><tbody>
    ${warmupPlans.map(p => `<tr><td><b>${esc(p.name)}</b><div class="hint">${esc(p.start_date)} 起 · ${p.days} 天 · ${p.targets.length} 个目标</div></td>
      <td>每天 ${p.minutes} 分钟 / 账号<br>${esc(p.start_time)}–${esc(p.end_time)} · 同时 ${p.concurrency} 个</td>
      <td>${esc(WARMUP_STATUS[p.status] || p.status)} · ${p.done}/${p.total}<div class="hint">${esc(p.note || '')}</div></td>
      <td><button class="btn" data-warmup-detail="${p.id}">查看记录</button>
      ${p.status !== 'finished' ? `<button class="btn primary" data-warmup-action="start" data-warmup-id="${p.id}" ${p.status==='running'?'disabled':''}>${p.status==='draft'?'开始':'继续'}</button>
      <button class="btn" data-warmup-action="pause" data-warmup-id="${p.id}" ${p.status!=='running'?'disabled':''}>暂停</button>
      <button class="btn" data-warmup-action="stop" data-warmup-id="${p.id}" ${['stopped','draft'].includes(p.status)?'disabled':''}>停止</button>
      ${p.runs.some(r=>r.status==='error') ? `<button class="btn" data-warmup-action="retry" data-warmup-id="${p.id}">重试失败目标</button>` : ''}` : ''}</td></tr>`).join('') || '<tr><td colspan="4" style="padding:30px;text-align:center">暂无计划，可先新建并检查配置。</td></tr>'}
    </tbody></table></div>`;
  document.getElementById('warmup-create').onclick = warmupGuard(warmupCreateModal);
  document.getElementById('warmup-refresh').onclick = warmupGuard(renderWarmup);
  document.querySelectorAll('[data-warmup-action]').forEach(button => button.onclick = warmupGuard(() => warmupAction(button.dataset.warmupId, button.dataset.warmupAction)));
  warmupApplyActionBusy();
  document.querySelectorAll('[data-warmup-detail]').forEach(button => button.onclick = warmupGuard(() => warmupDetail(+button.dataset.warmupDetail)));
  warmupTimer = setTimeout(() => {
    if (curSection === 'warmup') warmupRenderView();
  }, 5000);
}
async function warmupCreateModal() {
  if (workspaceModalOpen()) throw new Error('请先关闭当前弹窗');
  const section = curSection, request = ++warmupCreateRequest;
  const items = (await warmupCall('warmup_targets')).items || [];
  if (request !== warmupCreateRequest || curSection !== section) return;
  if (!items.length) throw new Error('请先新增浏览器账号，');
  const today = new Date();
  const day = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  if (workspaceModalOpen()) throw new Error('请先关闭当前弹窗');
  const pending = workspaceModal('新建自定义浏览计划', `
    <label>计划名称</label><input name="name" value="新号浏览计划">
    <div class="form-grid"><div><label>天数（1–30）</label><input name="days" type="number" min="1" max="30" value="3"></div>
    <div><label>每天每账号分钟数（1–60）</label><input name="minutes" type="number" min="1" max="60" value="10"></div>
    <div><label>开始日期</label><input name="start_date" type="date" min="${day}" value="${day}"></div>
    <div><label>同时执行（1–3）</label><input name="concurrency" type="number" min="1" max="3" value="1"></div>
    <div><label>每天开始时间</label><input name="start_time" type="time" value="09:00"></div>
    <div><label>每天结束时间</label><input name="end_time" type="time" value="21:00"></div></div>
    <label>浏览器操作通道</label><select name="engine"><option value="builtin">自有浏览器采集器</option><option value="plugin">工作台浏览器</option></select>
    <input type="hidden" name="selected_targets" value="[]">
    <label>目标账号 / 手机</label><div style="max-height:200px;overflow:auto">${items.map((target, i) => `<label style="display:block"><input type="checkbox" data-warmup-select="${i}"> ${esc(target.kind==='android'?'安卓手机':HUB_PLATFORMS[target.platform])} · ${esc(target.name)} <span class="hint">${target.ready?'已打开 / 已连接':'需要先打开浏览器或授权设备'}</span></label>`).join('')}</div>
    <div class="hint">创建后不会立即执行，检查配置并点击“开始”。浏览器需要先扫码并打开内容页。浏览计划只浏览当前页面，不自动点赞、评论或发消息。</div>`, '创建计划');
  const root = document.getElementById('modal-root');
  const selection = root.querySelector('[name="selected_targets"]');
  const checkboxes = [...root.querySelectorAll('[data-warmup-select]')];
  for (const checkbox of checkboxes) checkbox.onchange = () => {
    if (selection) selection.value = JSON.stringify(checkboxes.filter(input=>input.checked).map(input=>+input.dataset.warmupSelect));
  };
  const data = await pending;
  if (!data) return;
  const selected = JSON.parse(data.selected_targets || '[]');
  const targets = selected.map(i => items[i]).filter(Boolean).map(t => t.kind==='android'
    ? {kind:'android',serial:t.serial} : {kind:'browser',platform:t.platform,id:t.id});
  if (!targets.length) throw new Error('请至少勾选一个账号或手机');
  const {selected_targets, ...config} = data;
  const result = await warmupCall('warmup_create', {...config, targets});
  toast(`计划 #${result.id} 已创建，检查后可开始`);
  if (curSection === 'warmup') {
    try { await renderWarmup(); }
    catch (error) { toast(`计划已创建，列表刷新失败：${error.message}`, true); }
  }
}
async function warmupDetail(id) {
  if (workspaceModalOpen()) throw new Error('请先关闭当前弹窗');
  const section = curSection, request = ++warmupDetailRequest;
  const item = (await warmupCall('warmup_list')).items.find(p=>p.id===id);
  if (request !== warmupDetailRequest || curSection !== section) return;
  if (!item) throw new Error('计划不存在');
  const names = new Map(item.targets.map(t=>[t.kind==='android'?`android:${t.serial}`:`${t.platform}:${t.id}`, warmupTargetName(t)]));
  const text = `${item.name}\n${item.start_date} 起 ${item.days} 天，每天 ${item.minutes} 分钟\n完成 ${item.done}/${item.total}\n${item.note || ''}\n\n` +
    (item.runs.map(r => `第 ${r.day_index+1} 天 · ${names.get(r.target_key)||r.target_key} · ${WARMUP_RUN_STATUS[r.status]||r.status} · ${(r.elapsed_seconds/60).toFixed(1)} 分钟${r.error?'\n'+r.error:''}`).join('\n') || '尚未执行');
  workspaceResult('浏览计划记录', text, section);
}
async function warmupRenderView() {
  const request = warmupRequest + 1;
  try { await renderWarmup(); }
  catch (error) {
    if (request !== warmupRequest || curSection !== 'warmup') return;
    document.getElementById('page').innerHTML = `<div class="hint">浏览计划加载失败：${esc(error.message)}</div><button class="btn" id="warmup-retry">重新加载</button>`;
    document.getElementById('warmup-retry').onclick = warmupRenderView;
    toast(error.message, true);
  }
}
RENDER.warmup = warmupRenderView;
