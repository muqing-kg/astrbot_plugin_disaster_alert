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




# 主要城市锚点（用于台风位置细化到市；示意级最近点匹配）
CITY_ANCHORS = [
    # 华北
    ("北京市", 39.90, 116.41), ("天津市", 39.12, 117.19),
    ("石家庄市", 38.04, 114.51), ("唐山市", 39.63, 118.18), ("秦皇岛市", 39.94, 119.60),
    ("保定市", 38.87, 115.46), ("沧州市", 38.30, 116.84), ("廊坊市", 39.52, 116.68),
    ("太原市", 37.87, 112.55), ("大同市", 40.08, 113.30),
    ("呼和浩特市", 40.84, 111.75),
    # 东北
    ("沈阳市", 41.80, 123.43), ("大连市", 38.91, 121.61), ("鞍山市", 41.11, 122.99),
    ("锦州市", 41.10, 121.13), ("营口市", 40.67, 122.24), ("丹东市", 40.00, 124.38),
    ("长春市", 43.82, 125.32), ("吉林市", 43.84, 126.55),
    ("哈尔滨市", 45.80, 126.53),
    # 华东
    ("上海市", 31.23, 121.47),
    ("南京市", 32.06, 118.80), ("苏州市", 31.30, 120.62), ("无锡市", 31.49, 120.31),
    ("常州市", 31.81, 119.97), ("南通市", 32.01, 120.86), ("盐城市", 33.38, 120.14),
    ("扬州市", 32.39, 119.42), ("镇江市", 32.19, 119.45), ("泰州市", 32.48, 119.92),
    ("徐州市", 34.26, 117.18), ("淮安市", 33.61, 119.02), ("连云港市", 34.60, 119.22), ("宿迁市", 33.96, 118.28),
    ("杭州市", 30.27, 120.16), ("宁波市", 29.87, 121.54), ("温州市", 27.99, 120.70),
    ("嘉兴市", 30.75, 120.76), ("湖州市", 30.87, 120.09), ("绍兴市", 30.00, 120.58),
    ("金华市", 29.08, 119.65), ("台州市", 28.66, 121.42), ("舟山市", 29.99, 122.21), ("丽水市", 28.45, 119.92),
    ("合肥市", 31.82, 117.23), ("芜湖市", 31.35, 118.38), ("蚌埠市", 32.92, 117.39), ("安庆市", 30.53, 117.12),
    ("福州市", 26.07, 119.30), ("厦门市", 24.48, 118.09), ("泉州市", 24.87, 118.68), ("漳州市", 24.51, 117.65),
    ("南昌市", 28.68, 115.86), ("九江市", 29.71, 116.00), ("赣州市", 25.83, 114.94),
    ("济南市", 36.65, 117.12), ("青岛市", 36.07, 120.38), ("烟台市", 37.46, 121.45),
    ("潍坊市", 36.71, 119.16), ("临沂市", 35.10, 118.36), ("日照市", 35.42, 119.53),
    ("威海市", 37.51, 122.12), ("东营市", 37.43, 118.67), ("滨州市", 37.38, 117.97),
    ("德州市", 37.45, 116.36), ("菏泽市", 35.23, 115.48), ("济宁市", 35.41, 116.59), ("淄博市", 36.81, 118.05),
    # 华中华南
    ("郑州市", 34.75, 113.63), ("洛阳市", 34.62, 112.45), ("南阳市", 32.99, 112.53),
    ("武汉市", 30.59, 114.31), ("宜昌市", 30.69, 111.29), ("襄阳市", 32.04, 112.14),
    ("长沙市", 28.23, 112.94), ("岳阳市", 29.36, 113.13), ("衡阳市", 26.89, 112.57),
    ("广州市", 23.13, 113.26), ("深圳市", 22.54, 114.06), ("珠海市", 22.27, 113.58),
    ("汕头市", 23.35, 116.68), ("湛江市", 21.27, 110.36), ("茂名市", 21.66, 110.93),
    ("阳江市", 21.86, 111.98), ("江门市", 22.58, 113.08), ("中山市", 22.52, 113.39),
    ("东莞市", 23.02, 113.75), ("惠州市", 23.11, 114.42), ("清远市", 23.68, 113.06),
    ("南宁市", 22.82, 108.37), ("北海市", 21.47, 109.12), ("防城港市", 21.69, 108.35),
    ("海口市", 20.04, 110.20), ("三亚市", 18.25, 109.51),
    # 西南西北
    ("重庆市", 29.56, 106.55), ("成都市", 30.57, 104.07), ("绵阳市", 31.47, 104.74),
    ("贵阳市", 26.65, 106.63), ("昆明市", 25.04, 102.71),
    ("西安市", 34.34, 108.94), ("兰州市", 36.06, 103.83), ("西宁市", 36.62, 101.78),
    ("银川市", 38.49, 106.23), ("乌鲁木齐市", 43.83, 87.62),
    ("拉萨市", 29.65, 91.13), ("台北市", 25.03, 121.57), ("香港", 22.32, 114.17), ("澳门", 22.20, 113.55),
    # 海域参考点
    ("东海海域", 28.0, 125.0), ("南海海域", 16.0, 115.0), ("黄海海域", 35.0, 123.0), ("渤海海域", 38.7, 120.0),
]


