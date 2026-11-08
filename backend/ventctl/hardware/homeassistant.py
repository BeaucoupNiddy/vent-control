"""Home Assistant REST adapter (optional alternative to direct Ecowitt/Shelly).

Sensors: GET /api/states/<entity_id>. Map each app sensor id to entities in
settings (temp, rh, optional pressure_pa). Temperatures in deg F are converted
using the entity's unit_of_measurement.
Switches: Fan.switch_id is a Home Assistant entity id (switch.* or light.*);
POST /api/services/<domain>/turn_on|turn_off. Home Assistant has no generic
hardware auto-off, so configure one in the device itself if needed.
"""

from __future__ import annotations

from datetime import datetime

from ..clock import Clock
from ..sensors import SensorReading
from ..units import f_to_c
from .base import HardwareError, SensorSource, SwitchController, SwitchStatus
from .http import base_url, get_json


class HomeAssistant:
    def __init__(self, url: str, token: str, timeout: float = 4.0):
        self.url = base_url(url) if url else ""
        self.headers = {"Authorization": f"Bearer {token}"}
        self.timeout = timeout

    async def state(self, entity: str) -> dict:
        if not self.url:
            raise HardwareError("Home Assistant URL is not configured")
        return await get_json(f"{self.url}/api/states/{entity}", self.timeout, headers=self.headers)

    async def call(self, domain: str, service: str, entity: str) -> None:
        await get_json(f"{self.url}/api/services/{domain}/{service}", self.timeout, headers=self.headers,
                       method="POST", body={"entity_id": entity})


def _float(s: dict) -> float | None:
    try:
        return float(s.get("state"))
    except (TypeError, ValueError):
        return None


class HASensors(SensorSource):
    def __init__(self, ha: HomeAssistant, entities: dict[str, dict[str, str]], clock: Clock):
        self.ha = ha
        self.entities = entities
        self.clock = clock

    async def read_all(self) -> list[SensorReading]:
        out = []
        errors = []
        for sid, ent in self.entities.items():
            try:
                t = rh = dp = None
                ts = None
                if ent.get("temp"):
                    s = await self.ha.state(ent["temp"])
                    t = _float(s)
                    if t is not None and "F" in str(s.get("attributes", {}).get("unit_of_measurement", "")):
                        t = f_to_c(t)
                    ts = s.get("last_reported") or s.get("last_updated")
                if ent.get("rh"):
                    rh = _float(await self.ha.state(ent["rh"]))
                if ent.get("pressure_pa"):
                    dp = _float(await self.ha.state(ent["pressure_pa"]))
                when = datetime.fromisoformat(ts) if ts else self.clock.now()
                out.append(SensorReading(sid, when, temp_c=t, rh_pct=rh, diff_pressure_pa=dp, source="homeassistant"))
            except HardwareError as e:
                errors.append(str(e))
        if not out and errors:
            raise HardwareError("; ".join(errors))
        return out

    async def discover(self) -> list[dict]:
        return [{"id": k, "kind": "homeassistant", **v} for k, v in self.entities.items()]


class HASwitches(SwitchController):
    def __init__(self, ha: HomeAssistant, clock: Clock):
        self.ha = ha
        self.clock = clock

    async def get(self, switch_id: str) -> SwitchStatus:
        now = self.clock.now()
        try:
            s = await self.ha.state(switch_id)
            state = s.get("state")
            if state not in ("on", "off"):
                return SwitchStatus(switch_id, None, False, error=f"state is '{state}'", ts=now)
            power = s.get("attributes", {}).get("current_power_w")
            return SwitchStatus(switch_id, state == "on", True, float(power) if power is not None else None, now)
        except HardwareError as e:
            return SwitchStatus(switch_id, None, False, error=str(e), ts=now)

    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        try:
            await self.ha.call(switch_id.split(".")[0], "turn_on" if on else "turn_off", switch_id)
        except HardwareError as e:
            return SwitchStatus(switch_id, None, False, error=str(e), ts=self.clock.now())
        return await self.get(switch_id)
