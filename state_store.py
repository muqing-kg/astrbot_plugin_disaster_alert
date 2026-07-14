"""已推送事件去重状态。"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class StateStore:
    """持久化已推送事件指纹，避免重复刷屏。"""

    def __init__(self, path: str, max_entries: int = 3000) -> None:
        self.path = path
        self.max_entries = max_entries
        self._data: dict[str, Any] = {
            "seen": {},
            "cooldown": {},
            "history": [],
            "critical_areas": {},
            "bootstrapped": False,
            "updated_at": 0,
        }
        self.load()

    def load(self) -> None:
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data.update(data)
            if not isinstance(self._data.get("seen"), dict):
                self._data["seen"] = {}
            if not isinstance(self._data.get("cooldown"), dict):
                self._data["cooldown"] = {}
            if not isinstance(self._data.get("history"), list):
                self._data["history"] = []
            if not isinstance(self._data.get("critical_areas"), dict):
                self._data["critical_areas"] = {}
        except Exception:
            self._data = {
                "seen": {},
                "cooldown": {},
                "history": [],
                "critical_areas": {},
                "bootstrapped": False,
                "updated_at": 0,
            }

    def save(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._data["updated_at"] = int(time.time())
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    @property
    def bootstrapped(self) -> bool:
        return bool(self._data.get("bootstrapped"))

    def mark_bootstrapped(self) -> None:
        self._data["bootstrapped"] = True
        self.save()

    def has(self, fingerprint: str) -> bool:
        return fingerprint in self._data.get("seen", {})

    def mark_many(self, fingerprints: list[str]) -> None:
        now = int(time.time())
        seen: dict[str, int] = self._data.setdefault("seen", {})
        for fp in fingerprints:
            seen[fp] = now
        cooldown: dict[str, int] = self._data.setdefault("cooldown", {})
        for fp in fingerprints:
            cooldown.pop(fp, None)
        self._trim()
        self.save()

    def mark(self, fingerprint: str) -> None:
        self.mark_many([fingerprint])

    def set_cooldown(self, fingerprint: str, seconds: int = 600) -> None:
        """发送失败时短暂冷却，避免每轮重推刷屏，到期可再试。"""
        sec = max(30, int(seconds or 600))
        cd: dict[str, int] = self._data.setdefault("cooldown", {})
        cd[fingerprint] = int(time.time()) + sec
        self._trim_cooldown()
        self.save()

    def in_cooldown(self, fingerprint: str) -> bool:
        cd: dict[str, int] = self._data.get("cooldown", {}) or {}
        exp = cd.get(fingerprint)
        if exp is None:
            return False
        now = int(time.time())
        if exp <= now:
            cd.pop(fingerprint, None)
            self.save()
            return False
        return True

    def add_history(self, item: dict[str, Any], *, max_items: int = 50) -> None:
        hist: list[Any] = self._data.setdefault("history", [])
        row = dict(item or {})
        row.setdefault("ts", int(time.time()))
        hist.insert(0, row)
        limit = max(10, int(max_items or 50))
        if len(hist) > limit:
            del hist[limit:]
        self.save()

    def get_history(self, limit: int = 10) -> list[dict[str, Any]]:
        hist = self._data.get("history") or []
        n = max(1, min(int(limit or 10), 50))
        out: list[dict[str, Any]] = []
        for row in hist[:n]:
            if isinstance(row, dict):
                out.append(row)
        return out

    def get_critical_areas(self, key: str) -> list[str]:
        store = self._data.get("critical_areas") or {}
        val = store.get(key)
        if isinstance(val, dict):
            areas = val.get("areas") or []
            return [str(a) for a in areas if str(a).strip()]
        if isinstance(val, list):
            return [str(a) for a in val if str(a).strip()]
        return []

    def set_critical_areas(self, key: str, areas: list[str]) -> None:
        store: dict[str, Any] = self._data.setdefault("critical_areas", {})
        store[key] = {
            "areas": [str(a) for a in areas if str(a).strip()],
            "updated_at": int(time.time()),
        }
        if len(store) > 200:
            ordered = sorted(
                store.items(),
                key=lambda x: int((x[1] or {}).get("updated_at", 0) if isinstance(x[1], dict) else 0),
                reverse=True,
            )
            self._data["critical_areas"] = dict(ordered[:200])
        self.save()

    def _trim(self) -> None:
        seen: dict[str, int] = self._data.get("seen", {})
        if len(seen) > self.max_entries:
            ordered = sorted(seen.items(), key=lambda x: x[1], reverse=True)
            self._data["seen"] = dict(ordered[: self.max_entries])
        self._trim_cooldown()

    def _trim_cooldown(self) -> None:
        cd: dict[str, int] = self._data.get("cooldown", {}) or {}
        now = int(time.time())
        cd = {k: v for k, v in cd.items() if int(v) > now}
        if len(cd) > 500:
            ordered = sorted(cd.items(), key=lambda x: x[1], reverse=True)
            cd = dict(ordered[:500])
        self._data["cooldown"] = cd
