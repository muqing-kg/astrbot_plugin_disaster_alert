"""中央气象台台风网（typhoon.nmc.cn）。"""

from __future__ import annotations

import re
from typing import Any

from astrbot.api import logger

from ..http_client import HttpClient
from ..models import DisasterEvent
from ..geo_utils import is_china_related_typhoon, summarize_typhoon_impact, intensity_cn_with_wind

TYPHOON_LIST_URL = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
TYPHOON_VIEW_URL = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{tid}"
TYPHOON_PAGE = "https://typhoon.nmc.cn/"

# 强度缩写大致对照
INTENSITY_MAP = {
    "TD": "热带低压",
    "TS": "热带风暴",
    "STS": "强热带风暴",
    "TY": "台风",
    "STY": "强台风",
    "SuperTY": "超强台风",
}


async def fetch_typhoons(
    client: HttpClient,
    *,
    only_active: bool = True,
    min_wind_level: int = 8,
) -> list[DisasterEvent]:
    try:
        data = await client.get_json(
            TYPHOON_LIST_URL,
            headers={"Referer": TYPHOON_PAGE},
        )
    except Exception as e:
        logger.warning("台风列表获取失败: %s", e)
        return []

    rows = _extract_list(data)
    events: list[DisasterEvent] = []

    for row in rows:
        info = _parse_list_row(row)
        if not info:
            continue
        if only_active and info["status"] != "start":
            continue

        detail = await _fetch_detail(client, info["tid"])
        latest = detail.get("latest") if detail else None
        points = detail.get("points") if detail else []
        # 仅保留路径影响中国近海/陆地的台风
        if points and not is_china_related_typhoon(points):
            continue
        if not latest:
            # 无详情/路径点时无法判断是否影响国内，跳过
            continue

        # 只报：近岸 + 风力>=8 + 有明确影响区；同影响范围不重复；远海不报（即使很强）
        lat = latest.get("lat")
        lon = latest.get("lon")
        wind = latest.get("wind")
        pressure = latest.get("pressure")
        intensity = latest.get("intensity") or ""
        move = latest.get("move")
        speed = latest.get("speed")
        intensity_cn = intensity_cn_with_wind(str(intensity), wind)

        impact = summarize_typhoon_impact(
            points if isinstance(points, list) else [],
            latest,
            None,
            min_wind_level=int(min_wind_level or 8),
        )
        if not impact.get("should_report"):
            continue

        impact_text = str(impact.get("impact_text") or "")
        region_key = str(impact.get("region_key") or "impact")
        lv = impact.get("wind_level")
        event_id = f"typhoon-{info['tid']}-{region_key}-L{lv}"

        # 分行文案：实况 -> 中心位置 -> 影响范围
        summary_lines = []
        status_bits = []
        if pressure is not None:
            status_bits.append(f"中心气压 {pressure} hPa")
        if move or speed is not None:
            status_bits.append(f"移向移速 {move or '-'} {speed if speed is not None else '-'} km/h")
        if status_bits:
            summary_lines.append("实况：" + "；".join(status_bits))
        center_pos = str(impact.get("center_position") or "").strip()
        if center_pos:
            summary_lines.append(center_pos)
        summary_lines.append(f"影响范围：{impact_text}")

        cname = info.get("cname") or info.get("ename") or "台风"
        events.append(
            DisasterEvent(
                source="中央气象台台风网",
                category="台风动态",
                event_id=event_id,
                title=f"台风 {cname} 最新动向",
                summary="\n".join(summary_lines),
                occurred_at=str(latest.get("time_text") or latest.get("time_code") or ""),
                location="",  # 不单独输出地点，只在 summary 写影响范围
                level=intensity_cn,
                url=TYPHOON_PAGE,
                advice=(
                    "【安全忠告】关注台风路径与登陆影响，加固门窗、远离广告牌与临时搭建物；"
                    "沿海与低洼地区提前避险，勿在风浪中观潮。"
                ),
                raw={
                    "list": info,
                    "latest": latest,
                    "points": points if isinstance(points, list) else [],
                    "impact": impact,
                },
            )
        )


    return events


