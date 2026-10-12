/* User-started, bounded scrolling. No synthetic engagement or challenge solving. */
(() => {
  'use strict';
  if (window.top !== window || document.getElementById('matrix-browse-assistant')) return;
  const creator = ['creator.rednote.com', 'creator.xiaohongshu.com'].includes(location.hostname);
  const supported = Object.values(globalThis.__matrixPlatformHosts || {}).flat();
  if (!creator && !supported.includes(location.hostname)) return;
  const host = document.createElement('aside');
  host.id = 'matrix-browse-assistant';
  host.style.cssText = 'position:fixed;right:12px;top:90px;z-index:2147483647;width:300px;max-width:calc(100vw - 24px);';
  const shadow = host.attachShadow({mode:'open'});
  shadow.innerHTML = `<style>
    *{box-sizing:border-box}[hidden]{display:none!important}
    section{font:13px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;background:#172033;color:#e8edf5;border:1px solid #40536b;border-radius:10px;padding:10px;max-height:calc(100vh - 110px);overflow:auto}
    header{display:flex;align-items:center;justify-content:space-between;gap:8px}button,input{font:inherit}button{padding:5px 8px;border:1px solid #607894;border-radius:5px;background:#273b57;color:white;cursor:pointer}button:disabled{opacity:.5;cursor:default}
    label{display:block;margin:8px 0}input{width:66px;background:#0d1524;color:white;border:1px solid #607894;padding:4px}p{margin:8px 0}.buttons{display:flex;gap:5px;flex-wrap:wrap}.hint{font-size:12px;color:#aec1d9}#status{white-space:pre-wrap}
  </style><section><header><b>浏览辅助 · ${creator ? '页面检查' : '当前页计划'}</b><button id="toggle" aria-expanded="false">展开</button></header>
    <div id="body" hidden><div id="plan" ${creator ? 'hidden' : ''}>
    <label>计划时长 <input id="minutes" type="number" min="1" max="10" value="5"> 分钟</label>
    <label>每次停留 <input id="dwell" type="number" min="5" max="60" value="15"> 秒</label>
    <label>滚动幅度 <input id="amount" type="number" min="10" max="80" value="50"> %</label>
    <div class="buttons"><button id="select">选择滚动区域</button><button id="start" disabled>开始</button><button id="pause" disabled>暂停</button><button id="stop" disabled>停止</button></div>
    <p class="hint">仅滚动你选定的当前页面区域，最多 10 分钟。切换后台会暂停，换页面会停止；不自动点击下一条、点赞、评论或私信。</p></div>
    <div class="buttons"><button id="check">检查页面</button><button id="export">导出跑测记录</button></div>
    <p id="status" role="status">未启动。新页面不会自动运行。</p></div></section>`;
  document.documentElement.appendChild(host);
  const ui = id => shadow.getElementById(id);
  let target = null, selecting = false, job = null, lastTick = performance.now();
  const events = [], maximumEvents = 200;
  const status = text => { ui('status').textContent = text; };
  const record = (type, details = {}) => {
    events.push({at:new Date().toISOString(), type, ...details});
    if (events.length > maximumEvents) events.shift();
    host.dataset.lastEvent = type;
  };
  const number = (id, low, high) => Math.min(high, Math.max(low, Number(ui(id).value) || low));
  const gate = () => {
    try {
      const result = globalThis.__matrixReadAccessGate?.();
      if (result && typeof result.blocked === 'boolean') return result;
    } catch (_) { /* Fail closed if the page guard is unavailable. */ }
    return {blocked:true,stop_reason:'guard_unavailable',error:'页面状态检查失败，请重新打开页面。'};
  };
  const visible = element => element?.isConnected && element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== 'hidden';
  const scrollable = element => visible(element) && element.scrollHeight > element.clientHeight + 12
    && (element === document.scrollingElement || /auto|scroll/.test(getComputedStyle(element).overflowY));
  const controls = () => {
    for (const id of ['select','minutes','dwell','amount']) ui(id).disabled = !!job;
    ui('start').disabled = !!job || !target;
    ui('pause').disabled = ui('stop').disabled = !job;
    ui('pause').textContent = job?.paused ? '继续' : '暂停';
    host.dataset.state = job ? (job.paused ? 'paused' : 'running') : 'idle';
  };
  const cancelSelection = () => { selecting = false; ui('select').textContent = '选择滚动区域'; };
  const stop = (reason, message) => {
    if (!job) return;
    record('stopped', {reason, steps:job.steps, active_seconds:Math.round(job.elapsed / 1000)});
    job = null; clearInterval(timer); timer = null; controls();
    status(message || `已停止：${reason}。需要手动重新开始。`);
  };
  const pause = reason => {
    if (!job || job.paused) return;
    job.paused = true; record('paused', {reason}); controls();
    status(reason === 'hidden' ? '页面已切到后台，计划暂停。返回后需手动继续。' : '计划已暂停。');
  };
  let timer = null;
  const tick = () => {
    if (!job) return;
    const now = performance.now(), delta = now - lastTick; lastTick = now;
    const warning = gate();
    if (warning.blocked) { stop(warning.stop_reason, warning.error); return; }
    if (location.href !== job.url) { stop('page_changed', '页面地址已改变，计划停止。'); return; }
    if (document.hidden || !document.hasFocus()) { pause('hidden'); return; }
    if (job.paused) return;
    // A sleeping computer or heavily delayed timer must never trigger catch-up scrolls.
    if (delta > 2000) { pause('timer_gap'); status('计时中断，计划暂停；请人工检查后继续。'); return; }
    job.elapsed += delta; job.sinceScroll += delta;
    if (job.elapsed >= job.duration) { stop('completed', '计划完成，已停止。'); return; }
    if (!scrollable(target)) { stop('target_unavailable', '滚动区域已消失或不可滚动，计划停止。'); return; }
    if (job.sinceScroll < job.dwell) return;
    const remaining = target.scrollHeight - target.clientHeight - target.scrollTop;
    if (remaining <= 3) { stop('end_reached', '已到当前区域末尾，计划停止。'); return; }
    target.scrollBy({top:Math.min(remaining, Math.max(1, Math.round(target.clientHeight * job.amount))), behavior:'instant'});
    job.steps++; job.sinceScroll = 0;
    record('scrolled', {step:job.steps});
    status(`正在浏览：滚动 ${job.steps} 次，已用 ${Math.round(job.elapsed / 1000)} 秒。`);
  };
  ui('toggle').onclick = () => {
    ui('body').hidden = !ui('body').hidden;
    ui('toggle').textContent = ui('body').hidden ? '展开' : '收起';
    ui('toggle').setAttribute('aria-expanded', String(!ui('body').hidden));
  };
  ui('select').onclick = () => {
    if (selecting) { cancelSelection(); status('已取消选择。'); return; }
    selecting = true; ui('select').textContent = '取消选择';
    status('点击你想滚动的内容区域。此次选择不会点击网页按钮。');
  };
  document.addEventListener('click', event => {
    if (!selecting || event.composedPath().includes(host)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    let node = event.target;
    while (node && !scrollable(node)) node = node.parentElement;
    target = node || (scrollable(document.scrollingElement) ? document.scrollingElement : null);
    cancelSelection(); controls();
    status(target ? '区域已选择。请确认计划后点击开始。' : '此处没有可滚动区域，请重新选择。');
  }, true);
  ui('start').onclick = () => {
    if (creator || job || !scrollable(target)) { status('请先选择可滚动区域。'); return; }
    if (document.getElementById('matrix-browser-collector')?.shadowRoot?.getElementById('start')?.disabled) {
      status('评论采集正在使用页面，请先停止采集。'); return;
    }
    const warning = gate();
    if (warning.blocked) { record('start_blocked', {reason:warning.stop_reason}); status(warning.error); return; }
    if (document.hidden || !document.hasFocus()) { status('请将此页面置于前台再开始。'); return; }
    cancelSelection();
    job = {url:location.href, duration:number('minutes',1,10)*60000, dwell:number('dwell',5,60)*1000,
      amount:number('amount',10,80)/100, steps:0, elapsed:0, sinceScroll:0, paused:false};
    record('started', {minutes:job.duration/60000, dwell_seconds:job.dwell/1000, amount_percent:job.amount*100});
    lastTick = performance.now(); controls(); status('计划已开始；先停留，再滚动。');
    timer = setInterval(tick, 250);
  };
  ui('pause').onclick = () => {
    if (!job) return;
    if (!job.paused) { pause('user'); return; }
    const warning = gate();
    if (warning.blocked) { stop(warning.stop_reason, warning.error); return; }
    if (document.hidden || !document.hasFocus()) { status('请将页面置于前台后继续。'); return; }
    if (location.href !== job.url) { stop('page_changed'); return; }
    job.paused = false; job.sinceScroll = 0; lastTick = performance.now();
    record('resumed'); controls(); status('已继续；从一次完整停留开始。');
  };
  ui('stop').onclick = () => stop('user', '已手动停止。');
  document.addEventListener('visibilitychange', () => { if (document.hidden) pause('hidden'); });
  window.addEventListener('blur', () => pause('hidden'));
  window.addEventListener('pagehide', () => { cancelSelection(); stop('page_closed'); });
  ui('check').onclick = () => {
    const warnings = gate();
    if (warnings.blocked) stop(warnings.stop_reason, warnings.error);
    const images = [...document.images].filter(visible);
    const failed = images.filter(image => image.complete && image.naturalWidth === 0).length;
    const pending = images.filter(image => !image.complete).length;
    const publish = [...document.querySelectorAll('button,[role="button"]')]
      .filter(button => /^(发布|发布笔记|定时发布|發布|發佈)$/.test((button.innerText || '').trim()));
    const inViewport = button => {
      const rect = button.getBoundingClientRect();
      return visible(button) && rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth;
    };
    const details = {width:innerWidth,height:innerHeight,failed_images:failed,pending_images:pending,
      publish_buttons:publish.length,publish_buttons_in_view:publish.filter(inViewport).length,
      blocked:!!warnings.blocked,reason:warnings.stop_reason || ''};
    record('page_check', details);
    status(`${warnings.blocked ? warnings.error + '\n' : ''}窗口 ${innerWidth} × ${innerHeight}；图片失败 ${failed}，加载中 ${pending}。\n`
      + `发布按钮发现 ${publish.length} 个，完整可见 ${details.publish_buttons_in_view} 个。\n`
      + '未发现按钮也可能是页面阶段或结构不同；检查不会刷新页面或修改草稿。');
  };
  ui('export').onclick = () => {
    const payload = {version:'1.2.0', origin:location.origin, state:host.dataset.state, events};
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)], {type:'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = 'matrix-browse-test.json';
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  controls();
  host.dataset.ready = 'true';
})();
