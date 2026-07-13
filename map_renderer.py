"""灾害事件配图：中文地图底图 + 中文标注。"""

from __future__ import annotations

import io
import math
import os
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from .font_util import get_font

from .geo_utils import nearest_region, project, summarize_typhoon_impact

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AstrBotDisasterAlert/1.2"
# 高德矢量路网中文注记
TILE_TMPLS = [
    "https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
]


def _font(size: int = 22, bold: bool = False):
    return get_font(size=size, bold=bold)


def _tw(draw, text, font) -> int:
    b = draw.textbbox((0, 0), str(text), font=font)
    return max(0, b[2] - b[0])


def _fit(draw, text, font, max_w: int) -> str:
    text = str(text or "")
    if _tw(draw, text, font) <= max_w:
        return text
    for i in range(len(text), 0, -1):
        s = text[:i] + "…"
        if _tw(draw, s, font) <= max_w:
            return s
    return "…"


def _wrap(draw, text, font, max_w: int) -> list[str]:
    text = str(text or "")
    lines, cur = [], ""
    for ch in text:
        t = cur + ch
        if _tw(draw, t, font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines or ["—"]


def _deg2num(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    lat_r = math.radians(lat)
    n = 2.0 ** zoom
    xf = (lon + 180.0) / 360.0 * n
    yf = (1.0 - math.asinh(math.tan(lat_r)) / math.pi) / 2.0 * n
    return xf, yf


def _fetch_tile(z: int, x: int, y: int, cache_dir: Path) -> Image.Image | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    fp = cache_dir / f"v2_{z}_{x}_{y}.png"
    if fp.exists() and fp.stat().st_size > 200:
        try:
            return Image.open(fp).convert("RGB")
        except Exception:
            pass
    s = str((x % 4) + 1)
    for tmpl in TILE_TMPLS:
        try:
            url = tmpl.format(s=s, z=z, x=x, y=y)
        except KeyError:
            url = tmpl.format(z=z, x=x, y=y)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.amap.com/"})
            data = urllib.request.urlopen(req, timeout=6).read()
            if len(data) < 200:
                continue
            im = Image.open(io.BytesIO(data))
            # 关键处理调色板/透明通道，避免整图被洗成近白色
            if im.mode in ("P", "RGBA", "LA"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (230, 236, 242, 255))
                im = Image.alpha_composite(bg, im).convert("RGB")
            else:
                im = im.convert("RGB")
            # 过空瓦片丢弃
            sample = im.resize((16, 16))
            colors = set(sample.getdata())
            if len(colors) < 6:
                continue
            # 若几乎全是近白，也丢弃
            near_white = sum(1 for r,g,b in colors if r>235 and g>235 and b>235)
            if near_white >= max(1, int(len(colors) * 0.8)):
                continue
            im.save(fp, format="PNG")
            return im
        except Exception:
            continue
    return None


def render_basemap(center_lat: float, center_lon: float, zoom: int, width: int, height: int, cache_dir: str | Path):
    cache_dir = Path(cache_dir)
    tile_size = 256
    cx, cy = _deg2num(center_lat, center_lon, zoom)
    tiles_x = math.ceil(width / tile_size) + 1
    tiles_y = math.ceil(height / tile_size) + 1
    x0 = int(cx) - tiles_x // 2
    y0 = int(cy) - tiles_y // 2
    n = 2 ** zoom
    canvas = Image.new("RGB", (tiles_x * tile_size, tiles_y * tile_size), "#d7e2ea")
    for iy in range(tiles_y):
        for ix in range(tiles_x):
            tx = (x0 + ix) % n
            ty = y0 + iy
            if ty < 0 or ty >= n:
                continue
            tile = _fetch_tile(zoom, tx, ty, cache_dir)
            if tile is None:
                tile = Image.new("RGB", (tile_size, tile_size), "#cfd9e3")
            canvas.paste(tile, (ix * tile_size, iy * tile_size))

    def latlon_to_full_px(lat, lon):
        xf, yf = _deg2num(lat, lon, zoom)
        return (xf - x0) * tile_size, (yf - y0) * tile_size

    cpx, cpy = latlon_to_full_px(center_lat, center_lon)
    left = int(cpx - width / 2)
    top = int(cpy - height / 2)
    left = max(0, min(left, canvas.size[0] - width))
    top = max(0, min(top, canvas.size[1] - height))
    img = canvas.crop((left, top, left + width, top + height))

    def pixel_to_latlon(px, py):
        xf = x0 + (left + px) / tile_size
        yf = y0 + (top + py) / tile_size
        nn = 2.0 ** zoom
        lon = xf / nn * 360.0 - 180.0
        lat_r = math.atan(math.sinh(math.pi * (1 - 2 * yf / nn)))
        return lon, math.degrees(lat_r)

    min_lon, max_lat = pixel_to_latlon(0, 0)
    max_lon, min_lat = pixel_to_latlon(width, height)
    return img, (min_lon, min_lat, max_lon, max_lat)


def _xy(lon, lat, bbox, w, h):
    return project(float(lon), float(lat), bbox, w, h, pad=0)




def _draw_chinese_place_labels(img: Image.Image, bbox, center_lat: float, center_lon: float, w: int, h: int, count: int = 8) -> None:
    """在底图上叠加中文地区标签，避免只有英文地名。"""
    from .geo_utils import PROVINCE_ANCHORS
    d = ImageDraw.Draw(img)
    font = _font(18, True)
    ranked = []
    for name, pla, plo in PROVINCE_ANCHORS:
        if not (bbox[0] - 2 <= plo <= bbox[2] + 2 and bbox[1] - 2 <= pla <= bbox[3] + 2):
            continue
        dist = (pla - center_lat) ** 2 + (plo - center_lon) ** 2
        ranked.append((dist, name, pla, plo))
    ranked.sort(key=lambda x: x[0])
    for _, name, pla, plo in ranked[:count]:
        x, y = _xy(plo, pla, bbox, w, h)
        if x < 20 or y < 40 or x > w - 40 or y > h - 40:
            continue
        tw = _tw(d, name, font)
        d.rounded_rectangle((x - 6, y - 14, x + tw + 10, y + 12), radius=8, fill=(8, 14, 24, 190))
        d.text((x, y - 10), name, font=font, fill="#F5FAFF")


# ---------------- 地震卡片：无左侧大色块，震级右上角 ----------------

def render_earthquake_card(
    *,
    lat: float,
    lon: float,
    title: str,
    out_path: str | Path,
    magnitude: float | None = None,
    depth: Any = None,
    occurred_at: str = "",
    cache_dir: str | Path = "data/tile_cache",
) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    W, H = 1000, 720
    region = nearest_region(lat, lon)

    # 全图地图
    try:
        basemap, bbox = render_basemap(lat, lon, zoom=7, width=W, height=H, cache_dir=cache_dir)
    except Exception:
        basemap = Image.new("RGB", (W, H), "#1B2A38")
        bbox = (lon - 2.5, lat - 1.8, lon + 2.5, lat + 1.8)

    img = basemap.convert("RGBA")
    # map frame
    _edge = ImageDraw.Draw(img)
    _edge.rectangle((0,0,W-1,H-1), outline=(255,255,255,40), width=2)
    _draw_chinese_place_labels(img, bbox, lat, lon, W, H)
    # 顶部磨砂信息条
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    td = ImageDraw.Draw(top)
    td.rounded_rectangle((18, 16, W - 18, 150), radius=22, fill=(12, 18, 28, 168))
    img = Image.alpha_composite(img, top)
    d = ImageDraw.Draw(img)

    font_title = _font(30, True)
    font_body = _font(22)
    font_small = _font(18)
    font_mag = _font(34, True)

    # 标题
    d.text((40, 34), _fit(d, title or region, font_title, 680), font=font_title, fill="#F5F8FC")
    d.text((40, 80), occurred_at or "—", font=font_body, fill="#C5D3E2")
    d.text((40, 112), f"地区 {region}    深度 {depth if depth not in (None, '') else '—'} km", font=font_small, fill="#A9BBCC")

    # 右上角震级徽章（重新设计，不用大绿圆+小胶囊）
    mag = f"{magnitude:.1f}" if isinstance(magnitude, (int, float)) else "--"
    badge_w, badge_h = 150, 86
    bx0, by0 = W - 40 - badge_w, 30
    # 外层深色底板
    d.rounded_rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), radius=18, fill=(255, 255, 255, 235))
    d.rounded_rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), radius=18, outline=(30, 40, 55, 60), width=1)
    # 顶部细标签
    d.text((bx0 + 18, by0 + 10), "震级", font=font_small, fill="#5C6B7A")
    # 大号数字
    mw = _tw(d, mag, font_mag)
    d.text((bx0 + (badge_w - mw) / 2, by0 + 34), mag, font=font_mag, fill="#E23D48")

    # 震中红叉
    x, y = _xy(lon, lat, bbox, W, H)
    for r in (20, 12):
        d.line((x - r, y - r, x + r, y + r), fill=(255, 59, 48, 255), width=5)
        d.line((x - r, y + r, x + r, y - r), fill=(255, 59, 48, 255), width=5)
    d.ellipse((x - 6, y - 6, x + 6, y + 6), outline=(255, 255, 255, 220), width=2)

    # 底部信息条
    bot = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bot)
    bd.rounded_rectangle((18, H - 92, W - 18, H - 18), radius=18, fill=(12, 18, 28, 160))
    img = Image.alpha_composite(img, bot)
    d = ImageDraw.Draw(img)
    d.text((40, H - 72), f"震中坐标  {lat:.2f}°N, {lon:.2f}°E", font=font_body, fill="#E8F1FA")
    d.text((40, H - 42), "地图注记：中文  ·  仅供参考", font=font_small, fill="#9DB0C2")

    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    return str(out_path)


