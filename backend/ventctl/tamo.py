"""Temperature-Aware Moisture Optimization (TAMO).

For each candidate action (fans ON, fans OFF) the coupled temperature-moisture
model (coupled.py, the same library the simulator uses) predicts the basement
air temperature, structure temperature and humidity ratio after the decision
horizon. RH, dew point and surface margins are derived from those predicted
values. The action is then scored:

    utility = drying benefit - thermal preference penalty - energy - risk - switching

Normalization: every term is in **drying-equivalent grams of water per hour**.

  drying    = urgency x [ 0.5 x water removed            lasting benefit: vapor that left the basement
                         + 0.5 x RH relief ]              immediate material exposure: (RH_off - RH_on) at the
                                                          horizon, converted to the water that would cause that
                                                          RH change at today's temperature
              At constant temperature the two halves are equal. When cold air cools the
              basement, RH relief shrinks (cooling raises RH) or turns negative, but the
              simultaneous drop in humidity ratio is counted, so cold dry air is not
              rejected for its cooling alone.
  thermal   = k_eff x [ max(0, dD)^n x (1 + D_on/soft_tol)         preference, not energy (heuristic)
                        + swing_weight x max(0, |dT| - tol)^n ]
              dD = predicted distance from the preferred band, ON minus OFF. Negative dD (toward
              the band) is a bonus capped at half the drying benefit.
  energy    = (fan W + estimated HVAC W) / 1000 x the energy profile's g/kWh value
  risk      = risk_weight x increase in max(0, caution - predicted surface margin)^2 (heuristic)
  switching = small constant when the action changes the fan state

k_eff = k x profile multiplier x (1 - relaxation x urgency). Urgency comes from the
current RH and never touches hard limits, which the engine evaluates separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import coupled, psychro, thermal
from .airflow import VentilationEffect
from .config import AppConfig, EnergyProfile
from .psychro import AirState
from .units import c_to_f, delta_c_to_f, f_to_c, grams_to_gallons

HORIZONS = (15, 30, 60, 120)
PROFILE_THERMAL_MULT = {
    EnergyProfile.MAX_DRYING: 0.4,
    EnergyProfile.BALANCED: 1.0,
    EnergyProfile.TEMPERATURE_PRESERVATION: 2.5,
    EnergyProfile.ENERGY_SAVER: 1.0,
}


def urgency(rh_pct: float, low: float, high: float) -> float:
    """Continuous 0..1 drying urgency (smoothstep between low and high RH)."""
    x = min(1.0, max(0.0, (rh_pct - low) / (high - low)))
    return x * x * (3 - 2 * x)


def band_distance_f(t_f: float, lo: float, hi: float) -> float:
    return lo - t_f if t_f < lo else (t_f - hi if t_f > hi else 0.0)


def margin_band(margin_f: float, t) -> str:
    if margin_f <= 0:
        return "condensation"
    if margin_f < t.condensation_concern_f:
        return "concern"
    if margin_f < t.condensation_caution_f:
        return "caution"
    return "comfortable"


def attribute_rh_change(before: AirState, after: AirState) -> dict:
    """Split an RH change into the part from moisture content and the part from temperature."""
    rh_same_t = after.rh_at(before.t_c) * 100  # after's moisture at before's temperature
    dw = after.w - before.w
    from_w = rh_same_t - before.rh * 100
    from_t = after.rh * 100 - rh_same_t
    if abs(from_t) >= abs(from_w):  # the larger share names the cause
        cause = "cooling" if after.t_c < before.t_c else "warming"
    else:
        cause = "moisture_added" if dw > 0 else "drying"
    return {
        "rh_change_pct": (after.rh - before.rh) * 100,
        "w_change_g_kg": dw * 1000,
        "dew_point_change_f": delta_c_to_f(after.dew_point_c - before.dew_point_c),
        "from_moisture_pct": from_w,
        "from_temperature_pct": from_t,
        "cause": cause,
    }


@dataclass
class Outlook:
    """Coupled predictions for fans ON and fans OFF from the current state."""

    on: coupled.Trajectory
    off: coupled.Trajectory
    on_conservative: coupled.Trajectory  # thermal parameters pushed toward larger fan-driven change
    params: thermal.ThermalParams
    model: coupled.ModelParams
    state0: coupled.State
    inlet_t_c: float  # mass-weighted temperature of all air entering with fans ON
    inlet_w: float  # effective inlet humidity ratio of the fan air (mass-weighted, not RH-averaged)
    outdoor_fraction: float
    fan_kg_s: float

    @property
    def src_on(self) -> float:
        return self.inlet_t_c

    @property
    def tm0(self) -> float:
        return self.state0.tm_c

    def at(self, minutes: float, which: str = "on") -> float:
        tr = {"on": self.on, "off": self.off, "conservative": self.on_conservative}[which]
        return tr.t_c[tr.at(minutes)]


def outlook(indoor: AirState, outdoor: AirState, upstairs: AirState, effect: VentilationEffect | None,
            config: AppConfig, params: thermal.ThermalParams, fans_on_now: bool, horizon_min: float,
            minutes: tuple[int, ...] = HORIZONS) -> Outlook:
    mp = coupled.ModelParams.from_config(config, params)
    m_out, m_up = coupled.fan_flows(effect)
    bd_on = coupled.Boundary(outdoor, upstairs, m_out, m_up)
    bd_off = coupled.Boundary(outdoor, upstairs)
    g_on, src_on = coupled.inlet_conductance(mp, bd_on)
    g_off, src_off = coupled.inlet_conductance(mp, bd_off)
    g_now, src_now = (g_on, src_on) if fans_on_now else (g_off, src_off)
    st0 = coupled.initial_state(mp, indoor, g_now, src_now)
    mins = sorted(set(minutes) | {int(round(horizon_min))})
    on = coupled.predict(mp, st0, bd_on, mins)
    off = coupled.predict(mp, st0, bd_off, mins)
    if src_on < indoor.t_c:  # conservative path matters only for cold-air limits
        pc = params.conservative()
        mpc = coupled.ModelParams(pc, mp.volume_m3, mp.natural_ach, mp.source_g_h, mp.buffer_capacity_kg,
                                  mp.buffer_tau_h)
        # structure estimated as if the fans had just started: the warmest-air assumption, which predicts the
        # largest further drop (a running fan's quasi-steady estimate would be optimistic near the limit)
        onc = coupled.predict(mpc, coupled.initial_state(mpc, indoor, g_off, src_off), bd_on, mins)
    else:
        onc = on
    fan = m_out + m_up
    w_in = (m_out * outdoor.w + m_up * upstairs.w) / fan if fan else outdoor.w
    return Outlook(on, off, onc, params, mp, st0, src_on, w_in, (m_out / fan) if fan else 1.0, fan)


@dataclass
class TamoResult:
    utility_g_h: float
    benefit_g_h: float
    thermal_penalty_g_h: float
    energy_penalty_g_h: float
    risk_penalty_g_h: float
    switch_cost_g_h: float
    urgency: float
    k_eff: float
    fan_effect_f: float  # predicted fan-induced change of basement air temperature at the horizon
    band_change_f: float  # change in distance from the preferred band (+ = worse)
    beneficial_temperature: bool
    reasons: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def evaluate(indoor: AirState, outdoor: AirState, effect: VentilationEffect, look: Outlook, config: AppConfig,
             is_on: bool, surface_c: float | None, energy_value_g_kwh: float, want_on: bool = True,
             surfaces: list[dict] | None = None, air_follow: float = 0.2) -> TamoResult:
    t = config.temperature
    h = t.horizon_min
    hrs = h / 60
    i_on, i_off = look.on.at(h), look.off.at(h)
    rh_pct = indoor.rh * 100
    u = urgency(rh_pct, t.urgency_low_rh_pct, t.urgency_high_rh_pct) if t.urgency_enabled else 1.0
    m_da = coupled.dry_air_mass(look.model, look.state0)

    # predicted outcomes of each action
    t_on, t_off = look.on.t_c[i_on], look.off.t_c[i_off]
    w_on, w_off = look.on.w[i_on], look.off.w[i_off]
    rh_on, rh_off = look.on.rh[i_on] * 100, look.off.rh[i_off] * 100
    td_on, td_off = look.on.dew_point_c[i_on], look.off.dew_point_c[i_off]

    water_g_h = (w_off - w_on) * m_da * 1000 / hrs  # vapor removed by running instead of not running
    dw_drh = psychro.humidity_ratio(indoor.t_c, 0.01, indoor.p_kpa)  # humidity ratio per 1% RH at today's temp
    rh_relief_g_h = (rh_off - rh_on) * dw_drh * m_da * 1000 / hrs
    drying = 0.5 * water_g_h + 0.5 * rh_relief_g_h
    benefit = u * max(0.0, drying) if water_g_h > 0 else 0.0

    # thermal preference (direction-aware, continuous)
    on_f, off_f = c_to_f(t_on), c_to_f(t_off)
    lo, hi = t.preferred_min_f, t.preferred_max_f
    d_on, d_off = band_distance_f(on_f, lo, hi), band_distance_f(off_f, lo, hi)
    d_d = d_on - d_off
    swing = on_f - off_f
    n = t.penalty_exponent
    k_eff = t.k_g_h * PROFILE_THERMAL_MULT.get(config.control.energy_profile, 1.0) * (1 - t.urgency_relaxation * u)
    if d_d > 0:
        thermal_pen = k_eff * (d_d ** n) * (1 + d_on / t.soft_tolerance_f)
    else:
        thermal_pen = -min(k_eff * ((-d_d) ** n), 0.5 * benefit)  # bonus toward the band
    if d_d >= 0:
        thermal_pen += k_eff * t.swing_weight * max(0.0, abs(swing) - t.change_tolerance_f) ** n
    beneficial = d_d < -0.05

    energy = effect.total_power_w / 1000 * energy_value_g_kwh

    # condensation risk from predicted surface temperature and predicted LOCAL dew point.
    # Surface response: it follows a fraction f of the air-temperature change within the horizon
    # (learned from fan cycles; 0.2 assumed, a cautious value for slow concrete) plus the slow structure change.
    risk = 0.0
    cond: dict = {}
    f = air_follow
    dt_on, dt_off = t_on - indoor.t_c, t_off - indoor.t_c
    dtm_on, dtm_off = look.on.tm_c[i_on] - look.tm0, look.off.tm_c[i_off] - look.tm0
    dw_on, dw_off = w_on - indoor.w, w_off - indoor.w
    probes = [s for s in (surfaces or []) if s.get("t_c") is not None]
    if not probes and surface_c is not None:
        probes = [{"name": "surface", "t_c": surface_c, "local": None, "exposure": 1.0}]
    per = []
    for sp in probes:
        loc = sp.get("local") or indoor
        exp = float(sp.get("exposure", 1.0))

        def local_dew(dt: float, dw: float) -> float:
            # moisture added reaches the surface fully; drying reaches a pocket only partly (exposure)
            w = loc.w + (dw if dw > 0 else dw * exp)
            tt = loc.t_c + dt
            return AirState.from_t_w(tt, max(1e-6, min(w, psychro.humidity_ratio(tt, 1.0, loc.p_kpa))),
                                     loc.p_kpa).dew_point_c
        s_on = sp["t_c"] + f * dt_on + (1 - f) * dtm_on
        s_off = sp["t_c"] + f * dt_off + (1 - f) * dtm_off
        m_now = delta_c_to_f(sp["t_c"] - loc.dew_point_c)
        m_on = delta_c_to_f(s_on - local_dew(dt_on, dw_on))
        m_off = delta_c_to_f(s_off - local_dew(dt_off, dw_off))
        per.append({"name": sp["name"], "zone": sp.get("zone"), "margin_f": m_now, "margin_on_f": m_on,
                    "margin_off_f": m_off, "surface_on_f": c_to_f(s_on), "surface_off_f": c_to_f(s_off)})
    if per:
        def r(m: float) -> float:
            return max(0.0, t.condensation_caution_f - m) ** 2
        risk = t.risk_weight_g_h * max(max(0.0, r(x["margin_on_f"]) - r(x["margin_off_f"])) for x in per)
        w0 = min(per, key=lambda x: x["margin_on_f"])
        cond = {"indoor_margin_f": w0["margin_f"], "indoor_margin_on_f": w0["margin_on_f"],
                "indoor_margin_off_f": w0["margin_off_f"], "band": margin_band(min(x["margin_f"] for x in per), t),
                "band_on": margin_band(w0["margin_on_f"], t), "worst_surface": w0["name"], "surfaces": per,
                "air_follow_fraction": f}

    switching = t.switch_cost_g_h if want_on != is_on else 0.0
    utility = benefit - thermal_pen - energy - risk - switching

    reasons: list[str] = []
    if water_g_h > 0:
        reasons.append("MOISTURE_BENEFIT_POSITIVE")
    if u >= 0.75:
        reasons.append("HUMIDITY_URGENCY_HIGH")
    if beneficial:
        reasons.append("TEMPERATURE_CHANGE_BENEFICIAL")
    if rh_on > rh_pct + 0.5 and water_g_h > 0:
        reasons.append("RH_RISES_DURING_DRYING")
    if indoor.rh_at(f_to_c((lo + hi) / 2)) * 100 < rh_pct - 5 and c_to_f(indoor.t_c) < lo:
        reasons.append("RH_ELEVATED_BY_COOL_BASEMENT")

    removal = effect.net_removal_g_h
    details = {
        "removal_g_h": removal,  # instantaneous air-stream estimate
        "removal_l_h": removal / 1000,
        "removal_gal_h": grams_to_gallons(removal),
        "removal_gal_day": grams_to_gallons(removal) * 24,
        "removal_horizon_g": water_g_h * hrs,
        "water_removed_g_h": water_g_h,
        "rh_relief_g_h": rh_relief_g_h,
        "inlet_w_g_kg": look.inlet_w * 1000,
        "inlet_t_c": look.inlet_t_c,
        "outdoor_air_fraction": look.outdoor_fraction,
        "rh_pct": rh_pct,
        "rh_at_center_pct": indoor.rh_at(f_to_c((lo + hi) / 2)) * 100,
        "horizon_min": h,
        "predicted": {
            "on": {"t_f": on_f, "rh_pct": rh_on, "dew_point_f": c_to_f(td_on), "w_g_kg": w_on * 1000},
            "off": {"t_f": off_f, "rh_pct": rh_off, "dew_point_f": c_to_f(td_off), "w_g_kg": w_off * 1000},
        },
        "pred_on_f": on_f, "pred_off_f": off_f,
        "band_distance_on_f": d_on, "band_distance_off_f": d_off,
        "sensible_heat_w": thermal.sensible_heat_w(look.fan_kg_s, look.inlet_t_c, indoor.t_c),
        "condensation": cond,
        "energy_value_g_kwh": energy_value_g_kwh,
        "fan_power_w": effect.fan_power_w,
        "hvac_penalty_w": effect.conditioned_air_penalty_w,
    }
    details["sensible_heat_btu_h"] = details["sensible_heat_w"] * 3.412142
    return TamoResult(utility, benefit, thermal_pen, energy, risk, switching, u, k_eff, swing, d_d, beneficial,
                      reasons, details)


def opportunity(safety_blocked: bool, removal: float, utility: float, start: float, res: TamoResult | None) -> str:
    """Opportunity quality, from the same evaluation that drives the decision."""
    if safety_blocked:
        return "unsafe"
    if removal <= 0 or res is None or utility <= 0:
        return "unfavorable"
    if utility < start:
        return "marginal"
    if utility >= 3 * start and res.thermal_penalty_g_h <= 0.2 * max(res.benefit_g_h, 1e-9):
        return "excellent"
    return "good"


def forecast_choice(forecast: list[tuple[datetime, AirState]], now: datetime, evaluate_at, current: TamoResult,
                    config: AppConfig) -> dict | None:
    """Best forecast window within the horizon, and whether it is materially better than now."""
    t = config.temperature
    best = None
    for ts, out in forecast:
        dt_h = (ts - now).total_seconds() / 3600
        if dt_h <= 0.25 or dt_h > t.forecast_horizon_h:
            continue
        r = evaluate_at(out)
        if r is not None and (best is None or r.utility_g_h > best[1].utility_g_h):
            best = (ts, r, out)
    if best is None:
        return None
    ts, r, out = best
    return {"ts": ts.isoformat(), "hours_ahead": (ts - now).total_seconds() / 3600, "utility_g_h": r.utility_g_h,
            "thermal_penalty_g_h": r.thermal_penalty_g_h, "removal_g_h": r.details["removal_g_h"],
            "outdoor_t_c": out.t_c, "outdoor_dew_point_c": out.dew_point_c,
            "better": r.utility_g_h >= max(1.25 * current.utility_g_h, current.utility_g_h + 50)
            and r.details["removal_g_h"] > 0}
