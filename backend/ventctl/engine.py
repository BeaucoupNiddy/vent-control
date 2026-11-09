"""Ventilation decision engine.

Pure logic: given conditions, the group's current state, settings and a time,
return a Decision. No I/O, no clock, no hardware. The controller, the
simulator and historical replay all call ``decide()``.

Order of evaluation (first failure wins):
  1. mode (OFF, MANUAL_ON, SCHEDULE, AUTO/SIMULATION)
  2. hard safety: sensors, switch verification, interlocks, condensation,
     thermal limits (minimum basement temperature incl. a conservative
     prediction, pipe freeze warning) -> immediate shutdown, ignores minimum
     on/off timers. Applies to both control policies.
  3. moisture direction: outdoor wetter -> immediate stop (it adds moisture)
  4. benefit, by control policy (both respect minimum on/off timers):
     * "moisture" (original): RH target, dew-point advantage, removal rate, efficiency
     * "tamo" (temperature-aware): minimum removal, then a utility in drying-equivalent
       g/h that weighs urgency-weighted removal against a direction-aware thermal
       penalty, energy and soft condensation risk (see tamo.py), with separate start
       and stop thresholds, plus optional forecast deferral.

The TAMO evaluation is computed for every decision (also under the moisture
policy) so the dashboard and shadow comparison always show it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from . import tamo, thermal
from .airflow import VentilationEffect, ventilation_effect
from .config import AppConfig, ControlPolicy, EnergyProfile, Fan, FanGroup, FanType, Mode
from .psychro import AirState
from .sensors import Conditions
from .units import c_to_f, delta_c_to_f, delta_f_to_c, f_to_c


@dataclass
class GroupState:
    is_on: bool = False
    verified: bool = True  # controller can confirm the switch state
    last_change: datetime | None = None
    manual_until: datetime | None = None
    maintenance: bool = False  # authorized testing mode: bypasses hard interlocks (manual only)
    deferring_since: datetime | None = None  # forecast deferral of a start began


@dataclass
class Check:
    id: str
    label: str
    ok: bool | None  # None = not evaluated / not applicable
    detail: str
    hard: bool = False


@dataclass
class Decision:
    group_id: str
    mode: str
    target_on: bool
    reason_code: str
    explanation: str
    safety: bool = False  # True when a hard safety rule forced OFF
    held: bool = False  # True when a min on/off timer is holding the current state
    checks: list[Check] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    effect: VentilationEffect | None = None
    next_eligible_switch: datetime | None = None
    warnings: list[str] = field(default_factory=list)
    policy: str = ControlPolicy.MOISTURE.value
    reasons: list[str] = field(default_factory=list)  # all applicable structured reason codes, primary first
    tamo: dict | None = None  # TAMO evaluation (always computed when possible)
    opportunity: str | None = None  # excellent | good | marginal | unfavorable | unsafe

    def to_dict(self) -> dict:
        d = asdict(self)
        d["effect"] = self.effect.to_dict() if self.effect else None
        d["next_eligible_switch"] = self.next_eligible_switch.isoformat() if self.next_eligible_switch else None
        return d


def dehumidifier_efficiency(config: AppConfig) -> float:
    """Configured dehumidifier water removal per kWh [g/kWh]."""
    dh = config.dehumidifier
    g_h = dh.removal_pints_per_day * 473.176473 / 24
    return g_h / (dh.rated_w / 1000) if dh.rated_w > 0 else 0.0


def profile_thresholds(config: AppConfig) -> tuple[float, float]:
    """(min removal g/h, min efficiency g/kWh) for the selected energy profile."""
    c = config.control
    if c.energy_profile == EnergyProfile.MAX_DRYING:
        return c.min_removal_g_per_h * 0.4, 0.0
    if c.energy_profile == EnergyProfile.ENERGY_SAVER:
        eff = c.min_efficiency_g_per_kwh_saver
        dh = config.dehumidifier
        if dh.enabled and dh.require_ventilation_beats_dehumidifier and dh.rated_w > 0:
            eff = max(eff, dehumidifier_efficiency(config))
        return c.min_removal_g_per_h * 2.0, eff
    return c.min_removal_g_per_h, c.min_efficiency_g_per_kwh_balanced


def _fmt_f(c: float) -> str:
    return f"{c_to_f(c):.1f}°F"


def _gal_h(g_h: float) -> str:
    gal = g_h / 3785.411784
    return f"{gal:.2f} gallons/hour ({g_h:.0f} g/h)"


def group_fans(config: AppConfig, group: FanGroup) -> list[Fan]:
    return [f for f in config.fans if f.group == group.id and f.enabled]


def decide(
    cond: Conditions,
    state: GroupState,
    config: AppConfig,
    group: FanGroup,
    now: datetime,
    mode: Mode | None = None,
    local_hour: float | None = None,
    policy: ControlPolicy | None = None,
    thermal_params: thermal.ThermalParams | None = None,
    detail: bool = True,
) -> Decision:
    """detail=False (simulator) predicts only the horizons the decision needs."""
    mode = mode or config.mode
    policy = ControlPolicy(policy or config.control.policy)
    c = config.control
    fans = group_fans(config, group)
    checks: list[Check] = []
    warnings: list[str] = []
    reasons: list[str] = []
    tamo_out: dict | None = None
    tres: tamo.TamoResult | None = None

    def out(target: bool, code: str, text: str, safety: bool = False, immediate: bool = False) -> Decision:
        d = _finish(state, config, group, mode, target, code, text, safety or immediate, safety,
                    checks, metrics, effect, warnings, now)
        d.policy = policy.value
        prim = code.split(":")[-1]
        d.reasons = [prim] + [r for r in reasons if r != prim]
        if d.held:
            d.reasons.insert(0, "MINIMUM_RUNTIME_ACTIVE" if state.is_on else "MINIMUM_OFF_TIME_ACTIVE")
        d.tamo = tamo_out
        if tamo_out is not None:
            removal = effect.net_removal_g_h if effect else 0.0
            u = tamo_out["terms"]["utility_g_h"] if tres else 0.0
            blocked = safety
            d.opportunity = tamo.opportunity(blocked, removal, u, config.temperature.utility_start_g_h, tres)
            tamo_out["opportunity"] = d.opportunity
        return d

    metrics: dict = {}
    effect: VentilationEffect | None = None
    ind, outd = cond.indoor, cond.outdoor
    if ind and outd:
        effect = ventilation_effect(ind, outd, fans, config.house, cond.upstairs) if fans else None
        metrics = {
            "indoor": ind.to_dict(),
            "outdoor": outd.to_dict(),
            "dewpoint_advantage_f": delta_c_to_f(ind.dew_point_c - outd.dew_point_c),
            "delta_w_g_per_kg": (ind.w - outd.w) * 1000,
            "outdoor_rh_at_basement_temp_pct": outd.rh_at(ind.t_c) * 100,
        }
        if effect and effect.pressurized:
            warnings.append("Intake airflow exceeds exhaust: basement air may be pushed into the house.")
        if effect and effect.conditioned_air_assumed:
            warnings.append(
                f"Assumes {config.house.conditioned_air_fraction * 100:.0f}% of unbalanced exhaust is "
                "conditioned air from upstairs (scenario estimate, not measured)."
            )
    look: tamo.Outlook | None = None
    if ind and outd and effect:
        tp = thermal_params or thermal.params_from_config(config)
        up_air = cond.upstairs or AirState.from_t_rh(f_to_c(config.house.upstairs_temp_f),
                                                      config.house.upstairs_rh_pct / 100.0)
        mins = tamo.HORIZONS if detail else (60,)
        look = tamo.outlook(ind, outd, up_air, effect, config, tp, state.is_on, config.temperature.horizon_min,
                            minutes=mins)
        _, e_value = profile_thresholds(config)
        tres = tamo.evaluate(ind, outd, effect, look, config, state.is_on, cond.surface_c, e_value)
        tamo_out = {
            "in_control": policy == ControlPolicy.TAMO,
            "terms": {"benefit_g_h": tres.benefit_g_h, "thermal_penalty_g_h": tres.thermal_penalty_g_h,
                      "energy_penalty_g_h": tres.energy_penalty_g_h, "risk_penalty_g_h": tres.risk_penalty_g_h,
                      "switch_cost_g_h": tres.switch_cost_g_h, "utility_g_h": tres.utility_g_h},
            "urgency": tres.urgency, "k_eff_g_h": tres.k_eff,
            "fan_effect_f": tres.fan_effect_f, "band_change_f": tres.band_change_f,
            "beneficial_temperature": tres.beneficial_temperature,
            "thresholds": {"start_g_h": config.temperature.utility_start_g_h,
                           "stop_g_h": config.temperature.utility_stop_g_h},
            "prediction": {
                "minutes": look.on.minutes,
                "on_f": [c_to_f(x) for x in look.on.t_c], "off_f": [c_to_f(x) for x in look.off.t_c],
                "on_conservative_f": [c_to_f(x) for x in look.on_conservative.t_c],
                "fan_effect_f": [delta_c_to_f(a - b) for a, b in zip(look.on.t_c, look.off.t_c)],
                "on_rh_pct": [x * 100 for x in look.on.rh], "off_rh_pct": [x * 100 for x in look.off.rh],
                "on_dew_point_f": [c_to_f(x) for x in look.on.dew_point_c],
                "off_dew_point_f": [c_to_f(x) for x in look.off.dew_point_c]},
            "model": tp.to_dict(),
            "labels": {"measured": ["temperatures", "RH", "fan state"],
                       "estimated": ["moisture removal", "inlet mixture", "temperature predictions",
                                     "HVAC penalty", "fan power (rated, unless the plug meters it)"],
                       "heuristic": ["urgency", "thermal penalty", "risk penalty", "utility"]},
            **tres.details,
        }
        metrics["tamo_utility_g_h"] = tres.utility_g_h
    if cond.surface_c is not None:
        metrics["surface_c"] = cond.surface_c
        metrics["surface_source"] = cond.surface_source
        if outd:
            metrics["condensation_margin_f"] = delta_c_to_f(cond.surface_c - outd.dew_point_c)
    metrics["indoor_label"] = cond.indoor_label

    if not fans:
        return out(False, "NO_FANS", "Fans OFF: no enabled fans are assigned to this group.", immediate=True)

    # ---- mode: OFF -------------------------------------------------------
    if mode == Mode.OFF:
        return out(False, "MODE_OFF", "Fans OFF: mode is set to OFF.", immediate=True)

    # ---- hard safety checks shared by every running mode ------------------
    sensors_ok = ind is not None and outd is not None
    checks.append(Check("sensors", "Sensors reporting", sensors_ok,
                        "Indoor and outdoor readings are current" if sensors_ok
                        else (cond.indoor_problem or cond.outdoor_problem or "missing"), hard=True))
    checks.append(Check("switch", "Switch state verified", state.verified,
                        "Switch confirmed" if state.verified else "Cannot confirm the fan switch state", hard=True))

    interlock = _interlock(config, fans, cond)
    checks.append(Check("interlocks", "Safety interlocks", interlock is None,
                        interlock[1] if interlock else "None active", hard=True))

    # starting needs 0.5°F more margin than continuing, so the fans do not chatter at the safety boundary
    condensation = _condensation(cond, c.surface_margin_f + (0.0 if state.is_on else 0.5))
    checks.append(Check("condensation", "Condensation margin", condensation[0], condensation[2], hard=True))

    thermal_block = _thermal_safety(cond, look, config) if (ind and look) else None
    checks.append(Check("thermal", "Temperature limits", None if look is None else thermal_block is None,
                        thermal_block[1] if thermal_block else _thermal_scope(cond, config) if look else
                        "not evaluated", hard=True))

    bypass = mode == Mode.MANUAL_ON and state.maintenance

    if mode == Mode.MANUAL_ON:
        if state.manual_until is not None and now >= state.manual_until:
            return out(False, "MANUAL_EXPIRED", "Fans OFF: the manual override has expired.", immediate=True)
        if not bypass:
            if not state.verified:
                return out(False, "SWITCH_UNVERIFIED", "Fans OFF: the controller cannot confirm the fan switch state.", safety=True)
            if interlock:
                return out(False, interlock[0], f"Fans OFF: {interlock[1]}", safety=True)
            if condensation[0] is False:
                return out(False, condensation[1], f"Fans OFF: {condensation[2]}", safety=True)
            if thermal_block:
                return out(False, thermal_block[0], f"Fans OFF (safety limit): {thermal_block[1]}", safety=True)
            if condensation[0] is None:
                warnings.append("Condensation safety is unverified during this manual run.")
        else:
            warnings.append("MAINTENANCE MODE: safety interlocks are bypassed.")
        reasons.append("MANUAL_OVERRIDE")
        until = f" until {state.manual_until.strftime('%H:%M')}" if state.manual_until else ""
        return out(True, "MANUAL_ON", f"Fans ON: manual override{until}.", immediate=True)

    # Unattended modes (AUTO, SCHEDULE, SIMULATION) fail safe on every hard check.
    if not sensors_ok:
        reasons.append("SENSOR_DATA_STALE")
        code = "SENSOR_STALE" if "updated" in (cond.indoor_problem or cond.outdoor_problem or "") else "SENSOR_MISSING"
        return out(False, code, f"Fans OFF: {cond.outdoor_problem or cond.indoor_problem}.", safety=True)
    if not state.verified:
        return out(False, "SWITCH_UNVERIFIED", "Fans OFF: the controller cannot confirm the fan switch state.", safety=True)
    if interlock:
        reasons.append("SAFETY_INTERLOCK")
        return out(False, interlock[0], f"Fans OFF: {interlock[1]}", safety=True)
    if condensation[0] is not True:
        if condensation[0] is False:
            reasons.append("SURFACE_CONDENSATION_RISK")
        return out(False, condensation[1], f"Fans OFF: {condensation[2]}", safety=True)
    if thermal_block:
        return out(False, thermal_block[0], f"Fans OFF (safety limit): {thermal_block[1]}", safety=True)
    cnd = (tres.details.get("condensation") or {}) if tres else {}
    if cnd and cnd["indoor_margin_on_f"] <= 0 and cnd["indoor_margin_on_f"] < cnd["indoor_margin_off_f"]:
        reasons.append("SURFACE_CONDENSATION_RISK")
        return out(False, "PREDICTED_SURFACE_CONDENSATION",
                   f"Fans OFF (safety limit): running would cool surfaces to or below the basement dew point "
                   f"(predicted margin {cnd['indoor_margin_on_f']:.1f}°F).", safety=True)

    assert ind and outd and effect
    if mode == Mode.SCHEDULE:
        hour = local_hour if local_hour is not None else now.hour + now.minute / 60
        in_window = any(w.contains(hour) for w in c.schedule)
        checks.append(Check("schedule", "Schedule window", in_window, "Inside a scheduled window" if in_window
                            else "Outside scheduled windows"))
        if not in_window:
            return out(False, "SCHEDULE_OUTSIDE", "Fans OFF: outside the scheduled hours.", immediate=True)
        txt = "Fans ON: scheduled run."
        if effect.net_removal_g_h < 0:
            txt += f" Note: ventilation is currently adding moisture ({_gal_h(-effect.net_removal_g_h)})."
        return out(True, "SCHEDULE_ON", txt, immediate=True)

    # ---- AUTO / SIMULATION: drying benefit ------------------------------
    on = state.is_on
    adv_f = delta_c_to_f(ind.dew_point_c - outd.dew_point_c)
    dw = ind.w - outd.w

    wetter = dw <= 0
    checks.append(Check("direction", "Outdoor air drier", not wetter,
                        f"Outdoor dew point {_fmt_f(outd.dew_point_c)} vs basement {_fmt_f(ind.dew_point_c)}"))
    if wetter:
        reasons.append("OUTDOOR_AIR_WETTER")
        return out(False, "OUTDOOR_WETTER",
                   "Fans OFF: outdoor dew point is higher than basement dew point. Ventilation would add moisture.",
                   immediate=True)

    if policy == ControlPolicy.TAMO:
        return _tamo_decision(cond, state, config, group, now, effect, look, tres, reasons, checks, out)

    rh_pct = ind.rh * 100
    start_rh = c.target_rh_pct + c.rh_start_deadband_pct
    rh_ok = rh_pct > c.target_rh_pct if on else rh_pct > start_rh
    checks.append(Check("rh", "Basement above target", rh_ok,
                        f"Basement RH {rh_pct:.0f}% vs target {c.target_rh_pct:.0f}%"))
    if not rh_ok:
        if on:
            reason = ("TARGET_REACHED", "Fans OFF: basement humidity has reached the configured target.")
        else:
            reason = ("BELOW_TARGET", f"Fans OFF: basement humidity ({rh_pct:.0f}%) is at or near the target "
                                      f"({c.target_rh_pct:.0f}%).")
        return out(False, *reason)

    adv_needed = c.dewpoint_off_advantage_f if on else c.dewpoint_on_advantage_f
    adv_ok = adv_f > adv_needed if on else adv_f >= adv_needed
    checks.append(Check("advantage", "Dew-point advantage", adv_ok,
                        f"{adv_f:.1f}°F drier outside (need {'>' if on else '≥'} {adv_needed:.1f}°F)"))
    if not adv_ok:
        return out(False, "ADVANTAGE_TOO_SMALL",
                   f"Fans OFF: outdoor air is slightly drier ({adv_f:.1f}°F lower dew point), but the predicted "
                   "drying benefit is too small.")

    min_rem, min_eff = profile_thresholds(config)
    factor = c.efficiency_off_factor if on else 1.0
    rem_ok = effect.net_removal_g_h >= min_rem * factor
    checks.append(Check("removal", "Moisture removal", rem_ok,
                        f"{_gal_h(effect.net_removal_g_h)} (need ≥ {min_rem * factor:.0f} g/h)"))
    if not rem_ok:
        return out(False, "BENEFIT_TOO_SMALL",
                   f"Fans OFF: outdoor air is slightly drier, but the predicted drying benefit "
                   f"({effect.net_removal_g_h:.0f} g/h) is too small.")

    eff = effect.efficiency_g_per_kwh
    eff_ok = min_eff <= 0 or eff is None or eff >= min_eff * factor
    checks.append(Check("efficiency", "Efficiency", eff_ok,
                        (f"{eff:.0f} g/kWh" if eff is not None else "no power draw") +
                        (f" (need ≥ {min_eff * factor:.0f} g/kWh, {config.control.energy_profile.value})" if min_eff else
                         " (no minimum in Maximum Drying)")))
    if not eff_ok:
        return out(False, "EFFICIENCY_TOO_LOW",
                   f"Fans OFF: drying benefit ({effect.net_removal_g_h:.0f} g/h) does not justify the energy cost "
                   f"({effect.total_power_w:.0f} W incl. conditioned-air loss) under the "
                   f"{config.control.energy_profile.value.replace('_', ' ')} profile.")

    margin_txt = ""
    if cond.surface_c is not None:
        margin_txt = " Condensation safety checks passed"
        margin_txt += " (using estimated surface temperature)." if cond.surface_source == "estimated" else "."
    return out(True, "DRYING",
               f"Fans ON: outdoor air is significantly drier than basement air ({adv_f:.1f}°F lower dew point). "
               f"Estimated moisture removal is {_gal_h(effect.net_removal_g_h)}.{margin_txt}")


def _tamo_decision(cond, state, config, group, now, effect, look, tres, reasons, checks, out) -> Decision:
    t = config.temperature
    ind = cond.indoor
    on = state.is_on
    h = t.horizon_min
    removal = effect.net_removal_g_h
    min_rem, e_value = profile_thresholds(config)
    factor = config.control.efficiency_off_factor if on else 1.0
    dw = (ind.w - look.inlet_w) * 1000
    dw_need = t.min_dw_stop_g_kg if on else t.min_dw_start_g_kg
    dw_ok = dw > dw_need if on else dw >= dw_need
    checks.append(Check("advantage", "Moisture advantage", dw_ok,
                        f"Basement air holds {dw:.2f} g/kg more water than incoming air "
                        f"(need {'>' if on else '≥'} {dw_need:.2f})"))
    if not dw_ok:
        return out(False, "MOISTURE_BENEFIT_INSUFFICIENT",
                   f"Fans OFF: incoming air is only slightly drier ({dw:.2f} g/kg lower humidity ratio), too small a "
                   "difference to be worth running.")
    rem_ok = removal >= min_rem * factor
    checks.append(Check("removal", "Moisture removal", rem_ok,
                        f"{_gal_h(removal)} (need ≥ {min_rem * factor:.0f} g/h)"))
    if not rem_ok:
        return out(False, "MOISTURE_BENEFIT_INSUFFICIENT",
                   f"Fans OFF: outdoor air is slightly drier, but the estimated removal ({removal:.0f} g/h) is "
                   "below the minimum worth running for.")
    rh_pct = ind.rh * 100
    u = tres.urgency
    checks.append(Check("urgency", "Humidity urgency", u > 0, f"{u:.2f} at {rh_pct:.0f}% RH "
                        f"(0 at {t.urgency_low_rh_pct:.0f}%, 1 at {t.urgency_high_rh_pct:.0f}%)"))
    if u <= 0:
        return out(False, "HUMIDITY_LOW",
                   f"Fans OFF: basement RH is {rh_pct:.0f}%, at or below {t.urgency_low_rh_pct:.0f}% where further "
                   "drying has little value.")
    util = tres.utility_g_h
    thresh = t.utility_stop_g_h if on else t.utility_start_g_h
    eff_word = "cooling" if tres.fan_effect_f < 0 else "warming"
    temp_txt = (f"the predicted {h:.0f}-minute {eff_word} is {abs(tres.fan_effect_f):.1f}°F"
                + (" (toward the preferred range)" if tres.beneficial_temperature else ""))
    terms = (f"Drying benefit {tres.benefit_g_h:.0f}, thermal penalty {tres.thermal_penalty_g_h:.0f}, energy "
             f"{tres.energy_penalty_g_h:.0f}, risk {tres.risk_penalty_g_h:.0f}: net utility {util:.0f} "
             f"(drying-equivalent g/h; {'stop below' if on else 'start at'} {thresh:.0f}).")
    checks.append(Check("utility", "Net ventilation utility", util >= thresh, terms))
    if util < thresh:
        pen = tres.thermal_penalty_g_h
        if pen > 0.3 * tres.benefit_g_h:
            reasons.append("THERMAL_PENALTY_TOO_HIGH")
        if tres.energy_penalty_g_h > 0.3 * tres.benefit_g_h:
            reasons.append("EFFICIENCY_TOO_LOW")
        if pen > tres.energy_penalty_g_h and pen > 0.3 * tres.benefit_g_h:
            urg = ("within the desired range" if rh_pct <= t.urgency_low_rh_pct + 0.25 * (
                t.urgency_high_rh_pct - t.urgency_low_rh_pct) else f"{rh_pct:.0f}% (urgency {u:.2f})")
            return out(False, "THERMAL_PENALTY_TOO_HIGH",
                       f"Fans OFF: outdoor air is {abs(c_to_f(cond.outdoor.t_c) - c_to_f(ind.t_c)):.0f}°F "
                       f"{'colder' if cond.outdoor.t_c < ind.t_c else 'warmer'} than the basement. Estimated drying "
                       f"is {_gal_h(removal)}, while {temp_txt}. Basement humidity is {urg}, so the drying benefit "
                       f"does not justify the thermal impact. {terms}")
        if tres.energy_penalty_g_h >= pen and tres.energy_penalty_g_h > 0.3 * tres.benefit_g_h:
            return out(False, "EFFICIENCY_TOO_LOW",
                       f"Fans OFF: drying benefit does not justify the energy use ({effect.total_power_w:.0f} W incl. "
                       f"estimated conditioned-air loss). {terms}")
        return out(False, "MOISTURE_BENEFIT_INSUFFICIENT",
                   f"Fans OFF: at {rh_pct:.0f}% RH the drying benefit is too small to be worth running. {terms}")

    # optional forecast deferral (only delays a start; never stops a running fan)
    if (not on and t.forecast_enabled and cond.forecast and u < t.forecast_max_urgency
            and tres.thermal_penalty_g_h + tres.energy_penalty_g_h > 0.25 * tres.benefit_g_h):
        up_air = cond.upstairs or AirState.from_t_rh(f_to_c(config.house.upstairs_temp_f),
                                                      config.house.upstairs_rh_pct / 100.0)

        def at(out_air):
            eff = ventilation_effect(ind, out_air, group_fans(config, group), config.house, cond.upstairs)
            if eff.net_removal_g_h <= 0:
                return None
            lk = tamo.outlook(ind, out_air, up_air, eff, config, look.params, False, h, minutes=(int(h),))
            if _thermal_safety(cond, lk, config):
                return None
            return tamo.evaluate(ind, out_air, eff, lk, config, False, cond.surface_c, e_value)
        choice = tamo.forecast_choice(cond.forecast, now, at, tres, config)
        if choice:
            metrics_fc = {**choice, "forecast_age_h": cond.forecast_age_h}
            if choice["better"]:
                since = state.deferring_since or now
                waited = (now - since).total_seconds() / 60
                if waited < t.max_deferral_min:
                    reasons.append("FORECAST_DEFERRAL")
                    d = out(False, "BETTER_FORECAST_WINDOW",
                            f"Fans OFF (waiting for a better window): drying now is worth {util:.0f} g/h-equivalent, "
                            f"but in about {choice['hours_ahead']:.1f} h the forecast offers "
                            f"{choice['utility_g_h']:.0f} with less temperature impact. Deferred {waited:.0f} of "
                            f"{t.max_deferral_min:.0f} minutes allowed.")
                    if d.tamo is not None:
                        d.tamo["forecast"] = metrics_fc
                    return d
                reasons.append("FORECAST_DEFERRAL_LIMIT_REACHED")
    reasons.extend(r for r in tres.reasons if r not in reasons)
    d = out(True, "MOISTURE_BENEFIT_POSITIVE",
            f"Fans ON: outdoor air is considerably drier ({_gal_h(removal)} estimated) and {temp_txt}. "
            f"Ventilation offers worthwhile moisture removal at {rh_pct:.0f}% RH. {terms}")
    return d


def _thermal_scope(cond: Conditions, config: AppConfig) -> str:
    t = config.temperature
    parts = [f"Basement air kept above {t.min_allowed_f:.0f}°F (whole-basement protection, conservative prediction)."]
    if cond.pipe_c is not None:
        parts.append(f"Pipe sensor {cond.pipe_label}: {c_to_f(cond.pipe_c):.1f}°F (freeze warning "
                     f"{t.freeze_warning_f:.0f}°F).")
    else:
        parts.append("No pipe sensor: pipes near vents are not monitored.")
    return " ".join(parts)


def _thermal_safety(cond: Conditions, look: tamo.ThermalOutlook, config: AppConfig) -> tuple[str, str] | None:
    """Hard thermal limits for cold-air ventilation. Returns (code, text) when ventilation must stop."""
    t = config.temperature
    ind, outd = cond.indoor, cond.outdoor
    m = t.sensor_margin_f
    inlet_c = look.src_on
    if cond.pipe_c is not None and inlet_c < cond.pipe_c and c_to_f(cond.pipe_c) <= t.freeze_warning_f + m:
        return ("LOCALIZED_FREEZE_RISK",
                f"{cond.pipe_label} is {c_to_f(cond.pipe_c):.1f}°F, at or below the {t.freeze_warning_f:.0f}°F "
                "freeze warning. Cold-air ventilation is blocked.")
    if cond.pipe_sensors and cond.pipe_c is None and outd and c_to_f(outd.t_c) <= t.freeze_warning_f:
        return ("PIPE_SENSOR_UNAVAILABLE",
                "a pipe sensor is configured but not reporting while outdoor air is below the "
                f"{t.freeze_warning_f:.0f}°F freeze warning.")
    if inlet_c >= ind.t_c:
        return None
    tf = c_to_f(ind.t_c)
    if tf <= t.min_allowed_f + m:
        return ("MIN_TEMP_REACHED",
                f"cold-air ventilation is blocked because the basement ({tf:.1f}°F) is at the configured minimum "
                f"of {t.min_allowed_f:.0f}°F.")
    check_min = t.horizon_min + config.system.decision_seconds / 60
    if look.params.confidence == "low":
        check_min = max(check_min, 60)  # poorly calibrated: look further ahead before allowing long runs
    pred = c_to_f(look.at(check_min, "conservative"))
    if pred < t.min_allowed_f + m:
        return ("PREDICTED_MIN_TEMP_BREACH",
                f"cold-air ventilation is blocked because the basement ({tf:.1f}°F) could fall below the "
                f"{t.min_allowed_f:.0f}°F minimum within about {check_min:.0f} minutes (conservative prediction "
                f"{pred:.1f}°F, model confidence {look.params.confidence}).")
    return None


def _interlock(config: AppConfig, fans: list[Fan], cond: Conditions) -> tuple[str, str] | None:
    c = config.control
    if c.water_intrusion_lockout:
        return "INTERLOCK_WATER", "water-intrusion lockout is active."
    exhaust = sum(f.actual_cfm for f in fans if f.type == FanType.EXHAUST)
    intake = sum(f.actual_cfm for f in fans if f.type == FanType.INTAKE)
    if c.combustion_interlock and exhaust > intake:
        return "INTERLOCK_COMBUSTION", "combustion-appliance interlock blocks depressurizing (exhaust-only) ventilation."
    if c.max_depressurization_pa is not None and cond.diff_pressure_pa is not None:
        if cond.diff_pressure_pa < -abs(c.max_depressurization_pa):
            return "INTERLOCK_PRESSURE", (f"basement depressurization {cond.diff_pressure_pa:.1f} Pa exceeds the "
                                          f"{c.max_depressurization_pa:.1f} Pa limit.")
    return None


def _condensation(cond: Conditions, margin_f: float) -> tuple[bool | None, str, str]:
    if cond.surface_c is None:
        return (None, "CONDENSATION_UNVERIFIED",
                "condensation safety is unverified. Add a surface sensor or configure a conservative minimum "
                "surface temperature.")
    if cond.outdoor is None:
        return None, "CONDENSATION_UNVERIFIED", "condensation safety cannot be checked without outdoor data."
    margin = cond.surface_c - cond.outdoor.dew_point_c
    kind = "measured" if cond.surface_source == "measured" else "estimated"
    if margin < delta_f_to_c(margin_f):
        if margin < 0:
            text = (f"outdoor dew point ({_fmt_f(cond.outdoor.dew_point_c)}) is above the {kind} surface "
                    f"temperature ({_fmt_f(cond.surface_c)}). Condensation risk.")
        else:
            text = (f"outdoor dew point ({_fmt_f(cond.outdoor.dew_point_c)}) is within {margin_f:.0f}°F of the "
                    f"{kind} surface temperature ({_fmt_f(cond.surface_c)}). Condensation risk.")
        return False, "CONDENSATION_RISK", text
    return True, "OK", (f"{delta_c_to_f(margin):.1f}°F between {kind} surface ({_fmt_f(cond.surface_c)}) and "
                        f"outdoor dew point")


def _finish(state, config, group, mode, target, code, text, immediate, safety, checks, metrics, effect,
            warnings, now) -> Decision:
    c = config.control
    held = False
    next_ok = None
    if state.last_change is not None:
        wait = timedelta(minutes=c.min_on_minutes if state.is_on else c.min_off_minutes)
        next_ok = state.last_change + wait
    if target != state.is_on and not immediate and next_ok is not None and now < next_ok:
        held = True
        mins = (next_ok - now).total_seconds() / 60
        if state.is_on:
            text = (f"Fans ON (minimum runtime): {mins:.0f} more minute(s) before switching off. "
                    f"Reason to stop: {text.split(': ', 1)[-1]}")
            code = "MIN_ON_HOLD:" + code
        else:
            text = (f"Fans OFF (minimum off-time): {mins:.0f} more minute(s) before switching on. "
                    f"{text.split(': ', 1)[-1]}")
            code = "MIN_OFF_HOLD:" + code
        target = state.is_on
    return Decision(
        group_id=group.id, mode=mode.value, target_on=target, reason_code=code, explanation=text,
        safety=safety, held=held, checks=checks, metrics=metrics, effect=effect,
        next_eligible_switch=next_ok if (next_ok and next_ok > now) else None, warnings=warnings,
    )
