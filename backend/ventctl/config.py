"""Application configuration (validated with Pydantic).

Field names carry their units (``_f`` = deg F, ``_cfm``, ``_w`` = watts, ``_min`` =
minutes) because these are the values the person types into the settings
screen. The engine converts to SI before calculating.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Mode(str, Enum):
    AUTO = "auto"
    OFF = "off"
    MANUAL_ON = "manual_on"
    SCHEDULE = "schedule"
    SIMULATION = "simulation"


class EnergyProfile(str, Enum):
    MAX_DRYING = "max_drying"
    BALANCED = "balanced"
    ENERGY_SAVER = "energy_saver"


class FanType(str, Enum):
    INTAKE = "intake"
    EXHAUST = "exhaust"


class SensorRole(str, Enum):
    OUTDOOR = "outdoor"
    BASEMENT = "basement"  # an indoor basement zone
    SURFACE = "surface"  # concrete / wood / pipe surface temperature
    UPSTAIRS = "upstairs"  # conditioned living space
    PRESSURE = "pressure"  # basement-to-upstairs differential pressure
    IGNORE = "ignore"


class SensorMapping(BaseModel):
    sensor_id: str
    name: str
    role: SensorRole
    zone: str | None = Field(None, description="Zone id for basement sensors, e.g. 'main'")
    temp_offset_f: float = Field(0.0, ge=-10, le=10, description="Calibration offset added to temperature")
    rh_offset_pct: float = Field(0.0, ge=-15, le=15, description="Calibration offset added to RH")
    enabled: bool = True


class Fan(BaseModel):
    id: str
    name: str
    type: FanType = FanType.EXHAUST
    rated_cfm: float = Field(100, gt=0, le=5000)
    actual_cfm: float = Field(80, gt=0, le=5000, description="Estimated installed airflow")
    rated_w: float = Field(30, ge=0, le=3000)
    motor_load_ok: bool = Field(True, description="Smart switch is rated for this motor load")
    switch_id: str = ""
    group: str = "main"
    enabled: bool = True


class FanGroup(BaseModel):
    id: str
    name: str
    zone: str | None = Field(None, description="Basement zone this group serves; None = aggregate of all zones")


class ScheduleWindow(BaseModel):
    start_hour: float = Field(..., ge=0, lt=24)
    end_hour: float = Field(..., ge=0, le=24)

    def contains(self, hour: float) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour  # wraps midnight


class ControlSettings(BaseModel):
    target_rh_pct: float = Field(55, ge=30, le=80)
    upper_rh_pct: float = Field(60, ge=30, le=90)
    high_rh_alert_pct: float = Field(70, ge=40, le=100)
    rh_start_deadband_pct: float = Field(2, ge=0, le=10, description="Start only when RH exceeds target by this much")
    dewpoint_on_advantage_f: float = Field(3, ge=0, le=20)
    dewpoint_off_advantage_f: float = Field(1, ge=0, le=20)
    min_removal_g_per_h: float = Field(50, ge=0, description="Minimum estimated net vapor removal to run (Balanced)")
    min_on_minutes: float = Field(15, ge=0, le=240)
    min_off_minutes: float = Field(10, ge=0, le=240)
    surface_margin_f: float = Field(3, ge=0, le=20)
    surface_temp_estimate_f: float | None = Field(
        None, description="Conservative minimum surface temperature if no surface sensor (user-approved)"
    )
    energy_profile: EnergyProfile = EnergyProfile.BALANCED
    min_efficiency_g_per_kwh_balanced: float = Field(1500, ge=0)
    min_efficiency_g_per_kwh_saver: float = Field(5000, ge=0)
    efficiency_off_factor: float = Field(0.7, gt=0, le=1, description="Hysteresis: stop below this fraction of the start threshold")
    max_depressurization_pa: float | None = Field(
        None, description="Safety stop if basement is more negative than this vs upstairs (e.g. 5 Pa)"
    )
    max_cycles_per_hour_alert: int = Field(4, ge=1)
    manual_override_minutes: float = Field(60, gt=0, le=24 * 60)
    schedule: list[ScheduleWindow] = Field(default_factory=lambda: [ScheduleWindow(start_hour=20, end_hour=8)])
    water_intrusion_lockout: bool = Field(False, description="Interlock: block ventilation (set while water is present)")
    combustion_interlock: bool = Field(
        False, description="Interlock: block exhaust-only ventilation (naturally vented gas appliance present)"
    )
    zone_aggregation: Literal["mean", "wettest"] = "wettest"

    @model_validator(mode="after")
    def _check(self) -> "ControlSettings":
        if self.dewpoint_off_advantage_f > self.dewpoint_on_advantage_f:
            raise ValueError("fan-off dew-point advantage must not exceed fan-on advantage")
        if self.upper_rh_pct < self.target_rh_pct:
            raise ValueError("upper RH threshold must be >= target RH")
        return self


class HouseSettings(BaseModel):
    conditioned_air_fraction: float = Field(
        0.10, ge=0, le=1, description="Assumed fraction of unbalanced exhaust airflow drawn from upstairs"
    )
    upstairs_temp_f: float = Field(74, description="Assumed upstairs conditions when no upstairs sensor")
    upstairs_rh_pct: float = Field(50, ge=1, le=100)
    cooling_cop: float = Field(3.0, gt=0.5, le=10)
    heating_cop: float = Field(1.0, gt=0.5, le=6, description="1.0 = resistance / gas-equivalent; 3 = heat pump")
    electricity_cost_per_kwh: float = Field(0.13, ge=0)
    natural_ach: float = Field(0.3, ge=0, le=10, description="Natural air changes per hour (simulation)")


class Dehumidifier(BaseModel):
    enabled: bool = False
    name: str = "Dehumidifier"
    setpoint_rh_pct: float = Field(50, ge=30, le=80)
    rated_w: float = Field(500, ge=0)
    removal_pints_per_day: float = Field(35, ge=0, description="Estimated real removal at basement conditions")


class SensorSettings(BaseModel):
    provider: Literal["mock", "ecowitt", "homeassistant"] = "mock"
    ecowitt_host: str = ""
    poll_seconds: float = Field(60, ge=5, le=3600)
    stale_minutes: float = Field(5, ge=1, le=120)
    mappings: list[SensorMapping] = Field(default_factory=list)


class SwitchSettings(BaseModel):
    provider: Literal["mock", "shelly", "homeassistant"] = "mock"
    verify_after_command: bool = True
    startup_state: Literal["off"] = "off"
    hardware_max_on_minutes: float | None = Field(
        None, description="Configured on the plug itself (auto-off timer). Informational until Phase 2."
    )


class SystemSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(8000, ge=1, le=65535)
    timezone: str = "America/Chicago"
    history_minutes: float = Field(2, ge=1, le=30)
    retention_days: int = Field(730, ge=1)
    decision_seconds: float = Field(60, ge=5, le=3600)


class AppConfig(BaseModel):
    mode: Mode = Mode.AUTO
    control: ControlSettings = Field(default_factory=ControlSettings)
    house: HouseSettings = Field(default_factory=HouseSettings)
    fans: list[Fan] = Field(default_factory=list)
    groups: list[FanGroup] = Field(default_factory=lambda: [FanGroup(id="main", name="Basement")])
    dehumidifier: Dehumidifier = Field(default_factory=Dehumidifier)
    sensors: SensorSettings = Field(default_factory=SensorSettings)
    switches: SwitchSettings = Field(default_factory=SwitchSettings)
    system: SystemSettings = Field(default_factory=SystemSettings)

    @field_validator("fans")
    @classmethod
    def _unique_fans(cls, fans: list[Fan]) -> list[Fan]:
        ids = [f.id for f in fans]
        if len(ids) != len(set(ids)):
            raise ValueError("fan ids must be unique")
        return fans

    @model_validator(mode="after")
    def _groups_exist(self) -> "AppConfig":
        gids = {g.id for g in self.groups}
        for f in self.fans:
            if f.group not in gids:
                raise ValueError(f"fan {f.id} references unknown group {f.group}")
        return self


def example_config() -> AppConfig:
    """Default configuration for a fresh install in mock/simulation mode."""
    return AppConfig(
        fans=[
            Fan(id="exhaust_1", name="Exhaust fan (east wall)", type=FanType.EXHAUST, rated_cfm=110,
                actual_cfm=85, rated_w=28, switch_id="mock-plug-1"),
            Fan(id="exhaust_2", name="Exhaust fan (north wall)", type=FanType.EXHAUST, rated_cfm=110,
                actual_cfm=85, rated_w=28, switch_id="mock-plug-2"),
        ],
        control=ControlSettings(surface_temp_estimate_f=None),
        sensors=SensorSettings(
            mappings=[
                SensorMapping(sensor_id="mock-out", name="Outdoor (north side)", role=SensorRole.OUTDOOR),
                SensorMapping(sensor_id="mock-bsmt-1", name="Main basement", role=SensorRole.BASEMENT, zone="main"),
                SensorMapping(sensor_id="mock-bsmt-2", name="Basement room 2", role=SensorRole.BASEMENT, zone="room2"),
                SensorMapping(sensor_id="mock-slab", name="Concrete slab (NE corner)", role=SensorRole.SURFACE),
                SensorMapping(sensor_id="mock-up", name="Upstairs living room", role=SensorRole.UPSTAIRS),
            ]
        ),
    )
