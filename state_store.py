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
        except Exception:
            self._data = {"seen": {}, "bootstrapped": False, "updated_at": 0}

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
        self._trim()
        self.save()

    def mark(self, fingerprint: str) -> None:
        self.mark_many([fingerprint])

    def _trim(self) -> None:
        seen: dict[str, int] = self._data.get("seen", {})
        if len(seen) <= self.max_entries:
            return
        # 保留最近的条目
        ordered = sorted(seen.items(), key=lambda x: x[1], reverse=True)
        self._data["seen"] = dict(ordered[: self.max_entries])