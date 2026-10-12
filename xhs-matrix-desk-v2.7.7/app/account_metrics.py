"""公开账号身份、可见指标与跨日快照。只读页面，不请求私有接口。"""
import json
import re
from datetime import datetime
from .share_links import classify
from .db import _now

SCHEMA = '''
CREATE TABLE IF NOT EXISTS metric_identities(account_id INTEGER PRIMARY KEY,profile_url TEXT NOT NULL,user_id TEXT NOT NULL,public_id TEXT NOT NULL DEFAULT '',name TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metric_snapshots(id INTEGER PRIMARY KEY,batch_id INTEGER NOT NULL,platform TEXT NOT NULL,kind TEXT NOT NULL,canonical TEXT NOT NULL,owner_profile TEXT NOT NULL DEFAULT '',observer_id INTEGER NOT NULL,name TEXT NOT NULL DEFAULT '',title TEXT NOT NULL DEFAULT '',metrics TEXT NOT NULL,sampled_at TEXT NOT NULL,UNIQUE(batch_id,platform,canonical));
CREATE INDEX IF NOT EXISTS metric_snapshot_lookup ON metric_snapshots(platform,canonical,sampled_at,id);
CREATE TABLE IF NOT EXISTS metric_settings(id INTEGER PRIMARY KEY CHECK(id=1),config TEXT NOT NULL,last_day TEXT NOT NULL DEFAULT '',last_batch INTEGER,note TEXT NOT NULL DEFAULT '');
'''

# Scope the owner link to navigation, never infer it from the current content's author.
IDENTITY_SCRIPT = r'''() => {
 const visible=e=>e && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none';
 const candidates=[...document.querySelectorAll('aside a[href],.side-bar a[href],#sidebar a[href],li.user a[href]')];
 const own=candidates.find(a=>visible(a)&&/^\/(?:user\/profile)\/[A-Za-z0-9_-]+\/?$/.test(new URL(a.href,location.href).pathname)&&(/^(我|Me|Profile)$/i.test(a.innerText.trim())||a.closest('li.user,.user.side-bar-component')));
 return own ? {profile_url:own.href} : null;
}'''
METRICS_SCRIPT = r'''kind => {
 const visible=e=>e && e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none';
 const first=selectors=>[...document.querySelectorAll(selectors)].find(visible);
 const text=selectors=>first(selectors)?.innerText?.trim()||'';
 const values={};
 if(kind==='profile'){
  for(const item of document.querySelectorAll('.user-interactions .interaction,.user-info .interaction')){
   if(!visible(item))continue;
   const label=item.querySelector('.name,.label')?.innerText?.trim();
   const count=item.querySelector('.count')?.innerText?.trim();
   if(label==='粉丝'||label==='Followers')values.followers=count;
   if(label==='获赞与收藏'||label==='Likes & Saves')values.likes_and_saves=count;
   if(label==='获赞'||label==='Likes')values.likes=count;
   if(label==='收藏'||label==='Saves')values.saves=count;
  }
 }else{
  for(const [key,selector] of Object.entries({likes:'.interact-container .like-wrapper .count,.interactions .like-wrapper .count',saves:'.interact-container .collect-wrapper .count,.interactions .collect-wrapper .count',comments:'.interact-container .chat-wrapper .count,.comments-container .total'})){
   const count=text(selector);if(count)values[key]=count;
  }
 }
 const info=text('.user-info .user-redId,.user-info .user-red-id,.user-info .user-id');
 return {url:location.href,name:text(kind==='profile'?'.user-info .user-name,.user-info .username':'.author-wrapper .name'),title:text('#detail-title,.note-content .title')||document.title,public_id:info,values};
}'''


def ensure(conn):
    conn.executescript(SCHEMA)


def count_value(raw):
    """Keep missing values null, and mark rounded display counts as approximate."""
    label = str(raw or '').strip()
    match = re.fullmatch(r'(?:共\s*)?(\d[\d,]*(?:\.\d+)?)\s*(万|亿|[kKwWmM])?\s*\+?\s*(?:条评论|条|人)?', label)
    if not match:
        return {'value': None, 'raw': label, 'approximate': False}
    unit = match[2] or ''
    if not unit and '.' in match[1]:return {'value':None,'raw':label,'approximate':False}
    multiplier = {'万':10000,'亿':100000000,'k':1000,'w':10000,'m':1000000}.get(unit.lower(),1)
    return {'value':int(float(match[1].replace(',',''))*multiplier),'raw':label,'approximate':bool(unit or '+' in label)}


