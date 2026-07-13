"""极危天气主动推送：仅推送最极端、可能危及人身财产安全的红色预警。

默认不推送普通极端天气（橙/黄/蓝），避免全国群刷屏。
暴雨季会对同省同类型做合并，减少连发刷屏。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from astrbot.api import logger

from ..http_client import HttpClient
from ..models import DisasterEvent
from .weather import (
    LEVEL_RANK,
    NMC_ALARM_URL,
    NMC_PAGE,
    absolute_nmc_url,
    extract_alarm_list,
    extract_level,
    extract_location,
)

CRITICAL_RED_KEYWORDS = [
    "暴雨",
    "台风",
    "暴雪",
    "寒潮",
    "大风",
    "强对流",
    "冰雹",
    "雷雨大风",
    "山洪",
    "地质灾害",
    "森林火险",
    "沙尘暴",
    "道路结冰",
]

CRITICAL_ADVICE = (
    "【安全忠告】该预警属极高风险等级。请立即远离危险区域，"
    "避免涉水、登山、临水临崖与户外聚集；听从当地政府与应急部门转移指令，"
    "勿传谣、勿冒险围观，确保人身与财产安全。"
)

PROVINCE_PREFIXES = [
    "北京市", "天津市", "上海市", "重庆市",
    "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "海南省",
    "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区",
    "香港特别行政区", "澳门特别行政区",
]


def _province_of(title: str, location: str = "") -> str:
    text = f"{location} {title}"
    for p in sorted(PROVINCE_PREFIXES, key=len, reverse=True):
        if p in text:
            return p
    # 兜底：取地点前缀
    loc = (location or "").strip()
    if not loc:
        return "多地"
    return loc[:3]


def _hazard_of(title: str, keywords: list[str]) -> str:
    for k in keywords:
        if k in title:
            return k
    return "高危天气"


def _area_short(title: str, location: str = "") -> str:
    # 尽量取“xx市/县/区”短名
    loc = (location or "").strip()
    if loc:
        # 去掉省级前缀
        for p in PROVINCE_PREFIXES:
            if loc.startswith(p):
                loc = loc[len(p):]
                break
        loc = loc.replace("气象台", "").strip(" ·")
        if loc:
            return loc
    m = re.search(r"([一-鿿]{2,12}(?:市|县|区|旗|州))", title)
    return m.group(1) if m else "相关地区"


async def fetch_critical_life_alerts(
    client: HttpClient,
    *,
    enabled: bool = True,
    keywords: list[str] | None = None,
    page_size: int = 100,
    merge: bool = True,
) -> list[DisasterEvent]:
    if not enabled:
        return []

    kws = [k.strip() for k in (keywords or CRITICAL_RED_KEYWORDS) if k and str(k).strip()]
    if not kws:
        kws = list(CRITICAL_RED_KEYWORDS)

    try:
        data = await client.get_json(
            NMC_ALARM_URL,
            headers={"Referer": NMC_PAGE},
            params={
                "pageNo": 1,
                "pageSize": page_size,
                "signaltype": "",
                "signallevel": "",
                "province": "",
            },
        )
    except Exception as e:
        logger.warning("极危天气预警获取失败: %s", e)
        return []

    rows = extract_alarm_list(data)
    raw_events: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        level = extract_level(title)
        if LEVEL_RANK.get(level, 0) < 4:
            continue
        if not any(k in title for k in kws):
            continue
        alert_id = str(item.get("alertid") or "").strip() or f"{item.get('issuetime')}|{title}"
        loc = extract_location(title)
        raw_events.append(
            {
                "alert_id": alert_id,
                "title": title,
                "location": loc,
                "province": _province_of(title, loc),
                "hazard": _hazard_of(title, kws),
                "time": str(item.get("issuetime") or "").replace("/", "-"),
                "url": absolute_nmc_url(str(item.get("url") or "")),
                "area": _area_short(title, loc),
            }
        )

    if not raw_events:
        return []

    if not merge:
        return [
            DisasterEvent(
                source="中央气象台 NMC",
                category="极危天气预警",
                event_id=f"critical-{e['alert_id']}",
                title=e["title"],
                summary="已达红色预警，可能严重威胁当地人身与财产安全。",
                occurred_at=e["time"],
                location=e["location"],
                level="红色",
                url=e["url"],
                advice=CRITICAL_ADVICE,
                raw=e,
            )
            for e in raw_events
        ]

    # 合并：同省 + 同灾害类型
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in raw_events:
        groups[(e["province"], e["hazard"])].append(e)

    events: list[DisasterEvent] = []
    for (prov, hazard), items in groups.items():
        items = sorted(items, key=lambda x: x.get("time") or "", reverse=True)
        areas = []
        for it in items:
            a = it.get("area") or ""
            if a and a not in areas:
                areas.append(a)
            if len(areas) >= 6:
                break
        latest_time = items[0].get("time") or ""
        # 稳定去重键：省+类型+日期小时（同小时内合并为一条）
        hour_key = latest_time[:13] if len(latest_time) >= 13 else latest_time
        if len(items) == 1:
            it = items[0]
            title = it["title"]
            location = it["location"] or prov
            summary = "已达红色预警，可能严重威胁当地人身与财产安全。"
            event_id = f"critical-{it['alert_id']}"
        else:
            area_text = "、".join(areas[:5])
            more = f"等{len(items)}地" if len(items) > 5 else f"{len(items)}地"
            title = f"{prov}多地发布{hazard}红色预警（{more}）"
            location = f"{prov}：{area_text}"
            summary = f"共 {len(items)} 条红色{hazard}预警，可能严重威胁当地人身与财产安全。"
            # 同省同类型同小时只推一条
            event_id = f"critical-merge-{prov}-{hazard}-{hour_key}"

        events.append(
            DisasterEvent(
                source="中央气象台 NMC",
                category="极危天气预警",
                event_id=event_id,
                title=title,
                summary=summary,
                occurred_at=latest_time,
                location=location,
                level="红色",
                url=items[0].get("url") or NMC_PAGE,
                advice=CRITICAL_ADVICE,
                raw={
                    "merge": len(items) > 1,
                    "count": len(items),
                    "province": prov,
                    "hazard": hazard,
                    "areas": areas,
                    "alert_ids": [x.get("alert_id") for x in items],
                },
            )
        )
    # 新在前
    events.sort(key=lambda e: e.occurred_at or "", reverse=True)
    return events
