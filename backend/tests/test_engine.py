"""Decision-engine tests. Numbers refer to the spec's section 18 test list."""

from datetime import timedelta

import pytest

from ventctl.config import EnergyProfile, FanGroup, Mode, SensorMapping, SensorRole
from ventctl.engine import GroupState, decide
from ventctl.sensors import SensorReading, Snapshot, conditions_for_group, validate

from .helpers import T0, air_f, cond, config, minutes


def run(c, cfg=None, state=None, now=T0, mode=None, hour=None):
    cfg = cfg or config()
    return decide(c, state or GroupState(), cfg, cfg.groups[0], now, mode=mode, local_hour=hour)


# 1
def test_outside_rh_higher_but_dewpoint_lower_is_eligible():
    indoor = air_f(85, 65)   # dew point ~72 F
    outdoor = air_f(72, 85)  # higher RH, dew point ~67 F
    assert outdoor.rh > indoor.rh and outdoor.dew_point_c < indoor.dew_point_c
    d = run(cond(indoor, outdoor))
    assert d.target_on and d.reason_code == "DRYING"


# 2
def test_outside_rh_lower_but_dewpoint_higher_stays_off():
    indoor = air_f(70, 80)   # dew point ~63.6 F
    outdoor = air_f(90, 50)  # lower RH, dew point ~69 F
    assert outdoor.rh < indoor.rh and outdoor.dew_point_c > indoor.dew_point_c
    d = run(cond(indoor, outdoor))
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"
    assert "add moisture" in d.explanation


# 3
def test_79f_95pct_vs_74f_dewpoint_has_positive_drying():
    d = run(cond(air_f(79, 95), air_f(84, td_f=74), surface_f=78))
    assert d.effect.net_removal_g_h > 0
    assert d.metrics["dewpoint_advantage_f"] == pytest.approx(3.4, abs=0.1)
    assert d.target_on and d.reason_code == "DRYING"


# 4
def test_79f_70pct_vs_74f_dewpoint_is_moisture_addition():
    d = run(cond(air_f(79, 70), air_f(84, td_f=74), surface_f=84))
    assert d.effect.net_removal_g_h < 0
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"


# 5 (revised in the physics review): outdoor air drier than the basement lowers the dew point next to
# every surface, so a cold wall is a reason to ventilate, not to block it
def test_drier_outdoor_air_runs_even_when_concrete_is_near_dew_point():
    d = run(cond(air_f(79, 90), air_f(80, td_f=70), surface_f=68))
    assert d.target_on and d.reason_code == "DRYING"
    assert next(ch for ch in d.checks if ch.id == "condensation").ok is True


def test_wetter_outdoor_air_near_cold_concrete_is_a_condensation_risk():
    # the risk is outdoor air WETTER than the basement reaching a cold surface (e.g. a manual run in summer)
    st = GroupState(manual_until=T0 + minutes(30))
    d = run(cond(air_f(79, 60), air_f(85, td_f=76), surface_f=74), mode=Mode.MANUAL_ON, state=st)
    assert not d.target_on and d.reason_code == "CONDENSATION_RISK" and d.safety
    assert "wetter than the basement" in d.explanation


def test_cold_air_chilling_a_surface_toward_local_dew_point_blocks():
    from ventctl.units import f_to_c
    ind = air_f(62, td_f=57)
    c = cond(ind, air_f(40, td_f=36), surface_f=59.8)
    c.surfaces = [{"name": "pocket wall", "t_c": f_to_c(59.8), "zone": "pocket", "local": ind, "exposure": 0.0}]
    d = run(c, state=GroupState(is_on=True))
    assert not d.target_on and d.reason_code == "CONDENSATION_RISK" and d.safety
    assert "pocket wall" in d.explanation and "fans off" in d.explanation


def test_no_surface_sensor_runs_with_warm_dry_air():
    d = run(cond(air_f(79, 90), air_f(80, td_f=60), surface_f=None))
    assert d.target_on and d.reason_code == "DRYING"


def test_no_surface_sensor_blocks_air_cold_enough_to_chill_surfaces():
    d = run(cond(air_f(70, 70), air_f(50, td_f=35), surface_f=None))
    assert not d.target_on and d.reason_code == "CONDENSATION_UNVERIFIED" and d.safety
    assert "no surface sensor" in d.explanation


def test_no_surface_sensor_and_wetter_outdoor_air_reports_wetter():
    d = run(cond(air_f(70, 50), air_f(80, td_f=70), surface_f=None))
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"
    # a scheduled run still fails safe when it cannot be verified
    assert run(cond(air_f(70, 50), air_f(80, td_f=70), surface_f=None), mode=Mode.SCHEDULE, hour=22).safety


