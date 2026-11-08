"""FastAPI application: REST API + static dashboard."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import analysis, climate, simulation
from .clock import AcceleratedClock, Clock, ManualClock
from .config import AppConfig, Mode, ShellyDevice, example_config
from .controller import Controller
from .hardware import factory
from .hardware.base import HardwareError
from .hardware.ecowitt import EcowittSource
from .hardware.mock import MockWorld
from .hardware.shelly import ShellySwitches
from .psychro import AirState
from .units import f_to_c

log = logging.getLogger("ventctl")
STATIC_DIR = Path(__file__).parent / "static"


class ProxyClock(Clock):
    """Lets the app swap the backfill clock for the live clock without rewiring adapters."""

    def __init__(self, inner: Clock):
        self.inner = inner

    @property
    def speed(self) -> float:  # type: ignore[override]
        return self.inner.speed

    def now(self) -> datetime:
        return self.inner.now()


class ModeRequest(BaseModel):
    mode: Mode
    minutes: float | None = None
    maintenance: bool = False


class SimRequest(BaseModel):
    params: simulation.SimParams = simulation.SimParams()
    profile_id: str = "nola_summer"
    custom_temp_f: list[float] | None = None
    custom_dewpoint_f: list[float] | None = None
    csv_text: str | None = None
    recorded_range: str | None = None  # "24h" | "7d" | "30d" for historical recorded conditions
    strategies: list[str] | None = None


class ReplayRequest(BaseModel):
    range: str = "7d"
    start: str | None = None
    end: str | None = None


class FaultRequest(BaseModel):
    target: str  # "sensor" | "switch" | "gateway"
    id: str | None = None
    fault: str | None = None  # sensor: "offline" | None ; switch: "unreachable" | "stuck" | None ; gateway: "down" | None


class SwitchTestRequest(BaseModel):
    host: str
    channel: int = 0
    generation: str = "auto"


class CalcRequest(BaseModel):
    t_f: float
    rh_pct: float


async def backfill(ctrl: Controller, clock: ManualClock, until: datetime) -> None:
    t = clock.now()
    step = timedelta(seconds=ctrl.config.system.decision_seconds)
    while t < until:
        clock.set(t)
        await ctrl.tick()
        t += step


def create_app(db_path: str | None = None, demo_speed: float | None = None, backfill_hours: float | None = None,
               start_loop: bool = True) -> FastAPI:
    from .storage import Storage

    db_path = db_path or os.environ.get("VENTCTL_DB", "data/ventctl.db")
    demo_speed = demo_speed if demo_speed is not None else float(os.environ.get("VENTCTL_DEMO_SPEED", "1"))
    backfill_hours = backfill_hours if backfill_hours is not None else float(os.environ.get("VENTCTL_BACKFILL_HOURS", "48"))
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage = Storage(db_path)
        cfg = storage.load_config()
        if cfg is None:
            cfg = example_config()
            storage.save_config(cfg)
        now = datetime.now(timezone.utc)
        mock = cfg.sensors.provider == "mock" and cfg.switches.provider == "mock"
        empty = storage.history_count() == 0
        # mock history backfill only when everything is mock: never mix invented data with real history
        start = now - timedelta(hours=backfill_hours) if (mock and empty and backfill_hours > 0) else now
        bclock = ManualClock(start)
        clock = ProxyClock(bclock)
        sensors, switches, world = factory.build(cfg, clock, None, start)
        ctrl = Controller(cfg, storage, sensors, switches, clock, data_source=factory.data_source(cfg))
        await ctrl.startup()
        if start < now:
            log.info("Backfilling %.0f h of mock history", backfill_hours)
            await backfill(ctrl, bclock, now)
        clock.inner = AcceleratedClock(now, demo_speed) if (demo_speed != 1 and mock) else Clock()
        state.update(storage=storage, ctrl=ctrl, world=world, clock=clock)
        task = asyncio.create_task(ctrl.run_forever()) if start_loop else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Basement Ventilation Controller", lifespan=lifespan)
    app.state.vc = state

    def ctrl() -> Controller:
        return state["ctrl"]

    def tz() -> ZoneInfo:
        return ZoneInfo(ctrl().config.system.timezone)

    @app.get("/api/health")
    def health():
        return {"ok": True, "now": ctrl().clock.now().isoformat()}

    @app.get("/api/status")
    def status():
        return ctrl().status()

    @app.get("/api/config")
    def get_config():
        return ctrl().config.model_dump(mode="json")

    @app.get("/api/config/example")
    def get_example():
        return example_config().model_dump(mode="json")

    @app.put("/api/config")
    def put_config(body: dict):
        try:
            new = AppConfig.model_validate(body)
        except ValidationError as e:
            return JSONResponse(status_code=422, content={"detail": e.errors(include_url=False, include_context=False)})
        c = ctrl()
        old = c.config
        hw_keys = ("sensors", "switches", "homeassistant")
        hw_changed = any(getattr(old, k).model_dump(exclude={"mappings"}) != getattr(new, k).model_dump(exclude={"mappings"})
                         for k in hw_keys)
        c.apply_config(new)
        if hw_changed:
            sensors, switches, world = factory.build(new, c.clock, state.get("world"), c.clock.now())
            c.sensors, c.switches = sensors, switches
            c.data_source = factory.data_source(new)
            state["world"] = world
            c.storage.add_event(c.clock.now(), "config", "warning",
                                f"Hardware adapters changed: sensors={new.sensors.provider}, switches={new.switches.provider}",
                                "HARDWARE_CHANGE")
        if state.get("world"):
            state["world"].config = new
        return new.model_dump(mode="json")

    @app.get("/api/sensors/discover")
    async def discover():
        try:
            return {"channels": await ctrl().sensors.discover(),
                    "mapped": [m.sensor_id for m in ctrl().config.sensors.mappings]}
        except HardwareError as e:
            raise HTTPException(502, str(e))

    @app.get("/api/sensors/raw")
    def sensors_raw():
        """Last raw gateway response, for checking the format against the parser."""
        return {"raw": getattr(ctrl().sensors, "last_raw", None),
                "poll_error": getattr(ctrl().sensors, "last_poll_error", None)}

    @app.post("/data/report")
    @app.post("/data/report/")
    async def ecowitt_push(request: Request):
        src = ctrl().sensors
        if not isinstance(src, EcowittSource) or not ctrl().config.sensors.ecowitt_push:
            raise HTTPException(404, "Ecowitt push is not enabled")
        body = (await request.body()).decode("utf-8", "replace")
        form = {k: v[-1] for k, v in urllib.parse.parse_qs(body).items()}
        readings = src.ingest_push(form)
        return {"accepted": len(readings)}

    @app.post("/api/switches/test")
    async def switch_test(req: SwitchTestRequest):
        """Read a Shelly plug's identity and relay state without switching it."""
        dev = ShellyDevice(switch_id="_test", host=req.host, channel=req.channel, generation=req.generation)
        sw = ShellySwitches([dev], ctrl().clock, ctrl().config.switches.timeout_s)
        try:
            info = await sw.device_info(req.host)
        except HardwareError as e:
            raise HTTPException(502, str(e))
        st = await sw.get("_test")
        return {"info": info, "status": {"on": st.on, "reachable": st.reachable, "power_w": st.power_w,
                                         "error": st.error}}

    @app.post("/api/mode")
    def set_mode(req: ModeRequest):
        ctrl().set_mode(req.mode, req.minutes, req.maintenance)
        return ctrl().status()

    @app.get("/api/history")
    def history(range: str = "24h", start: str | None = None, end: str | None = None, max_points: int = 1500):
        s, e = analysis.range_for(range, ctrl().clock.now(), start, end)
        rows = state["storage"].history(s, e, max_points)
        return {"start": s.isoformat(), "end": e.isoformat(), "rows": rows,
                "labels": {"removal_g_h": "modeled", "potential_removal_g_h": "modeled",
                           "power_w": rows[-1]["source"] if rows else None}}

    @app.get("/api/stats")
    def stats(range: str = "24h", start: str | None = None, end: str | None = None):
        s, e = analysis.range_for(range, ctrl().clock.now(), start, end)
        rows = state["storage"].history(s, e, max_points=10**7)
        return analysis.stats(rows, ctrl().config)

    @app.get("/api/events")
    def events(limit: int = 200, kind: str | None = None):
        return state["storage"].events(limit, kind)

    @app.get("/api/sim/profiles")
    def profiles():
        return [p.summary() for p in climate.BUILTIN_PROFILES.values()]

    @app.get("/api/sim/profiles/{pid}")
    def profile(pid: str):
        try:
            return climate.get_profile(pid).to_dict()
        except KeyError:
            raise HTTPException(404, "unknown profile")

    @app.get("/api/sim/defaults")
    def sim_defaults():
        c = ctrl().config
        p = simulation.SimParams(conditioned_air_fraction=c.house.conditioned_air_fraction,
                                 electricity_cost_per_kwh=c.house.electricity_cost_per_kwh,
                                 natural_ach=c.house.natural_ach)
        fans = [f for f in c.fans if f.enabled]
        if fans:
            p.fan_count = len(fans)
            p.fan_cfm = sum(f.actual_cfm for f in fans) / len(fans)
            p.fan_w = sum(f.rated_w for f in fans) / len(fans)
        return {"params": p.model_dump(), "strategies": simulation.STRATEGIES}

    @app.post("/api/sim/run")
    def sim_run(req: SimRequest):
        try:
            if req.csv_text:
                prof = climate.profile_from_csv(req.csv_text)
            elif req.custom_temp_f and req.custom_dewpoint_f:
                prof = climate.custom_profile(req.custom_temp_f, req.custom_dewpoint_f)
            elif req.recorded_range:
                s, e = analysis.range_for(req.recorded_range, ctrl().clock.now())
                prof = analysis.recorded_profile(state["storage"].history(s, e, 10**7))
            else:
                prof = climate.get_profile(req.profile_id)
        except KeyError:
            raise HTTPException(404, "unknown profile")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return simulation.compare(req.params, prof, ctrl().config, req.strategies)

    @app.post("/api/replay")
    def run_replay(req: ReplayRequest):
        s, e = analysis.range_for(req.range, ctrl().clock.now(), req.start, req.end)
        rows = state["storage"].history(s, e, 10**7)
        if len(rows) < 2:
            raise HTTPException(400, "not enough recorded history in this range")
        return analysis.replay(rows, ctrl().config, tz())

    @app.post("/api/calc")
    def calc(req: CalcRequest):
        if not (0 < req.rh_pct <= 100):
            raise HTTPException(400, "RH must be between 0 and 100")
        return AirState.from_t_rh(f_to_c(req.t_f), req.rh_pct / 100).to_dict()

    @app.get("/api/mock/state")
    def mock_state():
        w: MockWorld | None = state.get("world")
        if w is None:
            raise HTTPException(404, "mock hardware is not active")
        return {"sensor_faults": sorted(w.sensor_faults), "switch_faults": w.switch_faults,
                "gateway_down": w.gateway_down, "switches": w.switch_on,
                "profile": w.profile.summary(), "profile_hour": w.profile_hour(ctrl().clock.now())}

    @app.post("/api/mock/fault")
    def mock_fault(req: FaultRequest):
        w: MockWorld | None = state.get("world")
        if w is None:
            raise HTTPException(404, "mock hardware is not active")
        if req.target == "sensor" and req.id:
            (w.sensor_faults.add if req.fault else w.sensor_faults.discard)(req.id)
        elif req.target == "switch" and req.id:
            if req.fault:
                w.switch_faults[req.id] = req.fault
            else:
                w.switch_faults.pop(req.id, None)
        elif req.target == "gateway":
            w.gateway_down = bool(req.fault)
        else:
            raise HTTPException(400, "bad fault request")
        state["storage"].add_event(ctrl().clock.now(), "config", "warning",
                                   f"Mock fault injected: {req.target} {req.id or ''} -> {req.fault or 'cleared'}",
                                   "MOCK_FAULT")
        return mock_state()

    @app.get("/api/export/config")
    def export_config():
        return JSONResponse(ctrl().config.model_dump(mode="json"),
                            headers={"Content-Disposition": "attachment; filename=ventctl-config.json"})

    @app.get("/api/export/history.csv")
    def export_history(range: str = "30d"):
        s, e = analysis.range_for(range, ctrl().clock.now())
        rows = state["storage"].history(s, e, 10**7)
        buf = io.StringIO()
        if rows:
            wr = csv.DictWriter(buf, fieldnames=[k for k in rows[0] if k != "zones"], extrasaction="ignore")
            wr.writeheader()
            wr.writerows(rows)
        return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=ventctl-history.csv"})

    if STATIC_DIR.exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            f = STATIC_DIR / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(STATIC_DIR / "index.html")

    return app
