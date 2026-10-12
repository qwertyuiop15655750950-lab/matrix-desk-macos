"""抖音/小红书分享文案和账号主页识别；仅访问官方 HTTPS 域名。"""
import re
from urllib.parse import urlsplit
import urllib.request
from .comment_selectors import PLATFORM_HOSTS

HOSTS = set().union(*PLATFORM_HOSTS.values(), {'v.douyin.com','xhslink.com','www.xhslink.com'})
REDIRECT_HOSTS = HOSTS | {'iesdouyin.com', 'www.iesdouyin.com'}


def classify(url):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in HOSTS or parts.port not in (None, 443) or parts.username or parts.password:
        raise ValueError('链接须为抖音或小红书的官方 HTTPS 分享链接')
    platform = 'xiaohongshu' if parts.hostname in PLATFORM_HOSTS['xiaohongshu'] or parts.hostname in {'xhslink.com','www.xhslink.com'} else 'douyin'
    if parts.hostname in {'v.douyin.com', 'xhslink.com', 'www.xhslink.com'}:
        kind = 'share'
        canonical = f'https://{parts.hostname}{parts.path.rstrip("/")}/'
    else:
        profile = re.fullmatch(r'/user/(?:profile/)?([A-Za-z0-9_-]+)/?', parts.path)
        content = re.fullmatch(r'/(?:video|explore|discovery/item)/([A-Za-z0-9_-]+)/?', parts.path)
        if profile:
            kind = 'profile'
            canonical = ('https://www.xiaohongshu.com/user/profile/' if platform == 'xiaohongshu' else 'https://www.douyin.com/user/') + profile[1]
        elif content:
            kind = 'content'
            canonical = ('https://www.xiaohongshu.com/explore/' if platform == 'xiaohongshu' else 'https://www.douyin.com/video/') + content[1]
        else:
            raise ValueError('请提供账号主页或具体作品链接')
    return {'platform': platform, 'kind': kind, 'url': url, 'canonical': canonical}


def parse_shares(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('分享文案最大 2 MB')
    entries, rejected, seen, duplicates = [], [], set(), 0
    for match in re.finditer(r'https?://[A-Za-z0-9_./?=&%#:+~-]+', text):
        url = match[0].rstrip('.,;')
        try:
            row = classify(url)
        except ValueError as error:
            rejected.append(str(error))
            continue
        if row['canonical'] in seen:
            duplicates += 1
            continue
        seen.add(row['canonical'])
        # Do not include access query parameters in displayed labels or exports.
        row['label'] = row['canonical']
        entries.append(row)
        if len(entries) > 100:
            raise ValueError('一次最多 100 个账号主页或作品链接')
    if not entries:
        raise ValueError('未识别到账号主页或作品链接，可整段粘贴分享文案或表格中的链接列')
    return {'items': entries, 'duplicates': duplicates, 'rejected': len(rejected)}


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, request, response, code, message, headers, target):
        parts = urlsplit(target)
        if parts.scheme != 'https' or parts.hostname not in REDIRECT_HOSTS or parts.port not in (None, 443) or parts.username or parts.password:
            raise ValueError('分享链接跳转到了不支持的地址，未继续访问')
        return super().redirect_request(request, response, code, message, headers, target)


def resolve_share(row):
    if row['kind'] != 'share':
        return row
    request = urllib.request.Request(row['url'], headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.build_opener(OfficialRedirect()).open(request, timeout=15) as response:
        final = response.url
        response.read(4096)
    parts = urlsplit(final)
    if parts.hostname in {'iesdouyin.com', 'www.iesdouyin.com'}:
        match = re.search(r'/(?:share/)?video/(\d+)', parts.path)
        if not match:
            raise ValueError('短链接未解析到作品，请粘贴浏览器中的完整作品地址')
        final = 'https://www.douyin.com/video/' + match[1]
    result = classify(final)
    if result['kind'] == 'share' or result['platform'] != row['platform']:
        raise ValueError('短链接未跳转到同平台的账号或作品，请提供完整链接')
    return result