def render_earthquake_map(**kwargs) -> str:
    cache = kwargs.pop("cache_dir", None)
    if cache is None:
        cache = Path(kwargs.get("out_path", ".")).resolve().parent / "tile_cache"
    return render_earthquake_card(cache_dir=cache, depth=kwargs.pop("depth", None), occurred_at=kwargs.pop("occurred_at", ""), **kwargs)


# ---------------- 台风路径：中文地图 + 影响省市 ----------------

def render_typhoon_track(
    *,
    points: list[dict[str, Any]],
    title: str,
    out_path: str | Path,
    cache_dir: str | Path = "data/tile_cache",
) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pts = []
    for p in points:
        try:
            pts.append({
                "lon": float(p["lon"]), "lat": float(p["lat"]),
                "intensity": str(p.get("intensity") or ""),
                "time_text": str(p.get("time_text") or p.get("time_code") or ""),
                "wind": p.get("wind"), "pressure": p.get("pressure"),
            })
        except Exception:
            continue
    if not pts:
        img = Image.new("RGB", (1100, 780), "#102033")
        ImageDraw.Draw(img).text((30, 30), "暂无路径点", font=_font(28), fill="#fff")
        img.save(out_path, format="PNG", optimize=True)
        return str(out_path)

    # 点数过多时抽样，保留最新段更密
    if len(pts) > 80:
        head = pts[:-30:max(1, (len(pts) - 30) // 40)]
        pts = head + pts[-30:]

    lons = [p["lon"] for p in pts]
    lats = [p["lat"] for p in pts]
    center_lon = (min(lons) + max(lons)) / 2
    center_lat = (min(lats) + max(lats)) / 2
    span = max(max(lons) - min(lons), max(lats) - min(lats), 4)
    zoom = 5 if span > 16 else 6 if span > 9 else 7

    W, H = 1100, 820
    try:
        basemap, bbox = render_basemap(center_lat, center_lon, zoom, W, H, cache_dir)
    except Exception:
        basemap = Image.new("RGB", (W, H), "#0F2438")
        bbox = (min(lons) - 2, min(lats) - 2, max(lons) + 2, max(lats) + 2)

    img = basemap.convert("RGBA")
    _draw_chinese_place_labels(img, bbox, center_lat, center_lon, W, H, count=10)
    d = ImageDraw.Draw(img)
    xy = [_xy(p["lon"], p["lat"], bbox, W, H) for p in pts]
    if len(xy) >= 2:
        d.line(xy, fill=(40, 140, 255, 230), width=5)
    for (x, y), p in zip(xy, pts):
        col = _ty_color(p["intensity"])
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=col, outline=(255, 255, 255, 220), width=2)

    # 当前中心
    x, y = xy[-1]
    d.ellipse((x - 18, y - 18, x + 18, y + 18), outline=(255, 70, 50, 255), width=4)
    d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 70, 50, 255))

    latest = pts[-1]
    impact = summarize_typhoon_impact(pts, latest)
    current_region = impact.get("current") or nearest_region(latest["lat"], latest["lon"])
    regions = impact.get("regions") or []

    # 顶部标题
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.rounded_rectangle((18, 16, W - 18, 118), radius=20, fill=(12, 18, 28, 165))
    img = Image.alpha_composite(img, over)
    d = ImageDraw.Draw(img)
    d.text((40, 34), f"台风路径：{title}", font=_font(30, True), fill="#F5FAFF")
    d.text((40, 78), f"当前中心靠近：{current_region}    {latest['lat']:.1f}°N, {latest['lon']:.1f}°E", font=_font(20), fill="#D5E4F2")

    # 右侧/底部影响区域重点卡
    over2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    o2 = ImageDraw.Draw(over2)
    o2.rounded_rectangle((18, H - 170, W - 18, H - 18), radius=20, fill=(12, 18, 28, 170))
    img = Image.alpha_composite(img, over2)
    d = ImageDraw.Draw(img)

    d.text((40, H - 150), "重点影响/邻近地区（估算）", font=_font(22, True), fill="#FFE08A")
    reg_text = "、".join(regions[:8]) if regions else "路径尚远离主要陆地区域"
    for i, line in enumerate(_wrap(d, reg_text, _font(22), W - 100)[:2]):
        d.text((40, H - 112 + i * 30), line, font=_font(22), fill="#F2F7FC")

    sub = []
    if latest.get("wind") is not None:
        sub.append(f"风速 {latest['wind']} m/s")
    if latest.get("pressure") is not None:
        sub.append(f"气压 {latest['pressure']} hPa")
    sub.append(f"路径点 {len(pts)}")
    d.text((40, H - 48), "  ·  ".join(sub) + "  ·  中文地图", font=_font(18), fill="#A9BDCF")

    # 给当前区域一个地图标签
    if current_region:
        d.rounded_rectangle((x + 20, y - 42, x + 20 + _tw(d, current_region, _font(18, True)) + 24, y - 10), radius=10, fill=(20, 30, 45, 210))
        d.text((x + 32, y - 36), current_region, font=_font(18, True), fill="#FFFFFF")

    img.convert("RGB").save(out_path, format="PNG", optimize=True)
    return str(out_path)


