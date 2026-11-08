"""Dynamic basement moisture simulation and strategy comparison.

Model (single well-mixed air zone, fixed dry-air mass M):

  M dW/dt = m_vent_out (W_out - W) + m_vent_up (W_up - W) + m_nat (W_out - W)
            + G_sources - D_dehumidifier + F_buffer - C_condensation

  * m_vent_*  dry-air mass flows through the fans, split into outdoor and
              upstairs paths exactly as the live controller estimates them
  * m_nat     natural infiltration (configured air changes per hour)
  * G         constant moisture source (slab, walls, leaks), configurable
  * F_buffer  exchange with a moisture reservoir representing wood/concrete:
              flux to air = (S - Cap * RH) / tau. Set capacity 0 to disable.
  * C         any vapor above saturation condenses out (tracked in the ledger)

Basement temperature relaxes toward a daily profile (mean +/- variation) with
time constant tau_thermal and is nudged by ventilation air through an
effective thermal mass. RH is always derived from W at the simulated
temperature, never held constant.

Every term is accumulated in a ledger so tests can verify mass conservation.
This is a simplified model for comparing strategies, not a prediction.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from . import psychro
from .airflow import ventilation_effect
from .climate import ClimateProfile
from .config import AppConfig, EnergyProfile, Fan, FanGroup, FanType, Mode
from .engine import GroupState, decide
from .psychro import AirState
from .sensors import Conditions
from .units import (
    FT2_TO_M2, FT_TO_M, M3_PER_FT3, c_to_f, cfm_to_m3s, delta_f_to_c, f_to_c, grams_to_gallons,
    pints_per_day_to_g_per_h,
)


class SimParams(BaseModel):
    floor_area_ft2: float = Field(1000, gt=10, le=20000)
    ceiling_ft: float = Field(7, gt=3, le=20)
    initial_temp_f: float = 79
    initial_rh_pct: float = Field(75, ge=5, le=100)
    basement_mean_temp_f: float | None = Field(None, description="Defaults to the profile's suggestion")
    temp_variation_f: float = Field(1.0, ge=0, le=15, description="Daily +/- swing of basement temperature")
    thermal_tau_h: float = Field(6, gt=0.1)
    thermal_mass_factor: float = Field(30, ge=1, description="Effective heat capacity as a multiple of the air's")
    fan_count: int = Field(2, ge=0, le=20)
    fan_cfm: float = Field(85, gt=0, le=5000, description="Actual airflow per fan")
    fan_w: float = Field(28, ge=0)
    fan_type: FanType = FanType.EXHAUST
    balanced: bool = Field(False, description="Add one matching intake fan per exhaust fan")
    moisture_source_pints_day: float = Field(6, ge=0, le=500)
    natural_ach: float = Field(0.3, ge=0, le=10)
    conditioned_air_fraction: float = Field(0.10, ge=0, le=1)
    surface_temp_f: float | None = Field(None, description="Coldest surface; defaults to the profile's suggestion")
    electricity_cost_per_kwh: float = Field(0.13, ge=0)
    buffer_capacity_kg: float = Field(25, ge=0, description="Moisture the materials exchange between 0% and 100% RH")
    buffer_tau_h: float = Field(12, gt=0.1)
    dehumidifier: bool = False
    dehumidifier_pints_day: float = Field(35, ge=0)
    dehumidifier_w: float = Field(500, ge=0)
    dehumidifier_setpoint_pct: float = Field(50, ge=30, le=80)
    duration_h: float = Field(72, gt=0, le=24 * 31)
    step_s: float = Field(60, ge=10, le=600)
    start_hour: float = Field(0, ge=0, lt=24)
    day_hours: tuple[float, float] = (8, 20)
    night_hours: tuple[float, float] = (20, 8)
    output_every_min: float = Field(10, ge=1)


@dataclass
class Ledger:
    """Cumulative moisture flows into the basement air+buffer system [kg]. + = added."""

    fan_outdoor: float = 0.0
    fan_upstairs: float = 0.0
    infiltration: float = 0.0
    sources: float = 0.0
    dehumidifier: float = 0.0  # stored as negative (removal)
    condensed: float = 0.0  # stored as negative (removal)

    def total(self) -> float:
        return (self.fan_outdoor + self.fan_upstairs + self.infiltration + self.sources
                + self.dehumidifier + self.condensed)


class BasementModel:
    """Stateful physical model. Used by the strategy simulator and the mock hardware."""

    def __init__(self, params: SimParams, profile: ClimateProfile, fans: list[Fan], house,
                 upstairs: AirState | None = None):
        self.p = params
        self.profile = profile
        self.fans = fans
        self.house = house
        self.upstairs = upstairs
        vol = params.floor_area_ft2 * FT2_TO_M2 * params.ceiling_ft * FT_TO_M
        self.volume_m3 = vol
        self.t_c = f_to_c(params.initial_temp_f)
        self.w = psychro.humidity_ratio(self.t_c, params.initial_rh_pct / 100)
        self.m_da = vol * psychro.dry_air_density(self.t_c, self.w)
        self.mean_t_c = f_to_c(params.basement_mean_temp_f if params.basement_mean_temp_f is not None
                               else profile.basement_temp_f)
        self.surface_c = f_to_c(params.surface_temp_f if params.surface_temp_f is not None
                                else profile.surface_temp_f)
        self.buffer_kg = params.buffer_capacity_kg * self.rh  # starts in equilibrium
        self.ledger = Ledger()
        self.dehum_on = False
        self.dehum_kwh = 0.0

    @property
    def rh(self) -> float:
        return psychro.rh_from_humidity_ratio(self.t_c, self.w)

    def air(self) -> AirState:
        return AirState.from_t_w(self.t_c, self.w)

    def outdoor_at(self, hour: float) -> AirState:
        t, td = self.profile.at(hour)
        return AirState.from_t_rh(t, psychro.rh_from_dew_point(t, td))

    def water_inventory(self) -> float:
        return self.m_da * self.w + self.buffer_kg

    def step(self, hour: float, dt_s: float, fans_on: list[Fan], outdoor: AirState | None = None) -> dict:
        p = self.p
        out = outdoor or self.outdoor_at(hour)
        ind = self.air()
        up = self.upstairs or AirState.from_t_rh(f_to_c(self.house.upstairs_temp_f), self.house.upstairs_rh_pct / 100)

        m_out = m_up = 0.0
        eff = None
        if fans_on:
            eff = ventilation_effect(ind, out, fans_on, self.house, up)
            total = eff.dry_air_kg_h / 3600
            qsum = eff.outdoor_path_cfm + eff.upstairs_path_cfm
            m_out = total * eff.outdoor_path_cfm / qsum if qsum else 0
            m_up = total * eff.upstairs_path_cfm / qsum if qsum else 0
        m_nat = p.natural_ach * self.volume_m3 / 3600 * psychro.dry_air_density(out.t_c, out.w)

        d_fan_out = m_out * (out.w - self.w) * dt_s
        d_fan_up = m_up * (up.w - self.w) * dt_s
        d_inf = m_nat * (out.w - self.w) * dt_s
        d_src = pints_per_day_to_g_per_h(p.moisture_source_pints_day) / 1000 / 3600 * dt_s

        # dehumidifier with 2% deadband
        rh_now = self.rh * 100
        if p.dehumidifier:
            if rh_now > p.dehumidifier_setpoint_pct + 2:
                self.dehum_on = True
            elif rh_now <= p.dehumidifier_setpoint_pct:
                self.dehum_on = False
        d_dh = 0.0
        if self.dehum_on:
            # capacity derates at lower RH (rough: proportional to RH/60, capped)
            rate = pints_per_day_to_g_per_h(p.dehumidifier_pints_day) / 1000 / 3600 * min(1.2, rh_now / 60)
            d_dh = -rate * dt_s
            self.dehum_kwh += p.dehumidifier_w * dt_s / 3.6e6

        # buffer exchange
        d_buf = 0.0
        if p.buffer_capacity_kg > 0:
            flux = (self.buffer_kg - p.buffer_capacity_kg * min(self.rh, 1.0)) / (p.buffer_tau_h * 3600)
            d_buf = flux * dt_s
            self.buffer_kg -= d_buf

        self.w += (d_fan_out + d_fan_up + d_inf + d_src + d_dh + d_buf) / self.m_da
        self.w = max(self.w, 1e-6)

        # temperature
        hour_angle = 2 * math.pi * (hour - 17) / 24  # basement lags outdoors
        t_prof = self.mean_t_c + delta_f_to_c(p.temp_variation_f) * math.cos(hour_angle)
        dT = (t_prof - self.t_c) / (p.thermal_tau_h * 3600) * dt_s
        m_air_in = m_out + m_nat
        if m_air_in > 0:
            c_eff = self.m_da * psychro.CP_DA * p.thermal_mass_factor
            dT += m_air_in * psychro.CP_DA * (out.t_c - self.t_c) / c_eff * dt_s
            if m_up > 0:
                dT += m_up * psychro.CP_DA * (up.t_c - self.t_c) / c_eff * dt_s
        self.t_c += dT

        # condensation: vapor above saturation leaves the air
        w_sat = psychro.humidity_ratio(self.t_c, 1.0)
        d_cond = 0.0
        if self.w > w_sat:
            d_cond = -(self.w - w_sat) * self.m_da
            self.w = w_sat

        L = self.ledger
        L.fan_outdoor += d_fan_out
        L.fan_upstairs += d_fan_up
        L.infiltration += d_inf
        L.sources += d_src
        L.dehumidifier += d_dh
        L.condensed += d_cond
        return {"outdoor": out, "effect": eff, "fan_out_kg": d_fan_out, "fan_up_kg": d_fan_up, "m_up": m_up}


# ---------------------------------------------------------------- strategies

STRATEGIES = {
    "continuous": ("A. Continuous (24/7)", "Fans run all the time."),
    "daytime": ("B. Daytime only", "Fans run during the configured daytime hours."),
    "nighttime": ("C. Nighttime only", "Fans run during the configured nighttime hours."),
    "dewpoint": ("D. Dew-point controlled", "Runs when moisture and safety thresholds permit (no efficiency minimum)."),
    "optimized": ("E. Efficiency optimized", "Full decision logic incl. fan cost, conditioned-air loss and safety."),
    "off": ("F. Ventilation off", "No forced ventilation; natural leakage and moisture sources still act."),
}


def _in_window(h: float, win: tuple[float, float]) -> bool:
    a, b = win
    return a <= h < b if a <= b else (h >= a or h < b)


def sim_fans(params: SimParams) -> list[Fan]:
    fans = [Fan(id=f"f{i}", name=f"Fan {i + 1}", type=params.fan_type, rated_cfm=params.fan_cfm,
                actual_cfm=params.fan_cfm, rated_w=params.fan_w, group="main") for i in range(params.fan_count)]
    if params.balanced:
        other = FanType.INTAKE if params.fan_type == FanType.EXHAUST else FanType.EXHAUST
        fans += [Fan(id=f"b{i}", name=f"Balancing fan {i + 1}", type=other, rated_cfm=params.fan_cfm,
                     actual_cfm=params.fan_cfm, rated_w=params.fan_w, group="main") for i in range(params.fan_count)]
    return fans


def _sim_config(base: AppConfig, params: SimParams, fans: list[Fan], strategy: str) -> AppConfig:
    cfg = base.model_copy(deep=True)
    cfg.fans = fans
    cfg.groups = [FanGroup(id="main", name="Simulated")]
    cfg.house.conditioned_air_fraction = params.conditioned_air_fraction
    cfg.house.electricity_cost_per_kwh = params.electricity_cost_per_kwh
    cfg.mode = Mode.SIMULATION
    if strategy == "dewpoint":
        cfg.control.energy_profile = EnergyProfile.MAX_DRYING
    elif strategy == "optimized" and cfg.control.energy_profile == EnergyProfile.MAX_DRYING:
        cfg.control.energy_profile = EnergyProfile.BALANCED
    return cfg


@dataclass
class StrategyResult:
    id: str
    name: str
    description: str
    summary: dict
    series: list[dict] = field(default_factory=list)
    reasons: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def run_strategy(strategy: str, params: SimParams, profile: ClimateProfile, base: AppConfig) -> StrategyResult:
    fans = sim_fans(params)
    cfg = _sim_config(base, params, fans, strategy)
    group = cfg.groups[0]
    model = BasementModel(params, profile, fans, cfg.house)
    inv0 = model.water_inventory()

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    steps = int(round(params.duration_h * 3600 / params.step_s))
    dt = params.step_s
    out_every = max(1, int(params.output_every_min * 60 / dt))
    state = GroupState(is_on=False, verified=True, last_change=None)
    margin_c = delta_f_to_c(cfg.control.surface_margin_f)

    rh_sum = 0.0
    rh_max = 0.0
    h60 = h70 = 0.0
    on_h = 0.0
    useful_on_h = 0.0
    unfavorable_on_h = 0.0
    cond_risk_h = 0.0
    surface_wet_h = 0.0
    fan_kwh = 0.0
    hvac_kwh = 0.0
    cond_air_m3 = 0.0
    cycles = 0
    reasons: dict[str, int] = {}
    series: list[dict] = []
    last_out = None

    for k in range(steps):
        sim_h = params.start_hour + k * dt / 3600
        local_h = sim_h % 24
        out = model.outdoor_at(sim_h)
        ind = model.air()

        if strategy == "continuous":
            want = True
        elif strategy == "daytime":
            want = _in_window(local_h, params.day_hours)
        elif strategy == "nighttime":
            want = _in_window(local_h, params.night_hours)
        elif strategy == "off":
            want = False
        else:
            cond = Conditions(indoor=ind, indoor_label="simulated basement", indoor_problem=None, outdoor=out,
                              outdoor_problem=None, surface_c=model.surface_c, surface_source="measured",
                              surface_label="simulated surface", upstairs=None, diff_pressure_pa=None)
            d = decide(cond, state, cfg, group, t0 + timedelta(seconds=k * dt), local_hour=local_h)
            want = d.target_on
            code = d.reason_code.split(":")[-1]
            reasons[code] = reasons.get(code, 0) + 1
        if want != state.is_on:
            state.is_on = want
            state.last_change = t0 + timedelta(seconds=k * dt)
            if want:
                cycles += 1

        res = model.step(sim_h, dt, fans if state.is_on else [], outdoor=out)
        hrs = dt / 3600
        rh = model.rh * 100
        rh_sum += rh
        rh_max = max(rh_max, rh)
        h60 += hrs if rh > 60 else 0
        h70 += hrs if rh > 70 else 0
        if model.air().dew_point_c >= model.surface_c:
            surface_wet_h += hrs
        if state.is_on:
            on_h += hrs
            e = res["effect"]
            fan_kwh += e.fan_power_w * hrs / 1000
            hvac_kwh += e.conditioned_air_penalty_w * hrs / 1000
            cond_air_m3 += cfm_to_m3s(e.upstairs_path_cfm) * dt
            if e.net_removal_g_h > 0:
                useful_on_h += hrs
            else:
                unfavorable_on_h += hrs
            if model.surface_c - out.dew_point_c < margin_c:
                cond_risk_h += hrs
        if k % out_every == 0 or k == steps - 1:
            a = model.air()
            series.append({
                "h": round(sim_h - params.start_hour, 3),
                "rh": round(rh, 2),
                "t_f": round(c_to_f(a.t_c), 2),
                "td_f": round(c_to_f(a.dew_point_c), 2),
                "out_t_f": round(c_to_f(out.t_c), 2),
                "out_td_f": round(c_to_f(out.dew_point_c), 2),
                "on": 1 if state.is_on else 0,
                "removed_gal": round(grams_to_gallons(-(model.ledger.fan_outdoor + model.ledger.fan_upstairs) * 1000), 4),
            })
        last_out = out

    L = model.ledger
    net_fan_removed_kg = -(L.fan_outdoor + L.fan_upstairs)
    inv1 = model.water_inventory()
    elec_cost = (fan_kwh + hvac_kwh + model.dehum_kwh) * params.electricity_cost_per_kwh
    removed_gal = grams_to_gallons(net_fan_removed_kg * 1000)
    summary = {
        "final_rh": model.rh * 100,
        "mean_rh": rh_sum / max(steps, 1),
        "max_rh": rh_max,
        "hours_above_60": h60,
        "hours_above_70": h70,
        "fan_net_removed_kg": net_fan_removed_kg,
        "fan_net_removed_gal": removed_gal,
        "fan_outdoor_removed_kg": -L.fan_outdoor,
        "fan_upstairs_removed_kg": -L.fan_upstairs,
        "infiltration_added_kg": L.infiltration,
        "sources_added_kg": L.sources,
        "dehumidifier_removed_kg": -L.dehumidifier,
        "condensed_kg": -L.condensed,
        "fan_runtime_h": on_h,
        "useful_runtime_pct": (useful_on_h / on_h * 100) if on_h else None,
        "unfavorable_runtime_h": unfavorable_on_h,
        "switch_cycles": cycles,
        "fan_kwh": fan_kwh,
        "fan_cost": fan_kwh * params.electricity_cost_per_kwh,
        "conditioned_air_ft3": cond_air_m3 / M3_PER_FT3,
        "conditioned_air_kwh": hvac_kwh,
        "conditioned_air_cost": hvac_kwh * params.electricity_cost_per_kwh,
        "dehumidifier_kwh": model.dehum_kwh,
        "dehumidifier_cost": model.dehum_kwh * params.electricity_cost_per_kwh,
        "total_cost": elec_cost,
        "cost_per_gallon_removed": ((fan_kwh + hvac_kwh) * params.electricity_cost_per_kwh / removed_gal)
        if removed_gal > 1e-6 else None,
        "condensation_risk_hours": cond_risk_h,
        "surface_at_dewpoint_hours": surface_wet_h,
        "mass_balance_error_kg": (inv1 - inv0) - L.total(),
        "final_outdoor_dewpoint_f": c_to_f(last_out.dew_point_c) if last_out else None,
    }
    name, desc = STRATEGIES[strategy]
    return StrategyResult(strategy, name, desc, summary, series, reasons)


def compare(params: SimParams, profile: ClimateProfile, base: AppConfig,
            strategies: list[str] | None = None) -> dict:
    ids = strategies or list(STRATEGIES)
    results = [run_strategy(s, params, profile, base) for s in ids]
    return {
        "kind": "modeled_simulation",
        "label": "Modeled result. Depends on assumed moisture sources, buffering and airflow.",
        "profile": profile.summary(),
        "params": params.model_dump(),
        "results": [r.to_dict() for r in results],
        "insights": insights(results),
    }


def insights(results: list[StrategyResult]) -> list[str]:
    if not results:
        return []
    out: list[str] = []
    by = {r.id: r for r in results}
    most = max(results, key=lambda r: r.summary["fan_net_removed_kg"])
    low_rh = min(results, key=lambda r: r.summary["mean_rh"])
    out.append(f"Lowest average humidity: {low_rh.name} ({low_rh.summary['mean_rh']:.1f}% RH).")
    if most.summary["fan_net_removed_kg"] > 0:
        out.append(f"Most vapor removed by fans: {most.name} ({most.summary['fan_net_removed_gal']:.2f} gal).")
    risky = [r for r in results if r.summary["condensation_risk_hours"] > 0.05]
    if risky:
        names = ", ".join(f"{r.name} ({r.summary['condensation_risk_hours']:.1f} h)" for r in risky)
        out.append(f"Ran fans while outdoor dew point was within the condensation margin of the surface: {names}. "
                   "Removing the most moisture is not the same as being safe.")
    adders = [r for r in results if r.summary["fan_outdoor_removed_kg"] < -0.01]
    if adders:
        names = ", ".join(f"{r.name} (+{grams_to_gallons(-r.summary['fan_outdoor_removed_kg'] * 1000):.2f} gal)"
                          for r in adders)
        out.append(f"Outdoor air brought in by the fans added moisture overall in: {names}.")
    up = [r for r in results if r.summary["fan_upstairs_removed_kg"] > 0.05]
    if up:
        top = max(up, key=lambda r: r.summary["fan_upstairs_removed_kg"])
        out.append(f"Part of the drying comes from conditioned air pulled down from upstairs (up to "
                   f"{grams_to_gallons(top.summary['fan_upstairs_removed_kg'] * 1000):.2f} gal in {top.name}). "
                   "Your air conditioner pays for that, shown as conditioned-air cost.")
    priced = [r for r in results if r.summary["cost_per_gallon_removed"] is not None and r.summary["fan_net_removed_kg"] > 0]
    if priced:
        cheap = min(priced, key=lambda r: r.summary["cost_per_gallon_removed"])
        out.append(f"Lowest cost per gallon removed: {cheap.name} (${cheap.summary['cost_per_gallon_removed']:.2f}/gal, "
                   "including estimated conditioned-air loss).")
    if "off" in by and "optimized" in by:
        d = by["off"].summary["mean_rh"] - by["optimized"].summary["mean_rh"]
        if abs(d) < 1:
            out.append("The optimized controller barely changed humidity versus no ventilation: under these "
                       "conditions outdoor air rarely offers a useful drying benefit. Consider a dehumidifier "
                       "or water-intrusion fixes.")
    return out
