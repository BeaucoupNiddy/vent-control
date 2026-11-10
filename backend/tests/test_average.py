"""Whole-basement average: moisture-weighted across zones, monitoring only."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from ventctl import psychro, zones
from ventctl.clock import ManualClock
from ventctl.config import example_config
from ventctl.controller import Controller
from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
from ventctl.storage import Storage

from .helpers import T0, air_f
from .test_v3 import zs


def test_average_mixes_moisture_not_rh():
    cfg = example_config()
    cool = zs(cfg, "main", 65, 70)
    warm = zs(cfg, "pocket", 75, 50)
    for z in cfg.zones:
        z.volume_ft3 = 8000
    cool.volume_ft3 = warm.volume_ft3 = 8000
    avg = zones.basement_average({"main": cool, "pocket": warm}, cfg)
    # moisture content is the dry-air-mass-weighted mean of the zones
    m1 = 8000 * psychro.dry_air_density(cool.air.t_c, cool.air.w)
    m2 = 8000 * psychro.dry_air_density(warm.air.t_c, warm.air.w)
    w = (m1 * cool.air.w + m2 * warm.air.w) / (m1 + m2)
    assert avg["w_g_kg"] == pytest.approx(w * 1000, rel=1e-9)
    # RH is derived from the averaged air, which differs from the plain mean of the two RH readings
    assert avg["rh_pct"] == pytest.approx(psychro.rh_from_humidity_ratio(avg["t_c"], w) * 100, rel=1e-6)
    assert abs(avg["rh_pct"] - 60.0) > 0.3
    assert avg["count"] == 2 and not avg["partial"]
    assert avg["rh_min_pct"] == pytest.approx(50) and avg["rh_max_pct"] == pytest.approx(70)


def test_average_weights_by_zone_volume():
    cfg = example_config()
    big = zs(cfg, "main", 70, 50)
    small = zs(cfg, "pocket", 70, 70)
    big.volume_ft3, small.volume_ft3 = 18000, 2000
    avg = zones.basement_average({"main": big, "pocket": small}, cfg)
    assert avg["w_g_kg"] < (big.air.w + small.air.w) / 2 * 1000  # pulled toward the big dry zone
    assert avg["included"][0]["weight"] == pytest.approx(0.9, abs=0.01)


def test_average_skips_missing_and_comparison_zones():
    cfg = example_config()
    a = zs(cfg, "main", 70, 55)
    b = zs(cfg, "pocket", 70, 65, colocated=True)
    c = zs(cfg, "damp", 70, 60)
    c.air, c.status = None, "unavailable"
    avg = zones.basement_average({"main": a, "pocket": b, "damp": c}, cfg)
    assert avg["count"] == 1 and avg["rh_pct"] == pytest.approx(55, abs=0.01)
    assert avg["partial"] and set(avg["missing"]) == {b.name, c.name}
    assert zones.basement_average({"damp": c}, cfg) is None


def test_controller_reports_and_stores_average_but_controls_on_worst_zone():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())

    async def run():
        for _ in range(20):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run())
    st = ctrl.status()
    avg = st["basement_average"]
    assert avg is not None and avg["count"] == len([z for z in ctrl.zone_status.values() if z.ok])
    lo = min(z.air.w for z in ctrl.zone_status.values() if z.ok)
    hi = max(z.air.w for z in ctrl.zone_status.values() if z.ok)
    assert lo * 1000 - 1e-9 <= avg["w_g_kg"] <= hi * 1000 + 1e-9
    rows = store.history(T0, clock.now())
    assert rows and rows[-1]["avg_rh"] == pytest.approx(avg["rh_pct"], abs=1.0)
    # control still uses the wettest zone (default aggregation), not the average
    assert cfg.control.zone_aggregation == "wettest"
    ind = st["groups"][0]["decision"]["metrics"]["indoor"]
    assert ind["w"] == pytest.approx(hi, rel=1e-6)
