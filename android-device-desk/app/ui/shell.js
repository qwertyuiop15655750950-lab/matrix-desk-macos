/* 独立设备 UI；无账号、发布、私信或演示数据。 */
const NAV = [], TITLES = {}, RENDER = {};
let curSection = 'android_control';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function api(method, ...args) {
  if (!window.pywebview?.api?.[method]) return {ok:false,error:'设备后端未就绪，请从启动入口打开'};
  return await window.pywebview.api[method](...args);
}
function toast(message, error=false) {
  const node=document.createElement('div'); node.className='toast'+(error?' err':''); node.textContent=message;
  document.getElementById('toast-root').appendChild(node); setTimeout(()=>node.remove(),5000);
}
function showInfo(title, message) {
  const root=document.getElementById('modal-root');
  root.innerHTML=`<div class="modal-mask"><div class="modal"><h2>${esc(title)}</h2><pre style="white-space:pre-wrap">${esc(message)}</pre><button class="btn primary" id="device-info-close">关闭</button></div></div>`;
  document.getElementById('device-info-close').onclick=()=>{root.innerHTML='';};
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast('已复制'); }
  catch(error) { showInfo('复制内容', text); }
}
async function showSection(id) {
  if (!RENDER[id]) return;
  curSection=id;
  document.getElementById('page-title').textContent=TITLES[id]||id;
  document.getElementById('nav').innerHTML=NAV.map(item=>`<div class="nav-item ${item.id===id?'active':''}" data-device-section="${esc(item.id)}">${esc(item.ico||'')} ${esc(item.name)}</div>`).join('');
  document.querySelectorAll('[data-device-section]').forEach(node=>node.onclick=()=>showSection(node.dataset.deviceSection));
  try { await RENDER[id](); } catch(error) { toast(error.message,true); }
}
let deviceInitialized=false;
function deviceInit() {
  if(deviceInitialized||!window.pywebview?.api)return;
  deviceInitialized=true; showSection('android_control');
}
window.addEventListener('pywebviewready',deviceInit);
window.addEventListener('DOMContentLoaded',deviceInit);