def nearest_city(lat: float, lon: float) -> str:
    best = ""
    best_d = 1e18
    for name, pla, plo in CITY_ANCHORS:
        d = (pla - lat) ** 2 + (plo - lon) ** 2
        if d < best_d:
            best_d = d
            best = name
    # 距离过远时回退省级
    if best_d > (2.2 ** 2):
        return nearest_region(lat, lon)
    return best


def wind_level_from_ms(wind_ms) -> str:
    """将 m/s 转为中国气象风力等级文案。"""
    try:
        v = float(wind_ms)
    except (TypeError, ValueError):
        return ""
    # 蒲福风级近似阈值（m/s）
    table = [
        (0.2, 0), (1.5, 1), (3.3, 2), (5.4, 3), (7.9, 4), (10.7, 5),
        (13.8, 6), (17.1, 7), (20.7, 8), (24.4, 9), (28.4, 10),
        (32.6, 11), (36.9, 12), (41.4, 13), (46.1, 14), (50.9, 15),
        (56.0, 16), (61.2, 17),
    ]
    level = 17
    for upper, lv in table:
        if v <= upper:
            level = lv
            break
    if v > 61.2:
        level = 17
    return f"{level}级"


def intensity_cn_with_wind(intensity_code: str, wind_ms) -> str:
    code = str(intensity_code or "").upper()
    base = {
        "TD": "热带低压",
        "TS": "热带风暴",
        "STS": "强热带风暴",
        "TY": "台风",
        "STY": "强台风",
        "SUPERTY": "超强台风",
        "SUPER TY": "超强台风",
    }.get(code, intensity_code or "未知")
    # SuperTY variants
    if "SUPER" in code:
        base = "超强台风"
    wind_lv = wind_level_from_ms(wind_ms)
    if wind_lv:
        return f"{base}（近中心风力{wind_lv}，{wind_ms} m/s）"
    return base



# 城市到省份映射（用于显示 省·市）
CITY_TO_PROVINCE = {
    "北京市": "北京市", "天津市": "天津市", "上海市": "上海市", "重庆市": "重庆市",
    "石家庄市": "河北省", "唐山市": "河北省", "秦皇岛市": "河北省", "保定市": "河北省", "沧州市": "河北省", "廊坊市": "河北省",
    "太原市": "山西省", "大同市": "山西省",
    "呼和浩特市": "内蒙古自治区",
    "沈阳市": "辽宁省", "大连市": "辽宁省", "鞍山市": "辽宁省", "锦州市": "辽宁省", "营口市": "辽宁省", "丹东市": "辽宁省",
    "长春市": "吉林省", "吉林市": "吉林省",
    "哈尔滨市": "黑龙江省",
    "南京市": "江苏省", "苏州市": "江苏省", "无锡市": "江苏省", "常州市": "江苏省", "南通市": "江苏省", "盐城市": "江苏省",
    "扬州市": "江苏省", "镇江市": "江苏省", "泰州市": "江苏省", "徐州市": "江苏省", "淮安市": "江苏省", "连云港市": "江苏省", "宿迁市": "江苏省",
    "杭州市": "浙江省", "宁波市": "浙江省", "温州市": "浙江省", "嘉兴市": "浙江省", "湖州市": "浙江省", "绍兴市": "浙江省",
    "金华市": "浙江省", "台州市": "浙江省", "舟山市": "浙江省", "丽水市": "浙江省",
    "合肥市": "安徽省", "芜湖市": "安徽省", "蚌埠市": "安徽省", "安庆市": "安徽省",
    "福州市": "福建省", "厦门市": "福建省", "泉州市": "福建省", "漳州市": "福建省",
    "南昌市": "江西省", "九江市": "江西省", "赣州市": "江西省",
    "济南市": "山东省", "青岛市": "山东省", "烟台市": "山东省", "潍坊市": "山东省", "临沂市": "山东省", "日照市": "山东省",
    "威海市": "山东省", "东营市": "山东省", "滨州市": "山东省", "德州市": "山东省", "菏泽市": "山东省", "济宁市": "山东省", "淄博市": "山东省",
    "郑州市": "河南省", "洛阳市": "河南省", "南阳市": "河南省",
    "武汉市": "湖北省", "宜昌市": "湖北省", "襄阳市": "湖北省",
    "长沙市": "湖南省", "岳阳市": "湖南省", "衡阳市": "湖南省",
    "广州市": "广东省", "深圳市": "广东省", "珠海市": "广东省", "汕头市": "广东省", "湛江市": "广东省", "茂名市": "广东省",
    "阳江市": "广东省", "江门市": "广东省", "中山市": "广东省", "东莞市": "广东省", "惠州市": "广东省", "清远市": "广东省",
    "南宁市": "广西壮族自治区", "北海市": "广西壮族自治区", "防城港市": "广西壮族自治区",
    "海口市": "海南省", "三亚市": "海南省",
    "成都市": "四川省", "绵阳市": "四川省",
    "贵阳市": "贵州省", "昆明市": "云南省", "拉萨市": "西藏自治区",
    "西安市": "陕西省", "兰州市": "甘肃省", "西宁市": "青海省", "银川市": "宁夏回族自治区", "乌鲁木齐市": "新疆维吾尔自治区",
    "台北市": "台湾省", "香港": "香港特别行政区", "澳门": "澳门特别行政区",
    "东海海域": "东海海域", "南海海域": "南海海域", "黄海海域": "黄海海域", "渤海海域": "渤海海域",
}


