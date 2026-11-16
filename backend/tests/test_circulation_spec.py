"""Circulation fan spec (2026-11-16): roles, three fans plus a blower, activation delay and dew-point
hysteresis, dehumidifier support, exhaust coordination, one resolved mode, and scenarios A-J."""

from __future__ import annotations

from datetime import timedelta

from ventctl import circulation, zones
from ventctl.circulation import CircState, decide_circulator, overview
from ventctl.config import AppConfig, Circulator, example_config, upgrade_defaults
from ventctl.surfaces import SurfaceStatus, classify, surface_rh
from ventctl.units import c_to_f, f_to_c
from ventctl.zones import ZoneStatus

from .helpers import T0, air_f


def zdp(cfg, zid, t_f, td_f) -> ZoneStatus:
    """Zone at a given temperature and dew point (calibrated sensors, nothing persistent yet)."""
    z = cfg.zone(zid)
    a = air_f(t_f, td_f=td_f)
    cs = cfg.circulation
    unc = cs.rh_uncertainty_calibrated_pct
    return ZoneStatus(zid, z.name, a, [], "ok", "good", unc, zones.w_uncertainty(a, unc, cs.temp_uncertainty_f),
                      zones.rh_level(a.rh * 100, cs), {}, {"elevated": 0, "high": 0, "critical": 0}, False,
                      z.volume_ft3, z.on_ventilation_path, z.ventilation_exposure)


def missing(cfg, zid) -> ZoneStatus:
    z = cfg.zone(zid)
    return ZoneStatus(zid, z.name, None, [], "unavailable", "unavailable", 3.0, volume_ft3=z.volume_ft3)


def probe(cfg, zone: ZoneStatus, surface_f: float | None) -> SurfaceStatus:
    if surface_f is None:  # probe configured but not reporting
        return SurfaceStatus("mock-slab", "Slab", zone.id, zone.name, None, "stale")
    ss = cfg.surfaces
    t = f_to_c(surface_f)
    st = SurfaceStatus("mock-slab", "Slab", zone.id, zone.name, t, "ok", zone.air, "zone")
    st.margin_f = c_to_f(t) - c_to_f(zone.air.dew_point_c)
    st.uncertainty_f = (ss.probe_uncertainty_f ** 2 + ss.dew_point_uncertainty_f ** 2) ** 0.5
    st.conservative_margin_f = st.margin_f - st.uncertainty_f
    st.surface_rh = surface_rh(zone.air, t)
    st.risk = classify(st.conservative_margin_f, ss)
    return st


def auto_cfg() -> AppConfig:
    cfg = example_config()
    cfg.circulation.mode = "auto"
    for c in cfg.circulators:
        c.commissioned = True
    return cfg


def fan(cfg, cid) -> Circulator:
    return next(c for c in cfg.circulators if c.id == cid)


def safe_zones(cfg, main_td=56, pocket_td=56, damp_td=56):
    return {"main": zdp(cfg, "main", 72, main_td), "pocket": zdp(cfg, "pocket", 71, pocket_td),
            "damp": zdp(cfg, "damp", 70, damp_td)}


# ------------------------------------------------------------------ configuration
def test_three_fans_and_optional_blower_are_config_driven():
    cfg = example_config()
    ids = [c.id for c in cfg.circulators]
    assert ids == ["circ-1", "circ-2", "circ-3", "blower"]
    assert fan(cfg, "blower").kind == "blower" and not fan(cfg, "blower").enabled
    assert fan(cfg, "circ-3").role == "general_mixing" and fan(cfg, "circ-3").zone == "main"
    # a fifth fan needs no code change
    cfg.circulators.append(Circulator(id="circ-5", name="Five", role="general_mixing", zone="damp"))
    AppConfig.model_validate(cfg.model_dump())


def test_upgrade_adds_third_fan_and_blower_only_to_untouched_setup():
    cfg = example_config()
    cfg.circulators = cfg.circulators[:2]
    for c in cfg.circulators:
        c.switch_id = ""
    changes = upgrade_defaults(cfg, 2)
    assert [c.id for c in cfg.circulators] == ["circ-1", "circ-2", "circ-3", "blower"]
    assert any("blower" in x for x in changes)
    # a customized list is left alone
    cfg2 = example_config()
    cfg2.circulators = cfg2.circulators[:2]
    upgrade_defaults(cfg2, 2)  # circ-1 still has a plug assigned
    assert len(cfg2.circulators) == 2


