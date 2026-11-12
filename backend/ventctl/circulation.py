"""Internal circulation fans: a decision engine separate from, but coordinated with,
the exterior ventilation engine.

Internal fans move air between parts of the basement. They do not remove water
from the building, so nothing here is ever reported as moisture removal. They can
even raise indoor vapor for a while by speeding evaporation from damp surfaces.

For each fan and each zone connection it serves, the engine predicts what mixing
would do over the horizon with a simple two-box exchange model:

    dry-air exchange Q (assumed; the same mass goes each way, so water and dry air
    are conserved), zone dry-air masses m_a, m_b

    W_a(t) = W_mix + (W_a - W_mix) e^(-k t),  k = Q (1/m_a + 1/m_b)
    W_mix  = (m_a W_a + m_b W_b) / (m_a + m_b)

Temperature differences decay more slowly (k divided by the air-plus-contents heat
capacity factor), because walls and contents hold each zone near its own temperature.
RH and dew point are then derived from the predicted temperature and humidity
ratio, the same way as everywhere else in the app.

The exchange flow is an assumption until fan cycles have been observed: the graph
describes intended circulation, not proven airflow. Results are labeled that way and
the engine says "benefit uncertain" instead of inventing a precise score.

Priority order (spec 16): electrical/equipment safety, manual override, condensation
restrictions, localized moisture urgency, beneficial zone mixing, exterior-ventilation
support, scheduled mixing, energy optimization.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import psychro
from .config import AppConfig, Circulator, ScheduleWindow, ZoneConnection
from .psychro import AirState
from .thermal import AIR_CONTENTS_FACTOR
from .units import M3_PER_FT3, c_to_f, delta_c_to_f

SAFETY_CODES = {"WATER_INTRUSION_LOCKOUT", "PLUG_UNREACHABLE", "PLUG_UNVERIFIED", "COMMAND_LOCKOUT", "DISABLED"}


@dataclass
class CircState:
    is_on: bool = False
    verified: bool = True
    last_change: datetime | None = None
    manual: bool | None = None  # True = manual ON, False = manual OFF, None = automatic
    manual_until: datetime | None = None
    post_vent_until: datetime | None = None
    last_reason: str | None = None
    manual_ended: bool = False  # a manual override just ended; return the fan to OFF unless automation runs it


@dataclass
class CircCheck:
    id: str
    label: str
    ok: bool | None
    detail: str


@dataclass
class CircDecision:
    circulator_id: str
    target_on: bool
    reason_code: str
    explanation: str
    benefit: str  # beneficial | uncertain | adverse | none
    safety: bool = False
    held: bool = False
    commanding: bool = False  # whether this decision may switch hardware (auto mode + commissioned)
    reasons: list[str] = field(default_factory=list)
    checks: list[CircCheck] = field(default_factory=list)
    predictions: list[dict] = field(default_factory=list)
    counts_as_moisture_removal: bool = False  # always False: circulation moves water, it does not remove it
    next_eligible_switch: datetime | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["next_eligible_switch"] = self.next_eligible_switch.isoformat() if self.next_eligible_switch else None
        return d


# ---------------------------------------------------------------- physics
def zone_dry_air_kg(air: AirState, volume_ft3: float) -> float:
    return volume_ft3 * M3_PER_FT3 * psychro.dry_air_density(air.t_c, air.w, air.p_kpa)


def exchange_kg_s(cfm: float, a: AirState, b: AirState) -> float:
    rho = 0.5 * (psychro.dry_air_density(a.t_c, a.w, a.p_kpa) + psychro.dry_air_density(b.t_c, b.w, b.p_kpa))
    return cfm * M3_PER_FT3 / 60 * rho


def mix(a: AirState, va_ft3: float, b: AirState, vb_ft3: float, cfm: float, minutes: float
        ) -> tuple[AirState, AirState]:
    """Two-box exchange over `minutes`. Conserves dry air and water (sum of m*W is unchanged)."""
    if cfm <= 0 or minutes <= 0:
        return a, b
    ma, mb = zone_dry_air_kg(a, va_ft3), zone_dry_air_kg(b, vb_ft3)
    ta, wa, tb, wb = mix_masses(a.t_c, a.w, ma, b.t_c, b.w, mb, exchange_kg_s(cfm, a, b), minutes * 60)
    # never above saturation: excess would condense (not expected from mixing two unsaturated states)
    wa = min(wa, psychro.humidity_ratio(ta, 1.0, a.p_kpa))
    wb = min(wb, psychro.humidity_ratio(tb, 1.0, b.p_kpa))
    return AirState.from_t_w(ta, wa, a.p_kpa), AirState.from_t_w(tb, wb, b.p_kpa)


def mix_masses(ta: float, wa: float, ma: float, tb: float, wb: float, mb: float, q_kg_s: float, t_s: float,
               thermal_factor: float = AIR_CONTENTS_FACTOR) -> tuple[float, float, float, float]:
    """Exact two-box exchange of dry-air mass flow q both ways for t_s seconds.

    Water is conserved exactly: ma*wa + mb*wb is unchanged. Temperature differences decay
    `thermal_factor` times more slowly (air plus contents and surface layers)."""
    if q_kg_s <= 0 or t_s <= 0:
        return ta, wa, tb, wb
    k = q_kg_s * (1 / ma + 1 / mb)
    e_w = math.exp(-k * t_s)
    e_t = math.exp(-k * t_s / thermal_factor)
    w_mix = (ma * wa + mb * wb) / (ma + mb)
    t_mix = (ma * ta + mb * tb) / (ma + mb)
    return (t_mix + (ta - t_mix) * e_t, w_mix + (wa - w_mix) * e_w,
            t_mix + (tb - t_mix) * e_t, w_mix + (wb - w_mix) * e_w)


def predict_connection(conn: ZoneConnection, za, zb, minutes: float) -> dict | None:
    """Predicted states of both zones with the fan ON vs OFF (natural exchange only)."""
    if za.air is None or zb.air is None:
        return None
    on_a, on_b = mix(za.air, za.volume_ft3, zb.air, zb.volume_ft3, conn.natural_cfm + conn.fan_exchange_cfm, minutes)
    off_a, off_b = mix(za.air, za.volume_ft3, zb.air, zb.volume_ft3, conn.natural_cfm, minutes)

    def st(x: AirState) -> dict:
        return {"t_f": c_to_f(x.t_c), "rh_pct": x.rh * 100, "dew_point_f": c_to_f(x.dew_point_c), "w_g_kg": x.w * 1000}
    return {"connection": conn.id, "zone_a": za.id, "zone_b": zb.id, "minutes": minutes,
            "exchange_cfm_on": conn.natural_cfm + conn.fan_exchange_cfm, "exchange_cfm_off": conn.natural_cfm,
            "a": {"now": st(za.air), "on": st(on_a), "off": st(off_a)},
            "b": {"now": st(zb.air), "on": st(on_b), "off": st(off_b)},
            "_air": {"a_on": on_a, "a_off": off_a, "b_on": on_b, "b_off": off_b},
            "label": "modeled from an assumed exchange flow"}


def _in_windows(windows: list[ScheduleWindow], hour: float) -> bool:
    return not windows or any(w.contains(hour) for w in windows)


# ---------------------------------------------------------------- decision
def decide_circulator(
    circ: Circulator, config: AppConfig, zones: dict, surfaces: list, state: CircState, now: datetime,
    exterior: dict | None = None, dehumidifier_running: bool | None = None, local_hour: float | None = None,
    commissioned_override: bool | None = None,
) -> CircDecision:
    cs = config.circulation
    ss = config.surfaces
    exterior = exterior or {}
    hz = cs.horizon_min
    checks: list[CircCheck] = []
    reasons: list[str] = []
    conns = [k for k in config.connections if k.circulator_id == circ.id]
    commissioned = circ.commissioned if commissioned_override is None else commissioned_override
    commanding = cs.mode == "auto" and circ.auto_enabled and commissioned

    def out(on: bool, code: str, text: str, benefit: str = "none", safety: bool = False, immediate: bool = False,
            preds: list | None = None) -> CircDecision:
        d = CircDecision(circ.id, on, code, text, benefit, safety=safety, commanding=commanding,
                         reasons=[code] + [r for r in reasons if r != code], checks=checks,
                         predictions=[{k: v for k, v in p.items() if k != "_air"} for p in (preds or [])])
        # minimum on/off times (safety and manual changes are immediate; a scheduled mixing run
        # ends on its own schedule)
        if state.last_reason == "SCHEDULED_MIXING" and not on:
            immediate = True
        if not (safety or immediate) and state.last_change is not None and on != state.is_on:
            need = cs.min_on_minutes if state.is_on else cs.min_off_minutes
            elig = state.last_change + timedelta(minutes=need)
            if now < elig:
                d.held = True
                d.target_on = state.is_on
                d.next_eligible_switch = elig
                d.reasons.insert(0, "MINIMUM_RUNTIME_ACTIVE" if state.is_on else "MINIMUM_OFF_TIME_ACTIVE")
                d.explanation = (f"Holding {'ON' if state.is_on else 'OFF'} until {elig.strftime('%H:%M')} "
                                 f"(minimum {'run' if state.is_on else 'off'} time). Pending: {text}")
        return d

    # 1. electrical and equipment safety
    if not circ.enabled:
        return out(False, "DISABLED", "Fan disabled in settings.", safety=True)
    if config.control.water_intrusion_lockout:
        checks.append(CircCheck("water", "Water-intrusion lockout", False, "Active"))
        return out(False, "WATER_INTRUSION_LOCKOUT", "OFF: water-intrusion lockout is active. Do not energize fans "
                   "or plugs that may be near water.", safety=True)
    if not state.verified:
        checks.append(CircCheck("plug", "Smart plug", False, "State not confirmed"))
        return out(False, "PLUG_UNVERIFIED", "OFF requested: the smart plug state cannot be confirmed, so the actual "
                   "fan state is uncertain.", safety=True)
    checks.append(CircCheck("plug", "Smart plug", True, "Responding"))

    # 2. manual override (still below electrical safety)
    if state.manual is not None and (state.manual_until is None or now < state.manual_until):
        until = f" until {state.manual_until.strftime('%H:%M')}" if state.manual_until else ""
        reasons.append("MANUAL_OVERRIDE")
        return out(state.manual, "MANUAL_ON" if state.manual else "MANUAL_OFF",
                   f"{'ON' if state.manual else 'OFF'}: manual override{until}.", immediate=True)
    if cs.mode == "off":
        return out(False, "CIRCULATION_AUTOMATION_OFF", "OFF: circulation automation is turned off.", immediate=True)

    if not conns:
        checks.append(CircCheck("graph", "Zone connection", None, "Not assigned to a connection"))

    # predictions for every connection this fan serves
    preds = []
    unavailable = []
    for k in conns:
        za, zb = zones.get(k.zone_a), zones.get(k.zone_b)
        if za is None or zb is None or not za.ok or not zb.ok:
            unavailable.append(k)
            continue
        preds.append(predict_connection(k, za, zb, hz))

    # 3. condensation restrictions: would mixing raise the dew point at a monitored surface?
    adverse_surface = []
    protects_surface = []
    unknown_surface = []
    if cs.condensation_aware:
        for s in surfaces:
            if s.t_c is None or s.zone is None:
                continue
            for p in preds:
                side = "a" if s.zone == p["zone_a"] else "b" if s.zone == p["zone_b"] else None
                if side is None:
                    continue
                on_air, off_air = p["_air"][f"{side}_on"], p["_air"][f"{side}_off"]
                m_on = delta_c_to_f(s.t_c - on_air.dew_point_c) - s.uncertainty_f
                m_off = delta_c_to_f(s.t_c - off_air.dew_point_c) - s.uncertainty_f
                p.setdefault("surfaces", []).append({"sensor_id": s.sensor_id, "name": s.name,
                                                     "conservative_margin_on_f": m_on,
                                                     "conservative_margin_off_f": m_off})
                if m_on < m_off - 0.2 and m_on < ss.elevated_margin_f:
                    adverse_surface.append((s, m_on, m_off))
                elif m_on > m_off + 0.2 and s.risk in ("monitor", "elevated", "possible"):
                    protects_surface.append((s, m_on, m_off))
            for k in unavailable:
                if s.zone in (k.zone_a, k.zone_b) and s.risk in ("elevated", "possible"):
                    unknown_surface.append(s)
    if adverse_surface:
        s, m_on, m_off = min(adverse_surface, key=lambda x: x[1])
        checks.append(CircCheck("surface", "Surface condensation", False,
                                f"Mixing would lower the conservative margin at {s.name} from {m_off:.1f}°F to "
                                f"{m_on:.1f}°F"))
        reasons.append("CIRCULATION_WORSENS_SURFACE_RISK")
        return out(False, "CIRCULATION_WORSENS_SURFACE_RISK",
                   f"OFF: moving air through this connection would carry higher-dew-point air toward {s.name} "
                   f"(predicted conservative margin {m_on:.1f}°F vs {m_off:.1f}°F with the fan off).",
                   "adverse", preds=preds)
    if unknown_surface:
        s = unknown_surface[0]
        checks.append(CircCheck("surface", "Surface condensation", None,
                                f"{s.name} is at {s.risk} risk and a zone sensor is unavailable"))
        return out(False, "SURFACE_RISK_EFFECT_UNKNOWN",
                   f"OFF: {s.name} is at elevated condensation risk and a zone reading is missing, so the effect of "
                   "circulation cannot be judged. Warning only.", "uncertain", preds=preds)
    checks.append(CircCheck("surface", "Surface condensation", True if preds else None,
                            "No monitored surface would be pushed toward condensation" if preds else
                            "Not evaluated"))

    # 4/5. localized humidity and beneficial mixing
    beneficial = []
    notes = []
    for p in preds:
        za, zb = zones[p["zone_a"]], zones[p["zone_b"]]
        target, other = (za, zb) if za.air.rh >= zb.air.rh else (zb, za)
        ts, os_ = ("a", "b") if target is za else ("b", "a")
        rh_on, rh_off = p[ts]["on"]["rh_pct"], p[ts]["off"]["rh_pct"]
        orh_on, orh_off = p[os_]["on"]["rh_pct"], p[os_]["off"]["rh_pct"]
        drop = rh_off - rh_on
        rise = orh_on - orh_off
        from .zones import gradient
        g = gradient(target, other, config)
        p["gradient"] = g
        p["target"] = target.id
        mins = target.minutes_above or {}
        persistent = ((mins.get("critical") or 0) >= cs.critical_persist_min or
                      (mins.get("high") or 0) >= cs.high_persist_min or
                      (mins.get("elevated") or 0) >= cs.elevated_persist_min)
        dw_need = cs.min_dw_stop_g_kg if state.is_on else cs.min_dw_start_g_kg
        grad_trigger = (g["reliable"] and g["dw_g_kg"] >= dw_need and target.air.rh * 100 >= cs.observe_rh_pct)
        improves = drop >= cs.min_rh_improvement_pct and (orh_on < cs.elevated_rh_pct or rise < 0.5 * drop)
        p["rh_drop_target_pct"], p["rh_rise_other_pct"] = drop, rise
        if not (persistent or grad_trigger):
            continue
        if not improves:
            notes.append(f"{target.name}: mixing predicted to change RH by {-drop:+.1f}% "
                         f"({other.name} {rise:+.1f}%), not a clear improvement")
            if g["reason"] in ("GRADIENT_WITHIN_SENSOR_UNCERTAINTY", "SENSOR_COMPARISON_ACTIVE"):
                reasons.append(g["reason"])
            continue
        if g["reliable"] and g["dw_g_kg"] > 0:
            kind = "ZONE_MOISTURE_GRADIENT"
            why = (f"{target.name} holds {g['dw_g_kg']:.2f} g/kg more water than {other.name} "
                   f"(beyond the ±{g['uncertainty_g_kg']:.2f} sensor uncertainty)")
        else:
            kind = "COOL_ZONE_WARMING"
            why = (f"{target.name} has higher RH mainly because it is cooler; its moisture content is about the same as "
                   f"{other.name}, so mixing helps by warming it, not by removing water")
            reasons.append(g["reason"] if g["reason"] != "ZONE_MOISTURE_GRADIENT" else "RH_DIFFERENCE_FROM_TEMPERATURE")
        level = "LOCAL_HUMIDITY_HIGH" if persistent else "BENEFICIAL_ZONE_MIXING"
        beneficial.append((p, target, other, drop, rise, kind, why, level))

    removal_path = bool(exterior.get("drying")) or bool(dehumidifier_running)
    all_humid = all(z.air is not None and z.air.rh * 100 >= cs.elevated_rh_pct for z in zones.values() if z.ok) and \
        any(z.ok for z in zones.values())
    if all_humid and not removal_path:
        reasons.append("NO_MOISTURE_REMOVAL_PATH")
    no_removal_txt = (" Every zone is humid and nothing is removing water right now (no useful exterior ventilation "
                      "or dehumidifier), so circulation only redistributes it." if all_humid and not removal_path
                      else " Circulation moves water between zones; it does not remove it.")

    if protects_surface:
        s, m_on, m_off = max(protects_surface, key=lambda x: x[1] - x[2])
        reasons.append("SURFACE_PROTECTION")
        return out(True, "SURFACE_PROTECTION",
                   f"ON: mixing should bring lower-dew-point air to {s.name} (predicted conservative margin "
                   f"{m_on:.1f}°F vs {m_off:.1f}°F with the fan off).{no_removal_txt}", "beneficial", preds=preds)

    if beneficial:
        p, target, other, drop, rise, kind, why, level = max(beneficial, key=lambda x: x[3])
        reasons.append(kind)
        txt = (f"ON: {why}. Predicted {target.name} RH in {hz:.0f} min: {p['a' if target.id == p['zone_a'] else 'b']['on']['rh_pct']:.0f}% "
               f"with the fan vs {p['a' if target.id == p['zone_a'] else 'b']['off']['rh_pct']:.0f}% without "
               f"({other.name} {rise:+.1f}%).{no_removal_txt}")
        checks.append(CircCheck("benefit", "Expected benefit", True, f"{target.name} RH −{drop:.1f}% (modeled)"))
        return out(True, level, txt, "beneficial", preds=preds)

    # 6. exterior-ventilation support
    ext_on = bool(exterior.get("on"))
    if cs.run_with_exhaust:
        if exterior.get("auto_on"):
            state.post_vent_until = now + timedelta(minutes=cs.post_ventilation_min)
            reasons.append("RUN_WITH_EXHAUST")
            return out(True, "RUN_WITH_EXHAUST",
                       "ON: the exhaust fans are bringing in drier outside air, so this fan stirs the basement to "
                       "dry damp corners and surfaces into the air being exhausted.", "beneficial", preds=preds)
        if state.post_vent_until and now < state.post_vent_until and state.last_reason in (
                "RUN_WITH_EXHAUST", "POST_EXHAUST_RUN"):
            return out(True, "POST_EXHAUST_RUN",
                       f"ON: finishing a short run after the exhaust fans stopped (until "
                       f"{state.post_vent_until.strftime('%H:%M')}).", "uncertain", preds=preds)
    if circ.ventilation_support and cs.ventilation_support:
        path_ok = any((zones.get(k.zone_a) and zones[k.zone_a].on_ventilation_path) !=
                      (zones.get(k.zone_b) and zones[k.zone_b].on_ventilation_path) for k in conns)
        both_on_path = any((zones.get(k.zone_a) and zones[k.zone_a].on_ventilation_path) and
                           (zones.get(k.zone_b) and zones[k.zone_b].on_ventilation_path) for k in conns)
        if ext_on and exterior.get("drying", True):
            if both_on_path and not path_ok:
                reasons.append("VENTILATION_SHORT_CIRCUIT_RISK")
                checks.append(CircCheck("vent", "Ventilation support", False,
                                        "Both zones are on the exterior air path; may short-circuit fresh air"))
            elif path_ok:
                state.post_vent_until = now + timedelta(minutes=cs.post_ventilation_min)
                return out(True, "VENTILATION_SUPPORT",
                           "ON: exterior ventilation is drying the basement; this fan helps carry that air into a "
                           "zone off the main air path.", "uncertain" if not preds else "beneficial", preds=preds)
        elif state.post_vent_until and now < state.post_vent_until and path_ok:
            return out(True, "POST_VENTILATION_SUPPORT",
                       f"ON: finishing a short run after exterior ventilation stopped (until "
                       f"{state.post_vent_until.strftime('%H:%M')}).", "uncertain", preds=preds)

    # 8. energy optimization (before scheduled mixing, which is otherwise unconditional)
    calm = all(z.air is None or z.air.rh * 100 < cs.observe_rh_pct for z in zones.values()) and \
        not any(s.risk in ("monitor", "elevated", "possible") for s in surfaces)

    # 7. scheduled anti-stagnation mixing
    if cs.periodic_mixing and (not cs.mixing_fans or circ.id in cs.mixing_fans):
        hour = local_hour if local_hour is not None else now.hour + now.minute / 60
        if _in_windows(cs.mixing_windows, hour):
            if cs.energy_optimization and calm:
                reasons.append("ENERGY_OPTIMIZATION")
            else:
                period = cs.mixing_on_min + cs.mixing_off_min
                phase = (hour * 60) % period
                if phase < cs.mixing_on_min:
                    return out(True, "SCHEDULED_MIXING",
                               f"ON: scheduled mixing ({cs.mixing_on_min:.0f} min on, {cs.mixing_off_min:.0f} min off). "
                               "Experimental; compare results on the History page.", "uncertain", preds=preds,
                               immediate=True)

    if unavailable and not preds:
        names = ", ".join(sorted({z for k in unavailable for z in (k.zone_a, k.zone_b)
                                  if not (zones.get(z) and zones[z].ok)}))
        reasons.append("ZONE_SENSOR_UNAVAILABLE")
        return out(False, "ZONE_SENSOR_UNAVAILABLE", f"OFF: no usable reading for {names}; humidity-based "
                   "circulation for this connection is paused.", "uncertain", preds=preds)
    if notes:
        return out(False, "NO_CLEAR_BENEFIT", "OFF: " + "; ".join(notes) + ".", "none", preds=preds)
    if calm and cs.energy_optimization:
        return out(False, "ZONES_ACCEPTABLE", "OFF: every zone is below the observe threshold and no monitored "
                   "surface is at risk.", "none", preds=preds)
    return out(False, "NO_TRIGGER", "OFF: no zone has persistent high humidity or a reliable moisture difference that "
               "mixing would improve.", "none", preds=preds)


def runtime_today(intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> float:
    total = 0.0
    for a, b in intervals:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return total / 3600
