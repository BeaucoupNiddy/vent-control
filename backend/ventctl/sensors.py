"""Sensor readings, validation, and conversion into engine inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from . import psychro
from .config import AppConfig, FanGroup, SensorMapping, SensorRole
from .psychro import AirState
from .units import delta_f_to_c

Status = Literal["ok", "stale", "implausible", "missing", "disabled"]


@dataclass
class SensorReading:
    """A raw reading as returned by a hardware adapter (SI units)."""

    sensor_id: str
    ts: datetime  # timezone-aware, when the device says it measured
    temp_c: float | None = None
    rh_pct: float | None = None
    pressure_kpa: float | None = None
    diff_pressure_pa: float | None = None  # basement minus upstairs (negative = basement depressurized)
    battery_low: bool | None = None
    signal: int | None = None
    source: str = "mock"


@dataclass
class ValidatedReading:
    mapping: SensorMapping
    status: Status
    age_s: float | None
    temp_c: float | None = None  # calibrated
    rh: float | None = None  # calibrated fraction
    pressure_kpa: float | None = None
    diff_pressure_pa: float | None = None
    battery_low: bool | None = None
    signal: int | None = None
    message: str = ""
    duplicate: bool = False
    source: str = "mock"
    raw_temp_c: float | None = None  # as reported by the device, before calibration offsets
    raw_rh_pct: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def air(self) -> AirState | None:
        if not self.ok or self.temp_c is None or self.rh is None:
            return None
        return AirState.from_t_rh(self.temp_c, self.rh, self.pressure_kpa)

    def to_dict(self) -> dict:
        a = self.air()
        return {
            "sensor_id": self.mapping.sensor_id,
            "name": self.mapping.name,
            "role": self.mapping.role.value,
            "zone": self.mapping.zone,
            "status": self.status,
            "age_s": self.age_s,
            "temp_c": self.temp_c,
            "rh_pct": None if self.rh is None else self.rh * 100,
            "dew_point_c": a.dew_point_c if a else None,
            "w": a.w if a else None,
            "diff_pressure_pa": self.diff_pressure_pa,
            "battery_low": self.battery_low,
            "signal": self.signal,
            "message": self.message,
            "duplicate": self.duplicate,
            "source": self.source,
            "raw_temp_c": self.raw_temp_c, "raw_rh_pct": self.raw_rh_pct,
            "temp_offset_f": self.mapping.temp_offset_f, "rh_offset_pct": self.mapping.rh_offset_pct,
            "calibrated_at": self.mapping.calibrated_at,
        }


PLAUSIBLE_T = (-40.0, 65.0)


def validate(
    mapping: SensorMapping,
    reading: SensorReading | None,
    now: datetime,
    stale_after_s: float,
    previous_ts: datetime | None = None,
    default_pressure_kpa: float | None = None,
) -> ValidatedReading:
    if not mapping.enabled:
        return ValidatedReading(mapping, "disabled", None, message="Sensor disabled in settings")
    if reading is None:
        return ValidatedReading(mapping, "missing", None, message=f"{mapping.name}: no data received")

    age = (now - reading.ts).total_seconds()
    v = ValidatedReading(
        mapping, "ok", age,
        pressure_kpa=reading.pressure_kpa or default_pressure_kpa, diff_pressure_pa=reading.diff_pressure_pa,
        battery_low=reading.battery_low, signal=reading.signal, source=reading.source,
        duplicate=previous_ts is not None and reading.ts == previous_ts,
        raw_temp_c=reading.temp_c, raw_rh_pct=reading.rh_pct,
    )
    if age > stale_after_s:
        v.status = "stale"
        v.message = f"{mapping.name} has not updated for {age / 60:.0f} minutes"
        return v
    if age < -120:
        v.status = "implausible"
        v.message = f"{mapping.name} timestamp is in the future"
        return v

    if mapping.role == SensorRole.PRESSURE:
        if reading.diff_pressure_pa is None or abs(reading.diff_pressure_pa) > 500:
            v.status = "implausible"
            v.message = f"{mapping.name}: implausible pressure reading"
        return v

    if reading.temp_c is None:
        v.status = "missing"
        v.message = f"{mapping.name}: no temperature in reading"
        return v
    t = reading.temp_c + delta_f_to_c(mapping.temp_offset_f)
    if not (PLAUSIBLE_T[0] <= t <= PLAUSIBLE_T[1]):
        v.status = "implausible"
        v.message = f"{mapping.name}: implausible temperature {t:.1f} C"
        return v
    v.temp_c = t

    if mapping.role in (SensorRole.SURFACE, SensorRole.PIPE):
        return v  # surface and pipe probes need temperature only

    if reading.rh_pct is None:
        v.status = "missing"
        v.message = f"{mapping.name}: no humidity in reading"
        return v
    rh = reading.rh_pct + mapping.rh_offset_pct
    if rh < 0.5 or rh > 103:
        v.status = "implausible"
        v.message = f"{mapping.name}: implausible humidity {rh:.0f}%"
        return v
    v.rh = min(rh, 100.0) / 100.0
    return v


@dataclass
class Snapshot:
    """All validated readings at one moment."""

    ts: datetime
    readings: list[ValidatedReading] = field(default_factory=list)

    def by_role(self, role: SensorRole) -> list[ValidatedReading]:
        return [r for r in self.readings if r.mapping.role == role and r.status != "disabled"]

    def to_dict(self) -> dict:
        return {"ts": self.ts.isoformat(), "readings": [r.to_dict() for r in self.readings]}


@dataclass
class Conditions:
    """Engine input for one fan group, derived from a Snapshot."""

    indoor: AirState | None
    indoor_label: str
    indoor_problem: str | None
    outdoor: AirState | None
    outdoor_problem: str | None
    surface_c: float | None
    surface_source: Literal["measured", "estimated"] | None
    surface_label: str | None
    upstairs: AirState | None
    diff_pressure_pa: float | None
    zones: dict[str, dict] = field(default_factory=dict)
    surfaces: list[dict] = field(default_factory=list)  # valid surface probes {name, t_c, zone, local, exposure}
    surface_follow: float = 0.2  # fraction of an air-temperature change the surface follows within the horizon
    pipe_c: float | None = None  # coldest pipe / vulnerable-spot sensor
    pipe_label: str | None = None
    pipe_sensors: int = 0  # configured pipe sensors (valid or not)
    exhaust: AirState | None = None  # air at the exhaust fan (ventilation-path zones); None = use indoor
    exhaust_label: str | None = None
    forecast: list | None = None  # [(datetime, AirState)] outdoor forecast, optional
    forecast_age_h: float | None = None
    # every usable basement sensor on its own: humidity demand is judged room by room (a cool room can be at
    # 80% RH while holding less water than a warm room at 55%), removal on the exhaust air
    rooms: list[dict] = field(default_factory=list)  # {name, zone, sensor_id, air}


def aggregate_zones(airs: list[AirState], method: str) -> AirState:
    if len(airs) == 1:
        return airs[0]
    if method == "wettest":
        # by moisture content (the air ventilation would remove). Humidity *demand* is judged per room
        # in engine.humidity_demand, so a cool room with high RH is not missed.
        return max(airs, key=lambda a: a.w)
    # mean: average temperature and humidity ratio (moisture content), then derive RH
    t = sum(a.t_c for a in airs) / len(airs)
    w = sum(a.w for a in airs) / len(airs)
    return AirState.from_t_w(t, w, airs[0].p_kpa)


def conditions_for_group(snapshot: Snapshot, group: FanGroup, config: AppConfig) -> Conditions:
    zones: dict[str, dict] = {}
    basement = snapshot.by_role(SensorRole.BASEMENT)
    if group.zone:
        basement = [r for r in basement if r.mapping.zone == group.zone]
    good = [(r, r.air()) for r in basement if r.ok]
    good = [(r, a) for r, a in good if a is not None]
    for r in basement:
        zones[r.mapping.zone or r.mapping.sensor_id] = r.to_dict()

    indoor_problem = None
    if not basement:
        indoor = None
        indoor_problem = "No basement sensor is configured" + (f" for zone '{group.zone}'" if group.zone else "")
        label = "-"
    elif not good:
        indoor = None
        indoor_problem = "; ".join(r.message for r in basement)
        label = "-"
    else:
        indoor = aggregate_zones([a for _, a in good], config.control.zone_aggregation)
        if len(good) == 1:
            label = good[0][0].mapping.name
        else:
            label = f"{config.control.zone_aggregation} of {len(good)} zones"
        bad = [r for r in basement if not r.ok]
        if bad:
            label += f" ({len(bad)} zone sensor(s) unavailable)"

    # The exhaust fan removes the air around it, not the wettest zone's air: direction and
    # removal are judged on the ventilation-path zones, urgency on the aggregate above.
    exhaust, exhaust_label = None, None
    path = {z.id for z in config.zones if z.on_ventilation_path}
    if indoor is not None and not group.zone and path and len(path) < len(config.zones):
        pa = [a for r, a in good if r.mapping.zone in path]
        if pa:
            exhaust = AirState.from_t_w(sum(a.t_c for a in pa) / len(pa), sum(a.w for a in pa) / len(pa),
                                        pa[0].p_kpa)
            exhaust_label = ", ".join(sorted({r.mapping.zone for r, _ in good if r.mapping.zone in path}))

    outdoor_rs = snapshot.by_role(SensorRole.OUTDOOR)
    outdoor = next((r.air() for r in outdoor_rs if r.ok), None)
    outdoor_problem = None
    if outdoor is None:
        outdoor_problem = "; ".join(r.message for r in outdoor_rs) or "No outdoor sensor is configured"

    surf = [r for r in snapshot.by_role(SensorRole.SURFACE) if r.ok and r.temp_c is not None]
    if surf:
        coldest = min(surf, key=lambda r: r.temp_c)
        surface_c, surface_source, surface_label = coldest.temp_c, "measured", coldest.mapping.name
    elif config.control.surface_temp_estimate_f is not None:
        from .units import f_to_c

        surface_c = f_to_c(config.control.surface_temp_estimate_f)
        surface_source, surface_label = "estimated", "configured minimum surface temperature"
    else:
        surface_c = surface_source = surface_label = None

    pipes_all = snapshot.by_role(SensorRole.PIPE)
    pipes = [r for r in pipes_all if r.ok and r.temp_c is not None]
    pipe = min(pipes, key=lambda r: r.temp_c) if pipes else None

    up = next((r.air() for r in snapshot.by_role(SensorRole.UPSTAIRS) if r.ok), None)
    dp = next((r.diff_pressure_pa for r in snapshot.by_role(SensorRole.PRESSURE) if r.ok), None)

    return Conditions(
        indoor=indoor, indoor_label=label, indoor_problem=indoor_problem,
        outdoor=outdoor, outdoor_problem=outdoor_problem,
        surface_c=surface_c, surface_source=surface_source, surface_label=surface_label,
        upstairs=up, diff_pressure_pa=dp, zones=zones, exhaust=exhaust, exhaust_label=exhaust_label,
        surfaces=[_surface_entry(r, snapshot, config) for r in surf],
        pipe_c=pipe.temp_c if pipe else None, pipe_label=pipe.mapping.name if pipe else None,
        pipe_sensors=len(pipes_all),
        rooms=[{"name": r.mapping.name, "zone": r.mapping.zone, "sensor_id": r.mapping.sensor_id, "air": a}
               for r, a in good],
    )


def _zone_air(snapshot: Snapshot, zone: str | None) -> AirState | None:
    if not zone:
        return None
    airs = [r.air() for r in snapshot.by_role(SensorRole.BASEMENT) if r.ok and r.mapping.zone == zone]
    airs = [a for a in airs if a is not None]
    if not airs:
        return None
    return AirState.from_t_w(sum(a.t_c for a in airs) / len(airs), sum(a.w for a in airs) / len(airs), airs[0].p_kpa)


def _surface_entry(r: ValidatedReading, snapshot: Snapshot, config: AppConfig) -> dict:
    z = config.zone(r.mapping.zone)
    return {"name": r.mapping.name, "t_c": r.temp_c, "sensor_id": r.mapping.sensor_id, "zone": r.mapping.zone,
            "local": _zone_air(snapshot, r.mapping.zone), "exposure": z.ventilation_exposure if z else 1.0}


__all__ = [
    "SensorReading", "ValidatedReading", "validate", "Snapshot", "Conditions",
    "conditions_for_group", "aggregate_zones", "psychro",
]
