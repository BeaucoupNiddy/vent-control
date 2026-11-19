"""Timed circulation cycling (2026-11-19): a repeating on/off timer for the circulation fans, with quiet
hours and a per-fan choice, that never overrides safety lockouts or manual control."""

from __future__ import annotations

from datetime import timedelta

from ventctl.circulation import CircState, decide_circulator, overview, schedule_status
from ventctl.config import AppConfig

from .helpers import T0
from .test_circulation_spec import auto_cfg, fan, safe_zones, zdp

# T0 is 12:00 UTC; with local_hour 6.0 that is 6:00 AM in New Orleans (UTC-6)


def timed_cfg(on=60, off=60) -> AppConfig:
    cfg = auto_cfg()
    cs = cfg.circulation
    cs.periodic_mixing, cs.mixing_on_min, cs.mixing_off_min = True, on, off
    return cfg


def at(hours: float):
    """(now, local_hour) `hours` after 6:00 AM local."""
    return T0 + timedelta(hours=hours), (6.0 + hours) % 24


def decide(cfg, cid="circ-3", hours=0.0, state=None, zones=None, surfaces=None):
    now, lh = at(hours)
    return decide_circulator(fan(cfg, cid), cfg, zones or safe_zones(cfg), surfaces or [], state or CircState(),
                             now, {}, None, lh)


def test_one_hour_on_one_hour_off_runs_on_even_local_hours():
    cfg = timed_cfg()
    d = decide(cfg, hours=0)
    assert d.target_on and d.reason_code == "SCHEDULED_MIXING" and d.commanding
    assert "until 7:00 AM" in d.explanation
    d = decide(cfg, hours=1.25)
    assert not d.target_on
    assert "Next timed run 8:00 AM" in d.explanation
    assert decide(cfg, hours=2.5).target_on


def test_off_when_timer_disabled():
    cfg = timed_cfg()
    cfg.circulation.periodic_mixing = False
    d = decide(cfg, hours=0)
    assert not d.target_on and "timed" not in d.explanation.lower()


def test_cycle_is_even_across_midnight_for_any_on_off_pair():
    cfg = timed_cfg(on=50, off=50)  # 100-minute period does not divide a day
    runs, cur, prev = [], 0, False
    for m in range(0, 3 * 1440):
        now = T0 + timedelta(minutes=m)
        on = schedule_status(cfg.circulation, now, (6.0 + m / 60) % 24)["running"]
        if on:
            cur += 1
        elif prev:
            runs.append(cur)
            cur = 0
        prev = on
    assert runs and all(r == 50 for r in runs[1:])


def test_quiet_hours_pause_and_report_next_run():
    cfg = timed_cfg()
    cfg.circulation.mixing_quiet_start_hour, cfg.circulation.mixing_quiet_end_hour = 22, 7
    d = decide(cfg, hours=16)  # 10 PM: an ON block, but quiet hours
    assert not d.target_on and "SCHEDULED_QUIET_HOURS" in d.reasons
    assert "next run 8:00 AM" in d.explanation
    assert decide(cfg, hours=26).target_on  # 8 AM next day


def test_run_resumes_partway_through_a_block_when_quiet_hours_end():
    cfg = timed_cfg()
    cfg.circulation.mixing_quiet_start_hour, cfg.circulation.mixing_quiet_end_hour = 22, 6.5
    now, lh = at(-2 + 10 / 60)  # 4:10 AM
    sch = schedule_status(cfg.circulation, now, lh)
    assert not sch["running"] and sch["quiet"]
    assert sch["next_start"] == T0 + timedelta(minutes=30)  # 6:30 AM
    assert not decide(cfg, hours=0.25).target_on   # 6:15, still quiet
    assert decide(cfg, hours=0.6).target_on        # 6:36


def test_run_ends_when_quiet_hours_begin():
    cfg = timed_cfg()
    cfg.circulation.mixing_quiet_start_hour, cfg.circulation.mixing_quiet_end_hour = 6.5, 7
    sch = schedule_status(cfg.circulation, *at(0))
    assert sch["running"] and sch["ends"] == T0 + timedelta(minutes=30)


def test_safety_and_manual_override_beat_the_timer():
    cfg = timed_cfg()
    cfg.control.water_intrusion_lockout = True
    d = decide(cfg)
    assert not d.target_on and d.reason_code == "WATER_INTRUSION_LOCKOUT"
    cfg.control.water_intrusion_lockout = False
    st = CircState(manual=False, manual_until=T0 + timedelta(hours=1))
    d = decide(cfg, state=st)
    assert not d.target_on and d.reason_code == "MANUAL_OFF"
    fan(cfg, "circ-3").enabled = False
    assert decide(cfg).reason_code == "DISABLED"


def test_circulation_off_or_uncommissioned_never_switches():
    cfg = timed_cfg()
    cfg.circulation.mode = "off"
    assert not decide(cfg).target_on
    cfg = timed_cfg()
    cfg.circulation.mode = "shadow"
    d = decide(cfg)
    assert d.target_on and not d.commanding  # logged as what would happen, not switched
    cfg = timed_cfg()
    fan(cfg, "circ-3").commissioned = False
    assert not decide(cfg).commanding


def test_surface_drying_fan_without_probe_stays_off():
    cfg = timed_cfg()
    fan(cfg, "circ-3").role = "surface_drying"
    d = decide(cfg)
    assert not d.target_on and d.reason_code == "SURFACE_DATA_MISSING"


def test_only_chosen_fans_cycle():
    cfg = timed_cfg()
    cfg.circulation.mixing_fans = ["circ-1"]
    assert not decide(cfg, "circ-3").target_on
    assert decide(cfg, "circ-1").target_on


def test_skip_when_dry_is_optional():
    cfg = timed_cfg()
    dry = {z: zdp(cfg, z, 72, 50) for z in ("main", "pocket", "damp")}
    assert decide(cfg, zones=dry).target_on  # default: the timer runs regardless
    cfg.circulation.mixing_skip_when_dry = True
    d = decide(cfg, zones=dry)
    assert not d.target_on and "SCHEDULED_RUN_SKIPPED_DRY" in d.reasons


def test_scheduled_run_stops_on_time_despite_minimum_run():
    cfg = timed_cfg(on=10, off=50)
    st = CircState(is_on=True, last_change=T0, last_reason="SCHEDULED_MIXING")
    d = decide(cfg, hours=11 / 60, state=st)  # 1 min after the 10-min run, min ON time is 15
    assert not d.target_on and not d.held


def test_overview_reports_schedule():
    cfg = timed_cfg()
    now, lh = at(1.5)
    ov = overview(cfg, safe_zones(cfg), [], {}, {}, None, None, now, lh)
    s = ov["schedule"]
    assert s["enabled"] and not s["running"] and s["next_start_label"] == "8:00 AM"


def test_quiet_hour_settings_validate():
    cfg = timed_cfg()
    d = cfg.model_dump()
    d["circulation"]["mixing_quiet_start_hour"] = 22
    d["circulation"]["mixing_quiet_end_hour"] = 7
    AppConfig.model_validate(d)
