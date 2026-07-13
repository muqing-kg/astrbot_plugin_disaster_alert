"""HTTP 请求工具。"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp

from astrbot.api import logger

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, text/plain, */*;q=0.9",
}


class HttpClient:
    def __init__(self, timeout: int = 20) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=self.timeout,
                    headers=DEFAULT_HEADERS,
                    trust_env=True,
                )

    async def close(self) -> None:
        async with self._lock:
            if self._session and not self._session.closed:
                await self._session.close()
            self._session = None

    async def get_text(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        await self.start()
        assert self._session is not None
        async with self._session.get(url, headers=headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.text(encoding="utf-8", errors="ignore")

    async def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        text = await self.get_text(url, headers=headers, params=params)
        text = text.strip()
        if not text:
            return None
        # 兼容 JSONP：callback({...}) 或 callback(({...}))
        if text[0] not in "{[":
            m = re.search(r"\((\{.*\}|\[.*\])\)\s*;?\s*$", text, re.S)
            if m:
                text = m.group(1)
            else:
                # typhoon_jsons_list_default(({"typhoonList":...}))
                m2 = re.search(r"\(\((\{.*\}|\[.*\])\)\)\s*;?\s*$", text, re.S)
                if m2:
                    text = m2.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("JSON 解析失败: %s, 片段: %s", e, text[:200])
            raise