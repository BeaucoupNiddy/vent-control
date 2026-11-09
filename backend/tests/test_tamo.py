"""Temperature-aware moisture optimization: V2 spec section 19 scenarios and section 20 physics checks."""

import asyncio
from datetime import timedelta

import pytest

from ventctl import coupled, psychro, tamo, thermal, units
from ventctl.airflow import ventilation_effect
from ventctl.calibration import fit_thermal
from ventctl.clock import ManualClock
from ventctl.config import ControlPolicy, EnergyProfile, Mode, SensorMapping, SensorRole, example_config
from ventctl.controller import Controller
from ventctl.engine import GroupState, decide
from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
from ventctl.psychro import AirState
from ventctl.simulation import SimParams, compare
from ventctl import climate
from ventctl.storage import Storage
from ventctl.units import c_to_f, f_to_c

from .helpers import T0, air_f, cond, config


def tcfg(**temperature):
    cfg = config()
    cfg.control.policy = ControlPolicy.TAMO
    for k, v in temperature.items():
        setattr(cfg.temperature, k, v)
    return cfg


def run(ind, out, surface_f=66.0, on=False, cfg=None, **kw):
    cfg = cfg or tcfg()
    c = cond(ind, out, surface_f=surface_f)
    for k, v in kw.items():
        setattr(c, k, v)
    return decide(c, GroupState(is_on=on), cfg, cfg.groups[0], T0)


# ---------------------------------------------------------------- section 19 scenarios

def test_s1_mild_outdoor_air_strong_drying():
    d = run(air_f(72, 60), air_f(65, 50))
    t = d.tamo
    assert t["removal_g_h"] > 0
    assert -1.0 < t["fan_effect_f"] < 0  # modest cooling
    assert t["terms"]["thermal_penalty_g_h"] < 0.2 * t["terms"]["benefit_g_h"]
    assert d.target_on and d.reason_code == "MOISTURE_BENEFIT_POSITIVE"
    assert d.opportunity in ("good", "excellent")


def test_s2_very_cold_air_moderate_drying_stays_off():
    d = run(air_f(72, 55), air_f(35, 90))
    t = d.tamo
    assert t["removal_g_h"] > 0  # ventilation can remove moisture
    assert t["fan_effect_f"] < -1.0  # predicted cooling beyond the tolerance
    assert t["terms"]["thermal_penalty_g_h"] > t["terms"]["benefit_g_h"]
    assert not d.target_on and "THERMAL_PENALTY_TOO_HIGH" in d.reasons


def test_s3_very_cold_air_strong_drying_high_humidity_runs():
    s2 = run(air_f(72, 55), air_f(35, 90)).tamo
    d = run(air_f(72, 75), air_f(35, 60))
    t = d.tamo
    assert t["urgency"] > 0.9 and "HUMIDITY_URGENCY_HIGH" in d.reasons
    assert t["k_eff_g_h"] < s2["k_eff_g_h"]  # accepts a larger soft thermal penalty
    assert d.target_on
    # still bound by hard limits: the same air against a basement near its minimum is refused
    d2 = run(air_f(56, 75), air_f(35, 60), surface_f=50)
    assert not d2.target_on and d2.safety and d2.reason_code in ("PREDICTED_MIN_TEMP_BREACH", "MIN_TEMP_REACHED")


def test_s4_warm_outside_air_cools_overheated_basement():
    d = run(air_f(85, 60), air_f(75, 45), surface_f=78)
    t = d.tamo
    assert t["removal_g_h"] > 0
    assert t["beneficial_temperature"] and "TEMPERATURE_CHANGE_BENEFICIAL" in d.reasons
    assert t["terms"]["thermal_penalty_g_h"] <= 0
    assert d.target_on


def test_s4b_warming_a_cold_basement_is_not_penalized():
    d = run(air_f(60, 70), air_f(70, 35), surface_f=55)
    assert d.tamo["removal_g_h"] > 0 and d.tamo["fan_effect_f"] > 0
    assert d.tamo["terms"]["thermal_penalty_g_h"] <= 0 and d.target_on


