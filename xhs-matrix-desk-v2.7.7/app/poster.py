# -*- coding: utf-8 -*-
"""大字报生成器：输入文字 -> 按模板排版 -> 输出 1080x1440 PNG 图文。
复刻小红书 App 内大字报的视觉效果（模板为自研复刻，非调用小红书内置引擎）。
只依赖 Pillow。"""
import os
import re
import unicodedata

from PIL import Image, ImageDraw, ImageFont

from .config import POSTER_DIR, find_cjk_font

W, H = 1080, 1440

# 去掉彩色 emoji（中文字体无彩色 emoji 字形，会显示为方框）
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)


def clean_text(text):
    text = _EMOJI_RE.sub("", text or "")
    # 去掉其它控制字符，保留换行
    return "".join(
        ch for ch in text if ch == "\n" or not unicodedata.category(ch).startswith("C")
    )


TEMPLATES = {
    "rice": {
        "name": "米白深青",
        "bg": (247, 243, 235),
        "fg": (2, 49, 68),
        "accent": (2, 49, 68),
        "muted": (120, 130, 135),
        "title_size": 76,
        "body_size": 56,
        "align": "center",
        "gradient": False,
    },
    "classic": {
        "name": "经典黄黑",
        "bg": (255, 214, 64),
        "fg": (20, 20, 20),
        "accent": (200, 30, 30),
        "muted": (90, 80, 60),
        "title_size": 80,
        "body_size": 58,
        "align": "center",
        "gradient": False,
    },
    "dark": {
        "name": "暗夜高级",
        "bg": (18, 20, 24),
        "fg": (240, 238, 232),
        "accent": (255, 200, 90),
        "muted": (140, 145, 150),
        "title_size": 76,
        "body_size": 56,
        "align": "center",
        "gradient": False,
    },
    "morandi": {
        "name": "莫兰迪灰粉",
        "bg": (232, 222, 214),
        "fg": (74, 64, 60),
        "accent": (160, 90, 80),
        "muted": (140, 125, 118),
        "title_size": 74,
        "body_size": 54,
        "align": "center",
        "gradient": False,
    },
    "mint": {
        "name": "清新薄荷",
        "bg_top": (214, 245, 232),
        "bg_bottom": (168, 224, 205),
        "fg": (22, 70, 58),
        "accent": (16, 120, 95),
        "muted": (110, 150, 140),
        "title_size": 76,
        "body_size": 56,
        "align": "center",
        "gradient": True,
    },
}


def list_templates():
    return [{"id": k, "name": v["name"]} for k, v in TEMPLATES.items()]


def _load_font(size):
    path = find_cjk_font(bold=True)
    if not path:
        raise RuntimeError("未找到中文字体，请设置环境变量 XHS_FONT 指向 .ttf/.ttc 字体文件")
    # .ttc 取第一个字形即可（Noto CJK 各语言子字体字形覆盖一致）
    for index in (0, 1, 2, 3):
        try:
            return ImageFont.truetype(path, size, index=index)
        except Exception:
            continue
    return ImageFont.truetype(path, size)


def _wrap(draw, text, font, max_width):
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        buf = ""
        for ch in para:
            if draw.textlength(buf + ch, font=font) <= max_width:
                buf += ch
            else:
                lines.append(buf)
                buf = ch
        lines.append(buf)
    return lines


def _bg_image(tpl):
    if tpl.get("gradient"):
        top = Image.new("RGB", (W, 1), tpl["bg_top"])
        bottom = Image.new("RGB", (W, 1), tpl["bg_bottom"])
        img = Image.new("RGB", (W, H))
        for y in range(H):
            r = int(y / (H - 1) * 255)
            band = Image.blend(top, bottom, r / 255).resize((W, 1))
            img.paste(band, (0, y))
        return img
    return Image.new("RGB", (W, H), tpl["bg"])


def generate_poster(text, template="rice", title="", out_dir=None, basename="poster"):
    """生成大字报图片，返回 PNG 路径列表（文字过长自动分页）。"""
    if template not in TEMPLATES:
        raise ValueError(f"未知模板: {template}，可选: {', '.join(TEMPLATES)}")
    tpl = TEMPLATES[template]
    text = clean_text(text)
    if not text.strip() and not title.strip():
        raise ValueError("文字内容不能为空")

    out_dir = out_dir or POSTER_DIR
    os.makedirs(out_dir, exist_ok=True)

    font_title = _load_font(tpl["title_size"])
    font_body = _load_font(tpl["body_size"])
    font_small = _load_font(36)

    margin_x = 90
    max_w = W - margin_x * 2
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, text, font_body, max_w)

    line_h = int(tpl["body_size"] * 1.7)
    top_pad = 150
    bottom_pad = 130
    title_h = int(tpl["title_size"] * 1.9) if title.strip() else 0
    usable = H - top_pad - bottom_pad - title_h
    per_page = max(1, usable // line_h)
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    total = len(pages)

    paths = []
    for idx, page_lines in enumerate(pages, start=1):
        img = _bg_image(tpl)
        d = ImageDraw.Draw(img)
        y = top_pad
        if title.strip():
            d.text((W / 2, y), title.strip(), font=font_title,
                   fill=tpl["accent"], anchor="ma")
            # 标题下装饰线
            tw = d.textlength(title.strip(), font=font_title)
            d.line([(W / 2 - tw / 2, y + tpl["title_size"] * 1.35),
                    (W / 2 + tw / 2, y + tpl["title_size"] * 1.35)],
                   fill=tpl["accent"], width=5)
            y += title_h
        for ln in page_lines:
            if ln:
                d.text((W / 2, y), ln, font=font_body, fill=tpl["fg"], anchor="ma")
            y += line_h
        # 页脚
        foot = f"{idx}/{total}"
        d.text((W / 2, H - 80), foot, font=font_small, fill=tpl["muted"], anchor="ma")
        path = os.path.join(out_dir, f"{basename}_{template}_p{idx}.png")
        img.save(path)
        paths.append(path)
    return paths
