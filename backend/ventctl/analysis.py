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
from .engine import GroupState, decide, group_fans, profile_thresholds, ventilation_wets_surface
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
    "dewpoint": "Simple dew-point control",
    "optimized": "Moisture-optimized (original)",
    "tamo": "Moisture + temperature",
    "preserve": "Temperature preservation",
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
        from .simulation import strategy_config
        cfg = strategy_config(config, pid)
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
                want = decide(cond, state, cfg, group, ts, local_hour=lh, detail=False).target_on
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
                if surf is not None and ventilation_wets_surface(surf, ind.dew_point_c, out.dew_point_c, margin):
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


def shadow_agreement(rows: list[dict]) -> dict:
    """How often the policy in control and the shadow policy agreed, from recorded history."""
    hrs = _intervals(rows) if rows else []
    both = agree = on_ctrl = on_shadow = 0.0
    by_policy: dict[str, float] = {}
    for r, h in zip(rows, hrs):
        if r.get("shadow_on") is None:
            continue
        ctrl_on = (r.get("fans_on") or 0) > 0
        sh_on = bool(r["shadow_on"])
        both += h
        agree += h if ctrl_on == sh_on else 0
        on_ctrl += h if ctrl_on else 0
        on_shadow += h if sh_on else 0
        by_policy[r.get("policy") or "unknown"] = by_policy.get(r.get("policy") or "unknown", 0) + h
    return {"hours_compared": both, "agreement_pct": agree / both * 100 if both else None,
            "runtime_in_control_h": on_ctrl, "runtime_shadow_h": on_shadow, "hours_by_policy": by_policy}


# ---------------------------------------------------------------- Version 3: zones, surfaces, circulation
def zone_stats(rows: list[dict], config: AppConfig) -> dict:
    """Per-zone time above the circulation RH thresholds, mean/max RH and dew point, and per-probe
    condensation exposure (hours with the conservative margin below each class threshold)."""
    cs, ss = config.circulation, config.surfaces
    hrs = _intervals(rows)
    zs: dict[str, dict] = {}
    sf: dict[str, dict] = {}
    for r, h in zip(rows, hrs):
        for zid, z in (r.get("zones") or {}).items():
            if not isinstance(z, dict) or z.get("rh") is None or z.get("coloc"):
                continue
            rh = z["rh"] * 100 if z["rh"] <= 1.5 else z["rh"]
            d = zs.setdefault(zid, {"hours": 0.0, "rh_sum": 0.0, "rh_max": 0.0, "td_sum": 0.0, "td_n": 0,
                                    "above_elevated_h": 0.0, "above_high_h": 0.0, "above_critical_h": 0.0})
            d["hours"] += h
            d["rh_sum"] += rh * h
            d["rh_max"] = max(d["rh_max"], rh)
            if z.get("td_c") is not None:
                d["td_sum"] += z["td_c"] * h
                d["td_n"] += h
            for k in ("elevated", "high", "critical"):
                if rh >= getattr(cs, f"{k}_rh_pct"):
                    d[f"above_{k}_h"] += h
        for sid, s in (r.get("surfaces") or {}).items():
            if not isinstance(s, dict) or s.get("cons_margin_f") is None:
                continue
            m = s["cons_margin_f"]
            d = sf.setdefault(sid, {"hours": 0.0, "below_monitor_h": 0.0, "below_elevated_h": 0.0,
                                    "possible_h": 0.0, "surface_rh_elevated_h": 0.0, "min_margin_f": None,
                                    "zone": s.get("zone")})
            d["hours"] += h
            d["below_monitor_h"] += h if m < ss.monitor_margin_f else 0
            d["below_elevated_h"] += h if m < ss.elevated_margin_f else 0
            d["possible_h"] += h if m <= ss.possible_margin_f else 0
            if s.get("srh") is not None and s["srh"] * 100 >= ss.surface_rh_elevated_pct:
                d["surface_rh_elevated_h"] += h
            mm = s.get("margin_f")
            if mm is not None:
                d["min_margin_f"] = mm if d["min_margin_f"] is None else min(d["min_margin_f"], mm)
    zones_out = {}
    for zid, d in zs.items():
        t = d["hours"] or 1e-9
        zones_out[zid] = {"name": config.zone_name(zid), "hours": d["hours"], "mean_rh": d["rh_sum"] / t,
                          "max_rh": d["rh_max"], "mean_dew_point_c": d["td_sum"] / d["td_n"] if d["td_n"] else None,
                          "above_elevated_h": d["above_elevated_h"], "above_high_h": d["above_high_h"],
                          "above_critical_h": d["above_critical_h"]}
    names = {m.sensor_id: m.name for m in config.sensors.mappings}
    return {"zones": zones_out,
            "surfaces": {sid: {**d, "name": names.get(sid, sid)} for sid, d in sf.items()},
            "label": "Measured. Surface exposure uses the conservative margin (margin minus sensor uncertainty); it "
                     "describes conditions where condensation is possible, not observed water."}


MIN_EFFECT_SAMPLES = 30
MIN_EFFECT_CYCLES = 3


