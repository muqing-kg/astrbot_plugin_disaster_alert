"""官方灾害数据源采集器。"""

from .earthquake import fetch_earthquakes
from .typhoon import fetch_typhoons
from .tsunami import fetch_tsunami_events
from .critical_weather import fetch_critical_life_alerts
from .region_weather import query_region_weather

__all__ = [
    "fetch_earthquakes",
    "fetch_typhoons",
    "fetch_tsunami_events",
    "fetch_critical_life_alerts",
    "query_region_weather",
]