def format_region_name(city_or_region: str) -> str:
    name = str(city_or_region or "").strip()
    if not name:
        return ""
    # 已是省级或海域
    if name.endswith(("省", "自治区", "特别行政区", "海域")) or name in {"北京市", "天津市", "上海市", "重庆市"}:
        return name
    prov = CITY_TO_PROVINCE.get(name)
    if prov:
        # 直辖市不重复
        if prov == name:
            return name
        return f"{prov}·{name}"
    return name


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def wind_level_number(wind_ms) -> int | None:
    try:
        v = float(wind_ms)
    except (TypeError, ValueError):
        return None
    table = [
        (0.2, 0), (1.5, 1), (3.3, 2), (5.4, 3), (7.9, 4), (10.7, 5),
        (13.8, 6), (17.1, 7), (20.7, 8), (24.4, 9), (28.4, 10),
        (32.6, 11), (36.9, 12), (41.4, 13), (46.1, 14), (50.9, 15),
        (56.0, 16), (61.2, 17),
    ]
    for upper, lv in table:
        if v <= upper:
            return lv
    return 17


def is_near_china_coast_or_land(lat: float, lon: float) -> bool:
    """是否接近中国近岸/陆地。远海（即使很强）不报。"""
    if not in_china_bbox(lat, lon):
        return False
    if lon >= 126 and 18 <= lat <= 34:
        return False
    if lon >= 128 and lat < 40:
        return False
    return True


def top_impact_cities(lat: float, lon: float, *, wind_ms=None, limit: int = 3) -> list[tuple[str, float]]:
    """按风力半径+距离，选出当前最可能受实质影响的城市。"""
    lv = wind_level_number(wind_ms)
    if lv is None or lv < 8:
        return []
    if lv >= 12:
        max_km, hard_km = 420.0, 380.0
    elif lv >= 10:
        max_km, hard_km = 340.0, 300.0
    else:
        max_km, hard_km = 260.0, 230.0

    ranked: list[tuple[float, str]] = []
    for name, pla, plo in CITY_ANCHORS:
        if name.endswith("海域"):
            continue
        d = haversine_km(lat, lon, pla, plo)
        if d <= max_km:
            ranked.append((d, name))
    ranked.sort(key=lambda x: x[0])

    out: list[tuple[str, float]] = []
    for d, name in ranked:
        if d > hard_km:
            continue
        label = format_region_name(name)
        if label and all(label != x[0] for x in out):
            out.append((label, d))
        if len(out) >= limit:
            break
    return out


def summarize_typhoon_impact(
    points: list[dict],
    latest: dict | None = None,
    forecast_points: list[dict] | None = None,
) -> dict:
    """只根据当前实况：够近+够强+有明确影响区 才 should_report。

    明确不用预测；远海（哪怕很强）不报。
    """
    _ = points, forecast_points
    empty = {
        "should_report": False,
        "near_land": False,
        "impact_regions": [],
        "impact_text": "",
        "region_key": "skip",
        "wind_level": None,
    }
    if not latest:
        return empty
    try:
        la = float(latest.get("lat"))
        lo = float(latest.get("lon"))
    except Exception:
        return empty

    wind = latest.get("wind")
    lv = wind_level_number(wind)
    near = is_near_china_coast_or_land(la, lo)
    if not near or lv is None or lv < 8:
        return {
            "should_report": False,
            "near_land": near,
            "impact_regions": [],
            "impact_text": "",
            "region_key": "skip",
            "wind_level": lv,
        }

    pairs = top_impact_cities(la, lo, wind_ms=wind, limit=3)
    if not pairs:
        return {
            "should_report": False,
            "near_land": near,
            "impact_regions": [],
            "impact_text": "",
            "region_key": "skip",
            "wind_level": lv,
        }

    regions = [name for name, _ in pairs]
    impact_text = "、".join(regions)
    return {
        "should_report": True,
        "near_land": True,
        "impact_regions": regions,
        "impact_text": impact_text,
        "region_key": "|".join(regions),
        "wind_level": lv,
        "current_label": regions[0],
        "regions_label": regions,
        "upcoming_label": [],
    }

