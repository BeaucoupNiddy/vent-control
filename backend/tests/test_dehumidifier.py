import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from ventctl import circulation
from ventctl import dehumidifier as dh_mod
from ventctl.app import create_app
from ventctl.config import example_config
from ventctl.engine import Decision

from .helpers import air_f, cond
from .test_v3 import zs

NOW = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def cfg_with_dehum(commissioned=True):
    cfg = example_config()
    dh = cfg.dehumidifier
    dh.enabled, dh.switch_id, dh.zone, dh.commissioned = True, "mock-plug-7", "damp", commissioned
    dh.setpoint_rh_pct, dh.start_margin_pct = 50, 3
    return cfg


def test_auto_starts_above_margin_and_stops_at_target():
    cfg = cfg_with_dehum()
    st = dh_mod.DehumState(verified=True)
    z = {"damp": zs(cfg, "damp", 70, 52)}
    d = dh_mod.decide(cfg, st, None, z, None, NOW)
    assert not d.target_on and d.reason_code == "BELOW_START" and d.commanding
    z = {"damp": zs(cfg, "damp", 70, 54)}
    d = dh_mod.decide(cfg, st, None, z, None, NOW)
    assert d.target_on and d.reason_code == "ABOVE_TARGET" and d.control_source == cfg.zone_name("damp")
    st.is_on, st.last_change = True, NOW
    # keeps running between target and start point
    z = {"damp": zs(cfg, "damp", 70, 51)}
    assert dh_mod.decide(cfg, st, None, z, None, NOW + timedelta(minutes=40)).target_on
    # reaches target, but the minimum run holds it on
    z = {"damp": zs(cfg, "damp", 70, 49.5)}
    d = dh_mod.decide(cfg, st, None, z, None, NOW + timedelta(minutes=10))
    assert d.target_on and d.held
    d = dh_mod.decide(cfg, st, None, z, None, NOW + timedelta(minutes=31))
    assert not d.target_on and d.reason_code == "TARGET_REACHED"


def test_falls_back_to_basement_average_and_overrides():
    cfg = cfg_with_dehum()
    st = dh_mod.DehumState(verified=True)
    d = dh_mod.decide(cfg, st, None, {}, {"rh_pct": 60}, NOW)
    assert d.target_on and d.control_source == "basement average"
    cfg.dehumidifier.zone_only = True
    assert dh_mod.decide(cfg, st, None, {}, {"rh_pct": 60}, NOW).reason_code == "NO_READING"
    assert dh_mod.decide(cfg, st, {"state": "off"}, {}, {"rh_pct": 60}, NOW).reason_code == "HELD_OFF"
    d = dh_mod.decide(cfg, st, {"state": "on", "until": None}, {}, None, NOW)
    assert d.target_on and d.commanding
    cfg.control.water_intrusion_lockout = True
    d = dh_mod.decide(cfg, st, {"state": "on", "until": None}, {}, None, NOW)
    assert not d.target_on and d.safety


def test_not_commissioned_auto_never_commands():
    cfg = cfg_with_dehum(commissioned=False)
    d = dh_mod.decide(cfg, dh_mod.DehumState(verified=True), None, {"damp": zs(cfg, "damp", 70, 65)}, None, NOW)
    assert d.target_on and not d.commanding and "Not commissioned" in d.explanation
    # manual control works before commissioning
    d = dh_mod.decide(cfg, dh_mod.DehumState(verified=True), {"state": "on", "until": None}, {}, None, NOW)
    assert d.commanding and d.target_on


def test_circulation_runs_while_dehumidifier_runs():
    cfg = cfg_with_dehum()
    c = cfg.circulators[0]
    st = circulation.CircState(verified=True)
    z = {"main": zs(cfg, "main", 72, 45), "pocket": zs(cfg, "pocket", 72, 45)}
    d = circulation.decide_circulator(c, cfg, z, [], st, NOW, {"on": False}, True, 12)
    assert d.target_on and d.reason_code == "RUN_WITH_DEHUMIDIFIER"
    st.last_reason = d.reason_code
    d2 = circulation.decide_circulator(c, cfg, z, [], st, NOW + timedelta(minutes=5), {"on": False}, False, 12)
    assert d2.target_on and d2.reason_code == "POST_DEHUMIDIFIER_RUN"
    cfg.dehumidifier.run_circulation = False
    d3 = circulation.decide_circulator(c, cfg, z, [], circulation.CircState(verified=True), NOW, {"on": False},
                                       True, 12)
    assert d3.reason_code != "RUN_WITH_DEHUMIDIFIER"


