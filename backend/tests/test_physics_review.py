"""Tests for the physics-review fixes (items 1-7). Condensation-rule tests live in test_engine.py."""

from datetime import timedelta

import pytest

from ventctl import calibration, coupled, thermal
from ventctl.airflow import hvac_penalty_w, ventilation_effect
from ventctl.config import FanGroup, SensorMapping, SensorRole, example_config
from ventctl.engine import GroupState, decide, ventilation_wets_surface
from ventctl.psychro import AirState
from ventctl.sensors import SensorReading, Snapshot, conditions_for_group, validate
from ventctl.storage import Storage
from ventctl.surfaces import DEFAULT_AIR_FOLLOW
from ventctl.units import f_to_c

from .helpers import T0, air_f, cond


# 1: the condensation rule itself
def test_drier_outdoor_air_never_wets_a_surface():
    assert not ventilation_wets_surface(f_to_c(60), basement_dp_c=f_to_c(62), outdoor_dp_c=f_to_c(59), margin_c=2)
    assert ventilation_wets_surface(f_to_c(60), basement_dp_c=f_to_c(55), outdoor_dp_c=f_to_c(59), margin_c=2)


# 2: upstairs air costs HVAC energy only while the heating or AC runs
def test_hvac_penalty_only_when_hvac_runs():
    house = example_config().house
    house.conditioned_air_fraction = 0.3
    up = air_f(74, 50)
    m_up = 0.03
    assert hvac_penalty_w(m_up, air_f(65, td_f=50), up, house) == 0  # mild: heat and AC off
    assert hvac_penalty_w(m_up, air_f(40, td_f=30), up, house) > 0  # heating weather
    assert hvac_penalty_w(m_up, air_f(90, td_f=75), up, house) > 0  # AC weather
    fans = example_config().fans
    mild = ventilation_effect(air_f(70, 75), air_f(64, td_f=48), fans, house, up)
    assert mild.conditioned_air_penalty_w == 0 and mild.total_power_w == pytest.approx(40)


# 6: the wettest zone is the one holding the most water, and the fan is judged on the air it removes
def _snap(zones):
    snap = Snapshot(T0)
    for sid, zone, t, rh in zones:
        m = SensorMapping(sensor_id=sid, name=sid, role=SensorRole.BASEMENT, zone=zone)
        snap.readings.append(validate(m, SensorReading(sid, T0, temp_c=t, rh_pct=rh), T0, 300))
    mo = SensorMapping(sensor_id="o", name="out", role=SensorRole.OUTDOOR)
    snap.readings.append(validate(mo, SensorReading("o", T0, temp_c=18, rh_pct=60), T0, 300))
    return snap


def test_wettest_zone_is_chosen_by_moisture_content_not_rh():
    cfg = example_config()
    cfg.control.zone_aggregation = "wettest"
    # pocket: cool, higher RH but less water; main: warmer, lower RH, more water
    snap = _snap([("a", "main", 24.0, 70.0), ("b", "pocket", 18.0, 80.0)])
    c = conditions_for_group(snap, FanGroup(id="all", name="all"), cfg)
    assert c.indoor.t_c == pytest.approx(24.0)


def test_exhaust_air_comes_from_the_ventilation_path_zone():
    cfg = example_config()
    # damp zone holds the most water, main (at the fan) is drier
    snap = _snap([("a", "main", 21.0, 55.0), ("b", "pocket", 21.0, 60.0), ("c", "damp", 21.0, 75.0)])
    c = conditions_for_group(snap, FanGroup(id="all", name="all"), cfg)
    assert c.exhaust is not None and c.exhaust_label == "main"
    assert c.indoor.w > c.exhaust.w
    c.surface_c = f_to_c(65)
    c.surface_source = "measured"
    d = decide(c, GroupState(), cfg, FanGroup(id="main", name="Basement"), T0)
    # removal is computed from the air the fan actually exhausts
    expected = ventilation_effect(c.exhaust, c.outdoor, cfg.fans, cfg.house).net_removal_g_h
    assert d.effect.net_removal_g_h == pytest.approx(expected)
    assert "main air at the fan" in next(ch for ch in d.checks if ch.id == "direction").detail


