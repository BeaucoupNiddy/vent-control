"""A temperature-only Ecowitt channel (WN30BL concrete probe) becomes its own surface probe."""

import asyncio

from ventctl.clock import ManualClock
from ventctl.config import SensorMapping, SensorRole, example_config
from ventctl.controller import Controller
from ventctl.hardware.base import SensorSource
from ventctl.hardware.ecowitt import parse_livedata, parse_push
from ventctl.hardware.mock import MockSwitches, MockWorld
from ventctl.sensors import SensorReading
from ventctl.storage import Storage

from .helpers import T0, minutes


class Fixed(SensorSource):
    def __init__(self, clock, rows):
        self.clock, self.rows = clock, rows

    async def read_all(self):
        now = self.clock.now()
        return [SensorReading(sid, now, temp_c=t, rh_pct=rh, source="ecowitt") for sid, t, rh in self.rows]


def _ctrl(mappings, rows):
    cfg = example_config()
    cfg.sensors.mappings = mappings
    clock = ManualClock(T0)
    world = MockWorld(cfg, T0)
    store = Storage(":memory:")
    store.save_config(cfg)
    c = Controller(cfg, store, Fixed(clock, rows), MockSwitches(world, clock), clock)
    asyncio.run(c.startup())
    return c, clock, store


ROOMS = [SensorMapping(sensor_id="ecowitt:ch1", name="Outdoors", role=SensorRole.OUTDOOR),
         SensorMapping(sensor_id="ecowitt:indoor", name="Main Hub", role=SensorRole.BASEMENT, zone="main")]
ROWS = [("ecowitt:ch1", 15.0, 60.0), ("ecowitt:indoor", 20.0, 55.0), ("ecowitt:ch6", 17.5, None)]


def _ticks(c, clock, n):
    for _ in range(n):
        asyncio.run(c.tick())
        clock.set(clock.now() + minutes(1))


def test_unmapped_probe_is_added_as_surface():
    c, clock, store = _ctrl(list(ROOMS), ROWS)
    _ticks(c, clock, 2)
    assert not any(m.sensor_id == "ecowitt:ch6" for m in c.config.sensors.mappings)
    _ticks(c, clock, 2)
    m = next(m for m in c.config.sensors.mappings if m.sensor_id == "ecowitt:ch6")
    assert m.role == SensorRole.SURFACE and m.name == "Concrete slab probe"
    assert any(m.sensor_id == "ecowitt:ch6" for m in store.load_config().sensors.mappings)
    s = next(s for s in c.surface_status if s.sensor_id == "ecowitt:ch6")
    assert s.t_c is not None and abs(s.t_c - 17.5) < 0.01 and s.margin_f is not None
    # never blended into the room air
    basement = [m.sensor_id for m in c.config.sensors.mappings if m.role == SensorRole.BASEMENT]
    assert basement == ["ecowitt:indoor"]


def test_probe_mapped_as_basement_moves_to_surface():
    maps = list(ROOMS) + [SensorMapping(sensor_id="ecowitt:ch6", name="Slab", role=SensorRole.BASEMENT, zone="main")]
    c, clock, _ = _ctrl(maps, ROWS)
    _ticks(c, clock, 3)
    m = next(m for m in c.config.sensors.mappings if m.sensor_id == "ecowitt:ch6")
    assert m.role == SensorRole.SURFACE and m.zone == "main" and m.name == "Slab"


def test_room_sensor_with_humidity_gap_is_left_alone():
    rows = [("ecowitt:ch1", 15.0, 60.0), ("ecowitt:indoor", 20.0, 55.0), ("ecowitt:ch2", 19.0, 50.0)]
    maps = list(ROOMS) + [SensorMapping(sensor_id="ecowitt:ch2", name="Bike room", role=SensorRole.BASEMENT)]
    c, clock, _ = _ctrl(maps, rows)
    _ticks(c, clock, 1)
    c.sensors.rows = [r if r[0] != "ecowitt:ch2" else ("ecowitt:ch2", 19.0, None) for r in rows]
    _ticks(c, clock, 5)
    assert next(m for m in c.config.sensors.mappings if m.sensor_id == "ecowitt:ch2").role == SensorRole.BASEMENT


def test_wn30_parsed_without_humidity():
    data = {"ch_aisle": [{"channel": "6", "temp": "64.4", "unit": "F", "humidity": "None", "battery": "0"}]}
    (r,) = parse_livedata(data, T0)
    assert r.sensor_id == "ecowitt:ch6" and r.rh_pct is None and abs(r.temp_c - 18.0) < 0.01
    (p,) = parse_push({"temp6f": "64.4", "batt6": "0"}, T0)
    assert p.sensor_id == "ecowitt:ch6" and p.rh_pct is None