def test_per_fan_minimum_times_override_defaults():
    cfg = auto_cfg()
    c = fan(cfg, "circ-1")
    c.min_on_minutes = 40
    st = CircState(is_on=True, last_change=T0, last_reason="BENEFICIAL_ZONE_MIXING")
    d = decide_circulator(c, cfg, safe_zones(cfg), [], st, T0 + timedelta(minutes=30))
    assert d.held and d.target_on and d.next_eligible_switch == T0 + timedelta(minutes=40)


# ------------------------------------------------------------------ scenarios A-J
def test_a_humid_outdoor_circulation_still_allowed():
    """Exhaust off (outdoor too humid); a zone imbalance still runs circulation on its own."""
    cfg = auto_cfg()
    z = safe_zones(cfg, main_td=56, pocket_td=63)
    st = CircState()
    ext = {"on": False, "drying": False, "auto_on": False}
    decide_circulator(fan(cfg, "circ-1"), cfg, z, [], st, T0, ext)
    d = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], st, T0 + timedelta(minutes=11), ext)
    assert d.target_on and d.reason_code == "BENEFICIAL_ZONE_MIXING"


def test_b_zone_imbalance_waits_for_activation_delay_then_runs():
    cfg = auto_cfg()
    z = safe_zones(cfg, main_td=58, pocket_td=65)
    st = CircState()
    d0 = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], st, T0)
    assert not d0.target_on and d0.reason_code == "ZONE_DIFFERENCE_PENDING"
    d1 = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], st, T0 + timedelta(minutes=5))
    assert not d1.target_on
    d2 = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], st, T0 + timedelta(minutes=12))
    assert d2.target_on and "ZONE_MOISTURE_GRADIENT" in d2.reasons
    assert "dew point is 7.0°F above" in d2.explanation and "for 12 min" in d2.explanation
    assert d2.counts_as_moisture_removal is False


def test_c_cold_concrete_high_surface_rh_restricts_surface_fan():
    cfg = auto_cfg()
    fan(cfg, "circ-2").role = "surface_drying"
    z = safe_zones(cfg, pocket_td=60, damp_td=60)  # the neighbouring zone is no drier
    s = probe(cfg, z["damp"], 61.5)
    assert s.surface_rh * 100 >= 90
    d = decide_circulator(fan(cfg, "circ-2"), cfg, z, [s], CircState(), T0)
    assert not d.target_on and d.reason_code in ("SURFACE_DRYING_RESTRICTED", "CIRCULATION_WORSENS_SURFACE_RISK",
                                                 "SURFACE_RISK_EFFECT_UNKNOWN")
    ov = overview(cfg, z, [s], {"circ-2": d})
    assert ov["mode"] == "SURFACE_MOISTURE_RISK" and ov["surface_risk"] in ("high", "condensation_possible")
    # genuinely drier air next door (lower dew point, judged by vapor content): bringing it in is allowed
    z = safe_zones(cfg, pocket_td=54, damp_td=60)
    s = probe(cfg, z["damp"], 61.5)
    d = decide_circulator(fan(cfg, "circ-2"), cfg, z, [s], CircState(), T0)
    assert d.target_on and d.reason_code == "SURFACE_PROTECTION"


def test_high_surface_rh_never_forces_a_fan_on():
    cfg = auto_cfg()
    for c in cfg.circulators:
        c.role = "surface_drying"
    z = safe_zones(cfg, main_td=60, pocket_td=60, damp_td=60)
    s = probe(cfg, z["damp"], 61)
    for c in cfg.circulators:
        d = decide_circulator(c, cfg, z, [s], CircState(), T0)
        assert not d.target_on