def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=0, start_loop=False))


def setup_dehum(c, commissioned=True):
    cfg = c.get("/api/config").json()
    cfg["dehumidifier"].update(enabled=True, switch_id="mock-plug-7", zone="damp", commissioned=commissioned,
                               setpoint_rh_pct=30, start_margin_pct=1)  # mock basement is well above 31%
    r = c.put("/api/config", json=cfg)
    assert r.status_code == 200, r.text


def plugs(c):
    return c.app.state.vc["world"].switch_on


def test_controller_switches_dehumidifier_plug(tmp_path):
    with client(tmp_path) as c:
        setup_dehum(c)
        ctrl = c.app.state.vc["ctrl"]
        asyncio.run(ctrl.tick())
        assert plugs(c).get("mock-plug-7") is True
        s = c.get("/api/status").json()["dehumidifier"]
        assert s["controlled"] and s["decision"]["target_on"] and s["running"] is True
        # held off by hand; the minimum run time does not block a manual change
        s = c.post("/api/dehumidifier/override", json={"state": "off"}).json()
        assert plugs(c).get("mock-plug-7") is False and s["dehumidifier"]["override"]["state"] == "off"
        assert c.post("/api/dehumidifier/override", json={"state": "maybe"}).status_code == 400


def test_uncommissioned_manual_on_then_auto_switches_off(tmp_path):
    with client(tmp_path) as c:
        setup_dehum(c, commissioned=False)
        ctrl = c.app.state.vc["ctrl"]
        asyncio.run(ctrl.tick())
        assert not plugs(c).get("mock-plug-7")  # auto would run it, but not commissioned
        c.post("/api/dehumidifier/override", json={"state": "on", "minutes": 30})
        assert plugs(c).get("mock-plug-7") is True
        c.post("/api/dehumidifier/override", json={"state": "auto"})
        assert plugs(c).get("mock-plug-7") is False


def test_dehumidifier_plug_cannot_share_a_fan_plug(tmp_path):
    with client(tmp_path) as c:
        cfg = c.get("/api/config").json()
        cfg["dehumidifier"].update(enabled=True, switch_id=cfg["fans"][0]["switch_id"])
        assert c.put("/api/config", json=cfg).status_code in (400, 422)
        assert c.post("/api/dehumidifier/override", json={"state": "on"}).status_code == 400


def test_exhaust_held_off_when_outdoor_wetter_and_dehumidifier_runs(tmp_path):
    with client(tmp_path) as c:
        setup_dehum(c)
        ctrl = c.app.state.vc["ctrl"]
        asyncio.run(ctrl.tick())
        assert ctrl.dehumidifier_running()
        wet = cond(air_f(72, 60), air_f(80, td_f=75))
        d = Decision("main", "auto", True, "X", "x")
        assert ctrl._hold_exhaust_for_dehumidifier(wet, d).reason_code == "DEHUMIDIFIER_OUTDOOR_WETTER"
        dry = cond(air_f(72, 60), air_f(70, td_f=45))
        d = Decision("main", "auto", True, "X", "x")
        assert ctrl._hold_exhaust_for_dehumidifier(dry, d).target_on


def test_tapo_dehumidifier_plug_keeps_auto_off_disabled():
    """A re-arm cycles power, which would short-cycle the compressor: that plug gets no auto-off timer."""
    from kasa import Module

    from ventctl.clock import ManualClock
    from ventctl.config import TapoDevice
    from ventctl.hardware.tapo import TapoSwitches

    from .test_hardware import FakeTapo
    clock = ManualClock(NOW)
    fake = FakeTapo(clock)
    fake.modules[Module.AutoOff].enabled = True  # e.g. left on from an earlier use as a fan plug

    async def connect(host):
        return fake
    sw = TapoSwitches([TapoDevice(switch_id="dh", host="10.0.0.9")], clock, "", "", auto_off_minutes=30,
                      connector=connect, no_auto_off={"dh"})
    asyncio.run(sw.set("dh", True))
    assert not fake.modules[Module.AutoOff].enabled
    clock.set(NOW + timedelta(minutes=29))
    asyncio.run(sw.rearm("dh", clock.now()))
    assert fake.calls == ["on"] and sw.supports_hardware_auto_off()
