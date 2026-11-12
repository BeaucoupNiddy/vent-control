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
    calibrated_at: str | None = Field(None, description="When offsets were last set from a side-by-side comparison")


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


class Zone(BaseModel):
    """An environmental zone: a part of the basement that can differ from the rest."""

    id: str
    name: str
    volume_ft3: float = Field(8000, gt=10, le=500000, description="Air volume of this zone (estimate)")
    on_ventilation_path: bool = Field(False, description="Exterior fan air passes through this zone")
    ventilation_exposure: float = Field(0.5, ge=0, le=1, description="Share of exterior-ventilation drying that "
                                                                     "reaches this zone (assumption until observed)")
    moisture_source_share: float = Field(0.33, ge=0, le=1, description="Share of the basement's moisture sources")
    notes: str = ""


class ZoneConnection(BaseModel):
    """An intended air path between two zones. Describes intent, not proven airflow."""

    id: str
    zone_a: str
    zone_b: str
    circulator_id: str | None = Field(None, description="Internal fan that serves this connection")
    direction: Literal["a_to_b", "b_to_a", "mixing"] = "a_to_b"
    natural_cfm: float = Field(15, ge=0, le=5000, description="Assumed exchange with the fan off")
    fan_exchange_cfm: float = Field(60, ge=0, le=5000, description="Assumed exchange with the fan on (a fan in a "
                                                                   "doorway moves far less between rooms than its rating)")
    unobstructed: bool = True
    placement: Literal["doorway", "corridor", "in_zone"] = "doorway"


class Circulator(BaseModel):
    """An internal circulation fan on its own smart plug (ON/OFF only)."""

    id: str
    name: str
    model: str = "Honeywell HT-900"
    switch_id: str = ""
    location: str = ""
    physical_speed: Literal["low", "medium", "high", "unknown"] = Field("unknown", description="Set on the fan's "
                                                                                           "own switch")
    rated_w: float = Field(30, ge=0, le=500, description="Power draw at the selected speed (estimate unless metered)")
    ventilation_support: bool = Field(False, description="May run to spread exterior-fan air into remote zones")
    auto_enabled: bool = Field(True, description="Allow automatic control once commissioned")
    commissioned: bool = Field(False, description="Fan checked: restarts after power loss, plug rated for the motor, "
                                                  "speed switch set, kept clear of water")
    enabled: bool = True


class CirculationSettings(BaseModel):
    """Internal air circulation. Automatic control starts in shadow mode."""

    mode: Literal["off", "shadow", "auto"] = Field("shadow", description="shadow: decide and log, never switch")
    min_on_minutes: float = Field(15, ge=0, le=240)
    min_off_minutes: float = Field(10, ge=0, le=240)
    observe_rh_pct: float = Field(55, ge=30, le=90)
    elevated_rh_pct: float = Field(60, ge=30, le=95)
    high_rh_pct: float = Field(65, ge=30, le=98)
    critical_rh_pct: float = Field(70, ge=30, le=100)
    elevated_persist_min: float = Field(30, ge=0, le=720)
    high_persist_min: float = Field(15, ge=0, le=720)
    critical_persist_min: float = Field(60, ge=0, le=1440)
    rh_hysteresis_pct: float = Field(2, ge=0, le=10)
    min_dw_start_g_kg: float = Field(0.6, ge=0, le=10, description="Humidity-ratio difference worth acting on")
    min_dw_stop_g_kg: float = Field(0.3, ge=0, le=10)
    min_rh_improvement_pct: float = Field(1.0, ge=0, le=20, description="Predicted RH drop in the target zone needed "
                                                                         "to call mixing beneficial")
    rh_uncertainty_pct: float = Field(3.0, ge=0, le=15, description="Air sensor RH accuracy before comparison")
    rh_uncertainty_calibrated_pct: float = Field(1.5, ge=0, le=15, description="RH accuracy after side-by-side "
                                                                              "comparison")
    temp_uncertainty_f: float = Field(0.5, ge=0, le=5)
    horizon_min: float = Field(30, ge=5, le=240)
    ventilation_support: bool = True
    run_with_exhaust: bool = Field(True, description="Run every circulation fan whenever the exhaust fans run in "
                                                      "Automatic, so damp corners and surfaces dry into the air being "
                                                      "exhausted. Condensation and safety checks still apply")
    post_ventilation_min: float = Field(15, ge=0, le=240, description="Keep supporting fans on after exterior "
                                                                      "ventilation stops")
    condensation_aware: bool = True
    periodic_mixing: bool = False
    mixing_on_min: float = Field(10, ge=1, le=240)
    mixing_off_min: float = Field(30, ge=1, le=1440)
    mixing_windows: list[ScheduleWindow] = Field(default_factory=list, description="Empty = any time")
    mixing_fans: list[str] = Field(default_factory=list, description="Empty = all circulators")
    energy_optimization: bool = Field(True, description="Skip circulation when every zone is below the observe "
                                                        "threshold and no surface is at risk")
    manual_override_minutes: float = Field(60, gt=0, le=24 * 60)

    @model_validator(mode="after")
    def _check(self) -> "CirculationSettings":
        if not (self.observe_rh_pct <= self.elevated_rh_pct <= self.high_rh_pct <= self.critical_rh_pct):
            raise ValueError("circulation RH thresholds must increase: observe <= elevated <= high <= critical")
        if self.min_dw_stop_g_kg > self.min_dw_start_g_kg:
            raise ValueError("circulation humidity-ratio stop threshold must not exceed the start threshold")
        return self


