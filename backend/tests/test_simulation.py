"""Simulation, replay and labeling tests (spec 18: #14, #15, #16, #17)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ventctl import analysis, climate, simulation
from ventctl.config import example_config
from ventctl.simulation import SimParams, compare, run_strategy


@pytest.mark.parametrize("params", [
    SimParams(duration_h=24),
    SimParams(duration_h=48, dehumidifier=True, buffer_capacity_kg=40, natural_ach=1.0),
    SimParams(duration_h=24, initial_rh_pct=99, moisture_source_pints_day=200, buffer_capacity_kg=0),  # condensing
    SimParams(duration_h=24, balanced=True, conditioned_air_fraction=0.5),
])
def test_simulation_conserves_moisture_mass(params):
    for s in simulation.STRATEGIES:
        r = run_strategy(s, params, climate.get_profile("nola_front"), example_config())
        assert abs(r.summary["mass_balance_error_kg"]) < 1e-6, (s, r.summary["mass_balance_error_kg"])


def test_closed_box_with_no_flows_keeps_humidity_ratio():
    p = SimParams(duration_h=12, natural_ach=0, moisture_source_pints_day=0, buffer_capacity_kg=0,
                  temp_variation_f=0, basement_mean_temp_f=79, initial_temp_f=79)
    r = run_strategy("off", p, climate.get_profile("nola_summer"), example_config())
    first, last = r.series[0], r.series[-1]
    assert last["td_f"] == pytest.approx(first["td_f"], abs=1e-6)
    assert r.summary["final_rh"] == pytest.approx(75, abs=1e-3)


def test_only_sources_raise_moisture_by_expected_amount():
    p = SimParams(duration_h=24, natural_ach=0, moisture_source_pints_day=10, buffer_capacity_kg=0)
    r = run_strategy("off", p, climate.get_profile("nola_summer"), example_config())
    assert r.summary["sources_added_kg"] == pytest.approx(10 * 0.473176473, rel=1e-3)


def test_rh_is_derived_from_moisture_and_temperature_not_held_constant():
    p = SimParams(duration_h=24, natural_ach=0, moisture_source_pints_day=0, buffer_capacity_kg=0,
                  temp_variation_f=4, initial_temp_f=79)
    r = run_strategy("off", p, climate.get_profile("nola_summer"), example_config())
    rhs = [x["rh"] for x in r.series]
    assert max(rhs) - min(rhs) > 5  # temperature swing changes RH at constant W
    tds = [x["td_f"] for x in r.series]
    assert max(tds) - min(tds) < 1e-6


# 15
def test_continuous_and_scheduled_runtimes():
    p = SimParams(duration_h=72)
    prof = climate.get_profile("nola_summer")
    cfg = example_config()
    assert run_strategy("continuous", p, prof, cfg).summary["fan_runtime_h"] == pytest.approx(72, abs=0.02)
    assert run_strategy("daytime", p, prof, cfg).summary["fan_runtime_h"] == pytest.approx(36, abs=0.02)
    assert run_strategy("nighttime", p, prof, cfg).summary["fan_runtime_h"] == pytest.approx(36, abs=0.02)
    assert run_strategy("off", p, prof, cfg).summary["fan_runtime_h"] == 0
    r = run_strategy("continuous", p, prof, cfg)
    assert r.summary["fan_kwh"] == pytest.approx(72 * 40 / 1000, rel=1e-3)


def test_controller_strategies_avoid_condensation_risk():
    res = compare(SimParams(duration_h=72), climate.get_profile("nola_front"), example_config())
    by = {r["id"]: r["summary"] for r in res["results"]}
    assert by["dewpoint"]["condensation_risk_hours"] == 0
    assert by["optimized"]["condensation_risk_hours"] == 0
    assert by["continuous"]["condensation_risk_hours"] > 0


def test_humid_summer_controller_does_not_add_moisture():
    # dry-ish basement, no internal sources: on a tropical day outdoor air only adds moisture
    p = SimParams(duration_h=48, initial_rh_pct=65, moisture_source_pints_day=0, buffer_capacity_kg=0)
    res = compare(p, climate.get_profile("nola_humid"), example_config())
    by = {r["id"]: r["summary"] for r in res["results"]}
    assert by["optimized"]["fan_runtime_h"] == 0
    assert by["optimized"]["fan_net_removed_kg"] == 0
    assert by["continuous"]["fan_outdoor_removed_kg"] < 0  # 24/7 outdoor air adds moisture
    # 17: moisture addition is reported as such
    assert any("added moisture" in s for s in res["insights"])


# 16
def test_replay_is_labeled_as_opportunity_not_counterfactual():
    cfg = example_config()
    cfg.control.surface_temp_estimate_f = 70
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(240):
        rows.append({"ts": (t0 + timedelta(minutes=5 * i)).isoformat(), "source": "mock",
                     "indoor_t_c": 26.0, "indoor_rh": 80.0, "outdoor_t_c": 24.0, "outdoor_rh": 60.0,
                     "outdoor_td_c": 15.8, "surface_t_c": 24.0, "surface_source": "measured",
                     "fans_on": 0, "power_w": 0, "removal_g_h": 0, "potential_removal_g_h": 500,
                     "reason_code": "DRYING", "indoor_td_c": 22.3, "zones": {}})
    r = analysis.replay(rows, cfg, ZoneInfo("America/Chicago"))
    assert r["kind"] == "opportunity_replay" and r["counterfactual"] is False and r["measured_inputs"] is True
    assert "does not show what basement humidity would have been" in r["label"]
    by = {x["id"]: x for x in r["results"]}
    assert by["continuous"]["runtime_h"] == pytest.approx(20, abs=0.1)
    assert by["off"]["runtime_h"] == 0
    assert by["optimized"]["opportunity_transfer_gal"] > 0
    prof = analysis.recorded_profile(rows)
    assert prof.source == "recorded" and "modeled" in prof.notes[0].lower()
    sim = compare(SimParams(duration_h=12), prof, cfg, ["off", "optimized"])
    assert sim["kind"] == "modeled_simulation" and "Modeled" in sim["label"]


def test_builtin_profiles_are_labeled_synthetic():
    for p in climate.BUILTIN_PROFILES.values():
        assert p.source == "synthetic"
        assert any("not NOAA" in n for n in p.notes)


def test_csv_import():
    text = "hour,temp_f,rh_pct\n0,80,70\n1,79,72\n2,78,75\n"
    p = climate.profile_from_csv(text)
    assert p.hours == 3 and p.source == "imported"
    assert p.dewpoint_f[0] == pytest.approx(69.4, abs=0.2)
    p2 = climate.profile_from_csv("temp_c,dewpoint_c\n25,20\n26,21\n")
    assert p2.temp_f[0] == pytest.approx(77)
    with pytest.raises(ValueError):
        climate.profile_from_csv("temp_f\n80\n81\n")
    from pathlib import Path
    sample = Path(__file__).parents[2] / "examples" / "synthetic_nola_summer_day.csv"
    p3 = climate.profile_from_csv(sample.read_text())
    assert p3.hours == 24


def test_stats_from_history():
    cfg = example_config()
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    rows = [{"ts": (t0 + timedelta(minutes=2 * i)).isoformat(), "source": "mock", "fans_on": 2 if 10 <= i < 40 else 0,
             "removal_g_h": 300 if 10 <= i < 40 else 0, "power_w": 56 if 10 <= i < 40 else 0,
             "indoor_rh": 72.0, "potential_removal_g_h": 300, "reason_code": "DRYING"} for i in range(60)]
    s = analysis.stats(rows, cfg)
    assert s["fan_runtime_h"] == pytest.approx(1.0)
    assert s["switch_cycles"] == 1
    assert s["electricity_kwh"] == pytest.approx(0.056)
    assert s["hours_above_high"] == pytest.approx(59 * 2 / 60)
    assert s["net_vapor_removed_label"] == "modeled"