def _gradient_rates(rows: list[dict], za: str, zb: str):
    """(fan rows state, exterior on, d|dW|/dt in g/kg per hour) for consecutive row pairs."""
    out = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        dt = (datetime.fromisoformat(b["ts"]) - datetime.fromisoformat(a["ts"])).total_seconds() / 3600
        if not (0 < dt <= 0.25):
            continue
        za0, zb0 = (a.get("zones") or {}).get(za), (a.get("zones") or {}).get(zb)
        za1, zb1 = (b.get("zones") or {}).get(za), (b.get("zones") or {}).get(zb)
        if not all(isinstance(x, dict) and x.get("w") is not None and not x.get("coloc") for x in (za0, zb0, za1, zb1)):
            continue
        g0 = abs(za0["w"] - zb0["w"]) * 1000
        g1 = abs(za1["w"] - zb1["w"]) * 1000
        out.append((a, g0, (g1 - g0) / dt))
    return out


def _mean_se(xs: list[float]) -> tuple[float, float]:
    import math
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, float("inf")
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, sd / math.sqrt(n)


def circulation_effectiveness(rows: list[dict], cycles: list[dict], config: AppConfig) -> dict:
    """Informational: does each circulator measurably shrink the moisture gradient it serves?

    Compares the rate of change of |dW| between the connected zones while the fan runs against
    periods when it is off under the same exterior-ventilation state. Weather, the dehumidifier
    and moisture sources also move humidity, so results stay "insufficient data" until there
    are enough samples and cycles, and they never change fan behaviour automatically.
    """
    fans = []
    for c in config.circulators:
        conns = [k for k in config.connections if k.circulator_id == c.id]
        own = [x for x in cycles if x.get("circulator_id") == c.id]
        res_conns = []
        for k in conns:
            rates = _gradient_rates(rows, k.zone_a, k.zone_b)
            groups: dict[tuple[bool, bool], list[float]] = {}
            for r, g0, rate in rates:
                on = bool(((r.get("circ") or {}).get(c.id) or {}).get("on"))
                ext = (r.get("fans_on") or 0) > 0
                if on and g0 < 0.05:
                    continue  # nothing to mix
                groups.setdefault((on, ext), []).append(rate)
            comps = []
            for ext in (False, True):
                on_x, off_x = groups.get((True, ext), []), groups.get((False, ext), [])
                if len(on_x) >= 2 and len(off_x) >= 2:
                    mo, so = _mean_se(on_x)
                    mf, sf_ = _mean_se(off_x)
                    comps.append({"exterior_on": ext, "on_rate_g_kg_h": mo, "off_rate_g_kg_h": mf,
                                  "effect_g_kg_h": mo - mf, "se_g_kg_h": (so ** 2 + sf_ ** 2) ** 0.5,
                                  "on_samples": len(on_x), "off_samples": len(off_x)})
            n_on = sum(x["on_samples"] for x in comps)
            enough = n_on >= MIN_EFFECT_SAMPLES and len(own) >= MIN_EFFECT_CYCLES
            verdict = "insufficient_data"
            text = (f"Not enough data yet ({len(own)} recorded run(s), {n_on} fan-on samples with comparable fan-off "
                    f"periods; need {MIN_EFFECT_CYCLES} runs and {MIN_EFFECT_SAMPLES} samples).")
            if enough:
                sig = [x for x in comps if x["on_samples"] >= 10 and abs(x["effect_g_kg_h"]) > 2 * x["se_g_kg_h"]]
                helps = [x for x in sig if x["effect_g_kg_h"] < 0]
                hurts = [x for x in sig if x["effect_g_kg_h"] > 0]
                za, zb = config.zone_name(k.zone_a), config.zone_name(k.zone_b)
                if helps and not hurts:
                    only = {x["exterior_on"] for x in helps}
                    verdict = "reduces_gradient"
                    when = (" only while exterior ventilation runs" if only == {True} else
                            " when exterior ventilation is off" if only == {False} else "")
                    text = f"{c.name} reliably reduces the moisture difference between {za} and {zb}{when}."
                elif hurts and not helps:
                    verdict = "increases_gradient"
                    text = (f"While {c.name} runs, the moisture difference between {za} and {zb} grows faster than "
                            "when it is off. Check its placement and direction.")
                else:
                    verdict = "no_measurable_benefit"
                    text = f"{c.name} has not shown a measurable benefit under current conditions."
            res_conns.append({"connection": k.id, "zone_a": k.zone_a, "zone_b": k.zone_b, "verdict": verdict,
                              "text": text, "comparisons": comps})
        # surface margins over recorded runs
        worse = []
        for x in own:
            ctx = x.get("context") or {}
            s0, s1 = ctx.get("surface_margins_start_f") or {}, ctx.get("surface_margins_end_f") or {}
            for sid, m0 in s0.items():
                m1 = s1.get(sid)
                if m0 is not None and m1 is not None and m1 < m0 - 0.5:
                    worse.append({"ts": x.get("ts_start"), "sensor_id": sid, "start_f": m0, "end_f": m1})
        surface_note = None
        if worse:
            surface_note = (f"During {len(worse)} of {len(own)} runs the condensation margin at a monitored surface "
                            "fell by more than 0.5°F. Weather may explain some of this; watch for a pattern.")
        fans.append({"id": c.id, "name": c.name, "runs": len(own), "connections": res_conns,
                     "surface_margin_drops": worse[:20], "surface_note": surface_note,
                     "runs_detail": own[:20]})
    return {"fans": fans,
            "label": "Informational only. Compares fan-on with fan-off periods under similar exterior ventilation; "
                     "weather, the dehumidifier and moisture sources also change humidity. Fan behaviour is never "
                     "changed automatically from these results."}
