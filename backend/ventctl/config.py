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
    TEMPERATURE_PRESERVATION = "temperature_preservation"
    ENERGY_SAVER = "energy_saver"


class ControlPolicy(str, Enum):
    MOISTURE = "moisture"  # original moisture-only logic (kept for comparison and rollback)
    TAMO = "tamo"  # temperature-aware moisture optimization


class FanType(str, Enum):
    INTAKE = "intake"
    EXHAUST = "exhaust"


class SensorRole(str, Enum):
    OUTDOOR = "outdoor"
    BASEMENT = "basement"  # an indoor basement zone
    SURFACE = "surface"  # concrete / wood / masonry surface temperature (condensation checks)
    PIPE = "pipe"  # pipe or vulnerable spot near a vent (freeze protection)
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
    policy: ControlPolicy = Field(
        ControlPolicy.MOISTURE,
        description="Which decision logic commands the fans. The other one is evaluated in shadow mode.")
    shadow_evaluation: bool = Field(True, description="Evaluate the policy that is not in control, without switching")

    @model_validator(mode="after")
    def _check(self) -> "ControlSettings":
        if self.dewpoint_off_advantage_f > self.dewpoint_on_advantage_f:
            raise ValueError("fan-off dew-point advantage must not exceed fan-on advantage")
        if self.upper_rh_pct < self.target_rh_pct:
            raise ValueError("upper RH threshold must be >= target RH")
        return self


class TemperatureSettings(BaseModel):
    """Temperature-aware moisture optimization (TAMO). All defaults are starting suggestions."""

    preferred_min_f: float = Field(65, ge=30, le=90)
    preferred_max_f: float = Field(78, ge=35, le=100)
    soft_tolerance_f: float = Field(5, gt=0, le=30, description="Penalty grows with distance outside the preferred band "
                                                                "relative to this")
    min_allowed_f: float = Field(55, ge=35, le=80, description="Hard limit: stop cold-air ventilation before the "
                                                               "basement air reaches this. Not a freeze guarantee.")
    freeze_warning_f: float = Field(40, ge=33, le=60, description="Hard limit for pipe/vulnerable-spot sensors")
    sensor_margin_f: float = Field(0.5, ge=0, le=5, description="Sensor accuracy allowance on hard limits")
    sensitivity: Literal["low", "medium", "high", "custom"] = "medium"
    custom_sensitivity_g_h: float = Field(3000, ge=0, description="Penalty weight k when sensitivity is custom")
    penalty_exponent: float = Field(2.0, ge=1.0, le=3.0)
    change_tolerance_f: float = Field(1.0, ge=0, le=10, description="Predicted swing inside the band that costs nothing")
    swing_weight: float = Field(0.5, ge=0, le=5, description="Weight of swings that stay inside the preferred band")
    horizon_min: float = Field(30, ge=5, le=240, description="Decision horizon for thermal predictions")
    urgency_enabled: bool = True
    urgency_low_rh_pct: float = Field(50, ge=20, le=90, description="Urgency 0 at or below this RH")
    urgency_high_rh_pct: float = Field(70, ge=25, le=100, description="Urgency 1 at or above this RH")
    urgency_relaxation: float = Field(0.8, ge=0, le=1, description="Fraction of the thermal weight relaxed at urgency 1")
    min_dw_start_g_kg: float = Field(0.6, ge=0, le=10, description="Humidity-ratio advantage needed to start "
                                                                  "(basement minus inlet air)")
    min_dw_stop_g_kg: float = Field(0.25, ge=0, le=10, description="Fans stop below this humidity-ratio advantage")
    utility_start_g_h: float = Field(150, ge=0, description="Net utility needed to start the fans")
    utility_stop_g_h: float = Field(0, description="Fans stop when net utility falls below this")
    switch_cost_g_h: float = Field(25, ge=0, description="Small penalty for changing fan state")
    risk_weight_g_h: float = Field(200, ge=0, description="Soft penalty per °F² inside the condensation caution band")
    condensation_caution_f: float = Field(5, ge=0, le=20)
    condensation_concern_f: float = Field(2, ge=0, le=20)
    learning_enabled: bool = True
    learning_confidence: Literal["conservative", "standard"] = Field(
        "conservative", description="conservative: use a learned thermal model only at high confidence")
    # thermal model assumptions (used until learning replaces them)
    air_structure_coupling_w_k: float | None = Field(None, gt=0, description="Heat exchange between air and structure; "
                                                                           "blank = estimate from volume")
    structure_heat_capacity_mj_k: float | None = Field(None, gt=0, description="Blank = estimate from volume")
    structure_tau_h: float = Field(12, gt=0.5, le=240)
    forecast_enabled: bool = Field(True, description="Uses a forecast only if one can be fetched; never required")
    forecast_latitude: float = Field(29.95, ge=-90, le=90)
    forecast_longitude: float = Field(-90.07, ge=-180, le=180)
    forecast_horizon_h: float = Field(4, ge=1, le=12)
    max_deferral_min: float = Field(120, ge=0, le=720)
    forecast_max_urgency: float = Field(0.6, ge=0, le=1, description="Never defer above this urgency")

    @model_validator(mode="after")
    def _check(self) -> "TemperatureSettings":
        if self.preferred_max_f <= self.preferred_min_f:
            raise ValueError("preferred maximum temperature must be above the minimum")
        if self.min_allowed_f >= self.preferred_min_f:
            raise ValueError("minimum allowed temperature must be below the preferred minimum")
        if self.urgency_high_rh_pct <= self.urgency_low_rh_pct:
            raise ValueError("urgency high RH must be above urgency low RH")
        if self.utility_stop_g_h > self.utility_start_g_h:
            raise ValueError("utility stop threshold must not exceed the start threshold")
        if self.min_dw_stop_g_kg > self.min_dw_start_g_kg:
            raise ValueError("humidity-ratio stop threshold must not exceed the start threshold")
        return self

    @property
    def k_g_h(self) -> float:
        return {"low": 1000.0, "medium": 3000.0, "high": 8000.0}.get(self.sensitivity, self.custom_sensitivity_g_h)


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
    basement_volume_ft3: float = Field(24000, gt=100, le=500000, description="Air volume of the basement")
    moisture_source_pints_day: float = Field(6, ge=0, le=500, description="Assumed internal moisture sources "
                                                                          "(damp slab, intrusion); used in predictions")
    moisture_buffer_kg: float = Field(25, ge=0, le=5000, description="Assumed water the materials exchange between "
                                                                    "0% and 100% RH (0 = off)")
    moisture_buffer_tau_h: float = Field(12, gt=0.1, le=500)
    elevation_ft: float = Field(0, ge=-500, le=15000, description="Sets standard pressure when no barometer is read")
    site_pressure_kpa: float | None = Field(None, ge=50, le=110, description="Fixed pressure override (blank = from "
                                                                            "barometer or elevation)")

    def default_pressure_kpa(self) -> float:
        if self.site_pressure_kpa:
            return self.site_pressure_kpa
        z = self.elevation_ft * 0.3048
        return 101.325 * (1 - 2.25577e-5 * z) ** 5.2559  # ASHRAE Fundamentals eq. 3


