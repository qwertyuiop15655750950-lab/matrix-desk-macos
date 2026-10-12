"""批量主页/作品采集 API；使用工作台自己的采集器。"""
import csv
import io
from pathlib import Path
from . import account_hub
from . import profiles
from .config import DATA_DIR
from .share_links import parse_shares, resolve_share, classify
from .comment_selectors import PLATFORM_HOSTS

PROFILE_SCRIPT = r'''config => {
  const found=[], seen=new Set();
  for(const anchor of document.querySelectorAll('a[href]')) {
    let parsed; try { parsed=new URL(anchor.href,location.href); } catch(_) { continue; }
    if(parsed.protocol!=='https:' || !config.hosts.includes(parsed.hostname))continue;
    const match=parsed.pathname.match(config.platform==='douyin'?/^\/video\/([A-Za-z0-9_-]+)\/?$/:/^\/(?:explore|discovery\/item)\/([A-Za-z0-9_-]+)\/?$/);
    if(!match||seen.has(match[1]))continue;
    const card=anchor.closest('article,li,section,[data-e2e="user-post-item"],.note-item');
    const badge=card?.querySelector('[class*="pin"],[class*="top-tag"],[data-e2e="pinned"]');
    if(badge?.textContent.includes('置顶'))continue;
    seen.add(match[1]);
    found.push({url:parsed.href, canonical:(config.platform==='douyin'?'https://www.douyin.com/video/':'https://www.xiaohongshu.com/explore/')+match[1]});
  }
  return {works:found, title:document.title.slice(0,300)};
}'''


class BatchAPI:
    def batch_preview(self, text):
        try:
            result = parse_shares(text)
            # Access parameters are kept only by create, not displayed in preview.
            return self._ok(**{**result, 'items':[{k:v for k,v in row.items() if k != 'url'} for row in result['items']]})
        except Exception as error:
            return self._err(error)

    def batch_create(self, text, accounts, words, works=20, comments=1000):
        try:
            entries = parse_shares(text)['items']
            if not isinstance(accounts, dict): raise ValueError('请选择各平台已登录账号')
            selected = {}
            for platform in {entry['platform'] for entry in entries}:
                account = account_hub.get(self.conn, platform, self._browser_account_id(accounts.get(platform)))
                selected[platform] = account['id']
            return self._ok(id=self._batch.create(entries, selected, words, works, comments))
        except Exception as error:
            return self._err(error)

    def batch_list(self):
        return self._ok(items=self._batch.list())

    def batch_action(self, bid, action):
        try: return self._ok(status=self._batch.action(bid, action))
        except Exception as error: return self._err(error)

    def batch_export(self, bid):
        try:
            rows = self._batch.results(bid)
            output = io.StringIO()
            fields = ['platform','account_id','source_url','comment_id','user_id','nickname','content','matched_words','created_at']
            writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({k: "'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for k,v in row.items()})
            return self._ok(csv=output.getvalue(), count=len(rows))
        except Exception as error: return self._err(error)

    def _run_batch_source(self, source, config):
        try:
            entry = resolve_share(source)
            account = account_hub.get(self.conn, entry['platform'], config['accounts'][entry['platform']])
            def run(page):
                if entry['kind'] == 'profile':
                    from .page_guard import check_page
                    page.goto(entry['url'], wait_until='domcontentloaded', timeout=30000)
                    check_page(page)
                    if classify(page.url)['canonical'] != entry['canonical']:
                        raise ValueError('未进入目标主页，请完成登录并检查账号链接')
                    found = {}
                    title = ''
                    for _ in range(5):
                        page.wait_for_timeout(1200)
                        check_page(page)
                        result = page.evaluate(PROFILE_SCRIPT, {'platform': entry['platform'], 'hosts': sorted(PLATFORM_HOSTS[entry['platform']])})
                        title = result['title']
                        for work in result['works']:
                            found.setdefault(work['canonical'], work)
                        if len(found) >= config['works']: break
                        page.evaluate('window.scrollBy(0, 800)')
                    if not found: raise ValueError('未发现公开作品，请检查登录、主页作品页和页面选择器')
                    snapshot = self._read_batch_metrics(page,entry)
                    return {'ok':True,'metrics':snapshot,'works':list(found.values())[:config['works']], 'title':title,
                            'discovered':len(found),'warning':'按主页作品展示顺序选取，能识别的置顶作品已跳过；未核实每条发布时间。'}
                from .builtin_crawler import collect
                result = collect(page, entry['url'], config['words'], collect_all=True, max_comments=config['comments'])
                if result.get('ok') and not result.get('blocked'):
                    try:
                        result['metrics']=self._read_batch_metrics(page,entry)
                    except Exception as error:
                        from .page_guard import PageBlocked
                        if isinstance(error,PageBlocked):
                            result.update({k:v for k,v in error.result.items() if k not in ('items','read')})
                        else:
                            result['metric_error']=str(error)[:500]
                result['source_url'] = entry['canonical']
                result['warning'] = '本次读取已加载的公开评论；未读到末尾或达到上限时不代表全部评论。'
                return result
            if entry['platform'] == 'xiaohongshu':
                def selected(browser):
                    pages = [p for p in browser.ctx.pages if not p.is_closed()]
                    if not pages: raise ValueError('账号浏览器已关闭')
                    visible = [p for p in pages if p.evaluate('document.visibilityState') == 'visible']
                    return run(visible[-1] if visible else pages[-1])
                return self._call_xhs_browser(account['id'], selected, timeout=180, require_open=True)
            return self._dy_browsers.perform(account, run, timeout=180, require_open=True)
        except Exception as error:
            from .page_guard import PageBlocked
            if isinstance(error, PageBlocked): return error.result
            raise
        finally:
            self.close_thread_conn()

    @staticmethod
    def _read_batch_metrics(page,entry):
        if entry['platform']!='xiaohongshu': return None
        from .account_metrics import read_metrics
        return read_metrics(page,entry)
