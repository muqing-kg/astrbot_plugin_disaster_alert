# AGENTS.md — astrbot_plugin_disaster_alert

## 项目定位

AstrBot 插件 v1.1：
- 及时主动推送：地震 / 海啸海洋 / 台风 / 极危红色天气（附安全忠告）
- 普通极端天气不主动推送
- 用户主动：天气 <地区> 查询中央气象台并渲染卡片图

## 默认工作目录

D:\My_Project\astrbot_plugin_disaster_alert

## 关键入口

- main.py
- sources/region_weather.py
- card_renderer.py
- _conf_schema.json
- README.md

## 验证

- 语法：python -m py_compile / AST parse
- 实况：天气 北京 卡片生成
- 及时通道采集：_collect_timely 不应包含普通“极端天气预警”

## 禁止事项

- 不回显密钥
- 不把普通橙黄蓝预警重新做成默认主动推送
- 不删除数据缓存目录中的用户数据以外的核心源码
