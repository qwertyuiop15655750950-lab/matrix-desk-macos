# -*- coding: utf-8 -*-
"""全局配置：路径、选择器加载、中文字体发现。只用标准库。"""
import json
import os
import subprocess

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
POSTER_DIR = os.path.join(DATA_DIR, "posters")
ERROR_SHOTS_DIR = os.path.join(DATA_DIR, "error_shots")
# 页面操作超时（毫秒）：goto 30s / 点击 10s / load 20s
PAGE_GOTO_TIMEOUT = 30000
PAGE_CLICK_TIMEOUT = 10000
PAGE_LOAD_TIMEOUT = 20000
DB_PATH = os.path.join(DATA_DIR, "desk.db")
CONFIG_DIR = os.path.join(APP_DIR, "config")
SELECTORS_PATH = os.path.join(CONFIG_DIR, "selectors.json")

APP_NAME = "多平台矩阵工作台"
APP_VERSION = "2.8.3"


def ensure_dirs():
    for d in (DATA_DIR, PROFILES_DIR, MEDIA_DIR, POSTER_DIR,
            ERROR_SHOTS_DIR):
        os.makedirs(d, exist_ok=True)


def load_selectors():
    """读取选择器配置；文件缺失则返回内置默认。"""
    try:
        with open(SELECTORS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default_selectors()


def default_selectors():
    # 注意：小红书页面 DOM 会变，这些选择器以 config/selectors.json 为准，
    # 可通过发布器的选择器自检功能按真站校准。
    return {
        "creator_home": "https://creator.xiaohongshu.com",
        "xhs_home": "https://www.rednote.com",
        "selectors": {
            "login_avatar": "img.avatar, .user-avatar img",
            "publish_title": "input[placeholder*='标题'], textarea[placeholder*='标题']",
            "publish_content": "[contenteditable='true'], textarea[placeholder*='正文']",
            "publish_topic": "input[placeholder*='话题']",
            "publish_upload": "input[type='file']",
            "publish_button": "button:has-text('发布')",
        },
    }


def find_cjk_font(bold=True):
    """按优先级找中文字体；允许环境变量 XHS_FONT 覆盖。"""
    env_font = os.environ.get("XHS_FONT")
    if env_font and os.path.exists(env_font):
        return env_font
    candidates = [
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Songti.ttc",
        # Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    # Windows
    windir = os.environ.get("WINDIR", r"C:\Windows")
    candidates += [
        os.path.join(windir, "Fonts", "msyhbd.ttc"),  # 微软雅黑 Bold
        os.path.join(windir, "Fonts", "msyh.ttc"),  # 微软雅黑
        os.path.join(windir, "Fonts", "simhei.ttf"),  # 黑体
        os.path.join(windir, "Fonts", "simsun.ttc"),  # 宋体
    ]
    if not bold:
        candidates = [c.replace("Bold", "Regular") for c in candidates] + candidates
    for c in candidates:
        if os.path.exists(c):
            return c
    # 最后兜底：fc-list 找一个带 CJK 的
    try:
        out = subprocess.run(
            ["fc-list", ":lang=zh", "file"], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines():
            path = line.strip().split(":")[0]
            if os.path.exists(path):
                return path
    except Exception:
        pass
    return None