def read_metrics(page, entry):
    from .page_guard import check_page
    check_page(page)
    if classify(page.url)['canonical'] != entry['canonical']:
        raise ValueError('页面已离开目标账号或作品，未保存指标')
    raw = page.evaluate(METRICS_SCRIPT, entry['kind'])
    if not isinstance(raw, dict) or classify(raw.get('url',''))['canonical'] != entry['canonical']:
        raise ValueError('指标读取期间页面发生变化，未保存')
    keys = ('followers','likes','saves','likes_and_saves') if entry['kind']=='profile' else ('likes','saves','comments')
    return {'name':str(raw.get('name') or '')[:200], 'title':str(raw.get('title') or '')[:500],
            'public_id':str(raw.get('public_id') or '')[:200],
            'values':{key:count_value((raw.get('values') or {}).get(key)) for key in keys}}


def save_identity(conn, aid, profile_url, details=None):
    entry = classify(profile_url)
    if entry['platform']!='xiaohongshu' or entry['kind']!='profile':
        raise ValueError('账号身份必须是小红书或 REDnote 官方主页')
    public_id = str((details or {}).get('public_id') or '')
    match = re.search(r'(?:小红书号|rednote ID)\s*[:：]\s*([A-Za-z0-9_.-]+)', public_id, re.I)
    public_id = match[1] if match else ''
    with conn:
        conn.execute('INSERT INTO metric_identities(account_id,profile_url,user_id,public_id,name,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET profile_url=excluded.profile_url,user_id=excluded.user_id,public_id=CASE WHEN excluded.public_id!=\'\' THEN excluded.public_id ELSE CASE WHEN metric_identities.user_id=excluded.user_id THEN metric_identities.public_id ELSE \'\' END END,name=excluded.name,updated_at=excluded.updated_at',
                     (int(aid),entry['canonical'],entry['canonical'].rsplit('/',1)[-1],public_id,str((details or {}).get('name') or '')[:200],_now()))


def record(conn, source, config, batch_id, metrics):
    if not isinstance(metrics, dict) or not metrics.get('values'):
        return
    # Results without a readable metric are retained as unavailable snapshots.
    canonical = classify(source['canonical'])['canonical']
    conn.execute('INSERT OR REPLACE INTO metric_snapshots(batch_id,platform,kind,canonical,owner_profile,observer_id,name,title,metrics,sampled_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (batch_id,source['platform'],source['kind'],canonical,source.get('owner_profile',''),int(config['accounts'][source['platform']]),str(metrics.get('name') or '')[:200],str(metrics.get('title') or '')[:500],json.dumps(metrics['values'],ensure_ascii=False),_now()))


def dashboard(conn):
    rows = [dict(row) for row in conn.execute('WITH ranked AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY platform,canonical ORDER BY sampled_at DESC,id DESC) rank FROM metric_snapshots) SELECT * FROM ranked WHERE rank=1 ORDER BY sampled_at DESC,id DESC')]
    output=[]
    for row in rows:
        values=json.loads(row['metrics'])
        day=row['sampled_at'][:10]
        previous=conn.execute('SELECT * FROM metric_snapshots WHERE platform=? AND canonical=? AND sampled_at<? ORDER BY sampled_at DESC,id DESC LIMIT 1',(row['platform'],row['canonical'],day)).fetchone()
        old_values=json.loads(previous['metrics']) if previous else {}
        delta={}; approximate={}
        for name,value in values.items():
            old=old_values.get(name,{})
            delta[name]=value['value']-old['value'] if value.get('value') is not None and old.get('value') is not None else None
            approximate[name]=bool(value.get('approximate') or old.get('approximate'))
        output.append({**row,'metrics':values,'changes':delta,'change_approximate':approximate,'comparison_day':previous['sampled_at'][:10] if previous else '',
                       'day_gap':(datetime.fromisoformat(day)-datetime.fromisoformat(previous['sampled_at'][:10])).days if previous else None})
    return output
