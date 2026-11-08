"""Mock hardware backed by the physical basement model.

The mock world steps a BasementModel forward in (optionally accelerated)
time, so mock fans really change mock basement humidity. Faults can be
injected from the Diagnostics page to exercise the fail-safe logic.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..climate import ClimateProfile, get_profile
from ..config import AppConfig
from ..sensors import SensorReading
from ..simulation import BasementModel, SimParams
from ..units import f_to_c
from .base import HardwareError, SensorSource, SwitchController, SwitchStatus


class MockWorld:
    def __init__(self, config: AppConfig, start: datetime, profile: ClimateProfile | None = None,
                 seed: int = 7, tz: str = "America/Chicago"):
        self.config = config
        self.profile = profile or get_profile("nola_front")
        self.tz = ZoneInfo(tz)
        self.rng = random.Random(seed)
        params = SimParams(initial_temp_f=79, initial_rh_pct=72, moisture_source_pints_day=6,
                           surface_temp_f=75.5, conditioned_air_fraction=config.house.conditioned_air_fraction,
                           volume_ft3=config.house.basement_volume_ft3)
        self.model = BasementModel(params, self.profile, [], config.house)
        self.t = start
        # profile hour 0 = local midnight of the start day
        local = start.astimezone(self.tz)
        self.epoch = local.replace(hour=0, minute=0, second=0, microsecond=0)
        self.switch_on: dict[str, bool] = {}
        self.sensor_faults: set[str] = set()  # sensor ids that stop updating
        self.frozen: dict[str, SensorReading] = {}
        self.switch_faults: dict[str, str] = {}  # switch id -> "unreachable" | "stuck"
        self.gateway_down = False

    def profile_hour(self, t: datetime) -> float:
        return (t.astimezone(self.tz) - self.epoch).total_seconds() / 3600

    def advance(self, now: datetime) -> None:
        step = timedelta(seconds=60)
        while self.t + step <= now:
            on_fans = [f for f in self.config.fans if f.enabled and self.switch_on.get(f.switch_id)]
            self.model.step(self.profile_hour(self.t), 60, on_fans)
            self.t += step

    def _noise(self, s: float) -> float:
        return self.rng.gauss(0, s)

    def readings(self, now: datetime) -> list[SensorReading]:
        self.advance(now)
        m = self.model
        out = m.outdoor_at(self.profile_hour(now))
        a = m.air()
        up_t = f_to_c(self.config.house.upstairs_temp_f)
        vals = {
            "mock-out": (out.t_c, out.rh * 100),
            "mock-bsmt-1": (a.t_c, a.rh * 100),
            "mock-bsmt-2": (a.t_c - 0.4, min(100, a.rh * 100 + 3.5)),  # back room slightly cooler and damper
            "mock-slab": (m.surface_c + 0.3 * (a.t_c - m.mean_t_c), None),
            "mock-up": (up_t, self.config.house.upstairs_rh_pct),
        }
        res = []
        for sid, (t, rh) in vals.items():
            if sid in self.sensor_faults:
                if sid in self.frozen:
                    res.append(self.frozen[sid])
                continue
            r = SensorReading(sensor_id=sid, ts=now, temp_c=round(t + self._noise(0.05), 2),
                              rh_pct=None if rh is None else round(min(100, max(1, rh + self._noise(0.3))), 1),
                              pressure_kpa=101.3, battery_low=False, signal=4, source="mock")
            self.frozen[sid] = r
            res.append(r)
        return res


class MockSensors(SensorSource):
    def __init__(self, world: MockWorld, clock):
        self.world = world
        self.clock = clock

    async def read_all(self) -> list[SensorReading]:
        if self.world.gateway_down:
            raise HardwareError("mock gateway unreachable")
        return self.world.readings(self.clock.now())

    async def discover(self) -> list[dict]:
        return [{"id": s, "kind": "temp_rh"} for s in ("mock-out", "mock-bsmt-1", "mock-bsmt-2", "mock-up")] + [
            {"id": "mock-slab", "kind": "temp"}]


class MockSwitches(SwitchController):
    def __init__(self, world: MockWorld, clock):
        self.world = world
        self.clock = clock

    def _power(self, sid: str) -> float:
        fans = [f for f in self.world.config.fans if f.switch_id == sid]
        return sum(f.rated_w for f in fans) * (1 + self.world.rng.gauss(0, 0.02))

    async def get(self, switch_id: str) -> SwitchStatus:
        if self.world.switch_faults.get(switch_id) == "unreachable":
            return SwitchStatus(switch_id, None, False, error="mock plug unreachable", ts=self.clock.now())
        on = self.world.switch_on.get(switch_id, False)
        return SwitchStatus(switch_id, on, True, self._power(switch_id) if on else 0.0, self.clock.now())

    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        self.world.advance(self.clock.now())
        fault = self.world.switch_faults.get(switch_id)
        if fault == "unreachable":
            return SwitchStatus(switch_id, None, False, error="mock plug unreachable", ts=self.clock.now())
        if fault != "stuck":
            self.world.switch_on[switch_id] = on
        return await self.get(switch_id)

    def supports_hardware_auto_off(self) -> bool:
        return True