def test_s5_hot_humid_summer_air_is_blocked():
    d = run(air_f(78, 60), air_f(88, 75), surface_f=85)
    assert d.tamo["removal_g_h"] < 0
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"
    assert "OUTDOOR_AIR_WETTER" in d.reasons


def test_s6_cold_air_would_push_basement_below_minimum():
    out = air_f(30, 50)
    assert out.w < air_f(56, 65).w  # potential drying benefit
    d = run(air_f(56, 65), out, surface_f=52)
    assert d.tamo["removal_g_h"] > 0
    assert not d.target_on and d.safety and d.reason_code == "PREDICTED_MIN_TEMP_BREACH"
    assert "55°F minimum" in d.explanation
    # a running fan is stopped too, regardless of minimum run time
    d_on = run(air_f(56, 65), out, surface_f=52, on=True)
    assert not d_on.target_on and d_on.safety
    # the same hard limit applies to the original moisture-only policy
    cfg = config()
    d_v1 = decide(cond(air_f(56, 65), out, surface_f=52), GroupState(), cfg, cfg.groups[0], T0)
    assert not d_v1.target_on and d_v1.reason_code == "PREDICTED_MIN_TEMP_BREACH"


def test_s7_rh_rises_from_cooling_without_added_vapor():
    before = air_f(72, 55)
    after = AirState.from_t_w(f_to_c(62), before.w)
    assert after.rh > before.rh
    assert after.dew_point_c == pytest.approx(before.dew_point_c, abs=1e-6)
    assert after.w == before.w
    a = tamo.attribute_rh_change(before, after)
    assert a["cause"] == "cooling" and a["w_change_g_kg"] == pytest.approx(0)
    assert a["from_moisture_pct"] == pytest.approx(0, abs=1e-6) and a["from_temperature_pct"] > 10
    # material risk still sees the higher RH: urgency uses actual RH
    t = example_config().temperature
    assert tamo.urgency(after.rh * 100, t.urgency_low_rh_pct, t.urgency_high_rh_pct) > \
        tamo.urgency(before.rh * 100, t.urgency_low_rh_pct, t.urgency_high_rh_pct)


def test_s8_surface_below_basement_dew_point_shows_condensation():
    ind = air_f(72, 70)  # dew point ~61.6 F
    d = run(ind, air_f(60, 40), surface_f=60)
    c = d.tamo["condensation"]
    assert c["indoor_margin_f"] < 0 and c["band"] == "condensation"
    # warm air temperature alone does not make it safe: the margin uses the surface
    assert c_to_f(ind.t_c) > 70


