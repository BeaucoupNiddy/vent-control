"""Environmental zones: per-zone psychrometrics, trends, persistence and sensor quality.

A zone is a part of the basement that can differ from the rest (a remote pocket,
a damp corner). Zones are compared by humidity ratio (water per kg of dry air)
and dew point, never by RH alone: a cooler zone shows higher RH with the same
moisture content. RH still matters on its own because materials respond to RH.

A moisture difference between two zones is only treated as real when it exceeds
the combined uncertainty of the two sensors. Inexpensive RH sensors disagree by a
few percent, which can look like a gradient that does not exist.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import psychro
from .config import AppConfig, SensorRole
from .psychro import AirState
from .units import c_to_f, delta_c_to_f, delta_f_to_c

LEVELS = ("acceptable", "observe", "elevated", "high", "critical")


def rh_level(rh_pct: float, cs) -> str:
    if rh_pct >= cs.critical_rh_pct:
        return "critical"
    if rh_pct >= cs.high_rh_pct:
        return "high"
    if rh_pct >= cs.elevated_rh_pct:
        return "elevated"
    if rh_pct >= cs.observe_rh_pct:
        return "observe"
    return "acceptable"


def w_uncertainty(air: AirState, rh_unc_pct: float, t_unc_f: float) -> float:
    """1-sigma-ish uncertainty of a humidity ratio derived from one T/RH sensor [kg/kg]."""
    d_rh = psychro.humidity_ratio(air.t_c, min(1.0, air.rh + rh_unc_pct / 100), air.p_kpa) - air.w
    dt = delta_f_to_c(t_unc_f)
    d_t = psychro.humidity_ratio(air.t_c + dt, air.rh, air.p_kpa) - air.w  # RH read correctly, T off by dt
    return math.hypot(d_rh, d_t)


@dataclass
class ZoneStatus:
    id: str
    name: str
    air: AirState | None
    sensors: list[dict]
    status: str  # ok | partial | unavailable | unconfigured
    quality: str  # good | uncalibrated | poor | unavailable
    rh_uncertainty_pct: float
    w_uncertainty: float | None = None
    level: str = "acceptable"
    trend: dict = field(default_factory=dict)
    minutes_above: dict = field(default_factory=dict)
    colocated: bool = False  # sensor is in a side-by-side comparison, not at its zone
    volume_ft3: float = 8000
    on_ventilation_path: bool = False
    ventilation_exposure: float = 0.5
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.air is not None and not self.colocated

    def to_dict(self) -> dict:
        a = self.air
        return {
            "id": self.id, "name": self.name, "status": self.status, "quality": self.quality,
            "t_c": a.t_c if a else None, "rh_pct": a.rh * 100 if a else None,
            "dew_point_c": a.dew_point_c if a else None, "w_g_kg": a.w * 1000 if a else None,
            "abs_humidity_g_m3": a.abs_humidity_g_m3 if a else None,
            "rh_uncertainty_pct": self.rh_uncertainty_pct,
            "w_uncertainty_g_kg": self.w_uncertainty * 1000 if self.w_uncertainty is not None else None,
            "level": self.level, "trend": self.trend, "minutes_above": self.minutes_above,
            "sensors": self.sensors, "colocated": self.colocated, "volume_ft3": self.volume_ft3,
            "on_ventilation_path": self.on_ventilation_path, "message": self.message,
        }


class ZoneTracker:
    """Short in-memory history per zone for trends and persistence (survives only while running)."""

    def __init__(self, maxlen: int = 300):
        self.hist: dict[str, deque] = {}
        self.above: dict[tuple[str, str], datetime] = {}

    def update(self, now: datetime, zid: str, air: AirState | None, cs) -> dict:
        h = self.hist.setdefault(zid, deque(maxlen=300))
        if air is not None:
            if h and h[-1][0] >= now:
                h.pop()
            h.append((now, air.t_c, air.rh * 100, air.dew_point_c, air.w * 1000))
        mins = {}
        for name in ("elevated", "high", "critical"):
            thr = getattr(cs, f"{name}_rh_pct")
            key = (zid, name)
            if air is None:
                self.above.pop(key, None)
                mins[name] = None
                continue
            rh = air.rh * 100
            active = key in self.above
            if rh >= thr or (active and rh > thr - cs.rh_hysteresis_pct):
                self.above.setdefault(key, now)
                mins[name] = (now - self.above[key]).total_seconds() / 60
            else:
                self.above.pop(key, None)
                mins[name] = 0.0
        return mins

    def trend(self, zid: str, now: datetime, window_min: float = 60) -> dict:
        """Per-hour slopes of RH, dew point and humidity ratio (least squares over the window)."""
        pts = [p for p in self.hist.get(zid, ()) if now - p[0] <= timedelta(minutes=window_min)]
        if len(pts) < 3 or (pts[-1][0] - pts[0][0]) < timedelta(minutes=10):
            return {"rh_pct_h": None, "dew_point_f_h": None, "w_g_kg_h": None, "t_f_h": None}
        xs = [(p[0] - pts[0][0]).total_seconds() / 3600 for p in pts]
        mx = sum(xs) / len(xs)
        sxx = sum((x - mx) ** 2 for x in xs) or 1e-9

        def slope(i: int) -> float:
            ys = [p[i] for p in pts]
            my = sum(ys) / len(ys)
            return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        return {"t_f_h": slope(1) * 1.8, "rh_pct_h": slope(2), "dew_point_f_h": slope(3) * 1.8, "w_g_kg_h": slope(4)}


def zone_ids(config: AppConfig) -> list[str]:
    ids = [z.id for z in config.zones]
    for m in config.sensors.mappings:
        if m.role == SensorRole.BASEMENT and m.zone and m.zone not in ids:
            ids.append(m.zone)
    return ids


def zone_statuses(snapshot, config: AppConfig, tracker: ZoneTracker | None, now: datetime,
                  colocated: set[str] | None = None) -> dict[str, ZoneStatus]:
    cs = config.circulation
    colocated = colocated or set()
    out: dict[str, ZoneStatus] = {}
    basement = snapshot.by_role(SensorRole.BASEMENT) if snapshot else []
    for zid in zone_ids(config):
        z = config.zone(zid)
        rs = [r for r in basement if r.mapping.zone == zid]
        good = [(r, r.air()) for r in rs if r.ok]
        good = [(r, a) for r, a in good if a is not None]
        if good:
            # several sensors in one zone: average temperature and moisture content, then derive RH
            t = sum(a.t_c for _, a in good) / len(good)
            w = sum(a.w for _, a in good) / len(good)
            air = AirState.from_t_w(t, w, good[0][1].p_kpa)
        else:
            air = None
        calibrated = bool(good) and all(r.mapping.calibrated_at for r, _ in good)
        poor = any(r.battery_low or r.duplicate for r, _ in good)
        unc = cs.rh_uncertainty_calibrated_pct if calibrated else cs.rh_uncertainty_pct
        if not rs:
            status, quality, msg = "unconfigured", "unavailable", "No sensor assigned to this zone"
        elif not good:
            status, quality = "unavailable", "unavailable"
            msg = "; ".join(r.message for r in rs) or "Sensor unavailable"
        else:
            status = "ok" if len(good) == len(rs) else "partial"
            quality = "poor" if poor else "good" if calibrated else "uncalibrated"
            msg = "" if calibrated else "Not yet compared side by side with the other sensors"
        is_coloc = any(r.mapping.sensor_id in colocated for r in rs)
        mins = tracker.update(now, zid, air, cs) if tracker else {}
        zs = ZoneStatus(
            id=zid, name=z.name if z else zid, air=air,
            sensors=[{"sensor_id": r.mapping.sensor_id, "name": r.mapping.name, "status": r.status,
                      "calibrated_at": r.mapping.calibrated_at, "battery_low": r.battery_low} for r in rs],
            status=status, quality=quality, rh_uncertainty_pct=unc,
            w_uncertainty=w_uncertainty(air, unc, cs.temp_uncertainty_f) if air else None,
            level=rh_level(air.rh * 100, cs) if air else "acceptable",
            trend=tracker.trend(zid, now) if tracker else {},
            minutes_above=mins, colocated=is_coloc,
            volume_ft3=z.volume_ft3 if z else 8000, on_ventilation_path=z.on_ventilation_path if z else False,
            ventilation_exposure=z.ventilation_exposure if z else 0.5,
            message="Sensor is in a side-by-side comparison; zone readings are not used for control" if is_coloc
            else msg,
        )
        out[zid] = zs
    return out


def gradient(a: ZoneStatus, b: ZoneStatus, config: AppConfig) -> dict:
    """Moisture difference a - b with uncertainty. reliable = larger than combined sensor uncertainty."""
    if a.air is None or b.air is None:
        return {"a": a.id, "b": b.id, "available": False, "reliable": False,
                "reason": "ZONE_SENSOR_UNAVAILABLE"}
    dw = a.air.w - b.air.w
    unc = math.hypot(a.w_uncertainty or 0.0, b.w_uncertainty or 0.0)
    drh = (a.air.rh - b.air.rh) * 100
    reliable = abs(dw) > unc and not (a.colocated or b.colocated)
    if a.colocated or b.colocated:
        reason = "SENSOR_COMPARISON_ACTIVE"
    elif not reliable and abs(drh) >= 2 * max(a.rh_uncertainty_pct, b.rh_uncertainty_pct):
        reason = "RH_DIFFERENCE_FROM_TEMPERATURE"  # RH differs but moisture content does not, beyond uncertainty
    elif not reliable:
        reason = "GRADIENT_WITHIN_SENSOR_UNCERTAINTY"
    else:
        reason = "ZONE_MOISTURE_GRADIENT"
    return {
        "a": a.id, "b": b.id, "available": True, "reliable": reliable, "reason": reason,
        "dw_g_kg": dw * 1000, "uncertainty_g_kg": unc * 1000,
        "d_dew_point_f": delta_c_to_f(a.air.dew_point_c - b.air.dew_point_c),
        "d_rh_pct": drh, "d_t_f": delta_c_to_f(a.air.t_c - b.air.t_c),
        "wetter": a.id if dw > 0 else b.id,
    }


def summary(zs: dict[str, ZoneStatus], config: AppConfig) -> dict:
    """Wettest zone (by RH and by moisture content) and the largest measured gradient."""
    ok = [z for z in zs.values() if z.ok]
    if not ok:
        return {"wettest_rh": None, "wettest_w": None, "largest_gradient": None}
    by_rh = max(ok, key=lambda z: z.air.rh)
    by_w = max(ok, key=lambda z: z.air.w)
    best = None
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            g = gradient(a, b, config)
            if g["available"] and (best is None or abs(g["dw_g_kg"]) > abs(best["dw_g_kg"])):
                best = g
    return {"wettest_rh": by_rh.id, "wettest_rh_pct": by_rh.air.rh * 100,
            "wettest_w": by_w.id, "wettest_w_g_kg": by_w.air.w * 1000, "largest_gradient": best,
            "cooler_not_wetter": by_rh.id != by_w.id}


# ---------------------------------------------------------------- sensor comparison
def compare_sensors(samples: dict[str, list[tuple[float, float]]], rh_threshold_pct: float = 3.0,
                    dew_threshold_f: float = 1.5, reference: str | None = None) -> dict:
    """Side-by-side comparison of co-located T/RH sensors.

    samples: sensor_id -> [(t_c, rh_pct)] taken at the same moments. The reference is
    the chosen sensor, or the per-moment median of the group. Returns the mean
    difference of each sensor from the reference, its spread, and the offset that
    would remove it. A sensor whose dew point differs by more than ``dew_threshold_f``
    (or RH by more than ``rh_threshold_pct``) is flagged as disagreeing.
    """
    ids = [s for s, v in samples.items() if v]
    n = min((len(samples[s]) for s in ids), default=0)
    if len(ids) < 2 or n < 3:
        return {"ok": False, "note": "Need at least two sensors with three or more common readings", "sensors": {}}

    def med(xs):
        xs = sorted(xs)
        k = len(xs) // 2
        return xs[k] if len(xs) % 2 else 0.5 * (xs[k - 1] + xs[k])
    res = {}
    for sid in ids:
        dts, drhs, dtds = [], [], []
        for i in range(n):
            t, rh = samples[sid][i]
            if reference and reference in samples:
                rt, rrh = samples[reference][i]
            else:
                rt, rrh = med([samples[s][i][0] for s in ids]), med([samples[s][i][1] for s in ids])
            a = AirState.from_t_rh(t, max(0.01, min(rh, 100)) / 100)
            r = AirState.from_t_rh(rt, max(0.01, min(rrh, 100)) / 100)
            dts.append(t - rt)
            drhs.append(rh - rrh)
            dtds.append(a.dew_point_c - r.dew_point_c)
        mt, mrh, mtd = sum(dts) / n, sum(drhs) / n, sum(dtds) / n
        sd = math.sqrt(sum((x - mrh) ** 2 for x in drhs) / max(1, n - 1))
        res[sid] = {
            "mean_dt_f": delta_c_to_f(mt), "mean_drh_pct": mrh, "mean_d_dew_point_f": delta_c_to_f(mtd),
            "rh_spread_pct": sd, "suggested_temp_offset_f": round(-delta_c_to_f(mt), 1),
            "suggested_rh_offset_pct": round(-mrh, 1),
            "disagrees": abs(delta_c_to_f(mtd)) > dew_threshold_f or abs(mrh) > rh_threshold_pct,
            "stable": sd < 1.5,
        }
    return {"ok": True, "samples": n, "reference": reference or "median of all sensors", "sensors": res,
            "disagreement": any(v["disagrees"] for v in res.values())}


def f(t_c: float | None) -> float | None:
    return c_to_f(t_c) if t_c is not None else None
