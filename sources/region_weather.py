"""按省市区查询中央气象台实况/预报（用户主动请求）。"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

from ..http_client import HttpClient
from .weather import NMC_ALARM_URL, extract_alarm_list, extract_level, extract_location

NMC_PAGE = "https://www.nmc.cn/"
PROVINCE_ALL = "https://www.nmc.cn/rest/province/all"
PROVINCE_CITIES = "https://www.nmc.cn/rest/province/{code}"
WEATHER_URL = "https://www.nmc.cn/rest/weather"

# 省级别名
PROVINCE_ALIASES: dict[str, str] = {
    "北京": "北京市",
    "北京市": "北京市",
    "天津": "天津市",
    "天津市": "天津市",
    "上海": "上海市",
    "上海市": "上海市",
    "重庆": "重庆市",
    "重庆市": "重庆市",
    "河北": "河北省",
    "河北省": "河北省",
    "山西": "山西省",
    "山西省": "山西省",
    "辽宁": "辽宁省",
    "辽宁省": "辽宁省",
    "吉林": "吉林省",
    "吉林省": "吉林省",
    "黑龙江": "黑龙江省",
    "黑龙江省": "黑龙江省",
    "江苏": "江苏省",
    "江苏省": "江苏省",
    "浙江": "浙江省",
    "浙江省": "浙江省",
    "安徽": "安徽省",
    "安徽省": "安徽省",
    "福建": "福建省",
    "福建省": "福建省",
    "江西": "江西省",
    "江西省": "江西省",
    "山东": "山东省",
    "山东省": "山东省",
    "河南": "河南省",
    "河南省": "河南省",
    "湖北": "湖北省",
    "湖北省": "湖北省",
    "湖南": "湖南省",
    "湖南省": "湖南省",
    "广东": "广东省",
    "广东省": "广东省",
    "海南": "海南省",
    "海南省": "海南省",
    "四川": "四川省",
    "四川省": "四川省",
    "贵州": "贵州省",
    "贵州省": "贵州省",
    "云南": "云南省",
    "云南省": "云南省",
    "陕西": "陕西省",
    "陕西省": "陕西省",
    "甘肃": "甘肃省",
    "甘肃省": "甘肃省",
    "青海": "青海省",
    "青海省": "青海省",
    "台湾": "台湾省",
    "台湾省": "台湾省",
    "内蒙古": "内蒙古自治区",
    "内蒙古自治区": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "广西壮族自治区": "广西壮族自治区",
    "西藏": "西藏自治区",
    "西藏自治区": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "宁夏回族自治区": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
    "新疆维吾尔自治区": "新疆维吾尔自治区",
    "香港": "香港特别行政区",
    "香港特别行政区": "香港特别行政区",
    "澳门": "澳门特别行政区",
    "澳门特别行政区": "澳门特别行政区",
}


@dataclass
class DayForecast:
    date: str
    day_weather: str
    night_weather: str
    day_temp: str
    night_temp: str
    day_wind: str
    night_wind: str


@dataclass
class RegionWeather:
    query: str
    province: str
    city: str
    station_code: str
    publish_time: str
    weather: str
    temperature: str
    feels_like: str
    humidity: str
    wind: str
    rain: str
    aqi: str
    aqi_text: str
    warn_text: str
    warns: list[dict] = field(default_factory=list)
    forecasts: list[DayForecast] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    source: str = "中央气象台 NMC"
    ok: bool = True
    message: str = ""


class RegionIndex:
    """缓存省/市站点索引，支持中文地区模糊匹配。"""

    CACHE_TTL = 7 * 24 * 3600

    def __init__(self, cache_path: str | None = None) -> None:
        self.provinces: list[dict[str, str]] = []
        self.cities: list[dict[str, str]] = []
        self.loaded = False
        self.cache_path = cache_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "data",
            "region_index_cache.json",
        )

    def _load_cache(self) -> bool:
        try:
            if not os.path.exists(self.cache_path):
                return False
            with open(self.cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            if time.time() - float(data.get("ts") or 0) > self.CACHE_TTL:
                return False
            provinces = data.get("provinces") or []
            cities = data.get("cities") or []
            if not isinstance(provinces, list) or not isinstance(cities, list) or not cities:
                return False
            self.provinces = provinces
            self.cities = cities
            self.loaded = True
            logger.info("地区索引命中缓存：省 %d，站点 %d", len(self.provinces), len(self.cities))
            return True
        except Exception as e:
            logger.warning("地区索引缓存读取失败: %s", e)
            return False

    def _save_cache(self) -> None:
        try:
            parent = os.path.dirname(self.cache_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ts": int(time.time()),
                        "provinces": self.provinces,
                        "cities": self.cities,
                    },
                    f,
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.warning("地区索引缓存写入失败: %s", e)

    async def ensure(self, client: HttpClient) -> None:
        if self.loaded and self.cities:
            return
        if self._load_cache():
            return
        try:
            pdata = await client.get_json(PROVINCE_ALL, headers={"Referer": NMC_PAGE})
        except Exception as e:
            logger.warning("省级列表获取失败: %s", e)
            raise

        if not isinstance(pdata, list):
            raise RuntimeError("省级列表格式异常")

        self.provinces = []
        self.cities = []
        # 并发拉取各省城市列表
        import asyncio

        async def one(code: str, name: str):
            try:
                cities = await client.get_json(
                    PROVINCE_CITIES.format(code=code),
                    headers={"Referer": NMC_PAGE},
                )
            except Exception as e:
                logger.warning("城市列表获取失败 %s: %s", name, e)
                return code, name, []
            return code, name, cities if isinstance(cities, list) else []

        tasks = []
        for p in pdata:
            if not isinstance(p, dict):
                continue
            code = str(p.get("code") or "")
            name = str(p.get("name") or "")
            if not code or not name:
                continue
            self.provinces.append({"code": code, "name": name})
            tasks.append(one(code, name))

        results = await asyncio.gather(*tasks)
        for code, name, cities in results:
            for c in cities:
                if not isinstance(c, dict):
                    continue
                self.cities.append(
                    {
                        "code": str(c.get("code") or ""),
                        "province": str(c.get("province") or name),
                        "city": str(c.get("city") or ""),
                        "url": str(c.get("url") or ""),
                    }
                )
        self.loaded = True
        self._save_cache()
        logger.info("地区索引已加载：省 %d，站点 %d", len(self.provinces), len(self.cities))

    def search(self, text: str, limit: int = 8) -> list[dict[str, str]]:
        q = _normalize_place(text)
        if not q:
            return []

        # 1) 纯省名：回省会
        for alias, full in PROVINCE_ALIASES.items():
            if q in {_normalize_place(alias), _normalize_place(full)}:
                return self._province_default_cities(full, limit)

        # 2) 省+市 组合，如 四川成都 / 四川省成都市
        prov_city = self._match_province_city_combo(q)
        if prov_city:
            return prov_city[:limit]

        scored: list[tuple[int, dict[str, str]]] = []
        q_core = _strip_admin_suffix(q)
        for c in self.cities:
            city_n = _normalize_place(c["city"])
            prov_n = _normalize_place(c["province"])
            city_core = _strip_admin_suffix(city_n)
            full = prov_n + city_n
            score = 0
            if q in {city_n, city_core, full, prov_n + city_core}:
                score = 100
            elif q_core and q_core == city_core:
                score = 98
            elif city_n.startswith(q) or q.startswith(city_n):
                score = 80
            elif q_core and (city_core.startswith(q_core) or q_core.startswith(city_core)):
                score = 78
            elif q in city_n or city_n in q:
                score = 60
            elif q_core and (q_core in city_core or city_core in q_core):
                # 注意：避免 "成都" 命中 "成县" 的高分
                if min(len(q_core), len(city_core)) >= 2 and (
                    city_core.startswith(q_core) or q_core.startswith(city_core)
                ):
                    score = 55
            elif q in full:
                score = 40
            if score > 0:
                # 稍偏好更短、更像地级市名的结果
                score = score * 100 - len(city_n)
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        out: list[dict[str, str]] = []
        seen = set()
        for _, c in scored:
            if c["code"] in seen:
                continue
            seen.add(c["code"])
            out.append(c)
            if len(out) >= limit:
                break
        return out

    def _province_default_cities(self, full_province: str, limit: int) -> list[dict[str, str]]:
        prov_cities = [
            c for c in self.cities
            if _normalize_place(c["province"]) == _normalize_place(full_province)
        ]
        if not prov_cities:
            return []
        capital_hints = {
            "河北省": "石家庄", "山西省": "太原", "辽宁省": "沈阳", "吉林省": "长春",
            "黑龙江省": "哈尔滨", "江苏省": "南京", "浙江省": "杭州", "安徽省": "合肥",
            "福建省": "福州", "江西省": "南昌", "山东省": "济南", "河南省": "郑州",
            "湖北省": "武汉", "湖南省": "长沙", "广东省": "广州", "海南省": "海口",
            "四川省": "成都", "贵州省": "贵阳", "云南省": "昆明", "陕西省": "西安",
            "甘肃省": "兰州", "青海省": "西宁", "台湾省": "台北",
            "内蒙古自治区": "呼和浩特", "广西壮族自治区": "南宁", "西藏自治区": "拉萨",
            "宁夏回族自治区": "银川", "新疆维吾尔自治区": "乌鲁木齐",
            "北京市": "北京", "天津市": "天津", "上海市": "上海", "重庆市": "重庆",
        }
        hint = capital_hints.get(full_province, "")
        if hint:
            preferred = [c for c in prov_cities if hint in c["city"]]
            if preferred:
                rest = [c for c in prov_cities if c not in preferred]
                return preferred + rest[: max(0, limit - len(preferred))]
        return prov_cities[:limit]

    def _match_province_city_combo(self, q: str) -> list[dict[str, str]]:
        # 尝试拆出省前缀
        for alias, full in sorted(PROVINCE_ALIASES.items(), key=lambda x: -len(x[0])):
            a = _normalize_place(alias)
            f = _normalize_place(full)
            rest = ""
            if q.startswith(f):
                rest = q[len(f):]
            elif q.startswith(a):
                rest = q[len(a):]
            else:
                continue
            rest = rest.strip()
            if not rest:
                return self._province_default_cities(full, 8)
            rest_core = _strip_admin_suffix(rest)
            prov_cities = [
                c for c in self.cities
                if _normalize_place(c["province"]) == f
            ]
            hits = []
            for c in prov_cities:
                city_n = _normalize_place(c["city"])
                city_core = _strip_admin_suffix(city_n)
                if rest in {city_n, city_core} or rest_core == city_core:
                    hits.append((100, c))
                elif city_core.startswith(rest_core) or rest_core.startswith(city_core):
                    hits.append((80, c))
                elif rest_core and rest_core in city_core:
                    hits.append((50, c))
            hits.sort(key=lambda x: -x[0])
            if hits:
                return [c for _, c in hits]
        return []


_index = RegionIndex()



async def _fetch_local_alerts(client: HttpClient, province: str, city: str) -> list[dict]:
    """拉取与该地区相关的当前预警（用于卡片完整展示）。"""
    try:
        data = await client.get_json(
            NMC_ALARM_URL,
            headers={"Referer": NMC_PAGE},
            params={
                "pageNo": 1,
                "pageSize": 100,
                "signaltype": "",
                "signallevel": "",
                "province": "",
            },
        )
    except Exception as e:
        logger.warning("地区预警获取失败: %s", e)
        return []

    rows = extract_alarm_list(data)
    keys = []
    for x in (city, province):
        s = str(x or "").strip()
        if not s:
            continue
        keys.append(s)
        for suf in ("省", "市", "区", "县", "自治州", "地区", "盟"):
            if s.endswith(suf) and len(s) > len(suf):
                keys.append(s[: -len(suf)])
    keys = [k for k in keys if len(k) >= 2]
    # 去重保序
    seen = set()
    keys2 = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            keys2.append(k)

    out: list[dict] = []
    city_keys = []
    for s in [str(city or "").strip()]:
        if not s:
            continue
        city_keys.append(s)
        for suf in ("市", "区", "县", "自治州", "地区", "盟"):
            if s.endswith(suf) and len(s) > len(suf):
                city_keys.append(s[: -len(suf)])
    city_keys = [k for k in dict.fromkeys(city_keys) if len(k) >= 2]

    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        loc = extract_location(title)
        # 优先：标题/地点必须命中城市名，避免“同省其他市”误入
        hit = False
        for k in city_keys:
            if k in title or k in loc:
                hit = True
                break
        if not hit and not city_keys:
            # 仅当查询到省级、没有城市时，才用省名匹配
            for k in keys2:
                if k in title or k in loc:
                    hit = True
                    break
        if not hit:
            continue
        level = extract_level(title) or "预警"
        out.append({
            "level": level,
            "text": title,
            "time": str(item.get("issuetime") or "").replace("/", "-"),
            "id": str(item.get("alertid") or ""),
        })
        if len(out) >= 6:
            break
    return out


async def query_region_weather(client: HttpClient, place: str) -> RegionWeather:
    place = (place or "").strip()
    if not place:
        return RegionWeather(
            query="",
            province="",
            city="",
            station_code="",
            publish_time="",
            weather="",
            temperature="",
            feels_like="",
            humidity="",
            wind="",
            rain="",
            aqi="",
            aqi_text="",
            warn_text="",
            ok=False,
            message="请提供地区，例如：天气 北京 / 天气 四川成都 / 天气 朝阳",
        )

    await _index.ensure(client)
    matches = _index.search(place)
    if not matches:
        return RegionWeather(
            query=place,
            province="",
            city="",
            station_code="",
            publish_time="",
            weather="",
            temperature="",
            feels_like="",
            humidity="",
            wind="",
            rain="",
            aqi="",
            aqi_text="",
            warn_text="",
            ok=False,
            message=f"未找到地区「{place}」，请换用省/市/区县名称试试。",
        )

    # 多个高相关结果时，若分数接近且城市名不同，返回候选
    top = matches[0]
    ambiguous = []
    if len(matches) > 1:
        # 查询很短或匹配到多个不同城市
        names = [f"{m['province']}{m['city']}" for m in matches[:5]]
        unique_cities = {m["city"] for m in matches[:5]}
        if len(unique_cities) > 1 and len(_normalize_place(place)) <= 2:
            ambiguous = names

    station_code = top["code"]
    try:
        data = await client.get_json(
            WEATHER_URL,
            headers={"Referer": NMC_PAGE},
            params={"stationid": station_code},
        )
    except Exception as e:
        logger.warning("天气查询失败 %s: %s", station_code, e)
        return RegionWeather(
            query=place,
            province=top.get("province", ""),
            city=top.get("city", ""),
            station_code=station_code,
            publish_time="",
            weather="",
            temperature="",
            feels_like="",
            humidity="",
            wind="",
            rain="",
            aqi="",
            aqi_text="",
            warn_text="",
            ok=False,
            message=f"天气数据获取失败：{e}",
        )

    payload = data.get("data") if isinstance(data, dict) else None
    if not isinstance(payload, dict) or not payload:
        return RegionWeather(
            query=place,
            province=top.get("province", ""),
            city=top.get("city", ""),
            station_code=station_code,
            publish_time="",
            weather="",
            temperature="",
            feels_like="",
            humidity="",
            wind="",
            rain="",
            aqi="",
            aqi_text="",
            warn_text="",
            ok=False,
            message="该站点暂无天气数据。",
            candidates=ambiguous,
        )

    real = payload.get("real") if isinstance(payload.get("real"), dict) else {}
    weather = real.get("weather") if isinstance(real.get("weather"), dict) else {}
    wind = real.get("wind") if isinstance(real.get("wind"), dict) else {}
    warn = real.get("warn") if isinstance(real.get("warn"), dict) else {}
    air = payload.get("air") if isinstance(payload.get("air"), dict) else {}
    predict = payload.get("predict") if isinstance(payload.get("predict"), dict) else {}

    warn_text = ""
    alert = str(warn.get("alert") or "")
    if alert and alert != "9999":
        warn_text = alert
    elif str(warn.get("signaltype") or "") not in ("", "9999"):
        warn_text = f"{warn.get('signaltype')}{warn.get('signallevel') or ''}预警"

    forecasts: list[DayForecast] = []
    details = predict.get("detail") if isinstance(predict.get("detail"), list) else []
    for d in details[:7]:
        if not isinstance(d, dict):
            continue
        day = d.get("day") if isinstance(d.get("day"), dict) else {}
        night = d.get("night") if isinstance(d.get("night"), dict) else {}
        dw = day.get("weather") if isinstance(day.get("weather"), dict) else {}
        nw = night.get("weather") if isinstance(night.get("weather"), dict) else {}
        dwind = day.get("wind") if isinstance(day.get("wind"), dict) else {}
        nwind = night.get("wind") if isinstance(night.get("wind"), dict) else {}
        forecasts.append(
            DayForecast(
                date=str(d.get("date") or ""),
                day_weather=str(dw.get("info") or "-"),
                night_weather=str(nw.get("info") or "-"),
                day_temp=str(dw.get("temperature") or "-"),
                night_temp=str(nw.get("temperature") or "-"),
                day_wind=f"{dwind.get('direct') or ''}{dwind.get('power') or ''}".strip() or "-",
                night_wind=f"{nwind.get('direct') or ''}{nwind.get('power') or ''}".strip() or "-",
            )
        )

    station = real.get("station") if isinstance(real.get("station"), dict) else {}
    local_alerts = await _fetch_local_alerts(
        client,
        province=str(station.get("province") or top.get("province") or ""),
        city=str(station.get("city") or top.get("city") or ""),
    )
    # 天气现象优先 real.info；为空时回退到今日白天预报
    weather_info = str(weather.get("info") or "").strip()
    if not weather_info or weather_info in {"-", "9999"}:
        if forecasts:
            weather_info = forecasts[0].day_weather or forecasts[0].night_weather or "-"
        else:
            weather_info = "-"

    wind_txt = " ".join(
        x for x in [
            str(wind.get("direct") or "").strip(),
            str(wind.get("power") or "").strip(),
            (f"{wind.get('speed')}m/s" if wind.get("speed") not in (None, "", "9999") else ""),
        ] if x
    ) or "-"

    return RegionWeather(
        query=place,
        province=str(station.get("province") or top.get("province") or ""),
        city=str(station.get("city") or top.get("city") or ""),
        station_code=station_code,
        publish_time=str(real.get("publish_time") or ""),
        weather=weather_info,
        temperature=_fmt_num(weather.get("temperature"), "℃"),
        feels_like=_fmt_num(weather.get("feelst"), "℃"),
        humidity=_fmt_num(weather.get("humidity"), "%"),
        wind=wind_txt,
        rain=_fmt_num(weather.get("rain"), "mm"),
        aqi=str(air.get("aqi") if air.get("aqi") is not None else "-"),
        aqi_text=str(air.get("text") or "-"),
        warn_text=(";".join([a["text"] for a in local_alerts]) if local_alerts else (warn_text or "当前站点无特别预警")),
        warns=local_alerts,
        forecasts=forecasts,
        candidates=ambiguous,
        ok=True,
        message="",
    )



def _strip_admin_suffix(text: str) -> str:
    s = str(text or '')
    return re.sub(r'(特别行政区|自治区|地区|自治州|州|盟|省|市|区|县)$', '', s)


def _normalize_place(text: str) -> str:
    s = str(text or "").strip().lower()
    s = s.replace(" ", "").replace("　", "")
    s = s.replace("中国", "")
    return s


def _fmt_num(v: Any, unit: str = "") -> str:
    if v is None or v == "" or v == "9999":
        return "-"
    try:
        f = float(v)
        if abs(f - 9999) < 0.1:
            return "-"
        if f.is_integer():
            return f"{int(f)}{unit}"
        return f"{f:.1f}{unit}"
    except (TypeError, ValueError):
        return f"{v}{unit}"