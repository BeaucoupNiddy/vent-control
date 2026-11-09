"""Model calibration from recorded history.

Fits a simple moisture balance to observed basement humidity changes:

    M * dW/dt = a * m_fans * fan_fraction * (W_out - W_in)    effective fan performance (a = 1: CFM estimate is right)
              + ach * M * (W_out - W_in)                         natural infiltration
              + g                                                baseline moisture gain (sources + materials)

on 30-minute blocks with ordinary least squares. Reports point estimates with
95% intervals. Intervals are approximate: consecutive blocks are correlated
and the materials buffer is folded into g, so treat them as optimistic.

Nothing here changes settings. Suggested values are shown to the user, who may
apply them explicitly. Safety limits are never touched.
"""

from __future__ import annotations

import math
from datetime import datetime

from . import psychro
from .config import AppConfig, FanType
from .units import M3_PER_FT3, cfm_to_m3s, grams_to_gallons

BLOCK_MIN = 30


def _solve(xtx: list[list[float]], xty: list[float]) -> tuple[list[float], list[list[float]]] | None:
    """Invert a small symmetric matrix (Gauss-Jordan) and return (beta, inverse)."""
    n = len(xtx)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(xtx)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-18:
            return None
        a[c], a[piv] = a[piv], a[c]
        p = a[c][c]
        a[c] = [v / p for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [vr - f * vc for vr, vc in zip(a[r], a[c])]
    inv = [row[n:] for row in a]
    beta = [sum(inv[i][j] * xty[j] for j in range(n)) for i in range(n)]
    return beta, inv


def blocks(rows: list[dict]) -> list[dict]:
    """Aggregate history rows into ~30 minute blocks with W at both ends."""
    out = []
    cur: list[dict] = []
    for r in rows:
        if r.get("indoor_w") is None or r.get("outdoor_w") is None or not r.get("fans_verified", 1):
            cur = []
            continue
        if cur:
            gap = (datetime.fromisoformat(r["ts"]) - datetime.fromisoformat(cur[-1]["ts"])).total_seconds()
            if gap > 900:
                cur = []
        cur.append(r)
        span = (datetime.fromisoformat(cur[-1]["ts"]) - datetime.fromisoformat(cur[0]["ts"])).total_seconds()
        if span >= BLOCK_MIN * 60:
            hrs = span / 3600
            seg = cur[:-1]
            out.append({
                "hours": hrs,
                "dw": cur[-1]["indoor_w"] - cur[0]["indoor_w"],
                "fan": sum(1 for x in seg if (x.get("fans_on") or 0) > 0) / len(seg),
                "dwo": sum(x["outdoor_w"] - x["indoor_w"] for x in seg) / len(seg),
                "dwo_fan": sum((x["outdoor_w"] - x["indoor_w"]) * (1 if (x.get("fans_on") or 0) > 0 else 0)
                               for x in seg) / len(seg),
                "t_c": sum(x["indoor_t_c"] for x in seg) / len(seg),
            })
            cur = [cur[-1]]
    return out


def calibrate(rows: list[dict], config: AppConfig) -> dict:
    h = config.house
    vol = h.basement_volume_ft3 * M3_PER_FT3
    fans = [f for f in config.fans if f.enabled]
    exhaust = sum(f.actual_cfm for f in fans if f.type == FanType.EXHAUST)
    intake = sum(f.actual_cfm for f in fans if f.type == FanType.INTAKE)
    q = cfm_to_m3s(max(exhaust, intake)) * 3600  # m3/h
    bl = blocks(rows)
    result: dict = {
        "kind": "calibration",
        "label": "Estimated from recorded data. Not applied automatically.",
        "blocks": len(bl), "block_minutes": BLOCK_MIN,
        "parameters": [], "limitations": [], "identifiable": False,
        "data_source": rows[0]["source"] if rows else None,
    }
    if rows and rows[0]["source"] == "mock":
        result["limitations"].append("This history is mock data, so the fit describes the simulator, not your basement.")
    if len(bl) < 24:
        result["limitations"].append(f"Only {len(bl)} usable 30-minute blocks; need at least 24 (12 hours), ideally days.")
        return result
    rho = psychro.dry_air_density(sum(b["t_c"] for b in bl) / len(bl), 0.015)
    m_air = vol * rho  # kg dry air in the basement
    m_fan = q * rho  # kg/h at the configured airflow

    X, y = [], []
    for b in bl:
        X.append([m_fan * b["dwo_fan"], m_air * b["dwo"], 1.0])
        y.append(m_air * b["dw"] / b["hours"])
    fan_frac = sum(b["fan"] for b in bl) / len(bl)
    cols = [0, 1, 2]
    if fan_frac < 0.05 or fan_frac > 0.95 or m_fan == 0:
        result["limitations"].append("Fans were almost always on or always off, so fan performance cannot be "
                                     "separated from natural leakage. Fan performance is not estimated.")
        cols = [1, 2]
    Xs = [[row[c] for c in cols] for row in X]
    p = len(cols)
    xtx = [[sum(r[i] * r[j] for r in Xs) for j in range(p)] for i in range(p)]
    xty = [sum(r[i] * yy for r, yy in zip(Xs, y)) for i in range(p)]
    sol = _solve(xtx, xty)
    if sol is None:
        result["limitations"].append("The data does not vary enough to fit the model (singular system).")
        return result
    beta, inv = sol
    n = len(y)
    resid = [yy - sum(b * x for b, x in zip(beta, r)) for r, yy in zip(Xs, y)]
    rss = sum(e * e for e in resid)
    ybar = sum(y) / n
    tss = sum((yy - ybar) ** 2 for yy in y) or 1e-12
    s2 = rss / max(n - p, 1)
    se = [math.sqrt(max(s2 * inv[i][i], 0)) for i in range(p)]
    result["r_squared"] = 1 - rss / tss
    result["identifiable"] = True

    def param(name, label, est, err, unit, scale=1.0, configured=None, lo_ok=None, hi_ok=None, suggest_key=None):
        e, lo, hi = est * scale, (est - 1.96 * err) * scale, (est + 1.96 * err) * scale
        reliable = err > 0 and abs(1.96 * err) < 0.5 * abs(est) and (lo_ok is None or lo_ok <= e <= hi_ok)
        result["parameters"].append({
            "id": name, "label": label, "estimate": e, "low": lo, "high": hi, "unit": unit,
            "configured": configured, "reliable": reliable, "suggest_key": suggest_key if reliable else None,
        })

    names = {0: "fan", 1: "ach", 2: "gain"}
    for k, c in enumerate(cols):
        est, err = beta[k], se[k]
        if names[c] == "fan":
            param("fan_cfm", "Effective total fan airflow", est, err, "CFM", scale=max(exhaust, intake),
                  configured=max(exhaust, intake), lo_ok=5, hi_ok=5000, suggest_key="fans.actual_cfm_total")
        elif names[c] == "ach":
            param("natural_ach", "Natural air exchange", est, err, "ACH", configured=h.natural_ach,
                  lo_ok=0, hi_ok=10, suggest_key="house.natural_ach")
        else:
            gal = grams_to_gallons(1000) * 24  # kg/h -> gal/day
            param("baseline_gain", "Baseline moisture gain (sources + materials)", est, err, "gal/day", scale=gal)
    if not any(pp["reliable"] for pp in result["parameters"]):
        result["limitations"].append("No parameter could be pinned down reliably yet; keep the configured "
                                     "assumptions and collect more varied data (fan on and off in different weather).")
    result["limitations"].append("Intervals assume independent 30-minute blocks; real errors are larger.")
    return result


# ---------------------------------------------------------------- thermal response learning
THERMAL_MODEL_VERSION = "two-node-1"
MIN_DRIVE_F = 8.0  # indoor/inlet difference needed for a fan cycle to be informative


def _cycle_starts(rows: list[dict]) -> list[int]:
    out = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if (a.get("fans_on") or 0) == 0 and (b.get("fans_on") or 0) > 0 and b.get("fans_verified", 1):
            out.append(i)
    return out


def thermal_cycles(rows: list[dict], horizon_min: float = 30) -> list[dict]:
    """Observed fan cycles: temperature at start, after the horizon, and after stopping."""
    from datetime import datetime as _dt
    cycles = []
    for i in _cycle_starts(rows):
        r0 = rows[i - 1]
        t_start = _dt.fromisoformat(rows[i]["ts"])
        if r0.get("indoor_t_c") is None or r0.get("outdoor_t_c") is None:
            continue
        j = i
        while j < len(rows) and (rows[j].get("fans_on") or 0) > 0:
            j += 1
        run_rows = rows[i:j]
        dur = (_dt.fromisoformat(run_rows[-1]["ts"]) - t_start).total_seconds() / 60 if run_rows else 0
        at = next((r for r in run_rows if (_dt.fromisoformat(r["ts"]) - t_start).total_seconds() / 60 >= horizon_min),
                  None)
        stop = None
        if j < len(rows) and run_rows:
            t_stop = _dt.fromisoformat(rows[j]["ts"])
            after = next((r for r in rows[j:] if (_dt.fromisoformat(r["ts"]) - t_stop).total_seconds() / 60 >= 30),
                         None)
            if after and after.get("indoor_t_c") is not None and run_rows[-1].get("indoor_t_c") is not None:
                stop = {"rebound_30min_f": (after["indoor_t_c"] - run_rows[-1]["indoor_t_c"]) * 1.8}
        rate = None
        if dur >= 60 and at and run_rows[-1].get("indoor_t_c") is not None:
            hrs = (_dt.fromisoformat(run_rows[-1]["ts"]) - _dt.fromisoformat(at["ts"])).total_seconds() / 3600
            if hrs > 0.25:
                rate = (run_rows[-1]["indoor_t_c"] - at["indoor_t_c"]) * 1.8 / hrs
        cycles.append({
            "ts": rows[i]["ts"], "start_t_c": r0["indoor_t_c"], "start_rh": r0.get("indoor_rh"),
            "outdoor_t_c": r0["outdoor_t_c"], "outdoor_rh": r0.get("outdoor_rh"), "duration_min": dur,
            "t_horizon_c": at["indoor_t_c"] if at else None,
            "minutes": (_dt.fromisoformat(at["ts"]) - t_start).total_seconds() / 60 if at else None,
            "running_rate_f_h": rate, **(stop or {}),
        })
    return cycles


def fit_thermal(rows: list[dict], config: AppConfig, horizon_min: float = 30) -> dict:
    """Fit the air-to-structure coupling H of the two-node model to observed fan starts.

    For each informative cycle, H is found so the shared coupled model reproduces the
    observed temperature change after ~30 minutes. The estimate is the geometric mean
    with a 95% interval; confidence depends on the number of cycles and the spread.
    """
    from . import coupled, thermal
    from .airflow import ventilation_effect
    from .engine import group_fans
    from .psychro import AirState
    from .units import f_to_c

    base = thermal.default_params(config.house.basement_volume_ft3, None, config.temperature.structure_heat_capacity_mj_k,
                                  config.temperature.structure_tau_h)
    fans = group_fans(config, config.groups[0]) if config.groups else []
    up = AirState.from_t_rh(f_to_c(config.house.upstairs_temp_f), config.house.upstairs_rh_pct / 100)
    cycles = thermal_cycles(rows, horizon_min)
    estimates = []
    used = []
    for c in cycles:
        if c["t_horizon_c"] is None or c["start_rh"] is None or c["outdoor_rh"] is None or not fans:
            continue
        ind = AirState.from_t_rh(c["start_t_c"], max(0.01, min(c["start_rh"], 100)) / 100)
        out = AirState.from_t_rh(c["outdoor_t_c"], max(0.01, min(c["outdoor_rh"], 100)) / 100)
        eff = ventilation_effect(ind, out, fans, config.house, up)
        m_out, m_up = coupled.fan_flows(eff)
        bd = coupled.Boundary(out, up, m_out, m_up)
        obs = c["t_horizon_c"] - c["start_t_c"]
        mins = int(round(c["minutes"]))

        def drop(h: float) -> float:
            p = thermal.ThermalParams(base.air_heat_capacity_j_k, h, base.structure_heat_capacity_j_k,
                                      base.structure_tau_h)
            mp = coupled.ModelParams.from_config(config, p)
            # same start-state estimate as the live engine (fans were off: quasi-steady with infiltration)
            g_off, src_off = coupled.inlet_conductance(mp, coupled.Boundary(out, up))
            st = coupled.initial_state(mp, ind, g_off, src_off)
            return coupled.predict(mp, st, bd, [mins]).t_c[0] - ind.t_c

        _, src = coupled.inlet_conductance(coupled.ModelParams.from_config(config, base), bd)
        if abs(src - ind.t_c) * 1.8 < MIN_DRIVE_F or obs * (src - ind.t_c) <= 0:
            continue
        lo, hi = 100.0, 50000.0  # predicted change shrinks as H grows
        d_lo, d_hi = drop(lo), drop(hi)
        if not (min(d_lo, d_hi) <= obs <= max(d_lo, d_hi)):
            continue
        for _ in range(40):
            mid = math.sqrt(lo * hi)
            if (drop(mid) - obs) * (d_lo - obs) > 0:
                lo = mid
            else:
                hi = mid
        estimates.append(math.log(math.sqrt(lo * hi)))
        used.append(c)
    n = len(estimates)
    res: dict = {
        "kind": "thermal_calibration", "version": THERMAL_MODEL_VERSION, "cycles_found": len(cycles),
        "cycles_used": n, "assumed_coupling_w_k": base.coupling_w_k,
        "label": "Estimated from recorded fan cycles. Weather, sun and upstairs HVAC also change basement "
                 "temperature, so treat this as approximate.",
        "observed": {
            "running_rate_f_h": _mean([c["running_rate_f_h"] for c in cycles if c.get("running_rate_f_h") is not None]),
            "rebound_30min_f": _mean([c["rebound_30min_f"] for c in cycles if c.get("rebound_30min_f") is not None]),
        },
        "cycles": used[-20:],
    }
    if n == 0:
        res.update(confidence="none", coupling_w_k=None, uncertainty=None,
                   note=f"No informative fan cycles yet (need runs of ≥{horizon_min:.0f} min with ≥{MIN_DRIVE_F:.0f}°F "
                        "between basement and incoming air).")
        return res
    mu = sum(estimates) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in estimates) / (n - 1)) if n > 1 else 1.0
    half = 1.96 * sd / math.sqrt(n)
    unc = math.exp(half) - 1
    conf = "high" if n >= 8 and unc <= 0.25 else "medium" if n >= 3 and unc <= 0.5 else "low"
    res.update(coupling_w_k=math.exp(mu), low_w_k=math.exp(mu - half), high_w_k=math.exp(mu + half),
               uncertainty=unc, confidence=conf)
    return res


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None
