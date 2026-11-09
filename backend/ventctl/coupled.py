"""Coupled temperature-moisture model of the basement: the one calculation library
shared by the live decision engine, the simulator and the mock basement.

State: air temperature, structure temperature, humidity ratio of the air, and
water held in a simple materials buffer. One step:

  1. dry-air exchange: fan outdoor path, fan upstairs path, natural infiltration
  2. water vapor carried in and out with that air (humidity ratio, never RH)
  3. internal moisture sources and the materials buffer (first-order reservoir)
  4. new humidity ratio
  5. new air and structure temperatures (thermal.py, exact two-node solution)
  6. vapor above saturation at the new temperature condenses
  7. RH and dew point are derived from the resulting temperature and humidity ratio

Predictions for a candidate action (fans ON or OFF) run the same steps with the
boundary conditions held constant, so a cold, dry inflow shows both effects at
once: lower temperature (RH up) and lower humidity ratio (RH down). The engine
scores the predicted RH, dew point and surface margins, not the temperature
change in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import psychro, thermal
from .airflow import VentilationEffect
from .psychro import AirState
from .units import M3_PER_FT3, STD_PRESSURE_KPA


@dataclass
class ModelParams:
    thermal: thermal.ThermalParams
    volume_m3: float
    natural_ach: float
    source_g_h: float = 0.0  # internal moisture sources (damp slab, intrusion, occupants)
    buffer_capacity_kg: float = 0.0  # water the materials exchange between 0% and 100% RH; 0 = off
    buffer_tau_h: float = 12.0

    @classmethod
    def from_config(cls, config, tp: thermal.ThermalParams) -> "ModelParams":
        h = config.house
        return cls(tp, h.basement_volume_ft3 * M3_PER_FT3, h.natural_ach,
                   h.moisture_source_pints_day * 473.176473 / 24, h.moisture_buffer_kg, h.moisture_buffer_tau_h)


@dataclass
class State:
    t_c: float
    tm_c: float  # structure temperature (not measured)
    w: float
    buffer_kg: float
    p_kpa: float = STD_PRESSURE_KPA

    def air(self) -> AirState:
        return AirState.from_t_w(self.t_c, self.w, self.p_kpa)

    @property
    def rh(self) -> float:
        return psychro.rh_from_humidity_ratio(self.t_c, self.w, self.p_kpa)


@dataclass
class Boundary:
    outdoor: AirState
    upstairs: AirState
    fan_out_kg_s: float = 0.0  # dry air entering from outdoors via the fans' flow paths
    fan_up_kg_s: float = 0.0  # dry air drawn from upstairs
    t_eq_c: float | None = None  # structure equilibrium (ground/house); None = hold current
    dehumidifier_g_h: float = 0.0


@dataclass
class StepResult:
    d_fan_out: float = 0.0  # kg water, + = added to the basement
    d_fan_up: float = 0.0
    d_inf: float = 0.0
    d_src: float = 0.0
    d_dh: float = 0.0
    d_buf: float = 0.0  # + = released by materials into the air
    d_cond: float = 0.0


def fan_flows(effect: VentilationEffect | None) -> tuple[float, float]:
    """Split the fans' dry-air flow into outdoor and upstairs paths [kg/s]."""
    if effect is None or effect.dry_air_kg_h <= 0:
        return 0.0, 0.0
    total = effect.dry_air_kg_h / 3600
    qsum = effect.outdoor_path_cfm + effect.upstairs_path_cfm
    fo = effect.outdoor_path_cfm / qsum if qsum else 1.0
    return total * fo, total * (1 - fo)


def dry_air_mass(mp: ModelParams, st: State) -> float:
    return mp.volume_m3 * psychro.dry_air_density(st.t_c, st.w, st.p_kpa)


def natural_kg_s(mp: ModelParams, outdoor: AirState) -> float:
    return mp.natural_ach * mp.volume_m3 / 3600 * psychro.dry_air_density(outdoor.t_c, outdoor.w, outdoor.p_kpa)


