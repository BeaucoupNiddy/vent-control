"""Airflow and moisture-transfer model for a set of running fans.

Flow paths (volumetric flows at the fan):
  * exhaust fans push basement air outdoors (Qe)
  * intake fans pull outdoor air in (Qi)
  * if Qe > Qi the shortfall (Qe - Qi) is made up through passive vents and
    leaks. An assumed fraction f of that shortfall comes from the conditioned
    upstairs (through the floor); the rest comes from outdoors.
  * the upstairs air costs HVAC energy only while the heating or AC is running
    (judged from outdoor temperature, see HouseSettings)
  * if Qi > Qe the basement is pressurized and the excess pushes basement air
    into the house / out through leaks. Flagged as a warning.

Moisture removal = dry-air mass flow x (W_basement - W_incoming), split by path.
The upstairs fraction is an assumption unless pressure/airflow measurements
support it, so every result carries ``conditioned_air_assumed``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import psychro
from .config import Fan, FanType, HouseSettings
from .psychro import AirState
from .units import c_to_f, cfm_to_m3s, g_per_h_to_gal_per_day, grams_to_gallons


@dataclass
class VentilationEffect:
    exhaust_cfm: float
    intake_cfm: float
    outdoor_path_cfm: float  # outdoor air entering the basement
    upstairs_path_cfm: float  # conditioned air drawn down from upstairs
    pressurized: bool
    dry_air_kg_h: float
    removal_outdoor_g_h: float  # + = drying, - = moisture addition
    removal_upstairs_g_h: float
    net_removal_g_h: float
    net_removal_gal_day: float
    net_removal_gal_h: float
    fan_power_w: float
    conditioned_air_penalty_w: float  # extra HVAC electricity to replace lost conditioned air
    total_power_w: float
    efficiency_g_per_kwh: float | None  # net removal per kWh (fan + HVAC penalty); None if no power
    cost_per_hour: float
    cost_per_gallon: float | None
    conditioned_air_assumed: bool
    upstairs_assumed: bool

    def to_dict(self) -> dict:
        return asdict(self)


def hvac_running(outdoor: AirState, house: HouseSettings) -> str | None:
    """'heating', 'cooling' or None, judged from the outdoor temperature."""
    t_f = c_to_f(outdoor.t_c)
    if t_f < house.heating_below_outdoor_f:
        return "heating"
    if t_f > house.cooling_above_outdoor_f:
        return "cooling"
    return None


def hvac_penalty_w(m_up: float, outdoor: AirState, upstairs: AirState, house: HouseSettings) -> float:
    """Extra HVAC electricity [W] to condition the outdoor air that replaces m_up [kg/s] drawn from upstairs."""
    if m_up <= 0:
        return 0.0
    mode = hvac_running(outdoor, house)
    if mode == "cooling":
        dh = outdoor.enthalpy_kj_kg - upstairs.enthalpy_kj_kg
        return m_up * dh * 1000 / house.cooling_cop if dh > 0 else 0.0
    if mode == "heating" and outdoor.t_c < upstairs.t_c:
        return m_up * psychro.CP_DA * (upstairs.t_c - outdoor.t_c) * 1000 / house.heating_cop
    return 0.0


def ventilation_effect(
    indoor: AirState,
    outdoor: AirState,
    fans: list[Fan],
    house: HouseSettings,
    upstairs: AirState | None = None,
) -> VentilationEffect:
    upstairs_assumed = upstairs is None
    if upstairs is None:
        from .units import f_to_c

        upstairs = AirState.from_t_rh(f_to_c(house.upstairs_temp_f), house.upstairs_rh_pct / 100.0)

    qe = sum(f.actual_cfm for f in fans if f.type == FanType.EXHAUST)
    qi = sum(f.actual_cfm for f in fans if f.type == FanType.INTAKE)
    fan_w = sum(f.rated_w for f in fans)
    f_up = house.conditioned_air_fraction

    if qe >= qi:
        shortfall = qe - qi
        q_up = shortfall * f_up
        q_out = qi + shortfall * (1 - f_up)
        pressurized = False
        # dry-air mass leaving through exhausts, at basement density
        m_total = cfm_to_m3s(qe) * psychro.dry_air_density(indoor.t_c, indoor.w, indoor.p_kpa)
    else:
        q_up = 0.0
        q_out = qi
        pressurized = True
        m_total = cfm_to_m3s(qi) * psychro.dry_air_density(outdoor.t_c, outdoor.w, outdoor.p_kpa)

    q_sum = q_up + q_out
    m_out = m_total * (q_out / q_sum) if q_sum else 0.0  # kg/s dry air from outdoors
    m_up = m_total * (q_up / q_sum) if q_sum else 0.0

    rem_out = m_out * (indoor.w - outdoor.w) * 3600 * 1000  # g/h
    rem_up = m_up * (indoor.w - upstairs.w) * 3600 * 1000
    net = rem_out + rem_up

    # Air pulled from upstairs is replaced upstairs by outdoor infiltration. That costs energy
    # only while the HVAC is running: on mild days with heat and AC off the house floats and the
    # replacement air is free.
    penalty_w = hvac_penalty_w(m_up, outdoor, upstairs, house)

    total_w = fan_w + penalty_w
    eff = (net / (total_w / 1000.0)) if total_w > 0 else None
    cost_h = total_w / 1000.0 * house.electricity_cost_per_kwh
    gal_h = grams_to_gallons(net)
    return VentilationEffect(
        exhaust_cfm=qe,
        intake_cfm=qi,
        outdoor_path_cfm=q_out,
        upstairs_path_cfm=q_up,
        pressurized=pressurized,
        dry_air_kg_h=m_total * 3600,
        removal_outdoor_g_h=rem_out,
        removal_upstairs_g_h=rem_up,
        net_removal_g_h=net,
        net_removal_gal_day=g_per_h_to_gal_per_day(net),
        net_removal_gal_h=gal_h,
        fan_power_w=fan_w,
        conditioned_air_penalty_w=penalty_w,
        total_power_w=total_w,
        efficiency_g_per_kwh=eff,
        cost_per_hour=cost_h,
        cost_per_gallon=(cost_h / gal_h) if gal_h > 1e-9 else None,
        conditioned_air_assumed=m_up > 0,
        upstairs_assumed=upstairs_assumed,
    )
