"""Seasonal reports from recorded history, and annual performance estimates."""

from __future__ import annotations

import calendar
import math
from datetime import datetime

from . import analysis, simulation
from .climate import ClimateProfile
from .config import AppConfig

SEASONS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Spring", 4: "Spring", 5: "Spring",
           6: "Summer", 7: "Summer", 8: "Summer", 9: "Fall", 10: "Fall", 11: "Fall"}


def seasonal(rows: list[dict], config: AppConfig, tz) -> dict:
    """Per-month and per-season summaries of recorded history."""
    months: dict[str, list[dict]] = {}
    seasons: dict[str, list[dict]] = {}
    for r in rows:
        local = datetime.fromisoformat(r["ts"]).astimezone(tz)
        months.setdefault(local.strftime("%Y-%m"), []).append(r)
        seasons.setdefault(f"{SEASONS[local.month]} {local.year if local.month != 12 else local.year + 1}", []).append(r)

    def summarize(label: str, rs: list[dict]) -> dict:
        s = analysis.stats(rs, config)
        rh = [r["indoor_rh"] for r in rs if r.get("indoor_rh") is not None]
        tdi = [r["indoor_td_c"] for r in rs if r.get("indoor_td_c") is not None]
        tdo = [r["outdoor_td_c"] for r in rs if r.get("outdoor_td_c") is not None]
        return {"period": label, **s,
                "mean_rh": sum(rh) / len(rh) if rh else None,
                "max_rh": max(rh) if rh else None,
                "mean_indoor_dewpoint_c": sum(tdi) / len(tdi) if tdi else None,
                "mean_outdoor_dewpoint_c": sum(tdo) / len(tdo) if tdo else None}

    return {
        "kind": "seasonal_report",
        "data_source": rows[0]["source"] if rows else None,
        "months": [summarize(k, months[k]) for k in sorted(months)],
        "seasons": [summarize(k, seasons[k]) for k in sorted(seasons, key=lambda x: (x.split()[1], x))],
    }


# Synthetic, illustrative monthly climate for New Orleans: (mean temp F, daily swing +/- F, mean dew point F,
# basement temp F). Shaped to resemble typical conditions; NOT NOAA normals.
SYNTHETIC_MONTHS = {
    1: (53, 8, 42, 64), 2: (57, 8, 45, 65), 3: (63, 8, 52, 68), 4: (69, 8, 59, 72), 5: (76, 7, 66, 76),
    6: (81, 6.5, 72, 78), 7: (83, 6.5, 74, 79), 8: (83, 6.5, 74, 79), 9: (80, 6.5, 70, 78), 10: (71, 8, 60, 75),
    11: (62, 8, 51, 70), 12: (55, 8, 45, 66),
}


def synthetic_month(m: int) -> ClimateProfile:
    t, amp, td, bt = SYNTHETIC_MONTHS[m]
    temps = [round(t + amp * math.cos(2 * math.pi * (h - 15) / 24), 2) for h in range(24)]
    dps = [round(min(td - 1.0 * math.cos(2 * math.pi * (h - 15) / 24), temps[h]), 2) for h in range(24)]
    return ClimateProfile(f"syn_{m}", f"Synthetic {calendar.month_name[m]}", "synthetic monthly profile",
                          temps, dps, basement_temp_f=bt, surface_temp_f=bt - 3,
                          notes=["Synthetic illustrative profile. Not measured data, not NOAA normals."])


ANNUAL_STRATEGIES = ["off", "continuous", "optimized"]


def annual(config: AppConfig, base: simulation.SimParams, observed: dict[int, ClimateProfile] | None = None,
           days_per_month: int = 6, warmup_days: int = 2) -> dict:
    """Estimate a year by simulating a representative period for each month and scaling to the month length.

    ``observed`` maps month -> profile built from cached NOAA observations; other months use the
    synthetic monthly profiles.
    """
    observed = observed or {}
    months = []
    totals = {s: {"fan_runtime_h": 0.0, "fan_kwh": 0.0, "conditioned_air_kwh": 0.0, "total_cost": 0.0,
                  "fan_net_removed_gal": 0.0, "hours_above_60": 0.0, "hours_above_70": 0.0,
                  "condensation_risk_hours": 0.0} for s in ANNUAL_STRATEGIES}
    sources = set()
    for m in range(1, 13):
        prof = observed.get(m) or synthetic_month(m)
        sources.add(prof.source)
        bt = prof.basement_temp_f
        params = base.model_copy(update={
            "duration_h": 24 * (days_per_month + warmup_days), "step_s": 300, "output_every_min": 60,
            "initial_temp_f": bt, "basement_mean_temp_f": bt, "surface_temp_f": prof.surface_temp_f,
            "initial_rh_pct": 70,
        })
        scale = calendar.monthrange(2026, m)[1] / days_per_month
        row = {"month": m, "name": calendar.month_abbr[m], "profile": prof.name, "source": prof.source, "results": {}}
        for s in ANNUAL_STRATEGIES:
            r = simulation.run_strategy(s, params, prof, config)
            # discard warm-up: use only the series after warmup for RH, summary totals scaled by fraction
            series = [p for p in r.series if p["h"] >= warmup_days * 24]
            frac = days_per_month / (days_per_month + warmup_days)
            sm = r.summary
            out = {
                "mean_rh": sum(p["rh"] for p in series) / len(series) if series else sm["mean_rh"],
                "fan_runtime_h": sm["fan_runtime_h"] * frac * scale,
                "fan_kwh": sm["fan_kwh"] * frac * scale,
                "conditioned_air_kwh": sm["conditioned_air_kwh"] * frac * scale,
                "total_cost": sm["total_cost"] * frac * scale,
                "fan_net_removed_gal": sm["fan_net_removed_gal"] * frac * scale,
                "hours_above_60": sum(1 for p in series if p["rh"] > 60) * scale,
                "hours_above_70": sum(1 for p in series if p["rh"] > 70) * scale,
                "condensation_risk_hours": sm["condensation_risk_hours"] * frac * scale,
            }
            row["results"][s] = out
            for k in totals[s]:
                totals[s][k] += out[k]
        months.append(row)
    for s in ANNUAL_STRATEGIES:
        totals[s]["mean_rh"] = sum(r["results"][s]["mean_rh"] for r in months) / 12
    return {
        "kind": "annual_estimate",
        "label": ("Modeled annual estimate: each month is simulated for a representative period and scaled. "
                  "Months without cached NOAA observations use synthetic profiles, which have no weather "
                  "fronts, so real results will differ."),
        "sources": sorted(sources),
        "strategies": {s: simulation.STRATEGIES[s][0] for s in ANNUAL_STRATEGIES},
        "months": months,
        "totals": totals,
    }