def test_d_dehumidifier_confirmed_running_gets_support():
    cfg = auto_cfg()
    cfg.dehumidifier.enabled = True
    cfg.dehumidifier.monitor_switch_id = "mock-meter"
    cfg.dehumidifier.zone = "pocket"
    c = fan(cfg, "circ-2")  # pocket <-> damp
    c.role = "dehumidifier_support"
    z = {"main": zdp(cfg, "main", 72, 56), "pocket": zdp(cfg, "pocket", 70, 56), "damp": zdp(cfg, "damp", 68, 58)}
    st = CircState()
    d = decide_circulator(c, cfg, z, [], st, T0, dehumidifier_running=True)
    assert d.target_on and d.reason_code == "DEHUMIDIFIER_SUPPORT"
    st.is_on, st.last_change, st.last_reason = True, T0, d.reason_code
    # dehumidifier stops: short post-run, then off
    d2 = decide_circulator(c, cfg, z, [], st, T0 + timedelta(minutes=5), dehumidifier_running=False)
    assert d2.target_on and d2.reason_code == "POST_DEHUMIDIFIER_RUN"
    st.last_reason = d2.reason_code
    d3 = decide_circulator(c, cfg, z, [], st, T0 + timedelta(minutes=20), dehumidifier_running=False)
    assert not d3.target_on


def test_d_unknown_dehumidifier_state_is_not_invented():
    cfg = auto_cfg()
    cfg.dehumidifier.enabled = True  # level 1: no monitoring plug
    c = fan(cfg, "circ-2")
    c.role = "dehumidifier_support"
    z = {"main": zdp(cfg, "main", 72, 56), "pocket": zdp(cfg, "pocket", 70, 56), "damp": zdp(cfg, "damp", 68, 58)}
    d = decide_circulator(c, cfg, z, [], CircState(), T0, dehumidifier_running=None)
    assert d.reason_code != "DEHUMIDIFIER_SUPPORT"
    assert any(k.id == "dehum" and k.ok is None for k in d.checks)
    ov = overview(cfg, z, [], {"circ-2": d})
    assert ov["dehumidifier"]["level"] == 1 and ov["dehumidifier"]["running"] is None


def test_e_favorable_ventilation_spreads_air_but_avoids_short_circuit():
    cfg = auto_cfg()
    z = safe_zones(cfg)
    ext = {"on": True, "drying": True, "auto_on": True}
    d = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], CircState(), T0, ext)  # main (path) <-> pocket
    assert d.target_on and d.reason_code == "RUN_WITH_EXHAUST"
    ov = overview(cfg, z, [], {"circ-1": d}, ext)
    assert ov["mode"] == "VENTILATION_ASSIST" and ov["exhaust"]["on"]
    # both ends on the exhaust air path: it would push incoming air straight to the exhaust
    cfg.zone("pocket").on_ventilation_path = True
    z = safe_zones(cfg)
    d = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], CircState(), T0, ext)
    assert d.reason_code != "RUN_WITH_EXHAUST" and "VENTILATION_SHORT_CIRCUIT_RISK" in d.reasons


def test_e_in_zone_mixing_fan_runs_with_exhaust():
    cfg = auto_cfg()
    c = fan(cfg, "circ-3")
    c.ventilation_support = True
    d = decide_circulator(c, cfg, safe_zones(cfg), [], CircState(), T0, {"on": True, "drying": True, "auto_on": True})
    assert d.target_on and d.reason_code == "RUN_WITH_EXHAUST"


def test_f_surface_risk_beats_mixing_but_safe_fans_stay_available():
    cfg = auto_cfg()
    fan(cfg, "circ-2").role = "surface_drying"
    z = {"main": zdp(cfg, "main", 72, 56), "pocket": zdp(cfg, "pocket", 71, 63), "damp": zdp(cfg, "damp", 66, 60)}
    s = probe(cfg, z["damp"], 61.5)
    st1 = CircState(trigger_since=T0 - timedelta(minutes=20))
    d1 = decide_circulator(fan(cfg, "circ-1"), cfg, z, [s], st1, T0)  # main <-> pocket: nowhere near the slab
    d2 = decide_circulator(fan(cfg, "circ-2"), cfg, z, [s], CircState(), T0)
    assert d1.target_on
    assert not d2.target_on
    ov = overview(cfg, z, [s], {"circ-1": d1, "circ-2": d2})
    assert ov["mode"] == "SURFACE_MOISTURE_RISK"