def inlet_conductance(mp: ModelParams, bd: Boundary) -> tuple[float, float]:
    """(G [W/K], mass-weighted source temperature) of all incoming air."""
    m_nat = natural_kg_s(mp, bd.outdoor)
    m = bd.fan_out_kg_s + m_nat + bd.fan_up_kg_s
    if m <= 0:
        return 0.0, bd.outdoor.t_c
    t = ((bd.fan_out_kg_s + m_nat) * bd.outdoor.t_c + bd.fan_up_kg_s * bd.upstairs.t_c) / m
    return thermal.conductance_w_k(m), t


def advance(mp: ModelParams, st: State, bd: Boundary, dt_s: float, m_da: float | None = None) -> StepResult:
    """Advance the state in place by dt_s. Returns the water flows of this step [kg]."""
    out, up = bd.outdoor, bd.upstairs
    m_da = m_da if m_da is not None else dry_air_mass(mp, st)
    m_nat = natural_kg_s(mp, out)
    r = StepResult()
    r.d_fan_out = bd.fan_out_kg_s * (out.w - st.w) * dt_s
    r.d_fan_up = bd.fan_up_kg_s * (up.w - st.w) * dt_s
    r.d_inf = m_nat * (out.w - st.w) * dt_s
    r.d_src = mp.source_g_h / 1000 / 3600 * dt_s
    r.d_dh = -bd.dehumidifier_g_h / 1000 / 3600 * dt_s
    if mp.buffer_capacity_kg > 0:
        flux = (st.buffer_kg - mp.buffer_capacity_kg * min(st.rh, 1.0)) / (mp.buffer_tau_h * 3600)
        r.d_buf = flux * dt_s
        st.buffer_kg -= r.d_buf
    st.w = max(1e-6, st.w + (r.d_fan_out + r.d_fan_up + r.d_inf + r.d_src + r.d_dh + r.d_buf) / m_da)

    g, src = inlet_conductance(mp, bd)
    teq = st.tm_c if bd.t_eq_c is None else bd.t_eq_c
    st.t_c, st.tm_c = thermal.step(mp.thermal, st.t_c, st.tm_c, g, src, teq, dt_s)

    w_sat = psychro.humidity_ratio(st.t_c, 1.0, st.p_kpa)
    if st.w > w_sat:
        r.d_cond = -(st.w - w_sat) * m_da
        st.w = w_sat
    return r


@dataclass
class Trajectory:
    minutes: list[int]
    t_c: list[float] = field(default_factory=list)
    tm_c: list[float] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    rh: list[float] = field(default_factory=list)
    dew_point_c: list[float] = field(default_factory=list)
    water_kg: float = 0.0  # net water the fans moved out over the longest horizon (+ = removed)

    def at(self, minute: float) -> int:
        for i, m in enumerate(self.minutes):
            if m >= minute:
                return i
        return len(self.minutes) - 1


def predict(mp: ModelParams, st0: State, bd: Boundary, minutes: list[int], step_s: float = 300.0) -> Trajectory:
    """Run the coupled model forward with constant boundary conditions."""
    st = State(st0.t_c, st0.tm_c, st0.w, st0.buffer_kg, st0.p_kpa)
    tr = Trajectory(sorted(minutes))
    m_da = dry_air_mass(mp, st0)
    t = 0.0
    for m in tr.minutes:
        while t < m * 60 - 1e-6:
            dt = min(step_s, m * 60 - t)
            r = advance(mp, st, bd, dt, m_da)
            tr.water_kg -= r.d_fan_out + r.d_fan_up
            t += dt
        a = st.air()
        tr.t_c.append(st.t_c)
        tr.tm_c.append(st.tm_c)
        tr.w.append(st.w)
        tr.rh.append(a.rh)
        tr.dew_point_c.append(a.dew_point_c)
    return tr


def initial_state(mp: ModelParams, indoor: AirState, g_now: float, src_now: float) -> State:
    """Live state from sensors: structure estimated from the quasi-steady air balance, buffer at equilibrium."""
    tm = thermal.structure_estimate(mp.thermal, indoor.t_c, g_now, src_now)
    return State(indoor.t_c, tm, indoor.w, mp.buffer_capacity_kg * indoor.rh, indoor.p_kpa)