class SurfaceSettings(BaseModel):
    """Measured-surface (concrete probe) condensation monitoring."""

    monitor_margin_f: float = Field(5, ge=0, le=30, description="Below this margin: monitor (advisory)")
    elevated_margin_f: float = Field(3, ge=0, le=30, description="Below this margin: elevated risk (warning)")
    possible_margin_f: float = Field(0, ge=-10, le=10, description="At or below: condensation possible (high risk)")
    probe_uncertainty_f: float = Field(1.0, ge=0, le=5, description="Probe accuracy (Ecowitt WN30: up to ±2°F; lower "
                                                                    "after checking against a reference)")
    dew_point_uncertainty_f: float = Field(1.0, ge=0, le=5, description="Local air dew-point accuracy")
    hysteresis_f: float = Field(0.5, ge=0, le=5)
    advisory_sustain_min: float = Field(30, ge=0, le=720)
    surface_rh_elevated_pct: float = Field(80, ge=50, le=100, description="Estimated near-surface RH worth tracking")
    persistent_hours: float = Field(6, gt=0, le=240, description="Near-saturation for this long = persistent risk")
    air_follow_fraction: float | None = Field(None, ge=0, le=1, description="How much of an air-temperature change "
                                                                            "the surface follows within the horizon; "
                                                                            "blank = learned, else 0.5 (conservative)")


