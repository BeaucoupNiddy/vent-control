from fastapi.testclient import TestClient

from ventctl import controller as ctrl_mod
from ventctl.app import create_app
from ventctl.engine import Decision


def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "t.db"), backfill_hours=0, start_loop=False))


def two_fans(c):
    cfg = c.get("/api/config").json()
    second = dict(cfg["fans"][0], id="v2d-2", name="V2D #2", switch_id="mock-plug-9")
    cfg["fans"].append(second)
    assert c.put("/api/config", json=cfg).status_code == 200


def plugs(c):
    return c.app.state.vc["world"].switch_on


def test_hold_one_fan_off_while_the_other_runs(tmp_path):
    with client(tmp_path) as c:
        two_fans(c)
        c.post("/api/fans/v2d-2/override", json={"state": "off"})
        # group manual run in maintenance mode (bypasses interlocks) turns on every fan that follows the group
        c.post("/api/mode", json={"mode": "manual_on", "minutes": 10, "maintenance": True})
        s = c.post("/api/fans/v2d/override", json={"state": "auto"}).json()  # also runs a tick
        assert plugs(c).get("mock-plug-1") is True and not plugs(c).get("mock-plug-9")
        fans = {f["id"]: f for f in s["groups"][0]["fans"]}
        assert fans["v2d-2"]["override"] == {"state": "off", "until": None} and fans["v2d"]["override"] is None
        assert s["groups"][0]["is_on"] is True
        # back to auto: it follows the group again
        c.post("/api/fans/v2d-2/override", json={"state": "auto"})
        assert plugs(c).get("mock-plug-9") is True


def test_manual_on_runs_one_fan_and_respects_safety(tmp_path, monkeypatch):
    real = ctrl_mod.decide
    blocked = {"v": False}

    def fake(cond, state, cfg, g, now, mode=None, **kw):
        d = real(cond, state, cfg, g, now, mode=mode, **kw)
        if mode is not None and mode.value == "manual_on":
            return Decision(g.id, "manual_on", not blocked["v"], "CONDENSATION_RISK" if blocked["v"] else "MANUAL_ON",
                            "x", safety=blocked["v"])
        return d

    monkeypatch.setattr(ctrl_mod, "decide", fake)
    with client(tmp_path) as c:
        two_fans(c)
        c.post("/api/mode", json={"mode": "off"})
        s = c.post("/api/fans/v2d/override", json={"state": "on", "minutes": 30}).json()
        assert plugs(c).get("mock-plug-1") is True and not plugs(c).get("mock-plug-9")
        assert s["groups"][0]["fans"][0]["override"]["state"] == "on" and s["groups"][0]["is_on"] is True
        blocked["v"] = True  # a hard safety check fails: the manual run stops
        import asyncio
        asyncio.run(c.app.state.vc["ctrl"].tick())
        assert plugs(c).get("mock-plug-1") is False
        # overrides survive a restart
    with client(tmp_path) as c:
        assert c.app.state.vc["ctrl"].fan_overrides["v2d"]["state"] == "on"


def test_manual_on_expires(tmp_path):
    from datetime import timedelta
    with client(tmp_path) as c:
        ctrl = c.app.state.vc["ctrl"]
        ctrl.set_fan_override("v2d", "on", 5)
        ctrl._expire_fan_overrides(ctrl.clock.now() + timedelta(minutes=6))
        assert "v2d" not in ctrl.fan_overrides
        assert c.post("/api/fans/nope/override", json={"state": "on"}).status_code == 404
        assert c.post("/api/fans/v2d/override", json={"state": "maybe"}).status_code == 400


def test_circulation_runs_with_exhaust_in_automatic(tmp_path):
    from datetime import timedelta

    from ventctl import circulation
    from ventctl.config import example_config
    cfg = example_config()
    c = cfg.circulators[0]
    st = circulation.CircState(verified=True)
    now = __import__("datetime").datetime(2026, 7, 1, 12, tzinfo=__import__("datetime").timezone.utc)
    d = circulation.decide_circulator(c, cfg, {}, [], st, now, {"on": True, "drying": True, "auto_on": True}, None, 12)
    assert d.target_on and d.reason_code == "RUN_WITH_EXHAUST"
    st.last_reason = d.reason_code
    d2 = circulation.decide_circulator(c, cfg, {}, [], st, now + timedelta(minutes=5), {"on": False}, None, 12)
    assert d2.target_on and d2.reason_code == "POST_EXHAUST_RUN"  # short run-on after the exhaust stops
    st.last_reason = d2.reason_code
    d3 = circulation.decide_circulator(c, cfg, {}, [], st, now + timedelta(minutes=60), {"on": False}, None, 12)
    assert d3.reason_code != "POST_EXHAUST_RUN"
    # a manual exhaust run (auto_on False) or the setting turned off does not start it
    cfg.circulation.run_with_exhaust = False
    st2 = circulation.CircState(verified=True)
    d4 = circulation.decide_circulator(c, cfg, {}, [], st2, now, {"on": True, "drying": True, "auto_on": True}, None, 12)
    assert d4.reason_code != "RUN_WITH_EXHAUST"
