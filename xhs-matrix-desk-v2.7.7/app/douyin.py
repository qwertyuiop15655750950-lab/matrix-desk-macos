# -*- coding: utf-8 -*-
"""抖音平台：选择器与页面地址。

抖音网页版：https://www.douyin.com
注意：抖音网页版功能弱于 App，发布/私信以实际页面结构为准，
选择器需真机校准。
"""
PLATFORM = "douyin"

URLS = {
    "home": "https://www.douyin.com/",
    # 扫码登录页
    "login": "https://www.douyin.com/login",
    # 创作者中心（发布）
    "creator_home": "https://creator.douyin.com/",
    "publish_video": "https://creator.douyin.com/content/publish",
    # 私信
    "chat": "https://www.douyin.com/message",
}

SELECTORS = {
    # 登录态判断：右上角头像/用户名
    "login_avatar": "[data-e2e='avatar'], .avatar, [class*='avatar']",
    # 首页
    "home_feed": "[data-e2e='feed-item'], [class*='feed-item']",
    # 发布页
    "publish": {
        # 创作者中心发布页的元素（需真机校准）
        "upload_btn": "input[type='file']",
        "title_input": "[placeholder*='标题'], [class*='title-input']",
        "publish_btn": "button:has-text('发布')",
    },
    # 私信
    "message": {
        "conv_list": "[class*='conversation-list'], [class*='session-list']",
        "conv_item": "[class*='conversation-item'], [class*='session-item']",
        "msg_list": "[class*='message-list']",
        "msg_item": "[class*='message-item']",
        "input": "[contenteditable='true'], textarea[placeholder*='发送']",
        "send_btn": "button:has-text('发送')",
    },
    # 评论区
    "comment": {
        "list": "[class*='comment-list']",
        "item": "[class*='comment-item']",
        "author": "[class*='author'], [class*='user-name']",
        "text": "[class*='comment-text'], [class*='content']",
    },
}


def is_logged_in(page):
    """判断抖音是否已登录：找头像元素。"""
    try:
        return page.locator(SELECTORS["login_avatar"]).first.is_visible(
            timeout=5000)
    except Exception:
        return False
