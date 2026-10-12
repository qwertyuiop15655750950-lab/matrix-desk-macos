(() => {
  'use strict';
  if (globalThis.__matrixCollectorInstalled) return;
  const hosts = Object.fromEntries(Object.entries(globalThis.__matrixPlatformHosts || {})
    .filter(([name]) => ['douyin','xiaohongshu'].includes(name)).map(([name, values]) => [name,new Set(values)]));
  const platform = Object.keys(hosts).find(name => hosts[name].has(location.hostname));
  if (!platform || window.top !== window) return;
  // These page selectors require verification when the platform changes its DOM.
  const selectors = {
    douyin: {
      item: '[data-e2e="comment-item"], [class*="comment-item"]',
      text: ['[data-e2e="comment-content"]', '[class*="comment-text"]', '[class*="comment-content"]', '[class*="content"]'],
      author: ['[data-e2e="comment-author"]', '[class*="author"]', '[class*="user-name"]', 'a[href*="/user/"]'],
      list: '[data-e2e="comment-list"], [class*="comment-list"]',
    },
    xiaohongshu: {
      item: '.comment-item, .parent-comment, [class*="comment-item"]',
      text: ['.content .note-text', '.content', '[class*="comment-content"]', '[class*="comment-text"]'],
      author: ['.author .name', '.author', '[class*="user-name"]', 'a[href*="/user/profile/"]'],
      list: '.comments-container, .comment-list, [class*="comment-list"]',
    },
  };
  const host = document.createElement('div');
  host.id = 'matrix-browser-collector';
  host.style.cssText = 'position:fixed;right:18px;bottom:18px;width:316px;z-index:2147483647;';
  const shadow = host.attachShadow({mode: 'open'});
  shadow.innerHTML = `<style>
    *{box-sizing:border-box} .panel{font:13px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;color:#e8edf5;background:#172033;border:1px solid #3c506c;border-radius:14px;box-shadow:0 8px 36px #0006;padding:14px}
    header{display:flex;align-items:center;justify-content:space-between;font-weight:700;font-size:14px;margin-bottom:8px}
    label{display:block;margin-top:8px}textarea,input{font:inherit;color:#e8edf5;background:#0d1524;border:1px solid #40536b;border-radius:6px;padding:7px;width:100%}textarea{resize:vertical;min-height:62px}input{width:68px;margin:0 5px}
    button{font:inherit;border:1px solid #4a607e;background:#273b57;color:#fff;border-radius:6px;cursor:pointer;padding:6px 9px}button:disabled{opacity:.45;cursor:default}.start{background:#267269}.buttons{display:flex;gap:6px;margin-top:10px;flex-wrap:wrap}
    .hint{color:#a8b8ce;font-size:12px;margin:8px 0 0}.status{margin:9px 0 0;white-space:pre-wrap}.body[hidden]{display:none}
  </style><section class="panel"><header><span>评论采集 · ${platform === 'douyin' ? '抖音' : '小红书'}</span><button id="collapse" aria-label="收起采集面板">收起</button></header>
    <div class="body"><label>关键词（逗号或换行分隔）<textarea id="words">价格,多少钱,怎么买,地址,咨询</textarea></label>
    <label>滚动后等待<input id="interval" type="number" min="2" max="10" step="1" value="4">秒</label>
    <div class="buttons"><button id="start" class="start">开始采集</button><button id="pause" disabled>暂停</button><button id="stop" disabled>停止</button><button id="export" disabled>导出 JSON</button></div>
    <p class="status" id="status" role="status">先打开目标视频或笔记的评论区。</p><p class="hint">读取当前网页已加载的评论。登录、验证或异常提醒会停止采集，已读结果会保留；请人工处理后再继续。</p></div></section>`;
  document.documentElement.appendChild(host);
  const ui = id => shadow.getElementById(id);
  let active = null;
  let lastResult = null;
  const status = text => { ui('status').textContent = text; };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const interval = () => Math.min(10, Math.max(2, Number(ui('interval').value) || 4)) * 1000;
  const bounded = (value, minimum, maximum, fallback) => Number.isFinite(Number(value))
    ? Math.min(maximum, Math.max(minimum, Math.floor(Number(value)))) : fallback;
  const wordsFrom = value => [...new Set((Array.isArray(value) ? value : String(value).split(/[,，\n]/))
    .map(word => String(word).trim()).filter(Boolean))].slice(0, 200);
  const visible = element => element.getClientRects().length > 0
    && getComputedStyle(element).visibility !== 'hidden';
  const firstElement = (item, choices, excluded = null) => {
    for (const selector of choices) {
      for (const element of item.querySelectorAll(selector)) {
        // An outer comment must never borrow a nested reply's text or author.
        if (element.closest(selectors[platform].item) !== item
            || (excluded && excluded.contains(element))) continue;
        if ((element.innerText || element.textContent || '').trim()) return element;
      }
    }
    return null;
  };
  const elementText = element => element ? (element.innerText || element.textContent || '').trim() : '';
  const profileId = href => {
    if (!href) return '';
    try {
      const url = new URL(href, location.href);
      if (url.protocol !== 'https:' || !hosts[platform].has(url.hostname) || url.username || url.password) return '';
      const match = url.pathname.match(/^\/user\/(?:profile\/)?([^/]+)\/?$/);
      if (!match) return '';
      const id = decodeURIComponent(match[1]).trim();
      return id && !/[\s/?#]/.test(id) ? id : '';
    } catch (_) { return ''; }
  };
  const contacts = text => ({
    wechat: (text.match(/(?:微信|wx|wechat)[:：\s]*([a-zA-Z0-9_-]{6,20})/i) || [])[1] || '',
    phone: (text.match(/1[3-9]\d{9}/) || [])[0] || '',
    qq: (text.match(/(?:QQ|qq)[:：\s]*([1-9]\d{4,10})/) || [])[1] || '',
  });
  function readComments(job) {
    const config = selectors[platform];
    const nodes = [...document.querySelectorAll(config.item)].filter(visible);
    for (const item of nodes) {
      if (job.read >= job.maxComments) break;
      const textElement = firstElement(item, config.text);
      const text = elementText(textElement);
      if (!text) continue;
      const authorElement = firstElement(item, config.author, textElement);
      const nickname = elementText(authorElement);
      let userId = '';
      const authorLinks = authorElement ? [authorElement.closest('a[href]'),
        ...authorElement.querySelectorAll('a[href]')] : [];
      for (const link of authorLinks) {
        if (!link || !item.contains(link) || link.closest(config.item) !== item
            || (textElement && textElement.contains(link))) continue;
        userId = profileId(link.getAttribute('href'));
        if (userId) break;
      }
      const commentId = item.getAttribute('data-comment-id') || '';
      const key = JSON.stringify([commentId, userId, nickname, text]);
      if (job.seen.has(key)) continue;
      job.seen.add(key);
      job.read++;
      const keyword = job.keywords.find(word => text.toLocaleLowerCase().includes(word.toLocaleLowerCase()));
      if (job.collectAll || keyword) job.items.push({comment_id:commentId, nickname, user_id: userId, text, keyword:keyword||'', contacts: contacts(text)});
    }
    return nodes;
  }
  function scrollComments(nodes) {
    const panels = [...document.querySelectorAll(selectors[platform].list), ...nodes];
    for (const panel of panels) {
      if (!visible(panel)) continue;
      let current = panel;
      for (let depth = 0; current && depth < 9; depth++, current = current.parentElement) {
        if (current === document.body || current === document.documentElement) break;
        const style = getComputedStyle(current);
        if (current.scrollHeight > current.clientHeight + 12 && /auto|scroll/.test(style.overflowY)) {
          current.scrollBy({top: Math.max(320, Math.round(current.clientHeight * .8)), behavior: 'smooth'});
          return true;
        }
      }
    }
    // Never scroll the Douyin video feed when its comment panel isn't scrollable.
    if (platform === 'xiaohongshu' && document.scrollingElement
        && document.scrollingElement.scrollHeight > innerHeight + 12) {
      window.scrollBy({top: Math.round(innerHeight * .7), behavior: 'smooth'});
      return true;
    }
    return false;
  }
  function accessStopped(job) {
    const gate = typeof globalThis.__matrixReadAccessGate === 'function'
      ? globalThis.__matrixReadAccessGate()
      : {blocked:true,stop_reason:'guard_unavailable',requires_review:true,error:'页面状态检查未就绪，采集已停止；请重新打开账号浏览器'};
    if (!gate.blocked) return false;
    job.gate = gate;
    job.stopped = true;
    job.paused = false;
    job.stopReason = gate.stop_reason;
    return true;
  }
  async function waitWhileRunning(job, duration) {
    let elapsed = 0;
    while (!job.stopped && elapsed < duration) {
      if (accessStopped(job)) break;
      if (location.href !== job.sourceUrl) {
        job.stopped = true;
        job.stopReason = 'page_changed';
        break;
      }
      if (job.paused) { await sleep(100); continue; }
      const step = Math.min(100, duration - elapsed);
      await sleep(step);
      if (!job.paused) elapsed += step;
    }
  }
  function reply(requestId, result) {
    window.postMessage({type: 'MATRIX_PLUGIN_RESULT', requestId, result}, location.origin);
  }
  function setButtons(running) {
    ui('start').disabled = running;
    ui('words').disabled = running;
    ui('pause').disabled = !running;
    ui('stop').disabled = !running;
    ui('pause').textContent = '暂停';
    ui('export').disabled = !lastResult || !lastResult.items.length;
  }
  async function run(request) {
    const requestId = typeof request.requestId === 'string' ? request.requestId : '';
    if (!requestId) return;
    if (['running','paused'].includes(document.getElementById('matrix-browse-assistant')?.dataset.state)) {
      status('浏览计划正在使用页面，请先停止浏览计划');
      reply(requestId, {ok:false,items:[],error:'浏览计划正在使用页面，请先停止浏览计划'}); return;
    }
    if (active) { reply(requestId, {ok: false, items: [], error: '已有采集任务，请先暂停或停止当前任务'}); return; }
    if (request.platform && request.platform !== platform) {
      reply(requestId, {ok: false, items: [], error: '当前浏览器页面与采集平台不一致'}); return;
    }
    const keywords = wordsFrom(request.keywords || ui('words').value);
    if (!keywords.length && request.collectAll !== true) { reply(requestId, {ok: false, items: [], error: '请填写至少一个关键词'}); return; }
    if (request.intervalMs !== undefined) ui('interval').value = bounded(request.intervalMs, 2000, 10000, 4000) / 1000;
    ui('words').value = keywords.join(',');
    shadow.querySelector('.body').hidden = false;
    ui('collapse').textContent = '收起';
    const job = {requestId, keywords, collectAll:request.collectAll===true, maxComments: bounded(request.maxComments, 1, 1000, 100),
      scrollTimes: bounded(request.scrollTimes, 0, 20, 5), sourceUrl: location.href,
      read: 0, items: [], seen: new Set(), paused: false, stopped: false, stopReason: '', gate:null};
    active = job;
    setButtons(true);
    let result;
    try {
      status('等待评论区，请在网页中打开目标视频或笔记的评论。');
      let nodes = [];
      // Allow the user to finish opening the comment panel before scanning.
      for (let waited = 0; waited < 10000 && !job.stopped; waited += 250) {
        if (location.href !== job.sourceUrl) { job.stopped = true; job.stopReason = 'page_changed'; break; }
        await waitWhileRunning(job, 250);
        if (job.stopped) break;
        nodes = readComments(job);
        if (job.read) break;
      }
      if (!job.read && !job.stopped) {
        result = {ok: false, items: [], read: 0,
          error: '未读取到评论，请完成登录并打开评论区；页面评论选择器可能需要校准'};
      } else {
        for (let round = 0; round < job.scrollTimes && !job.stopped && job.read < job.maxComments; round++) {
          await waitWhileRunning(job, 1);
          if (job.stopped) break;
          if (location.href !== job.sourceUrl) { job.stopped = true; job.stopReason = 'page_changed'; break; }
          status(`已读取 ${job.read} 条评论，命中 ${job.items.length} 条；滚动 ${round + 1}/${job.scrollTimes}。`);
          if (!scrollComments(nodes)) break;
          await waitWhileRunning(job, interval());
          if (job.stopped) break;
          if (location.href !== job.sourceUrl) { job.stopped = true; job.stopReason = 'page_changed'; break; }
          nodes = readComments(job);
        }
        result = {ok: !job.gate || job.read > 0, items: job.items, read: job.read, stopped: job.stopped,
                  stop_reason: job.stopReason, timed_out: job.stopReason === 'timeout', sourceUrl: job.sourceUrl, truncated:job.read >= job.maxComments, collect_all:job.collectAll, ...(job.gate||{})};
      }
    } catch (_) {
      result = {ok: job.read > 0, items: job.items, read:job.read, stopped:true, warning:'页面评论读取中断，已保留先前结果。', error: '页面评论读取失败，请重新打开评论区或校准页面选择器'};
    } finally {
      if (active === job) active = null;
      lastResult = result || {ok: false, items: [], error: '采集任务未完成'};
      setButtons(false);
      status(lastResult.blocked ? `${lastResult.error}\n已保留 ${job.items.length} 条评论。` : lastResult.ok ? `${job.stopped ? (job.stopReason === 'timeout' ? '等待超时，已停止并保留结果' : '已停止') : '采集完成'}：读取 ${job.read} 条，命中 ${job.items.length} 条。`
                          : lastResult.error);
      reply(requestId, lastResult);
    }
  }
  ui('collapse').onclick = () => {
    const body = shadow.querySelector('.body');
    body.hidden = !body.hidden;
    ui('collapse').textContent = body.hidden ? '展开' : '收起';
  };
  ui('start').onclick = () => { void run({requestId: `manual-${Date.now()}`, platform, keywords: ui('words').value}); };
  ui('pause').onclick = () => {
    if (!active) return;
    active.paused = !active.paused;
    ui('pause').textContent = active.paused ? '继续' : '暂停';
    status(active.paused ? `已暂停：读取 ${active.read} 条，命中 ${active.items.length} 条。`
                         : `继续采集：读取 ${active.read} 条，命中 ${active.items.length} 条。`);
  };
  const stop = (reason = 'user') => { if (active) {
    active.stopped = true; active.paused = false; active.stopReason = reason; status('正在停止采集…');
  } };
  ui('stop').onclick = () => stop();
  ui('export').onclick = () => {
    if (!lastResult || !lastResult.items.length) return;
    const rows = lastResult.items.map(item => ({comment_id: item.comment_id || '', user_id: item.user_id,
      nickname: item.nickname, content: item.text, keyword: item.keyword}));
    const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(rows, null, 2)], {type: 'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = `评论采集-${platform}-${Date.now()}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  };
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || !event.data) return;
    const request = event.data;
    if (request.type === 'MATRIX_PLUGIN_SCAN') { void run(request); }
    if (request.type === 'MATRIX_PLUGIN_CONTROL' && active
        && request.requestId === active.requestId && request.action === 'stop') stop(request.reason === 'timeout' ? 'timeout' : 'user');
  });
  globalThis.__matrixCollectorInstalled = true;
  document.documentElement.dataset.matrixCollector = 'ready';
})();
