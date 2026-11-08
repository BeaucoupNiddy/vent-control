"""Placeholders for Phase 2 adapters (not wired up yet).

Notes for the Phase 2 implementation, to verify against the real devices:

Ecowitt GW1200
  * The gateway serves local HTTP endpoints (e.g. ``/get_livedata_info``) on
    firmware that supports them, and can also push data with the
    "Customized" upload (Ecowitt or Wunderground protocol) to a local URL.
    Do not assume the JSON schema: capture a real response from the user's
    gateway first and write the parser and tests from that capture.
  * WN31 channels appear as ch1..ch8 temperature/humidity; values may be
    strings with units. Map channels to locations in settings.

Shelly (Gen2+ RPC)
  * ``GET http://<ip>/rpc/Switch.GetStatus?id=0`` returns output state and
    power; ``Switch.Set?id=0&on=true`` sets it. Gen1 uses ``/relay/0``.
  * For controller-failure safety configure the plug's own auto-off timer
    (``toggle_after`` / ``auto_off``) so a stuck-ON fan stops even if this
    app dies. A software-only safeguard cannot do that.
"""

from __future__ import annotations

from .base import SensorSource, SwitchController, SwitchStatus


class EcowittLocalSource(SensorSource):
    def __init__(self, host: str):
        self.host = host

    async def read_all(self):
        raise NotImplementedError("Ecowitt integration is Phase 2")


class ShellySwitches(SwitchController):
    def __init__(self, hosts: dict[str, str]):
        self.hosts = hosts

    async def get(self, switch_id: str) -> SwitchStatus:
        raise NotImplementedError("Shelly integration is Phase 2")

    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        raise NotImplementedError("Shelly integration is Phase 2")

    def supports_hardware_auto_off(self) -> bool:
        return True
