"""Ecowitt GW1200 local integration.

Two ingestion paths, both producing the same channel ids:

1. **Polling** ``GET http://<gateway>/get_livedata_info`` (local web API on
   current GW1100/GW1200/GW2000 firmware).
2. **Push**: the gateway's *Weather Services > Customized* upload, protocol
   "Ecowitt", pointed at ``http://<this computer>:8000/data/report/``. The body
   is form-encoded (``tempf``, ``humidity``, ``temp1f``/``humidity1`` for WN31
   channel 1, ``tf_ch1`` for WN34 probes, ``batt1``...).

Firmware versions differ, so the parsers accept values with or without units,
deg C or deg F, and strings or numbers, and ignore fields they do not know.
**Before relying on a gateway**, capture its real output with
``python -m ventctl.tools.capture_ecowitt <ip>`` and add it as a test fixture
(see tests/fixtures/). The fixtures shipped here are representative samples,
not captures from Patrick's gateway.

Channel ids:
  ecowitt:outdoor      outdoor temp/humidity (WS sensor array / WN32)
  ecowitt:indoor       the gateway's own temp/humidity sensor
  ecowitt:ch1..ch8     WN31 / WN31S channels
  ecowitt:tf1..tf8     WN34 probes (good for surface temperature)
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from ..clock import Clock
from ..sensors import SensorReading
from ..units import f_to_c, hpa_to_kpa, inhg_to_kpa
from .base import HardwareError, SensorSource
from .http import base_url, get_json

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.search(str(v))
    if not m or str(v).strip() in ("--", "---", ""):
        return None
    return float(m.group())


def _temp_c(v, unit: str | None = None) -> float | None:
    x = _num(v)
    if x is None:
        return None
    u = (unit or "").upper() or ("F" if "F" in str(v).upper() else "C")
    return f_to_c(x) if "F" in u else x


def _pressure_kpa(v, unit: str | None = None) -> float | None:
    x = _num(v)
    if x is None:
        return None
    u = (unit or str(v)).lower()
    if "inhg" in u:
        return inhg_to_kpa(x)
    if "kpa" in u:
        return x
    return hpa_to_kpa(x)  # hPa / mbar default


def _battery_low(v) -> bool | None:
    """Ecowitt batteries: WN31 reports 0 = OK, 1 = low. Some sensors report a 0-5 level or volts."""
    x = _num(v)
    if x is None:
        return None
    if x in (0, 1):
        return x == 1
    if x <= 1.6:  # volts on a 1.5 V cell
        return x < 1.2
    return x <= 1  # 0-5 level scale


def parse_livedata(data: dict, ts: datetime) -> list[SensorReading]:
    """Parse a ``/get_livedata_info`` JSON response."""
    out: list[SensorReading] = []
    if not isinstance(data, dict):
        raise HardwareError("unexpected live-data format (not an object)")
    pressure = None
    for item in data.get("wh25") or []:
        pressure = _pressure_kpa(item.get("abs"), None)
        t = _temp_c(item.get("intemp"), item.get("unit"))
        rh = _num(item.get("inhumi"))
        if t is not None:
            out.append(SensorReading("ecowitt:indoor", ts, temp_c=t, rh_pct=rh, pressure_kpa=pressure, source="ecowitt"))

    common = {str(i.get("id", "")).lower(): i for i in data.get("common_list") or [] if isinstance(i, dict)}
    # 0x02 outdoor temperature, 0x07 outdoor humidity (Ecowitt gateway API ids)
    if "0x02" in common:
        t = _temp_c(common["0x02"].get("val"), common["0x02"].get("unit"))
        rh = _num(common.get("0x07", {}).get("val"))
        if t is not None:
            out.append(SensorReading("ecowitt:outdoor", ts, temp_c=t, rh_pct=rh, pressure_kpa=pressure,
                                     source="ecowitt"))

    for ch in data.get("ch_aisle") or []:
        n = str(ch.get("channel", "")).strip()
        t = _temp_c(ch.get("temp"), ch.get("unit"))
        if not n or t is None:
            continue
        out.append(SensorReading(f"ecowitt:ch{n}", ts, temp_c=t, rh_pct=_num(ch.get("humidity")),
                                 pressure_kpa=pressure, battery_low=_battery_low(ch.get("battery")), source="ecowitt"))

    for ch in data.get("ch_temp") or []:  # WN34
        n = str(ch.get("channel", "")).strip()
        t = _temp_c(ch.get("temp"), ch.get("unit"))
        if n and t is not None:
            out.append(SensorReading(f"ecowitt:tf{n}", ts, temp_c=t, battery_low=_battery_low(ch.get("battery")),
                                     source="ecowitt"))
    if not out:
        raise HardwareError("live data contained no temperature readings; capture the response and check the format")
    return out


def parse_push(form: dict[str, str], received: datetime) -> list[SensorReading]:
    """Parse an Ecowitt-protocol 'Customized' upload (form fields)."""
    f = {k.lower(): v for k, v in form.items()}
    ts = received
    if f.get("dateutc") and f["dateutc"].lower() != "now":
        try:
            ts = datetime.strptime(f["dateutc"].replace("+", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            ts = received
        # never trust a gateway clock that is far off; use receive time instead
        if abs((received - ts).total_seconds()) > 600:
            ts = received
    p = _pressure_kpa(f.get("baromabsin"), "inhg") if f.get("baromabsin") else None
    out: list[SensorReading] = []

    def add(sid, tkey, hkey=None, bkey=None):
        if f.get(tkey) not in (None, ""):
            out.append(SensorReading(sid, ts, temp_c=_temp_c(f[tkey], "F"),
                                     rh_pct=_num(f.get(hkey)) if hkey else None, pressure_kpa=p,
                                     battery_low=_battery_low(f.get(bkey)) if bkey else None, source="ecowitt"))

    add("ecowitt:outdoor", "tempf", "humidity", "wh65batt")
    add("ecowitt:indoor", "tempinf", "humidityin")
    for n in range(1, 9):
        add(f"ecowitt:ch{n}", f"temp{n}f", f"humidity{n}", f"batt{n}")
        add(f"ecowitt:tf{n}", f"tf_ch{n}", None, f"tf_batt{n}")
    return out


class EcowittSource(SensorSource):
    """Polls the gateway, and merges any readings pushed to /data/report/."""

    def __init__(self, host: str, clock: Clock, timeout: float = 4.0):
        self.host = host
        self.clock = clock
        self.timeout = timeout
        self.pushed: dict[str, SensorReading] = {}
        self.last_poll_error: str | None = None
        self.last_raw: dict | None = None

    def ingest_push(self, form: dict[str, str]) -> list[SensorReading]:
        readings = parse_push(form, self.clock.now())
        for r in readings:
            self.pushed[r.sensor_id] = r
        return readings

    async def read_all(self) -> list[SensorReading]:
        polled: list[SensorReading] = []
        if self.host:
            try:
                data = await get_json(f"{base_url(self.host)}/get_livedata_info", self.timeout)
                self.last_raw = data
                polled = parse_livedata(data, self.clock.now())
                self.last_poll_error = None
            except HardwareError as e:
                self.last_poll_error = str(e)
                if not self.pushed:
                    raise
        merged = {r.sensor_id: r for r in self.pushed.values()}
        for r in polled:
            old = merged.get(r.sensor_id)
            if old is None or r.ts >= old.ts:
                merged[r.sensor_id] = r
        if not merged:
            raise HardwareError("no Ecowitt data yet: set the gateway IP or configure its Customized upload")
        return list(merged.values())

    async def discover(self) -> list[dict]:
        readings = await self.read_all()
        return [{"id": r.sensor_id, "kind": "temp_rh" if r.rh_pct is not None else "temp",
                 "temp_c": r.temp_c, "rh_pct": r.rh_pct} for r in readings]
