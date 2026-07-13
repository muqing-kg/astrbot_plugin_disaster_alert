# 灾害及时推送与天气卡片 - AstrBot 插件

基于官方数据，主动推送国内地震、台风、海啸/海洋、极危红色天气；并支持 `/天气 北京` 按需查询天气卡片。

## 功能

### 主动及时推送
- 地震（中国地震台网 CEIC，仅国内）
- 台风（中央气象台台风网，仅国内相关）
- 海啸/海洋（中央气象台相关预警）
- 极危红色天气（暴雨/台风/山洪等红色高危）
- 四类推送可在 WebUI **分别开关**
- 推送文案附带安全忠告，不输出来源链接/数据源名

### 不主动推送
- 普通橙/黄/蓝极端天气

### 天气查询
- 命令：`/天气 北京`、`/天气 成都`
- 支持市、以及中央气象台有站点的区/县
- 成功时直接返回卡片图，不发“正在查询”和文字简讯

## 安装

1. 将本目录放到 AstrBot：`data/plugins/astrbot_plugin_disaster_alert`
2. WebUI 启用插件
3. 配置目标群 `target_groups`
4. 如缺 Pillow：在 AstrBot 使用的 Python 环境执行  
   `pip install -r requirements.txt`

## 配置建议

| 项 | 建议 |
|---|---|
| 地震推送 | 开，最小震级 4.5 或 5.0 |
| 台风推送 | 开 |
| 海啸/海洋推送 | 开 |
| 极危红色天气 | 开（默认发图片，失败降级文字） |
| 轮询间隔 | 120 秒 |
| 启动跳过历史 | 开 |
| 每轮最多推送 | 5~8 |

目标群示例：
- `123456789`
- `aiocqhttp:GroupMessage:123456789`

## 命令

| 命令 | 说明 |
|---|---|
| `/天气 北京` | 查询天气卡片 |
| `灾害状态` | 查看开关与运行状态 |
| `灾害检测` | 立即拉取摘要（不推送） |
| `灾害推送` | 立即检测并推送到配置群 |
| `灾害帮助` | 帮助 |

## 推送形态

- 地震 / 台风 / 海啸海洋：纯文字
- 极危红色天气：默认图片，失败才文字

## 验证

1. 配置目标群并启用插件
2. 群内发送：`灾害状态`
3. 群内发送：`/天气 北京`，应直接收到卡片
4. 发送：`灾害检测`，应只看到及时通道事件

## 主要文件

```text
astrbot_plugin_disaster_alert/
  main.py
  card_renderer.py
  map_renderer.py
  geo_utils.py
  models.py
  http_client.py
  state_store.py
  _conf_schema.json
  metadata.yaml
  sources/
    earthquake.py
    typhoon.py
    tsunami.py
    critical_weather.py
    region_weather.py
```

## 免责声明

本插件仅汇总公开官方信息，推送中的安全忠告为一般性提示，不构成应急指令。请以当地政府部门最新通告为准。