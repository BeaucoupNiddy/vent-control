"""Owner's outdoor air temperature limits (control.intake_*)."""

from ventctl.config import ControlPolicy, Mode
from ventctl.engine import GroupState
from ventctl.config import ControlSettings
import pytest

from .helpers import T0, air_f, cond, config, minutes
from .test_engine import run

BASEMENT = air_f(75, 70)  # dew point ~64.5 F


def chk(d):
    return next(c for c in d.checks if c.id == "intake_temp")


def test_defaults_allow_normal_drying_air():
    d = run(cond(BASEMENT, air_f(80, td_f=55), surface_f=74))
    assert d.target_on and d.reason_code == "DRYING"
    assert chk(d).ok is True and "between 40 and 90" in chk(d).detail


def test_hot_dry_air_blocked_by_default():
    d = run(cond(BASEMENT, air_f(93, td_f=55), surface_f=74))
    assert not d.target_on and d.reason_code == "OUTDOOR_TOO_HOT"
    assert "90°F limit" in d.explanation and chk(d).ok is False
    assert "OUTDOOR_TEMP_LIMIT" in d.reasons


def test_cold_air_blocked_and_running_fans_stop_immediately():
    cfg = config(surface_temp_estimate_f=None)
    running = GroupState(is_on=True, last_change=T0 - minutes(2))  # inside the 15-minute minimum run
    d = run(cond(BASEMENT, air_f(38, td_f=25), surface_f=74), cfg=cfg, state=running)
    assert not d.target_on and d.reason_code == "OUTDOOR_TOO_COLD" and not d.held and not d.safety


def test_deadband_on_start_but_not_while_running():
    c = cond(BASEMENT, air_f(89.5, td_f=55), surface_f=74)
    assert run(c).reason_code == "OUTDOOR_TOO_HOT"  # stopped: needs 89°F or cooler to start
    on = run(c, state=GroupState(is_on=True, last_change=T0 - minutes(60)))
    assert on.target_on  # running: keeps going up to 90°F


def test_wetter_outdoor_reason_wins():
    d = run(cond(air_f(75, 60), air_f(95, td_f=75), surface_f=90))
    assert d.reason_code == "OUTDOOR_WETTER"


def test_blank_limits_disable():
    cfg = config(intake_min_outdoor_f=None, intake_max_outdoor_f=None)
    d = run(cond(BASEMENT, air_f(93, td_f=55), surface_f=74), cfg=cfg)
    assert d.target_on and chk(d).ok is None


def test_gap_limit_both_directions():
    cfg = config(intake_max_gap_f=10)
    d = run(cond(BASEMENT, air_f(62, td_f=40), surface_f=74), cfg=cfg)
    assert d.reason_code == "OUTDOOR_TEMP_GAP_TOO_LARGE" and "colder than the basement" in d.explanation
    d = run(cond(BASEMENT, air_f(86, td_f=55), surface_f=74), cfg=cfg)
    assert d.reason_code == "OUTDOOR_TEMP_GAP_TOO_LARGE" and "warmer" in d.explanation


def test_manual_ignores_limits_schedule_and_tamo_follow_them():
    c = cond(BASEMENT, air_f(93, td_f=55), surface_f=74)
    st = GroupState(manual_until=T0 + minutes(30))
    assert run(c, state=st, mode=Mode.MANUAL_ON).target_on
    assert run(c, mode=Mode.SCHEDULE, hour=22).reason_code == "OUTDOOR_TOO_HOT"
    assert run(c, cfg=config(policy=ControlPolicy.TAMO)).reason_code == "OUTDOOR_TOO_HOT"


def test_limits_must_be_apart():
    with pytest.raises(ValueError):
        ControlSettings(intake_min_outdoor_f=70, intake_max_outdoor_f=72)
