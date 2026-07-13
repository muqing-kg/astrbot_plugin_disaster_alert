"""天气卡片 - 参考手机天气 App 的磨砂竖卡。

设计：
1. 全卡圆角 + 天气主题渐变背景
2. 场景装饰在底层（太阳/云/雨/雪）
3. 底部信息区用真实磨砂玻璃（截取背景模糊）
4. 文字严格限制在安全边距内，自动适配宽度
5. 信息层级：天气名 / 大温度 / 体感 / 三指标 / 小时预报
"""

from __future__ import annotations

import math
import os
import random
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

if TYPE_CHECKING:
    from .sources.region_weather import RegionWeather

W, H = 720, 1280
R = 56  # 外圆角
SAFE = 48


def render_weather_card(weather: "RegionWeather", out_path: str | Path) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    theme = theme_of(weather.weather)
    # 顶部主信息强制高对比（深色字），磨砂区用 theme text
    theme.setdefault('hero_text', '#1A1F28')
    theme.setdefault('hero_muted', '#3C4654')
    # 浅色主题沿用自身深色
    if theme.get('key') in {'sunny', 'cloudy', 'snow', 'windy', 'fog'}:
        theme['hero_text'] = theme['text']
        theme['hero_muted'] = theme['muted']
    fonts = load_fonts()

    # ---- 场景底图 ----
    scene = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    paint_gradient(scene, theme["bg"])
    draw_atmosphere(scene, theme["key"])

    # 外轮廓圆角蒙版后的卡面
    card = scene.copy()

    # 顶部文案（直接写在场景上，不做脏色块）
    d = ImageDraw.Draw(card)
    title = weather_cn(weather.weather)
    badge = theme["badge"]
    # 顶部区域取偏深色字，保证在亮/暗渐变上都清楚
    hero_ink = theme["hero_text"]
    hero_muted = theme["hero_muted"]

    # 标题
    d.text((SAFE, 64), title, font=fonts["title"], fill=hero_ink)
    bar_w = min(86, text_w(d, title, fonts["title"]))
    d.rounded_rectangle((SAFE, 116, SAFE + bar_w, 122), radius=3, fill=rgba_of(theme.get("accent", "#E67A1F"), 220))
    # 右上角中文胶囊
    bw = text_w(d, badge, fonts["badge"]) + 36
    badge_box = (W - SAFE - bw, 68, W - SAFE, 108)
    frost_rect(card, scene, badge_box, radius=20, tint=theme["chip"], border=theme["border"], blur=10)
    d = ImageDraw.Draw(card)
    d.text((badge_box[0] + 18, 78), badge, font=fonts["badge"], fill=theme["chip_text"])

    # 地点
    # 地点只显示城市/区县名，不加省份前缀
    place = (weather.city or weather.query or weather.province or "").strip()
    d.text((SAFE, 126), fit(d, place, fonts["sub"], W - SAFE * 2 - bw - 12), font=fonts["sub"], fill=hero_muted)

    # 大温度
    temp = pretty_temp(weather.temperature)
    d.text((SAFE, 200), f"{temp}°", font=fonts["temp"], fill=hero_ink)

    # 体感
    feels = pretty_temp(weather.feels_like)
    d.ellipse((SAFE, 382, SAFE + 10, 392), fill=rgba_of(theme.get("accent", "#E67A1F"), 255))
    d.text((SAFE + 18, 370), f"体感 {feels}°", font=fonts["body"], fill=hero_muted)

    # ---- 中部：磨砂指标板 ----
    metrics_box = (SAFE, 470, W - SAFE, 620)
    frost_rect(card, scene, metrics_box, radius=28, tint=theme["frost"], border=theme["border"], blur=16)
    d = ImageDraw.Draw(card)

    metrics = build_metrics(weather, theme["key"])
    col_w = (W - SAFE * 2) / 3
    for i, (lab, val) in enumerate(metrics):
        cx = SAFE + col_w * i
        if i > 0:
            xline = int(cx)
            d.line((xline, 505, xline, 585), fill=theme["divider"], width=1)
        lab_s = fit(d, lab, fonts["tiny"], col_w - 16)
        val_s = fit(d, val, fonts["metric"], col_w - 16)
        d.text((cx + (col_w - text_w(d, lab_s, fonts["tiny"])) / 2, 505), lab_s, font=fonts["tiny"], fill=theme.get("label", theme["muted"]))
        d.text((cx + (col_w - text_w(d, val_s, fonts["metric"])) / 2, 545), val_s, font=fonts["metric"], fill=theme.get("value", theme["text"]))

    # ---- 预警（若有，占用指标下方一条磨砂）----
    y = 648
    warns = normalize_warns(getattr(weather, "warns", None), weather.warn_text)
    if warns:
        # 最多 2 条，完整换行
        blocks = []
        md = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        for witem in warns[:2]:
            lines = wrap(md, witem["text"], fonts["small"], W - SAFE * 2 - 40)
            blocks.append((witem["level"], lines))
        warn_h = 24
        for _, lines in blocks:
            warn_h += 18 + 28 + len(lines) * 30
        warn_box = (SAFE, y, W - SAFE, y + warn_h)
        frost_rect(card, scene, warn_box, radius=26, tint=theme["frost"], border=theme["border"], blur=14)
        d = ImageDraw.Draw(card)
        cy = y + 18
        for level, lines in blocks:
            pal = warn_palette(level)
            d.ellipse((SAFE + 22, cy + 8, SAFE + 38, cy + 24), fill=pal["dot"])
            d.text((SAFE + 48, cy + 4), level, font=fonts["badge"], fill=pal["fg"])
            cy += 32
            for line in lines:
                d.text((SAFE + 22, cy), fit(d, line, fonts["small"], W - SAFE * 2 - 40), font=fonts["small"], fill=theme["text"])
                cy += 30
            cy += 12
        y = y + warn_h + 24
    else:
        y = 656

    # ---- 底部小时/短期预报磨砂板 ----
    bottom_h = H - y - 70
    bottom_box = (SAFE, y, W - SAFE, y + bottom_h)
    frost_rect(card, scene, bottom_box, radius=28, tint=theme["frost2"], border=theme["border"], blur=16)
    d = ImageDraw.Draw(card)

    # 用未来几天白天当作“时段卡”，没有小时数据时也保持参考布局
    items = list(weather.forecasts or [])[:4]
    if not items:
        d.text((SAFE + 24, y + 30), "暂无预报数据", font=fonts["body"], fill=theme["muted"])
    else:
        n = len(items)
        gap = 10
        inner_w = W - SAFE * 2 - 32
        cell = (inner_w - gap * (n - 1)) / n
        base_x = SAFE + 16
        top = y + 22
        for i, f in enumerate(items):
            x = base_x + i * (cell + gap)
            label = short_date(f.date) if i else "现在"
            ikey = icon_key(f.day_weather if i else weather.weather)
            wtext = (f.day_weather if i else (weather.weather or f.day_weather or "-")) or "-"
            tval = pretty_temp(f.day_temp if i else weather.temperature)

            # 日期
            tw = text_w(d, label, fonts["tiny"])
            d.text((x + (cell - tw) / 2, top), label, font=fonts["tiny"], fill=theme.get("label", theme["muted"]))
            # 矢量图标（避免方块缺字）
            # 图标统一深色单色，不使用彩色
            ink = rgba_of(theme.get("value", theme.get("text", "#1A1F28")), 245)
            draw_weather_icon(d, ikey, x + cell / 2, top + 56, 1.15, ink, ink)
            # 天气文字
            wt = fit(d, wtext, fonts["tiny"], cell - 8)
            tw = text_w(d, wt, fonts["tiny"])
            d.text((x + (cell - tw) / 2, top + 96), wt, font=fonts["tiny"], fill=theme.get("label", theme["muted"]))
            # 温度
            ts = f"{tval}°"
            tw = text_w(d, ts, fonts["metric"])
            d.text((x + (cell - tw) / 2, top + 128), ts, font=fonts["metric"], fill=theme.get("value", theme["text"]))

    # 底部来源
    foot = "中央气象台 · 仅供参考"
    fw = text_w(d, foot, fonts["tiny"])
    d.text(((W - fw) / 2, H - 48), foot, font=fonts["tiny"], fill=theme.get("hero_muted", theme["muted"]))

    # 应用大圆角，避免直角丑边
    rounded = apply_round_mask(card, R)
    # 外阴影画在更深底上，增强“卡片感”
    final = Image.new("RGBA", (W + 40, H + 40), (12, 16, 28, 255))
    # 软阴影
    shadow = Image.new("RGBA", (W + 40, H + 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((28, 30, 28 + W, 30 + H), radius=R + 2, fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    final = Image.alpha_composite(final, shadow)
    final.paste(rounded, (20, 16), rounded)

    final.convert("RGB").save(out_path, format="PNG", optimize=True)
    return str(out_path)


# ================= 场景 / 主题 =================

def rgba_of(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = str(color or '#FFFFFF').lstrip('#')
    if len(c) >= 6:
        r = int(c[0:2], 16); g = int(c[2:4], 16); b = int(c[4:6], 16)
        return (r, g, b, alpha)
    return (255, 255, 255, alpha)


def theme_of(weather_text: str) -> dict[str, Any]:

    t = str(weather_text or "")
    if any(k in t for k in ("雷",)) or "暴雨" in t:
        return dict(
            key="storm",
            badge="雷暴",
            bg=[(108, 118, 160), (132, 138, 176), (168, 168, 196)],
            text="#1C2438", muted="#3A4660", soft="#55627A",
            frost=(255, 255, 255, 78), frost2=(255, 255, 255, 70),
            border=(255, 255, 255, 95), divider=(40, 50, 70, 55),
            chip=(255, 255, 255, 110), accent="#6B5CFF", accent2="#FFD36A", label="#4A5678", value="#1C2438", icon_main="#5B4CFF", icon_sub="#FFD36A", chip_text="#2A3350",
        )
    if "雪" in t:
        return dict(
            key="snow",
            badge="暴雪" if "暴" in t else "降雪",
            bg=[(176, 192, 214), (198, 210, 226), (220, 226, 236)],
            text="#163044", muted="#2F4F66", soft="#456781",
            frost=(255, 255, 255, 78), frost2=(255, 255, 255, 70),
            border=(255, 255, 255, 95), divider=(255, 255, 255, 65),
            chip=(255, 255, 255, 110), accent="#3E7CAE", accent2="#E8F4FF", label="#3A5C78", value="#163044", icon_main="#5EA0D8", icon_sub="#FFFFFF", chip_text="#243E56",
        )
    if "雨" in t:
        return dict(
            key="rain",
            badge="降雨",
            bg=[(102, 128, 164), (130, 150, 180), (164, 176, 198)],
            text="#173046", muted="#355872", soft="#4F7390",
            frost=(255, 255, 255, 80), frost2=(255, 255, 255, 72),
            border=(255, 255, 255, 95), divider=(30, 50, 70, 55),
            chip=(255, 255, 255, 115), accent="#2F7FD6", accent2="#7ED0FF", label="#355872", value="#173046", icon_main="#2F7FD6", icon_sub="#7ED0FF", chip_text="#27384E",
        )
    if any(k in t for k in ("风",)):
        return dict(
            key="windy",
            badge="大风",
            bg=[(118, 160, 176), (148, 184, 196), (184, 208, 214)],
            text="#102C34", muted="#2C4E58", soft="#426872",
            frost=(255, 255, 255, 55), frost2=(255, 255, 255, 48),
            border=(255, 255, 255, 70), divider=(255, 255, 255, 48),
            chip=(255, 255, 255, 95), accent="#1F8A8A", accent2="#7FE0D8", label="#2C4E58", value="#102C34", icon_main="#1F8A8A", icon_sub="#7FE0D8", chip_text="#1C4048",
        )
    if any(k in t for k in ("雾", "霾", "沙", "尘")):
        return dict(
            key="fog",
            badge="雾霾",
            bg=[(156, 156, 158), (178, 176, 174), (200, 196, 190)],
            text="#1A1E22", muted="#3A4046", soft="#555C62",
            frost=(255, 255, 255, 60), frost2=(255, 255, 255, 55),
            border=(255, 255, 255, 75), divider=(255, 255, 255, 50),
            chip=(255, 255, 255, 100), accent="#6A7078", accent2="#D0D4D8", label="#3A4046", value="#1A1E22", icon_main="#7A828A", icon_sub="#C8CDD2", chip_text="#2A3034",
        )
    if "阴" in t:
        return dict(
            key="overcast",
            badge="阴天",
            bg=[(126, 138, 152), (154, 164, 176), (186, 192, 202)],
            text="#1C2832", muted="#3C4C5A", soft="#5A6A78",
            frost=(255, 255, 255, 80), frost2=(255, 255, 255, 72),
            border=(255, 255, 255, 95), divider=(40, 50, 60, 55),
            chip=(255, 255, 255, 115), accent="#5B6B7A", accent2="#B7C4D0", label="#3C4C5A", value="#1C2832", icon_main="#6A7A88", icon_sub="#D5DEE6", chip_text="#2C3842",
        )
    if "云" in t:
        return dict(
            key="cloudy",
            badge="多云",
            bg=[(118, 174, 218), (154, 194, 228), (194, 216, 238)],
            text="#102838", muted="#2A4A60", soft="#3E6680",
            frost=(255, 255, 255, 60), frost2=(255, 255, 255, 52),
            border=(255, 255, 255, 80), divider=(255, 255, 255, 55),
            chip=(255, 255, 255, 105), accent="#0E6FAF", accent2="#FFC85A", label="#2A4A60", value="#102838", icon_main="#3C8FD0", icon_sub="#FFC85A", chip_text="#183C52",
        )
    # sunny - 贴近参考：上蓝下暖橙
    return dict(
        key="sunny",
        badge="晴天",
        bg=[(126, 188, 236), (255, 196, 132), (255, 154, 112)],
        text="#24140C", muted="#4E2C1C", soft="#6A3C28",
        frost=(255, 255, 255, 55), frost2=(255, 255, 255, 48),
        border=(255, 255, 255, 75), divider=(255, 255, 255, 50),
        chip=(255, 255, 255, 100), accent="#E67A1F", accent2="#FFD36A", label="#6A3C28", value="#24140C", icon_main="#FFC107", icon_sub="#FF8A3D", chip_text="#5A2E18",
    )


def paint_gradient(img: Image.Image, colors: list[tuple[int, int, int]]) -> None:
    w, h = img.size
    d = ImageDraw.Draw(img)
    c0, c1, c2 = colors[0], colors[1], colors[2]
    for y in range(h):
        t = y / max(h - 1, 1)
        if t < 0.42:
            tt = t / 0.42
            col = tuple(int(c0[i] + (c1[i] - c0[i]) * tt) for i in range(3))
        else:
            tt = (t - 0.42) / 0.58
            col = tuple(int(c1[i] + (c2[i] - c1[i]) * tt) for i in range(3))
        d.line([(0, y), (w, y)], fill=col + (255,))


def draw_atmosphere(img: Image.Image, key: str) -> None:
    d = ImageDraw.Draw(img)
    rnd = random.Random(abs(hash(key)) % 10007)
    w, h = img.size

    if key == "sunny":
        # 太阳在右上，柔光多层
        cx, cy = int(w * 0.72), int(h * 0.30)
        for r, a in ((150, 28), (115, 50), (85, 85), (60, 140), (46, 190)):
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 226, 140, a))
        soft_cloud(d, 50, int(h * 0.58), 1.15, (255, 255, 255, 58))
        soft_cloud(d, int(w * 0.42), int(h * 0.64), 1.35, (255, 255, 255, 48))
        soft_cloud(d, 20, int(h * 0.74), 1.0, (255, 255, 255, 40))

    elif key == "cloudy":
        d.ellipse((int(w * 0.66), 110, int(w * 0.66) + 92, 202), fill=(255, 228, 150, 95))
        soft_cloud(d, 40, 180, 1.45, (255, 255, 255, 115))
        soft_cloud(d, 210, 150, 1.2, (255, 255, 255, 100))
        soft_cloud(d, int(w * 0.52), 220, 1.55, (255, 255, 255, 105))
        soft_cloud(d, 90, int(h * 0.58), 1.25, (255, 255, 255, 70))

    elif key == "overcast":
        soft_cloud(d, 20, 150, 1.7, (236, 240, 245, 110))
        soft_cloud(d, 180, 125, 1.5, (228, 233, 240, 100))
        soft_cloud(d, int(w * 0.48), 180, 1.75, (220, 226, 234, 100))

    elif key in {"rain", "storm"}:
        soft_cloud(d, 30, 130, 1.55, (232, 238, 246, 105))
        soft_cloud(d, int(w * 0.45), 150, 1.65, (222, 230, 242, 95))
        for _ in range(75 if key == "rain" else 55):
            x = rnd.randint(40, w - 40)
            y = rnd.randint(int(h * 0.24), int(h * 0.82))
            d.line((x, y, x - 7, y + 26), fill=(235, 242, 255, 72), width=3)
        if key == "storm":
            bolt = [
                (int(w * 0.70), 170), (int(w * 0.63), 265), (int(w * 0.68), 265),
                (int(w * 0.58), 380), (int(w * 0.72), 270), (int(w * 0.67), 270),
            ]
            d.polygon(bolt, fill=(250, 236, 140, 185))

    elif key == "snow":
        soft_cloud(d, 50, 140, 1.45, (255, 255, 255, 125))
        soft_cloud(d, int(w * 0.46), 160, 1.55, (255, 255, 255, 110))
        d.ellipse((int(w * 0.68), 130, int(w * 0.68) + 88, 218), fill=(255, 255, 255, 125))
        for _ in range(90):
            x = rnd.randint(30, w - 30)
            y = rnd.randint(200, h - 80)
            rr = rnd.randint(2, 4)
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=(255, 255, 255, 155))

    elif key == "windy":
        for i in range(7):
            yy = 190 + i * 75
            d.arc((50, yy, w - 50, yy + 70), 200, 340, fill=(255, 255, 255, 42), width=3)
        soft_cloud(d, 110, 170, 1.1, (255, 255, 255, 70))

    else:  # fog
        for i, a in enumerate((55, 45, 40, 34, 28)):
            yy = 150 + i * 95
            d.ellipse((-120, yy, w + 120, yy + 130), fill=(255, 255, 255, a))


def soft_cloud(draw: ImageDraw.ImageDraw, x: float, y: float, scale: float, fill) -> None:
    s = scale
    for a, b, c, dd in [(0, 24, 100, 82), (42, 0, 160, 88), (108, 18, 214, 92), (28, 34, 186, 108)]:
        draw.ellipse((x + a * s, y + b * s, x + c * s, y + dd * s), fill=fill)


# ================= 磨砂 =================

def frost_rect(
    canvas: Image.Image,
    scene: Image.Image,
    box,
    radius: int,
    tint,
    border,
    blur: int = 16,
) -> None:
    x0, y0, x1, y1 = map(int, box)
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(canvas.size[0], x1); y1 = min(canvas.size[1], y1)
    if x1 <= x0 or y1 <= y0:
        return
    pad = blur + 4
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    sx1, sy1 = min(scene.size[0], x1 + pad), min(scene.size[1], y1 + pad)
    crop = scene.crop((sx0, sy0, sx1, sy1)).filter(ImageFilter.GaussianBlur(blur))
    crop = Image.alpha_composite(crop.convert("RGBA"), Image.new("RGBA", crop.size, tint))
    local = crop.crop((x0 - sx0, y0 - sy0, x1 - sx0, y1 - sy0))
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=radius, fill=255)
    canvas.paste(local, (x0, y0), mask)
    # 边线 + 顶高光
    od = ImageDraw.Draw(canvas)
    od.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius, outline=border, width=2)
    hl = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(hl).rounded_rectangle((1, 1, x1 - x0 - 2, min(42, (y1 - y0) // 3)), radius=max(10, radius - 10), fill=(255, 255, 255, 34))
    canvas.paste(hl, (x0, y0), hl)


def apply_round_mask(img: Image.Image, radius: int) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = img.copy()
    out.putalpha(mask)
    return out


# ================= 文本工具 =================

def load_fonts() -> dict[str, Any]:
    return {
        "title": font(46, True),
        "temp": font(128, True),
        "sub": font(24),
        "body": font(28),
        "metric": font(30, True),
        "small": font(24),
        "tiny": font(20),
        "badge": font(18, True),
        "icon": font(42, True),
    }


def font(size: int, bold: bool = False):
    cands = [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"] if bold else [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"]
    cands += [r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def text_w(draw, text, fnt) -> int:
    try:
        b = draw.textbbox((0, 0), str(text), font=fnt)
        return max(0, b[2] - b[0])
    except Exception:
        return len(str(text)) * 14


def fit(draw, text: str, fnt, max_w: float) -> str:
    text = str(text or "")
    if text_w(draw, text, fnt) <= max_w:
        return text
    for i in range(len(text), 0, -1):
        s = text[:i] + "…"
        if text_w(draw, s, fnt) <= max_w:
            return s
    return "…"


def wrap(draw, text: str, fnt, max_w: float) -> list[str]:
    text = str(text or "").strip() or "-"
    lines, cur = [], ""
    for ch in text:
        t = cur + ch
        if text_w(draw, t, fnt) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines or ["-"]


def pretty_temp(v) -> str:
    s = str(v if v is not None else "").replace("℃", "").replace("°C", "").replace("°", "").strip()
    if s in {"", "-", "9999", "None"}:
        return "--"
    try:
        f = float(s)
        return str(int(round(f)))
    except Exception:
        return s[:4]


def clean(v) -> str:
    s = str(v if v is not None else "").strip()
    return "-" if s in {"", "-", "9999", "None"} else s


def build_metrics(weather, key: str) -> list[tuple[str, str]]:
    hum = clean(weather.humidity)
    wind = clean(weather.wind)
    # 压缩风力
    if wind != "-" and len(wind) > 8:
        wind = wind.replace("m/s", "").strip().split()[0]
    rain = clean(weather.rain)
    aqi = clean(weather.aqi_text)
    if key in {"rain", "storm"}:
        return [("湿度", hum), ("降水", rain), ("能见度", "-")]
    if key == "snow":
        return [("湿度", hum), ("风力", wind), ("体感", pretty_temp(weather.feels_like) + "°")]
    if key == "windy":
        return [("湿度", hum), ("风力", wind), ("风向", wind)]
    return [("湿度", hum), ("空气", aqi), ("风力", wind)]


def weather_cn(text: str) -> str:
    t = str(text or "").strip() or "实况"
    if len(t) <= 4:
        return t
    for k, name in [("暴雨", "暴雨"), ("雷", "雷雨"), ("雪", "降雪"), ("雨", "降雨"), ("雾", "雾"), ("霾", "霾"), ("阴", "阴天"), ("云", "多云"), ("晴", "晴天"), ("风", "大风")]:
        if k in t:
            return name
    return t[:4]


def icon_key(text: str) -> str:
    t = text or ""
    if "雪" in t: return "snow"
    if "雷" in t: return "storm"
    if "雨" in t: return "rain"
    if "阴" in t: return "overcast"
    if "云" in t: return "cloudy"
    if "雾" in t or "霾" in t: return "fog"
    if "风" in t: return "windy"
    return "sunny"





def draw_weather_icon(
    draw: ImageDraw.ImageDraw,
    key: str,
    cx: float,
    cy: float,
    scale: float,
    main: tuple[int, int, int, int],
    sub: tuple[int, int, int, int] | None = None,
) -> None:
    """黑色单色矢量图标（简洁版）。"""
    s = max(0.85, scale)
    # 强制单色：优先用传入 main，但调用侧会传深色字色
    ink = main
    if sub is None:
        sub = main

    def circle(x, y, r, fill=None):
        fill = fill or ink
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)

    def line(x1, y1, x2, y2, w=2):
        draw.line((x1, y1, x2, y2), fill=ink, width=max(2, int(w * s)))

    if key == "sunny":
        r = 14 * s
        circle(cx, cy, r)
        for i in range(8):
            a = math.radians(i * 45)
            x1 = cx + math.cos(a) * (r + 5 * s)
            y1 = cy + math.sin(a) * (r + 5 * s)
            x2 = cx + math.cos(a) * (r + 12 * s)
            y2 = cy + math.sin(a) * (r + 12 * s)
            line(x1, y1, x2, y2, 3)

    elif key in {"cloudy", "overcast"}:
        # 云：三圆 + 底条
        circle(cx - 10 * s, cy + 2 * s, 10 * s)
        circle(cx + 2 * s, cy - 6 * s, 13 * s)
        circle(cx + 14 * s, cy + 2 * s, 10 * s)
        draw.rounded_rectangle(
            (cx - 20 * s, cy + 2 * s, cx + 24 * s, cy + 14 * s),
            radius=8 * s,
            fill=ink,
        )
        if key == "cloudy":
            # 小太阳点缀（同样黑色勾边感：实心小圆）
            circle(cx + 16 * s, cy - 14 * s, 7 * s)

    elif key == "rain":
        circle(cx - 8 * s, cy - 6 * s, 10 * s)
        circle(cx + 6 * s, cy - 8 * s, 12 * s)
        circle(cx + 16 * s, cy - 4 * s, 9 * s)
        draw.rounded_rectangle(
            (cx - 18 * s, cy - 2 * s, cx + 22 * s, cy + 8 * s),
            radius=7 * s,
            fill=ink,
        )
        for dx in (-8, 0, 8):
            line(cx + dx * s, cy + 12 * s, cx + (dx - 3) * s, cy + 22 * s, 2.6)

    elif key == "storm":
        circle(cx - 8 * s, cy - 8 * s, 10 * s)
        circle(cx + 6 * s, cy - 10 * s, 12 * s)
        circle(cx + 16 * s, cy - 5 * s, 9 * s)
        draw.rounded_rectangle(
            (cx - 18 * s, cy - 3 * s, cx + 22 * s, cy + 7 * s),
            radius=7 * s,
            fill=ink,
        )
        bolt = [
            (cx + 2 * s, cy + 4 * s),
            (cx - 6 * s, cy + 16 * s),
            (cx + 0 * s, cy + 16 * s),
            (cx - 8 * s, cy + 30 * s),
            (cx + 6 * s, cy + 14 * s),
            (cx + 1 * s, cy + 14 * s),
        ]
        draw.polygon(bolt, fill=ink)

    elif key == "snow":
        circle(cx - 8 * s, cy - 8 * s, 10 * s)
        circle(cx + 6 * s, cy - 10 * s, 12 * s)
        circle(cx + 16 * s, cy - 5 * s, 9 * s)
        draw.rounded_rectangle(
            (cx - 18 * s, cy - 3 * s, cx + 22 * s, cy + 7 * s),
            radius=7 * s,
            fill=ink,
        )
        for dx, dy in ((-8, 14), (0, 18), (8, 14)):
            x, y = cx + dx * s, cy + dy * s
            r = 2.2 * s
            circle(x, y, r)
            line(x - 3 * s, y, x + 3 * s, y, 1.5)
            line(x, y - 3 * s, x, y + 3 * s, 1.5)

    elif key == "windy":
        draw.arc((cx - 18 * s, cy - 10 * s, cx + 16 * s, cy + 8 * s), 200, 335, fill=ink, width=max(2, int(3 * s)))
        draw.arc((cx - 14 * s, cy + 0 * s, cx + 14 * s, cy + 16 * s), 200, 335, fill=ink, width=max(2, int(3 * s)))
        circle(cx + 14 * s, cy - 2 * s, 2.2 * s)

    else:  # fog
        for yy in (-8, 0, 8):
            draw.rounded_rectangle(
                (cx - 16 * s, cy + yy * s, cx + 16 * s, cy + (yy + 4) * s),
                radius=2 * s,
                fill=ink,
            )



def short_date(date: str) -> str:
    s = str(date or "")
    return s[5:] if len(s) >= 10 else (s or "--")


def normalize_warns(warns, warn_text: str) -> list[dict]:
    if isinstance(warns, list) and warns:
        out = []
        for w in warns:
            if not isinstance(w, dict):
                continue
            text = str(w.get("text") or "").strip()
            if text and text not in {"9999", "-"}:
                out.append({"level": str(w.get("level") or extract_level(text) or "预警"), "text": text})
        if out:
            return out
    text = str(warn_text or "").strip()
    if not text or text in {"当前站点无特别预警", "当前查询地区暂无特别预警", "-", "9999"}:
        return []
    return [{"level": extract_level(text) or "预警", "text": text}]


def extract_level(text: str) -> str:
    for lv in ("红色", "橙色", "黄色", "蓝色"):
        if lv in (text or ""):
            return lv
    return ""


def warn_palette(level: str) -> dict[str, Any]:
    if "红" in level: return {"dot": (255, 92, 100, 255), "fg": "#FF6B73"}
    if "橙" in level: return {"dot": (255, 164, 64, 255), "fg": "#FFA33C"}
    if "黄" in level: return {"dot": (250, 220, 70, 255), "fg": "#E0B400"}
    if "蓝" in level: return {"dot": (90, 170, 255, 255), "fg": "#5AA8FF"}
    return {"dot": (200, 210, 220, 255), "fg": "#D0D8E0"}