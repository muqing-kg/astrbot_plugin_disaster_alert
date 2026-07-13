"""国内范围与地图渲染辅助。"""

from __future__ import annotations

import math
import re
from typing import Any


# 大致覆盖中国陆地+近海（含南海海域）
CHINA_LAT_MIN, CHINA_LAT_MAX = 3.0, 54.0
CHINA_LON_MIN, CHINA_LON_MAX = 73.0, 135.5

FOREIGN_MARKERS = (
    "日本", "菲律宾", "印尼", "印度尼西亚", "美国", "俄罗斯", "朝鲜", "韩国",
    "越南", "老挝", "缅甸", "泰国", "马来", "新加坡", "印度", "巴基斯坦",
    "阿富汗", "哈萨克", "吉尔吉斯", "塔吉克", "蒙古", "澳大利亚", "新西兰",
    "智利", "秘鲁", "墨西哥", "加拿大", "意大利", "土耳其", "伊朗", "伊拉克",
    "大西洋", "印度洋", "南桑威奇", "斐济", "汤加", "所罗门", "巴布亚",
    "关岛", "马里亚纳", "琉球", "本州", "北海道", "四国", "九州",
)


def in_china_bbox(lat: Any, lon: Any) -> bool:
    try:
        la = float(lat)
        lo = float(lon)
    except (TypeError, ValueError):
        return False
    return CHINA_LAT_MIN <= la <= CHINA_LAT_MAX and CHINA_LON_MIN <= lo <= CHINA_LON_MAX


def looks_china_location(text: str) -> bool:
    t = str(text or "")
    if not t:
        return False
    # 明确国外关键词优先排除
    if any(k in t for k in FOREIGN_MARKERS):
        # 但仍可能是“中国东海/南海”等，下面再放行
        if not any(k in t for k in ("中国", "我国", "南海", "东海", "黄海", "渤海", "台湾", "香港", "澳门", "新疆", "西藏", "内蒙古")):
            return False
    china_tokens = (
        "中国", "我国", "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
        "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏",
        "新疆", "香港", "澳门", "南海", "东海", "黄海", "渤海", "钓鱼岛", "省", "市", "自治区",
        "特别行政区", "地区", "州", "盟", "县", "区",
    )
    return any(k in t for k in china_tokens)


def is_china_earthquake(location: str, lat: Any, lon: Any) -> bool:
    # 坐标优先
    if lat is not None and lon is not None:
        try:
            if in_china_bbox(lat, lon):
                # 坐标在框内，但地点文本明显国外时仍排除
                if location and any(k in location for k in FOREIGN_MARKERS) and not looks_china_location(location):
                    return False
                return True
            return False
        except Exception:
            pass
    return looks_china_location(location)


def is_china_related_typhoon(points: list[dict[str, Any]], name_text: str = "") -> bool:
    """活动台风是否与中国相关：路径点进入近海/陆地框，或预报点进入。"""
    if not points:
        return False
    for p in points:
        if in_china_bbox(p.get("lat"), p.get("lon")):
            return True
    # 名称本身不作为国外过滤依据
    return False


def is_china_related_tsunami_or_ocean(title: str, location: str = "", source: str = "") -> bool:
    text = f"{title} {location}"
    # 中央气象台海洋预警默认视为国内
    if "中央气象台" in (source or "") or "NMC" in (source or ""):
        return True
    # JMA 等仅当明确涉及中国/近海中文区域
    china_sea = ("中国", "台湾", "香港", "澳门", "南海", "东海", "黄海", "渤海", "钓鱼岛", "巴士海峡")
    if any(k in text for k in china_sea):
        return True
    # 英文里极少直接含 China；默认丢弃国外海啸
    return False


def project(lon: float, lat: float, bbox: tuple[float, float, float, float], w: int, h: int, pad: int = 30):
    min_lon, min_lat, max_lon, max_lat = bbox
    # y 轴北上
    x = pad + (lon - min_lon) / max(max_lon - min_lon, 1e-6) * (w - pad * 2)
    y = pad + (max_lat - lat) / max(max_lat - min_lat, 1e-6) * (h - pad * 2)
    return int(x), int(y)

# 粗粒度省界中心，用于路径点归属判断（示意级，非精确行政边界）
PROVINCE_ANCHORS = [
    ("北京市", 39.90, 116.40), ("天津市", 39.12, 117.20), ("河北省", 38.04, 114.51),
    ("山西省", 37.87, 112.55), ("内蒙古自治区", 40.82, 111.77), ("辽宁省", 41.80, 123.43),
    ("吉林省", 43.89, 125.32), ("黑龙江省", 45.75, 126.65), ("上海市", 31.23, 121.47),
    ("江苏省", 32.06, 118.80), ("浙江省", 30.27, 120.15), ("安徽省", 31.86, 117.28),
    ("福建省", 26.08, 119.30), ("江西省", 28.68, 115.86), ("山东省", 36.67, 117.00),
    ("河南省", 34.75, 113.65), ("湖北省", 30.59, 114.31), ("湖南省", 28.23, 112.94),
    ("广东省", 23.13, 113.26), ("广西壮族自治区", 22.82, 108.37), ("海南省", 20.02, 110.35),
    ("重庆市", 29.56, 106.55), ("四川省", 30.67, 104.07), ("贵州省", 26.65, 106.63),
    ("云南省", 25.04, 102.71), ("西藏自治区", 29.65, 91.13), ("陕西省", 34.27, 108.95),
    ("甘肃省", 36.06, 103.83), ("青海省", 36.62, 101.78), ("宁夏回族自治区", 38.47, 106.27),
    ("新疆维吾尔自治区", 43.83, 87.62), ("台湾省", 25.03, 121.57), ("香港特别行政区", 22.32, 114.17),
    ("澳门特别行政区", 22.20, 113.55),
    # 近海分区
    ("东海海域", 28.0, 125.0), ("南海海域", 16.0, 115.0), ("黄海海域", 35.0, 123.0), ("渤海海域", 38.7, 120.0),
]


def nearest_region(lat: float, lon: float) -> str:
    best = ""
    best_d = 1e18
    for name, pla, plo in PROVINCE_ANCHORS:
        d = (pla - lat) ** 2 + (plo - lon) ** 2
        if d < best_d:
            best_d = d
            best = name
    return best


def summarize_typhoon_impact(points: list[dict], latest: dict | None = None) -> dict:
    """根据路径点估算影响/邻近区域。"""
    regions = []
    seen = set()
    for p in points[-40:]:
        try:
            la = float(p.get("lat"))
            lo = float(p.get("lon"))
        except Exception:
            continue
        if not in_china_bbox(la, lo):
            continue
        name = nearest_region(la, lo)
        if name and name not in seen:
            seen.add(name)
            regions.append(name)
    current = ""
    if latest:
        try:
            current = nearest_region(float(latest.get("lat")), float(latest.get("lon")))
        except Exception:
            current = ""
    # 重点区域：当前所在 + 最近若干
    focus = []
    if current:
        focus.append(current)
    for r in regions:
        if r not in focus:
            focus.append(r)
        if len(focus) >= 6:
            break
    return {"current": current, "regions": focus}