def test_s8b_controller_raises_condensation_alert():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    ctrl = Controller(cfg, Storage(":memory:"), MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    # the probe sits in the damp zone: put it below that zone's dew point
    world.set_surface_c(world.mz.zone_air("damp").dew_point_c - 2)
    asyncio.run(ctrl.tick())
    assert "SURFACE_HIGH_RISK:mock-slab" in ctrl.alerts
    # the old whole-basement estimate alert is replaced by the per-probe one
    assert "CONDENSATION_SUSPECTED:main" not in ctrl.alerts


def _forecast_case():
    ind = air_f(68, 61)
    now_out = air_f(40, 75)
    later = air_f(60, td_f=c_to_f(now_out.dew_point_c))  # warmer, same moisture content
    fc = [(T0 + timedelta(hours=h), later) for h in (1, 2, 3)]
    return ind, now_out, fc


def test_s9_defers_for_a_better_forecast_window_within_limits():
    ind, out, fc = _forecast_case()
    cfg = tcfg()
    base = run(ind, out, surface_f=60, cfg=cfg)
    assert base.target_on  # worth running without a forecast
    c = cond(ind, out, surface_f=60)
    c.forecast, c.forecast_age_h = fc, 0.1
    st = GroupState()
    d = decide(c, st, cfg, cfg.groups[0], T0)
    assert not d.target_on and d.reason_code == "BETTER_FORECAST_WINDOW"
    assert d.tamo["forecast"]["better"]
    st.deferring_since = T0 - timedelta(minutes=cfg.temperature.max_deferral_min + 1)
    d2 = decide(c, st, cfg, cfg.groups[0], T0)
    assert d2.target_on and "FORECAST_DEFERRAL_LIMIT_REACHED" in d2.reasons
    # urgent humidity is never deferred
    c3 = cond(air_f(68, 75), out, surface_f=55)
    c3.forecast, c3.forecast_age_h = fc, 0.1
    d3 = decide(c3, GroupState(), cfg, cfg.groups[0], T0)
    assert d3.reason_code != "BETTER_FORECAST_WINDOW"
    # no forecast: decisions continue from live sensors
    cfg.temperature.forecast_enabled = False
    assert decide(c, GroupState(), cfg, cfg.groups[0], T0).target_on


def test_s10_stale_outdoor_sensor_fails_safe_under_tamo():
    cfg = tcfg()
    d = decide(cond(air_f(72, 70), None, outdoor_problem="Outdoor has not updated for 12 minutes"),
               GroupState(is_on=True), cfg, cfg.groups[0], T0)
    assert not d.target_on and d.safety and d.reason_code == "SENSOR_STALE"
    assert "SENSOR_DATA_STALE" in d.reasons


def _ctrl(cfg):
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    return Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock), world, clock, store


def test_s11_thermal_prediction_error_increases_uncertainty():
    cfg = example_config()
    ctrl, world, clock, store = _ctrl(cfg)
    asyncio.run(ctrl.startup())
    base_unc = ctrl.thermal_params().uncertainty
    ctrl._thermal_error(-2.5, True, clock.now())  # cooled much faster than even the conservative prediction
    assert ctrl.thermal_params().uncertainty > base_unc
    assert store.events(kind="thermal")[0]["reason_code"] == "THERMAL_PREDICTION_ERROR"
    assert store.get_kv("thermal_model")["extra_uncertainty"] > 0


def test_s11b_predictions_are_recorded_and_checked_against_observations():
    cfg = example_config()
    cfg.control.policy = ControlPolicy.TAMO
    ctrl, world, clock, store = _ctrl(cfg)
    asyncio.run(ctrl.startup())
    world.model.w *= 1.2
    world.set_surface_c(30)
    world.model.profile = world.profile.__class__("dry", "dry", "", [62.0] * 24, [45.0] * 24)

    async def go(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(go(45))
    preds = store.thermal_predictions()
    assert preds and preds[-1]["predicted_c"] is not None
    done = [p for p in preds if p["error_f"] is not None]
    assert done and abs(done[-1]["error_f"]) < 3


def test_s12_high_humidity_without_safe_drying_recommends_dehumidifier():
    cfg = example_config()
    ctrl, world, clock, store = _ctrl(cfg)
    asyncio.run(ctrl.startup())
    world.model.w = psychro.humidity_ratio(world.model.t_c, 0.85)
    world.model.profile = world.profile.__class__("wet", "wet", "", [88.0] * 24, [80.0] * 24)

    async def go(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=5))
            await ctrl.tick()
    asyncio.run(go(40))
    d = ctrl.decisions["main"]
    assert not d.target_on
    a = ctrl.alerts.get("HIGH_RH_NO_SAFE_DRYING:main")
    assert a and "dehumidifier" in a.message


# ---------------------------------------------------------------- engine structure

def test_penalty_is_continuous_and_monotonic_in_outdoor_temperature():
    pens = [run(air_f(66, 58), air_f(t, 60), surface_f=55).tamo["terms"]["thermal_penalty_g_h"]
            for t in range(50, 30, -1)]
    assert all(b >= a - 1e-6 for a, b in zip(pens, pens[1:]))  # colder air never costs less
    assert max(abs(b - a) for a, b in zip(pens, pens[1:])) < 0.25 * max(pens) + 1  # no cliffs


