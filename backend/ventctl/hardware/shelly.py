"""Shelly local switching (Gen1 HTTP and Gen2+/Plus/Pro RPC).

Gen2+:  GET /rpc/Switch.GetStatus?id=N  -> {"output": bool, "apower": W, ...}
        GET /rpc/Switch.Set?id=N&on=true&toggle_after=S
Gen1:   GET /relay/N                    -> {"ison": bool, ...}
        GET /relay/N?turn=on&timer=S ; power from /meter/N or /status
Detect: GET /shelly  (Gen2+ reports "gen": 2 or higher)

Dead-man watchdog: every ON command carries a hardware timer (toggle_after /
timer). The plug turns itself OFF when it runs out unless the controller re-arms
it, so a crashed or powered-off controller cannot leave fans running for more
than ``hardware_max_on_minutes``. Verify on your plug model that the timer is
honored (the Diagnostics page has a test button).

Digest authentication (Gen2 with a password set) is not supported yet; leave
the plug's local authentication off on a trusted LAN, or use Home Assistant.
"""

from __future__ import annotations

from ..clock import Clock
from ..config import ShellyDevice
from .base import HardwareError, SwitchController, SwitchStatus
from .http import base_url, get_json


class ShellySwitches(SwitchController):
    def __init__(self, devices: list[ShellyDevice], clock: Clock, timeout: float = 4.0,
                 auto_off_minutes: float | None = None):
        self.devices = {d.switch_id: d for d in devices}
        self.clock = clock
        self.timeout = timeout
        self.auto_off_s = int(auto_off_minutes * 60) if auto_off_minutes else None
        self._gen: dict[str, int] = {}
        self._armed: dict = {}

    def supports_hardware_auto_off(self) -> bool:
        return self.auto_off_s is not None

    async def _generation(self, d: ShellyDevice) -> int:
        if d.generation != "auto":
            return int(d.generation)
        if d.switch_id not in self._gen:
            info = await get_json(f"{base_url(d.host)}/shelly", self.timeout, d.username, d.password)
            self._gen[d.switch_id] = int(info.get("gen", 1)) if isinstance(info, dict) else 1
        return self._gen[d.switch_id]

    def _device(self, switch_id: str) -> ShellyDevice:
        d = self.devices.get(switch_id)
        if d is None:
            raise HardwareError(f"no Shelly device configured for switch id '{switch_id}'")
        return d

    async def get(self, switch_id: str) -> SwitchStatus:
        now = self.clock.now()
        try:
            d = self._device(switch_id)
            gen = await self._generation(d)
            url = base_url(d.host)
            if gen >= 2:
                st = await get_json(f"{url}/rpc/Switch.GetStatus?id={d.channel}", self.timeout, d.username, d.password)
                return SwitchStatus(switch_id, bool(st["output"]), True, _f(st.get("apower")), now)
            st = await get_json(f"{url}/relay/{d.channel}", self.timeout, d.username, d.password)
            power = None
            try:
                m = await get_json(f"{url}/meter/{d.channel}", self.timeout, d.username, d.password)
                power = _f(m.get("power"))
            except HardwareError:
                pass
            return SwitchStatus(switch_id, bool(st["ison"]), True, power, now)
        except (HardwareError, KeyError, TypeError, ValueError) as e:
            return SwitchStatus(switch_id, None, False, error=str(e), ts=now)

    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        now = self.clock.now()
        try:
            d = self._device(switch_id)
            gen = await self._generation(d)
            url = base_url(d.host)
            timer = self.auto_off_s if on else None
            if gen >= 2:
                q = f"id={d.channel}&on={'true' if on else 'false'}" + (f"&toggle_after={timer}" if timer else "")
                await get_json(f"{url}/rpc/Switch.Set?{q}", self.timeout, d.username, d.password)
            else:
                q = f"turn={'on' if on else 'off'}" + (f"&timer={timer}" if timer else "")
                await get_json(f"{url}/relay/{d.channel}?{q}", self.timeout, d.username, d.password)
        except HardwareError as e:
            return SwitchStatus(switch_id, None, False, error=str(e), ts=now)
        if on and timer:
            self._armed[switch_id] = now
        return await self.get(switch_id)

    async def rearm(self, switch_id: str, now=None) -> None:
        """Re-send ON with a fresh hardware timer (every third of the timer) while the fan should keep running."""
        if not self.auto_off_s:
            return
        now = now or self.clock.now()
        last = self._armed.get(switch_id)
        if last is None or (now - last).total_seconds() >= self.auto_off_s / 3:
            await self.set(switch_id, True)

    async def device_info(self, host: str) -> dict:
        return await get_json(f"{base_url(host)}/shelly", self.timeout)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
