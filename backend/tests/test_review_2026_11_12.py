"""Fixes from the 2026-11-12 fan physics review."""

from __future__ import annotations

from datetime import timedelta

from ventctl import airflow, circulation, engine
from ventctl.circulation import CircState, decide_circulator
from ventctl.config import AppConfig, example_config
from ventctl.engine import GroupState, decide, humidity_demand
from ventctl.units import f_to_c

from .helpers import T0, air_f, cond, config, exhaust_fans
from .test_v3 import auto_cfg, circ, probe, zs


def _cfg(**control) -> AppConfig:
    return config(**control)


def _rooms_cond(rooms, outdoor, surfaces=None):
    """Conditions with several basement sensors; indoor = highest humidity ratio (the 'wettest' aggregate)."""
    airs = [a for _, a in rooms]
    c = cond(max(airs, key=lambda a: a.w), outdoor, surface_f=None)
    c.rooms = [{"name": n, "zone": None, "sensor_id": n, "air": a} for n, a in rooms]
    if surfaces:
        c.surfaces = surfaces
        c.surface_c = min(s["t_c"] for s in surfaces)
        c.surface_source = "measured"
    return c


def _run(c, cfg, on=False):
    st = GroupState(is_on=on, verified=True, last_change=T0 - timedelta(hours=2))
    return decide(c, st, cfg, cfg.groups[0], T0)


def test_cool_room_at_high_rh_creates_demand():
    """Review finding 1: 75°F/55% holds more water than 60°F/80%, but the cool room must drive demand."""
    cfg = _cfg(surface_temp_estimate_f=None)
    warm, cool = air_f(75, 55), air_f(60, 80)
    assert warm.w > cool.w
    outdoor = air_f(66, td_f=40)  # dry outside, warm enough to pass the no-probe check
    c = _rooms_cond([("Warm room", warm), ("Cool room", cool)], outdoor)
    dem = humidity_demand(c, c.indoor, outdoor, cfg)
    assert dem.room_label == "Cool room" and round(dem.room_rh_pct) == 80
    d = _run(c, cfg)
    assert d.reason_code != "BELOW_TARGET"
    assert d.target_on and d.reason_code == "DRYING"


def test_room_ventilation_cannot_dry_is_skipped():
    cfg = _cfg()
    humid_cool = air_f(60, 80)  # ~8.8 g/kg
    dry_warm = air_f(75, 45)
    outdoor = air_f(70, td_f=56)  # ~9.6 g/kg: wetter than the cool room
    c = _rooms_cond([("Warm", dry_warm), ("Cool", humid_cool)], outdoor)
    dem = humidity_demand(c, c.indoor, outdoor, cfg)
    assert "Cool" in dem.skipped_rooms


def test_surface_demand_runs_fans_when_room_air_is_below_target():
    cfg = _cfg()
    room = air_f(71, 52)  # below the 55% room target
    outdoor = air_f(66, td_f=40)
    slab = {"name": "Slab", "t_c": f_to_c(60), "zone": "main", "local": room, "exposure": 1.0}
    c = _rooms_cond([("Main", room)], outdoor, [slab])
    dem = humidity_demand(c, c.indoor, outdoor, cfg)
    assert dem.surface_rh_pct > 75  # 60°F slab minus 2°F probe uncertainty under 71°F/52% air
    d = _run(c, cfg)
    assert d.target_on and "SURFACE_HUMIDITY_HIGH" in d.reasons
    # turned off: the room target alone decides
    cfg.control.surface_demand_enabled = False
    assert not _run(c, cfg).target_on


def test_measured_surface_margin_includes_probe_uncertainty(monkeypatch):
    """A measured probe's own and the dew point's uncertainty is added to the required margin."""
    seen = []
    real = engine._condensation
    monkeypatch.setattr(engine, "_condensation", lambda c, xa, tres, m: seen.append(m) or real(c, xa, tres, m))
    cfg = _cfg()
    on = GroupState(is_on=True, verified=True, last_change=T0 - timedelta(hours=2))
    decide(cond(air_f(70, 65), air_f(45, td_f=38), surface_f=62, surface_source="measured"), on, cfg,
           cfg.groups[0], T0)
    decide(cond(air_f(70, 65), air_f(45, td_f=38), surface_f=62, surface_source="estimated"), on, cfg,
           cfg.groups[0], T0)
    unc = (cfg.surfaces.probe_uncertainty_f ** 2 + cfg.surfaces.dew_point_uncertainty_f ** 2) ** 0.5
    assert abs(seen[0] - (cfg.control.surface_margin_f + unc)) < 1e-9
    assert seen[1] == cfg.control.surface_margin_f