def test_g_missing_probe_is_unknown_not_safe():
    cfg = auto_cfg()
    fan(cfg, "circ-2").role = "surface_drying"
    z = safe_zones(cfg)
    for surfs, state in (([], "not_installed"), ([probe(cfg, z["damp"], None)], "offline")):
        d = decide_circulator(fan(cfg, "circ-2"), cfg, z, surfs, CircState(), T0)
        assert not d.target_on and d.reason_code == "SURFACE_DATA_MISSING"
        ov = overview(cfg, z, surfs, {"circ-2": d})
        assert ov["concrete"]["state"] == state and ov["surface_risk"] == "unknown"
        assert "not assumed safe" in ov["concrete"]["label"]
    # other roles keep working without the probe
    d = decide_circulator(fan(cfg, "circ-1"), cfg, z, [], CircState(), T0, {"on": True, "drying": True, "auto_on": True})
    assert d.target_on


def test_h_unconfirmed_plug_is_an_equipment_fault():
    cfg = auto_cfg()
    d = decide_circulator(fan(cfg, "circ-1"), cfg, safe_zones(cfg), [], CircState(is_on=True, verified=False), T0)
    assert not d.target_on and d.reason_code == "PLUG_UNVERIFIED" and d.safety
    assert overview(cfg, safe_zones(cfg), [], {"circ-1": d})["mode"] == "EQUIPMENT_FAULT"


def test_i_water_intrusion_holds_every_fan_off_even_manual():
    cfg = auto_cfg()
    cfg.control.water_intrusion_lockout = True
    z = safe_zones(cfg, pocket_td=65)
    ds = {}
    for c in cfg.circulators:
        if not c.enabled:
            continue
        ds[c.id] = decide_circulator(c, cfg, z, [], CircState(manual=True), T0,
                                     {"on": True, "drying": True, "auto_on": True})
        assert not ds[c.id].target_on and ds[c.id].reason_code == "WATER_INTRUSION_LOCKOUT"
    assert overview(cfg, z, [], ds)["mode"] == "WATER_INTRUSION_ALERT"


def test_j_oscillating_spread_does_not_flap():
    cfg = auto_cfg()
    c = fan(cfg, "circ-1")
    st = CircState()
    switches = 0
    for i in range(120):  # two hours, one minute per step, spread alternating 3.6 / 2.4 F
        now = T0 + timedelta(minutes=i)
        z = safe_zones(cfg, main_td=58, pocket_td=61.6 if i % 2 == 0 else 60.4)
        d = decide_circulator(c, cfg, z, [], st, now)
        if d.target_on != st.is_on:
            switches += 1
            st.is_on, st.last_change = d.target_on, now
        st.last_reason = d.reason_code
    # alternating readings reset the activation timer only when no trigger at all; with hysteresis
    # (on at 3, keep at 2) the fan, once on, stays on
    assert switches <= 1


def test_overview_metrics():
    cfg = auto_cfg()
    z = safe_zones(cfg, main_td=58, pocket_td=62, damp_td=60)
    ov = overview(cfg, z, [probe(cfg, z["damp"], 68)], {}, outdoor=air_f(80, td_f=70))
    assert round(ov["zone_dew_point_spread_f"], 1) == 4.0
    assert ov["zone_dew_point_max_zone"] == "Remote pocket"
    assert round(ov["outdoor_minus_indoor_dew_point_f"], 1) == 8.0
    assert ov["concrete"]["state"] == "ok" and ov["surface_risk"] in ("low", "elevated")
    assert ov["mode"] == "IDLE" and circulation.MODES[ov["mode"]]


def test_controller_reports_mode_and_logs_transitions():
    import asyncio

    from ventctl.clock import ManualClock
    from ventctl.controller import Controller
    from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
    from ventctl.storage import Storage

    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    asyncio.run(ctrl.startup())
    world.set_surface_c(world.mz.zone_air("damp").t_c)  # warm slab: no surface risk
    asyncio.run(ctrl.tick())
    ov = ctrl.status()["circulation"]["overview"]
    assert ov["mode"] != "SURFACE_MOISTURE_RISK" and ov["mode"] in circulation.MODES and ov["evaluated_at"] and ov["next_evaluation"]
    world.set_surface_c(world.mz.zone_air("damp").dew_point_c - 2)
    clock.set(T0 + timedelta(minutes=1))
    asyncio.run(ctrl.tick())
    assert ctrl.circ_overview["mode"] == "SURFACE_MOISTURE_RISK"
    ev = [e for e in store.events(limit=200) if (e.get("reason_code") or "").startswith("CIRC_MODE:")]
    assert ev and ev[0]["details"]["mode"] == "SURFACE_MOISTURE_RISK"