class Dehumidifier(BaseModel):
    enabled: bool = False
    name: str = "Dehumidifier"
    setpoint_rh_pct: float = Field(50, ge=30, le=80)
    rated_w: float = Field(500, ge=0)
    removal_pints_per_day: float = Field(35, ge=0, description="Estimated real removal at basement conditions")
    monitor_switch_id: str = Field("", description="Power-metering plug feeding the dehumidifier (monitored, never switched)")
    running_threshold_w: float = Field(60, ge=0, description="Above this power draw the compressor is considered running")
    require_ventilation_beats_dehumidifier: bool = Field(
        False, description="Energy Saver: run fans only when ventilation is cheaper per gallon than the dehumidifier")


class HomeAssistantSettings(BaseModel):
    url: str = Field("", description="e.g. http://homeassistant.local:8123")
    token: str = Field("", description="Long-lived access token (stored only in the local database)")
    sensor_entities: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="sensor_id -> {temp: entity_id, rh: entity_id, pressure_pa: entity_id}",
    )


class SensorSettings(BaseModel):
    provider: Literal["mock", "ecowitt", "homeassistant"] = "mock"
    ecowitt_host: str = Field("", description="Gateway IP or host[:port] for local polling")
    ecowitt_push: bool = Field(True, description="Also accept the gateway's 'Customized' upload (Ecowitt protocol)")
    poll_seconds: float = Field(60, ge=5, le=3600)
    stale_minutes: float = Field(5, ge=1, le=120)
    mappings: list[SensorMapping] = Field(default_factory=list)


class ShellyDevice(BaseModel):
    switch_id: str = Field(..., description="Id used by fans (Fan.switch_id)")
    host: str = Field(..., description="Plug IP or hostname")
    channel: int = Field(0, ge=0, le=3)
    generation: Literal["auto", "1", "2"] = "auto"
    username: str = ""
    password: str = ""


class TapoDevice(BaseModel):
    switch_id: str = Field(..., description="Id used by fans (Fan.switch_id)")
    host: str = Field(..., description="Plug IP address (give it a DHCP reservation)")


class SwitchSettings(BaseModel):
    provider: Literal["mock", "tapo", "shelly", "homeassistant"] = "mock"
    verify_after_command: bool = True
    startup_state: Literal["off"] = "off"
    hardware_max_on_minutes: float | None = Field(
        30, ge=2, le=24 * 60,
        description="Dead-man timer: every ON command arms the plug's own auto-off. The controller re-arms it while "
                    "the fans should stay on, so if this computer stops the plug turns itself off.")
    shelly_devices: list[ShellyDevice] = Field(default_factory=list)
    tapo_devices: list[TapoDevice] = Field(default_factory=list)
    tapo_username: str = Field("", description="TP-Link account email (used only for the local handshake)")
    tapo_password: str = Field("", description="TP-Link account password (stored only in the local database)")
    timeout_s: float = Field(4, ge=0.5, le=30)


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
    temperature: TemperatureSettings = Field(default_factory=TemperatureSettings)
    house: HouseSettings = Field(default_factory=HouseSettings)
    fans: list[Fan] = Field(default_factory=list)
    groups: list[FanGroup] = Field(default_factory=lambda: [FanGroup(id="main", name="Basement")])
    dehumidifier: Dehumidifier = Field(default_factory=Dehumidifier)
    sensors: SensorSettings = Field(default_factory=SensorSettings)
    homeassistant: HomeAssistantSettings = Field(default_factory=HomeAssistantSettings)
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
            # Tjernlund V2D UnderAire: two 110 CFM fans in one housing on one cord, about 40 W together.
            # It mounts in a foundation vent with no ducting, so installed airflow is taken as the rating.
            Fan(id="v2d", name="Tjernlund V2D UnderAire (2 × 110 CFM)", type=FanType.EXHAUST, rated_cfm=220,
                actual_cfm=220, rated_w=40, switch_id="mock-plug-1"),
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
