"""Optional outdoor forecast for TAMO's deferral logic.

The controller never needs a forecast: when it cannot be fetched (no internet,
service down) decisions use live sensors only. Open-Meteo is used because it
needs no account or key. Data older than ``MAX_AGE_H`` is discarded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .hardware.base import HardwareError
from .hardware.http import get_json
from .psychro import AirState, rh_from_dew_point

MAX_AGE_H = 3.0
REFRESH_MIN = 30.0


def parse_open_meteo(data: dict) -> list[tuple[datetime, AirState]]:
    h = data.get("hourly") or {}
    out = []
    for ts, t, td in zip(h.get("time", []), h.get("temperature_2m", []), h.get("dew_point_2m", [])):
        if t is None or td is None:
            continue
        when = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        out.append((when, AirState.from_t_rh(t, min(1.0, rh_from_dew_point(t, min(td, t))))))
    return out


class OpenMeteoForecast:
    source = "open-meteo"

    async def fetch(self, lat: float, lon: float, now: datetime) -> list[tuple[datetime, AirState]]:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}"
               "&hourly=temperature_2m,dew_point_2m&forecast_days=2&timezone=UTC")
        data = await get_json(url, timeout=8)
        rows = parse_open_meteo(data)
        if not rows:
            raise HardwareError("forecast response had no hourly data")
        return rows


class MockForecast:
    """Forecast for mock hardware: the mock world's own climate profile (labeled mock)."""

    source = "mock"

    def __init__(self, world):
        self.world = world

    async def fetch(self, lat: float, lon: float, now: datetime) -> list[tuple[datetime, AirState]]:
        base = now.replace(minute=0, second=0, microsecond=0)
        out = []
        for j in range(1, 13):
            ts = base + timedelta(hours=j)
            out.append((ts, self.world.model.outdoor_at(self.world.profile_hour(ts))))
        return out


class ForecastCache:
    def __init__(self, provider):
        self.provider = provider
        self.rows: list[tuple[datetime, AirState]] = []
        self.fetched_at: datetime | None = None
        self.last_try: datetime | None = None
        self.error: str | None = None

    async def refresh(self, config, now: datetime) -> None:
        t = config.temperature
        if not t.forecast_enabled or self.provider is None:
            return
        if self.last_try and (now - self.last_try) < timedelta(minutes=REFRESH_MIN):
            return
        self.last_try = now
        try:
            self.rows = await self.provider.fetch(t.forecast_latitude, t.forecast_longitude, now)
            self.fetched_at = now
            self.error = None
        except (HardwareError, ValueError, KeyError) as e:
            self.error = str(e)

    def age_h(self, now: datetime) -> float | None:
        return (now - self.fetched_at).total_seconds() / 3600 if self.fetched_at else None

    def current(self, now: datetime) -> list[tuple[datetime, AirState]] | None:
        age = self.age_h(now)
        if age is None or age > MAX_AGE_H:
            return None
        return [(ts, a) for ts, a in self.rows if ts > now]

    def status(self, now: datetime, config) -> dict:
        return {"enabled": config.temperature.forecast_enabled, "source": getattr(self.provider, "source", None),
                "age_h": self.age_h(now), "error": self.error, "usable": self.current(now) is not None,
                "hours": [{"ts": ts.isoformat(), "t_c": a.t_c, "dew_point_c": a.dew_point_c, "w": a.w}
                          for ts, a in (self.current(now) or [])[:12]]}
