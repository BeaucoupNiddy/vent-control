"""Phase 2 adapters against local fake devices (no real hardware needed)."""

import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ventctl.clock import ManualClock
from ventctl.config import Fan, Mode, SensorMapping, SensorRole, ShellyDevice, example_config
from ventctl.controller import Controller
from ventctl.hardware.ecowitt import EcowittSource, parse_livedata, parse_push
from ventctl.hardware.homeassistant import HASensors, HASwitches, HomeAssistant
from ventctl.hardware.shelly import ShellySwitches
from ventctl.storage import Storage

from .helpers import T0

FIX = Path(__file__).parent / "fixtures"


class FakeDevices:
    """One HTTP server pretending to be a Shelly Gen2 plug, a Gen1 plug, an Ecowitt gateway and Home Assistant."""

    def __init__(self):
        self.gen2 = {"output": False, "apower": 0.0, "toggle_after": None}
        self.gen1 = {"ison": False, "timer": None}
        self.ha = {"switch.fan": "off", "sensor.t": ("78.5", "°F"), "sensor.h": ("81", "%")}
        self.livedata = json.loads((FIX / "ecowitt_livedata_sample.json").read_text())
        self.down = False
        self.requests: list[str] = []
        devices = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                devices.requests.append(self.path)
                if devices.down:
                    self.send_response(503)
                    self.end_headers()
                    return
                u = urlsplit(self.path)
                q = {k: v[-1] for k, v in parse_qs(u.query).items()}
                p = u.path
                if p == "/shelly":
                    gen = 2 if self.headers.get("Host", "").startswith("127.0.0.1") else 1
                    return self._send({"gen": gen, "model": "fake"} if gen == 2 else {"type": "SHPLG-S"})
                if p == "/rpc/Switch.GetStatus":
                    return self._send({"id": 0, "output": devices.gen2["output"], "apower": devices.gen2["apower"]})
                if p == "/rpc/Switch.Set":
                    was = devices.gen2["output"]
                    devices.gen2["output"] = q["on"] == "true"
                    devices.gen2["apower"] = 27.5 if devices.gen2["output"] else 0.0
                    devices.gen2["toggle_after"] = q.get("toggle_after")
                    return self._send({"was_on": was})
                if p == "/relay/0":
                    if "turn" in q:
                        devices.gen1["ison"] = q["turn"] == "on"
                        devices.gen1["timer"] = q.get("timer")
                    return self._send({"ison": devices.gen1["ison"]})
                if p == "/meter/0":
                    return self._send({"power": 30.0 if devices.gen1["ison"] else 0.0})
                if p == "/get_livedata_info":
                    return self._send(devices.livedata)
                if p.startswith("/api/states/"):
                    ent = p.rsplit("/", 1)[1]
                    v = devices.ha.get(ent)
                    if v is None:
                        return self._send({"message": "not found"}, 404)
                    if isinstance(v, tuple):
                        return self._send({"state": v[0], "attributes": {"unit_of_measurement": v[1]},
                                           "last_updated": datetime.now(timezone.utc).isoformat()})
                    return self._send({"state": v, "attributes": {}})
                self._send({"error": "unknown"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path.startswith("/api/services/switch/"):
                    devices.ha[body["entity_id"]] = "on" if self.path.endswith("turn_on") else "off"
                    return self._send([])
                self._send({}, 404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def gen2_host(self):
        return f"127.0.0.1:{self.port}"

    @property
    def gen1_host(self):
        return f"localhost:{self.port}"  # different Host header -> answers as Gen1


@pytest.fixture
def dev():
    d = FakeDevices()
    yield d
    d.server.shutdown()


def test_parse_livedata_sample():
    data = json.loads((FIX / "ecowitt_livedata_sample.json").read_text())
    rs = {r.sensor_id: r for r in parse_livedata(data, T0)}
    assert set(rs) == {"ecowitt:indoor", "ecowitt:outdoor", "ecowitt:ch1", "ecowitt:ch2", "ecowitt:tf1"}
    assert rs["ecowitt:outdoor"].temp_c == pytest.approx((84.2 - 32) * 5 / 9)
    assert rs["ecowitt:outdoor"].rh_pct == 71
    assert rs["ecowitt:ch1"].rh_pct == 83 and rs["ecowitt:ch1"].battery_low is False
    assert rs["ecowitt:ch2"].battery_low is True
    assert rs["ecowitt:tf1"].rh_pct is None and rs["ecowitt:tf1"].battery_low is False
    assert rs["ecowitt:indoor"].pressure_kpa == pytest.approx(101.18, abs=0.05)


def test_parse_livedata_celsius_and_empty():
    data = {"common_list": [{"id": "0x02", "val": "29.0", "unit": "C"}, {"id": "0x07", "val": "70%"}]}
    assert parse_livedata(data, T0)[0].temp_c == 29.0
    with pytest.raises(Exception):
        parse_livedata({"common_list": []}, T0)


def test_parse_push_protocol():
    form = {"PASSKEY": "x", "stationtype": "GW1200B_V1.3.1", "dateutc": "2026-07-15 12:00:00",
            "tempf": "86.0", "humidity": "65", "temp1f": "79.0", "humidity1": "84", "batt1": "0",
            "tf_ch1": "75.5", "baromabsin": "29.90", "tempinf": "76.0", "humidityin": "55"}
    rs = {r.sensor_id: r for r in parse_push(form, T0)}
    assert rs["ecowitt:outdoor"].temp_c == pytest.approx(30.0)
    assert rs["ecowitt:ch1"].rh_pct == 84 and rs["ecowitt:ch1"].battery_low is False
    assert rs["ecowitt:tf1"].temp_c == pytest.approx((75.5 - 32) * 5 / 9)
    assert rs["ecowitt:outdoor"].ts == T0
    # gateway clock far off -> receive time is used
    form["dateutc"] = "2020-01-01 00:00:00"
    assert parse_push(form, T0)[0].ts == T0


def test_ecowitt_source_polls_and_merges_push(dev):
    clock = ManualClock(datetime.now(timezone.utc))
    src = EcowittSource(dev.gen2_host, clock)
    rs = asyncio.run(src.read_all())
    assert any(r.sensor_id == "ecowitt:ch1" for r in rs)
    src.ingest_push({"temp5f": "70", "humidity5": "60"})
    ids = {r.sensor_id for r in asyncio.run(src.read_all())}
    assert "ecowitt:ch5" in ids and "ecowitt:ch1" in ids
    dev.down = True
    rs = asyncio.run(src.read_all())  # push data still available
    assert src.last_poll_error and any(r.sensor_id == "ecowitt:ch5" for r in rs)


def test_ecowitt_unreachable_raises():
    src = EcowittSource("127.0.0.1:9", ManualClock(T0), timeout=0.5)
    with pytest.raises(Exception):
        asyncio.run(src.read_all())


def test_shelly_gen2_switching_with_auto_off(dev):
    sw = ShellySwitches([ShellyDevice(switch_id="p", host=dev.gen2_host)], ManualClock(T0), auto_off_minutes=30)
    st = asyncio.run(sw.set("p", True))
    assert st.reachable and st.on and st.power_w == 27.5
    assert dev.gen2["toggle_after"] == "1800"  # hardware dead-man timer armed
    st = asyncio.run(sw.set("p", False))
    assert st.on is False and dev.gen2["toggle_after"] is None


def test_shelly_gen1_switching(dev):
    sw = ShellySwitches([ShellyDevice(switch_id="p", host=dev.gen1_host)], ManualClock(T0), auto_off_minutes=20)
    st = asyncio.run(sw.set("p", True))
    assert st.on and st.power_w == 30.0 and dev.gen1["timer"] == "1200"


def test_shelly_unreachable_and_unknown():
    sw = ShellySwitches([ShellyDevice(switch_id="p", host="127.0.0.1:9", generation="2")], ManualClock(T0), timeout=0.5)
    st = asyncio.run(sw.get("p"))
    assert not st.reachable and st.on is None and st.error
    assert not asyncio.run(sw.get("missing")).reachable


def test_home_assistant_adapters(dev):
    ha = HomeAssistant(f"http://{dev.gen2_host}", "token")
    sensors = HASensors(ha, {"bsmt": {"temp": "sensor.t", "rh": "sensor.h"}}, ManualClock(T0))
    r = asyncio.run(sensors.read_all())[0]
    assert r.temp_c == pytest.approx((78.5 - 32) * 5 / 9) and r.rh_pct == 81
    sw = HASwitches(ha, ManualClock(T0))
    assert asyncio.run(sw.set("switch.fan", True)).on is True
    assert asyncio.run(sw.get("switch.missing")).reachable is False


def test_controller_rearms_hardware_watchdog_and_runs_real_adapters(dev):
    """End to end: Ecowitt polling + Shelly Gen2 under the real controller."""
    cfg = example_config()
    cfg.sensors.provider = "ecowitt"
    cfg.sensors.ecowitt_host = dev.gen2_host
    cfg.switches.provider = "shelly"
    cfg.switches.hardware_max_on_minutes = 30
    cfg.fans = [Fan(id="f1", name="Fan", switch_id="p", actual_cfm=85, rated_w=28)]
    cfg.switches.shelly_devices = [ShellyDevice(switch_id="p", host=dev.gen2_host)]
    cfg.sensors.mappings = [
        SensorMapping(sensor_id="ecowitt:outdoor", name="Out", role=SensorRole.OUTDOOR),
        SensorMapping(sensor_id="ecowitt:ch1", name="Bsmt", role=SensorRole.BASEMENT, zone="main"),
        SensorMapping(sensor_id="ecowitt:tf1", name="Slab", role=SensorRole.SURFACE),
    ]
    # make outdoor drier than the 79.3F/83% basement and keep the slab warm enough
    dev.livedata["common_list"] = [{"id": "0x02", "val": "80.0", "unit": "F"}, {"id": "0x07", "val": "45%"}]
    clock = ManualClock(datetime.now(timezone.utc))
    from ventctl.hardware import factory
    sensors, switches, _ = factory.build(cfg, clock, None, clock.now())
    ctrl = Controller(cfg, Storage(":memory:"), sensors, switches, clock, data_source="measured")

    async def go():
        await ctrl.startup()
        for _ in range(3):
            await ctrl.tick()
        assert ctrl.decisions["main"].reason_code == "DRYING", ctrl.decisions["main"].explanation
        assert dev.gen2["output"] is True
        n = sum(1 for r in dev.requests if "Switch.Set" in r and "on=true" in r)
        clock.set(clock.now() + timedelta(minutes=11))  # > 30/3 minutes: re-arm due
        await ctrl.tick()
        n2 = sum(1 for r in dev.requests if "Switch.Set" in r and "on=true" in r)
        return n, n2

    # readings are timestamped at poll time so they stay fresh as the clock advances
    n, n2 = asyncio.run(go())
    assert n2 == n + 1
    assert "NO_HARDWARE_WATCHDOG" not in ctrl.alerts
    assert ctrl.config.mode == Mode.AUTO


# ---------------------------------------------------------------- Tapo (python-kasa) with a fake device
class FakeAutoOff:
    def __init__(self, dev):
        self.dev, self.delay, self.enabled = dev, 120, False

    async def set_delay(self, d):
        self.delay = d

    async def set_enabled(self, e):
        self.enabled = e

    @property
    def auto_off_at(self):
        if not (self.enabled and self.dev.is_on):
            return None
        return self.dev.on_at + timedelta(minutes=self.delay)


class FakeTapo:
    """Mimics the parts of kasa.Device the adapter uses; time is controlled by the test clock."""

    def __init__(self, clock, energy=False):
        from kasa import Module
        self.clock, self.is_on, self.on_at, self.calls, self.fail = clock, False, None, [], False
        self.modules = {Module.AutoOff: FakeAutoOff(self)}
        if energy:
            self.modules[Module.Energy] = type("E", (), {"current_consumption": 0.0})()
        self.energy = self.modules.get(Module.Energy)

    @property
    def time(self):
        return self.clock.now()

    async def update(self):
        if self.fail:
            from kasa import KasaException
            raise KasaException("timeout")
        mod = next(iter(self.modules.values()))
        if self.is_on and mod.enabled and self.clock.now() >= self.on_at + timedelta(minutes=mod.delay):
            self.is_on = False  # the plug's own auto-off fired
        if self.energy:
            self.energy.current_consumption = 26.0 if self.is_on else 0.0

    async def turn_on(self):
        self.calls.append("on")
        if not self.is_on:
            self.on_at = self.clock.now()
        self.is_on = True

    async def turn_off(self):
        self.calls.append("off")
        self.is_on = False


def tapo(clock, minutes=30, energy=False):
    from ventctl.config import TapoDevice
    from ventctl.hardware.tapo import TapoSwitches
    fake = FakeTapo(clock, energy)

    async def connect(host):
        return fake
    sw = TapoSwitches([TapoDevice(switch_id="t", host="10.0.0.9")], clock, "me@x", "pw",
                      auto_off_minutes=minutes, connector=connect)
    return sw, fake


def test_tapo_enables_plug_auto_off_and_switches():
    clock = ManualClock(T0)
    sw, fake = tapo(clock, minutes=30, energy=True)
    st = asyncio.run(sw.set("t", True))
    assert st.reachable and st.on and st.power_w == 26.0
    from kasa import Module
    assert fake.modules[Module.AutoOff].enabled and fake.modules[Module.AutoOff].delay == 30
    assert sw.supports_hardware_auto_off()
    assert asyncio.run(sw.set("t", False)).on is False


def test_tapo_mini_without_energy_monitoring_reports_no_power():
    sw, _ = tapo(ManualClock(T0))
    assert asyncio.run(sw.set("t", True)).power_w is None


def test_tapo_rearm_restarts_countdown_before_it_fires():
    clock = ManualClock(T0)
    sw, fake = tapo(clock, minutes=30)
    asyncio.run(sw.set("t", True))
    for minute in range(1, 120):
        clock.set(T0 + timedelta(minutes=minute))
        asyncio.run(sw.rearm("t"))
        assert asyncio.run(sw.get("t")).on, f"plug turned itself off at minute {minute}"
    assert fake.calls.count("off") >= 6  # brief off/on blips every ~15 min


def test_tapo_plug_turns_off_by_itself_if_controller_stops():
    clock = ManualClock(T0)
    sw, fake = tapo(clock, minutes=30)
    asyncio.run(sw.set("t", True))
    clock.set(T0 + timedelta(minutes=31))  # no re-arm: controller is dead
    assert asyncio.run(sw.get("t")).on is False


def test_tapo_unreachable():
    clock = ManualClock(T0)
    sw, fake = tapo(clock)
    asyncio.run(sw.get("t"))
    fake.fail = True
    st = asyncio.run(sw.get("t"))
    assert not st.reachable and "timeout" in st.error