class ControlSettings(BaseModel):
    target_rh_pct: float = Field(55, ge=30, le=80)
    upper_rh_pct: float = Field(60, ge=30, le=90)
    high_rh_alert_pct: float = Field(70, ge=40, le=100)
    rh_start_deadband_pct: float = Field(2, ge=0, le=10, description="Start only when RH exceeds target by this much")
    dewpoint_on_advantage_f: float = Field(3, ge=0, le=20)
    dewpoint_off_advantage_f: float = Field(1, ge=0, le=20)
    dry_window_advantage_f: float = Field(
        8, ge=0, le=40, description="Outdoor dew point at least this much lower = a strong drying window")
    dry_window_stop_rh_pct: float = Field(
        50, ge=30, le=80, description="During a strong drying window a running fan keeps going down to this RH "
                                      "instead of stopping at the target, so the wood and concrete dry too")
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
    learn_conditioned_air_fraction: bool = Field(
        True, description="Estimate the upstairs share from fan starts (needs an upstairs T/RH sensor); the learned "
                          "value replaces the assumed one once its confidence is medium or better")
    heating_below_outdoor_f: float = Field(
        55, ge=-20, le=80, description="Outdoor temperature below which the heating is assumed to run. Air drawn "
                                       "from upstairs costs heating energy only below it")
    cooling_above_outdoor_f: float = Field(
        78, ge=50, le=110, description="Outdoor temperature above which the AC is assumed to run. Air drawn from "
                                       "upstairs costs cooling energy only above it")
    cooling_cop: float = Field(3.0, gt=0.5, le=10)
    heating_cop: float = Field(1.0, gt=0.5, le=6, description="1.0 = resistance / gas-equivalent; 3 = heat pump")
    electricity_cost_per_kwh: float = Field(0.13, ge=0)
    natural_ach: float = Field(0.3, ge=0, le=10, description="Natural air changes per hour (simulation)")
    basement_volume_ft3: float = Field(24000, gt=100, le=500000, description="Air volume of the basement")
    moisture_source_pints_day: float = Field(6, ge=0, le=500, description="Assumed internal moisture sources "
                                                                          "(damp slab, intrusion); used in predictions")
    moisture_buffer_kg: float = Field(400, ge=0, le=5000, description="Assumed water the materials (wood framing, "
                                      "subfloor, concrete, contents) exchange between 0% and 100% RH (0 = off). The "
                                      "air itself holds only ~10 kg; real buffers are usually far larger")
    moisture_buffer_tau_h: float = Field(36, gt=0.1, le=500)
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
    tapo_username: str = Field("", description="TP-Link account email. Leave empty for plugs never added in the Tapo app (e.g. set up via Matter); only used for the local handshake")
    tapo_password: str = Field("", description="TP-Link account password, only if the email is set (stored only in the local database)")
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
    zones: list[Zone] = Field(default_factory=list)
    connections: list[ZoneConnection] = Field(default_factory=list)
    circulators: list[Circulator] = Field(default_factory=list)
    circulation: CirculationSettings = Field(default_factory=CirculationSettings)
    surfaces: SurfaceSettings = Field(default_factory=SurfaceSettings)
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
        zids = [z.id for z in self.zones]
        if len(zids) != len(set(zids)):
            raise ValueError("zone ids must be unique")
        cids = [c.id for c in self.circulators]
        if len(cids) != len(set(cids)):
            raise ValueError("circulator ids must be unique")
        fan_switches = {f.switch_id for f in self.fans if f.switch_id}
        for c in self.circulators:
            if c.switch_id and c.switch_id in fan_switches:
                raise ValueError(f"circulator {c.id} shares smart plug {c.switch_id} with an exterior fan")
        for k in self.connections:
            if zids and (k.zone_a not in zids or k.zone_b not in zids):
                raise ValueError(f"connection {k.id} references an unknown zone")
            if k.zone_a == k.zone_b:
                raise ValueError(f"connection {k.id} must join two different zones")
            if k.circulator_id and k.circulator_id not in cids:
                raise ValueError(f"connection {k.id} references unknown circulator {k.circulator_id}")
        return self

    def zone(self, zid: str | None) -> "Zone | None":
        return next((z for z in self.zones if z.id == zid), None)

    def zone_name(self, zid: str | None) -> str:
        z = self.zone(zid)
        return z.name if z else (zid or "basement")


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
        zones=[
            Zone(id="main", name="Main basement", volume_ft3=16000, on_ventilation_path=True, ventilation_exposure=1.0,
                 moisture_source_share=0.5),
            Zone(id="pocket", name="Remote pocket", volume_ft3=4500, ventilation_exposure=0.4,
                 moisture_source_share=0.2),
            Zone(id="damp", name="Moisture-prone area", volume_ft3=3500, ventilation_exposure=0.25,
                 moisture_source_share=0.3),
        ],
        connections=[
            ZoneConnection(id="main-pocket", zone_a="main", zone_b="pocket", circulator_id="circ-1",
                           direction="a_to_b", placement="corridor"),
            ZoneConnection(id="pocket-damp", zone_a="pocket", zone_b="damp", circulator_id="circ-2",
                           direction="a_to_b", natural_cfm=10, placement="doorway"),
        ],
        circulators=[
            Circulator(id="circ-1", name="Circulation fan 1", switch_id="mock-plug-2", location="Main passage",
                       physical_speed="medium", ventilation_support=True),
            Circulator(id="circ-2", name="Circulation fan 2", switch_id="mock-plug-3", location="Moisture-prone area",
                       physical_speed="low"),
        ],
        sensors=SensorSettings(
            mappings=[
                SensorMapping(sensor_id="mock-out", name="Outdoor (north side)", role=SensorRole.OUTDOOR),
                SensorMapping(sensor_id="mock-bsmt-1", name="Main basement", role=SensorRole.BASEMENT, zone="main"),
                SensorMapping(sensor_id="mock-bsmt-2", name="Remote pocket", role=SensorRole.BASEMENT, zone="pocket"),
                SensorMapping(sensor_id="mock-bsmt-3", name="Moisture-prone area", role=SensorRole.BASEMENT,
                              zone="damp"),
                SensorMapping(sensor_id="mock-slab", name="Foundation wall — northwest", role=SensorRole.SURFACE,
                              zone="damp"),
                SensorMapping(sensor_id="mock-up", name="Upstairs living room", role=SensorRole.UPSTAIRS),
            ]
        ),
    )


# Defaults that changed after installs existed. A saved configuration that still holds the old
# default is moved to the new one once; a value the user changed is left alone.
DEFAULTS_VERSION = 1
_DEFAULT_UPGRADES: list[tuple[int, str, str, float, float]] = [
    # (version, section, field, old default, new default)
    (1, "house", "moisture_buffer_kg", 25.0, 400.0),  # physics review: materials hold far more water than 25 kg
    (1, "house", "moisture_buffer_tau_h", 12.0, 36.0),
]


def upgrade_defaults(config: AppConfig, from_version: int) -> list[str]:
    """Apply default upgrades newer than from_version in place; returns a description of each change."""
    changes = []
    for v, section, name, old, new in _DEFAULT_UPGRADES:
        if v <= from_version:
            continue
        obj = getattr(config, section)
        if getattr(obj, name) == old:
            setattr(obj, name, new)
            changes.append(f"{section}.{name}: {old:g} -> {new:g}")
    return changes
