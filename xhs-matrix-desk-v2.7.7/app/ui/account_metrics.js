/* 专用观察账号读取公开数据，展示真实可读值和跨日快照。 */
let metricsRenderRequest = 0, metricsDraft = null;
function metricsRememberDraft() {
  if (!document.getElementById('metrics-targets')) return;
  metricsDraft = {observer_id:document.getElementById('metrics-observer').value,targets:document.getElementById('metrics-targets').value,works:Number(document.getElementById('metrics-works').value),comments:Number(document.getElementById('metrics-comments').value),daily:document.getElementById('metrics-daily').checked,time:document.getElementById('metrics-time').value};
}
const METRIC_LABELS = {followers:'粉丝',likes:'点赞',saves:'收藏',comments:'评论',likes_and_saves:'获赞与收藏（合计）'};
function metricsValue(value) {
  if (!value || value.value == null) return '未读到';
  return `${value.approximate ? '约 ' : ''}${esc(value.raw || value.value)}`;
}
async function renderAccountMetrics(target) {
  const request = ++metricsRenderRequest;
  const [result, accounts] = await Promise.all([hubCall('metrics_get'), hubCall('hub_accounts','xiaohongshu')]);
  if (request !== metricsRenderRequest || curSection !== 'account_hub' || hubView !== 'metrics' || !target.isConnected) return;
  const config = metricsDraft || result.config || {}, identities = result.identities || [], rows = result.items || [];
  target.innerHTML = `<div class="hint">一个专门监测账号，逐个查看多个目标账号。保存公开主页、近期作品、点赞 / 收藏 / 评论与粉丝快照；评论内容在「批量采集」查看。未展示的指标保留为“未读到”，简写计数标注为约数。跨日变化比较的是该链接上一次有记录的日期。</div>
    <div class="form-grid">
      <label for="metrics-observer">监测账号</label><select id="metrics-observer"><option value="">请选择已登录的小红书账号</option>${accounts.items.map(a=>`<option value="${a.id}" ${Number(config.observer_id)===Number(a.id)?'selected':''}>${esc(a.nickname)} (#${Number(a.id)})</option>`).join('')}</select>
      <label for="metrics-targets">目标主页</label><textarea id="metrics-targets" rows="5" placeholder="每行一个小红书 / REDnote 主页链接">${esc(config.targets || '')}</textarea>
      <label for="metrics-works">每号作品</label><input type="number" id="metrics-works" min="1" max="50" value="${config.works || 20}">
      <label for="metrics-comments">评论上限</label><input type="number" id="metrics-comments" min="1" max="1000" value="${config.comments || 1000}">
      <label>每天采集</label><div class="metrics-schedule"><label><input type="checkbox" id="metrics-daily" ${config.daily?'checked':''}>开启每日计划</label><input type="time" id="metrics-time" value="${esc(config.time || '09:00')}"></div>
    </div>
    <div class="toolbar"><button class="btn" id="metrics-known">添加已识别的本机账号主页（${identities.length}）</button><button class="btn" id="metrics-save">保存配置</button><button class="btn primary" id="metrics-run">开始本次监测</button><button class="btn" id="metrics-refresh">刷新数据</button><button class="btn" id="metrics-export">导出指标 CSV</button><button class="btn" id="metrics-batches">查看批次 / 评论</button></div>
    <div class="hint" id="metrics-note" role="status">${esc(result.note || '尚未开始监测')}${result.last_batch?' · 最近批次 #'+Number(result.last_batch):''}</div>
    <div class="hint">每日计划按本机时间运行，需要工作台保持运行、监测账号浏览器已打开且登录有效。浏览器关闭或出现验证时暂停操作并显示原因。主页作品按展示顺序选取，能识别的置顶作品跳过；不把近期作品指标当成账号全部作品的总量。</div>
    <div class="section-sub">已识别账号</div>
    ${dyTable(['本机账号','小红书号','主页内部 ID','主页链接'],identities.map(a=>`<tr><td>${esc(a.nickname)}</td><td>${esc(a.public_id || '未读到')}</td><td class="mono">${esc(a.user_id)}</td><td class="metrics-link">${esc(a.profile_url)}</td></tr>`))}
    <div class="section-sub">公开指标快照 · ${rows.length} 个主页 / 作品</div>
    ${dyTable(['目标 / 作品','类型','公开指标','跨日变化','采集时间'], rows.map(row=>`<tr><td><b>${esc(row.name || row.title || row.canonical)}</b><div class="hint metrics-link">${esc(row.canonical)}</div></td><td>${row.kind==='profile'?'账号主页':'作品'}</td>
      <td>${Object.entries(row.metrics).map(([key,value])=>`${esc(METRIC_LABELS[key] || key)}：${metricsValue(value)}`).join('<br>')}</td>
      <td>${row.comparison_day?`<div class="hint">较 ${esc(row.comparison_day)}${row.day_gap===1?'（前一天）':`（间隔 ${Number(row.day_gap)} 天）`}</div>`:'尚无前日快照'}${row.comparison_day?Object.entries(row.changes).map(([key,delta])=>`<div>${esc(METRIC_LABELS[key] || key)}：${delta==null?'无法比较':`${row.change_approximate?.[key]?'约 ':''}${delta>0?'+':''}${delta}`}</div>`).join(''):''}</td><td class="mono">${esc(row.sampled_at)}</td></tr>`))}`;
  const fields = () => ({observer_id:document.getElementById('metrics-observer').value,targets:document.getElementById('metrics-targets').value,works:Number(document.getElementById('metrics-works').value),comments:Number(document.getElementById('metrics-comments').value),daily:document.getElementById('metrics-daily').checked,time:document.getElementById('metrics-time').value});
  document.getElementById('metrics-known').onclick = () => {
    const input=document.getElementById('metrics-targets');
    input.value=[...new Set([...input.value.split(/\r?\n/).filter(Boolean),...identities.map(a=>a.profile_url)])].join('\n');
    toast('已填入目标主页，保存配置后生效');
  };
  document.getElementById('metrics-save').onclick = hubGuard(async()=>{await hubCall('metrics_save',fields());metricsDraft=null;toast('监测配置已保存');await hubRenderPane();});
  document.getElementById('metrics-run').onclick = hubGuard(async()=>{
    const button=document.getElementById('metrics-run');button.disabled=true;
    try {await hubCall('metrics_save',fields());metricsDraft=null;const started=await hubCall('metrics_run');toast(`监测批次 #${started.id} 已开始`);await hubRenderPane();}
    finally {if(button.isConnected)button.disabled=false;}
  });
  document.getElementById('metrics-refresh').onclick=hubGuard(async()=>{metricsRememberDraft();await hubRenderPane();});
  document.getElementById('metrics-export').onclick=hubGuard(async()=>{const data=await hubCall('metrics_export');download('小红书公开指标.csv',data.csv);toast(`已导出 ${data.count} 条指标`);});
  document.getElementById('metrics-batches').onclick=()=>showSection('batch_collect');
}
