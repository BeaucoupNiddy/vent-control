"""TP-Link Tapo smart plugs over the local network (via python-kasa).

Works with Tapo plugs that python-kasa supports, including the Matter-enabled
minis (P125M, P110M, P100M). Control is local (TP-Link's KLAP protocol) and
needs no cloud.

No TP-Link account needed
  A plug that was never added in the Tapo app (for example one set up only
  through Matter in Apple Home) is not linked to any account, and it accepts
  the local handshake with no login. Leave the email and password empty.
  Only plugs that were added in the Tapo app need that account's email and
  password, which are used solely to derive the local handshake key.

Hardware watchdog
  Tapo plugs have a built-in **auto-off** timer ("turn off N minutes after it is
  turned on"). It lives on the plug, so it still works if this computer dies.
  On connect the adapter enables it with N = ``hardware_max_on_minutes``.
  Unlike Shelly's ``toggle_after``, re-sending ON does not restart the countdown,
  so while a fan should keep running the adapter briefly switches the plug
  OFF then ON when less than half the timer remains (a ~1 s blip every
  N/2 minutes). Note that the auto-off also applies when you turn the plug on
  from the Tapo app.

Limits
  * Power readings exist only on energy-monitoring models (P110/P110M/P115).
    The P125M mini reports none, so "unexpected power" alerts are unavailable.
  * TP-Link firmware updates have broken local access before: newer P125M
    firmware switches to TP-Link's "TPAP" security, which python-kasa cannot
    speak yet. So on connect the adapter turns OFF the plug's automatic
    firmware updates. A plug that already moved to TPAP is reported clearly;
    replace it with one on older firmware, or control it through Matter in
    Home Assistant (switch provider "homeassistant").

Connecting
  The first contact uses UDP discovery to learn the plug's protocol. After
  that the adapter reconnects straight to the plug over HTTP with the learned
  settings, because discovery broadcasts are sometimes lost on Wi-Fi. If
  discovery itself gets no answer, it tries the usual Tapo plug settings
  (KLAP, login v2) directly.
  * Check the plug's rated load against the fan motor.
"""

from __future__ import annotations

from datetime import datetime

from ..clock import Clock
from ..config import TapoDevice
from .base import SwitchController, SwitchStatus

try:  # optional dependency; only needed when the Tapo provider is selected
    from kasa import (AuthenticationError, Credentials, Device, DeviceConfig, Discover, KasaException, Module,
                      UnsupportedDeviceError)
    from kasa.deviceconfig import DeviceConnectionParameters, DeviceEncryptionType, DeviceFamily
except ImportError:  # pragma: no cover
    Credentials = Device = DeviceConfig = Discover = Module = None  # type: ignore[assignment]
    KasaException = AuthenticationError = UnsupportedDeviceError = Exception  # type: ignore[assignment,misc]

TPAP_HELP = ("This plug's firmware switched to TP-Link's newer TPAP security, which this app can't talk to yet. "
             "Swap in a spare plug (the app keeps its firmware from updating), or control it through Home Assistant.")


def _friendly(e: BaseException) -> str:
    text = str(e) or type(e).__name__
    return TPAP_HELP if "TPAP" in text else text


async def discover_plugs(username: str = "", password: str = "", timeout: float = 5.0) -> list[dict]:
    """Find Tapo/Kasa plugs on the local network. Read-only: nothing is switched."""
    if Discover is None:
        raise KasaException("python-kasa is not installed (pip install python-kasa)")
    creds = Credentials(username, password) if username else None
    unsupported: list[dict] = []

    async def on_unsupported(e) -> None:
        info = getattr(e, "discovery_result", None) or {}
        unsupported.append({"host": getattr(e, "host", None) or info.get("ip"), "model": info.get("device_model"),
                            "mac": (info.get("mac") or "").replace("-", ":") or None, "name": None, "on": None,
                            "error": _friendly(e)})

    found = await Discover.discover(credentials=creds, discovery_timeout=max(int(timeout), 3),
                                    on_unsupported=on_unsupported)
    out = [u for u in unsupported if u["host"] and u["host"] not in found]
    for host, dev in sorted(found.items()):
        row = {"host": host, "model": None, "mac": None, "name": None, "on": None, "error": None}
        try:
            await dev.update()
            row.update(model=dev.model, mac=dev.mac, name=dev.alias or None, on=bool(dev.is_on))
        except AuthenticationError:
            row["error"] = "This plug is linked to a TP-Link account: enter that email and password, or reset the plug"
        except UnsupportedDeviceError as e:
            row["error"] = _friendly(e)
        except (KasaException, OSError, TimeoutError) as e:
            row["error"] = str(e) or type(e).__name__
        finally:
            try:
                await dev.disconnect()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        out.append(row)
    return sorted(out, key=lambda r: r["host"])


