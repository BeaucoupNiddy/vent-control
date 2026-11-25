"""TAMO start threshold (2026-11-25): the 150 g/h bar blocked useful drying at moderate humidity."""

from ventctl.config import EnergyProfile, example_config, upgrade_defaults

from .test_tamo import run, tcfg
from .helpers import air_f


def _owner_like():
    # Patrick's settings on 2026-11-25: Maximum Drying, wide preferred band, urgency 0 at 55% and 1 at 93% RH
    cfg = tcfg(preferred_min_f=55, preferred_max_f=83, urgency_low_rh_pct=55, urgency_high_rh_pct=93)
    cfg.control.energy_profile = EnergyProfile.MAX_DRYING
    return cfg


def test_moderate_humidity_with_a_real_gap_starts_the_fans():
    # basement 67°F / 68% RH, outdoor 57°F / 80%: about 700 g/h (4-5 gallons a day) of drying
    d = run(air_f(67, 68), air_f(57, 80), surface_f=63, cfg=_owner_like())
    assert d.target_on, d.explanation


def test_the_old_150_bar_would_have_kept_them_off():
    cfg = _owner_like()
    cfg.temperature.utility_start_g_h = 150
    d = run(air_f(67, 68), air_f(57, 80), surface_f=63, cfg=cfg)
    assert not d.target_on


def test_small_gap_still_does_not_start():
    # outdoor only marginally drier: the 0.6 g/kg gap check keeps sensor noise from starting the fans
    d = run(air_f(66, 68), air_f(60, 80), surface_f=63, cfg=_owner_like())  # about 0.45 g/kg
    assert not d.target_on
    assert d.reason_code == "MOISTURE_BENEFIT_INSUFFICIENT"


def test_default_upgrade_only_replaces_the_old_default():
    cfg = example_config()
    cfg.temperature.utility_start_g_h = 150
    assert any("utility_start_g_h" in c for c in upgrade_defaults(cfg, 3))
    assert cfg.temperature.utility_start_g_h == 50
    custom = example_config()
    custom.temperature.utility_start_g_h = 120
    upgrade_defaults(custom, 3)
    assert custom.temperature.utility_start_g_h == 120
