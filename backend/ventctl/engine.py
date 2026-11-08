"""Ventilation decision engine.

Pure logic: given conditions, the group's current state, settings and a time,
return a Decision. No I/O, no clock, no hardware. The controller, the
simulator and historical replay all call ``decide()``.

Order of evaluation (first failure wins):
  1. mode (OFF, MANUAL_ON, SCHEDULE, AUTO/SIMULATION)
  2. hard safety: sensors, switch verification, interlocks, condensation
     -> immediate shutdown, ignores minimum on/off timers
  3. moisture direction: outdoor wetter -> immediate stop (it adds moisture)
  4. drying benefit: RH target, dew-point advantage, removal rate, efficiency
     -> these respect minimum on/off timers (hysteresis)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .airflow import VentilationEffect, ventilation_effect
from .config import AppConfig, EnergyProfile, Fan, FanGroup, FanType, Mode
from .sensors import Conditions
from .units import c_to_f, delta_c_to_f, delta_f_to_c


@dataclass
class GroupState:
    is_on: bool = False
    verified: bool = True  # controller can confirm the switch state
    last_change: datetime | None = None
    manual_until: datetime | None = None
    maintenance: bool = False  # authorized testing mode: bypasses hard interlocks (manual only)


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
) -> Decision:
    mode = mode or config.mode
    c = config.control
    fans = group_fans(config, group)
    checks: list[Check] = []
    warnings: list[str] = []

    def out(target: bool, code: str, text: str, safety: bool = False, immediate: bool = False) -> Decision:
        return _finish(state, config, group, mode, target, code, text, safety or immediate, safety,
                       checks, metrics, effect, warnings, now)

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

    condensation = _condensation(cond, c.surface_margin_f)
    checks.append(Check("condensation", "Condensation margin", condensation[0], condensation[2], hard=True))

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
            if condensation[0] is None:
                warnings.append("Condensation safety is unverified during this manual run.")
        else:
            warnings.append("MAINTENANCE MODE: safety interlocks are bypassed.")
        until = f" until {state.manual_until.strftime('%H:%M')}" if state.manual_until else ""
        return out(True, "MANUAL_ON", f"Fans ON: manual override{until}.", immediate=True)

    # Unattended modes (AUTO, SCHEDULE, SIMULATION) fail safe on every hard check.
    if not sensors_ok:
        code = "SENSOR_STALE" if "updated" in (cond.indoor_problem or cond.outdoor_problem or "") else "SENSOR_MISSING"
        return out(False, code, f"Fans OFF: {cond.outdoor_problem or cond.indoor_problem}.", safety=True)
    if not state.verified:
        return out(False, "SWITCH_UNVERIFIED", "Fans OFF: the controller cannot confirm the fan switch state.", safety=True)
    if interlock:
        return out(False, interlock[0], f"Fans OFF: {interlock[1]}", safety=True)
    if condensation[0] is not True:
        return out(False, condensation[1], f"Fans OFF: {condensation[2]}", safety=True)

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
        return out(False, "OUTDOOR_WETTER",
                   "Fans OFF: outdoor dew point is higher than basement dew point. Ventilation would add moisture.",
                   immediate=True)

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
