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
    trigger_since: datetime | None = None  # when a zone difference first called for this fan (activation delay)
    dehum_post_until: datetime | None = None


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
    # zones this fan affects: both ends of its connections, else the zone it sits in
    fan_zones = sorted({z for k in conns for z in (k.zone_a, k.zone_b)} | ({circ.zone} if circ.zone else set()))
    min_on = cs.min_on_minutes if circ.min_on_minutes is None else circ.min_on_minutes
    min_off = cs.min_off_minutes if circ.min_off_minutes is None else circ.min_off_minutes
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
            need = min_on if state.is_on else min_off
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

    if not conns and not circ.zone:
        checks.append(CircCheck("graph", "Zone connection", None, "Not assigned to a connection or zone"))

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
        # a safety stop: it overrides the minimum run time
        return out(False, "CIRCULATION_WORSENS_SURFACE_RISK",
                   f"OFF now: moving air through this connection would carry higher-dew-point air toward {s.name} "
                   f"(predicted conservative margin {m_on:.1f}°F vs {m_off:.1f}°F with the fan off).",
                   "adverse", preds=preds, safety=True)
    if unknown_surface:
        s = unknown_surface[0]
        checks.append(CircCheck("surface", "Surface condensation", None,
                                f"{s.name} is at {s.risk} risk and a zone sensor is unavailable"))
        # missing data at a surface already at risk: stop now rather than keep stirring blind
        return out(False, "SURFACE_RISK_EFFECT_UNKNOWN",
                   f"OFF now: {s.name} is at elevated condensation risk and a zone reading is missing, so the effect "
                   "of circulation cannot be judged.", "uncertain", preds=preds, safety=True)
    checks.append(CircCheck("surface", "Surface condensation", True if preds else None,
                            "No monitored surface would be pushed toward condensation" if preds else
                            "Not evaluated"))

    # 4/5. localized humidity and beneficial mixing
    beneficial = []
    notes = []
    waiting: list[str] = []
    any_raw_trigger = False
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
        # dew-point spread (spec: start at 3°F, keep going until 2°F). Compared by dew point, never by RH,
        # so a cool zone with a high RH but less water does not count as the wetter one.
        dp_gap = delta_c_to_f(target.air.dew_point_c - other.air.dew_point_c)
        p["dew_point_spread_f"] = dp_gap
        # only a gap larger than the combined sensor uncertainty counts
        spread_trigger = g["reliable"] and dp_gap >= (cs.dewpoint_spread_off_f if state.is_on
                                                     else cs.dewpoint_spread_on_f)
        if (grad_trigger or spread_trigger) and not persistent:
            any_raw_trigger = True
            state.trigger_since = state.trigger_since or now
            waited = (now - state.trigger_since).total_seconds() / 60
            if not state.is_on and waited < cs.exchange_delay_min:
                waiting.append(f"{target.name} is {dp_gap:.1f}°F dew point above {other.name}; starting if it lasts "
                               f"{cs.exchange_delay_min - waited:.0f} more min")
                continue
        if spread_trigger:
            grad_trigger = True
        lasted = (f" for {(now - state.trigger_since).total_seconds() / 60:.0f} min"
                  if state.trigger_since and now > state.trigger_since else "")
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
        if (g["reliable"] and g["dw_g_kg"] > 0) or spread_trigger:
            kind = "ZONE_MOISTURE_GRADIENT"
            why = (f"{target.name}'s dew point is {dp_gap:.1f}°F above {other.name}'s{lasted} "
                   f"({g['dw_g_kg']:.2f} g/kg more water, sensor uncertainty ±{g['uncertainty_g_kg']:.2f})")
        else:
            kind = "COOL_ZONE_WARMING"
            why = (f"{target.name} has higher RH mainly because it is cooler; its moisture content is about the same as "
                   f"{other.name}, so mixing helps by warming it, not by removing water")
            reasons.append(g["reason"] if g["reason"] != "ZONE_MOISTURE_GRADIENT" else "RH_DIFFERENCE_FROM_TEMPERATURE")
        level = "LOCAL_HUMIDITY_HIGH" if persistent else "BENEFICIAL_ZONE_MIXING"
        beneficial.append((p, target, other, drop, rise, kind, why, level))
    if not any_raw_trigger:
        state.trigger_since = None

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

    # surface-drying fans blow across concrete: they need a working probe in their area and a surface that
    # is not already near saturation before they run for any automatic reason (SURFACE_PROTECTION above
    # is the one case where the model shows mixing lowers the dew point at the surface)
    if circ.role == "surface_drying":
        mine = [s for s in surfaces if (s.zone in fan_zones) or not fan_zones]
        live = [s for s in mine if s.t_c is not None and s.conservative_margin_f is not None]
        if not live:
            checks.append(CircCheck("surface_data", "Concrete probe", None, "No working probe reading for this area"))
            reasons.append("SURFACE_DATA_MISSING")
            return out(False, "SURFACE_DATA_MISSING",
                       "OFF: this fan blows across a damp surface, and there is no working concrete probe reading for "
                       "its area, so the app cannot tell whether moving air there dries it or adds condensation. "
                       "Manual control still works.", "uncertain", preds=preds, immediate=True)
        risky = [s for s in live if s.risk in ("elevated", "possible") or
                 (s.surface_rh is not None and s.surface_rh * 100 >= ss.surface_rh_elevated_pct)]
        if risky:
            s = risky[0]
            checks.append(CircCheck("surface_data", "Concrete probe", False,
                                    f"{s.name}: margin {s.conservative_margin_f:.1f}°F, surface RH "
                                    f"{min(100, (s.surface_rh or 0) * 100):.0f}%"))
            reasons.append("SURFACE_DRYING_RESTRICTED")
            return out(False, "SURFACE_DRYING_RESTRICTED",
                       f"OFF now: {s.name} is close to its dew point (conservative margin "
                       f"{s.conservative_margin_f:.1f}°F, surface RH about {min(100, (s.surface_rh or 0) * 100):.0f}%), "
                       "so blowing basement air across it could add moisture rather than dry it.", "adverse",
                       preds=preds, safety=True)
        checks.append(CircCheck("surface_data", "Concrete probe", True, "Surface comfortably above its dew point"))

    if beneficial:
        p, target, other, drop, rise, kind, why, level = max(beneficial, key=lambda x: x[3])
        reasons.append(kind)
        txt = (f"ON: {why}. Predicted {target.name} RH in {hz:.0f} min: {p['a' if target.id == p['zone_a'] else 'b']['on']['rh_pct']:.0f}% "
               f"with the fan vs {p['a' if target.id == p['zone_a'] else 'b']['off']['rh_pct']:.0f}% without "
               f"({other.name} {rise:+.1f}%).{no_removal_txt}")
        checks.append(CircCheck("benefit", "Expected benefit", True, f"{target.name} RH −{drop:.1f}% (modeled)"))
        return out(True, level, txt, "beneficial", preds=preds)

    # 5b. dehumidifier support: only on confirmed operation (a power-metering plug); never guessed
    if circ.role == "dehumidifier_support":
        dz = config.dehumidifier.zone
        if not config.dehumidifier.enabled or dehumidifier_running is None:
            checks.append(CircCheck("dehum", "Dehumidifier", None, "Running state not monitored (needs a "
                                    "power-metering plug); this fan only uses humidity readings"))
        elif dehumidifier_running:
            far = [zones[z] for z in fan_zones if z != dz and zones.get(z) and zones[z].ok]
            ref = zones.get(dz) if dz and zones.get(dz) and zones[dz].ok else None
            humid = [z for z in far if z.air.rh * 100 >= cs.elevated_rh_pct or
                     (ref is not None and (z.air.w - ref.air.w) * 1000 >= cs.min_dw_start_g_kg)]
            if humid:
                state.dehum_post_until = now + timedelta(minutes=cs.dehumidifier_post_run_min)
                reasons.append("DEHUMIDIFIER_SUPPORT")
                return out(True, "DEHUMIDIFIER_SUPPORT",
                           f"ON: the dehumidifier is running and {humid[0].name} is more humid "
                           f"({humid[0].air.rh * 100:.0f}% RH), so this fan moves that air toward it.", "beneficial",
                           preds=preds)
            checks.append(CircCheck("dehum", "Dehumidifier", True, "Running; no connected zone needs help"))
        if (dehumidifier_running is not True and state.dehum_post_until and now < state.dehum_post_until
                and state.last_reason in ("DEHUMIDIFIER_SUPPORT", "POST_DEHUMIDIFIER_RUN")):
            return out(True, "POST_DEHUMIDIFIER_RUN",
                       f"ON: finishing a short run after the dehumidifier stopped (until "
                       f"{state.dehum_post_until.strftime('%H:%M')}).", "uncertain", preds=preds)

    # 6. exterior-ventilation support
    ext_on = bool(exterior.get("on"))
    if cs.run_with_exhaust:
        # Only alongside a real Automatic drying run (not a schedule, which can run while adding moisture),
        # only for fans marked to support ventilation, and only where both ends of the fan's connection have
        # a working sensor: an unobserved path is not a demonstrated benefit.
        if exterior.get("auto_on") and exterior.get("drying"):
            missing = sorted({z for z in fan_zones if not (zones.get(z) and zones[z].ok)})
            short = bool(conns) and all(
                zones.get(k.zone_a) and zones[k.zone_a].on_ventilation_path and
                zones.get(k.zone_b) and zones[k.zone_b].on_ventilation_path for k in conns)
            if not circ.ventilation_support:
                checks.append(CircCheck("with_exhaust", "Run with exhaust", False,
                                        "This fan is not marked to support ventilation"))
            elif not fan_zones:
                checks.append(CircCheck("with_exhaust", "Run with exhaust", False,
                                        "Not assigned to a zone connection or zone"))
            elif short and cs.avoid_short_circuit:
                reasons.append("VENTILATION_SHORT_CIRCUIT_RISK")
                checks.append(CircCheck("with_exhaust", "Run with exhaust", False,
                                        "Both ends are on the exhaust air path, so it would push incoming air "
                                        "straight to the exhaust"))
            elif missing:
                reasons.append("RUN_WITH_EXHAUST_NO_DATA")
                checks.append(CircCheck("with_exhaust", "Run with exhaust", False,
                                        f"No working sensor in {', '.join(missing)}, so the benefit cannot be seen"))
            else:
                state.post_vent_until = now + timedelta(minutes=cs.post_ventilation_min)
                reasons.append("RUN_WITH_EXHAUST")
                return out(True, "RUN_WITH_EXHAUST",
                           "ON: the exhaust fans are bringing in drier outside air, so this fan stirs the basement "
                           "to dry damp corners and surfaces into the air being exhausted.", "beneficial",
                           preds=preds)
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
    if waiting:
        return out(False, "ZONE_DIFFERENCE_PENDING", "OFF for now: " + "; ".join(waiting) + ".", "none", preds=preds)
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


