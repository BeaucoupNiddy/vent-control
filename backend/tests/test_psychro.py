import math

import pytest

from ventctl import psychro, units
from ventctl.config import SensorMapping, SensorRole
from ventctl.psychro import AirState
from ventctl.sensors import SensorReading, validate

from .helpers import T0, air_f, minutes


@pytest.mark.parametrize("t_c, expected_kpa", [
    (0.01, 0.6117),  # ASHRAE Fundamentals Table 3
    (20.0, 2.3389),
    (25.0, 3.1699),
    (30.0, 4.2467),
    (40.0, 7.3844),
])
def test_saturation_pressure_matches_ashrae_table(t_c, expected_kpa):
    assert psychro.saturation_pressure(t_c) == pytest.approx(expected_kpa, rel=5e-4)


def test_humidity_ratio_at_saturation_matches_ashrae():
    # Ideal-gas humidity ratio (no enhancement factor). ASHRAE Table 2 lists 0.020170 at 25 C,
    # which includes the ~0.4% enhancement factor; far below sensor accuracy (+/-2-3% RH).
    assert psychro.humidity_ratio(25.0, 1.0) == pytest.approx(0.020170, rel=6e-3)
    assert psychro.humidity_ratio(25.0, 1.0) == pytest.approx(0.020083, rel=1e-3)
    assert psychro.humidity_ratio(25.0, 0.5) == pytest.approx(0.009875, rel=2e-3)


def test_dew_point_roundtrip_and_known_values():
    for t in (-10, 0, 5, 15, 25, 35):
        for rh in (0.1, 0.4, 0.75, 1.0):
            td = psychro.dew_point(t, rh)
            assert psychro.saturation_pressure(td) == pytest.approx(rh * psychro.saturation_pressure(t), rel=1e-7)
    assert psychro.dew_point(25, 1.0) == pytest.approx(25, abs=1e-6)
    # 79 F at 95% RH has a dew point of about 77.4 F; at 70% about 68.4 F
    assert units.c_to_f(psychro.dew_point(units.f_to_c(79), 0.95)) == pytest.approx(77.4, abs=0.15)
    assert units.c_to_f(psychro.dew_point(units.f_to_c(79), 0.70)) == pytest.approx(68.4, abs=0.15)


def test_humidity_ratio_from_dew_point_and_rh_conversions():
    w = psychro.humidity_ratio_from_dew_point(units.f_to_c(74))
    assert w == pytest.approx(0.0181, abs=0.0002)
    a = AirState.from_t_rh(26, 0.6)
    assert a.rh_at(26) == pytest.approx(0.6, rel=1e-9)
    assert a.rh_at(20) > 0.6  # cooling raises RH
    b = AirState.from_t_w(26, a.w)
    assert b.rh == pytest.approx(0.6, rel=1e-6)


def test_density_enthalpy_and_absolute_humidity_plausible():
    rho = psychro.dry_air_density(20, 0.0)
    assert rho == pytest.approx(1.204, abs=0.003)
    assert psychro.enthalpy(25, 0.01) == pytest.approx(50.6, abs=0.3)
    # 30 C saturated air holds ~30.4 g/m3
    assert psychro.absolute_humidity(30, 1.0) == pytest.approx(30.4, abs=0.4)


def test_input_validation():
    with pytest.raises(ValueError):
        psychro.vapor_pressure(20, 1.5)
    with pytest.raises(ValueError):
        psychro.saturation_pressure(500)


def test_unit_conversions():
    assert units.f_to_c(32) == 0
    assert units.c_to_f(100) == 212
    assert units.f_to_c(units.c_to_f(23.7)) == pytest.approx(23.7)
    assert units.delta_f_to_c(9) == pytest.approx(5)
    assert units.cfm_to_m3s(1000) == pytest.approx(0.4719, abs=1e-4)
    assert units.grams_to_gallons(3785.411784) == pytest.approx(1.0)
    assert units.g_per_h_to_gal_per_day(1000) == pytest.approx(24 / 3.785411784)
    assert units.pints_per_day_to_g_per_h(24) == pytest.approx(473.176473)
    assert units.inhg_to_kpa(29.92) == pytest.approx(101.32, abs=0.01)


def test_sensor_calibration_offsets_applied():
    m = SensorMapping(sensor_id="s", name="S", role=SensorRole.BASEMENT, temp_offset_f=1.8, rh_offset_pct=-3)
    v = validate(m, SensorReading("s", T0, temp_c=25.0, rh_pct=60.0), T0, 300)
    assert v.ok
    assert v.temp_c == pytest.approx(26.0)
    assert v.rh == pytest.approx(0.57)


def test_sensor_validation_rejects_implausible_and_stale():
    m = SensorMapping(sensor_id="s", name="S", role=SensorRole.OUTDOOR)
    assert validate(m, SensorReading("s", T0, temp_c=99, rh_pct=50), T0, 300).status == "implausible"
    assert validate(m, SensorReading("s", T0, temp_c=20, rh_pct=140), T0, 300).status == "implausible"
    assert validate(m, SensorReading("s", T0, temp_c=20, rh_pct=None), T0, 300).status == "missing"
    v = validate(m, SensorReading("s", T0 - minutes(7), temp_c=20, rh_pct=50), T0, 300)
    assert v.status == "stale" and "7 minutes" in v.message
    assert v.air() is None  # stale data can never produce an air state
    assert validate(m, None, T0, 300).status == "missing"
    dup = validate(m, SensorReading("s", T0, temp_c=20, rh_pct=50), T0, 300, previous_ts=T0)
    assert dup.duplicate and dup.ok


def test_air_f_helper_consistency():
    a = air_f(79, td_f=74)
    assert units.c_to_f(a.dew_point_c) == pytest.approx(74, abs=1e-6)
    assert not math.isnan(a.w)
