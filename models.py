"""灾害事件统一模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_ADVICE = (
    "【安全忠告】请以当地应急管理、气象/地震/海洋部门最新通告为准；"
    "远离危险区域，勿信谣言，必要时及时转移并拨打当地应急电话。"
)


@dataclass(slots=True)
class DisasterEvent:
    """统一的灾害/预警事件。"""

    source: str
    category: str
    event_id: str
    title: str
    summary: str
    occurred_at: str = ""
    level: str = ""
    location: str = ""
    url: str = ""
    magnitude: float | None = None
    advice: str = DEFAULT_ADVICE
    image_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return f"{self.source}:{self.event_id}"

    def format_message(self) -> str:
        """推送正文：不输出来源链接与数据源名称。"""
        lines = [f"【{self.category}】{self.title}"]
        if self.occurred_at:
            lines.append(f"时间：{self.occurred_at}")
        if self.location:
            lines.append(f"地点：{self.location}")
        if self.level:
            lines.append(f"等级：{self.level}")
        if self.magnitude is not None:
            lines.append(f"震级：M{self.magnitude}")
        if self.summary:
            lines.append(self.summary)
        if self.advice:
            lines.append("")
            lines.append(self.advice)
        return "\n".join(lines)