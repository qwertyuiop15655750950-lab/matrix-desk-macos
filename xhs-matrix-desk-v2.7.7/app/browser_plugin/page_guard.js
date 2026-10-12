/* Detect visible access gates. Never interact with or solve a challenge. */
(() => {
  'use strict';
  globalThis.__matrixPlatformHosts = {
    douyin:['www.douyin.com','douyin.com'],
    xiaohongshu:['www.xiaohongshu.com','xiaohongshu.com','www.rednote.com','rednote.com'],
  };
  globalThis.__matrixReadAccessGate = () => {
    const content = '[data-e2e="comment-item"],.comment-item,.parent-comment';
    const visible = node => node.getClientRects().length > 0
      && getComputedStyle(node).visibility !== 'hidden'
      && getComputedStyle(node).display !== 'none';
    const blocked = (reason, error) => ({blocked:true, stop_reason:reason, error, requires_review:true});
    const classify = text => {
      text = text.replace(/\s+/g, '');
      if (/区域性显示调整|區域性顯示調整|目前在特定地区不对其他使用者显示|目前在特定地區不對其他使用者顯示|部分功能目前在特定地区无法使用|部分功能目前在特定地區無法使用/.test(text))
        return blocked('region_restricted', '页面出现地区功能限制，自动任务已停止；请人工查看通知或申诉，不会自动重试');
      if (/账号异常提醒|帳號異常提醒|账号已封禁|帳號已封禁|异常浏览行为|不常见的浏览行为|与真人操作习惯不一致/.test(text))
        return blocked('account_warning', '页面出现账号或浏览异常提醒，采集已暂停；请人工检查账号状态');
      if (/访问过于频繁|访问频繁|请求频繁|操作频繁|访问受限|TooManyRequests/i.test(text))
        return blocked('rate_limited', '页面提示访问频繁或受限，采集已暂停；请人工检查后再继续');
      if (/安全验证|滑块验证|拖动滑块|完成验证|请输入验证码|captcha/i.test(text))
        return blocked('captcha', '页面需要安全验证，采集已暂停；请在浏览器中人工处理');
      if (/扫码登录|登录后查看|登录后才能|请先登录|登录已失效|登录已过期/.test(text))
        return blocked('login_required', '页面需要登录或登录已失效，采集已暂停；请人工扫码登录');
      return null;
    };
    const roots = document.querySelectorAll('[role="dialog"],[role="alert"],[aria-modal="true"],.login-container,.login-modal,[data-e2e="login-modal"],[class*="captcha"],[id*="captcha"],[data-e2e="verify-container"],.error-page');
    for (const node of roots) {
      if (!visible(node) || node.matches(content) || node.querySelector(content)) continue;
      const text = (node.innerText || '').trim().slice(0,1600);
      const result = classify(text);
      if (result) return result;
      const marker = `${node.id} ${node.className}`;
      if (/captcha/i.test(marker)) return blocked('captcha','页面出现验证码控件，采集已暂停；请人工处理');
    }
    for (const frame of document.querySelectorAll('iframe[src]')) {
      if (visible(frame) && /captcha|\/challenge(?:[/?]|$)/i.test(frame.getAttribute('src')))
        return blocked('captcha','页面出现安全验证窗口，采集已暂停；请人工处理');
    }
    // Full-page gates are checked only without content cards/comments. This
    // avoids treating a user comment about a warning as a platform warning.
    if (!document.querySelector(`${content},.note-item,[data-e2e="user-post-item"],video`)) {
      const text = (document.body?.innerText || '').trim();
      if (text.length <= 1600) {
        const result = classify(`${document.title}\n${text}`);
        if (result) return result;
      }
    }
    return {blocked:false};
  };
})();
