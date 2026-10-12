/* One manual surface over existing browsers; frames are ephemeral and never replay input. */
let browserDeskEpoch=0,browserDeskTimer=null,browserDeskSelection=null,browserDeskFrame=null,browserDeskBusy=false,browserDeskActive=false,browserDeskPage=0,browserDeskReadEpoch=null;
const browserDeskCache=new Map();
function browserDeskStop(){++browserDeskEpoch;clearTimeout(browserDeskTimer);browserDeskTimer=null;browserDeskActive=false;browserDeskFrame=null;browserDeskCache.clear();}
function browserDeskCurrent(epoch,selection=null){return browserDeskActive&&curSection==='account_hub'&&epoch===browserDeskEpoch&&(!selection||browserDeskSelection?.key===selection.key&&browserDeskSelection?.token===selection.token);}
function browserDeskStatus(text,error=false){const el=document.getElementById('bd-status');if(el){el.textContent=text;el.classList.toggle('error',error);}}
function browserDeskAccounts(){return hubVisibleItems();}
async function browserDeskOpen(){
 browserDeskStop();browserDeskActive=true;browserDeskSelection=null;browserDeskPage=0;browserDeskDraw();await browserDeskRefresh();
}
function browserDeskDraw(){
 const accounts=browserDeskAccounts(),selected=browserDeskSelection;clearTimeout(browserDeskTimer);browserDeskFrame=null;
 document.getElementById('page').innerHTML=`<div class="toolbar"><button class="btn" id="bd-back">返回账号管理</button><button class="btn ${selected?'':'primary'}" id="bd-overview">多账号总览</button><button class="btn" id="bd-refresh">刷新画面</button><span class="hint">${accounts.filter(a=>a.running).length} 个浏览器已打开</span></div>
 <div class="browser-desk ${selected?'':'overview'}"><aside class="browser-rail"><h3>账号</h3>${accounts.map(a=>`<button class="browser-account ${selected?.key===hubKey(a)?'active':''}" data-bd-account="${esc(hubKey(a))}"><span class="platform-dot ${esc(a.platform)}">${esc(HUB_PLATFORMS[a.platform]?.slice(0,1)||'·')}</span><span><b>${esc(a.nickname)}</b><small>${esc(HUB_PLATFORMS[a.platform])} · ${a.running?'已打开':'未打开'}</small></span></button>`).join('')||'<p class="hint">当前筛选无账号</p>'}</aside>
 <section class="browser-surface"><div id="bd-status" class="hint" role="status">点击账号选择页面；仅操作你当前选中的账号。</div>${selected?`<div class="toolbar browser-tabs" id="bd-tabs"></div><div class="toolbar"><b id="bd-title"></b><button class="btn" id="bd-native">显示原浏览器</button><button class="btn" id="bd-expand">放大查看</button></div><div class="browser-frame-wrap"><img id="bd-frame" alt="当前账号页面，点击操作，滚轮滚动" tabindex="0" draggable="false"></div><div class="toolbar browser-input"><input id="bd-text" aria-label="向页面输入文字" placeholder="先点击页面输入框，在这里输入文字" maxlength="2000"><button class="btn" id="bd-insert">输入到页面</button><button class="btn" data-bd-key="Enter">回车</button><button class="btn" data-bd-key="Backspace">退格</button><button class="btn" data-bd-key="Escape">Esc</button></div>`:'<div id="bd-grid" class="browser-grid"></div><div class="toolbar"><button class="btn" id="bd-prev">上一组</button><span id="bd-page" class="hint"></span><button class="btn" id="bd-next">下一组</button></div>'}
 <p class="hint browser-footnote">画面定时刷新；仅当前账号持续刷新。文件上传、扫码及复杂编辑可点击「显示原浏览器」。登录状态沿用现有目录。</p></section></div>`;
 document.getElementById('bd-back').onclick=()=>{browserDeskStop();hubDraw();};
 document.getElementById('bd-overview').onclick=()=>{++browserDeskEpoch;browserDeskSelection=null;browserDeskDraw();browserDeskRefresh();};
 document.getElementById('bd-refresh').onclick=()=>browserDeskRefresh();
 document.querySelectorAll('[data-bd-account]').forEach(el=>el.onclick=()=>browserDeskChooseAccount(el.dataset.bdAccount));
 if(!selected){
  const count=Math.max(1,Math.ceil(accounts.length/4));browserDeskPage=Math.min(browserDeskPage,count-1);
  document.getElementById('bd-page').textContent=`${browserDeskPage+1} / ${count} · 每组 4 个账号`;
  document.getElementById('bd-prev').disabled=browserDeskPage===0;document.getElementById('bd-next').disabled=browserDeskPage>=count-1;
  for(const [id,delta] of [['bd-prev',-1],['bd-next',1]])document.getElementById(id).onclick=()=>{++browserDeskEpoch;browserDeskPage+=delta;browserDeskDraw();browserDeskRefresh();};
  browserDeskRenderGrid();return;
 }
 browserDeskRenderTabs();
 document.getElementById('bd-native').onclick=hubGuard(async()=>{const a=hubItems.find(a=>hubKey(a)===selected.key);await hubCall('hub_browser_focus',a.platform,a.id,selected.token,true);});
 document.getElementById('bd-expand').onclick=()=>{const desk=document.querySelector('.browser-desk');const expanded=desk.classList.toggle('expanded');document.getElementById('bd-expand').textContent=expanded?'退出放大':'放大查看';};
 const image=document.getElementById('bd-frame');
 const point=event=>{const r=image.getBoundingClientRect();return {x:Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))};};
 image.onclick=event=>{image.focus();browserDeskInput('click',point(event));};
 image.onwheel=event=>{event.preventDefault();browserDeskInput('scroll',{...point(event),delta:Math.max(-900,Math.min(900,event.deltaY*(event.deltaMode===1?20:1)))});};
 image.onkeydown=event=>{if(['Enter','Backspace','Delete','Tab','Escape','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(event.key)){event.preventDefault();browserDeskInput('key',{key:event.key});}};
 document.querySelectorAll('[data-bd-key]').forEach(el=>el.onclick=()=>browserDeskInput('key',{key:el.dataset.bdKey}));
 document.getElementById('bd-insert').onclick=async()=>{const input=document.getElementById('bd-text');const text=input.value;if(!text)return;const sent=await browserDeskInput('text',{text});if(sent&&input.isConnected)input.value='';};
}
function browserDeskRenderTabs(){const box=document.getElementById('bd-tabs');if(!box)return;const selection=browserDeskSelection;box.innerHTML=(browserDeskCache.get(selection.key)?.tabs||browserDeskCache.get(selection.key)?.items||[]).map(p=>`<button class="btn ${p.token===selection.token?'primary':''}" data-bd-tab="${esc(p.token)}">${esc(p.title||'页面')}</button>`).join('');box.querySelectorAll('[data-bd-tab]').forEach(el=>el.onclick=()=>browserDeskSelect(selection.key,el.dataset.bdTab));}
function browserDeskRenderGrid(){
 const box=document.getElementById('bd-grid');if(!box)return;
 box.innerHTML=browserDeskAccounts().slice(browserDeskPage*4,browserDeskPage*4+4).map(a=>{const cache=browserDeskCache.get(hubKey(a));return `<article class="browser-preview"><header><b>${esc(a.nickname)}</b><span class="hint">${esc(HUB_PLATFORMS[a.platform])}</span></header>${cache?.items?.length?cache.items.map(p=>`<button class="browser-thumbnail" data-bd-preview="${esc(p.token)}" data-bd-owner="${esc(hubKey(a))}">${p.image?`<img alt="${esc(a.nickname)}的页面预览" src="${p.image}">`:'<span>页面未就绪</span>'}<small>${esc(p.title||'页面')}</small></button>`).join(''):`<div class="browser-placeholder">${esc(cache?.error||(a.running?'正在读取画面…':'浏览器未打开'))}${!a.running?`<button class="btn" data-bd-open="${esc(hubKey(a))}">打开此账号</button>`:''}</div>`}${cache?.more?`<small class="hint">还有 ${cache.more} 个页面，点击账号后切换页面查看</small>`:''}</article>`;}).join('')||'<p class="hint">暂无账号</p>';
 box.querySelectorAll('[data-bd-preview]').forEach(el=>el.onclick=()=>browserDeskSelect(el.dataset.bdOwner,el.dataset.bdPreview));
 box.querySelectorAll('[data-bd-open]').forEach(el=>el.onclick=()=>browserDeskOpenAccount(el.dataset.bdOpen));
}
async function browserDeskOpenAccount(key){const a=hubItems.find(a=>hubKey(a)===key);if(!a)return;browserDeskStatus('正在打开，请完成扫码登录。');try{await hubCall('hub_open',a.platform,a.id);const r=await hubCall('hub_accounts','');hubItems=r.items||[];if(browserDeskActive){browserDeskDraw();await browserDeskRefresh();}}catch(e){browserDeskStatus(e.message,true);}}
async function browserDeskChooseAccount(key){const a=hubItems.find(a=>hubKey(a)===key);if(!a)return;if(!a.running){browserDeskStatus('该账号未打开，请在总览中点击「打开此账号」。');return;}const epoch=++browserDeskEpoch;browserDeskSelection=null;clearTimeout(browserDeskTimer);try{const r=await hubCall('hub_browser_previews',a.platform,a.id);if(!browserDeskCurrent(epoch))return;browserDeskCache.set(key,r);if(!r.items.length)return browserDeskStatus('浏览器没有可查看的页面。',true);await browserDeskSelect(key,r.items[0].token);}catch(e){if(browserDeskCurrent(epoch))browserDeskStatus(e.message,true);}}
async function browserDeskSelect(key,token){++browserDeskEpoch;browserDeskSelection={key,token};browserDeskDraw();await browserDeskRefresh();}
async function browserDeskRefresh(){
 clearTimeout(browserDeskTimer);const epoch=browserDeskEpoch,selected=browserDeskSelection;if(!browserDeskCurrent(epoch)||browserDeskBusy||browserDeskReadEpoch===epoch)return;
 browserDeskReadEpoch=epoch;
 try{
  if(selected){const a=hubItems.find(a=>hubKey(a)===selected.key);const r=await hubCall('hub_browser_previews',a.platform,a.id,selected.token);if(!browserDeskCurrent(epoch,selected))return;const p=r.items[0];if(!p?.image||!p.frame)throw Error(p?.error||'页面已关闭，请返回总览');const image=document.getElementById('bd-frame');browserDeskFrame=null;image.onload=()=>{if(browserDeskCurrent(epoch,selected))browserDeskFrame=p;};image.src=p.image;document.getElementById('bd-title').textContent=p.title;browserDeskStatus(`当前账号：${a.nickname} · ${new Date().toLocaleTimeString()}`);
  }else{
   browserDeskStatus('正在读取已打开的浏览器…');
   // At most four accounts, serially; no background browser launches.
   for(const a of browserDeskAccounts().slice(browserDeskPage*4,browserDeskPage*4+4)){
    if(!browserDeskCurrent(epoch))return;if(!a.running)continue;
    try{const r=await hubCall('hub_browser_previews',a.platform,a.id);if(!browserDeskCurrent(epoch))return;browserDeskCache.set(hubKey(a),r);}catch(e){if(!browserDeskCurrent(epoch))return;browserDeskCache.set(hubKey(a),{error:e.message});}browserDeskRenderGrid();
   }browserDeskStatus('点击任意预览进入该账号。总览按需刷新。');
  }
 }catch(e){if(browserDeskCurrent(epoch,selected)){browserDeskFrame=null;const image=document.getElementById('bd-frame');if(image)image.removeAttribute('src');browserDeskStatus(e.message,true);}}
 finally{if(browserDeskReadEpoch===epoch)browserDeskReadEpoch=null;if(browserDeskCurrent(epoch,selected)&&selected&&!document.hidden)browserDeskTimer=setTimeout(()=>browserDeskRefresh(),2000);}
}
async function browserDeskInput(action,params){
 const selected=browserDeskSelection,frame=browserDeskFrame,epoch=browserDeskEpoch;if(!browserDeskCurrent(epoch,selected)||!selected||!frame||browserDeskBusy)return false;
 if(browserDeskReadEpoch===epoch){browserDeskStatus('画面正在刷新，请稍后点击。');return false;}
 let inputError='';browserDeskBusy=true;browserDeskFrame=null;clearTimeout(browserDeskTimer);const a=hubItems.find(a=>hubKey(a)===selected.key);
 try{await hubCall('hub_browser_input',a.platform,a.id,selected.token,frame.frame,action,params);return true;}
 catch(e){inputError=e.message+'；不会自动重试该操作。';return false;}
 finally{browserDeskBusy=false;if(browserDeskActive&&curSection==='account_hub')await browserDeskRefresh();if(inputError&&browserDeskCurrent(epoch,selected))browserDeskStatus(inputError,true);}
}
document.addEventListener('visibilitychange',()=>{clearTimeout(browserDeskTimer);if(!document.hidden&&browserDeskActive)browserDeskRefresh();});
const browserDeskOriginalShow=showSection;
showSection=async function(id){if(browserDeskActive)browserDeskStop();return browserDeskOriginalShow(id);};