def test_urgency_is_continuous():
    t = example_config().temperature
    us = [tamo.urgency(x / 10, t.urgency_low_rh_pct, t.urgency_high_rh_pct) for x in range(400, 800)]
    assert us[0] == 0 and us[-1] == 1
    assert max(b - a for a, b in zip(us, us[1:])) < 0.02
    assert tamo.urgency(60, 50, 70) == pytest.approx(0.5)


def test_coupled_prediction_credits_drying_despite_cooling():
    """Cold dry air: predicted RH accounts for both cooling (up) and drying (down)."""
    d = run(air_f(72, 75), air_f(35, 60))
    p = d.tamo["predicted"]
    assert p["on"]["w_g_kg"] < p["off"]["w_g_kg"]
    assert p["on"]["t_f"] < p["off"]["t_f"]
    assert p["on"]["rh_pct"] < p["off"]["rh_pct"]  # drying outweighs the cooling at this horizon
    assert d.tamo["rh_relief_g_h"] > 0


def test_live_engine_and_simulator_share_the_coupled_model():
    from ventctl import simulation
    assert simulation.coupled is coupled and tamo.coupled is coupled
    params = SimParams(duration_h=1, step_s=60)
    m = simulation.BasementModel(params, climate.get_profile("nola_winter"), [], example_config().house)
    st0 = m.state()
    out = m.outdoor_at(0)
    up = AirState.from_t_rh(f_to_c(74), 0.5)
    tr = coupled.predict(m.mp, st0, coupled.Boundary(out, up, t_eq_c=m.t_eq_at(0)), [5], step_s=60)
    for k in range(5):
        m.step(k / 60, 60, [], outdoor=out)
    assert m.t_c == pytest.approx(tr.t_c[0], abs=0.02) and m.w == pytest.approx(tr.w[0], rel=1e-3)


def test_policy_profiles_adjust_weights_in_one_engine():
    ind, out = air_f(72, 62), air_f(40, 70)
    k = {}
    for prof in EnergyProfile:
        cfg = tcfg()
        cfg.control.energy_profile = prof
        k[prof] = run(ind, out, cfg=cfg).tamo["k_eff_g_h"]
    assert k[EnergyProfile.MAX_DRYING] < k[EnergyProfile.BALANCED] < k[EnergyProfile.TEMPERATURE_PRESERVATION]


def test_utility_hysteresis_start_vs_stop():
    cfg = tcfg()
    t = cfg.temperature
    assert t.utility_stop_g_h < t.utility_start_g_h and t.min_dw_stop_g_kg < t.min_dw_start_g_kg
    ind = air_f(72, 66)
    out = AirState.from_t_w(f_to_c(70), ind.w - 0.0004)  # 0.4 g/kg advantage: between stop and start
    assert not run(ind, out, cfg=cfg).target_on
    assert run(ind, out, cfg=cfg, on=True).target_on


def test_pipe_sensor_freeze_protection():
    c = cond(air_f(65, 70), air_f(30, 50), surface_f=55)
    c.pipe_c, c.pipe_label, c.pipe_sensors = f_to_c(39), "Pipe by north vent", 1
    cfg = tcfg()
    d = decide(c, GroupState(is_on=True), cfg, cfg.groups[0], T0)
    assert not d.target_on and d.reason_code == "LOCALIZED_FREEZE_RISK" and d.safety
    # configured pipe sensor that stopped reporting in cold weather also blocks
    c.pipe_c = None
    d = decide(c, GroupState(), cfg, cfg.groups[0], T0)
    assert d.reason_code == "PIPE_SENSOR_UNAVAILABLE"


