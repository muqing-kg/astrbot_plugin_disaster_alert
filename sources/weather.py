"""中央气象台预警相关工具（供极危预警 / 地区查询复用）。"""

from __future__ import annotations

import re
from typing import Any

LEVEL_RANK = {
    "蓝色": 1,
    "黄色": 2,
    "橙色": 3,
    "红色": 4,
}

NMC_ALARM_URL = "https://www.nmc.cn/rest/findAlarm"
NMC_PAGE = "https://www.nmc.cn/"


def extract_alarm_list(data: Any) -> list[Any]:
    if not isinstance(data, dict):
        return []
    page = ((data.get("data") or {}) if isinstance(data.get("data"), dict) else {}).get("page")
    if isinstance(page, dict) and isinstance(page.get("list"), list):
        return page["list"]
    return []


def extract_level(title: str) -> str:
    for lv in ("红色", "橙色", "黄色", "蓝色"):
        if lv in title:
            return lv
    return ""


def extract_location(title: str) -> str:
    m = re.match(r"^(.+?)气象台", title)
    if m:
        return m.group(1)
    m2 = re.match(r"^(.+?)发布", title)
    if m2:
        return m2.group(1)
    return ""


def absolute_nmc_url(path: str) -> str:
    path = str(path or "").strip()
    if not path:
        return NMC_PAGE
    if path.startswith("http"):
        return path
    return NMC_PAGE.rstrip("/") + path