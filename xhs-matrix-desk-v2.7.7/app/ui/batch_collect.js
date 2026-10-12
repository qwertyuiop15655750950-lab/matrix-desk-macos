/* 分享文案 / 主页表格批量导入；逐来源归档，创建后由用户启动。 */
NAV.splice(2,0,{id:'batch_collect',name:'批量作品评论采集',ico:'📋'});
TITLES.batch_collect='对标账号与作品评论采集';
let batchRequest=0, batchTimer=null, batchItems=[];
const batchBusy=new Set();
const BATCH_STATUS={draft:'待启动',running:'执行中',paused:'已暂停',stopped:'已停止',done:'已结束',done_with_errors:'已结束，有失败'};
async function batchCall(method,...args){
 const result=await api(method,...args);
 if(!result?.ok)throw new Error(result?.error||'批量采集操作失败');
 return result;
}
async function renderBatchCollect(){
 clearTimeout(batchTimer);
 const request=++batchRequest;
 const result=await batchCall('batch_list');
 if(request!==batchRequest||curSection!=='batch_collect')return;
 batchItems=result.items;
 document.getElementById('page').innerHTML=`
  <div class="publish-intro"><p>导入链接或对标主页，集中采集与导出。</p><span>小红书 · 抖音</span></div>
  <div class="toolbar"><button class="btn primary" id="batch-create">导入链接 / 主页表格</button><button class="btn" id="batch-refresh">刷新</button><details class="inline-more"><summary>已有归档</summary><div class="more-actions"><button class="btn" id="batch-xhs-archive">小红书评论</button><button class="btn" id="batch-dy-archive">抖音评论</button></div></details></div>
  <details class="secondary-info"><summary>采集范围与说明</summary><p class="hint">先在账号管理打开浏览器并登录。默认每个主页最新 20 条作品；关键词用于标记评论。登录失效、未到末尾或达到读取上限时会保留实际进度。</p></details>
  ${dyTable(['批次','结果','状态','操作'],batchItems.map(item=>`<tr><td>#${item.id}<br><small>${esc(item.created_at)}</small></td><td>${item.sources.filter(s=>s.status==='done').length}/${item.sources.length} 个来源 · ${item.comments} 条评论</td><td>${esc(BATCH_STATUS[item.status]||item.status)}<div class="hint">${esc(item.note)}</div></td><td><button class="btn" data-batch-details="${item.id}">来源明细</button><button class="btn" data-batch-export="${item.id}">导出评论 CSV</button>${['done','done_with_errors'].includes(item.status)?'':`<button class="btn primary" data-batch-action="start" data-batch-id="${item.id}" ${item.status==='running'||batchBusy.has(item.id)?'disabled':''}>${item.status==='draft'?'开始':'继续'}</button><button class="btn" data-batch-action="pause" data-batch-id="${item.id}" ${item.status!=='running'||batchBusy.has(item.id)?'disabled':''}>暂停</button><button class="btn" data-batch-action="stop" data-batch-id="${item.id}" ${item.status!=='running'||batchBusy.has(item.id)?'disabled':''}>停止</button>`}</td></tr>`))}`;
 document.getElementById('batch-create').onclick=dyGuard(batchCreateModal);
 document.getElementById('batch-refresh').onclick=dyGuard(renderBatchCollect);
 document.getElementById('batch-xhs-archive').onclick=dyGuard(hubArchiveModal);
 document.getElementById('batch-dy-archive').onclick=dyGuard(batchDouyinArchive);
 document.querySelectorAll('[data-batch-action]').forEach(button=>button.onclick=dyGuard(async()=>{
  const id=Number(button.dataset.batchId);if(batchBusy.has(id))return;
  batchBusy.add(id);button.disabled=true;
  try{await batchCall('batch_action',id,button.dataset.batchAction);if(curSection==='batch_collect')await renderBatchCollect();}
  finally{batchBusy.delete(id);if(button.isConnected)button.disabled=false;}
 }));
 document.querySelectorAll('[data-batch-export]').forEach(button=>button.onclick=dyGuard(async()=>{
  const result=await batchCall('batch_export',Number(button.dataset.batchExport));download(`批次-${button.dataset.batchExport}-评论.csv`,result.csv);toast(`导出 ${result.count} 条评论`);
 }));
 document.querySelectorAll('[data-batch-details]').forEach(button=>button.onclick=()=>{
  const item=batchItems.find(row=>row.id===Number(button.dataset.batchDetails));
  showInfo('批次来源明细',item.sources.map(source=>`${source.platform} · ${source.kind} · ${source.status}\n${source.canonical}\n${source.error||source.result.warning||''}${source.result.read!==undefined?'\n实际读取 '+source.result.read+' 条':''}${source.result.truncated?'\n达到读取上限，属于部分结果':''}`).join('\n\n'));
 });
 batchTimer=setTimeout(()=>{if(curSection==='batch_collect')batchRenderView();},5000);
}
async function batchCreateModal(){
 if(workspaceModalOpen())throw new Error('请先关闭当前弹窗');
 const section=curSection;
 const [hub,keywords]=await Promise.all([batchCall('hub_accounts'),batchCall('dy_keywords')]);
 if(curSection!==section||workspaceModalOpen())return;
 const options=platform=>hub.items.filter(a=>a.platform===platform).map(a=>`<option value="${a.id}">${esc(a.nickname)}${a.running?' · 已打开':' · 请先打开并登录'}</option>`).join('');
 const data=await workspaceModal('批量链接 / 对标主页采集',`
  <label>抖音登录账号<select name="douyin"><option value="">此批次不含抖音</option>${options('douyin')}</select></label>
  <label>小红书登录账号<select name="xiaohongshu"><option value="">此批次不含小红书</option>${options('xiaohongshu')}</select></label>
  <label>分享文案 / 表格链接列<textarea name="text" id="batch-import-text" placeholder="整段分享文案、多行主页链接，或复制表格中的链接列"></textarea></label>
  <label>或导入 CSV / TXT<input type="file" id="batch-import-file" accept=".csv,.txt"></label><div class="hint" id="batch-file-status"></div>
  <div class="form-grid"><div><label>每个主页作品数（1–50）</label><input name="works" type="number" min="1" max="50" value="20"></div><div><label>每篇评论上限（1–1000）</label><input name="comments" type="number" min="1" max="1000" value="1000"></div></div>
  <label>需求标记关键词（每行一个，可留空）<textarea name="words">${esc(keywords.items.join('\n'))}</textarea></label>
  <div class="hint">所有读到的评论都会归档。主页作品按展示顺序读取并跳过能识别的置顶；发布时间仍需核对。创建后不会自动执行。</div>`,'识别并创建');
 if(!data)return;
 const preview=await batchCall('batch_preview',data.text);
 const accounts={};for(const platform of ['douyin','xiaohongshu'])if(data[platform])accounts[platform]=Number(data[platform]);
 const result=await batchCall('batch_create',data.text,accounts,data.words.split('\n'),Number(data.works),Number(data.comments));
 toast(`批次 #${result.id} 已创建：${preview.items.length} 个来源，去重 ${preview.duplicates} 个${preview.rejected?'，忽略 '+preview.rejected+' 个不支持的链接':''}`);
 if(curSection==='batch_collect')await renderBatchCollect();
}
document.addEventListener('change',async event=>{
 if(event.target.id==='batch-import-file')await workspaceReadImportFile(event.target,'batch-import-text','batch-file-status');
});
async function batchRenderView(){
 const request=batchRequest+1;
 try{await renderBatchCollect();}
 catch(error){
  if(curSection!=='batch_collect'||request!==batchRequest)return;
  document.getElementById('page').innerHTML=`<div class="hint">批次加载失败：${esc(error.message)}</div><button class="btn" id="batch-retry">重试</button>`;
  document.getElementById('batch-retry').onclick=batchRenderView;
 }
}
RENDER.batch_collect=batchRenderView;

async function batchDouyinArchive(){
 const section=curSection;
 const response=await batchCall('dy_comments',null,'',false);
 if(curSection!==section)return;
 const rows=response.items||[];
 const closed=workspaceModal(`抖音评论归档（${rows.length}条）`,`<div class="toolbar"><button class="btn" id="batch-dy-export">导出 CSV</button></div>${dyTable(['用户','评论','来源','归档时间'],rows.map(row=>`<tr><td>${esc(row.nickname||'未提供昵称')}<br><small>${esc(row.user_id||'')}</small></td><td style="white-space:pre-wrap">${esc(row.content)}</td><td>${esc(row.source_id)}</td><td>${esc(row.created_at)}</td></tr>`))}`,'关闭');
 document.getElementById('batch-dy-export').onclick=dyGuard(async()=>{const result=await batchCall('dy_export','comments',null,'',false);download('抖音评论归档.csv',result.csv);});
 await closed;
}
