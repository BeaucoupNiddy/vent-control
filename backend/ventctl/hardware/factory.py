"""Build sensor and switch adapters from configuration."""

from __future__ import annotations

from datetime import datetime

from ..clock import Clock
from ..config import AppConfig
from .base import SensorSource, SwitchController
from .ecowitt import EcowittSource
from .homeassistant import HASensors, HASwitches, HomeAssistant
from .mock import MockSensors, MockSwitches, MockWorld
from .shelly import ShellySwitches


def needs_mock_world(cfg: AppConfig) -> bool:
    return cfg.sensors.provider == "mock" or cfg.switches.provider == "mock"


def build(cfg: AppConfig, clock: Clock, world: MockWorld | None, start: datetime) -> tuple[SensorSource, SwitchController, MockWorld | None]:
    if needs_mock_world(cfg) and world is None:
        world = MockWorld(cfg, start, tz=cfg.system.timezone)
    ha = HomeAssistant(cfg.homeassistant.url, cfg.homeassistant.token, cfg.switches.timeout_s)

    if cfg.sensors.provider == "ecowitt":
        sensors: SensorSource = EcowittSource(cfg.sensors.ecowitt_host, clock, cfg.switches.timeout_s)
    elif cfg.sensors.provider == "homeassistant":
        sensors = HASensors(ha, cfg.homeassistant.sensor_entities, clock)
    else:
        sensors = MockSensors(world, clock)  # type: ignore[arg-type]

    if cfg.switches.provider == "shelly":
        switches: SwitchController = ShellySwitches(cfg.switches.shelly_devices, clock, cfg.switches.timeout_s,
                                                    cfg.switches.hardware_max_on_minutes)
    elif cfg.switches.provider == "homeassistant":
        switches = HASwitches(ha, clock)
    else:
        switches = MockSwitches(world, clock)  # type: ignore[arg-type]
    return sensors, switches, world


def data_source(cfg: AppConfig) -> str:
    return "mock" if cfg.sensors.provider == "mock" else "measured"
