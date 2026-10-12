/* Real macOS web views occupy this DOM slot; no screenshots or simulated input. */
let nativeBrowserActive=false,nativeBrowserEpoch=Date.now(),nativeBrowserSelected='',nativeBrowserRevision=0,nativeBrowserTimer=null,nativeBrowserObserver=null,nativeBrowserHidePromise=Promise.resolve(),nativeBrowserOverview=false,nativeBrowserOverviewKeys=[];
const nativeBrowserOpened=new Set();
const nativeLegacyOpen=browserDeskOpen,nativeLegacyStop=browserDeskStop;
function nativeBrowserRect(slot=document.getElementById('nb-slot')){
 if(!slot)return null;
 const r=slot.getBoundingClientRect(),bottom=Math.min(r.bottom,innerHeight),right=Math.min(r.right,innerWidth);
 const blocked=['modal-root','prompt-root'].some(id=>document.getElementById(id)?.childElementCount);
 return{x:Math.max(0,r.left),y:Math.max(0,r.top),width:Math.max(1,right-Math.max(0,r.left)),height:Math.max(1,bottom-Math.max(0,r.top)),visible:!document.hidden&&!blocked&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth};
}
function nativeBrowserStatus(text,error=false){const box=document.getElementById('nb-status');if(box){box.textContent=text;box.classList.toggle('error',error);}}
function nativeBrowserLayout(){
 if(!nativeBrowserActive)return;
 if(nativeBrowserOverview){hubCall('native_browser_overview_layout',nativeBrowserEpoch,++nativeBrowserRevision,nativeBrowserOverviewSlots()).catch(error=>nativeBrowserStatus(error.message,true));return;}
 if(!nativeBrowserSelected)return;
 const rect=nativeBrowserRect();if(!rect)return;
 const epoch=nativeBrowserEpoch,revision=++nativeBrowserRevision;
 hubCall('native_browser_layout',epoch,revision,rect).catch(error=>{if(nativeBrowserActive&&epoch===nativeBrowserEpoch)nativeBrowserStatus(error.message,true);});
}
function nativeBrowserDraw(){
 const accounts=hubVisibleItems();
 document.getElementById('page').innerHTML=`<div class="toolbar"><button class="btn" id="nb-back">返回账号管理</button><button class="btn" id="nb-overview">多账号总览</button><span class="hint">点击账号直接打开 · 登录状态分别保存</span></div>
 <div class="browser-desk native-desk"><aside class="browser-rail"><h3>账号</h3>${accounts.map(a=>`<button class="browser-account ${nativeBrowserSelected===hubKey(a)?'active':''}" data-native-account="${esc(hubKey(a))}"><span class="platform-dot ${esc(a.platform)}">${esc(HUB_PLATFORMS[a.platform]?.slice(0,1)||'·')}</span><span><b>${esc(a.nickname)}</b><small>${esc(HUB_PLATFORMS[a.platform])} · ${nativeBrowserOpened.has(hubKey(a))?'已打开':'点击打开'}</small></span></button>`).join('')}</aside>
 <section class="browser-surface native-surface"><div class="toolbar native-navigation"><button class="btn" id="nb-prev" title="后退">←</button><button class="btn" id="nb-next" title="前进">→</button><button class="btn" id="nb-reload">刷新</button><input id="nb-url" type="url" aria-label="当前账号网址" placeholder="平台网址，回车打开"><button class="btn" id="nb-go">打开</button><button class="btn" id="nb-expand">放大</button></div><div class="hint" id="nb-status" role="status">${nativeBrowserSelected?'正在打开所选账号…':'在左侧选择账号，网页会直接在这里打开。'}</div><div id="nb-slot" class="native-browser-slot"><div class="native-empty">${nativeBrowserSelected?'正在载入网页…':'选择一个账号'}</div></div></section></div>`;
 document.getElementById('nb-overview').onclick=hubGuard(nativeBrowserShowOverview);
 document.getElementById('nb-back').onclick=hubGuard(async()=>{browserDeskStop();await nativeBrowserHidePromise;hubDraw();});
 document.querySelectorAll('[data-native-account]').forEach(button=>button.onclick=()=>nativeBrowserChoose(button.dataset.nativeAccount));
 for(const [id,command] of [['nb-prev','back'],['nb-next','forward'],['nb-reload','reload']])document.getElementById(id).onclick=hubGuard(()=>nativeBrowserCommand(command));
 document.getElementById('nb-go').onclick=hubGuard(()=>nativeBrowserCommand('navigate',document.getElementById('nb-url').value.trim()));
 document.getElementById('nb-url').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();document.getElementById('nb-go').click();}};
 document.getElementById('nb-expand').onclick=()=>{const desk=document.querySelector('.native-desk');const expanded=desk.classList.toggle('expanded');document.getElementById('nb-expand').textContent=expanded?'退出放大':'放大';nativeBrowserLayout();};
 nativeBrowserObserver?.disconnect();nativeBrowserObserver=new ResizeObserver(nativeBrowserLayout);nativeBrowserObserver.observe(document.getElementById('nb-slot'));
}
async function nativeBrowserChoose(key){
 const account=hubItems.find(a=>hubKey(a)===key);if(!account||!nativeBrowserActive)return;
 const epoch=++nativeBrowserEpoch;nativeBrowserRevision=0;nativeBrowserOverview=false;nativeBrowserSelected=key;clearTimeout(nativeBrowserTimer);nativeBrowserDraw();
 try{
  const result=await hubCall('native_browser_select',account.platform,account.id,nativeBrowserRect(),epoch);
  if(!nativeBrowserActive||epoch!==nativeBrowserEpoch||result.stale)return;
  document.querySelector('#nb-slot .native-empty').textContent='';nativeBrowserOpened.add(key);document.querySelector(`[data-native-account="${CSS.escape(key)}"] small`).textContent=`${HUB_PLATFORMS[account.platform]} · 已打开`;
  nativeBrowserStatus(`当前账号：${account.nickname} · 可直接在网页中点击和输入`);nativeBrowserLayout();nativeBrowserPoll(epoch);
 }catch(error){if(nativeBrowserActive&&epoch===nativeBrowserEpoch){nativeBrowserStatus(error.message,true);document.querySelector('#nb-slot .native-empty').textContent='页面未打开，请重选账号或刷新。';}}
}
async function nativeBrowserPoll(epoch){
 if(!nativeBrowserActive||epoch!==nativeBrowserEpoch)return;
 try{
  const result=await hubCall('native_browser_status');if(!nativeBrowserActive||epoch!==nativeBrowserEpoch)return;
  nativeBrowserOpened.clear();for(const item of result.items||[])nativeBrowserOpened.add(`${item.platform}:${item.id}`);
  const account=hubItems.find(a=>hubKey(a)===nativeBrowserSelected),item=(result.items||[]).find(i=>i.platform===account?.platform&&i.id===account?.id);
  if(item){const input=document.getElementById('nb-url');if(document.activeElement!==input)input.value=item.url||'';nativeBrowserStatus(item.error||`${account.nickname} · ${item.title||'网页'}`,!!item.error);}
 }catch(error){if(nativeBrowserActive&&epoch===nativeBrowserEpoch)nativeBrowserStatus(error.message,true);}
 if(nativeBrowserActive&&epoch===nativeBrowserEpoch&&!document.hidden)nativeBrowserTimer=setTimeout(()=>nativeBrowserPoll(epoch),800);
}
async function nativeBrowserCommand(command,url=''){
 if(!nativeBrowserActive||!nativeBrowserSelected)throw Error('先在左侧选择账号');
 return hubCall('native_browser_command',nativeBrowserEpoch,command,url);
}
browserDeskStop=function(){
 if(nativeBrowserActive){nativeBrowserActive=false;++nativeBrowserEpoch;nativeBrowserOverview=false;nativeBrowserSelected='';clearTimeout(nativeBrowserTimer);nativeBrowserObserver?.disconnect();nativeBrowserObserver=null;
  nativeBrowserHidePromise=hubCall('native_browser_hide',nativeBrowserEpoch);
  nativeBrowserHidePromise.catch(error=>toast(error.message,true));
 }
 nativeLegacyStop();
};
browserDeskOpen=async function(key=''){
 browserDeskStop();await nativeBrowserHidePromise;
 let status;try{status=await hubCall('native_browser_status');}catch(_){return nativeLegacyOpen();}
 if(!status.available){await nativeLegacyOpen();if(key)await browserDeskChooseAccount(key);return;}
 if(curSection!=='account_hub')return;
 nativeBrowserActive=true;browserDeskActive=true;nativeBrowserOverview=false;nativeBrowserSelected='';++nativeBrowserEpoch;
 nativeBrowserOpened.clear();for(const item of status.items||[])nativeBrowserOpened.add(`${item.platform}:${item.id}`);
 nativeBrowserDraw();if(key)await nativeBrowserChoose(key);
};
const nativePreviousShow=showSection;
showSection=async function(id){if(nativeBrowserActive){browserDeskStop();await nativeBrowserHidePromise;}return nativePreviousShow(id);};
// The main page scrolls independently of the OS window.
document.getElementById('page').addEventListener('scroll',nativeBrowserLayout,{passive:true});
window.addEventListener('resize',nativeBrowserLayout);
document.addEventListener('visibilitychange',()=>{nativeBrowserLayout();clearTimeout(nativeBrowserTimer);if(!document.hidden&&nativeBrowserActive&&nativeBrowserSelected)nativeBrowserPoll(nativeBrowserEpoch);});
for(const id of ['modal-root','prompt-root']){const root=document.getElementById(id);if(root)new MutationObserver(nativeBrowserLayout).observe(root,{childList:true,subtree:false});}

