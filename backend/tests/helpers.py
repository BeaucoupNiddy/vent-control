from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ventctl.config import AppConfig, ControlSettings, Fan, FanType, example_config
from ventctl.psychro import AirState, rh_from_dew_point
from ventctl.sensors import Conditions
from ventctl.units import f_to_c

T0 = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def air_f(t_f: float, rh_pct: float | None = None, td_f: float | None = None) -> AirState:
    t = f_to_c(t_f)
    rh = rh_pct / 100 if rh_pct is not None else rh_from_dew_point(t, f_to_c(td_f))
    return AirState.from_t_rh(t, rh)


def cond(indoor: AirState | None, outdoor: AirState | None, surface_f: float | None = 85.0,
         surface_source: str = "measured", upstairs: AirState | None = None, dp: float | None = None,
         indoor_problem: str | None = None, outdoor_problem: str | None = None) -> Conditions:
    return Conditions(
        indoor=indoor, indoor_label="test", indoor_problem=indoor_problem if indoor is None else None,
        outdoor=outdoor, outdoor_problem=outdoor_problem if outdoor is None else None,
        surface_c=f_to_c(surface_f) if surface_f is not None else None,
        surface_source=surface_source if surface_f is not None else None, surface_label="slab",
        upstairs=upstairs, diff_pressure_pa=dp,
    )


def config(**control) -> AppConfig:
    c = example_config()
    c.control = ControlSettings(**{**c.control.model_dump(), **control})
    return c


def exhaust_fans(n: int = 2, cfm: float = 85, w: float = 28) -> list[Fan]:
    return [Fan(id=f"e{i}", name=f"E{i}", type=FanType.EXHAUST, actual_cfm=cfm, rated_w=w,
                switch_id=f"mock-plug-{i + 1}") for i in range(n)]


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)
