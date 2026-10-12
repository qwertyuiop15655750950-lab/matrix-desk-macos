/* 抖音工作台：真实后端、授权采集与本地归档 */
TITLES.douyin='抖音矩阵工作台';
let dyTab='accounts', dyAccount='', dyQuery='', dyMatched=false, dyStatus='';
let dyRenderRequest=0, dyScanProgress='';
const dyStatuses={new:'待跟进',contacted:'已联系',won:'已成交',ignored:'已忽略'};
async function dyCall(name,...args){ const r=await api(name,...args); if(!r||!r.ok)throw new Error((r&&r.error)||'操作失败'); return r; }
function dyGuard(fn){return async()=>{try{await fn();}catch(e){toast(e.message,true);}};}
function workspaceBrowserBusy(){return !!window.matrixWorkspaceBrowserTask;}
function workspaceEngine(value,defaultEngine='plugin',comments=false){
 const engine=value||defaultEngine;if(!(comments?['plugin','builtin','bb-browser','crawl4ai']:['plugin','builtin','bb-browser']).includes(engine))throw new Error('请选择支持的浏览器通道');return engine;
}
function workspaceEngineOptions(selected='plugin',comments=false){
 const options=comments?[['builtin','自有评论采集器'],['plugin','工作台原插件']]:[['plugin','工作台浏览器'],['bb-browser','旧素材通道（冻结功能）']];
 return options.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${esc(label)}</option>`).join('');
}
function workspaceEngineName(engine){return engine==='builtin'?'自有评论采集器':engine==='plugin'?'工作台浏览器':engine==='bb-browser'?'bb-browser':engine==='crawl4ai'?'bb-browser + Crawl4AI 评论爬虫':String(engine||'');}
function workspaceCollectionNotes(result){
 return (result.truncated?'\n已达到本次读取上限，保存的是部分评论。':'')+(result.warning?'\n'+String(result.warning):'');
}
function workspaceRefreshBrowserControls(){
 if(curSection==='account_hub'&&typeof hubDraw==='function')hubDraw();
 if(curSection==='douyin'||curSection==='collect'||curSection==='account_hub'){
  document.querySelectorAll('[data-browser]').forEach(button=>button.disabled=workspaceBrowserBusy());
  document.querySelectorAll('[data-material-account]').forEach(button=>button.disabled=workspaceBrowserBusy());
  const scan=document.getElementById('dy-browser-scan');if(scan)scan.disabled=workspaceBrowserBusy();
  const progress=document.getElementById('dy-scan-progress');if(progress)progress.textContent=dyScanProgress;
 }
 if(typeof materialCaptureProgress!=='undefined'){
  const progress=document.getElementById('material-capture-progress');if(progress)progress.textContent=materialCaptureProgress;
 }
}
function workspaceModalOpen(){return !!window.matrixWorkspaceModalBusy||!!document.querySelector('#modal-root .modal-mask');}
async function workspaceModal(title,body,label){
 if(workspaceModalOpen())throw new Error('请先关闭当前弹窗');
 window.matrixWorkspaceModalBusy=true;
 try{return await openModal(title,body,label);}finally{window.matrixWorkspaceModalBusy=false;}
}
function workspaceResult(title,message,section){
 if(curSection===section&&!workspaceModalOpen())showInfo(title,message);
 else toast(message.split('\n')[0]);
}
function workspaceContentURL(value,platform){
 const domain=platform==='xiaohongshu'?'xiaohongshu.com':platform==='douyin'?'douyin.com':'';
 const domains=platform==='xiaohongshu'?['xiaohongshu.com','rednote.com']:domain?[domain]:[];
 let parsed;try{parsed=new URL(String(value||'').trim());}catch(_){throw new Error('请填写完整的 HTTPS 内容链接');}
 if(!domain||parsed.protocol!=='https:'||!domains.flatMap(value=>[value,`www.${value}`]).includes(parsed.hostname)||parsed.username||parsed.password||parsed.port)throw new Error(`请使用所选平台的 HTTPS 完整内容链接（${domains.join(' / ')}）`);
 return parsed.href;
}
async function workspaceReadImportFile(input,textareaId,statusId){
 const textarea=document.getElementById(textareaId),file=input.files&&input.files[0];
 if(!textarea||!file)return;
 const request=(textarea.matrixFileReadRequest||0)+1;textarea.matrixFileReadRequest=request;
 const status=statusId?document.getElementById(statusId):null,ok=document.querySelector('#modal-root [data-x="ok"]');
 const current=()=>textarea.isConnected&&textarea.matrixFileReadRequest===request;
 textarea.value='';
 if(file.size>10*1024*1024){textarea.readOnly=false;if(ok)ok.disabled=false;if(status)status.textContent='文件最大 10 MB，请重新选择或粘贴内容';toast('文件最大 10 MB',true);return;}
 textarea.readOnly=true;if(ok)ok.disabled=true;if(status)status.textContent='正在读取文件…';
 try{const content=await file.text();if(current()){textarea.value=content;if(status&&status.isConnected)status.textContent=`已读取：${file.name}`;}}
 catch(e){if(current()){toast(`文件读取失败：${e.message}`,true);if(status&&status.isConnected)status.textContent='文件读取失败，请重新选择或粘贴内容';}}
 finally{if(current()){textarea.readOnly=false;if(ok&&ok.isConnected)ok.disabled=false;}}
}
function dyWords(value){try{const words=Array.isArray(value)?value:JSON.parse(value||'[]');return Array.isArray(words)?words:[];}catch(_){return [];}}
function dyTable(headers,rows){return `<div class="tbl-wrap"><table><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" style="padding:30px;text-align:center">暂无数据</td></tr>`}</tbody></table></div>`;}
function dyOptions(accounts){return accounts.map(a=>`<option value="${a.id}" ${String(a.id)===String(dyAccount)?'selected':''}>${esc(a.nickname)}</option>`).join('');}
async function dyAccountModal(a={}){
 const d=await workspaceModal(a.id?'编辑抖音账号':'新增抖音账号',`<label>账号名称<input type="text" name="nickname" value="${esc(a.nickname||'')}" required></label><label>分组<input type="text" name="group_name" value="${esc(a.group_name||'')}"></label><label>备注<textarea name="notes">${esc(a.notes||'')}</textarea></label>`);
 if(d){if(!d.nickname.trim())throw new Error('请填写账号名称');await dyCall('dy_save_account',{...d,nickname:d.nickname.trim(),...(a.id?{id:a.id}:{})});toast('账号档案已保存');try{await renderDouyin();}catch(e){toast(`账号已保存，页面刷新失败：${e.message}`,true);}}
}
async function renderDouyin(){
 const section=curSection,tab=dyTab,request=++dyRenderRequest;
 const embedded=section==='account_hub',focus=embedded?hubFocus:'';
 if(section!=='douyin'&&section!=='collect'&&!embedded)return;
 if(embedded&&(!['comments','leads'].includes(hubView)||hubView!==tab))return;
 const current=()=>request===dyRenderRequest&&curSection===section&&dyTab===tab&&(!embedded||(hubFocus===focus&&hubView===tab));
 const accounts=(await dyCall('dy_accounts')).items||[];
 if(!current())return;
 if(dyAccount&&!accounts.some(a=>String(a.id)===String(dyAccount)))dyAccount='';
 let comments,kw,result;
 if(tab==='comments'){
  [comments,kw]=await Promise.all([dyCall('dy_comments',dyAccount||null,dyQuery,dyMatched),dyCall('dy_keywords')]);
 }else if(tab==='leads'){result=await dyCall('dy_leads',dyStatus,dyAccount||null);}
 if(!current())return;
 const mount=embedded?document.getElementById('hub-pane'):document.getElementById('page');
 if(!mount)return;
 mount.innerHTML=`${embedded?'':`<div class="toolbar">${[['accounts','账号管理'],['comments','评论采集'],['leads','线索跟进']].map(([id,label])=>`<button class="btn ${dyTab===id?'primary':''}" data-tab="${id}">${label}</button>`).join('')}</div>`}<div id="dy-content"></div>`;
 document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=dyGuard(async()=>{dyTab=b.dataset.tab;await renderDouyin();}));
 const container=document.getElementById('dy-content');
 if(tab==='accounts'){
  container.innerHTML=`<div class="toolbar"><button class="btn primary" id="dy-add">新增抖音账号</button><span class="hint">${accounts.length} 个账号 · 每个账号使用独立浏览器登录目录</span></div>`+dyTable(['账号','分组','备注','操作'],accounts.map(a=>`<tr><td>${esc(a.nickname)}</td><td>${esc(a.group_name)}</td><td>${esc(a.notes)}</td><td><button class="btn" data-edit="${a.id}">编辑</button> <button class="btn" data-browser="${a.id}">打开浏览器 / 扫码登录</button> </td></tr>`));
  document.getElementById('dy-add').onclick=dyGuard(()=>dyAccountModal());
  document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=dyGuard(()=>dyAccountModal(accounts.find(a=>a.id===Number(b.dataset.edit)))));
  document.querySelectorAll('[data-material-account]').forEach(b=>{b.disabled=workspaceBrowserBusy();b.onclick=dyGuard(()=>materialCaptureModal({...accounts.find(a=>a.id===Number(b.dataset.materialAccount)),platform:'douyin'}));});
  document.querySelectorAll('[data-browser]').forEach(b=>{b.disabled=workspaceBrowserBusy();b.onclick=dyGuard(async()=>{
   if(workspaceBrowserBusy())throw new Error('已有浏览器任务正在执行，请等待完成');
   const task={type:'open'};window.matrixWorkspaceBrowserTask=task;b.disabled=true;b.textContent='正在打开…';
   try{await dyCall('dy_browser',Number(b.dataset.browser));toast('浏览器已打开，请自行扫码登录');}
   finally{if(window.matrixWorkspaceBrowserTask===task)window.matrixWorkspaceBrowserTask=null;if(b.isConnected){b.disabled=false;b.textContent='打开浏览器 / 扫码登录';}workspaceRefreshBrowserControls();}
  });});
 }else if(tab==='comments'){
  container.innerHTML=`<div class="hint">使用账号的独立浏览器扫码登录，再采集指定视频的评论。采集选择器尚未真机校准，结果以实际页面为准；直播评论目前支持文件导入。</div><div class="toolbar"><button class="btn primary" id="dy-import">导入 CSV / JSON</button><button class="btn" id="dy-browser-scan">采集指定视频评论</button><button class="btn" id="dy-kw">设置关键词</button><button class="btn" id="dy-refilter">重新筛选已有评论</button><button class="btn" id="dy-export">导出当前结果</button></div><div class="toolbar"><select id="dy-account" ${embedded?'hidden':''}><option value="">全部账号</option>${dyOptions(accounts)}</select><input type="text" id="dy-query" placeholder="搜索评论、昵称或用户ID" value="${esc(dyQuery)}"><label><input type="checkbox" id="dy-matched" ${dyMatched?'checked':''}>仅关键词命中</label><button class="btn" id="dy-filter">筛选</button></div><div class="hint" id="dy-scan-progress" role="status">${esc(dyScanProgress)}</div><div class="hint">关键词：${esc(kw.items.join('、'))} · ${comments.items.length} 条评论 · 无用户ID的评论保留，但不创建用户线索</div>`+dyTable(['账号 / 来源','用户','评论','命中词','归档时间'],comments.items.map(c=>`<tr><td>${esc(c.account_name)}<br><small>${esc(c.source_type)} · ${esc(c.source_id)}</small></td><td>${esc(c.nickname||'未提供昵称')}<br><small>${esc(c.user_id||'未提供ID')}</small></td><td style="max-width:400px;white-space:pre-wrap">${esc(c.content)}<div class="hint">${esc(c.review_hint||'')}</div></td><td>${esc(dyWords(c.matched_words).join('、'))}</td><td>${esc(c.created_at)}</td></tr>`));
  document.getElementById('dy-account').value=dyAccount;
  document.getElementById('dy-filter').onclick=dyGuard(async()=>{dyAccount=document.getElementById('dy-account').value;dyQuery=document.getElementById('dy-query').value;dyMatched=document.getElementById('dy-matched').checked;await renderDouyin();});
  document.getElementById('dy-kw').onclick=dyGuard(async()=>{
   const presets=kw.presets||[];
   const pending=workspaceModal('关键词设置',`<label>美业预设<select id="dy-keyword-preset"><option value="">保留当前关键词</option>${presets.map((item,index)=>`<option value="${index}">${esc(item.name)}</option>`).join('')}</select></label><div class="hint">选择预设只改此处草稿，点击保存后才生效；可按门店业务增删。请先选择美业相关视频，再读取评论区。</div><label>每行一个关键词<textarea name="words">${esc(kw.items.join('\n'))}</textarea></label>`,'保存关键词');
   const root=document.getElementById('modal-root'),select=root.querySelector('#dy-keyword-preset'),words=root.querySelector('[name="words"]');
   select.onchange=()=>{if(select.value!==''&&presets[Number(select.value)])words.value=presets[Number(select.value)].words.join('\n');};
   const d=await pending;if(d){await dyCall('dy_keywords',d.words.split('\n'));await renderDouyin();}
  });
  document.getElementById('dy-refilter').onclick=dyGuard(async()=>{const r=await dyCall('dy_refilter');toast(`已重新筛选，${r.matched} 条命中；原有跟进记录保留`);await renderDouyin();});
  document.getElementById('dy-import').onclick=dyGuard(async()=>{
   if(!accounts.length)throw new Error('请先新增抖音账号');
   const d=await workspaceModal('导入评论',`<label>归属账号<select name="aid">${dyOptions(accounts)}</select></label><label>来源类型<select name="kind"><option value="video">视频</option><option value="live">直播</option><option value="manual">手工</option></select></label><label>来源ID或标记<input type="text" name="source" placeholder="视频ID、直播场次或文件来源"></label><label>选择文件<input type="file" id="dy-file" accept=".csv,.json"></label><div class="hint" id="dy-file-status" role="status"></div><label>或粘贴内容<textarea name="text" id="dy-import-text" placeholder='CSV列：comment_id,user_id,nickname,content；JSON使用同样字段'></textarea></label>`);
   if(d){if(!d.text.trim())throw new Error('请选择文件或粘贴导入内容');const r=await dyCall('dy_import',Number(d.aid),d.source,d.kind,d.text);toast(`新增 ${r.added} 条，重复 ${r.duplicates} 条，命中 ${r.matched} 条`);await renderDouyin();}
  });
  document.getElementById('dy-browser-scan').disabled=workspaceBrowserBusy();
  document.getElementById('dy-browser-scan').onclick=dyGuard(async()=>{
   if(workspaceBrowserBusy())throw new Error('已有浏览器任务正在执行，请等待完成');
   if(!accounts.length)throw new Error('请先新增抖音账号');
   if(!kw.items.length)throw new Error('请先设置至少一个采集关键词');
   const d=await workspaceModal('采集指定视频评论',`<div class="hint">使用选定账号的独立浏览器，只读筛选已加载评论。请先扫码登录。自有采集器直接读取该账号页面，不需要安装外部采集项目；原插件通道保留。页面面板可暂停、继续或停止；页面结构尚未真机验证，不能保证获取全部评论。外部工具缺失时，会改用同账号的工作台浏览器插件，并在结果中注明。</div><label>账号<select name="aid">${dyOptions(accounts)}</select></label><label>采集通道<select name="engine">${workspaceEngineOptions('builtin',true)}</select></label><label>完整视频地址<input type="url" name="url" placeholder="https://www.douyin.com/video/…"></label>`,'开始采集');
   if(!d)return;
   if(workspaceBrowserBusy())throw new Error('已有浏览器任务正在执行，请等待完成');
   const url=workspaceContentURL(d.url,'douyin'),engine=workspaceEngine(d.engine,'plugin',true),task={type:'collect'};window.matrixWorkspaceBrowserTask=task;
   const button=document.getElementById('dy-browser-scan');if(button)button.disabled=true;
   dyScanProgress=`正在通过${workspaceEngineName(engine)}采集，可在浏览器面板暂停或停止。`;workspaceRefreshBrowserControls();toast(dyScanProgress);
   let r;
   try{r=await dyCall('dy_scan_video',Number(d.aid),url,engine);dyScanProgress=`${r.timed_out?'采集超时停止，已保存当前结果。':r.stopped?'已停止，保留当前结果。':'采集完成。'}已读取 ${r.read??r.fetched} 条评论，其中 ${r.fetched} 条关键词命中，新增 ${r.added} 条，重复 ${r.duplicates} 条。`;}
   catch(e){dyScanProgress=`采集未完成：${e.message}`;throw e;}
   finally{if(window.matrixWorkspaceBrowserTask===task)window.matrixWorkspaceBrowserTask=null;if(button&&button.isConnected)button.disabled=false;workspaceRefreshBrowserControls();}
   workspaceResult('采集结果',`${dyScanProgress}${r.fetched?'':'未命中也可能是页面未加载、登录失效或选择器变化，请检查浏览器。'}${r.engine?'\n采集通道：'+workspaceEngineName(r.engine):''}${workspaceCollectionNotes(r)}`,section);
   try{await renderDouyin();}catch(e){toast(`采集结果已保存，页面刷新失败：${e.message}`,true);}
  });
  document.getElementById('dy-export').onclick=dyGuard(async()=>{const r=await dyCall('dy_export','comments',dyAccount||null,dyQuery,dyMatched);download('抖音评论.csv',r.csv);toast(`导出 ${r.count} 条`);});
 }else{
  container.innerHTML=`<div class="toolbar"><select id="dy-status"><option value="">全部状态</option>${Object.entries(dyStatuses).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select><button class="btn" id="dy-lead-export">导出线索</button><span class="hint">${result.items.length} 条线索 · 按账号与用户ID去重</span></div>`+dyTable(['归属账号','用户','评论数','状态','跟进备注','操作'],result.items.map(l=>`<tr><td>${esc(l.account_name)}</td><td>${esc(l.nickname||'未提供昵称')}<br><small>${esc(l.user_id)}</small></td><td>${l.comment_count}</td><td>${esc(dyStatuses[l.status])}</td><td style="white-space:pre-wrap">${esc(l.notes)}</td><td><button class="btn" data-lead="${l.id}">跟进</button><button class="btn" data-copy="${l.id}">复制用户ID</button><button class="btn" data-comments="${l.id}">查看评论</button></td></tr>`));
  document.getElementById('dy-status').value=dyStatus;
  document.getElementById('dy-status').onchange=dyGuard(async()=>{dyStatus=document.getElementById('dy-status').value;await renderDouyin();});
  document.getElementById('dy-lead-export').onclick=dyGuard(async()=>{const r=await dyCall('dy_export','leads',dyAccount||null,'',false,dyStatus);download('抖音线索.csv',r.csv);});
  document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>copyText(result.items.find(l=>l.id===Number(b.dataset.copy)).user_id));
  document.querySelectorAll('[data-comments]').forEach(b=>b.onclick=dyGuard(async()=>{const l=result.items.find(l=>l.id===Number(b.dataset.comments));dyTab='comments';dyAccount=String(l.account_id);dyQuery=l.user_id;dyMatched=false;if(embedded){hubFocus=`douyin:${l.account_id}`;hubView='comments';await hubDraw();}else await renderDouyin();}));
  document.querySelectorAll('[data-lead]').forEach(b=>b.onclick=dyGuard(async()=>{const l=result.items.find(l=>l.id===Number(b.dataset.lead));const d=await workspaceModal('线索跟进',`<label>状态<select name="status">${Object.entries(dyStatuses).map(([k,v])=>`<option value="${k}" ${k===l.status?'selected':''}>${v}</option>`).join('')}</select></label><label>备注<textarea name="notes">${esc(l.notes)}</textarea></label>`);if(d){await dyCall('dy_update_lead',l.id,d.status,d.notes);await renderDouyin();}}));
 }
}
async function dyRenderView(){
 const section=curSection,request=dyRenderRequest+1,focus=section==='account_hub'?hubFocus:'',view=dyTab;
 try{await renderDouyin();}
 catch(e){if(request!==dyRenderRequest)return;if(curSection===section&&(section==='douyin'||section==='collect'||(section==='account_hub'&&hubFocus===focus&&hubView===view))){const mount=section==='account_hub'?document.getElementById('hub-pane'):document.getElementById('page');if(!mount)return;mount.innerHTML=`<div class="hint">页面加载失败：${esc(e.message)}</div><button class="btn" id="dy-retry">重新加载</button>`;document.getElementById('dy-retry').onclick=dyRenderView;}toast(e.message,true);}
}
RENDER.douyin=dyRenderView;
document.addEventListener('change',async e=>{if(e.target.id==='dy-file')await workspaceReadImportFile(e.target,'dy-import-text','dy-file-status');});
// 源码中的关键词采集入口复用完整的评论工作台。
RENDER.collect=async()=>{dyTab='comments';await dyRenderView();};
