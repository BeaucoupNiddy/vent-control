"""Controller-level fail-safe tests (spec 18: #7, #8, #9, #18)."""

import asyncio
from datetime import timedelta

from ventctl.clock import ManualClock
from ventctl.config import Mode, example_config
from ventctl.controller import Controller
from ventctl.hardware.mock import MockSensors, MockSwitches, MockWorld
from ventctl.storage import Storage

from .helpers import T0, exhaust_fans


def make(mode=Mode.AUTO):
    cfg = example_config()
    cfg.fans = exhaust_fans(2)  # two fans on two plugs, to test per-plug fail-safes
    cfg.mode = mode
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    ctrl = Controller(cfg, store, MockSensors(world, clock), MockSwitches(world, clock), clock)
    return ctrl, world, clock, store


def run_minutes(ctrl, clock, n):
    async def go():
        for _ in range(n):
            clock.set(clock.now() + timedelta(minutes=1))
            await ctrl.tick()
    asyncio.run(go())


def force_on(ctrl, world, clock):
    """Put the world in strongly favorable drying conditions and run until fans are on."""
    world.model.w = world.model.w * 1.15  # very humid basement
    world.set_surface_c(30)  # warm surface: condensation margin passes
    world.model.profile = world.profile.__class__("dry", "dry", "", [70.0] * 24, [50.0] * 24)
    run_minutes(ctrl, clock, 2)


# 9
def test_startup_reconciles_and_turns_running_fans_off():
    ctrl, world, clock, store = make()
    world.switch_on["mock-plug-1"] = True  # e.g. left on before a power outage
    asyncio.run(ctrl.startup())
    assert world.switch_on["mock-plug-1"] is False
    ev = store.events(kind="startup")
    assert ev and ev[0]["reason_code"] == "STARTUP_RECONCILE"
    assert ev[0]["details"]["switches_found"]["mock-plug-1"] is True
    assert ctrl.groups["main"].last_change == T0  # min off-time applies after the safe shutdown


def test_auto_turns_fans_on_in_favorable_conditions():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    force_on(ctrl, world, clock)
    d = ctrl.decisions["main"]
    assert d.reason_code == "DRYING", d.explanation
    assert all(world.switch_on.get(s) for s in ("mock-plug-1", "mock-plug-2"))
    assert any(e["kind"] == "command" for e in store.events())


# 7 / 18
def test_outdoor_sensor_stops_updating_triggers_fail_safe():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    force_on(ctrl, world, clock)
    assert ctrl.groups["main"].is_on
    world.sensor_faults.add("mock-out")  # outdoor sensor freezes (keeps returning its old timestamp)
    run_minutes(ctrl, clock, 4)
    assert ctrl.groups["main"].is_on  # still fresh within 5-minute stale threshold
    run_minutes(ctrl, clock, 3)
    d = ctrl.decisions["main"]
    assert d.reason_code == "SENSOR_STALE" and d.safety
    assert "has not updated" in d.explanation
    assert not world.switch_on["mock-plug-1"] and not world.switch_on["mock-plug-2"]
    assert any(a.startswith("SENSOR_OFFLINE:mock-out") for a in ctrl.alerts)


def test_gateway_outage_does_not_reuse_old_readings_as_current():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    force_on(ctrl, world, clock)
    world.gateway_down = True
    run_minutes(ctrl, clock, 7)
    assert ctrl.decisions["main"].reason_code in ("SENSOR_STALE", "SENSOR_MISSING")
    assert not ctrl.groups["main"].is_on
    assert "GATEWAY_ERROR" in ctrl.alerts
    world.gateway_down = False
    run_minutes(ctrl, clock, 1)
    assert "GATEWAY_ERROR" not in ctrl.alerts


# 8
def test_fan_not_responding_records_command_failure():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    world.switch_faults["mock-plug-2"] = "stuck"  # relay ignores commands
    force_on(ctrl, world, clock)
    faults = [e for e in store.events(kind="fault") if e["reason_code"] == "COMMAND_FAILURE"]
    assert faults and faults[0]["details"]["switch"] == "mock-plug-2"
    assert faults[0]["details"]["confirmed_state"] is False
    assert "COMMAND_FAILURE:mock-plug-2" in ctrl.alerts
    # group cannot be verified -> engine forces OFF and the controller retries OFF
    assert not ctrl.groups["main"].verified or ctrl.decisions["main"].reason_code == "SWITCH_UNVERIFIED"


def test_unreachable_switch_keeps_last_known_separate_from_verified():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    force_on(ctrl, world, clock)
    world.switch_faults["mock-plug-1"] = "unreachable"
    run_minutes(ctrl, clock, 1)
    info = ctrl.switch_info["mock-plug-1"]
    assert info.reachable is False and info.confirmed is True  # last known ON, not verified
    assert ctrl.decisions["main"].reason_code == "SWITCH_UNVERIFIED"
    assert "SWITCH_OFFLINE:mock-plug-1" in ctrl.alerts
    assert world.switch_on["mock-plug-2"] is False  # reachable fan in the group was turned off


def test_manual_override_expires_and_is_logged():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    ctrl.set_mode(Mode.MANUAL_ON, minutes=5)
    world.set_surface_c(35)  # keep condensation interlock clear
    run_minutes(ctrl, clock, 1)
    assert ctrl.decisions["main"].reason_code == "MANUAL_ON"
    run_minutes(ctrl, clock, 6)
    assert ctrl.config.mode == Mode.AUTO
    kinds = [e["reason_code"] for e in store.events(kind="override")]
    assert "MANUAL_ON" in kinds and "MANUAL_EXPIRED" in kinds


def test_simulation_mode_issues_no_commands():
    ctrl, world, clock, store = make(Mode.SIMULATION)
    asyncio.run(ctrl.startup())
    force_on(ctrl, world, clock)
    assert ctrl.decisions["main"].target_on
    assert not any(world.switch_on.values())
    assert not store.events(kind="command")


def test_history_written_with_source_label():
    ctrl, world, clock, store = make()
    asyncio.run(ctrl.startup())
    run_minutes(ctrl, clock, 10)
    rows = store.history(T0, T0 + timedelta(hours=1))
    assert rows and all(r["source"] == "mock" for r in rows)


def test_storage_migrations_idempotent(tmp_path):
    p = tmp_path / "x.db"
    s = Storage(p)
    s.save_config(example_config())
    s2 = Storage(p)
    assert s2.migrate() == 4
    assert s2.load_config().fans[0].id == "v2d"
