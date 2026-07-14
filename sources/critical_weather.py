"""极危天气主动推送：仅推送最极端、可能危及人身财产安全的红色预警。

默认不推送普通极端天气（橙/黄/蓝），避免全国群刷屏。
暴雨季会对同省同类型做合并，减少连发刷屏。
首次全量通报；地区集合扩大时用「新增地区」专用文案。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
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
    noise = (
        "气象台", "水利厅", "水利局", "应急管理局", "自然资源局",
        "和", "与", "发布", "气象风险", "红色预警信号", "红色预警",
    )
    text = f"{location} {title}"
    for n in noise:
        text = text.replace(n, " ")
    m = re.search(r"([一-鿿]{2,8}市(?:[一-鿿]{1,8}(?:区|县|旗))?)", text)
    if m:
        return m.group(1)
    m = re.search(r"([一-鿿]{2,10}(?:区|县|旗|自治州|州))", text)
    if m:
        val = m.group(1)
        if any(bad in val for bad in ("水利", "气象", "应急", "自然")):
            return "相关地区"
        return val
    return "相关地区"


def _parse_alert_time(text: str) -> datetime | None:
    s = str(text or "").strip().replace("/", "-")
    for n, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(s[:n], fmt)
        except Exception:
            continue
    return None


def _areas_fingerprint(areas: list[str]) -> str:
    key = "|".join(sorted(a for a in areas if a))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


async def fetch_critical_life_alerts(
    client: HttpClient,
    *,
    enabled: bool = True,
    keywords: list[str] | None = None,
    page_size: int = 100,
    merge: bool = True,
    merge_window_minutes: int = 45,
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
                raw={
                    **e,
                    "merge": False,
                    "count": 1,
                    "areas": [e["area"]] if e.get("area") and e["area"] != "相关地区" else [],
                    "group_key": f"{e['province']}|{e['hazard']}",
                },
            )
            for e in raw_events
        ]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for e in raw_events:
        groups[(e["province"], e["hazard"])].append(e)

    window = max(5, int(merge_window_minutes or 45))
    events: list[DisasterEvent] = []
    for (prov, hazard), items in groups.items():
        enriched = []
        for it in items:
            dt = _parse_alert_time(it.get("time") or "")
            enriched.append((dt, it))
        timed = [x for x in enriched if x[0] is not None]
        if timed:
            latest_dt = max(x[0] for x in timed)
            cutoff = latest_dt - timedelta(minutes=window)
            window_items = [it for dt, it in enriched if dt is None or dt >= cutoff]
        else:
            window_items = items

        window_items = sorted(window_items, key=lambda x: x.get("time") or "", reverse=True)
        areas: list[str] = []
        for it in window_items:
            a = (it.get("area") or "").strip()
            if not a or a in areas:
                continue
            if a in {"相关地区", prov}:
                continue
            if any(bad in a for bad in ("水利", "气象", "应急", "自然", "厅", "局")):
                continue
            areas.append(a)
            if len(areas) >= 12:
                break

        latest_time = window_items[0].get("time") or ""
        alert_ids = [str(x.get("alert_id") or "") for x in window_items]
        # 仅按地区集合指纹：同区不重复，扩大后换指纹
        fp = _areas_fingerprint(areas) if areas else _areas_fingerprint(alert_ids)
        group_key = f"{prov}|{hazard}"

        if len(window_items) == 1 and len(areas) <= 1:
            it = window_items[0]
            title = it["title"]
            location = it["location"] or (areas[0] if areas else prov)
            summary = "已达红色预警，可能严重威胁当地人身与财产安全。"
            event_id = f"critical-{it['alert_id']}" if not areas else f"critical-merge-{prov}-{hazard}-{fp}"
            count = 1
        else:
            area_text = "、".join(areas[:8]) if areas else "多地"
            more = f"{len(window_items)}条"
            title = f"{prov}多地发布{hazard}红色预警（{more}）"
            location = f"{prov}：{area_text}" if areas else prov
            summary = (
                f"近{window}分钟内共 {len(window_items)} 条红色{hazard}预警，"
                "可能严重威胁当地人身与财产安全。"
            )
            event_id = f"critical-merge-{prov}-{hazard}-{fp}"
            count = len(window_items)

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
                url=window_items[0].get("url") or NMC_PAGE,
                advice=CRITICAL_ADVICE,
                raw={
                    "merge": count > 1,
                    "count": count,
                    "province": prov,
                    "hazard": hazard,
                    "areas": areas,
                    "alert_ids": alert_ids,
                    "window_minutes": window,
                    "group_key": group_key,
                    "areas_fp": fp,
                },
            )
        )
    events.sort(key=lambda e: e.occurred_at or "", reverse=True)
    return events
