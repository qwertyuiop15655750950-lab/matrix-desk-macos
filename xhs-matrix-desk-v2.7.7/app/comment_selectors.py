"""Public loaded-comment DOM selectors shared by browser and offline extractors.

These selectors are a starting point for the platform's current DOM, not a
promise that any particular live platform revision uses these class names.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PLATFORM_HOSTS = {
    'douyin': frozenset(('www.douyin.com', 'douyin.com')),
    'xiaohongshu': frozenset(('www.xiaohongshu.com', 'xiaohongshu.com', 'www.rednote.com', 'rednote.com')),
}

COMMENT_SELECTORS = {
    'douyin': {
        'item': '[data-e2e="comment-item"], [class*="comment-item"]',
        'text': ('[data-e2e="comment-content"]', '[class*="comment-text"]',
                 '[class*="comment-content"]', '[class*="content"]'),
        'author': ('[data-e2e="comment-author"]', '[class*="author"]',
                   '[class*="user-name"]', 'a[href*="/user/"]'),
        'list': '[data-e2e="comment-list"], [class*="comment-list"]',
        'time': ('[data-e2e="comment-time"]', '[class*="comment-time"]',
                 'time', '.date'),
    },
    'xiaohongshu': {
        'item': '.comment-item, .parent-comment, [class*="comment-item"]',
        'text': ('.content .note-text', '.content', '[class*="comment-content"]',
                 '[class*="comment-text"]'),
        'author': ('.author .name', '.author', '[class*="user-name"]',
                   'a[href*="/user/profile/"]'),
        'list': '.comments-container, .comment-list, [class*="comment-list"]',
        'time': ('.comment-time', '.date', 'time', '.time'),
    },
}

COMMENT_ID_ATTRIBUTES = ('data-comment-id', 'data-commentid', 'data-comment_id', 'data-cid')
USER_ID_ATTRIBUTES = ('data-user-id', 'data-userid', 'data-uid')
TIME_ATTRIBUTES = ('datetime', 'data-created-at', 'data-time', 'data-timestamp')
PUBLIC_COMMENT_ATTRIBUTES = frozenset({
    'class', 'id', 'data-e2e', 'href', 'title',
    *COMMENT_ID_ATTRIBUTES, *USER_ID_ATTRIBUTES, *TIME_ATTRIBUTES,
})
CONTENT_PARAMETERS = frozenset(('id', 'video_id', 'note_id', 'modal_id', 'aweme_id', 'vid'))


def comment_source_url(value, platform):
    """Require the selected platform's HTTPS host and remove private query state."""
    if platform not in PLATFORM_HOSTS:
        raise ValueError('评论采集仅支持小红书或抖音')
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('评论页面网址无效')
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or parts.hostname not in PLATFORM_HOSTS[platform]
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('评论页面必须是所选平台的 HTTPS 网址')
    query = urlencode([(key, item) for key, item in parse_qsl(parts.query)
                       if key.lower() in CONTENT_PARAMETERS and len(item) <= 500])
    return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', query, ''))


def comment_schema():
    """Crawl4AI schema for individually scoped, marked public comment elements.

The worker marks original chosen nodes, without synthesizing identity values.
This keeps CSS extraction from borrowing a nested reply's or mention's author.
"""
    attribute = lambda name, attr, selector=None: {
        'name': name, 'type': 'attribute', 'attribute': attr, 'default': '',
        **({'selector': selector} if selector else {}),
    }
    return {
        'name': 'Loaded public comments',
        'baseSelector': '[data-matrix-comment-row]',
        'baseFields': [
            attribute('comment_id', 'data-comment-id'),
            attribute('item_user_id', 'data-user-id'),
            attribute('item_created_at', 'data-created-at'),
        ],
        'fields': [
            {'name': 'content', 'selector': '[data-matrix-comment-content]', 'type': 'text', 'default': ''},
            {'name': 'nickname', 'selector': '[data-matrix-comment-author]', 'type': 'text', 'default': ''},
            attribute('author_user_id', 'data-user-id', '[data-matrix-comment-author]'),
            attribute('profile_user_id', 'data-user-id', '[data-matrix-comment-author-link]'),
            attribute('author_href', 'href', '[data-matrix-comment-author-link]'),
            attribute('time_attribute', 'data-created-at', '[data-matrix-comment-time]'),
            {'name': 'time_text', 'selector': '[data-matrix-comment-time]', 'type': 'text', 'default': ''},
        ],
    }
