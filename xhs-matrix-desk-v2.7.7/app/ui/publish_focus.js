/* Focused main workflows. Auxiliary views keep their existing handlers. */
let auxiliaryOpen = false, focusPublishEpoch = 0;
const publishDraftsSelected = new Set();
function renderNav() {
  const primary = [
    {id:'account_hub',name:'账号管理',ico:'◎'},
    {id:'publish',name:'批量发布',ico:'↑'},
    {id:'batch_collect',name:'数据采集',ico:'⌕'},
    {id:'warmup',name:'养号计划',ico:'◷'},
    {id:'phone_desk',name:'手机管理 · ADB',ico:'▯'},
  ];
  const auxiliary = NAV.filter(item => !primary.some(main=>main.id===item.id)
    && !['grid','cluster','accounts','douyin','drafts','collect','schedules','records'].includes(item.id));
  const entry = item => `<button class="nav-item ${curSection===item.id?'active':''}" data-s="${item.id}"><span class="ico">${item.ico}</span>${esc(item.name)}</button>`;
  document.getElementById('nav').innerHTML = `<div class="nav-label">工作台</div>${primary.map(entry).join('')}
    <button id="nav-aux" class="nav-aux" aria-expanded="${auxiliaryOpen}">辅助功能 ${auxiliaryOpen?'−':'＋'}</button>
    ${auxiliaryOpen?auxiliary.map(entry).join(''):''}`;
  document.querySelectorAll('#nav [data-s]').forEach(button=>button.onclick=()=>showSection(button.dataset.s));
  document.getElementById('nav-aux').onclick=()=>{auxiliaryOpen=!auxiliaryOpen;renderNav();};
  document.getElementById('page-title').textContent = primary.find(item=>item.id===curSection)?.name || TITLES[curSection];
}

async function renderPublishFocus() {
  const epoch = ++focusPublishEpoch;
  const [dr, ar] = await Promise.all([api('drafts_list'),api('accounts_list')]);
  if (epoch !== focusPublishEpoch || curSection !== 'publish') return;
  if (!dr.ok || !ar.ok) return showInfo('无法加载发布页',dr.error||ar.error||'请稍后重试');
  state.drafts=dr.items;state.accounts=ar.items;
  const rows = state.drafts.map(d=>`<tr><td><input type="checkbox" data-did="${d.id}" ${publishDraftsSelected.has(d.id)?'checked':''}></td>
    <td><button class="text-button" data-edit="${d.id}">${esc(d.title)}</button><div class="subtle">${d.mode==='poster'?'小红书文字配图 · 随机卡片':'自备媒体'}</div></td></tr>`).join('');
  const accounts = state.accounts.map(a=>`<label class="account-choice"><input type="checkbox" data-aid="${a.id}" ${state.pubAccounts.has(a.id)?'checked':''}>
    <span>${esc(a.nickname)}<small>${esc(a.group_name||'默认分组')}</small></span></label>`).join('');
  document.getElementById('page').innerHTML = `<div class="publish-intro"><p>导入文案，选择账号，按顺序发布。</p><span>使用原文 · 一次打开一个账号</span></div>
    <div class="focus-columns"><section class="card"><div class="focus-heading"><h2>文案</h2><button class="btn" id="p-import">导入 Word</button></div>
    <div class="toolbar"><button class="text-button" id="p-select-drafts">全选</button><button class="text-button" id="p-clear-drafts">清空选择</button></div>
    <div class="focus-list"><table><tbody>${rows||'<tr><td>导入包含封面大字报、发布标题、正文和话题的 .docx。</td></tr>'}</tbody></table></div></section>
    <section class="card"><div class="focus-heading"><h2>小红书账号</h2><button class="text-button" id="p-select-accounts">全选</button></div>
    <div class="focus-list">${accounts||'先在账号管理添加并登录账号。'}</div>
    <p class="hint">每条文案发到一个账号，按账号列表顺序循环分配。点击文案标题可核对和编辑。</p></section></div>
    <div class="publish-action"><span id="p-selection"></span><button class="btn primary" id="p-create">生成发布清单</button></div>
    <div class="card" id="p-qcard"></div><details class="publish-logs"><summary>排期、记录与日志</summary><div class="toolbar"><button class="btn" data-publish-tool="schedules">发布排期</button><button class="btn" data-publish-tool="records">发布记录</button><button class="btn" data-publish-tool="drafts">内容管理</button></div><div class="log-box" id="p-log"></div></details>`;
  const draftsInOrder = ()=>state.drafts.filter(d=>publishDraftsSelected.has(d.id));
  const accountsInOrder = ()=>state.accounts.filter(a=>state.pubAccounts.has(a.id));
  const update = ()=>{
    const drafts=draftsInOrder(), selected=accountsInOrder();
    document.getElementById('p-selection').textContent=`${drafts.length} 条文案 · ${selected.length} 个账号 · ${drafts.length} 次发布`;
    document.getElementById('p-create').disabled=!drafts.length||!selected.length;
  };
  document.querySelectorAll('#page input[data-did]').forEach(cb=>cb.onchange=()=>{
    cb.checked?publishDraftsSelected.add(+cb.dataset.did):publishDraftsSelected.delete(+cb.dataset.did);update();});
  document.querySelectorAll('#page input[data-aid]').forEach(cb=>cb.onchange=()=>{
    cb.checked?state.pubAccounts.add(+cb.dataset.aid):state.pubAccounts.delete(+cb.dataset.aid);update();});
  document.getElementById('p-select-drafts').onclick=()=>{state.drafts.forEach(d=>publishDraftsSelected.add(d.id));renderPublishFocus();};
  document.getElementById('p-clear-drafts').onclick=()=>{publishDraftsSelected.clear();renderPublishFocus();};
  document.getElementById('p-select-accounts').onclick=()=>{state.accounts.forEach(a=>state.pubAccounts.add(a.id));renderPublishFocus();};
  document.querySelectorAll('#page [data-edit]').forEach(button=>button.onclick=()=>focusDraftEdit(+button.dataset.edit));
  document.getElementById('p-import').onclick=async()=>{
    const button=document.getElementById('p-import');button.disabled=true;
    try {
      const response=await api('draft_import_docx');
      if (!response.ok) return toast(response.error,true);
      if (response.cancelled) return;
      (response.ids||[]).forEach(id=>publishDraftsSelected.add(id));
      toast(`导入 ${response.added} 条，重复 ${response.duplicates} 条`);await renderPublishFocus();
    } finally {if(button.isConnected)button.disabled=false;}
  };
  document.getElementById('p-create').onclick=async()=>{
    const response=await api('queue_create_many',draftsInOrder().map(d=>d.id),accountsInOrder().map(a=>a.id));
    if(!response.ok) return toast(response.error,true);
    toast('清单已生成，请核对后开始发布');await loadQueueCard();
  };
  document.querySelectorAll('[data-publish-tool]').forEach(button=>button.onclick=()=>showSection(button.dataset.publishTool));
  update();await loadQueueCard();
  clearInterval(queueTimer);clearInterval(logTimer);
  queueTimer=setInterval(()=>{if(curSection==='publish')loadQueueCard();},3000);
  logTimer=setInterval(async()=>{
    if(curSection!=='publish')return;
    const response=await api('publish_log',state.logCursor);
    if(curSection!=='publish'||!response.ok)return;
    state.logCursor=response.cursor;
    const box=document.getElementById('p-log');if(!box)return;
    for(const item of response.items){const row=document.createElement('div');row.textContent=`${item.t} ${item.msg}`;box.appendChild(row);}
  },2000);
}

