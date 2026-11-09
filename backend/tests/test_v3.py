"""Version 3: zones, concrete surface probe, internal circulation (validation scenarios A-J)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from ventctl import circulation, psychro, surfaces, zones
from ventctl.circulation import CircState, decide_circulator, mix_masses
from ventctl.climate import get_profile
from ventctl.config import example_config
from ventctl.controller import Controller
from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
from ventctl.clock import ManualClock
from ventctl.simulation import SimParams
from ventctl.storage import Storage
from ventctl.surfaces import SurfaceStatus, classify, surface_rh
from ventctl.units import c_to_f, f_to_c
from ventctl.zones import ZoneStatus, gradient
from ventctl.zonesim import compare_circulation

from .helpers import T0, air_f


def zs(cfg, zid, t_f, rh, minutes=None, calibrated=False, colocated=False) -> ZoneStatus:
    z = cfg.zone(zid)
    a = air_f(t_f, rh)
    cs = cfg.circulation
    unc = cs.rh_uncertainty_calibrated_pct if calibrated else cs.rh_uncertainty_pct
    return ZoneStatus(zid, z.name, a, [], "ok", "good" if calibrated else "uncalibrated", unc,
                      zones.w_uncertainty(a, unc, cs.temp_uncertainty_f), zones.rh_level(rh, cs), {},
                      minutes or {"elevated": 0, "high": 0, "critical": 0}, colocated, z.volume_ft3,
                      z.on_ventilation_path, z.ventilation_exposure)


def probe(cfg, zone: ZoneStatus, surface_f: float, sid="mock-slab") -> SurfaceStatus:
    ss = cfg.surfaces
    t = f_to_c(surface_f)
    st = SurfaceStatus(sid, "Foundation wall", zone.id, zone.name, t, "ok", zone.air, "zone")
    st.margin_f = c_to_f(t) - c_to_f(zone.air.dew_point_c)
    st.uncertainty_f = (ss.probe_uncertainty_f ** 2 + ss.dew_point_uncertainty_f ** 2) ** 0.5
    st.conservative_margin_f = st.margin_f - st.uncertainty_f
    st.surface_rh = surface_rh(zone.air, t)
    st.risk = classify(st.conservative_margin_f, ss)
    return st


def auto_cfg():
    cfg = example_config()
    cfg.circulation.mode = "auto"
    for c in cfg.circulators:
        c.commissioned = True
    return cfg


def circ(cfg, cid):
    return next(c for c in cfg.circulators if c.id == cid)


# ------------------------------------------------------------------ physics
def test_internal_mixing_conserves_water_and_energy_weighted_mean():
    a, b = air_f(72, 55), air_f(70, 72)
    ma, mb = circulation.zone_dry_air_kg(a, 16000), circulation.zone_dry_air_kg(b, 4500)
    before = ma * a.w + mb * b.w
    q = circulation.exchange_kg_s(60, a, b)
    ta, wa, tb, wb = mix_masses(a.t_c, a.w, ma, b.t_c, b.w, mb, q, 3600)
    assert ma * wa + mb * wb == pytest.approx(before, rel=1e-12)
    assert wb < b.w and wa > a.w  # moves toward the mass-weighted mean, never overshoots
    w_mean = before / (ma + mb)
    assert min(wa, wb) <= w_mean <= max(wa, wb)
    ta2, wa2, tb2, wb2 = mix_masses(a.t_c, a.w, ma, b.t_c, b.w, mb, q, 10 ** 8)
    assert wa2 == pytest.approx(w_mean, rel=1e-9) and wb2 == pytest.approx(w_mean, rel=1e-9)


def test_multizone_simulation_conserves_water_and_reports_all_strategies():
    cfg = example_config()
    res = compare_circulation(SimParams(duration_h=12), get_profile("nola_front"), cfg)
    ids = {r["id"] for r in res["results"]}
    assert ids == {"none", "fan1", "fan2", "both", "humidity", "ventilation", "adaptive", "periodic"}
    for r in res["results"]:
        s = r["summary"]
        assert abs(s["water_balance_error_kg"]) < 1e-6
        assert s["circulation_removed_gal"] == 0
        for key in ("worst_zone_mean_rh", "max_zone_rh", "mean_gradient_g_kg", "surface_risk_hours",
                    "circulator_hours", "kwh"):
            assert key in s
    none = next(r for r in res["results"] if r["id"] == "none")["summary"]
    both = next(r for r in res["results"] if r["id"] == "both")["summary"]
    assert sum(both["mean_gradient_g_kg"].values()) < sum(none["mean_gradient_g_kg"].values())
    assert sum(both["circulator_hours"].values()) > 0 and sum(none["circulator_hours"].values()) == 0


def test_gradient_needs_to_exceed_combined_uncertainty():
    cfg = example_config()
    a = zs(cfg, "main", 72, 55)
    b = zs(cfg, "pocket", 72, 56.5)  # 1.5% RH apart: inside 3% sensor uncertainty
    g = gradient(b, a, cfg)
    assert not g["reliable"] and g["reason"] == "GRADIENT_WITHIN_SENSOR_UNCERTAINTY"
    b2 = zs(cfg, "pocket", 72, 66)
    assert gradient(b2, a, cfg)["reliable"]
    # after side-by-side calibration the same 1.5% difference is still within 2 x 1.5% combined
    a_c, b_c = zs(cfg, "main", 72, 55, calibrated=True), zs(cfg, "pocket", 72, 58, calibrated=True)
    assert gradient(b_c, a_c, cfg)["reliable"]


def test_surface_rh_and_classes():
    cfg = example_config()
    lo = air_f(70, td_f=59)
    assert surface_rh(lo, f_to_c(59)) == pytest.approx(1.0, abs=1e-3)
    assert surface_rh(lo, f_to_c(55)) > 1.0  # uncapped internally
    st = probe(cfg, zs(cfg, "damp", 70, psychro.AirState.from_t_rh(f_to_c(70), 0.6).rh * 100), 70)
    assert st.to_dict()["surface_rh_pct"] <= 100
    ss = cfg.surfaces
    # 0.5 F above the dew point is not "safe": the conservative margin includes sensor uncertainty
    assert classify(0.5 - 1.414, ss) == "possible"
    assert classify(6, ss) == "lower" and classify(4, ss) == "monitor" and classify(1, ss) == "elevated"
    # hysteresis: improving from elevated needs margin to clear 3 F + 0.5 F
    assert classify(3.2, ss, "elevated") == "elevated"
    assert classify(3.6, ss, "elevated") == "monitor"


# ------------------------------------------------------------------ scenarios
def test_scenario_a_isolated_humid_pocket_turns_on_fan1():
    cfg = auto_cfg()
    z = {"main": zs(cfg, "main", 72, 55), "pocket": zs(cfg, "pocket", 70, 70, {"elevated": 40, "high": 20,
                                                                             "critical": 0}),
         "damp": zs(cfg, "damp", 70, 60)}
    d = decide_circulator(circ(cfg, "circ-1"), cfg, z, [], CircState(), T0)
    assert d.target_on and d.benefit == "beneficial" and d.commanding
    assert d.reason_code in ("LOCAL_HUMIDITY_HIGH", "BENEFICIAL_ZONE_MIXING")
    assert "ZONE_MOISTURE_GRADIENT" in d.reasons
    assert d.counts_as_moisture_removal is False


def test_scenario_b_cold_pocket_high_rh_is_not_called_wetter():
    cfg = auto_cfg()
    main = zs(cfg, "main", 72, 55)
    w = main.air.w
    cold = psychro.AirState.from_t_w(f_to_c(66), w)  # same moisture content, cooler
    pocket = zs(cfg, "pocket", 66, cold.rh * 100, {"elevated": 40, "high": 0, "critical": 0})
    g = gradient(pocket, main, cfg)
    assert not g["reliable"] and g["reason"] == "RH_DIFFERENCE_FROM_TEMPERATURE"
    d = decide_circulator(circ(cfg, "circ-1"), cfg, {"main": main, "pocket": pocket,
                                                     "damp": zs(cfg, "damp", 68, 55)}, [], CircState(), T0)
    assert "ZONE_MOISTURE_GRADIENT" not in d.reasons
    if d.target_on:  # warming a cold zone may help, but it is reported as warming, not water removal
        assert "COOL_ZONE_WARMING" in d.reasons


def test_scenario_c_concrete_below_dew_point_alerts_and_evaluates_fans():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    ctrl = Controller(cfg, Storage(":memory:"), MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    world.set_surface_c(world.mz.zone_air("damp").dew_point_c - 2)
    asyncio.run(ctrl.tick())
    assert "SURFACE_HIGH_RISK:mock-slab" in ctrl.alerts
    a = ctrl.alerts["SURFACE_HIGH_RISK:mock-slab"]
    text = (a.get("message") if isinstance(a, dict) else getattr(a, "message", str(a))) or ""
    assert "visibly wet" not in text.lower() or "not" in text.lower()
    s = ctrl.surface_status[0]
    assert s.risk == "possible" and s.local_source == "zone"
    # every circulator has a decision that looked at the surface
    assert set(ctrl.circ_decisions) == {"circ-1", "circ-2"}
    d2 = ctrl.circ_decisions["circ-2"]
    assert any(c.id == "surface" for c in d2.checks)


def test_scenario_d_humid_outdoors_cold_concrete_flags_outdoor_air_only():
    cfg = example_config()
    damp = zs(cfg, "damp", 68, None or 45)  # dry indoors: dew point about 46 F
    st = probe(cfg, damp, 55)
    assert st.risk == "lower"
    out = air_f(80, td_f=66)
    om = 55 - c_to_f(out.dew_point_c)
    assert om < 0
    # build via surface_statuses so the outdoor fields come from the real code path
    from types import SimpleNamespace
    from ventctl.config import SensorRole
    m = next(x for x in cfg.sensors.mappings if x.role == SensorRole.SURFACE)
    r = SimpleNamespace(mapping=m, ok=True, temp_c=f_to_c(55), status="ok", message="")
    snap = SimpleNamespace(by_role=lambda role: [r] if role == SensorRole.SURFACE else [])
    [s] = surfaces.surface_statuses(snap, cfg, {"damp": damp}, None, T0, outdoor=out)
    assert s.risk == "lower"  # current condensation is not claimed
    assert s.ventilation["outdoor_air_would_condense"] is True


def test_scenario_e_cold_dry_ventilation_uses_probe_in_tamo():
    from ventctl.config import ControlPolicy
    from ventctl.engine import GroupState, decide
    from .helpers import cond
    cfg = example_config()
    cfg.control.policy = ControlPolicy.TAMO
    ind = air_f(66, 70)
    out = air_f(40, 50)
    c = cond(ind, out, surface_f=60)
    c.surfaces = [{"name": "Wall", "t_c": f_to_c(60), "sensor_id": "s", "zone": "damp",
                   "local": ind, "exposure": 0.25}]
    d = decide(c, GroupState(), cfg, cfg.groups[0], T0)
    cd = d.tamo["condensation"]
    assert cd["surfaces"] and cd["worst_surface"]
    s = cd["surfaces"][0]
    # the surface cools with the air, but the dew point falls faster: the margin does not get worse
    assert s["surface_on_f"] < s["surface_off_f"]
    assert s["margin_on_f"] >= s["margin_off_f"] - 0.1
    assert d.tamo["terms"]["benefit_g_h"] > 0


def test_scenario_f_exterior_ventilation_support_turns_on_assigned_fan_only():
    cfg = auto_cfg()
    z = {"main": zs(cfg, "main", 72, 52), "pocket": zs(cfg, "pocket", 72, 54), "damp": zs(cfg, "damp", 71, 54)}
    st1, st2 = CircState(), CircState()
    d1 = decide_circulator(circ(cfg, "circ-1"), cfg, z, [], st1, T0, exterior={"on": True, "drying": True})
    d2 = decide_circulator(circ(cfg, "circ-2"), cfg, z, [], st2, T0, exterior={"on": True, "drying": True})
    assert d1.target_on and d1.reason_code == "VENTILATION_SUPPORT"
    assert not d2.target_on  # circ-2 is not assigned a ventilation-support role
    # continues briefly after the exterior fan stops
    st1.is_on, st1.last_change = True, T0
    d1b = decide_circulator(circ(cfg, "circ-1"), cfg, z, [], st1, T0 + timedelta(minutes=5),
                            exterior={"on": False})
    assert d1b.target_on and d1b.reason_code in ("POST_VENTILATION_SUPPORT", "VENTILATION_SUPPORT")


def test_scenario_g_fan_would_carry_high_dew_point_air_to_cold_wall():
    cfg = auto_cfg()
    pocket = zs(cfg, "pocket", 72, 72, {"elevated": 60, "high": 40, "critical": 0})  # dew point ~62 F
    damp = zs(cfg, "damp", 64, 55)  # dew point ~48 F
    z = {"main": zs(cfg, "main", 72, 55), "pocket": pocket, "damp": damp}
    st = probe(cfg, damp, 55)
    d = decide_circulator(circ(cfg, "circ-2"), cfg, z, [st], CircState(), T0)
    assert not d.target_on
    assert d.reason_code == "CIRCULATION_WORSENS_SURFACE_RISK" and d.benefit == "adverse"
    # the opposite case: lower-dew-point air toward a wall at risk is allowed
    damp2 = zs(cfg, "damp", 62, 90)
    pocket2 = zs(cfg, "pocket", 70, 45)
    st2 = probe(cfg, damp2, 58)
    d2 = decide_circulator(circ(cfg, "circ-2"), cfg, {"main": z["main"], "pocket": pocket2, "damp": damp2}, [st2],
                           CircState(), T0)
    assert d2.target_on and d2.reason_code in ("SURFACE_PROTECTION", "LOCAL_HUMIDITY_HIGH", "BENEFICIAL_ZONE_MIXING")


def test_scenario_h_colocated_sensor_disagreement():
    samples = {"a": [(22.0, 55.0)] * 10, "b": [(22.0, 55.4)] * 10, "c": [(22.0, 61.0)] * 10}
    res = zones.compare_sensors(samples)
    assert res["ok"] and res["disagreement"]
    assert res["sensors"]["c"]["disagrees"] and not res["sensors"]["a"]["disagrees"]
    assert res["sensors"]["c"]["suggested_rh_offset_pct"] == pytest.approx(-5.6, abs=0.2)
    cfg = auto_cfg()
    a = zs(cfg, "main", 72, 55, colocated=True)
    b = zs(cfg, "pocket", 72, 70, colocated=True)
    g = gradient(b, a, cfg)
    assert not g["reliable"] and g["reason"] == "SENSOR_COMPARISON_ACTIVE"
    d = decide_circulator(circ(cfg, "circ-1"), cfg, {"main": a, "pocket": b, "damp": zs(cfg, "damp", 70, 55)}, [],
                          CircState(), T0)
    assert not d.target_on


def test_scenario_h_controller_comparison_and_apply_offsets():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    ids = ["mock-bsmt-1", "mock-bsmt-2", "mock-bsmt-3"]
    ctrl.comparison_start(ids)

    async def run():
        for _ in range(10):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run())
    st = ctrl.comparison_status()
    assert st["active"] and st["result"]["ok"]
    # while comparing, zone readings are not used for circulation decisions
    assert all(z.colocated for zid, z in ctrl.zone_status.items())
    res = ctrl.comparison_stop(apply=True)
    assert res["applied"]
    m = next(x for x in ctrl.config.sensors.mappings if x.sensor_id == "mock-bsmt-3")
    assert m.calibrated_at is not None
    assert not any(z.colocated for z in ctrl.zone_status.values())


def test_scenario_i_plug_failure_is_reported_and_not_retried_rapidly():
    cfg = auto_cfg()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    world.switch_faults["mock-plug-2"] = "unreachable"
    ctrl.set_circulator_manual("circ-1", "on", 30)

    async def run(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run(3))
    s = ctrl.status()["circulation"]["circulators"]
    c1 = next(c for c in s if c["id"] == "circ-1")
    assert c1["verified"] is False and c1["state"] == "unknown" and c1["is_on"] is None  # not claimed ON
    assert ctrl.circ_decisions["circ-1"].reason_code == "PLUG_UNVERIFIED"
    assert "SWITCH_OFFLINE:mock-plug-2" in ctrl.alerts
    # a plug that answers but does not switch: one failure, then a lockout instead of rapid retries
    world.switch_faults["mock-plug-2"] = "stuck"
    asyncio.run(run(5))
    fails = [e for e in store.events(kind="fault") if e["details"].get("switch") == "mock-plug-2"]
    assert len(fails) == 1 and fails[0]["reason_code"] == "COMMAND_FAILURE"
    s = ctrl.status()["circulation"]["circulators"]
    c1 = next(c for c in s if c["id"] == "circ-1")
    assert c1["state"] != "on"
    # the exterior fan and the other circulator keep working
    assert ctrl.decisions["main"] is not None
    c2 = next(c for c in s if c["id"] == "circ-2")
    assert c2["verified"] is True


def test_scenario_j_no_removal_path_is_not_called_moisture_removal():
    cfg = auto_cfg()
    hot = {"elevated": 90, "high": 60, "critical": 0}
    z = {"main": zs(cfg, "main", 74, 68, hot), "pocket": zs(cfg, "pocket", 72, 80, hot),
         "damp": zs(cfg, "damp", 71, 74, hot)}
    for cid in ("circ-1", "circ-2"):
        d = decide_circulator(circ(cfg, cid), cfg, z, [], CircState(), T0, exterior={"on": False, "drying": False},
                              dehumidifier_running=False)
        assert "NO_MOISTURE_REMOVAL_PATH" in d.reasons
        assert d.counts_as_moisture_removal is False
        if d.target_on:
            assert "redistributes" in d.explanation


# ------------------------------------------------------------------ shadow mode and manual
def test_circulators_never_switch_in_shadow_mode():
    cfg = example_config()
    assert cfg.circulation.mode == "shadow"
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    ctrl = Controller(cfg, Storage(":memory:"), MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())

    async def run():
        for _ in range(90):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run())
    assert not world.switch_on.get("mock-plug-2") and not world.switch_on.get("mock-plug-3")
    assert all(not d.commanding for d in ctrl.circ_decisions.values())
    assert "CIRCULATOR_NOT_COMMISSIONED:circ-1" in ctrl.alerts or ctrl.circ_shadow


def test_manual_circulator_override_switches_and_expires():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    ctrl.set_circulator_manual("circ-2", "on", 20)

    async def run(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run(2))
    assert world.switch_on.get("mock-plug-3") is True
    assert ctrl.circ_decisions["circ-2"].reason_code == "MANUAL_ON"
    asyncio.run(run(25))
    assert not world.switch_on.get("mock-plug-3")
    cycles = store.circulation_cycles("circ-2")
    assert cycles and cycles[0]["reason"] == "MANUAL_ON"
    assert ctrl.circulator_runtime()["circ-2"]["today_h"] > 0.2


def test_water_intrusion_lockout_blocks_manual_circulator():
    cfg = example_config()
    cfg.control.water_intrusion_lockout = True
    d = decide_circulator(circ(cfg, "circ-1"), cfg, {}, [], CircState(manual=True), T0)
    assert not d.target_on and d.safety and d.reason_code == "WATER_INTRUSION_LOCKOUT"


def test_zone_sensor_failure_marks_zone_unavailable():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    ctrl = Controller(cfg, Storage(":memory:"), MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    world.sensor_faults.add("mock-bsmt-2")

    async def run(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run(40))
    p = ctrl.zone_status["pocket"]
    assert p.air is None and p.status == "unavailable"
    assert ctrl.circ_decisions["circ-1"].reason_code in ("ZONE_SENSOR_UNAVAILABLE", "VENTILATION_SUPPORT",
                                                         "POST_VENTILATION_SUPPORT", "NO_TRIGGER",
                                                         "SURFACE_RISK_EFFECT_UNKNOWN")
    assert ctrl.decisions["main"] is not None  # exterior control keeps running


def test_probe_failure_disables_probe_predictions_and_notifies():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    ctrl = Controller(cfg, Storage(":memory:"), MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    world.sensor_faults.add("mock-slab")

    async def run(n):
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(run(40))
    assert "SURFACE_PROBE_UNAVAILABLE:mock-slab" in ctrl.alerts
    s = ctrl.surface_status[0]
    assert s.t_c is None and s.risk is None


def test_effectiveness_learning_detects_a_fan_that_mixes():
    from ventctl import analysis
    cfg = example_config()
    rows, cycles = [], []
    t = T0
    gap = 2.0  # g/kg between main and pocket
    w_main = 0.010
    for cyc in range(4):
        for i in range(60):  # fan off: gradient grows slowly
            gap += 0.004
            rows.append({"ts": t.isoformat(), "fans_on": 0, "zones": {"main": {"w": w_main},
                                                                    "pocket": {"w": w_main + gap / 1000}},
                         "circ": {"circ-1": {"on": 0}}})
            t += timedelta(minutes=1)
        cycles.append({"circulator_id": "circ-1", "ts_start": t.isoformat(), "context": {}})
        for i in range(30):  # fan on: gradient shrinks
            gap *= 0.98
            rows.append({"ts": t.isoformat(), "fans_on": 0, "zones": {"main": {"w": w_main},
                                                                    "pocket": {"w": w_main + gap / 1000}},
                         "circ": {"circ-1": {"on": 1}}})
            t += timedelta(minutes=1)
    res = analysis.circulation_effectiveness(rows, cycles, cfg)
    f1 = next(f for f in res["fans"] if f["id"] == "circ-1")
    assert f1["connections"][0]["verdict"] == "reduces_gradient"
    assert "reliably reduces" in f1["connections"][0]["text"]
    f2 = next(f for f in res["fans"] if f["id"] == "circ-2")
    assert all(k["verdict"] == "insufficient_data" for k in f2["connections"])