# ---------------------------------------------------------------- overview (one resolved mode)
MODES = {
    "WATER_INTRUSION_ALERT": "Water alert: fans held off",
    "EQUIPMENT_FAULT": "Equipment fault",
    "SURFACE_MOISTURE_RISK": "Protecting the concrete",
    "DEHUMIDIFIER_SUPPORT": "Helping the dehumidifier",
    "VENTILATION_ASSIST": "Spreading incoming outside air",
    "ENHANCED_MIXING": "Evening out damp zones",
    "NORMAL_MIXING": "Routine mixing",
    "MANUAL": "Manual control",
    "IDLE": "Resting",
}
_MODE_OF_REASON = {
    "DEHUMIDIFIER_SUPPORT": "DEHUMIDIFIER_SUPPORT", "POST_DEHUMIDIFIER_RUN": "DEHUMIDIFIER_SUPPORT",
    "RUN_WITH_EXHAUST": "VENTILATION_ASSIST", "POST_EXHAUST_RUN": "VENTILATION_ASSIST",
    "VENTILATION_SUPPORT": "VENTILATION_ASSIST", "POST_VENTILATION_SUPPORT": "VENTILATION_ASSIST",
    "LOCAL_HUMIDITY_HIGH": "ENHANCED_MIXING", "BENEFICIAL_ZONE_MIXING": "ENHANCED_MIXING",
    "SCHEDULED_MIXING": "NORMAL_MIXING", "MANUAL_ON": "MANUAL", "SURFACE_PROTECTION": "SURFACE_MOISTURE_RISK",
}
_SURFACE_REASONS = {"CIRCULATION_WORSENS_SURFACE_RISK", "SURFACE_RISK_EFFECT_UNKNOWN", "SURFACE_DRYING_RESTRICTED",
                    "SURFACE_PROTECTION"}