def test_estimated_surface_is_labeled():
    d = run(cond(air_f(79, 90), air_f(80, td_f=60), surface_f=70, surface_source="estimated"))
    assert d.target_on and "estimated" in d.explanation


# 6
def test_advantage_fluctuating_around_threshold_does_not_cycle():
    cfg = config()
    st = GroupState()
    indoor = air_f(79, 85)
    td_in = 74.0  # approx dew point of indoor
    from ventctl.units import c_to_f
    td_in = c_to_f(indoor.dew_point_c)
    starts = stops = 0
    t = T0
    # dew-point advantage alternates 2.6 / 3.4 F, then 0.8 / 1.2 F, every minute
    seq = [2.6, 3.4] * 30 + [1.2, 0.8] * 30 + [2.6, 3.4] * 30
    for adv in seq:
        d = decide(cond(indoor, air_f(82, td_f=td_in - adv)), st, cfg, cfg.groups[0], t)
        if d.target_on != st.is_on:
            if d.target_on:
                starts += 1
            else:
                stops += 1
            st.is_on = d.target_on
            st.last_change = t
        t += minutes(1)
    # one start when 3.4 first appears, one stop once it drops below 1 F (after min runtime),
    # one restart after min off-time when 3.4 returns. Never a start/stop per oscillation.
    assert starts <= 2 and stops <= 1


def test_minimum_runtime_holds_normal_stop_but_not_safety_stop():
    cfg = config(min_on_minutes=15)
    st = GroupState(is_on=True, last_change=T0)
    indoor = air_f(79, 85)
    from ventctl.units import c_to_f
    td_in = c_to_f(indoor.dew_point_c)
    # small advantage -> normal stop -> held by min runtime
    d = run(cond(indoor, air_f(82, td_f=td_in - 0.5)), cfg, st, now=T0 + minutes(5))
    assert d.target_on and d.held and d.reason_code.startswith("MIN_ON_HOLD")
    assert d.next_eligible_switch == T0 + minutes(15)
    # condensation (wetter outdoor air reaching a cold surface) -> immediate stop even inside min runtime
    d2 = run(cond(indoor, air_f(82, td_f=td_in + 1), surface_f=td_in + 2), cfg, st, now=T0 + minutes(5))
    assert not d2.target_on and d2.safety


def test_minimum_off_time_delays_restart():
    cfg = config(min_off_minutes=10)
    st = GroupState(is_on=False, last_change=T0)
    d = run(cond(air_f(79, 90), air_f(80, td_f=60)), cfg, st, now=T0 + minutes(3))
    assert not d.target_on and d.reason_code.startswith("MIN_OFF_HOLD")
    d = run(cond(air_f(79, 90), air_f(80, td_f=60)), cfg, st, now=T0 + minutes(10))
    assert d.target_on


# 7 / 18 (engine level; controller-level tests in test_controller.py)
def test_missing_outdoor_reading_cannot_produce_on_decision():
    d = run(cond(air_f(79, 95), None, outdoor_problem="Outdoor has not updated for 7 minutes"))
    assert not d.target_on and d.safety and d.reason_code == "SENSOR_STALE"
    assert "7 minutes" in d.explanation


def test_switch_unverified_forces_off():
    d = run(cond(air_f(79, 95), air_f(80, td_f=60)), state=GroupState(is_on=True, verified=False, last_change=T0),
            now=T0 + minutes(1))
    assert not d.target_on and d.reason_code == "SWITCH_UNVERIFIED" and d.safety


# 10
def test_target_reached_turns_fans_off_after_min_runtime():
    cfg = config(target_rh_pct=55)
    st = GroupState(is_on=True, last_change=T0)
    c = cond(air_f(79, 54.5), air_f(75, td_f=56))  # ~5°F drier outside: an ordinary window
    d = run(c, cfg, st, now=T0 + minutes(20))
    assert not d.target_on and d.reason_code == "TARGET_REACHED"
    assert "reached the configured target" in d.explanation
    # and will not start again just above target (deadband)
    d2 = run(cond(air_f(79, 56), air_f(75, td_f=50)), cfg, GroupState(is_on=False))
    assert not d2.target_on and d2.reason_code == "BELOW_TARGET"