class TapoSwitches(SwitchController):
    def __init__(self, devices: list[TapoDevice], clock: Clock, username: str, password: str,
                 timeout: float = 5.0, auto_off_minutes: float | None = None, connector=None):
        self.devices = {d.switch_id: d for d in devices}
        self.clock = clock
        self.username, self.password = username, password
        self.timeout = timeout
        self.auto_off_min = int(round(auto_off_minutes)) if auto_off_minutes else None
        self._conn: dict[str, object] = {}
        self._connector = connector or self._connect
        self.watchdog: dict[str, str] = {}  # switch id -> "active" | "unsupported" | error text
        self.firmware_updates: dict[str, str] = {}  # switch id -> "off" | "unsupported" | error text
        self._known: dict[str, object] = {}  # host -> DeviceConfig learned from a successful connect

    def supports_hardware_auto_off(self) -> bool:
        # unknown until each plug has been contacted; any plug without a working timer makes it False
        return self.auto_off_min is not None and all(v == "active" for v in self.watchdog.values())

    async def _connect(self, host: str):
        if Discover is None:
            raise KasaException("python-kasa is not installed (pip install python-kasa)")
        creds = Credentials(self.username, self.password) if self.username else None
        known = self._known.get(host)
        if known is not None:
            try:
                return await Device.connect(config=known)
            except (KasaException, OSError, TimeoutError):
                self._known.pop(host, None)  # fall back to discovery below
        try:
            dev = await Discover.discover_single(host, credentials=creds, timeout=int(self.timeout))
        except TimeoutError:
            # discovery broadcasts get lost on Wi-Fi; try the usual Tapo plug settings directly
            cfg = DeviceConfig(host=host, credentials=creds, timeout=int(self.timeout),
                               connection_type=DeviceConnectionParameters(
                                   DeviceFamily.SmartTapoPlug, DeviceEncryptionType.Klap, login_version=2))
            dev = await Device.connect(config=cfg)
        self._known[host] = dev.config
        return dev

    async def _device(self, switch_id: str):
        if switch_id not in self.devices:
            raise KasaException(f"no Tapo plug configured for switch id '{switch_id}'")
        dev = self._conn.get(switch_id)
        if dev is None:
            dev = await self._connector(self.devices[switch_id].host)
            await dev.update()
            self._conn[switch_id] = dev
            await self._setup_watchdog(switch_id, dev)
            await self._block_firmware_updates(switch_id, dev)
        return dev

    async def _block_firmware_updates(self, switch_id: str, dev) -> None:
        """Turn off automatic firmware updates: a TP-Link update can end local control."""
        mod = dev.modules.get(Module.Firmware) if Module else None
        if mod is None or not hasattr(mod, "set_auto_update_enabled"):
            self.firmware_updates[switch_id] = "unsupported"
            return
        try:
            if mod.auto_update_enabled:
                await mod.set_auto_update_enabled(False)
            self.firmware_updates[switch_id] = "off"
        except (KasaException, OSError, TimeoutError, KeyError) as e:
            self.firmware_updates[switch_id] = f"could not turn off auto-update: {e}"

    async def _setup_watchdog(self, switch_id: str, dev) -> None:
        if not self.auto_off_min:
            return
        mod = dev.modules.get(Module.AutoOff) if Module else None
        if mod is None:
            self.watchdog[switch_id] = "unsupported"
            return
        try:
            # enable first and refresh: set_delay re-sends the cached "enable" flag and
            # set_enabled re-sends the cached delay, so doing both from stale data undoes one
            if not mod.enabled:
                await mod.set_enabled(True)
                await dev.update()
            if mod.delay != self.auto_off_min:
                await mod.set_delay(self.auto_off_min)
                await dev.update()
            self.watchdog[switch_id] = "active"
        except KasaException as e:
            self.watchdog[switch_id] = f"could not enable auto-off: {e}"

    def _status(self, switch_id: str, dev) -> SwitchStatus:
        power = None
        energy = dev.modules.get(Module.Energy) if Module else None
        if energy is not None:
            try:
                power = float(energy.current_consumption) if energy.current_consumption is not None else None
            except (TypeError, ValueError):
                power = None
        return SwitchStatus(switch_id, bool(dev.is_on), True, power, self.clock.now())

    async def get(self, switch_id: str) -> SwitchStatus:
        try:
            dev = await self._device(switch_id)
            await dev.update()
            return self._status(switch_id, dev)
        except (KasaException, OSError, TimeoutError) as e:
            self._conn.pop(switch_id, None)  # reconnect next time
            return SwitchStatus(switch_id, None, False, error=_friendly(e), ts=self.clock.now())

    async def set(self, switch_id: str, on: bool) -> SwitchStatus:
        try:
            dev = await self._device(switch_id)
            await (dev.turn_on() if on else dev.turn_off())
        except (KasaException, OSError, TimeoutError) as e:
            self._conn.pop(switch_id, None)
            return SwitchStatus(switch_id, None, False, error=_friendly(e), ts=self.clock.now())
        return await self.get(switch_id)

    async def rearm(self, switch_id: str, now: datetime | None = None) -> None:
        """Restart the plug's auto-off countdown when less than half of it remains."""
        if self.watchdog.get(switch_id) != "active":
            return
        try:
            dev = await self._device(switch_id)
            mod = dev.modules.get(Module.AutoOff)
            at = mod.auto_off_at if mod is not None else None
            if at is None or not dev.is_on:
                return
            remaining = (at - dev.time).total_seconds()
            if remaining < self.auto_off_min * 60 / 2:
                await dev.turn_off()
                await dev.turn_on()
                await dev.update()
        except (KasaException, OSError, TimeoutError):
            self._conn.pop(switch_id, None)
