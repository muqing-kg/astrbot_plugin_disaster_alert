"""极危天气主动推送：仅推送最极端、可能危及人身财产安全的红色预警。

默认不推送普通极端天气（橙/黄/蓝），避免全国群刷屏。
仅保留高危类型的红色信号。
"""

from __future__ import annotations

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

# 仅这些类型的红色才主动推（人身/重大财产风险）
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


async def fetch_critical_life_alerts(
    client: HttpClient,
    *,
    enabled: bool = True,
    keywords: list[str] | None = None,
    page_size: int = 100,
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
    events: list[DisasterEvent] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        level = extract_level(title)
        # 只推红色
        if LEVEL_RANK.get(level, 0) < 4:
            continue
        if not any(k in title for k in kws):
            continue

        alert_id = str(item.get("alertid") or "").strip() or f"{item.get('issuetime')}|{title}"
        events.append(
            DisasterEvent(
                source="中央气象台 NMC",
                category="极危天气预警",
                event_id=f"critical-{alert_id}",
                title=title,
                summary="已达红色预警，可能严重威胁当地人身与财产安全。",
                occurred_at=str(item.get("issuetime") or "").replace("/", "-"),
                location=extract_location(title),
                level="红色",
                url=absolute_nmc_url(str(item.get("url") or "")),
                advice=CRITICAL_ADVICE,
                raw={
                    "alertid": item.get("alertid"),
                    "issuetime": item.get("issuetime"),
                    "title": title,
                },
            )
        )
    return events