def test_strong_drying_window_keeps_running_below_target():
    # outdoor dew point ~11°F lower: keep drying the wood and concrete down to 50% instead of stopping at 55%
    cfg = config(target_rh_pct=55)
    st = GroupState(is_on=True, last_change=T0)
    d = run(cond(air_f(79, 54.5), air_f(75, td_f=50)), cfg, st, now=T0 + minutes(20))
    assert d.target_on and d.reason_code == "DRYING"
    assert "strong drying window" in next(ch for ch in d.checks if ch.id == "rh").detail
    d = run(cond(air_f(79, 49.5), air_f(75, td_f=48)), cfg, st, now=T0 + minutes(20))
    assert not d.target_on and d.reason_code == "TARGET_REACHED" and "50%" in d.explanation
    # it never starts below the normal start point
    assert run(cond(air_f(79, 54.5), air_f(75, td_f=50)), cfg, GroupState()).reason_code == "BELOW_TARGET"


# 11
def test_high_indoor_rh_but_wetter_outside_stays_off():
    d = run(cond(air_f(79, 90), air_f(88, td_f=78)))
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"


def test_humid_basement_with_modest_benefit_can_run_even_if_target_unreachable():
    # basement 90% RH; outdoor air would be ~80% at basement temperature. That can never reach the
    # 55% target, but it is still a net drying benefit, so ventilation is allowed.
    indoor = air_f(79, 90)
    outdoor = air_f(79, 80)
    d = run(cond(indoor, outdoor))
    assert d.metrics["outdoor_rh_at_basement_temp_pct"] == pytest.approx(80, abs=0.5)
    assert d.target_on and d.effect.net_removal_g_h > 0


def test_65pct_basement_with_outdoor_at_85pct_equivalent_stays_off():
    d = run(cond(air_f(79, 65), air_f(79, 85)))
    assert not d.target_on and d.reason_code == "OUTDOOR_WETTER"


# 12
def test_energy_saver_leaves_fans_off_when_cost_exceeds_threshold():
    from ventctl.units import c_to_f
    indoor = air_f(79, 80)
    td_in = c_to_f(indoor.dew_point_c)
    c = cond(indoor, air_f(92, td_f=td_in - 3.5))
    base = config()
    base.house.conditioned_air_fraction = 0.5  # half the exhaust is conditioned air
    saver = base.model_copy(deep=True)
    saver.control.energy_profile = EnergyProfile.ENERGY_SAVER
    maxd = base.model_copy(deep=True)
    maxd.control.energy_profile = EnergyProfile.MAX_DRYING
    d_saver = run(c, saver)
    d_max = run(c, maxd)
    assert d_saver.effect.net_removal_g_h > 0
    assert not d_saver.target_on and d_saver.reason_code in ("EFFICIENCY_TOO_LOW", "BENEFIT_TOO_SMALL")
    assert d_max.target_on


def test_interlocks_block_and_are_hard():
    cfg = config(water_intrusion_lockout=True)
    d = run(cond(air_f(79, 95), air_f(80, td_f=60)), cfg)
    assert not d.target_on and d.reason_code == "INTERLOCK_WATER" and d.safety
    cfg = config(combustion_interlock=True)
    assert run(cond(air_f(79, 95), air_f(80, td_f=60)), cfg).reason_code == "INTERLOCK_COMBUSTION"
    cfg = config(max_depressurization_pa=5)
    assert run(cond(air_f(79, 95), air_f(80, td_f=60), dp=-7), cfg).reason_code == "INTERLOCK_PRESSURE"
    assert run(cond(air_f(79, 95), air_f(80, td_f=60), dp=-3), cfg).target_on


def test_manual_on_is_time_limited_and_respects_interlocks():
    c = cond(air_f(79, 60), air_f(85, td_f=76), surface_f=82)  # wetter outside, but manual
    st = GroupState(manual_until=T0 + minutes(30))
    d = run(c, mode=Mode.MANUAL_ON, state=st)
    assert d.target_on and d.reason_code == "MANUAL_ON"
    d = run(c, mode=Mode.MANUAL_ON, state=st, now=T0 + minutes(31))
    assert not d.target_on and d.reason_code == "MANUAL_EXPIRED"
    # condensation interlock still applies
    c2 = cond(air_f(79, 60), air_f(85, td_f=76), surface_f=74)
    assert run(c2, mode=Mode.MANUAL_ON, state=st).reason_code == "CONDENSATION_RISK"
    # unless in authorized maintenance mode
    st.maintenance = True
    d = run(c2, mode=Mode.MANUAL_ON, state=st)
    assert d.target_on and any("MAINTENANCE" in w for w in d.warnings)


