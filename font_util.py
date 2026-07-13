"""跨平台中文字体加载。

优先顺序：
1) 插件内置 fonts/ 目录
2) 常见系统中文字体（Windows/Linux/macOS）
3) 自动下载 Noto Sans SC 到插件 data/fonts（可写目录）
"""

from __future__ import annotations

import os
import urllib.request
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_PLUGIN_DIR = Path(__file__).resolve().parent
_FONT_URLS = [
    # Google Noto Sans SC Regular (OFL)
    "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf",
    "https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
]
_BOLD_URLS = [
    "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Bold.otf",
    "https://github.com/googlefonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Bold.otf",
]


def _candidate_paths(bold: bool = False) -> list[str]:
    names = (
        ["NotoSansCJKsc-Bold.otf", "NotoSansSC-Bold.otf", "msyhbd.ttc", "SourceHanSansSC-Bold.otf"]
        if bold
        else ["NotoSansCJKsc-Regular.otf", "NotoSansSC-Regular.otf", "msyh.ttc", "SourceHanSansSC-Regular.otf"]
    )
    paths: list[str] = []

    # 1) plugin bundled fonts/
    bundled = _PLUGIN_DIR / "fonts"
    for n in names:
        paths.append(str(bundled / n))

    # 2) writable plugin data fonts (downloaded)
    for base in (
        os.environ.get("ASTRBOT_PLUGIN_DATA"),
        str(_PLUGIN_DIR / "data" / "fonts"),
        str(Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_disaster_alert" / "fonts"),
        str(Path.home() / ".astrbot" / "data" / "plugin_data" / "astrbot_plugin_disaster_alert" / "fonts"),
        str(Path.home() / ".cache" / "astrbot_plugin_disaster_alert" / "fonts"),
    ):
        if not base:
            continue
        for n in names:
            paths.append(str(Path(base) / n))

    # 3) system fonts
    if bold:
        paths.extend(
            [
                r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\simhei.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Medium.ttc",
            ]
        )
    else:
        paths.extend(
            [
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\simhei.ttf",
                r"C:\Windows\Fonts\simsun.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                "/usr/share/fonts/truetype/arphic/uming.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/STHeiti Light.ttc",
            ]
        )
    return paths


def _download_font(bold: bool = False) -> str | None:
    urls = _BOLD_URLS if bold else _FONT_URLS
    # writable targets
    targets = [
        _PLUGIN_DIR / "fonts",
        Path.home() / ".cache" / "astrbot_plugin_disaster_alert" / "fonts",
        Path.cwd() / "data" / "plugin_data" / "astrbot_plugin_disaster_alert" / "fonts",
    ]
    filename = "NotoSansCJKsc-Bold.otf" if bold else "NotoSansCJKsc-Regular.otf"
    for folder in targets:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            dest = folder / filename
            if dest.exists() and dest.stat().st_size > 100_000:
                return str(dest)
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "AstrBotDisasterAlert/1.2"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                    if len(data) < 100_000:
                        continue
                    dest.write_bytes(data)
                    return str(dest)
                except Exception:
                    continue
        except Exception:
            continue
    return None


@lru_cache(maxsize=16)
def get_font(size: int = 22, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # existing fonts
    for path in _candidate_paths(bold=bold):
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    # try download once
    downloaded = _download_font(bold=bold)
    if downloaded:
        try:
            return ImageFont.truetype(downloaded, size=size)
        except Exception:
            pass
    # last fallback: default (may not support CJK, but avoid crash)
    return ImageFont.load_default()