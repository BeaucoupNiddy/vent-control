"""Hardware abstraction layer.

The controller only talks to these two interfaces. Adapters for Ecowitt,
Shelly, Home Assistant, MQTT or Matter implement them. Phase 1 ships only the
mock adapters; real adapters are Phase 2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from ..sensors import SensorReading


class HardwareError(Exception):
    pass


class SensorSource(ABC):
    """Returns the latest reading for every sensor channel it knows about."""

    @abstractmethod
    async def read_all(self) -> list[SensorReading]:
        """Raise HardwareError if the gateway cannot be reached."""

    async def discover(self) -> list[dict]:
        """List available channels (id, kind, label) for the mapping screen."""
        return []


@dataclass
class SwitchStatus:
    switch_id: str
    on: bool | None  # None = unknown
    reachable: bool
    power_w: float | None = None
    ts: datetime | None = None
    error: str | None = None


class SwitchController(ABC):
    @abstractmethod
    async def get(self, switch_id: str) -> SwitchStatus:
        """Read the actual relay state. Must not raise for unreachable devices; set reachable=False."""

    @abstractmethod
    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        """Command the relay and return the state the device reports afterwards."""

    def supports_hardware_auto_off(self) -> bool:
        """True if the device can turn itself off after a timer (needed for controller-failure safety)."""
        return False
