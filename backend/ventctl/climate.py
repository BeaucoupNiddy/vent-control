"""Outdoor climate profiles for simulation.

All built-in profiles are SYNTHETIC and ILLUSTRATIVE. They were shaped by hand
to resemble typical New Orleans conditions; they are not NOAA observations or
climate normals. Real observations can be imported from CSV, or taken from the
app's own recorded outdoor sensor history.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field

from . import psychro
from .units import c_to_f, f_to_c


@dataclass
class ClimateProfile:
    id: str
    name: str
    description: str
    temp_f: list[float]  # hourly outdoor dry-bulb
    dewpoint_f: list[float]  # hourly outdoor dew point
    source: str = "synthetic"  # synthetic | imported | recorded
    basement_temp_f: float = 79.0  # suggested mean basement temperature
    surface_temp_f: float = 76.0  # suggested coldest-surface temperature
    notes: list[str] = field(default_factory=list)

    @property
    def hours(self) -> int:
        return len(self.temp_f)

    def at(self, hour: float) -> tuple[float, float]:
        """(temp_c, dewpoint_c) at fractional hour, linear interpolation, looping."""
        n = self.hours
        h = hour % n
        i = int(h)
        frac = h - i
        j = (i + 1) % n
        t = self.temp_f[i] + (self.temp_f[j] - self.temp_f[i]) * frac
        td = self.dewpoint_f[i] + (self.dewpoint_f[j] - self.dewpoint_f[i]) * frac
        td = min(td, t)
        return f_to_c(t), f_to_c(td)

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "description": self.description, "source": self.source,
            "hours": self.hours, "basement_temp_f": self.basement_temp_f, "surface_temp_f": self.surface_temp_f,
            "notes": self.notes,
        }

    def to_dict(self) -> dict:
        d = self.summary()
        d["temp_f"] = self.temp_f
        d["dewpoint_f"] = self.dewpoint_f
        return d


SYNTHETIC_NOTE = "Synthetic illustrative profile. Not measured data, not NOAA observations or normals."


def _diurnal(mean: float, amp: float, peak_hour: float = 15.0, hours: int = 24) -> list[float]:
    return [round(mean + amp * math.cos(2 * math.pi * (h - peak_hour) / 24), 2) for h in range(hours)]


def _builtin() -> dict[str, ClimateProfile]:
    p: dict[str, ClimateProfile] = {}
    p["nola_summer"] = ClimateProfile(
        "nola_summer", "Typical New Orleans summer day",
        "Highs near 90°F, lows near 77°F, dew point 73-75°F all day.",
        _diurnal(83.5, 6.5), _diurnal(74.0, -1.0), basement_temp_f=79, surface_temp_f=76,
        notes=[SYNTHETIC_NOTE],
    )
    p["nola_humid"] = ClimateProfile(
        "nola_humid", "Particularly humid summer day",
        "Tropical air mass: dew point 77-78°F, highs near 91°F.",
        _diurnal(85.0, 5.5), _diurnal(77.5, -0.5), basement_temp_f=80, surface_temp_f=76.5,
        notes=[SYNTHETIC_NOTE],
    )
    # 3-day late-summer front: humid day, front passes on day 2, dry day 3
    t, td = [], []
    for h in range(72):
        base_t = 83.5 + 6.5 * math.cos(2 * math.pi * (h - 15) / 24)
        if h < 30:
            dp = 74.0 - 1.0 * math.cos(2 * math.pi * (h - 15) / 24)
            tt = base_t
        elif h < 42:
            k = (h - 30) / 12
            dp = 74.0 + (60.0 - 74.0) * k
            tt = base_t - 6 * k
        else:
            dp = 60.0 - 2.0 * math.cos(2 * math.pi * (h - 15) / 24)
            tt = 77.0 + 8.0 * math.cos(2 * math.pi * (h - 15) / 24)
        t.append(round(tt, 2))
        td.append(round(min(dp, tt), 2))
    p["nola_front"] = ClimateProfile(
        "nola_front", "Summer with a dry-air intrusion (3 days)",
        "Humid day, then a front brings dew points into the upper 50s / low 60s.",
        t, td, basement_temp_f=79, surface_temp_f=76, notes=[SYNTHETIC_NOTE],
    )
    p["nola_fall"] = ClimateProfile(
        "nola_fall", "Typical fall day",
        "Highs near 80°F, lows in the low 60s, dew point 56-60°F.",
        _diurnal(71.5, 8.5), _diurnal(58.0, -1.5), basement_temp_f=74, surface_temp_f=71,
        notes=[SYNTHETIC_NOTE],
    )
    p["nola_winter"] = ClimateProfile(
        "nola_winter", "Typical winter day",
        "Highs in the low 60s, lows in the upper 40s, dew point near 40°F.",
        _diurnal(55.0, 8.0), _diurnal(41.0, -2.0), basement_temp_f=66, surface_temp_f=62,
        notes=[SYNTHETIC_NOTE],
    )
    return p


BUILTIN_PROFILES = _builtin()


def get_profile(profile_id: str) -> ClimateProfile:
    if profile_id not in BUILTIN_PROFILES:
        raise KeyError(profile_id)
    return BUILTIN_PROFILES[profile_id]


def profile_from_csv(text: str, name: str = "Imported CSV") -> ClimateProfile:
    """Parse hourly weather CSV.

    Required: a temperature column (``temp_f`` or ``temp_c``) and either a dew
    point column (``dewpoint_f`` / ``dewpoint_c``) or ``rh_pct``. An ``outdoor_``
    prefix is accepted. Lines starting with # are comments. One row per hour, in
    order. Other columns are ignored.
    """
    body = "\n".join(ln for ln in text.strip().splitlines() if not ln.lstrip().startswith("#"))
    reader = csv.DictReader(io.StringIO(body))
    temps: list[float] = []
    dps: list[float] = []
    for n, row in enumerate(reader, start=2):
        r = {k.strip().lower().removeprefix("outdoor_"): (v or "").strip() for k, v in row.items() if k}
        try:
            if r.get("temp_f"):
                tf = float(r["temp_f"])
            elif r.get("temp_c"):
                tf = c_to_f(float(r["temp_c"]))
            else:
                raise ValueError("missing temperature")
            if r.get("dewpoint_f"):
                df = float(r["dewpoint_f"])
            elif r.get("dewpoint_c"):
                df = c_to_f(float(r["dewpoint_c"]))
            elif r.get("rh_pct"):
                df = c_to_f(psychro.dew_point(f_to_c(tf), float(r["rh_pct"]) / 100))
            else:
                raise ValueError("missing dewpoint_f, dewpoint_c or rh_pct")
        except ValueError as e:
            raise ValueError(f"CSV row {n}: {e}") from None
        temps.append(tf)
        dps.append(min(df, tf))
    if len(temps) < 2:
        raise ValueError("CSV needs at least 2 hourly rows")
    return ClimateProfile("custom", name, f"{len(temps)} hourly rows imported from CSV", temps, dps,
                          source="imported", notes=["Imported by the user; source as provided."])


def custom_profile(temp_f: list[float], dewpoint_f: list[float], name: str = "Custom hourly profile") -> ClimateProfile:
    if len(temp_f) != len(dewpoint_f) or len(temp_f) < 2:
        raise ValueError("custom profile needs matching temp and dew point lists with at least 2 hours")
    return ClimateProfile("custom", name, f"{len(temp_f)} hours entered by the user", list(temp_f),
                          [min(d, t) for t, d in zip(temp_f, dewpoint_f)], source="custom",
                          notes=["Entered by the user."])