def test_off_and_schedule_modes():
    c = cond(air_f(79, 90), air_f(80, td_f=60))
    assert run(c, mode=Mode.OFF).reason_code == "MODE_OFF"
    cfg = config()  # default schedule 20:00-08:00
    assert run(c, cfg, mode=Mode.SCHEDULE, hour=22).target_on
    assert run(c, cfg, mode=Mode.SCHEDULE, hour=12).reason_code == "SCHEDULE_OUTSIDE"
    # schedule still fails safe on stale sensors
    assert run(cond(air_f(79, 90), None, outdoor_problem="x not updated"), cfg, mode=Mode.SCHEDULE,
               hour=22).safety


# 17
def test_negative_removal_is_reported_as_moisture_addition():
    cfg = config()
    c = cond(air_f(79, 70), air_f(84, td_f=74))
    d = run(c, cfg, mode=Mode.SCHEDULE, hour=22)
    assert d.target_on and d.effect.net_removal_g_h < 0
    assert "adding moisture" in d.explanation


def test_simulation_mode_uses_full_logic():
    d = run(cond(air_f(79, 90), air_f(80, td_f=60)), mode=Mode.SIMULATION)
    assert d.target_on and d.mode == "simulation"


# 13
def test_multi_room_zone_selection_and_aggregation():
    cfg = config()
    snap = Snapshot(T0)
    for sid, zone, t, rh in (("a", "main", 25.0, 60.0), ("b", "room2", 24.0, 80.0)):
        m = SensorMapping(sensor_id=sid, name=sid, role=SensorRole.BASEMENT, zone=zone)
        snap.readings.append(validate(m, SensorReading(sid, T0, temp_c=t, rh_pct=rh), T0, 300))
    mo = SensorMapping(sensor_id="o", name="out", role=SensorRole.OUTDOOR)
    snap.readings.append(validate(mo, SensorReading("o", T0, temp_c=20, rh_pct=50), T0, 300))

    cfg.control.zone_aggregation = "wettest"
    c = conditions_for_group(snap, FanGroup(id="all", name="all"), cfg)
    assert c.indoor.rh == pytest.approx(0.80)
    cfg.control.zone_aggregation = "mean"
    c = conditions_for_group(snap, FanGroup(id="all", name="all"), cfg)
    from ventctl.psychro import humidity_ratio
    assert c.indoor.w == pytest.approx((humidity_ratio(25, 0.6) + humidity_ratio(24, 0.8)) / 2, rel=1e-6)
    assert c.indoor.t_c == pytest.approx(24.5)
    c = conditions_for_group(snap, FanGroup(id="g2", name="room2", zone="room2"), cfg)
    assert c.indoor.rh == pytest.approx(0.80)
    c = conditions_for_group(snap, FanGroup(id="g1", name="main", zone="main"), cfg)
    assert c.indoor.rh == pytest.approx(0.60)
    # a stale zone sensor is excluded from the aggregate and reported
    snap.readings[1] = validate(snap.readings[1].mapping, SensorReading("b", T0 - timedelta(minutes=9),
                                                                        temp_c=24, rh_pct=80), T0, 300)
    c = conditions_for_group(snap, FanGroup(id="all", name="all"), cfg)
    assert c.indoor.rh == pytest.approx(0.60) and "unavailable" in c.indoor_label
    c = conditions_for_group(snap, FanGroup(id="g2", name="room2", zone="room2"), cfg)
    assert c.indoor is None and "9 minutes" in c.indoor_problem


def test_stop_gap_is_configurable_and_running_fans_ride_it_down():
    from ventctl.units import c_to_f
    indoor = air_f(79, 85)
    td_in = c_to_f(indoor.dew_point_c)
    on = GroupState(is_on=True, last_change=T0 - minutes(120))
    off = GroupState(is_on=False, last_change=T0 - minutes(120))
    cfg = config()
    # 1.5 F gap: too small to start, but a running fan keeps going (default stop gap 1 F)
    assert not decide(cond(indoor, air_f(82, td_f=td_in - 1.5)), off, cfg, cfg.groups[0], T0).target_on
    assert decide(cond(indoor, air_f(82, td_f=td_in - 1.5)), on, cfg, cfg.groups[0], T0).target_on
    assert not decide(cond(indoor, air_f(82, td_f=td_in - 0.8)), on, cfg, cfg.groups[0], T0).target_on
    # a smaller stop gap keeps it running closer to equal
    cfg = config(dewpoint_off_advantage_f=0.5)
    assert decide(cond(indoor, air_f(82, td_f=td_in - 0.8)), on, cfg, cfg.groups[0], T0).target_on
