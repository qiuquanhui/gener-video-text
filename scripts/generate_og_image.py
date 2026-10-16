# -*- coding: utf-8 -*-
"""构建期工具：生成 1200x630 的 OG 社交分享图（frontend/images/og-image.png）。

仅开发时手动运行（依赖系统 Python 的 Pillow），运行时网站零依赖：
    python scripts/generate_og_image.py

设计对齐方案设计.md 的设计体系：主色 #2563EB、浅底 #EFF6FF/#F5F7FA、白色圆角卡片。
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BLUE = (37, 99, 235)        # 主色 #2563EB
BLUE_DARK = (29, 78, 216)   # hover #1D4ED8
WHITE = (255, 255, 255)
INK = (17, 24, 39)          # 正文 #111827

OUT = Path(__file__).resolve().parent.parent / "frontend" / "images" / "og-image.png"

FONT_CANDIDATES = {
    "bold": [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf"],
    "regular": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"],
}


def load_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("未找到中文字体（msyh/simhei），无法生成分享图")


# 闪电形状归一化顶点（尖头朝上的经典闪电），scale 后落到目标位置
BOLT_POINTS = [(61, 0), (28, 55), (47, 55), (33, 100), (80, 40), (57, 40), (76, 0)]


def bolt(draw: ImageDraw.ImageDraw, x: float, y: float, scale: float, fill):
    pts = [(x + px * scale, y + py * scale) for px, py in BOLT_POINTS]
    draw.polygon(pts, fill=fill)


def text_width(font: ImageFont.FreeTypeFont, s: str) -> int:
    return font.getbbox(s)[2]


def main() -> None:
    # 背景：主色 → 深主色的纵向渐变
    img = Image.new("RGB", (W, H), BLUE)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        color = tuple(int(BLUE[i] + (BLUE_DARK[i] - BLUE[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=color)

    # 半透明装饰层：右侧大闪电 + 顶部光晕圆
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    bolt(od, 935, 110, 4.6, (255, 255, 255, 26))
    od.ellipse([980, -170, 1420, 210], fill=(255, 255, 255, 14))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    d = ImageDraw.Draw(img)

    # 品牌胶囊：白底圆角 + 蓝色闪电 + 蓝色品牌名
    f_brand = load_font("bold", 30)
    brand = "闪电下载"
    brand_w = text_width(f_brand, brand)
    pad, pill_h = 22, 60
    pill_w = 46 + brand_w + pad * 2  # 46px 留给闪电图标
    d.rounded_rectangle([90, 72, 90 + pill_w, 72 + pill_h], radius=30, fill=WHITE)
    bolt(d, 90 + pad, 72 + 11, 0.38, BLUE_DARK)
    d.text((90 + pad + 52, 72 + (pill_h - 30) / 2), brand, font=f_brand, fill=BLUE_DARK)

    # 主标题：与首页 Hero 同文案
    f_h1 = load_font("bold", 86)
    d.text((90, 196), "全网视频，一键下载", font=f_h1, fill=WHITE)

    # 副标题：平台覆盖
    f_sub = load_font("regular", 33)
    d.text((94, 330), "哔哩哔哩 · 抖音 · YouTube · 快手 · 微博 等 1800+ 平台", font=f_sub, fill=(230, 238, 255))
    d.text((94, 392), "支持 4K 高清 · MP3 提取 · 字幕转文字 · AI 视频总结", font=f_sub, fill=(230, 238, 255))

    # 底部权益胶囊：白色描边
    f_chip = load_font("bold", 29)
    chips = ["完全免费", "不限次数", "无需登录", "手机电脑能用"]
    x = 90
    for label in chips:
        w = text_width(f_chip, label) + 52
        top = 500
        d.rounded_rectangle([x, top, x + w, top + 62], radius=31, outline=WHITE, width=2)
        d.text((x + 26, top + (62 - 29) / 2), label, font=f_chip, fill=WHITE)
        x += w + 22

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, "PNG", optimize=True)
    print(f"已生成 {OUT}（{W}x{H}，{OUT.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
