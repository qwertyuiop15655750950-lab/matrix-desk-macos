/* One page for manual device control. Low-frame-rate previews, two ADB reads at a time. */
let wallRunning=false,wallPending=false,wallEpoch=0,wallTimer=null,wallPage=0,wallSize=200,wallSignature='';
const wallFrames=new Map();
function wallDevices(){return androidItems.filter(item=>item.state==='device'&&(!androidSelected.size||androidSelected.has(item.serial)));}
function wallVisible(){return wallDevices().slice(wallPage*8,wallPage*8+8);}
function wallStop(){wallRunning=false;++wallEpoch;clearTimeout(wallTimer);wallTimer=null;wallStatus();}
function wallStatus(){
 const node=document.getElementById('wall-status');if(node)node.textContent=wallRunning?'预览更新中 · 每轮间隔 3 秒':'预览已暂停，显示上次画面';
 const start=document.getElementById('wall-start');if(start)start.disabled=wallRunning||wallPending||!wallVisible().length;
 const stop=document.getElementById('wall-stop');if(stop)stop.disabled=!wallRunning;
 const once=document.getElementById('wall-once');if(once)once.disabled=wallPending||wallRunning||!wallVisible().length;
}
function wallMount(){
 if(curSection!=='android_control')return;
 const mount=document.getElementById('phone-wall');if(!mount)return;
 const devices=wallDevices();wallPage=Math.max(0,Math.min(wallPage,Math.max(0,Math.ceil(devices.length/8)-1)));
 const signature=wallVisible().map(item=>item.serial).join('|');
 if(signature!==wallSignature){wallStop();wallSignature=signature;}
 const online=new Set(androidItems.filter(item=>item.state==='device').map(item=>item.serial));
 for(const serial of wallFrames.keys())if(!online.has(serial))wallFrames.delete(serial);
 mount.innerHTML=`<div class="wall-heading"><h2>手机总览</h2><span>${devices.length} 台 · 每页最多 8 台</span></div>
 <div class="toolbar"><button class="btn" id="wall-devices">刷新设备</button><button class="btn primary" id="wall-start">开始预览</button><button class="btn" id="wall-stop">暂停</button><button class="btn" id="wall-once">刷新画面</button>
 <label>显示大小 <input id="wall-size" type="range" min="140" max="320" step="20" value="${wallSize}"></label>
 <button class="btn" id="wall-prev" ${wallPage===0?'disabled':''}>上一页</button><span>${wallPage+1} / ${Math.max(1,Math.ceil(devices.length/8))}</span><button class="btn" id="wall-next" ${(wallPage+1)*8>=devices.length?'disabled':''}>下一页</button></div>
 <p class="hint" id="wall-status" role="status"></p><div class="phone-wall-grid" style="--phone-width:${wallSize}px">${wallVisible().map((item,index)=>{
 const frame=wallFrames.get(item.serial);
 return `<article class="phone-card"><div class="phone-card-title"><b>${esc(item.model||'Android')}</b><small>${esc(item.serial)}</small></div><div class="phone-screen" data-wall-slot="${index}">${frame?.b64?`<img draggable="false" data-wall-screen="${index}" src="data:image/png;base64,${esc(frame.b64)}" alt="${esc(item.model||item.serial)} 屏幕">`:'<span>点击开始预览</span>'}</div><div class="hint" data-wall-time="${index}">${esc(frame?.error||frame?.time||'尚未获取')}</div><div class="phone-card-actions"><button class="btn" data-wall-action="back" data-wall-index="${index}">返回</button><button class="btn" data-wall-action="home" data-wall-index="${index}">主页</button></div></article>`;
 }).join('')||'<p class="hint">连接并授权手机后点击刷新设备。</p>'}</div><p class="hint">点击画面操作此机，拖动可滑动。点击「实时操控」在此页打开连续视频和直接触摸；总览保持低频预览。切换页面或窗口隐藏时暂停。</p>`;
 document.getElementById('wall-devices').onclick=androidGuard(async()=>{wallStop();await renderAndroid();});
 document.getElementById('wall-start').onclick=androidGuard(async()=>{if(wallPending)return;wallRunning=true;const epoch=++wallEpoch;await wallRefresh(epoch);});
 document.getElementById('wall-stop').onclick=wallStop;
 document.getElementById('wall-once').onclick=androidGuard(()=>wallRefresh(++wallEpoch));
 document.getElementById('wall-size').oninput=event=>{wallSize=Number(event.target.value);document.querySelector('.phone-wall-grid').style.setProperty('--phone-width',wallSize+'px');};
 document.getElementById('wall-prev').onclick=()=>{wallStop();wallPage--;wallMount();};
 document.getElementById('wall-next').onclick=()=>{wallStop();wallPage++;wallMount();};
 document.querySelectorAll('[data-wall-action]').forEach(button=>button.onclick=androidGuard(()=>wallAction(Number(button.dataset.wallIndex),button.dataset.wallAction)));
 wallBindScreens();wallStatus();
}
async function wallAction(index,action,params={}){
 const device=wallVisible()[index];if(!device||androidBusy||device.busy)throw new Error('设备不可操作，请刷新状态');
 androidInvalidateDraft();
 await androidRun(async()=>{const result=await androidCall('android_batch',[device.serial],action,params);const failure=result.items?.find(item=>!item.ok);if(failure)throw new Error(failure.error);});
 if(!wallPending)await wallRefresh(wallEpoch);
}
function wallBindScreens(){
 document.querySelectorAll('[data-wall-screen]').forEach(image=>{
  let down=null;
  const point=event=>{const box=image.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),y:Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))};};
  image.onpointerdown=event=>{if(event.button!==0||androidBusy)return;down=point(event);image.setPointerCapture(event.pointerId);event.preventDefault();};
  image.onpointercancel=()=>{down=null;};
  image.onpointerup=androidGuardEvent(async event=>{
   if(!down)return;const end=point(event),start=down;down=null;
   const dx=end.x-start.x,dy=end.y-start.y;
   if(Math.hypot(dx,dy)<.035)await wallAction(Number(image.dataset.wallScreen),'tap',end);
   else await wallAction(Number(image.dataset.wallScreen),'swipe',{direction:Math.abs(dx)>Math.abs(dy)?(dx>0?'right':'left'):(dy>0?'down':'up'),duration:500});
  });
 });
}
function androidGuardEvent(callback){return async event=>{try{await callback(event);}catch(error){toast(error.message,true);}};}
async function wallRefresh(epoch){
 if(wallPending||curSection!=='android_control'||document.hidden)return;
 wallPending=true;wallStatus();
 const devices=wallVisible();let cursor=0;
 const worker=async()=>{while(cursor<devices.length&&epoch===wallEpoch&&curSection==='android_control'&&!document.hidden){
  const index=cursor++,device=devices[index];let frame;
  try{if(device.busy)throw new Error('手机任务执行中');frame=await androidCall('android_screenshot',device.serial,480);frame.time=new Date().toLocaleTimeString();}
  catch(error){frame={error:error.message};}
  if(epoch!==wallEpoch||curSection!=='android_control'||document.hidden)return;
  wallFrames.set(device.serial,frame);
  const slot=document.querySelector(`[data-wall-slot="${index}"]`),time=document.querySelector(`[data-wall-time="${index}"]`);
  if(slot)slot.innerHTML=frame.b64?`<img draggable="false" data-wall-screen="${index}" src="data:image/png;base64,${esc(frame.b64)}" alt="${esc(device.model||device.serial)} 屏幕">`:'<span>画面不可用</span>';
  if(time)time.textContent=frame.error||frame.time;
  wallBindScreens();
 }};
 try{await Promise.all([worker(),worker()]);}
 finally{wallPending=false;wallStatus();if(wallRunning&&epoch===wallEpoch)wallTimer=setTimeout(()=>wallRefresh(epoch),3000);}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)wallStop();});
window.addEventListener('blur',wallStop);
const wallShowSection=showSection;
showSection=async id=>{wallStop();wallFrames.clear();return wallShowSection(id);};
