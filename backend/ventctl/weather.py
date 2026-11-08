"""Weather observations from NOAA, cached locally for offline simulation.

Source: NOAA NCEI Access Data Service, dataset ``global-hourly`` (Integrated
Surface Database). Documented at https://www.ncei.noaa.gov/access/services/data/v1
No API key needed. Default station: New Orleans Louis Armstrong International
(MSY), USAF 722310 / WBAN 12916 -> ``72231012916``.

Values arrive as ``"+0289,1"``: tenths of deg C followed by a quality code;
``+9999`` means missing. Reports are averaged into UTC hours and stored in the
``weather_cache`` table, so simulations work without internet afterwards.
These are station *observations*, distinct from the synthetic profiles.

Note: this was written from NOAA's documented format and checked against
fixture data. The cloud environment it was built in could not reach NOAA, so
the first real download should be verified (the Simulation page shows the
row count and date range).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .climate import ClimateProfile
from .hardware.base import HardwareError
from .hardware.http import get_json
from .units import c_to_f

SOURCE = "noaa-global-hourly"
DEFAULT_STATION = "72231012916"
STATIONS = {"72231012916": "New Orleans Intl (MSY)", "72231753917": "New Orleans Lakefront (NEW)"}
ADS_URL = "https://www.ncei.noaa.gov/access/services/data/v1"


def _isd_value(v) -> float | None:
    if not v or not isinstance(v, str):
        return None
    raw, _, quality = v.partition(",")
    if raw.strip().lstrip("+-") in ("9999", ""):
        return None
    if quality and quality.strip() not in ("0", "1", "4", "5", "9", "A", "C", "I", "M", "P", "R", "U"):
        return None  # suspect / erroneous quality flags (2, 3, 6, 7)
    try:
        return int(raw) / 10.0
    except ValueError:
        return None


def parse_ads(rows: list[dict]) -> list[tuple[datetime, float | None, float | None]]:
    """Average ISD reports into UTC hours -> [(hour, temp_c, dewpoint_c)]."""
    buckets: dict[datetime, list[tuple[float | None, float | None]]] = {}
    for r in rows:
        try:
            t = datetime.fromisoformat(r["DATE"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        hour = t.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour, []).append((_isd_value(r.get("TMP")), _isd_value(r.get("DEW"))))
    out = []
    for hour in sorted(buckets):
        ts = [a for a, _ in buckets[hour] if a is not None]
        ds = [b for _, b in buckets[hour] if b is not None]
        out.append((hour, sum(ts) / len(ts) if ts else None, sum(ds) / len(ds) if ds else None))
    return out


async def fetch_noaa(station: str, start: datetime, end: datetime, timeout: float = 60) -> list[tuple]:
    if (end - start) > timedelta(days=370):
        raise ValueError("download at most about one year at a time")
    q = urlencode({"dataset": "global-hourly", "stations": station, "dataTypes": "TMP,DEW", "format": "json",
                   "startDate": start.strftime("%Y-%m-%dT%H:%M:%S"), "endDate": end.strftime("%Y-%m-%dT%H:%M:%S"),
                   "units": "metric"})
    data = await get_json(f"{ADS_URL}?{q}", timeout)
    if not isinstance(data, list):
        raise HardwareError("unexpected NOAA response (expected a JSON list)")
    return parse_ads(data)


def fill_gaps(rows: list[dict], max_gap_h: int = 6) -> tuple[list[float], list[float], int]:
    """Hourly series in deg F with short gaps linearly interpolated. Returns (temps, dewpoints, filled)."""
    pts = [(datetime.fromisoformat(r["ts"]), r["temp_c"], r["dewpoint_c"]) for r in rows
           if r["temp_c"] is not None and r["dewpoint_c"] is not None]
    if len(pts) < 2:
        raise ValueError("not enough cached weather for this range")
    temps, dps, filled = [c_to_f(pts[0][1])], [c_to_f(pts[0][2])], 0
    for (t0, a0, b0), (t1, a1, b1) in zip(pts, pts[1:]):
        gap = int(round((t1 - t0).total_seconds() / 3600))
        if gap > max_gap_h:
            raise ValueError(f"cached weather has a {gap}-hour gap at {t0:%Y-%m-%d %H:00}; download that period again")
        for k in range(1, gap + 1):
            f = k / gap
            temps.append(c_to_f(a0 + (a1 - a0) * f))
            dps.append(c_to_f(b0 + (b1 - b0) * f))
        filled += max(gap - 1, 0)
    return temps, dps, filled


def profile_from_cache(rows: list[dict], station: str, start: datetime, end: datetime,
                       basement_temp_f: float = 79, surface_temp_f: float = 76) -> ClimateProfile:
    temps, dps, filled = fill_gaps(rows)
    name = STATIONS.get(station, station)
    return ClimateProfile(
        "noaa", f"NOAA observations · {name} · {start:%b %d %Y}–{end:%b %d %Y}",
        f"{len(temps)} hourly values from NOAA ISD global-hourly ({filled} gap hours interpolated)",
        [round(t, 2) for t in temps], [round(min(d, t), 2) for t, d in zip(temps, dps)],
        source="observed", basement_temp_f=basement_temp_f, surface_temp_f=surface_temp_f,
        notes=[f"Observed data: NOAA NCEI global-hourly, station {station}. Cached locally."],
    )
