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
- 最近预警 ：查看近期已成功推送的记录
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
    "1.3.0",
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
        self._source_health: dict[str, dict[str, Any]] = {}

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
            "disaster_alert v1.3.0 已加载，enabled=%s，目标群=%d，轮询=%ss",
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
        qh = self.config.get("quiet_hours", {}) or {}
        lines = [
            "灾害监测插件状态 v1.3.0",
            f"总开关：{'开' if self.config.get('enabled', True) else '关'}",
            f"轮询间隔：{self.config.get('poll_interval_seconds', 120)} 秒",
            f"目标群数量：{len(targets)}",
            f"地震推送：{'开' if eq.get('enabled', True) else '关'}（阈值 M{eq.get('min_magnitude', 4.5)}）",
            f"台风推送：{'开' if ty.get('enabled', True) else '关'}（近岸+风力≥{ty.get('min_wind_level', 8)}级）",
            f"海啸/海洋推送：{'开' if ts.get('enabled', True) else '关'}",
            f"极危红色天气：{'开' if cw.get('enabled', True) else '关'}（图片优先；合并窗 {cw.get('merge_window_minutes', 45)} 分钟）",
            f"夜间静默：{'开' if qh.get('enabled', False) else '关'}"
            + (
                f"（{qh.get('start_hour', 23)}:00-{qh.get('end_hour', 7)}:00，仅放行地震）"
                if qh.get("enabled", False)
                else ""
            ),
            "普通极端天气：不主动推送（请用 /天气 北京）",
            f"已记忆事件：{len(self.state._data.get('seen', {}))}",
            f"基线：{'已建立' if self.state.bootstrapped else '未建立'}",
        ]
        if self._source_health:
            lines.append("数据源：")
            for name, info in self._source_health.items():
                ok = info.get("ok")
                ts_txt = info.get("at") or "-"
                detail = info.get("detail") or ""
                status = "正常" if ok else "异常"
                lines.append(f"- {name}：{status} @ {ts_txt}" + (f"（{detail}）" if detail else ""))
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

    @filter.command("最近预警")
    async def cmd_recent(self, event: AstrMessageEvent):
        """查看近期已成功推送的灾害记录。"""
        rows = self.state.get_history(12)
        if not rows:
            yield event.plain_result("暂无已推送记录。")
            return
        lines = [f"最近推送 {len(rows)} 条："]
        for i, row in enumerate(rows, 1):
            cat = row.get("category") or "灾害"
            title = row.get("title") or "-"
            when = row.get("occurred_at") or row.get("pushed_at") or ""
            loc = row.get("location") or ""
            piece = f"{i}. 【{cat}】{title}"
            if when:
                piece += f"\n   时间：{when}"
            if loc:
                piece += f"\n   地区：{loc}"
            lines.append(piece)
        yield event.plain_result("\n".join(lines))

    @filter.command("灾害记录")
    async def cmd_history_alias(self, event: AstrMessageEvent):
        """最近预警别名。"""
        async for item in self.cmd_recent(event):
            yield item

    @filter.command("灾害帮助")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看插件用法。"""
        text = (
            "灾害气象插件 v1.3.0\n"
            "【主动及时推送】\n"
            "地震 / 台风 / 海啸海洋 / 极危红色天气（可在配置里分别开关）\n"
            "极危红色天气默认发图片，失败才发文字；地区扩大时用「新增地区」文案\n"
            "夜间静默可开：仅放行地震\n"
            "【不主动推送】普通橙黄蓝极端天气\n"
            "【天气查询】\n"
            "/天气 北京\n"
            "/天气 成都\n"
            "直接返回天气卡片，不发多余提示\n\n"
            "其他命令：\n"
            "灾害状态 / 灾害检测 / 灾害推送 / 最近预警 / 灾害帮助"
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

            # 极危红：首次全量 / 新增地区 专用文案
            events = self._apply_critical_incremental(events)

            fps = []
            for e in events:
                fps.append(e.fingerprint())
                fps.append(e.content_fingerprint())
            if (
                not self.state.bootstrapped
                and self.config.get("startup_skip_history", True)
                and not force_push
            ):
                self.state.mark_many(fps)
                # 建立基线时同步记录极危地区集合，避免启动后把旧区当「新增」
                for e in events:
                    if e.category == "极危天气预警":
                        key = str((e.raw or {}).get("group_key") or "")
                        areas = list((e.raw or {}).get("areas") or [])
                        if key:
                            self.state.set_critical_areas(key, areas)
                self.state.mark_bootstrapped()
                logger.info("disaster_alert 已建立历史基线，共 %d 条", len(fps))
                return 0

            if not self.state.bootstrapped:
                self.state.mark_bootstrapped()

            new_events = []
            for e in events:
                fp = e.fingerprint()
                cfp = e.content_fingerprint()
                if self.state.has(fp) or self.state.has(cfp):
                    continue
                if self.state.in_cooldown(fp) or self.state.in_cooldown(cfp):
                    continue
                # 夜间静默：非强制推送时只放行地震
                if not force_push and self._in_quiet_hours() and e.category != "地震速报":
                    continue
                new_events.append(e)
            if not new_events:
                return 0

            new_events.sort(key=lambda e: e.occurred_at or "", reverse=True)
            max_n = int(self.config.get("max_push_per_cycle", 8) or 8)
            max_n = max(1, min(max_n, 50))
            to_push = new_events[:max_n]

            targets = self._target_sessions()
            if not targets:
                self.state.mark_many([x for e in to_push for x in (e.fingerprint(), e.content_fingerprint())])
                for e in to_push:
                    if e.category == "极危天气预警":
                        key = str((e.raw or {}).get("group_key") or "")
                        areas = list((e.raw or {}).get("areas") or [])
                        if key:
                            self.state.set_critical_areas(key, areas)
                logger.warning("有新事件但未配置 target_groups，已记录未推送")
                return 0

            pushed = 0
            for ev in to_push:
                ok = await self._broadcast(targets, ev)
                fp = ev.fingerprint()
                cfp = ev.content_fingerprint()
                if ok:
                    self.state.mark_many([fp, cfp])
                    if ev.category == "极危天气预警":
                        key = str((ev.raw or {}).get("group_key") or "")
                        areas = list((ev.raw or {}).get("areas") or [])
                        if key:
                            self.state.set_critical_areas(key, areas)
                    self.state.add_history(
                        {
                            "category": ev.category,
                            "title": ev.title,
                            "location": ev.location,
                            "occurred_at": ev.occurred_at,
                            "level": ev.level,
                            "pushed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "event_id": ev.event_id,
                        }
                    )
                    pushed += 1
                else:
                    # 失败不永久去重，短冷却后可重试
                    self.state.set_cooldown(fp, 600)
                    self.state.set_cooldown(cfp, 600)
                    logger.warning("推送失败，已设 10 分钟冷却后重试: %s", fp)
                await asyncio.sleep(0.4)

            overflow = new_events[max_n:]
            if overflow:
                self.state.mark_many([x for e in overflow for x in (e.fingerprint(), e.content_fingerprint())])
                logger.info("本轮超额 %d 条已记入已读", len(overflow))

            if pushed:
                logger.info("本轮推送 %d 条到 %d 个群", pushed, len(targets))
            return pushed

    def _apply_critical_incremental(self, events: list[DisasterEvent]) -> list[DisasterEvent]:
        """极危红：地区扩大时改写为「新增地区」专用文案；无新增则跳过。"""
        out: list[DisasterEvent] = []
        for ev in events:
            if ev.category != "极危天气预警":
                out.append(ev)
                continue
            raw = dict(ev.raw or {})
            group_key = str(raw.get("group_key") or "")
            areas = [str(a).strip() for a in (raw.get("areas") or []) if str(a).strip()]
            if not group_key:
                out.append(ev)
                continue

            prev = self.state.get_critical_areas(group_key)
            if not prev:
                # 首次：全量文案
                raw["incremental"] = False
                raw["new_areas"] = areas
                ev.raw = raw
                out.append(ev)
                continue

            prev_set = set(prev)
            new_areas = [a for a in areas if a not in prev_set]
            if not new_areas:
                # 地区集合未扩大：不推
                continue

            # 有新增地区：专用文案
            prov = str(raw.get("province") or "")
            hazard = str(raw.get("hazard") or "高危天气")
            new_text = "、".join(new_areas[:8])
            all_text = "、".join(areas[:8]) if areas else new_text
            more = f"等{len(new_areas)}地" if len(new_areas) > 8 else f"{len(new_areas)}地"
            ev.title = f"{prov}{hazard}红色预警·新增地区（{more}）"
            ev.location = f"新增：{new_text}"
            ev.summary = (
                f"在既有红色{hazard}预警基础上，新增 {len(new_areas)} 个地区："
                f"{new_text}。当前覆盖：{all_text}。"
            )
            # 换 event_id，确保能作为新事件推送
            import hashlib

            na_fp = hashlib.sha1("|".join(sorted(new_areas)).encode("utf-8")).hexdigest()[:10]
            ev.event_id = f"critical-new-{group_key.replace('|', '-')}-{na_fp}"
            raw["incremental"] = True
            raw["new_areas"] = new_areas
            raw["prev_areas"] = prev
            ev.raw = raw
            out.append(ev)
        return out

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
                    min_wind_level=int(typhoon_cfg.get("min_wind_level", 8) or 8),
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
                    merge=bool(cw_cfg.get("merge_by_province", True)),
                    merge_window_minutes=int(cw_cfg.get("merge_window_minutes", 45) or 45),
                )
            )

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        events: list[DisasterEvent] = []
        now = datetime.now().strftime("%H:%M:%S")
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning("采集失败 [%s]: %s", name, result)
                self._source_health[name] = {"ok": False, "at": now, "detail": str(result)[:80]}
                continue
            if isinstance(result, list):
                self._source_health[name] = {"ok": True, "at": now, "detail": f"{len(result)}条"}
                events.extend(result)
        return events

    async def _broadcast(self, targets: list[str], event: DisasterEvent) -> bool:
        await self._ensure_event_image(event)
        text = event.format_message()
        has_img = bool(event.image_path and os.path.exists(event.image_path))

        comps = []
        if event.category == "极危天气预警":
            if has_img:
                try:
                    comps.append(Comp.Image.fromFileSystem(event.image_path))
                except Exception as e:
                    logger.warning("极危天气配图发送失败，降级文字: %s", e)
                    comps.append(Comp.Plain(text))
            else:
                comps.append(Comp.Plain(text))
        else:
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
            safe = re.sub(r"[^\w\-]+", "_", str(event.event_id))[:48]
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

    def _in_quiet_hours(self) -> bool:
        qh = self.config.get("quiet_hours", {}) or {}
        if not qh.get("enabled", False):
            return False
        try:
            start = int(qh.get("start_hour", 23) or 23)
            end = int(qh.get("end_hour", 7) or 7)
        except Exception:
            start, end = 23, 7
        start = max(0, min(23, start))
        end = max(0, min(23, end))
        hour = datetime.now().hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # 跨午夜，如 23-7
        return hour >= start or hour < end

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
        text = re.sub(r"^[\/!！．.\s]*天气\s*", "", text).strip()
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