async function focusDraftEdit(did) {
  const draft=state.drafts.find(d=>d.id===did);if(!draft)return;
  const data=await openModal('核对原文',`<div class="form-grid"><label>发布标题</label><input name="title" value="${esc(draft.title)}">
    <label>封面大字</label><textarea name="cover_text">${esc(draft.cover_text||'')}</textarea>
    <label>正文</label><textarea name="body" style="min-height:140px">${esc(draft.body)}</textarea>
    <label>话题</label><textarea name="topics">${esc(draft.topics)}</textarea></div>
    <p class="hint">封面使用小红书网页自带文字配图并随机选择已加载卡片；程序不会改写你的文字。标题过长等平台校验需人工修改。</p>`,'保存原文');
  if(!data)return;
  let media=[];try{media=JSON.parse(draft.media_json||'[]');}catch(_){return toast('附件数据异常，请先检查草稿',true);}
  const response=await api('draft_save',{...draft,...data,media,template:'xhs_builtin'});
  if(!response.ok)return toast(response.error,true);
  await renderPublishFocus();
}
RENDER.publish = renderPublishFocus;
renderPublish = renderPublishFocus;

TITLES.phone_desk='手机管理 · ADB';
RENDER.phone_desk=()=>{
 document.getElementById('page').innerHTML='<section class="card"><h2>Android 手机管理</h2><p class="hint">通过 USB 调试连接手机。设备、投屏和网络设置在独立手机工作台集中管理。</p><button class="btn primary" id="phone-open">打开手机工作台</button><p class="hint" id="phone-result" role="status"></p></section>';
 document.getElementById('phone-open').onclick=async()=>{
  const button=document.getElementById('phone-open');button.disabled=true;
  try{const response=await api('open_phone_desk');if(!response.ok)throw new Error(response.error);document.getElementById('phone-result').textContent=response.already_running?'手机工作台已打开，请切换到该窗口。':'已打开手机工作台。';}
  catch(error){toast(error.message,true);}finally{if(button.isConnected)button.disabled=false;}
 };
};

