"""海啸 / 海洋灾害相关监测。

1. 中央气象台海浪、风暴潮、海上大风、海啸相关预警（国内官方）
2. 日本气象厅（JMA）海啸预报（西北太平洋官方源）
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ..http_client import HttpClient
from ..models import DisasterEvent
from ..geo_utils import is_china_related_tsunami_or_ocean
from .weather import (
    LEVEL_RANK,
    NMC_ALARM_URL,
    NMC_PAGE,
    absolute_nmc_url,
    extract_alarm_list,
    extract_level,
    extract_location,
)

OCEAN_KEYWORDS = ["海浪", "风暴潮", "海上大风", "海啸"]
JMA_LIST_URL = "https://www.jma.go.jp/bosai/tsunami/data/list.json"
JMA_PAGE = "https://www.jma.go.jp/bosai/map.html#5/28.107/132.583/&elem=warn&contents=tsunami"

OCEAN_ADVICE = (
    "【安全忠告】请远离海边、码头、渔船与低洼岸段，勿观潮拍照；"
    "听从海事/海洋预报与当地应急部门指令，船只及时回港避风。"
)
TSUNAMI_ADVICE = (
    "【安全忠告】海啸信息发布后请立即远离海岸与河口低地，"
    "向高处或内陆转移；勿返回看海，以当地防灾部门指令为准。"
)


async def fetch_tsunami_events(
    client: HttpClient,
    *,
    use_nmc_ocean: bool = True,
    use_jma: bool = True,
    jma_max_age_hours: int = 48,
    nmc_min_level: str = "橙色",
) -> list[DisasterEvent]:
    events: list[DisasterEvent] = []
    if use_nmc_ocean:
        events.extend(await _fetch_nmc_ocean(client, min_level=nmc_min_level))
    if use_jma:
        events.extend(await _fetch_jma(client, max_age_hours=jma_max_age_hours))
    return events


async def _fetch_nmc_ocean(client: HttpClient, *, min_level: str) -> list[DisasterEvent]:
    min_rank = LEVEL_RANK.get(min_level.strip(), 3)
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
        logger.warning("NMC 海洋相关预警获取失败: %s", e)
        return []

    rows = extract_alarm_list(data)
    events: list[DisasterEvent] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if not any(k in title for k in OCEAN_KEYWORDS):
            continue
        level = extract_level(title)
        if LEVEL_RANK.get(level, 0) < min_rank:
            continue
        alert_id = str(item.get("alertid") or f"{item.get('issuetime')}|{title}")
        events.append(
            DisasterEvent(
                source="中央气象台 NMC",
                category="海啸/海洋灾害",
                event_id=f"ocean-{alert_id}",
                title=title,
                summary="",
                occurred_at=str(item.get("issuetime") or "").replace("/", "-"),
                location=extract_location(title),
                level=level or "未知",
                url=absolute_nmc_url(str(item.get("url") or "")),
                advice=OCEAN_ADVICE,
                raw={
                    "alertid": item.get("alertid"),
                    "title": title,
                    "issuetime": item.get("issuetime"),
                },
            )
        )
    return events


async def _fetch_jma(client: HttpClient, *, max_age_hours: int) -> list[DisasterEvent]:
    try:
        data = await client.get_json(JMA_LIST_URL)
    except Exception as e:
        logger.warning("JMA 海啸列表获取失败: %s", e)
        return []
    if not isinstance(data, list):
        return []

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(1, int(max_age_hours)))
    events: list[DisasterEvent] = []

    for item in data:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("eid") or item.get("ctt") or "").strip()
        if not eid:
            continue
        rdt = str(item.get("rdt") or "")
        at = str(item.get("at") or "")
        occurred = rdt or at
        dt = _parse_iso(occurred)
        if dt and dt < cutoff:
            continue

        kinds = item.get("kind") or []
        kind_text = ""
        if isinstance(kinds, list) and kinds:
            names = []
            for k in kinds:
                if isinstance(k, dict) and k.get("kind"):
                    names.append(str(k.get("kind")))
            kind_text = " / ".join(names)

        en_title = str(item.get("en_ttl") or item.get("ttl") or "Tsunami Info")
        en_area = str(item.get("en_anm") or item.get("anm") or "")
        mag = item.get("mag")
        title = f"JMA 海啸信息：{en_title}"
        if en_area:
            title += f" - {en_area}"

        summary_parts = []
        if kind_text:
            summary_parts.append(kind_text)
        if mag is not None:
            summary_parts.append(f"相关震级 M{mag}")
        if item.get("cod"):
            summary_parts.append(f"坐标码 {item.get('cod')}")
        summary_parts.append("数据来源：日本气象厅（可能影响西北太平洋/中国沿海）")

        if not is_china_related_tsunami_or_ocean(title, en_area, "日本气象厅 JMA"):
            continue
        events.append(
            DisasterEvent(
                source="日本气象厅 JMA",
                category="海啸预警",
                event_id=f"jma-tsunami-{eid}-{item.get('ctt') or item.get('ser') or 0}",
                title=title,
                summary="；".join(summary_parts),
                occurred_at=occurred,
                location=en_area,
                level=en_title,
                magnitude=_to_float(mag),
                url=JMA_PAGE,
                advice=TSUNAMI_ADVICE,
                raw=_safe_jma(item),
            )
        )
    return events


def _parse_iso(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _to_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_jma(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("ctt", "eid", "rdt", "ttl", "at", "anm", "mag", "en_ttl", "en_anm")
    return {k: item.get(k) for k in keys}