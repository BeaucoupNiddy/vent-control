"""Optional outdoor forecast for TAMO's deferral logic.

The controller never needs a forecast: when it cannot be fetched (no internet,
service down) decisions use live sensors only. Open-Meteo is used because it
needs no account or key. Data older than ``MAX_AGE_H`` is discarded.

Calibration to the outdoor sensor: a weather model describes a grid cell at 2 m in the
open, while the outdoor sensor sits in one yard, under cover, next to the house. The
difference is mostly steady (the sensor reads warmer and more humid), so the cache keeps
the last few days of outdoor readings and compares them with the model's values for the
same hours (Open-Meteo returns past hours too). Forecast hours are then shifted by

    offset(lead) = typical + (now - typical) * exp(-lead / BIAS_DECAY_H)

where ``now`` is the gap at the latest reading, so the forecast starts where the sensor
is, and ``typical`` is the average gap over ``BIAS_WINDOW_H`` (the same for the hour of day
when there is enough data). Temperature and dew point are corrected separately.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timedelta, timezone

from .hardware.base import HardwareError
from .hardware.http import get_json
from .psychro import AirState, rh_from_dew_point

MAX_AGE_H = 3.0
REFRESH_MIN = 30.0
BIAS_WINDOW_H = 72.0      # outdoor readings kept to learn the sensor-vs-model offset
BIAS_MIN_HOURS = 6        # hours of overlap needed before the typical offset is used
BIAS_DECAY_H = 6.0        # how fast the "right now" offset fades into the typical one
BIAS_NOW_MIN = 90.0       # readings in the last this many minutes set the "right now" offset
BIAS_CLAMP_C = 6.0        # never shift the forecast by more than this (~11 °F)
OBS_EVERY_MIN = 5.0


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
               "&hourly=temperature_2m,dew_point_2m&past_days=3&forecast_days=2&timezone=UTC")
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


def _interp(rows: list[tuple[datetime, AirState]], ts: datetime) -> tuple[float, float] | None:
    """Model temperature and dew point at ``ts`` by linear interpolation between hourly rows."""
    for (a, sa), (b, sb) in zip(rows, rows[1:]):
        if a <= ts <= b:
            span = (b - a).total_seconds()
            k = (ts - a).total_seconds() / span if span > 0 else 0.0
            return (sa.t_c + k * (sb.t_c - sa.t_c), sa.dew_point_c + k * (sb.dew_point_c - sa.dew_point_c))
    return None


def _clamp(x: float) -> float:
    return max(-BIAS_CLAMP_C, min(BIAS_CLAMP_C, x))


class ForecastCache:
    def __init__(self, provider):
        self.provider = provider
        self.rows: list[tuple[datetime, AirState]] = []
        self.fetched_at: datetime | None = None
        self.last_try: datetime | None = None
        self.error: str | None = None
        self.obs: deque = deque()          # (ts, t_c, dew_point_c) outdoor readings
        self.seeded = False

    # ---------------------------------------------------------------- outdoor sensor
    def seed(self, history_rows: list[dict]) -> None:
        """Load recent outdoor readings from stored history (once, after a restart)."""
        self.seeded = True
        for r in history_rows:
            t, td = r.get("outdoor_t_c"), r.get("outdoor_td_c")
            if t is None or td is None or r.get("source") not in (None, "measured"):
                continue
            try:
                ts = datetime.fromisoformat(r["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            self._add_obs(ts, float(t), float(td))

    def observe(self, now: datetime, air: AirState | None) -> None:
        if air is None:
            return
        self._add_obs(now, air.t_c, air.dew_point_c)

    def _add_obs(self, ts: datetime, t_c: float, td_c: float) -> None:
        if self.obs and (ts - self.obs[-1][0]) < timedelta(minutes=OBS_EVERY_MIN):
            return
        self.obs.append((ts, t_c, td_c))
        while self.obs and (ts - self.obs[0][0]) > timedelta(hours=BIAS_WINDOW_H):
            self.obs.popleft()

    def bias(self, now: datetime) -> dict | None:
        """Sensor minus model, °C, for temperature and dew point: typical and right now."""
        if not self.rows or not self.obs:
            return None
        pairs = []
        for ts, t, td in self.obs:
            if ts > now:
                continue
            m = _interp(self.rows, ts)
            if m is not None:
                pairs.append((ts, t - m[0], td - m[1]))
        if not pairs:
            return None
        hours = {p[0].replace(minute=0, second=0, microsecond=0) for p in pairs}
        recent = [p for p in pairs if (now - p[0]) <= timedelta(minutes=BIAS_NOW_MIN)]
        out: dict = {"hours": len(hours), "typical_t": 0.0, "typical_td": 0.0, "now_t": None, "now_td": None}
        if len(hours) >= BIAS_MIN_HOURS:
            out["typical_t"] = _clamp(sum(p[1] for p in pairs) / len(pairs))
            out["typical_td"] = _clamp(sum(p[2] for p in pairs) / len(pairs))
        if recent:
            # latest readings weigh most: the line should meet the sensor at "now"
            last = recent[-3:]
            out["now_t"] = _clamp(sum(p[1] for p in last) / len(last))
            out["now_td"] = _clamp(sum(p[2] for p in last) / len(last))
        return out

    def _corrected(self, now: datetime, rows: list[tuple[datetime, AirState]]) -> list[tuple[datetime, AirState]]:
        b = self.bias(now)
        if not b:
            return rows
        out = []
        for ts, a in rows:
            lead = max(0.0, (ts - now).total_seconds() / 3600)
            fade = math.exp(-lead / BIAS_DECAY_H)
            dt = b["typical_t"] + ((b["now_t"] - b["typical_t"]) * fade if b["now_t"] is not None else 0.0)
            dd = b["typical_td"] + ((b["now_td"] - b["typical_td"]) * fade if b["now_td"] is not None else 0.0)
            if abs(dt) < 1e-9 and abs(dd) < 1e-9:
                out.append((ts, a))
                continue
            t = a.t_c + dt
            td = min(a.dew_point_c + dd, t)
            out.append((ts, AirState.from_t_rh(t, min(1.0, rh_from_dew_point(t, td)), a.p_kpa)))
        return out

    # ---------------------------------------------------------------- forecast
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

    def raw(self, now: datetime) -> list[tuple[datetime, AirState]] | None:
        age = self.age_h(now)
        if age is None or age > MAX_AGE_H:
            return None
        return [(ts, a) for ts, a in self.rows if ts > now]

    def current(self, now: datetime) -> list[tuple[datetime, AirState]] | None:
        """Future hours, shifted to match the outdoor sensor (see module docstring)."""
        rows = self.raw(now)
        return None if rows is None else self._corrected(now, rows)

    def status(self, now: datetime, config) -> dict:
        raw = self.raw(now) or []
        cur = self._corrected(now, raw) if raw else []
        b = self.bias(now) if raw else None
        cal = None
        if b:
            f = lambda x: None if x is None else round(x * 1.8, 1)  # noqa: E731 (°C difference -> °F)
            cal = {"hours": b["hours"], "typical_t_f": f(b["typical_t"]), "typical_td_f": f(b["typical_td"]),
                   "now_t_f": f(b["now_t"]), "now_td_f": f(b["now_td"]), "decay_h": BIAS_DECAY_H,
                   "window_h": BIAS_WINDOW_H}
        return {"enabled": config.temperature.forecast_enabled, "source": getattr(self.provider, "source", None),
                "age_h": self.age_h(now), "error": self.error, "usable": self.raw(now) is not None,
                "calibration": cal,
                "hours": [{"ts": ts.isoformat(), "t_c": a.t_c, "dew_point_c": a.dew_point_c, "w": a.w,
                           "raw_t_c": r.t_c, "raw_dew_point_c": r.dew_point_c}
                          for (ts, a), (_, r) in list(zip(cur, raw))[:12]]}