# 3: learn the upstairs share of the makeup air from fan starts
def _synthetic_rows(true_f: float, days: int = 3):
    cfg = example_config()
    cfg.zones, cfg.connections, cfg.circulators = [], [], []
    cfg.house.conditioned_air_fraction = true_f
    cfg.house.moisture_buffer_kg = 0
    tp = thermal.params_from_config(cfg)
    mp = coupled.ModelParams.from_config(cfg, tp)
    st = coupled.State(f_to_c(68), f_to_c(68), AirState.from_t_rh(f_to_c(68), 0.70).w, 0.0)
    rows, t = [], T0
    for k in range(days * 24 * 30):  # 2-minute steps
        hour = k / 30
        on = (hour % 6) >= 3  # 3 h off, 3 h on
        # upstairs alternates between drier and wetter than outdoors so the starts are informative
        out = air_f(60, td_f=45)
        up = air_f(72, 25) if int(hour // 6) % 2 == 0 else air_f(72, 60)
        ind = st.air()
        eff = ventilation_effect(ind, out, cfg.fans, cfg.house, up)
        m_out, m_up = coupled.fan_flows(eff)
        bd = coupled.Boundary(out, up, m_out if on else 0.0, m_up if on else 0.0)
        rows.append({"ts": t.isoformat(), "indoor_t_c": ind.t_c, "indoor_w": ind.w, "exhaust_w": ind.w,
                     "outdoor_w": out.w, "upstairs_w": up.w, "upstairs_t_c": up.t_c,
                     "fans_on": 1 if on else 0, "fans_verified": 1})
        coupled.advance(mp, st, bd, 120)
        t += timedelta(minutes=2)
    return rows, cfg


@pytest.mark.parametrize("true_f", [0.1, 0.45])
def test_upstairs_fraction_is_recovered_from_fan_starts(true_f):
    rows, cfg = _synthetic_rows(true_f)
    cfg.house.conditioned_air_fraction = 0.10  # the app starts from the assumption
    fit = calibration.fit_upstairs_fraction(rows, cfg)
    assert fit["starts_used"] >= 8
    assert fit["fraction"] == pytest.approx(true_f, abs=0.05)
    assert fit["confidence"] in ("medium", "high")
    f, src = calibration.effective_upstairs_fraction(cfg, fit)
    assert src == "learned" and f == fit["fraction"]
    cfg.house.learn_conditioned_air_fraction = False
    assert calibration.effective_upstairs_fraction(cfg, fit) == (0.10, "assumed")


def test_upstairs_fraction_survives_sensor_resolution():
    # Ecowitt sensors report 1% RH and 0.1 °F: quantize the basement reading the same way
    from ventctl.psychro import humidity_ratio
    rows, cfg = _synthetic_rows(0.4, days=4)
    for r in rows:
        t_f = round((r["indoor_t_c"] * 9 / 5 + 32) * 10) / 10
        rh = round(AirState.from_t_w(r["indoor_t_c"], r["indoor_w"]).rh * 100) / 100
        r["indoor_w"] = r["exhaust_w"] = humidity_ratio(f_to_c(t_f), rh)
    fit = calibration.fit_upstairs_fraction(rows, cfg)
    assert fit["fraction"] == pytest.approx(0.4, abs=0.1)


def test_upstairs_fraction_needs_an_upstairs_sensor():
    rows, cfg = _synthetic_rows(0.3, days=1)
    for r in rows:
        r["upstairs_w"] = None
    fit = calibration.fit_upstairs_fraction(rows, cfg)
    assert fit["fraction"] is None and fit["confidence"] == "none"


# 4: a realistic materials buffer, moved onto existing installs that still have the old default
def test_buffer_default_and_upgrade_of_saved_configs(tmp_path):
    assert example_config().house.moisture_buffer_kg == 400
    old = example_config()
    old.house.moisture_buffer_kg, old.house.moisture_buffer_tau_h = 25, 12
    s = Storage(tmp_path / "a.db")
    s.save_config(old)
    cfg = Storage(tmp_path / "a.db").load_config()
    assert cfg.house.moisture_buffer_kg == 400 and cfg.house.moisture_buffer_tau_h == 36
    # a value the user chose is kept, and the upgrade runs only once
    custom = example_config()
    custom.house.moisture_buffer_kg = 120
    s2 = Storage(tmp_path / "b.db")
    s2.save_config(custom)
    assert Storage(tmp_path / "b.db").load_config().house.moisture_buffer_kg == 120
    cfg.house.moisture_buffer_kg = 25
    s.save_config(cfg)
    assert Storage(tmp_path / "a.db").load_config().house.moisture_buffer_kg == 25


# 7: surfaces follow air temperature slowly
def test_surface_follow_default_matches_slow_concrete():
    assert DEFAULT_AIR_FOLLOW == pytest.approx(0.2)
    assert cond(air_f(70, 60), air_f(50, 40)).surface_follow == pytest.approx(0.2)