async def _fetch_detail(client: HttpClient, tid: str) -> dict[str, Any]:
    url = TYPHOON_VIEW_URL.format(tid=tid)
    try:
        data = await client.get_json(url, headers={"Referer": TYPHOON_PAGE})
    except Exception as e:
        logger.warning("台风详情获取失败 tid=%s: %s", tid, e)
        return {}
    return _parse_detail(data)


def _extract_list(data: Any) -> list[Any]:
    if isinstance(data, dict):
        lst = data.get("typhoonList")
        if isinstance(lst, list):
            return lst
    return []


def _parse_list_row(row: Any) -> dict[str, Any] | None:
    # [id, ename, cname, tfid, tfbh, land, meaning, status]
    if not isinstance(row, list) or len(row) < 8:
        return None
    return {
        "tid": str(row[0]),
        "ename": str(row[1] or ""),
        "cname": str(row[2] or ""),
        "tfid": str(row[3] or ""),
        "status": str(row[7] or ""),
    }



def _parse_detail(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    typhoon = data.get("typhoon")
    if not isinstance(typhoon, list) or len(typhoon) < 9:
        return {}
    points = typhoon[8]
    if not isinstance(points, list) or not points:
        return {}

    parsed_points: list[dict[str, Any]] = []
    forecast_points: list[dict[str, Any]] = []
    for pt in points:
        if not isinstance(pt, list) or len(pt) < 10:
            continue
        time_text = ""
        if len(pt) > 12 and isinstance(pt[12], list) and pt[12]:
            time_text = str(pt[12][1] if len(pt[12]) > 1 else pt[12][0])
        item = {
            "point_id": pt[0],
            "time_code": pt[1],
            "time_text": time_text or _pretty_time(str(pt[1])),
            "intensity": pt[3],
            "lon": pt[4],
            "lat": pt[5],
            "pressure": pt[6],
            "wind": pt[7],
            "move": pt[8],
            "speed": pt[9],
        }
        parsed_points.append(item)

        # 预报点通常在 pt[11] 的 dict 中，如 {"BABJ":[[hour, time, lon, lat, pressure, wind, src, intensity], ...]}
        if len(pt) > 11 and isinstance(pt[11], dict):
            # 优先中央台 BABJ
            seq = pt[11].get("BABJ") or next(iter(pt[11].values()), None)
            if isinstance(seq, list):
                for fp in seq:
                    if not isinstance(fp, list) or len(fp) < 6:
                        continue
                    # [hour, yyyymmddHHMM, lon, lat, pressure, wind, agency, intensity]
                    try:
                        forecast_points.append(
                            {
                                "hour": fp[0],
                                "time_code": fp[1],
                                "lon": float(fp[2]),
                                "lat": float(fp[3]),
                                "pressure": fp[4],
                                "wind": fp[5],
                                "intensity": fp[7] if len(fp) > 7 else "",
                            }
                        )
                    except Exception:
                        continue

    if not parsed_points:
        return {}
    latest = parsed_points[-1]
    # 仅保留“最新实况点”对应的预报（取最后一次解析到的预报序列）
    # 上面循环会累积多次，截成最后一段更合理：按 hour 排序去重
    if forecast_points:
        # 只取最后一次出现的连续预报：按添加顺序尾部 12 个左右
        forecast_points = forecast_points[-12:]
    return {"latest": latest, "points": parsed_points, "forecast_points": forecast_points}


def _pretty_time(code: str) -> str:
    # 202607130600 -> 2026-07-13 06:00
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})", code or "")
    if not m:
        return code
    y, mo, d, h, mi = m.groups()
    return f"{y}-{mo}-{d} {h}:{mi}"