/* Message selection never depends on joining monitoring first. */
let messageEpoch=0,messageOpeningTimer=null,messageReadBusy=null,messageSendBusy=false;
function messageCurrent(epoch,aid,uid=null){return curSection==='messages'&&epoch===messageEpoch&&msgState.aid===aid&&(uid===null||msgState.curConv?.user_id===uid);}
function messageHint(text,error=false){const box=document.getElementById('m-connection');if(box){box.textContent=text;box.style.color=error?'var(--danger)':'var(--muted)';}}
function messageComposerEnabled(enabled){for(const id of ['m-reply','m-send','m-phrase']){const input=document.getElementById(id);if(input)input.disabled=!enabled;}}
async function renderMessagesFocus(){
 const epoch=++messageEpoch;clearInterval(msgTimer);clearTimeout(messageOpeningTimer);
 const [accounts,phrases]=await Promise.all([api('accounts_list'),api('phrases_list')]);
 if(curSection!=='messages'||epoch!==messageEpoch)return;
 if(!accounts.ok){document.getElementById('page').innerHTML=`<p class="hint">${esc(accounts.error||'账号加载失败')}</p>`;return;}
 state.accounts=accounts.items;state.phrases=phrases.ok?phrases.items:[];
 const previous=msgState.aid;
 msgState.aid=state.accounts.some(account=>account.id===previous)?previous:(state.accounts[0]?.id||null);
 msgState.convs=[];msgState.curConv=null;msgState.thread=[];
 document.getElementById('page').innerHTML=`<div class="toolbar"><select id="m-acct" aria-label="选择消息账号" style="max-width:240px">${state.accounts.map(account=>`<option value="${account.id}" ${account.id===msgState.aid?'selected':''}>${esc(account.nickname)}</option>`).join('')||'<option value="">暂无账号</option>'}</select>
 <button class="btn primary" id="m-open">打开消息页</button><button class="btn" id="m-refresh">刷新会话</button><span class="hint" id="m-quota"></span></div><p class="hint" id="m-connection" role="status">选择账号后加载会话。</p>
 <div class="message-layout"><section class="card message-conversations" id="m-convs"></section><section class="card message-chat"><h2 id="m-recipient">选择一条会话</h2><div id="m-thread" class="message-thread"></div>
 <div class="message-compose"><select id="m-phrase"><option value="">插入话术</option>${state.phrases.map(phrase=>`<option value="${phrase.id}">${esc(phrase.title)}</option>`).join('')}</select><textarea id="m-reply" placeholder="输入回复；点击发送后核对账号和收件人。" rows="3"></textarea><button class="btn primary" id="m-send">发送回复</button></div></section></div>
 <details class="secondary-info"><summary>后台轮询与连接说明</summary><p class="hint">当前界面用于小红书会话。网页消息入口不可用时会显示原因，请在平台 App 中处理。轮询会跳过手动使用中的浏览器，不能保证实时推送。</p><div class="toolbar"><button class="btn" id="m-add">为当前账号开启轮询</button><button class="btn" id="m-stopall">停止后台轮询</button><span id="m-engine" class="hint"></span></div></details>`;
 document.getElementById('m-acct').onchange=event=>messageSelectAccount(Number(event.target.value)||null);
 document.getElementById('m-refresh').onclick=()=>viewAccountConvs();
 document.getElementById('m-open').onclick=()=>messageOpenAccount();
 document.getElementById('m-send').onclick=()=>sendReply();
 document.getElementById('m-phrase').onchange=event=>{const phrase=state.phrases.find(item=>item.id===Number(event.target.value));if(phrase){const input=document.getElementById('m-reply');input.value+=(input.value?'\n':'')+phrase.content;}event.target.value='';};
 document.getElementById('m-add').onclick=async()=>{if(!msgState.aid)return;const response=await api('monitor_enable',msgState.aid);toast(response.ok?'已开启后台轮询':response.error,!response.ok);};
 document.getElementById('m-stopall').onclick=async()=>{const response=await api('monitor_stop_all');toast(response.ok?'已停止后台轮询':response.error,!response.ok);};
 messageComposerEnabled(false);await viewAccountConvs();
 msgTimer=setInterval(async()=>{if(curSection!=='messages'){clearInterval(msgTimer);return;}if(!document.hidden)await viewAccountConvs(true);},10000);
}
async function messageSelectAccount(aid){
 ++messageEpoch;clearTimeout(messageOpeningTimer);msgState.aid=aid;msgState.curConv=null;msgState.convs=[];msgState.thread=[];
 const select=document.getElementById('m-acct');if(select)select.value=String(aid||'');
 document.getElementById('m-thread').innerHTML='';document.getElementById('m-reply').value='';document.getElementById('m-recipient').textContent='选择一条会话';
 const open=document.getElementById('m-open');if(open)open.disabled=false;
 messageComposerEnabled(false);renderConvList();await viewAccountConvs();
}
async function messageOpenAccount(){
 const aid=msgState.aid,epoch=messageEpoch;if(!aid)return toast('先添加并选择账号',true);
 const button=document.getElementById('m-open');button.disabled=true;messageHint('正在打开消息页，请在该账号浏览器检查登录。');
 try{
  const response=await api('messages_open',aid);if(!response.ok)throw new Error(response.error);
  const deadline=Date.now()+260000;
  const poll=async()=>{
   if(!messageCurrent(epoch,aid))return;
   const status=await api('messages_open_status',aid);if(!messageCurrent(epoch,aid))return;
   if(!status.ok||status.state==='error'){messageHint(status.error||'消息页打开失败',true);button.disabled=false;return;}
   if(status.state==='ready'){button.disabled=false;await viewAccountConvs();return;}
   if(Date.now()>=deadline){button.disabled=false;messageHint('等待超时，请检查浏览器后重试。',true);return;}
   messageOpeningTimer=setTimeout(poll,1500);
  };await poll();
 }catch(error){if(messageCurrent(epoch,aid)){messageHint(error.message,true);button.disabled=false;}}
}
viewAccountConvs=async function(quiet=false){
 const aid=msgState.aid,epoch=messageEpoch;if(!aid){messageHint('先在账号管理添加账号。');return;}
 if(messageReadBusy===epoch)return;
 messageReadBusy=epoch;if(!quiet)messageHint('正在读取当前账号会话…');
 try{
  const response=await api('messages_conversations',aid);if(!messageCurrent(epoch,aid))return;
  if(!response.ok){messageHint(response.error||'会话读取失败',true);if(!msgState.convs.length)document.getElementById('m-convs').innerHTML='<p class="hint">会话尚未加载，请点击「打开消息页」或检查平台页面。</p>';return;}
  msgState.convs=response.items||[];renderConvList();messageHint(`已读取 ${msgState.convs.length} 条会话 · ${new Date().toLocaleTimeString()}`);
  if(msgState.curConv)await loadThread();
 }catch(error){if(messageCurrent(epoch,aid))messageHint(error.message,true);}
 finally{if(messageReadBusy===epoch)messageReadBusy=null;}
};
loadThread=async function(){
 const aid=msgState.aid,epoch=messageEpoch,conversation=msgState.curConv;if(!aid||!conversation)return;
 const uid=conversation.user_id;const composer=document.getElementById('m-reply');if(composer.dataset.uid!==uid){composer.value='';composer.dataset.uid=uid;}messageComposerEnabled(false);
 const response=await api('messages_thread',aid,uid);if(!messageCurrent(epoch,aid,uid))return;
 if(!response.ok){messageHint(response.error||'对话读取失败',true);return;}
 msgState.thread=response.items||[];document.getElementById('m-recipient').textContent=conversation.nickname||'当前会话';
 const box=document.getElementById('m-thread');box.innerHTML=msgState.thread.map(message=>`<div class="message-bubble ${message.direction==='outgoing'?'outgoing':''}">${esc(message.text||'[图片]')}</div>`).join('')||'<p class="hint">该会话暂无已读取消息。</p>';box.scrollTop=box.scrollHeight;
 messageComposerEnabled(true);
};
sendReply=async function(){
 if(messageSendBusy)return;
 const aid=msgState.aid,conversation=msgState.curConv,epoch=messageEpoch;
 const text=document.getElementById('m-reply').value.trim();if(!aid||!conversation||!text)return toast('先选择会话并填写回复',true);
 const uid=conversation.user_id,account=state.accounts.find(item=>item.id===aid);messageSendBusy=true;
 try{
  const confirmed=await openModal('核对回复',`<p>发送账号：${esc(account?.nickname||aid)}</p><p>收件人：${esc(conversation.nickname||uid)}</p><pre class="check">${esc(text)}</pre>`,'确认发送');
  if(!confirmed)return;
  if(!messageCurrent(epoch,aid,uid))return toast('账号或会话已经切换，回复未发送，请重新核对。',true);
  const response=await api('messages_send',aid,uid,conversation.nickname||'',text);
  if(!messageCurrent(epoch,aid,uid))return;
  if(!response.ok)return messageHint(response.error||'发送失败',true);
  document.getElementById('m-reply').value='';toast(response.msg||'已发送并核验');await loadThread();
 }catch(error){if(messageCurrent(epoch,aid,uid))messageHint(error.message,true);}finally{messageSendBusy=false;}
};
RENDER.messages=renderMessagesFocus;
renderMessages=renderMessagesFocus;