function nativeBrowserOverviewSlots(){
 return nativeBrowserOverviewKeys.map(key=>{const account=hubItems.find(a=>hubKey(a)===key),slot=[...document.querySelectorAll('[data-native-slot]')].find(e=>e.dataset.nativeSlot===key);return account&&slot?{platform:account.platform,id:account.id,rect:nativeBrowserRect(slot)}:null;}).filter(Boolean);
}
async function nativeBrowserShowOverview(){
 const requested=++nativeBrowserEpoch;clearTimeout(nativeBrowserTimer);
 const status=await hubCall('native_browser_status');if(!nativeBrowserActive||requested!==nativeBrowserEpoch)return;
 nativeBrowserOpened.clear();for(const item of status.items||[])nativeBrowserOpened.add(`${item.platform}:${item.id}`);
 nativeBrowserOverviewKeys=hubVisibleItems().filter(a=>nativeBrowserOpened.has(hubKey(a))).slice(0,4).map(hubKey);
 nativeBrowserSelected='';nativeBrowserOverview=true;nativeBrowserRevision=0;nativeBrowserDraw();
 const surface=document.querySelector('.native-surface');
 surface.innerHTML=`<div class="hint" id="nb-status">最多同时预览四个已打开账号；点击「进入账号」放大操作。其他账号点击左侧打开。</div><div class="native-overview-grid">${nativeBrowserOverviewKeys.map(key=>{const a=hubItems.find(a=>hubKey(a)===key);return `<article class="native-overview-card"><header><b>${esc(a.nickname)}</b><button class="btn" data-native-enter="${esc(key)}">进入账号</button></header><div class="native-overview-slot" data-native-slot="${esc(key)}"></div></article>`;}).join('')||'<div class="native-empty">先在左侧打开账号，再查看总览。</div>'}</div>`;
 nativeBrowserObserver.disconnect();document.querySelectorAll('[data-native-slot]').forEach(slot=>nativeBrowserObserver.observe(slot));
 document.querySelectorAll('[data-native-enter]').forEach(button=>button.onclick=()=>nativeBrowserChoose(button.dataset.nativeEnter));
 const result=await hubCall('native_browser_overview',requested,nativeBrowserOverviewSlots());if(!nativeBrowserActive||requested!==nativeBrowserEpoch||result.stale)return;
 nativeBrowserLayout();
}