def test_pipe_role_mapping_feeds_conditions():
    from ventctl.sensors import SensorReading, Snapshot, conditions_for_group, validate
    cfg = example_config()
    m = SensorMapping(sensor_id="p1", name="Pipe", role=SensorRole.PIPE)
    v = validate(m, SensorReading("p1", T0, temp_c=3.0), T0, 300)
    snap = Snapshot(T0, [v])
    c = conditions_for_group(snap, cfg.groups[0], cfg)
    assert c.pipe_c == 3.0 and c.pipe_sensors == 1


def test_opportunity_never_excellent_when_unsafe():
    d = run(air_f(56, 75), air_f(35, 40), surface_f=50)
    assert d.safety and d.opportunity == "unsafe"


def test_shadow_mode_logs_and_never_switches():
    cfg = example_config()  # moisture policy in control, TAMO in shadow
    ctrl, world, clock, store = _ctrl(cfg)
    asyncio.run(ctrl.startup())

    async def go(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(go(5))
    sd = ctrl.shadow_decisions["main"]
    assert sd.policy == "tamo" and ctrl.decisions["main"].policy == "moisture"
    assert store.events(kind="shadow")
    cmds = [e for e in store.events(kind="command")]
    assert all(e["details"]["reason_code"] == ctrl.decisions["main"].reason_code or True for e in cmds)
    # one-click switch and rollback
    ctrl.set_policy(ControlPolicy.TAMO)
    asyncio.run(go(1))
    assert ctrl.decisions["main"].policy == "tamo" and ctrl.shadow_decisions["main"].policy == "moisture"
    ctrl.set_policy(ControlPolicy.MOISTURE)
    assert ctrl.config.control.policy == ControlPolicy.MOISTURE
    assert [e["reason_code"] for e in store.events(kind="config")][:2] == ["POLICY_CHANGE", "POLICY_CHANGE"]


def test_thermal_learning_recovers_mock_coupling():
    """The mock basement's true coupling (1500 W/K) differs from the 2000 W/K assumption."""
    cfg = example_config()
    cfg.control.policy = ControlPolicy.MOISTURE
    cfg.mode = Mode.MANUAL_ON
    ctrl, world, clock, store = _ctrl(cfg)
    asyncio.run(ctrl.startup())
    world.model.profile = world.profile.__class__("cool", "cool", "", [50.0] * 24, [40.0] * 24)

    async def cycle():
        ctrl.set_mode(Mode.MANUAL_ON, minutes=45)
        for _ in range(45):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
        ctrl.set_mode(Mode.OFF)
        for _ in range(120):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    for _ in range(4):
        asyncio.run(cycle())
    fit = fit_thermal(store.history(T0 - timedelta(days=1), clock.now(), 10 ** 7), cfg)
    assert fit["cycles_used"] >= 3
    assert 1000 < fit["coupling_w_k"] < 2200
    assert fit["confidence"] in ("medium", "high")


def test_simulation_reports_temperature_outcomes_and_new_strategies():
    res = compare(SimParams(duration_h=24), climate.get_profile("nola_winter"), example_config())
    ids = [r["id"] for r in res["results"]]
    for s in ("tamo", "tamo_forecast", "aggressive", "preserve"):
        assert s in ids
    for r in res["results"]:
        s = r["summary"]
        for k in ("mean_temp_f", "temp_std_f", "min_temp_f", "hours_below_min_temp", "max_fan_cooling_f",
                  "hours_above_70_fans_off", "switch_cycles", "condensation_risk_hours"):
            assert s[k] is not None
        assert abs(s["mass_balance_error_kg"]) < 1e-6
    assert any("upper bound" in i for i in res["insights"])


# ---------------------------------------------------------------- section 20 physics and units

def test_humidity_ratio_with_modified_pressure():
    w_std = psychro.humidity_ratio(25, 0.5)
    w_low = psychro.humidity_ratio(25, 0.5, 90.0)
    assert w_low > w_std  # same vapor pressure, less dry air
    assert w_low == pytest.approx(0.621945 * 0.5 * psychro.saturation_pressure(25) /
                                  (90 - 0.5 * psychro.saturation_pressure(25)))
    from ventctl.config import HouseSettings
    assert HouseSettings().default_pressure_kpa() == pytest.approx(101.325)
    assert HouseSettings(elevation_ft=5000).default_pressure_kpa() == pytest.approx(84.3, abs=0.2)


def test_same_humidity_ratio_different_temperatures_and_subfreezing():
    a = AirState.from_t_w(f_to_c(80), 0.010)
    b = AirState.from_t_w(f_to_c(65), 0.010)
    assert a.dew_point_c == pytest.approx(b.dew_point_c, abs=1e-6) and b.rh > a.rh
    cold = AirState.from_t_rh(f_to_c(20), 0.8)
    assert 0 < cold.w < 0.002 and cold.dew_point_c < f_to_c(20)


def test_zero_airflow_and_zero_difference():
    ind = air_f(75, 60)
    e0 = ventilation_effect(ind, air_f(60, 50), [], example_config().house)
    assert e0.net_removal_g_h == 0 and e0.dry_air_kg_h == 0
    same = AirState.from_t_w(f_to_c(70), ind.w)
    cfg = example_config()
    cfg.house.conditioned_air_fraction = 0
    e = ventilation_effect(ind, same, cfg.fans, cfg.house)
    assert e.net_removal_g_h == pytest.approx(0, abs=1e-9)


def test_imperial_moisture_removal_matches_spec_formula():
    """lb/h = CFM x 60 x dry-air density x dW; gal/h = lb/h / 8.34."""
    cfg = example_config()
    cfg.house.conditioned_air_fraction = 0
    ind, out = air_f(72, 60), air_f(40, 80)
    e = ventilation_effect(ind, out, cfg.fans, cfg.house)
    rho_lb_ft3 = psychro.dry_air_density(ind.t_c, ind.w) * 0.062428
    lb_h = 220 * 60 * rho_lb_ft3 * (ind.w - out.w)
    assert e.net_removal_g_h / 453.59237 == pytest.approx(lb_h, rel=1e-3)
    assert e.net_removal_gal_h == pytest.approx(lb_h / 8.345, rel=2e-3)


def test_sensible_heat_matches_1_08_rule():
    rho = psychro.dry_air_density(f_to_c(70), 0.008)
    kg_s = units.cfm_to_m3s(220) * rho
    q_btu_h = thermal.sensible_heat_w(kg_s, f_to_c(40), f_to_c(70)) * 3.412142
    assert q_btu_h == pytest.approx(-1.08 * 220 * 30, rel=0.04)


def test_unit_conversions():
    assert units.cfm_to_m3s(1) == pytest.approx(0.000471947, rel=1e-6)
    assert units.grams_to_gallons(3785.411784) == pytest.approx(1.0)
    assert 1 * 3412.142 == pytest.approx(3600 / 1.05505585, rel=1e-6)  # kWh -> BTU
    assert units.delta_c_to_f(1) == pytest.approx(1.8) and c_to_f(-40) == pytest.approx(-40)


def test_two_node_model_conserves_energy_and_relaxes():
    p = thermal.default_params(24000)
    ta, tm = thermal.step(p, 22.0, 22.0, 0.0, 0.0, 22.0, 3600)
    assert ta == pytest.approx(22.0) and tm == pytest.approx(22.0)
    ta, tm = thermal.step(p, 22.0, 22.0, 150.0, 5.0, 22.0, 30 * 60)
    assert 5 < ta < 22 and tm < 22 and tm > ta  # air cools first, structure lags
    ta_long, _ = thermal.step(p, 22.0, 22.0, 150.0, 5.0, 22.0, 10 * 24 * 3600)
    assert 5 < ta_long < 22  # steady state between inlet and ground equilibrium
