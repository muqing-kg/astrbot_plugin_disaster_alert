"""AstrBot 灾害及时推送 + 地区天气卡片查询。

及时推送（主动）：
- 地震（CEIC）
- 海啸/海洋灾害
- 台风
- 极危天气（仅红色高危类型）

不做主动推送：
- 普通极端天气橙/黄/蓝预警

用户主动：
- /天气 北京 ：查询中央气象台实况与预报，直接返回卡片图
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools, register
import astrbot.api.message_components as Comp

from .card_renderer import render_weather_card
from .map_renderer import render_critical_weather_card
from .http_client import HttpClient
from .models import DisasterEvent
from .sources import (
    fetch_critical_life_alerts,
    fetch_earthquakes,
    fetch_tsunami_events,
    fetch_typhoons,
    query_region_weather,
)
from .state_store import StateStore


@register(
    "astrbot_plugin_disaster_alert",
    "云霄",
    "地震/海啸/台风/极危天气及时推送；地区天气按需查询并生成卡片图",
    "1.2.0",
)
class DisasterAlertPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self.http = HttpClient(timeout=25)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._push_lock = asyncio.Lock()
        self._data_dir = self._resolve_data_dir()
        self.state = StateStore(os.path.join(self._data_dir, "seen_events.json"))

    def _resolve_data_dir(self) -> str:
        try:
            data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_alert")
            path = str(data_dir)
        except Exception:
            path = os.path.join(os.path.dirname(__file__), "data")
        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "cards"), exist_ok=True)
        os.makedirs(os.path.join(path, "maps"), exist_ok=True)
        return path

    async def initialize(self) -> None:
        await self.http.start()
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="disaster-alert-loop")
        targets = self._target_sessions()
        logger.info(
            "disaster_alert v1.2.0 已加载，enabled=%s，目标群=%d，轮询=%ss",
            self.config.get("enabled", True),
            len(targets),
            self.config.get("poll_interval_seconds", 120),
        )

    async def terminate(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.http.close()
        logger.info("disaster_alert 已卸载")

    # ---------------- 命令 ----------------

    @filter.command("灾害状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看灾害监测运行状态。"""
        targets = self._target_sessions()
        eq = self.config.get("earthquake", {}) or {}
        ty = self.config.get("typhoon", {}) or {}
        ts = self.config.get("tsunami", {}) or {}
        cw = self.config.get("critical_weather", {}) or {}
        lines = [
            "灾害监测插件状态 v1.2.0",
            f"总开关：{'开' if self.config.get('enabled', True) else '关'}",
            f"轮询间隔：{self.config.get('poll_interval_seconds', 120)} 秒",
            f"目标群数量：{len(targets)}",
            f"地震推送：{'开' if eq.get('enabled', True) else '关'}（阈值 M{eq.get('min_magnitude', 4.5)}）",
            f"台风推送：{'开' if ty.get('enabled', True) else '关'}",
            f"海啸/海洋推送：{'开' if ts.get('enabled', True) else '关'}",
            f"极危红色天气：{'开' if cw.get('enabled', True) else '关'}（默认图片，失败降级文字）",
            "普通极端天气：不主动推送（请用 /天气 北京）",
            f"已记忆事件：{len(self.state._data.get('seen', {}))}",
            f"基线：{'已建立' if self.state.bootstrapped else '未建立'}",
        ]
        yield event.plain_result("\n".join(lines))


    @filter.command("灾害检测")
    async def cmd_check(self, event: AstrMessageEvent):
        """立即拉取及时通道数据并摘要（不强制推送）。"""
        yield event.plain_result("正在拉取及时通道数据，请稍候…")
        try:
            events = await self._collect_timely()
        except Exception as e:
            logger.exception("手动检测失败")
            yield event.plain_result(f"检测失败：{e}")
            return

        if not events:
            yield event.plain_result("当前没有符合及时推送条件的事件。")
            return

        buckets: dict[str, list[DisasterEvent]] = {}
        for ev in events:
            buckets.setdefault(ev.category, []).append(ev)

        lines = [f"检测完成，共 {len(events)} 条："]
        for cat, items in buckets.items():
            lines.append(f"- {cat}：{len(items)} 条")
            for item in items[:2]:
                lines.append(f"  · {item.title}")
            if len(items) > 2:
                lines.append(f"  · … 另有 {len(items) - 2} 条")
        yield event.plain_result("\n".join(lines))

    @filter.command("灾害推送")
    async def cmd_push_now(self, event: AstrMessageEvent):
        """立即检测并推送新事件到配置群。"""
        yield event.plain_result("开始立即检测并推送…")
        try:
            pushed = await self._poll_once(force_push=True)
        except Exception as e:
            logger.exception("立即推送失败")
            yield event.plain_result(f"推送失败：{e}")
            return
        yield event.plain_result(f"完成，本轮新推送 {pushed} 条。")

    @filter.command("灾害帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看插件用法。"""
        text = (
            "灾害气象插件 v1.2.0\n"
            "【主动及时推送】\n"
            "地震 / 台风 / 海啸海洋 / 极危红色天气（可在配置里分别开关）\n"
            "极危红色天气默认发图片，失败才发文字\n"
            "【不主动推送】普通橙黄蓝极端天气\n"
            "【天气查询】\n"
            "/天气 北京\n"
            "/天气 成都\n"
            "直接返回天气卡片，不发多余提示\n\n"
            "其他命令：\n"
            "灾害状态 / 灾害检测 / 灾害推送 / 灾害帮助"
        )
        yield event.plain_result(text)


    @filter.command("天气")
    async def cmd_weather(self, event: AstrMessageEvent):
        """查询指定地区天气并直接发送卡片图。用法：/天气 北京"""
        wq = self.config.get("weather_query", {}) or {}
        if not wq.get("enabled", True):
            yield event.plain_result("天气查询已关闭。")
            return

        place = self._extract_weather_place(event)
        if not place:
            yield event.plain_result("用法：/天气 北京\n例如：/天气 北京  /天气 成都")
            return

        try:
            result = await query_region_weather(self.http, place)
        except Exception as e:
            logger.exception("天气查询异常")
            yield event.plain_result(f"查询失败：{e}")
            return

        if not result.ok:
            msg = result.message or "查询失败"
            if result.candidates:
                msg += "\n可能是：" + "、".join(result.candidates[:5])
            yield event.plain_result(msg)
            return

        # 成功：只发卡片，不发“正在查询/简讯”
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            city = (result.city or place or "weather").strip()
            safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", city)[:40]
            path = os.path.join(self._data_dir, "cards", f"weather_{safe}_{ts}.png")
            render_weather_card(result, path)
            yield event.chain_result([Comp.Image.fromFileSystem(path)])
            return
        except Exception as e:
            logger.exception("天气卡片渲染失败，降级为文字")
            yield event.plain_result(self._format_weather_text(result))


    # ---------------- 定时循环 ----------------

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while not self._stop.is_set():
            try:
                if self.config.get("enabled", True):
                    await self._poll_once(force_push=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("disaster_alert 轮询异常")

            interval = int(self.config.get("poll_interval_seconds", 120) or 120)
            interval = max(30, min(interval, 3600))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def _poll_once(self, *, force_push: bool) -> int:
        async with self._push_lock:
            events = await self._collect_timely()
            if not events:
                return 0

            fps = [e.fingerprint() for e in events]
            if (
                not self.state.bootstrapped
                and self.config.get("startup_skip_history", True)
                and not force_push
            ):
                self.state.mark_many(fps)
                self.state.mark_bootstrapped()
                logger.info("disaster_alert 已建立历史基线，共 %d 条", len(fps))
                return 0

            if not self.state.bootstrapped:
                self.state.mark_bootstrapped()

            new_events = [e for e in events if not self.state.has(e.fingerprint())]
            if not new_events:
                return 0

            new_events.sort(key=lambda e: e.occurred_at or "", reverse=True)
            max_n = int(self.config.get("max_push_per_cycle", 8) or 8)
            max_n = max(1, min(max_n, 50))
            to_push = new_events[:max_n]

            targets = self._target_sessions()
            if not targets:
                self.state.mark_many([e.fingerprint() for e in to_push])
                logger.warning("有新事件但未配置 target_groups，已记录未推送")
                return 0

            pushed = 0
            for ev in to_push:
                ok = await self._broadcast(targets, ev)
                if ok:
                    self.state.mark(ev.fingerprint())
                    pushed += 1
                else:
                    logger.warning("推送失败，保留待重试: %s", ev.fingerprint())
                await asyncio.sleep(0.4)

            overflow = new_events[max_n:]
            if overflow:
                self.state.mark_many([e.fingerprint() for e in overflow])
                logger.info("本轮超额 %d 条已记入已读", len(overflow))

            if pushed:
                logger.info("本轮推送 %d 条到 %d 个群", pushed, len(targets))
            return pushed

    async def _collect_timely(self) -> list[DisasterEvent]:
        """只收集及时推送通道，不含普通极端天气。"""
        tasks = []
        names = []

        eq_cfg = self.config.get("earthquake", {}) or {}
        if eq_cfg.get("enabled", True):
            names.append("earthquake")
            tasks.append(
                fetch_earthquakes(
                    self.http,
                    min_magnitude=float(eq_cfg.get("min_magnitude", 4.5) or 4.5),
                )
            )

        typhoon_cfg = self.config.get("typhoon", {}) or {}
        if typhoon_cfg.get("enabled", True):
            names.append("typhoon")
            tasks.append(
                fetch_typhoons(
                    self.http,
                    only_active=bool(typhoon_cfg.get("only_active", True)),
                )
            )

        tsunami_cfg = self.config.get("tsunami", {}) or {}
        if tsunami_cfg.get("enabled", True):
            names.append("tsunami")
            tasks.append(
                fetch_tsunami_events(
                    self.http,
                    use_nmc_ocean=bool(tsunami_cfg.get("use_nmc_ocean", True)),
                    use_jma=bool(tsunami_cfg.get("use_jma", True)),
                    jma_max_age_hours=int(tsunami_cfg.get("jma_max_age_hours", 48) or 48),
                    nmc_min_level=str(tsunami_cfg.get("nmc_min_level", "橙色") or "橙色"),
                )
            )

        cw_cfg = self.config.get("critical_weather", {}) or {}
        if cw_cfg.get("enabled", True):
            names.append("critical_weather")
            tasks.append(
                fetch_critical_life_alerts(
                    self.http,
                    enabled=True,
                    keywords=self._split_keywords(cw_cfg.get("keywords", "")),
                )
            )

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        events: list[DisasterEvent] = []
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning("采集失败 [%s]: %s", name, result)
                continue
            if isinstance(result, list):
                events.extend(result)
        return events

    
    async def _broadcast(self, targets: list[str], event: DisasterEvent) -> bool:
        await self._ensure_event_image(event)
        text = event.format_message()
        has_img = bool(event.image_path and os.path.exists(event.image_path))

        comps = []
        if event.category == "极危天气预警":
            # 默认图片；失败降级文字
            if has_img:
                try:
                    comps.append(Comp.Image.fromFileSystem(event.image_path))
                except Exception as e:
                    logger.warning("极危天气配图发送失败，降级文字: %s", e)
                    comps.append(Comp.Plain(text))
            else:
                comps.append(Comp.Plain(text))
        else:
            # 地震 / 台风 / 海啸海洋：纯文字
            comps.append(Comp.Plain(text))

        chain = MessageChain(chain=comps)
        success_any = False
        for session in targets:
            try:
                ok = await self.context.send_message(session, chain)
                success_any = success_any or bool(ok)
                if not ok:
                    logger.warning("send_message 未匹配平台: %s", session)
            except Exception as e:
                logger.warning("发送到 %s 失败: %s", session, e)
        return success_any


    async def _ensure_event_image(self, event: DisasterEvent) -> None:
        """仅极危天气生成卡片图；地震/台风改为纯文字，避免刷图。"""
        if event.image_path and os.path.exists(event.image_path):
            return
        if event.category != "极危天气预警":
            return
        maps_dir = os.path.join(self._data_dir, "maps")
        os.makedirs(maps_dir, exist_ok=True)
        try:
            safe = re.sub(r"[^\\w\\-]+", "_", str(event.event_id))[:48]
            path = os.path.join(maps_dir, f"cw_{safe}.png")
            render_critical_weather_card(
                title=event.title,
                location=event.location,
                occurred_at=event.occurred_at,
                level=event.level or "红色",
                summary=event.summary,
                advice=event.advice,
                out_path=path,
            )
            event.image_path = path
        except Exception as e:
            logger.warning("生成极危天气配图失败: %s", e)


    def _target_sessions(self) -> list[str]:
        raw = self.config.get("target_groups", []) or []
        if isinstance(raw, str):
            raw_items = [x.strip() for x in raw.replace("，", ",").replace("\n", ",").split(",")]
        elif isinstance(raw, list):
            raw_items = []
            for item in raw:
                s = str(item).strip()
                if not s:
                    continue
                if "," in s or "，" in s:
                    raw_items.extend(
                        [x.strip() for x in s.replace("，", ",").split(",") if x.strip()]
                    )
                else:
                    raw_items.append(s)
        else:
            raw_items = [str(raw).strip()] if str(raw).strip() else []

        # 纯群号默认按 QQ 群会话拼接；完整会话 ID 原样使用
        prefix = "aiocqhttp:GroupMessage"

        sessions: list[str] = []
        for s in raw_items:
            if not s:
                continue
            if s.count(":") >= 2:
                sessions.append(s)
            else:
                sessions.append(f"{prefix}:{s}")

        seen = set()
        out: list[str] = []
        for s in sessions:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _extract_weather_place(self, event: AstrMessageEvent) -> str:
        text = ""
        try:
            text = (event.message_str or "").strip()
        except Exception:
            text = ""
        if not text:
            try:
                text = str(event.get_message_str() or "").strip()
            except Exception:
                text = ""
        # 去掉命令前缀
        text = re.sub(r"^[\/!！．.\s]*天气\s*", "", text).strip()
        # 兼容：天气：北京 / 天气-成都
        text = re.sub(r"^[:：\-\s]+", "", text).strip()
        return text

    @staticmethod
    def _format_weather_text(w) -> str:
        city = (w.city or w.query or w.province or "").strip()
        lines = [
            f"【{city} 天气】",
            f"实况：{w.weather} {w.temperature}（体感 {w.feels_like}）",
            f"湿度 {w.humidity}  风 {w.wind}  降水 {w.rain}",
            f"空气质量：{w.aqi_text}（AQI {w.aqi}）",
            f"预警：{w.warn_text}",
            f"更新：{w.publish_time}",
        ]
        if w.forecasts:
            lines.append("未来预报：")
            for d in w.forecasts[:5]:
                lines.append(
                    f"- {d.date}: 白天{d.day_weather}{d.day_temp}℃ / 夜间{d.night_weather}{d.night_temp}℃"
                )
        lines.append("数据来源：中央气象台 NMC")
        return "\n".join(lines)

    @staticmethod
    def _split_keywords(text: Any) -> list[str]:
        if isinstance(text, list):
            return [str(x).strip() for x in text if str(x).strip()]
        s = str(text or "")
        parts = []
        for chunk in s.replace("，", ",").replace("、", ",").split(","):
            c = chunk.strip()
            if c:
                parts.append(c)
        return parts