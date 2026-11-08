"""Statistics over recorded history, and historical replay.

Labels matter here:
  * "measured"  values come straight from sensors / plugs
  * "modeled"   values are estimates (moisture transfer, conditioned-air cost)
  * Level 1 replay re-runs control policies over *recorded* conditions. It shows
    how each policy would have switched and what transfer opportunity existed,
    conditional on the recorded indoor air. It is NOT what the basement
    humidity would have been.
  * Level 2 (counterfactual) uses the dynamic moisture model and is labeled
    modeled.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .airflow import ventilation_effect
from .climate import ClimateProfile
from .config import AppConfig, EnergyProfile, Mode
from .engine import GroupState, decide, group_fans, profile_thresholds
from .psychro import AirState
from .sensors import Conditions
from .units import c_to_f, delta_f_to_c, grams_to_gallons

SAFETY_CODES = ("SENSOR", "SWITCH", "INTERLOCK", "CONDENSATION")


def _intervals(rows: list[dict]) -> list[float]:
    """Hours each row represents (until the next row, capped at 15 min to bridge gaps safely)."""
    out = []
    for i, r in enumerate(rows):
        if i + 1 < len(rows):
            dt = (datetime.fromisoformat(rows[i + 1]["ts"]) - datetime.fromisoformat(r["ts"])).total_seconds() / 3600
        else:
            dt = 0.0
        out.append(min(max(dt, 0.0), 0.25))
    return out


def stats(rows: list[dict], config: AppConfig) -> dict:
    hrs = _intervals(rows)
    runtime = cycles = removed_g = kwh = above60 = above70 = fav = total = 0.0
    cond_ft3 = penalty_kwh = dh_h = dh_kwh = 0.0
    dh_known = False
    prev_on = False
    min_rem, _ = profile_thresholds(config)
    for r, h in zip(rows, hrs):
        on = (r["fans_on"] or 0) > 0
        if on and not prev_on:
            cycles += 1
        prev_on = on
        total += h
        if on:
            runtime += h
            removed_g += (r["removal_g_h"] or 0) * h
        kwh += (r["power_w"] or 0) * h / 1000
        cond_ft3 += (r.get("cond_air_cfm") or 0) * h * 60
        penalty_kwh += (r.get("penalty_w") or 0) * h / 1000
        if r.get("dehum_on") is not None:
            dh_known = True
            dh_h += h if r["dehum_on"] else 0
            dh_kwh += (r.get("dehum_power_w") or 0) * h / 1000
        rh = r["indoor_rh"]
        if rh is not None:
            above60 += h if rh > config.control.upper_rh_pct else 0
            above70 += h if rh > config.control.high_rh_alert_pct else 0
        code = (r["reason_code"] or "").split(":")[-1]
        if (r["potential_removal_g_h"] or 0) >= min_rem and not code.startswith(SAFETY_CODES):
            fav += h
    return {
        "hours_covered": total,
        "fan_runtime_h": runtime,
        "switch_cycles": int(cycles),
        "net_vapor_removed_gal": grams_to_gallons(removed_g),
        "net_vapor_removed_label": "modeled",
        "electricity_kwh": kwh,
        "electricity_label": "measured" if rows and rows[-1]["source"] == "measured" else "mock",
        "electricity_cost": kwh * config.house.electricity_cost_per_kwh,
        "hours_above_upper": above60,
        "hours_above_high": above70,
        "favorable_pct": (fav / total * 100) if total else None,
        "favorable_label": "modeled",
        "conditioned_air_ft3": cond_ft3,
        "conditioned_air_kwh": penalty_kwh,
        "conditioned_air_cost": penalty_kwh * config.house.electricity_cost_per_kwh,
        "conditioned_air_label": "modeled (assumed upstairs fraction)",
        "dehumidifier": None if not dh_known else {
            "runtime_h": dh_h,
            "kwh": dh_kwh,
            "cost": dh_kwh * config.house.electricity_cost_per_kwh,
            "water_gal_estimate": dh_h * config.dehumidifier.removal_pints_per_day / 24 / 8,
            "label": "runtime and energy measured at the plug; water removed estimated from the rated capacity",
        },
    }


REPLAY_POLICIES = {
    "continuous": "Continuous (24/7)",
    "daytime": "Daytime only (8 AM-8 PM)",
    "nighttime": "Nighttime only (8 PM-8 AM)",
    "dewpoint": "Dew-point controlled",
    "optimized": "Efficiency optimized",
    "off": "Ventilation off",
}


def _row_air(r: dict, prefix: str) -> AirState | None:
    t, rh = r.get(f"{prefix}_t_c"), r.get(f"{prefix}_rh")
    if t is None or rh is None:
        return None
    return AirState.from_t_rh(t, max(0.001, min(rh, 100)) / 100)


def replay(rows: list[dict], config: AppConfig, tz) -> dict:
    """Level 1 weather-opportunity replay over recorded rows."""
    hrs = _intervals(rows)
    group = config.groups[0]
    fans = group_fans(config, group)
    results = []
    for pid, name in REPLAY_POLICIES.items():
        cfg = config.model_copy(deep=True)
        cfg.mode = Mode.SIMULATION
        if pid == "dewpoint":
            cfg.control.energy_profile = EnergyProfile.MAX_DRYING
        elif pid == "optimized" and cfg.control.energy_profile == EnergyProfile.MAX_DRYING:
            cfg.control.energy_profile = EnergyProfile.BALANCED
        state = GroupState()
        on_h = adding_h = risk_h = transfer_g = kwh = 0.0
        cycles = 0
        margin = delta_f_to_c(cfg.control.surface_margin_f)
        for r, h in zip(rows, hrs):
            ts = datetime.fromisoformat(r["ts"])
            local = ts.astimezone(tz)
            lh = local.hour + local.minute / 60
            ind, out = _row_air(r, "indoor"), _row_air(r, "outdoor")
            surf = r.get("surface_t_c")
            if pid == "continuous":
                want = True
            elif pid == "off":
                want = False
            elif pid == "daytime":
                want = 8 <= lh < 20
            elif pid == "nighttime":
                want = lh >= 20 or lh < 8
            else:
                cond = Conditions(ind, "recorded", None if ind else "missing", out, None if out else "missing",
                                  surf, r.get("surface_source"), "recorded", None, None)
                want = decide(cond, state, cfg, group, ts, local_hour=lh).target_on
            if want != state.is_on:
                state.is_on = want
                state.last_change = ts
                cycles += 1 if want else 0
            if state.is_on and ind and out:
                e = ventilation_effect(ind, out, fans, cfg.house)
                on_h += h
                transfer_g += e.net_removal_g_h * h
                kwh += e.total_power_w * h / 1000
                if e.net_removal_g_h < 0:
                    adding_h += h
                if surf is not None and surf - out.dew_point_c < margin:
                    risk_h += h
        results.append({
            "id": pid, "name": name, "runtime_h": on_h, "switch_cycles": cycles,
            "opportunity_transfer_gal": grams_to_gallons(transfer_g),
            "hours_adding_moisture": adding_h, "condensation_risk_hours": risk_h,
            "energy_kwh_incl_conditioned_air": kwh, "cost": kwh * config.house.electricity_cost_per_kwh,
        })
    return {
        "kind": "opportunity_replay",
        "measured_inputs": True,
        "counterfactual": False,
        "label": ("Level 1 replay: how each policy would have switched under the recorded conditions, and the "
                  "moisture-transfer opportunity given the recorded basement air. It does not show what basement "
                  "humidity would have been under a different policy."),
        "data_source": rows[0]["source"] if rows else None,
        "hours": sum(hrs),
        "results": results,
    }


def recorded_profile(rows: list[dict], name: str = "Recorded outdoor conditions") -> ClimateProfile:
    """Hourly-averaged outdoor conditions from history, for Level 2 counterfactual simulation."""
    buckets: dict[str, list[tuple[float, float]]] = {}
    for r in rows:
        if r.get("outdoor_t_c") is None or r.get("outdoor_td_c") is None:
            continue
        key = r["ts"][:13]
        buckets.setdefault(key, []).append((r["outdoor_t_c"], r["outdoor_td_c"]))
    keys = sorted(buckets)
    if len(keys) < 2:
        raise ValueError("need at least 2 hours of recorded outdoor data")
    temps = [c_to_f(sum(t for t, _ in buckets[k]) / len(buckets[k])) for k in keys]
    dps = [c_to_f(sum(d for _, d in buckets[k]) / len(buckets[k])) for k in keys]
    src = rows[0]["source"]
    return ClimateProfile("recorded", name, f"{len(keys)} hours from this app's {src} outdoor sensor history",
                          temps, dps, source="recorded",
                          notes=[f"From recorded {src} data. Counterfactual basement humidity is modeled."])


def range_for(key: str, now: datetime, start: str | None = None, end: str | None = None) -> tuple[datetime, datetime]:
    if key == "custom" and start and end:
        return datetime.fromisoformat(start), datetime.fromisoformat(end)
    days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365, "all": 3650}.get(key, 1)
    return now - timedelta(days=days), now