def test_circulation_condensation_stop_overrides_minimum_runtime():
    """Review finding 2: a predicted worsening must stop the fan now, not after 15 minutes."""
    cfg = auto_cfg()
    pocket = zs(cfg, "pocket", 72, 72, {"elevated": 60, "high": 40, "critical": 0})
    damp = zs(cfg, "damp", 64, 55)
    z = {"main": zs(cfg, "main", 72, 55), "pocket": pocket, "damp": damp}
    st = CircState(is_on=True, last_change=T0 - timedelta(minutes=1), last_reason="LOCAL_HUMIDITY_HIGH")
    d = decide_circulator(circ(cfg, "circ-2"), cfg, z, [probe(cfg, damp, 55)], st, T0)
    assert d.reason_code == "CIRCULATION_WORSENS_SURFACE_RISK"
    assert not d.target_on and not d.held and d.safety


def test_run_with_exhaust_needs_support_flag_and_sensors():
    """Review finding 4: the blanket rule no longer overrides the per-fan and data checks."""
    cfg = auto_cfg()
    ext = {"on": True, "drying": True, "auto_on": True}
    z = {"main": zs(cfg, "main", 72, 58), "pocket": zs(cfg, "pocket", 72, 58)}  # no damp sensor
    d1 = decide_circulator(circ(cfg, "circ-1"), cfg, z, [], CircState(), T0, ext)
    assert d1.target_on and d1.reason_code == "RUN_WITH_EXHAUST"
    # circ-2: not marked for ventilation support
    d2 = decide_circulator(circ(cfg, "circ-2"), cfg, z, [], CircState(), T0, ext)
    assert d2.reason_code != "RUN_WITH_EXHAUST"
    # even when marked, its destination (damp) has no sensor
    circ(cfg, "circ-2").ventilation_support = True
    d3 = decide_circulator(circ(cfg, "circ-2"), cfg, z, [], CircState(), T0, ext)
    assert d3.reason_code != "RUN_WITH_EXHAUST" and "RUN_WITH_EXHAUST_NO_DATA" in d3.reasons
    # exhaust running but not drying (e.g. a schedule adding moisture): no run
    d4 = decide_circulator(circ(cfg, "circ-1"), cfg, z, [], CircState(), T0,
                           {"on": True, "drying": False, "auto_on": True})
    assert d4.reason_code != "RUN_WITH_EXHAUST"


def test_mild_humid_day_charges_latent_hvac_energy():
    """Review finding 3: no more free replacement air between 55 and 78°F when it is muggy."""
    cfg = example_config()
    cfg.fans = exhaust_fans(2, cfm=220, w=40)
    up, out, base = air_f(74, 50), air_f(75, 65), air_f(80, 70)
    e = airflow.ventilation_effect(base, out, cfg.fans, cfg.house, up)
    assert 50 < e.conditioned_air_penalty_w < 90  # ~68 W in the review's illustration
    cfg.house.mild_weather_latent_penalty = False
    assert airflow.ventilation_effect(base, out, cfg.fans, cfg.house, up).conditioned_air_penalty_w == 0
    # mild and drier than upstairs: still free
    cfg.house.mild_weather_latent_penalty = True
    assert airflow.ventilation_effect(base, air_f(70, 40), cfg.fans, cfg.house, up).conditioned_air_penalty_w == 0


def test_circulator_power_counts_toward_exhaust_efficiency():
    cfg = auto_cfg()
    cfg.circulation.run_with_exhaust = True
    aux = engine.circulator_aux_w(cfg)
    assert aux == sum(c.rated_w for c in cfg.circulators if c.ventilation_support)
    e0 = airflow.ventilation_effect(air_f(75, 65), air_f(70, td_f=45), exhaust_fans(), cfg.house)
    e1 = airflow.ventilation_effect(air_f(75, 65), air_f(70, td_f=45), exhaust_fans(), cfg.house, aux_w=aux)
    assert e1.fan_power_w == e0.fan_power_w + aux and e1.efficiency_g_per_kwh < e0.efficiency_g_per_kwh
    cfg.circulation.mode = "shadow"
    assert engine.circulator_aux_w(cfg) == 0


def test_probe_uncertainty_default_upgrade():
    from ventctl.config import upgrade_defaults
    cfg = example_config()
    cfg.surfaces.probe_uncertainty_f = 1.0
    assert any("probe_uncertainty_f" in c for c in upgrade_defaults(cfg, 1))
    assert cfg.surfaces.probe_uncertainty_f == 2.0