_PRIORITY = ["WATER_INTRUSION_ALERT", "EQUIPMENT_FAULT", "SURFACE_MOISTURE_RISK", "DEHUMIDIFIER_SUPPORT",
             "VENTILATION_ASSIST", "ENHANCED_MIXING", "NORMAL_MIXING", "MANUAL", "IDLE"]


def overview(config: AppConfig, zones: dict, surfaces: list, decisions: dict, exterior: dict | None = None,
             dehumidifier_running: bool | None = None, outdoor: AirState | None = None) -> dict:
    """One resolved circulation mode plus the measurements behind it, for the dashboard and the log.

    The mode follows a fixed priority (safety, equipment, surface risk, dehumidifier, ventilation,
    zone mixing, routine mixing, idle). Per-fan decisions stay independent: a higher-priority mode
    does not force every fan off. A missing concrete probe is reported as unknown, never as safe."""
    exterior = exterior or {}
    cs, ss = config.circulation, config.surfaces
    ok = [z for z in zones.values() if z.ok]
    dps = [(z, c_to_f(z.air.dew_point_c)) for z in ok]
    hi = max(dps, key=lambda x: x[1]) if dps else None
    lo = min(dps, key=lambda x: x[1]) if dps else None
    max_rh = max(ok, key=lambda z: z.air.rh) if ok else None
    issues: list[str] = []
    for z in zones.values():
        if not z.ok:
            issues.append(f"{z.name}: no usable reading")

    probes = [s for s in surfaces if s.t_c is not None and s.conservative_margin_f is not None]
    if not surfaces:
        concrete = {"state": "not_installed", "label": "No concrete probe yet: concrete condensation risk is unknown, "
                    "not assumed safe. Surface-drying fans stay off in automatic."}
        issues.append("No concrete probe")
    elif not probes:
        concrete = {"state": "offline", "label": "Concrete probe not reporting: condensation risk is unknown, not "
                    "assumed safe. Surface-drying fans stay off in automatic."}
        issues.append("Concrete probe not reporting")
    else:
        w = min(probes, key=lambda s: s.conservative_margin_f)
        concrete = {"state": "ok", "name": w.name, "temp_f": c_to_f(w.t_c), "margin_f": w.margin_f,
                    "conservative_margin_f": w.conservative_margin_f,
                    "surface_rh_pct": None if w.surface_rh is None else min(100.0, w.surface_rh * 100),
                    "risk": w.risk, "label": None}
    srh = concrete.get("surface_rh_pct")
    risk_level = ("unknown" if concrete["state"] != "ok" else
                  "condensation_possible" if concrete["conservative_margin_f"] <= ss.possible_margin_f else
                  "high" if (srh or 0) >= 90 or concrete["conservative_margin_f"] <= 2 else
                  "elevated" if (srh or 0) >= ss.surface_rh_elevated_pct or concrete["risk"] == "elevated" else "low")

    dh = config.dehumidifier
    dehum = {"level": 0 if not dh.enabled else (2 if dh.monitor_switch_id else 1),
             "running": dehumidifier_running if dh.enabled else None}
    dehum["label"] = ("Not set up" if not dh.enabled else
                      "Runs on its own; the app does not know when it is running" if dh.monitor_switch_id == "" else
                      "Running" if dehumidifier_running else "Off" if dehumidifier_running is False else
                      "Monitoring plug not responding")

    # resolve one mode by priority
    found = set()
    driving: dict[str, list[str]] = {}
    for cid, d in decisions.items():
        code = d.reason_code
        m = None
        if code == "WATER_INTRUSION_LOCKOUT":
            m = "WATER_INTRUSION_ALERT"
        elif code in ("PLUG_UNVERIFIED", "PLUG_UNREACHABLE", "COMMAND_LOCKOUT"):
            m = "EQUIPMENT_FAULT"
        elif code in _SURFACE_REASONS:
            m = "SURFACE_MOISTURE_RISK"
        elif d.target_on:
            m = _MODE_OF_REASON.get(code, "ENHANCED_MIXING" if code == "COOL_ZONE_WARMING" else None)
        if m:
            found.add(m)
            driving.setdefault(m, []).append(cid)
    if config.control.water_intrusion_lockout:
        found.add("WATER_INTRUSION_ALERT")
    if risk_level in ("high", "condensation_possible"):
        found.add("SURFACE_MOISTURE_RISK")
    mode = next((m for m in _PRIORITY if m in found), "IDLE")
    out_dp = c_to_f(outdoor.dew_point_c) if outdoor is not None else None
    return {
        "mode": mode, "mode_label": MODES[mode], "fans_driving": driving.get(mode, []),
        "automation": cs.mode,
        "fans_on": [cid for cid, d in decisions.items() if d.target_on],
        "zone_dew_point_max_f": hi[1] if hi else None, "zone_dew_point_max_zone": hi[0].name if hi else None,
        "zone_dew_point_min_f": lo[1] if lo else None, "zone_dew_point_min_zone": lo[0].name if lo else None,
        "zone_dew_point_spread_f": (hi[1] - lo[1]) if hi and lo and len(dps) > 1 else None,
        "spread_on_f": cs.dewpoint_spread_on_f, "spread_off_f": cs.dewpoint_spread_off_f,
        "max_rh_pct": max_rh.air.rh * 100 if max_rh else None, "max_rh_zone": max_rh.name if max_rh else None,
        "outdoor_dew_point_f": out_dp,
        "outdoor_minus_indoor_dew_point_f": (out_dp - hi[1]) if out_dp is not None and hi else None,
        "concrete": concrete, "surface_risk": risk_level,
        "exhaust": {"on": bool(exterior.get("on")), "drying": bool(exterior.get("drying"))},
        "dehumidifier": dehum,
        "data_issues": issues,
        "note": "Circulation moves water between zones; it never removes it.",
    }
