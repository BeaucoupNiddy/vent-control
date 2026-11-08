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