def _ty_color(intensity: str) -> tuple[int, int, int]:
    t = (intensity or "").upper()
    if "SUPER" in t or t in {"STY", "TY"}:
        return (226, 61, 72)
    if t == "STS":
        return (240, 138, 36)
    if t == "TS":
        return (60, 141, 222)
    if t == "TD":
        return (90, 174, 97)
    return (110, 193, 255)


# ---------------- 极危天气卡片 ----------------

def render_critical_weather_card(
    *,
    title: str,
    location: str,
    occurred_at: str,
    level: str,
    summary: str,
    out_path: str | Path,
    advice: str = "",
) -> str:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    W, H = 980, 620
    img = Image.new("RGB", (W, H), "#2A0D12")
    d = ImageDraw.Draw(img)
    for y in range(H):
        r = int(42 + (96 - 42) * y / H)
        g = int(12 + (28 - 12) * y / H)
        b = int(18 + (36 - 18) * y / H)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    d.rounded_rectangle((28, 28, W - 28, H - 28), radius=28, fill=(42, 14, 20), outline=(190, 60, 70), width=2)
    d.rounded_rectangle((48, 48, W - 48, 140), radius=20, fill=(180, 36, 48))
    d.text((70, 70), "极危天气预警", font=_font(34, True), fill="#FFFFFF")
    d.rounded_rectangle((W - 190, 72, W - 70, 112), radius=12, fill=(255, 230, 230))
    d.text((W - 160, 80), level or "红色", font=_font(22, True), fill="#A01828")
    d.text((70, 170), _fit(d, title, _font(28, True), W - 140), font=_font(28, True), fill="#FFE8EA")
    d.text((70, 230), f"地区：{location or '—'}", font=_font(22), fill="#F0C8CE")
    d.text((70, 270), f"时间：{occurred_at or '—'}", font=_font(22), fill="#F0C8CE")
    d.rounded_rectangle((70, 320, W - 70, 420), radius=16, fill=(70, 22, 30))
    d.text((90, 340), "风险说明", font=_font(20, True), fill="#FFB4BC")
    d.text((90, 375), _fit(d, summary or "已达红色预警，可能严重威胁当地人身与财产安全。", _font(22), W - 180), font=_font(22), fill="#FFEDEE")
    d.rounded_rectangle((70, 445, W - 70, 560), radius=16, fill=(55, 18, 24))
    d.text((90, 460), "安全忠告", font=_font(20, True), fill="#FF9AA5")
    lines = _wrap(d, (advice or "请立即远离危险区域，服从当地应急部门指令。").replace("\n", " "), _font(20), W - 180)
    yy = 492
    for line in lines[:3]:
        d.text((90, yy), line, font=_font(20), fill="#F8DADF")
        yy += 28
    img.save(out_path, format="PNG", optimize=True)
    return str(out_path)