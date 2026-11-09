"""Measured-surface condensation monitoring (concrete probe, e.g. Ecowitt WN30 on CH1-8).

    condensation margin = measured surface temperature - dew point of the LOCAL air

The local air is the zone the probe is assigned to. A probe measures one spot on
one wall: it is not a measurement of every wall and slab. The margin says when
condensation is thermodynamically possible, not that liquid water is present.

Risk is classified on a *conservative* margin (measured margin minus the combined
probe and dew-point uncertainty), so a reading 0.5 °F above the dew point is not
treated as safe. Classes only improve once the margin clears the threshold by a
hysteresis band.

Three situations are reported separately:
  1. current:  measured surface vs measured local air
  2. if ventilating: predicted surface vs predicted local dew point with exterior fans on
     (from the shared coupled model), plus outdoor dew point vs the surface
  3. future:  forecast outdoor dew point vs the surface, a potential risk only if
     outdoor air reaches the surface (infiltration, ventilation)

Estimated near-surface RH = local vapor pressure / saturation pressure at the surface
temperature. It is kept uncapped internally (above 100% = supersaturation potential)
and displayed capped. It is not the moisture content of the concrete.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import psychro
from .config import AppConfig, SensorRole
from .psychro import AirState
from .units import c_to_f, delta_c_to_f

CLASSES = ("lower", "monitor", "elevated", "possible")
CLASS_LABELS = {"lower": "Lower immediate risk", "monitor": "Monitor", "elevated": "Elevated risk",
                "possible": "Condensation possible"}
DEFAULT_AIR_FOLLOW = 0.5


def surface_rh(local: AirState, surface_c: float) -> float:
    """Uncapped estimated RH right at the surface, fraction."""
    return local.pw_kpa / psychro.saturation_pressure(surface_c)


def classify(cons_margin_f: float, ss, previous: str | None = None) -> str:
    """Risk class from the conservative margin, with hysteresis toward lower risk."""
    def raw(m: float) -> str:
        if m <= ss.possible_margin_f:
            return "possible"
        if m < ss.elevated_margin_f:
            return "elevated"
        if m < ss.monitor_margin_f:
            return "monitor"
        return "lower"
    new = raw(cons_margin_f)
    if previous in CLASSES and CLASSES.index(new) < CLASSES.index(previous):
        # improving: require the margin to clear the threshold by the hysteresis band
        held = raw(cons_margin_f - ss.hysteresis_f)
        if CLASSES.index(held) >= CLASSES.index(previous):
            return previous
        return held
    return new


def air_follow_fraction(config: AppConfig, learned: dict | None) -> tuple[float, str]:
    ss = config.surfaces
    if ss.air_follow_fraction is not None:
        return ss.air_follow_fraction, "configured"
    if learned and learned.get("air_follow_fraction") is not None and learned.get("confidence") in ("medium", "high"):
        return float(learned["air_follow_fraction"]), "learned"
    return DEFAULT_AIR_FOLLOW, "assumed"


@dataclass
class SurfaceStatus:
    sensor_id: str
    name: str
    zone: str | None
    zone_name: str
    t_c: float | None
    status: str
    local: AirState | None = None
    local_source: str = "zone"  # zone | fallback | none
    margin_f: float | None = None
    uncertainty_f: float = 0.0
    conservative_margin_f: float | None = None
    surface_rh: float | None = None  # uncapped fraction
    risk: str | None = None
    trend: dict = field(default_factory=dict)
    minutes: dict = field(default_factory=dict)
    cause: str = ""
    ventilation: dict = field(default_factory=dict)
    future: dict = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict:
        lo = self.local
        return {
            "sensor_id": self.sensor_id, "name": self.name, "zone": self.zone, "zone_name": self.zone_name,
            "status": self.status, "t_c": self.t_c,
            "local_t_c": lo.t_c if lo else None, "local_rh_pct": lo.rh * 100 if lo else None,
            "local_dew_point_c": lo.dew_point_c if lo else None, "local_source": self.local_source,
            "margin_f": self.margin_f, "uncertainty_f": self.uncertainty_f,
            "conservative_margin_f": self.conservative_margin_f,
            "surface_rh_pct": None if self.surface_rh is None else min(100.0, self.surface_rh * 100),
            "surface_rh_uncapped_pct": None if self.surface_rh is None else self.surface_rh * 100,
            "risk": self.risk, "risk_label": CLASS_LABELS.get(self.risk or "", None),
            "surface_minus_air_f": (delta_c_to_f(self.t_c - lo.t_c) if (lo and self.t_c is not None) else None),
            "trend": self.trend, "minutes": self.minutes, "cause": self.cause,
            "ventilation": self.ventilation, "future": self.future, "message": self.message,
        }


class SurfaceTracker:
    def __init__(self):
        self.hist: dict[str, deque] = {}
        self.risk: dict[str, str] = {}
        self.since: dict[tuple[str, str], datetime] = {}

    def _since(self, key: tuple[str, str], active: bool, now: datetime) -> float:
        if active:
            self.since.setdefault(key, now)
            return (now - self.since[key]).total_seconds() / 60
        self.since.pop(key, None)
        return 0.0

    def trend(self, sid: str, now: datetime, window_min: float = 60) -> dict:
        pts = [p for p in self.hist.get(sid, ()) if now - p[0] <= timedelta(minutes=window_min)]
        if len(pts) < 3 or (pts[-1][0] - pts[0][0]) < timedelta(minutes=10):
            return {"surface_f_h": None, "dew_point_f_h": None, "margin_f_h": None}
        xs = [(p[0] - pts[0][0]).total_seconds() / 3600 for p in pts]
        mx = sum(xs) / len(xs)
        sxx = sum((x - mx) ** 2 for x in xs) or 1e-9

        def slope(i: int) -> float:
            ys = [p[i] for p in pts]
            my = sum(ys) / len(ys)
            return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        s, d = slope(1) * 1.8, slope(2) * 1.8
        return {"surface_f_h": s, "dew_point_f_h": d, "margin_f_h": s - d}


def surface_statuses(snapshot, config: AppConfig, zones: dict, tracker: SurfaceTracker | None, now: datetime,
                     fallback: AirState | None = None, outdoor: AirState | None = None,
                     forecast: list | None = None) -> list[SurfaceStatus]:
    ss = config.surfaces
    out: list[SurfaceStatus] = []
    probes = [r for r in snapshot.by_role(SensorRole.SURFACE)] if snapshot else []
    for r in probes:
        m = r.mapping
        st = SurfaceStatus(m.sensor_id, m.name, m.zone, config.zone_name(m.zone) if m.zone else "not assigned",
                           r.temp_c if r.ok else None, r.status)
        if not r.ok or r.temp_c is None:
            st.message = (f"{m.name}: direct surface monitoring unavailable ({r.message or r.status}). Probe-based "
                          "condensation predictions are off; general dew-point protection still applies.")
            if tracker:
                tracker.risk.pop(m.sensor_id, None)
            out.append(st)
            continue
        z = zones.get(m.zone) if m.zone else None
        if z is not None and z.air is not None and not z.colocated:
            st.local, st.local_source = z.air, "zone"
        elif fallback is not None:
            st.local, st.local_source = fallback, "fallback"
            st.message = ("Local air sensor unavailable or not assigned; comparing with the wettest basement zone "
                          "instead (conservative).")
        else:
            st.local_source = "none"
            st.message = "No air reading to compare with; margin unavailable."
            out.append(st)
            continue
        lo = st.local
        st.margin_f = delta_c_to_f(r.temp_c - lo.dew_point_c)
        st.uncertainty_f = math.hypot(ss.probe_uncertainty_f, ss.dew_point_uncertainty_f)
        st.conservative_margin_f = st.margin_f - st.uncertainty_f
        st.surface_rh = surface_rh(lo, r.temp_c)
        prev = tracker.risk.get(m.sensor_id) if tracker else None
        st.risk = classify(st.conservative_margin_f, ss, prev)
        if tracker:
            tracker.risk[m.sensor_id] = st.risk
            h = tracker.hist.setdefault(m.sensor_id, deque(maxlen=300))
            if h and h[-1][0] >= now:
                h.pop()
            h.append((now, r.temp_c, lo.dew_point_c, lo.t_c))
            st.trend = tracker.trend(m.sensor_id, now)
            sid = m.sensor_id
            st.minutes = {
                "below_monitor": tracker._since((sid, "monitor"), st.conservative_margin_f < ss.monitor_margin_f, now),
                "below_elevated": tracker._since((sid, "elevated"), st.conservative_margin_f < ss.elevated_margin_f,
                                                 now),
                "condensation_possible": tracker._since((sid, "possible"), st.risk == "possible", now),
                "surface_rh_elevated": tracker._since((sid, "srh"), st.surface_rh * 100 >= ss.surface_rh_elevated_pct,
                                                      now),
                "near_saturation": tracker._since((sid, "sat"), st.surface_rh >= 0.95 or st.risk == "possible", now),
            }
        st.cause = explain_cause(st)
        if outdoor is not None:
            om = delta_c_to_f(r.temp_c - outdoor.dew_point_c)
            st.ventilation["outdoor_dew_point_c"] = outdoor.dew_point_c
            st.ventilation["outdoor_margin_f"] = om
            st.ventilation["outdoor_air_would_condense"] = om <= ss.possible_margin_f + st.uncertainty_f
            st.ventilation["outdoor_air_risky"] = om < ss.elevated_margin_f + st.uncertainty_f
        if forecast:
            horizon = [(ts, a) for ts, a in forecast if timedelta(0) < ts - now <= timedelta(hours=12)]
            if horizon:
                ts, a = max(horizon, key=lambda x: x[1].dew_point_c)
                fm = delta_c_to_f(r.temp_c - a.dew_point_c)
                st.future = {"max_outdoor_dew_point_c": a.dew_point_c, "at": ts.isoformat(), "margin_f": fm,
                             "potential_risk": fm < ss.elevated_margin_f + st.uncertainty_f,
                             "note": "Applies only if outdoor air reaches this surface (ventilation, infiltration)."}
        out.append(st)
    return out


def explain_cause(st: SurfaceStatus) -> str:
    tr = st.trend or {}
    s, d = tr.get("surface_f_h"), tr.get("dew_point_f_h")
    if st.margin_f is None:
        return ""
    if s is None or d is None:
        return "Not enough recent data for a trend yet."
    if st.risk in ("lower", None) and (tr.get("margin_f_h") or 0) >= -0.2:
        return "Margin is comfortable and not shrinking."
    if s < -0.3 and d > -0.1:
        return "Concrete temperature has fallen while the basement dew point stays elevated."
    if d > 0.3 and s > -0.1:
        return "Basement dew point is rising and approaching the concrete temperature."
    if s < -0.3 and d > 0.3:
        return "Concrete is cooling and the local dew point is rising at the same time."
    if (tr.get("margin_f_h") or 0) > 0.2:
        return "Margin is recovering."
    return "Margin is steady but small."


def worst(statuses: list[SurfaceStatus]) -> SurfaceStatus | None:
    ok = [s for s in statuses if s.conservative_margin_f is not None]
    return min(ok, key=lambda s: s.conservative_margin_f) if ok else None


def fit_air_follow(rows: list[dict], min_air_change_f: float = 1.0) -> dict:
    """How much of a fan-driven air-temperature change the measured surface follows within ~45 minutes."""
    from datetime import datetime as _dt
    ratios = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if not ((a.get("fans_on") or 0) == 0 and (b.get("fans_on") or 0) > 0):
            continue
        t0 = _dt.fromisoformat(b["ts"])
        if a.get("indoor_t_c") is None or a.get("surface_t_c") is None or a.get("surface_source") != "measured":
            continue
        later = next((r for r in rows[i:] if 40 <= (_dt.fromisoformat(r["ts"]) - t0).total_seconds() / 60 <= 70
                      and (r.get("fans_on") or 0) > 0), None)
        if not later or later.get("indoor_t_c") is None or later.get("surface_t_c") is None:
            continue
        da = (later["indoor_t_c"] - a["indoor_t_c"]) * 1.8
        ds = (later["surface_t_c"] - a["surface_t_c"]) * 1.8
        if abs(da) >= min_air_change_f:
            ratios.append(max(0.0, min(1.0, ds / da)))
    n = len(ratios)
    if n == 0:
        return {"air_follow_fraction": None, "cycles": 0, "confidence": "none"}
    ratios.sort()
    med = ratios[n // 2] if n % 2 else 0.5 * (ratios[n // 2 - 1] + ratios[n // 2])
    spread = (ratios[int(0.75 * (n - 1))] - ratios[int(0.25 * (n - 1))]) if n >= 4 else 1.0
    conf = "high" if n >= 8 and spread < 0.2 else "medium" if n >= 4 and spread < 0.35 else "low"
    # report the 75th percentile when confidence is not high: a surface that follows the air more is the
    # conservative assumption for cold-air ventilation
    use = med if conf == "high" else ratios[int(0.75 * (n - 1))]
    return {"air_follow_fraction": use, "median": med, "cycles": n, "confidence": conf}


def to_f(t_c: float | None) -> float | None:
    return c_to_f(t_c) if t_c is not None else None
