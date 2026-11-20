"""Circulation fan plugs (2026-11-19): a plug that switched itself off (its hardware auto-off timer,
its button or the Tapo app) must not leave the fan stuck as "unverified", and circulation plugs must
have their auto-off timer re-armed while the fan should keep running."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from ventctl.clock import ManualClock
from ventctl.config import example_config
from ventctl.controller import Controller
from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
from ventctl.storage import Storage

from .helpers import T0


class RearmSwitches(MockSwitches):
    def __init__(self, world, clock):
        super().__init__(world, clock)
        self.rearmed: list[str] = []

    async def rearm(self, switch_id, now=None):
        self.rearmed.append(switch_id)


def setup():
    cfg = example_config()
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    sw = RearmSwitches(world, clock)
    ctrl = Controller(cfg, store, MockSensors(world, clock), sw, clock)
    asyncio.run(ctrl.startup())
    asyncio.run(ctrl.tick())
    return cfg, clock, world, store, sw, ctrl


def test_manual_on_recovers_after_plug_switched_itself_off():
    cfg, clock, world, store, sw, ctrl = setup()
    c = cfg.circulators[0]
    ctrl.set_circulator_manual(c.id, "on", 60)
    clock.set(T0 + timedelta(minutes=1))
    asyncio.run(ctrl.tick())
    assert world.switch_on[c.switch_id] is True
    assert c.switch_id in sw.rearmed  # circulation plugs get their auto-off timer re-armed too

    world.switch_on[c.switch_id] = False  # the plug's own 30-minute timer fired
    clock.set(T0 + timedelta(minutes=31))
    asyncio.run(ctrl.tick())
    clock.set(T0 + timedelta(minutes=32))
    asyncio.run(ctrl.tick())
    # still inside the manual hour: the fan is switched back on instead of sitting as "unverified"
    assert world.switch_on[c.switch_id] is True
    st = ctrl.circ_states[c.id]
    assert st.verified and st.is_on
    assert any(e.get("reason_code") == "CIRC_PLUG_STATE_ADOPTED" for e in store.events(limit=300))


def test_plug_that_ignores_commands_is_still_reported():
    cfg, clock, world, store, sw, ctrl = setup()
    c = cfg.circulators[0]
    world.switch_faults[c.switch_id] = "stuck"
    ctrl.set_circulator_manual(c.id, "on", 60)
    clock.set(T0 + timedelta(minutes=1))
    asyncio.run(ctrl.tick())
    assert world.switch_on.get(c.switch_id, False) is False
    assert any(e.get("reason_code") == "COMMAND_FAILURE" for e in store.events(limit=300))
    assert not ctrl.circ_states[c.id].verified
