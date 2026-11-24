"""One exhaust fan's plug dropping off Wi-Fi must not stop the other fan."""

import pytest

from ventctl import controller as ctrl_mod
from ventctl.engine import Decision

from .test_fan_override import client, plugs, two_fans


@pytest.fixture
def run_unless_unverified(monkeypatch):
    """The mock basement trips other hard checks; keep only the switch-verification gate under test."""
    real = ctrl_mod.decide

    def fake(cond, state, cfg, g, now, mode=None, **kw):
        d = real(cond, state, cfg, g, now, mode=mode, **kw)
        if mode is None and d.reason_code != "SWITCH_UNVERIFIED":
            return Decision(g.id, "auto", True, "TEST_ON", "Fans ON: test")
        return d

    monkeypatch.setattr(ctrl_mod, "decide", fake)


def _tick(c):
    c.portal.call(c.app.state.vc["ctrl"].tick)
    return c.get("/api/status").json()


def test_other_fan_keeps_running_when_one_plug_is_unreachable(tmp_path, run_unless_unverified):
    with client(tmp_path) as c:
        two_fans(c)
        world = c.app.state.vc["world"]
        world.switch_faults["mock-plug-9"] = "unreachable"
        _tick(c)
        s = _tick(c)
        g = s["groups"][0]
        assert g["verified"] is True and g["decision"]["reason_code"] == "TEST_ON"
        assert plugs(c).get("mock-plug-1") is True and g["is_on"] is True
        alerts = {a["id"]: a for a in s["alerts"]}
        assert "1 of 2" in alerts["FANS_DEGRADED:main"]["message"]

        # the plug answers again: it rejoins right away instead of waiting out the 30-minute lockout
        world.switch_faults.pop("mock-plug-9")
        _tick(c)
        s = _tick(c)
        assert plugs(c).get("mock-plug-9") is True and plugs(c).get("mock-plug-1") is True
        assert "FANS_DEGRADED:main" not in {a["id"] for a in s["alerts"]}


def test_no_usable_plug_still_fails_safe(tmp_path, run_unless_unverified):
    with client(tmp_path) as c:
        two_fans(c)
        world = c.app.state.vc["world"]
        world.switch_faults["mock-plug-1"] = "unreachable"
        world.switch_faults["mock-plug-9"] = "unreachable"
        _tick(c)
        g = _tick(c)["groups"][0]
        assert g["verified"] is False and g["decision"]["reason_code"] == "SWITCH_UNVERIFIED"
        assert not plugs(c).get("mock-plug-1") and not plugs(c).get("mock-plug-9")
