"""Calibration recovers known parameters from synthetic history (Phase 3)."""

import random
from datetime import timedelta

import pytest

from ventctl import calibration, climate
from ventctl.config import example_config
from ventctl.simulation import BasementModel, SimParams, sim_fans

from .helpers import T0


def synthetic_history(hours=96, fans_always=None, seed=3):
    p = SimParams(buffer_capacity_kg=0, temp_variation_f=0, conditioned_air_fraction=0, natural_ach=0.3,
                  moisture_source_pints_day=6, fan_count=2, fan_cfm=85, initial_rh_pct=80)
    cfg = example_config()
    cfg.house.conditioned_air_fraction = 0
    model = BasementModel(p, climate.get_profile("nola_front"), sim_fans(p), cfg.house)
    rng = random.Random(seed)
    rows, on, until = [], False, 0
    for k in range(hours * 60):
        if k >= until:
            on = rng.random() < 0.5 if fans_always is None else fans_always
            until = k + rng.randint(60, 180)
        out = model.outdoor_at(k / 60)
        if k % 2 == 0:
            rows.append({"ts": (T0 + timedelta(minutes=k)).isoformat(), "source": "test", "indoor_w": model.w,
                         "outdoor_w": out.w, "indoor_t_c": model.t_c, "fans_on": 2 if on else 0, "fans_verified": 1})
        model.step(k / 60, 60, model.fans if on else [], outdoor=out)
    return rows, cfg


def test_calibration_recovers_known_parameters():
    rows, cfg = synthetic_history()
    r = calibration.calibrate(rows, cfg)
    assert r["identifiable"] and r["r_squared"] > 0.9
    p = {x["id"]: x for x in r["parameters"]}
    assert p["fan_cfm"]["estimate"] == pytest.approx(170, rel=0.15)
    assert p["fan_cfm"]["low"] < 170 * 1.15 and p["fan_cfm"]["high"] > 170 * 0.85
    assert p["natural_ach"]["estimate"] == pytest.approx(0.3, abs=0.15)
    assert p["baseline_gain"]["estimate"] == pytest.approx(6 / 8, rel=0.3)  # 6 pints/day = 0.75 gal/day
    assert "Not applied automatically" in r["label"]


def test_calibration_reports_limits_when_fans_never_cycle():
    rows, cfg = synthetic_history(hours=48, fans_always=False)
    r = calibration.calibrate(rows, cfg)
    assert not any(x["id"] == "fan_cfm" for x in r["parameters"])
    assert any("cannot be separated" in s for s in r["limitations"])


def test_calibration_needs_enough_data():
    rows, cfg = synthetic_history(hours=4)
    r = calibration.calibrate(rows, cfg)
    assert not r["identifiable"] and r["parameters"] == []
