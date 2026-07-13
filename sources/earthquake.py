"""中国地震台网（CEIC）正式目录。"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..http_client import HttpClient
from ..models import DisasterEvent
from ..geo_utils import is_china_earthquake

CEIC_URL = "https://www.ceic.ac.cn/data/data.json"
CEIC_PAGE = "https://www.ceic.ac.cn/"


async def fetch_earthquakes(
    client: HttpClient,
    *,
    min_magnitude: float = 4.0,
) -> list[DisasterEvent]:
    try:
        data = await client.get_json(
            CEIC_URL,
            headers={"Referer": CEIC_PAGE},
        )
    except Exception as e:
        logger.warning("CEIC 地震数据获取失败: %s", e)
        return []

    if not isinstance(data, list):
        logger.warning("CEIC 返回格式异常: %s", type(data))
        return []

    events: list[DisasterEvent] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            mag = float(item.get("magnitude", 0) or 0)
        except (TypeError, ValueError):
            continue
        if mag < float(min_magnitude):
            continue

        eid = str(item.get("id") or "").strip()
        if not eid:
            # 兜底：时间+位置+震级
            eid = f"{item.get('time')}|{item.get('location')}|{mag}"

        location = str(item.get("location") or "").strip()
        depth = item.get("depth")
        lat = item.get("latitude")
        lon = item.get("longitude")
        # 仅国内（含近海）
        if not is_china_earthquake(location, lat, lon):
            continue
        summary_parts = []
        if depth is not None:
            summary_parts.append(f"深度 {depth} km")
        if lat is not None and lon is not None:
            summary_parts.append(f"坐标 {lat}, {lon}")

        events.append(
            DisasterEvent(
                source="中国地震台网 CEIC",
                category="地震速报",
                event_id=eid,
                title=f"{location or '未知地点'} 发生 M{mag} 地震",
                summary="；".join(summary_parts),
                occurred_at=str(item.get("time") or ""),
                location=location,
                magnitude=mag,
                level=_mag_level(mag),
                url=CEIC_PAGE,
                advice=(
                    "【安全忠告】如感明显摇晃：就近避险，远离悬挂物与玻璃；"
                    "勿乘电梯，余震期间勿进入受损建筑；关注当地应急部门通知。"
                ),
                raw=_safe_raw(item),
            )
        )
    return events


def _mag_level(mag: float) -> str:
    if mag >= 7.0:
        return "特大地震"
    if mag >= 6.0:
        return "强震"
    if mag >= 5.0:
        return "中强震"
    if mag >= 4.0:
        return "有感/中等"
    return "弱震"


def _safe_raw(item: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "time", "latitude", "longitude", "depth", "magnitude", "location")
    return {k: item.get(k) for k in keys}