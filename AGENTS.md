# AGENTS.md — astrbot_plugin_disaster_alert

## 项目定位

AstrBot 插件 v1.3.0：
- 及时主动推送：地震 / 海啸海洋 / 台风 / 极危红色天气（附安全忠告）
- 普通极端天气不主动推送
- 用户主动：/天气 <地区> 查询中央气象台并渲染卡片图

## 默认工作目录

D:\My_Project\astrbot_plugin_disaster_alert

## 关键入口

- main.py
- sources/typhoon.py
- sources/critical_weather.py
- sources/region_weather.py
- card_renderer.py
- geo_utils.py
- state_store.py
- _conf_schema.json
- README.md
- metadata.yaml

## Continuity Snapshot (2026-07-14)

- Version: v1.3.0, commit 8bfb9df on main
- Repo: https://github.com/muqing-kg/astrbot_plugin_disaster_alert
- Shipped: typhoon min_wind_level (default 8), quiet hours (EQ only), 最近预警/灾害记录, critical 新增地区 copy, success-only permanent dedupe + 10m fail cooldown, distance 约/估算
- Next: AstrBot host reload + smoke (灾害状态 / 最近预警 / quiet_hours / min_wind_level)
- Locked: far sea no report; no forecast; weather image-only; no EQ/typhoon maps; China-only; no source URLs in push

## 验证

- 语法：python -m py_compile / AST parse
- 实况：/天气 北京 卡片生成
- 及时通道：_collect_timely 不含普通“极端天气预警”
- 台风闸门：远海强不报、近岸弱不报、近岸够强才报

## 禁止事项

- 不回显密钥
- 不把普通橙黄蓝预警重新做成默认主动推送
- 不恢复地震/台风地图渲染（主人已否决）
- 不删除数据缓存目录中的用户数据以外的核心源码