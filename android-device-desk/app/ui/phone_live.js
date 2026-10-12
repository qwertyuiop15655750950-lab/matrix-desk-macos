/* Explicitly selected phone: one H.264 decoder, latest frame only, direct scrcpy touch socket. */
let phoneLiveToken='',phoneLiveEpoch=0,phoneLiveTimer=null,phoneLiveSequence=0,phoneLiveSize=null,phoneLivePointer=null,phoneLiveMoveBusy=false,phoneLiveMovePending=null,phoneLivePasteBusy=false;
async function phoneLiveClose(){
 ++phoneLiveEpoch;clearTimeout(phoneLiveTimer);phoneLiveTimer=null;
 const token=phoneLiveToken;phoneLiveToken='';phoneLiveSize=null;phoneLivePointer=null;
 const panel=document.getElementById('phone-live');if(panel)panel.remove();
 if(token)try{await androidCall('android_live_stop',token);}catch(error){toast(error.message,true);}
}
async function phoneLiveOpen(index){
 const device=wallVisible()[index];if(!device||device.busy)return toast('手机不可操作，请刷新设备状态',true);
 wallStop();await phoneLiveClose();const epoch=phoneLiveEpoch;
 const mount=document.getElementById('phone-wall');if(!mount)return;
 const panel=document.createElement('section');panel.id='phone-live';panel.className='phone-live-panel';
 panel.innerHTML=`<div class="toolbar"><b>${esc(device.model||device.serial)} · 实时操作</b><button class="btn" id="live-back">返回总览</button><button class="btn" id="live-expand">放大</button><button class="btn" data-live-key="back">手机返回</button><button class="btn" data-live-key="home">手机主页</button></div><p class="hint" id="live-status" role="status">正在连接实时视频…</p><div class="phone-live-screen"><img id="live-image" alt="实时手机画面，点击和拖动操作，按 Command V 粘贴文字" draggable="false" tabindex="0"></div><div class="toolbar"><textarea id="live-text" rows="2" placeholder="电脑复制后在这里按 ⌘V，或点击手机输入框后直接在画面中按 ⌘V"></textarea><button class="btn" id="live-clipboard">粘贴电脑剪贴板</button><button class="btn primary" id="live-paste">粘贴到手机</button></div><p class="hint">触摸仅发给此手机；粘贴前先点击手机输入框。视频目标 24 帧/秒，界面最多显示 10 帧/秒，实际速度取决于设备和 USB 连接。</p>`;
 mount.prepend(panel);panel.scrollIntoView({block:'start',behavior:'instant'});document.getElementById('live-back').onclick=()=>phoneLiveClose();
 document.getElementById('live-expand').onclick=()=>{const full=panel.classList.toggle('expanded');document.getElementById('live-expand').textContent=full?'退出放大':'放大';};
 try{
  const response=await androidCall('android_live_start',device.serial);
  if(epoch!==phoneLiveEpoch||curSection!=='android_control'){await androidCall('android_live_stop',response.token);return;}
  phoneLiveToken=response.token;phoneLiveSequence=0;phoneLiveBind();await phoneLivePoll(epoch);
 }catch(error){if(epoch===phoneLiveEpoch){const status=document.getElementById('live-status');if(status)status.textContent=error.message;}}
}
async function phoneLivePoll(epoch){
 if(epoch!==phoneLiveEpoch||!phoneLiveToken||curSection!=='android_control'||document.hidden)return;
 const token=phoneLiveToken;
 try{
  const response=await androidCall('android_live_frame',token,phoneLiveSequence);
  if(epoch!==phoneLiveEpoch||token!==phoneLiveToken)return;
  const image=document.getElementById('live-image'),status=document.getElementById('live-status');if(!image||!status)return;
  if(response.ready&&response.b64){phoneLiveSequence=response.sequence;const size={width:response.width,height:response.height};image.onload=()=>{if(epoch===phoneLiveEpoch)phoneLiveSize=size;};image.src='data:image/jpeg;base64,'+response.b64;}
  status.textContent=response.ready?`实时连接 · ${response.width} × ${response.height} · 画面解码后 ${response.frame_age_ms} ms`:'等待首帧…';
 }catch(error){if(epoch===phoneLiveEpoch){const status=document.getElementById('live-status');if(status)status.textContent=error.message;phoneLiveSize=null;}return;}
 if(epoch===phoneLiveEpoch)phoneLiveTimer=setTimeout(()=>phoneLivePoll(epoch),100);
}
async function phoneLiveInput(action,params={}){
 const token=phoneLiveToken,size=phoneLiveSize;if(!token||!size)return false;
 if(action==='paste'&&phoneLivePasteBusy)return false;
 if(action==='paste')phoneLivePasteBusy=true;
 try{await androidCall('android_live_input',token,action,{...params,...size});return token===phoneLiveToken;}
 catch(error){if(token===phoneLiveToken)toast(error.message+'，未自动重试',true);return false;}finally{if(action==='paste')phoneLivePasteBusy=false;}
}
function phoneLiveBind(){
 const image=document.getElementById('live-image');
 const point=event=>{const r=image.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))};};
 image.onpointerdown=async event=>{
  if(event.button!==0||phoneLivePointer||!phoneLiveSize)return;event.preventDefault();image.focus();image.setPointerCapture(event.pointerId);
  const pointer={id:event.pointerId,token:phoneLiveToken,point:point(event),starting:true,ended:false};phoneLivePointer=pointer;
  const accepted=await phoneLiveInput('down',pointer.point);pointer.starting=false;
  if(!accepted||pointer.token!==phoneLiveToken){if(phoneLivePointer===pointer)phoneLivePointer=null;return;}
  if(pointer.ended){await phoneLiveInput('up',pointer.point);if(phoneLivePointer===pointer)phoneLivePointer=null;}
 };
 image.onpointermove=event=>{const p=phoneLivePointer;if(!p||p.id!==event.pointerId||p.starting||p.ended)return;p.point=point(event);phoneLiveMovePending=p;phoneLiveFlushMove();};
 image.onpointerup=async event=>{const p=phoneLivePointer;if(!p||p.id!==event.pointerId)return;p.point=point(event);p.ended=true;phoneLiveMovePending=null;if(p.starting)return;if(phoneLiveMoveBusy)return;await phoneLiveInput('up',p.point);if(phoneLivePointer===p)phoneLivePointer=null;};
 image.onpointercancel=async()=>{const p=phoneLivePointer;if(!p)return;p.ended=true;phoneLiveMovePending=null;if(p.starting)return;await phoneLiveInput('cancel',p.point);if(phoneLivePointer===p)phoneLivePointer=null;};
 image.onpaste=event=>{const text=event.clipboardData?.getData('text/plain');if(!text)return;event.preventDefault();phoneLiveInput('paste',{text});};
 document.querySelectorAll('[data-live-key]').forEach(button=>button.onclick=()=>phoneLiveInput(button.dataset.liveKey));
 document.getElementById('live-clipboard').onclick=async()=>{const token=phoneLiveToken,size=phoneLiveSize;if(!token||!size||phoneLivePasteBusy)return;phoneLivePasteBusy=true;try{await androidCall('android_live_paste_clipboard',token,size.width,size.height);if(token===phoneLiveToken)toast('已同步电脑文字并请求手机粘贴');}catch(error){toast(error.message,true);}finally{phoneLivePasteBusy=false;}};
 document.getElementById('live-paste').onclick=async()=>{const input=document.getElementById('live-text');const text=input.value;if(!text)return toast('请先粘贴电脑复制的文字',true);const ok=await phoneLiveInput('paste',{text});if(ok&&input.isConnected){input.value='';toast('已同步文字并请求手机粘贴');}};
}
async function phoneLiveFlushMove(){
 if(phoneLiveMoveBusy)return;phoneLiveMoveBusy=true;
 try{
  while(phoneLiveMovePending){const p=phoneLiveMovePending;phoneLiveMovePending=null;if(p!==phoneLivePointer||p.token!==phoneLiveToken||p.ended)continue;await phoneLiveInput('move',p.point);}
 }finally{phoneLiveMoveBusy=false;const p=phoneLivePointer;if(p?.ended&&!p.starting){await phoneLiveInput('up',p.point);if(phoneLivePointer===p)phoneLivePointer=null;}}
}
const liveWallMount=wallMount;
wallMount=function(){if(phoneLiveToken||document.getElementById('phone-live'))phoneLiveClose();liveWallMount();document.querySelectorAll('[data-wall-slot]').forEach(slot=>{const button=document.createElement('button');button.className='btn';button.textContent='实时操控';button.dataset.liveIndex=slot.dataset.wallSlot;button.onclick=()=>phoneLiveOpen(Number(button.dataset.liveIndex));slot.parentElement.querySelector('.phone-card-actions')?.appendChild(button);});};
const phoneLiveShowSection=showSection;
showSection=async id=>{await phoneLiveClose();return phoneLiveShowSection(id);};
document.addEventListener('visibilitychange',()=>{if(document.hidden)phoneLiveClose();});
window.addEventListener('blur',()=>{const pointer=phoneLivePointer;if(pointer){phoneLiveInput('cancel',pointer.point);phoneLivePointer=null;phoneLiveMovePending=null;}});
