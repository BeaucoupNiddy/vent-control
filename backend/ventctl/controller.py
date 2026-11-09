"""Runtime controller: acquisition -> validation -> decision -> switching -> logging.

Fail-safe rules implemented here:
  * sensor gateway errors or stale readings -> engine forces OFF, we command OFF
  * a switch that cannot be read or does not confirm a command marks the group
    unverified; the engine then forces OFF and we keep retrying OFF
  * last *commanded* state is kept separately from *verified* device state
  * at startup we read every switch and command anything ON to OFF before
    automatic control resumes (safe state after a power outage)
  * manual overrides are time-limited and logged

Temperature-aware optimization (TAMO):
  * the policy in control (``control.policy``) commands the fans; the other policy
    is evaluated every tick in shadow mode (never switches hardware) and every
    agreement/disagreement change is logged, so the new logic can be observed
    before it is given control, and rolled back with one click
  * thermal predictions made at each fan start are checked against the observed
    temperature 30 minutes later; large errors raise the model uncertainty
    (more conservative limits) and trigger a refit
  * the thermal model is refit from recorded fan cycles every few hours when learning is on
  * an optional forecast is refreshed in the background; failures never affect control

Version 3 (zones, concrete probe, internal circulation):
  * every tick computes per-zone conditions (trends, persistence, sensor quality) and the
    status of each measured surface against its own zone's dew point
  * internal circulation fans have their own decision engine (circulation.py). They only
    switch hardware in "auto" mode after the fan is marked commissioned; otherwise the
    decision is recorded as a shadow ("would turn ON") and nothing is commanded
  * manual circulator overrides are time-limited; a water-intrusion lockout turns them off
  * completed circulator runs are recorded with before/after zone conditions for learning
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .clock import Clock
from . import calibration, circulation, tamo, thermal
from . import surfaces as surf_mod
from . import zones as zones_mod
from .config import AppConfig, ControlPolicy, FanGroup, Mode, SensorRole
from .engine import Decision, GroupState, decide, group_fans
from .forecast import ForecastCache
from .hardware.base import HardwareError, SensorSource, SwitchController, SwitchStatus
from .sensors import SensorReading, Snapshot, conditions_for_group, validate
from .storage import Storage
from .units import delta_f_to_c

log = logging.getLogger("ventctl")
COMMAND_LOCKOUT_MIN = 30
THERMAL_REFIT_H = 6
THERMAL_CHECK_MIN = 30
SAFETY_ALERT_CODES = {"LOCALIZED_FREEZE_RISK": "critical", "PIPE_SENSOR_UNAVAILABLE": "critical",
                      "MIN_TEMP_REACHED": "warning", "PREDICTED_MIN_TEMP_BREACH": "warning",
                      "PREDICTED_SURFACE_CONDENSATION": "warning"}
NO_SAFE_DRYING_CODES = {"OUTDOOR_WETTER", "CONDENSATION_RISK", "THERMAL_PENALTY_TOO_HIGH", "MIN_TEMP_REACHED",
                        "PREDICTED_MIN_TEMP_BREACH", "LOCALIZED_FREEZE_RISK", "PREDICTED_SURFACE_CONDENSATION",
                        "ADVANTAGE_TOO_SMALL", "MOISTURE_BENEFIT_INSUFFICIENT", "BENEFIT_TOO_SMALL"}


@dataclass
class SwitchInfo:
    switch_id: str
    commanded: bool | None = None  # what we last asked for
    confirmed: bool | None = None  # what the device last reported
    reachable: bool = False
    power_w: float | None = None
    last_ok: datetime | None = None
    error: str | None = None
    on_since: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "switch_id": self.switch_id, "commanded": self.commanded, "confirmed": self.confirmed,
            "reachable": self.reachable, "power_w": self.power_w,
            "last_ok": self.last_ok.isoformat() if self.last_ok else None, "error": self.error,
            "on_since": self.on_since.isoformat() if self.on_since else None,
        }


@dataclass
class Alert:
    id: str
    severity: str
    message: str
    since: datetime

    def to_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "message": self.message, "since": self.since.isoformat()}


@dataclass
class Controller:
    config: AppConfig
    storage: Storage
    sensors: SensorSource
    switches: SwitchController
    clock: Clock
    data_source: str = "mock"

    groups: dict[str, GroupState] = field(default_factory=dict)
    switch_info: dict[str, SwitchInfo] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    snapshot: Snapshot | None = None
    alerts: dict[str, Alert] = field(default_factory=dict)
    last_history: datetime | None = None
    previous_mode: Mode = Mode.AUTO
    _prev_ts: dict[str, datetime] = field(default_factory=dict)
    _last_readings: dict[str, SensorReading] = field(default_factory=dict)
    _gateway_error: str | None = None
    _rh_above: dict[str, datetime | None] = field(default_factory=dict)
    _cycle_times: list[datetime] = field(default_factory=list)
    _command_failures: dict[str, tuple] = field(default_factory=dict)
    dehum_status: SwitchStatus | None = None
    forecast: ForecastCache | None = None
    shadow_groups: dict[str, GroupState] = field(default_factory=dict)
    shadow_decisions: dict[str, Decision] = field(default_factory=dict)
    thermal_learned: dict | None = None
    _pending_preds: list[dict] = field(default_factory=list)
    _thermal_errors: list[float] = field(default_factory=list)
    _last_fit: datetime | None = None
    _indoor_hist: deque = field(default_factory=lambda: deque(maxlen=120))
    _no_safe_since: dict[str, datetime] = field(default_factory=dict)
    _agree: dict[str, bool] = field(default_factory=dict)
    zone_tracker: zones_mod.ZoneTracker = field(default_factory=zones_mod.ZoneTracker)
    surface_tracker: surf_mod.SurfaceTracker = field(default_factory=surf_mod.SurfaceTracker)
    zone_status: dict = field(default_factory=dict)
    surface_status: list = field(default_factory=list)
    circ_states: dict = field(default_factory=dict)  # real plug state per circulator
    circ_shadow: dict = field(default_factory=dict)  # hypothetical state while not commanding
    circ_decisions: dict = field(default_factory=dict)
    surface_learned: dict | None = None
    comparison: dict | None = None
    _cmp_samples: list = field(default_factory=list)
    _circ_cycle: dict = field(default_factory=dict)
    _circ_auto_on: set = field(default_factory=set)
    _runtime_cache: tuple | None = None
    _started: bool = False
    _task: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        """Reconcile actual switch states before resuming automatic control."""
        now = self.clock.now()
        found = {}
        turned_off = False
        for sid in self._switch_ids():
            st = await self.switches.get(sid)
            self._record_status(st, now)
            found[sid] = st.on if st.reachable else "unreachable"
            if st.reachable and st.on and self.config.mode != Mode.SIMULATION:
                res = await self.switches.set(sid, False)
                self._record_status(res, now, commanded=False)
                turned_off = True
        for g in self.config.groups:
            gs = GroupState(is_on=False, verified=True, last_change=now if turned_off else None)
            gs.is_on, gs.verified = self._group_switch_state(g.id)
            self.groups[g.id] = gs
        if self.thermal_learned is None:
            self.thermal_learned = self.storage.get_kv("thermal_model")
        if self.surface_learned is None:
            self.surface_learned = self.storage.get_kv("surface_model")
        if self.comparison is None:
            self.comparison = self.storage.get_kv("sensor_comparison")
            self._cmp_samples = list((self.storage.get_kv("sensor_comparison_samples") or [])[-3000:])
        for c in self.config.circulators:
            info = self.switch_info.get(c.switch_id)
            self.circ_states[c.id] = circulation.CircState(
                is_on=bool(info and info.confirmed), verified=bool(info and info.reachable),
                last_change=now if turned_off else None)
        self.storage.add_event(now, "startup", "info",
                               "Controller started; reconciled switch states" +
                               (" and switched running fans OFF (safe startup state)" if turned_off else ""),
                               "STARTUP_RECONCILE", {"switches_found": found, "mode": self.config.mode.value})
        self._started = True

    async def run_forever(self) -> None:
        if not self._started:
            await self.startup()
        while True:
            try:
                await self.tick()
            except Exception as e:  # keep the loop alive; record the fault
                log.exception("tick failed")
                self.storage.add_event(self.clock.now(), "fault", "critical", f"Controller error: {e}", "TICK_ERROR")
            await asyncio.sleep(max(0.2, self.config.system.decision_seconds / max(self.clock.speed, 1e-6)))

    # ------------------------------------------------------------ main tick
    async def tick(self) -> None:
        now = self.clock.now()
        cfg = self.config
        self._expire_manual(now)

        readings: list[SensorReading] = []
        try:
            readings = await self.sensors.read_all()
            if self._gateway_error:
                self.storage.add_event(now, "fault", "info", "Sensor gateway reachable again", "GATEWAY_OK")
            self._gateway_error = None
        except HardwareError as e:
            if not self._gateway_error:
                self.storage.add_event(now, "fault", "critical", f"Sensor gateway error: {e}", "GATEWAY_ERROR")
            self._gateway_error = str(e)
        # A gateway outage must not let old readings look current: keep the last
        # reading per sensor, and validation will age it out to "stale".
        for r in readings:
            self._last_readings[r.sensor_id] = r

        stale_s = cfg.sensors.stale_minutes * 60
        snap = Snapshot(now)
        for m in cfg.sensors.mappings:
            if m.role == SensorRole.IGNORE:
                continue
            r = self._last_readings.get(m.sensor_id)
            snap.readings.append(validate(m, r, now, stale_s, self._prev_ts.get(m.sensor_id),
                                          cfg.house.default_pressure_kpa()))
            if r:
                self._prev_ts[m.sensor_id] = r.ts
        self.snapshot = snap

        # verify switch states every tick
        for sid in self._switch_ids():
            self._record_status(await self.switches.get(sid), now)

        if self.forecast is not None:
            try:
                await self.forecast.refresh(cfg, now)
            except Exception as e:  # never let a forecast problem affect control
                log.warning("forecast refresh failed: %s", e)
        fc = self.forecast.current(now) if self.forecast and cfg.temperature.forecast_enabled else None
        fc_age = self.forecast.age_h(now) if fc else None
        tp = self.thermal_params()
        air_follow, _ = surf_mod.air_follow_fraction(cfg, self.surface_learned)

        local = now.astimezone(ZoneInfo(cfg.system.timezone))
        local_hour = local.hour + local.minute / 60
        for g in cfg.groups:
            gs = self.groups.setdefault(g.id, GroupState())
            if cfg.mode != Mode.SIMULATION:
                gs.is_on, gs.verified = self._group_switch_state(g.id)
            cond = conditions_for_group(snap, g, cfg)
            cond.forecast, cond.forecast_age_h = fc, fc_age
            cond.surface_follow = air_follow
            was_on = gs.is_on
            d = decide(cond, gs, cfg, g, now, local_hour=local_hour, thermal_params=tp)
            _track_deferral(gs, d, now)
            prev = self.decisions.get(g.id)
            self.decisions[g.id] = d
            await self._apply(g.id, gs, d, now)
            self._shadow(g, cond, gs, d, now, local_hour, tp)
            if prev is None or prev.reason_code != d.reason_code or prev.target_on != d.target_on:
                self.storage.add_event(now, "decision", "warning" if d.safety else "info", d.explanation,
                                       d.reason_code, {"group": g.id, "target_on": d.target_on,
                                                       "policy": d.policy, "reasons": d.reasons,
                                                       "opportunity": d.opportunity,
                                                       "tamo": _tamo_summary(d),
                                                       "readings": snap.to_dict()["readings"],
                                                       "metrics": d.metrics,
                                                       "switches": {s: i.to_dict() for s, i in self.switch_info.items()}})
            if gs.is_on and not was_on:
                self._record_prediction(g.id, cond, d, now, tp)
            self._check_predictions(g.id, gs, cond, now)
        allc = conditions_for_group(snap, FanGroup(id="_all", name="all"), cfg)
        if allc.indoor:
            self._indoor_hist.append((now, allc.indoor))
        self._zones_and_surfaces(snap, allc, fc, now)
        await self._circulate(now, local_hour)
        self._maybe_refit(now)

        await self._rearm_watchdogs(now)
        await self._read_dehumidifier()
        self._update_alerts(now)
        if self.last_history is None or (now - self.last_history) >= timedelta(minutes=cfg.system.history_minutes):
            self._write_history(now)
            self.last_history = now

    # ------------------------------------------------------------ zones, surfaces, circulation
    def _zones_and_surfaces(self, snap: Snapshot, allc, fc, now: datetime) -> None:
        cfg = self.config
        coloc = set(self.comparison["sensor_ids"]) if self.comparison else set()
        zs = zones_mod.zone_statuses(snap, cfg, self.zone_tracker, now, coloc)
        self.zone_status = zs
        ss = surf_mod.surface_statuses(snap, cfg, zs, self.surface_tracker, now, fallback=allc.indoor,
                                       outdoor=allc.outdoor, forecast=fc)
        d0 = self.decisions.get(cfg.groups[0].id) if cfg.groups else None
        per = (((d0.tamo or {}).get("condensation") or {}).get("surfaces", [])) if d0 else []
        for st in ss:
            m = next((x for x in per if x["name"] == st.name), None)
            if m:
                st.ventilation.update({"predicted_margin_on_f": m["margin_on_f"] - st.uncertainty_f,
                                       "predicted_margin_off_f": m["margin_off_f"] - st.uncertainty_f,
                                       "predicted_surface_on_f": m["surface_on_f"],
                                       "horizon_min": cfg.temperature.horizon_min,
                                       "note": "Conservative margins if the exterior fans run, from the shared "
                                               "coupled model"})
        self.surface_status = ss
        if self.comparison:
            row = {"ts": now.isoformat()}
            for r in snap.readings:
                if r.mapping.sensor_id in coloc and r.ok and r.temp_c is not None and r.rh is not None:
                    row[r.mapping.sensor_id] = [r.temp_c, r.rh * 100]
            if len(row) > 2:
                self._cmp_samples = (self._cmp_samples + [row])[-3000:]

    def _circ_switch_verified(self, sid: str, now: datetime) -> tuple[bool, bool]:
        info = self.switch_info.get(sid)
        if not sid or info is None:
            return False, False
        verified = info.reachable and info.confirmed is not None
        fail = self._command_failures.get(sid)
        if fail and now < fail[1]:
            verified = False
        if info.commanded is not None and info.confirmed is not None and info.commanded != info.confirmed:
            verified = False
        return bool(info.confirmed), verified

    async def _circulate(self, now: datetime, local_hour: float) -> None:
        cfg = self.config
        cs = cfg.circulation
        sim = cfg.mode == Mode.SIMULATION
        ext_on = any(gs.is_on for gs in self.groups.values())
        drying = any(d.target_on and d.effect is not None and d.effect.net_removal_g_h > 0
                     for d in self.decisions.values())
        exterior = {"on": ext_on, "drying": ext_on and drying}
        for c in cfg.circulators:
            st = self.circ_states.setdefault(c.id, circulation.CircState())
            if st.manual is not None and st.manual_until is not None and now >= st.manual_until:
                st.manual, st.manual_until = None, None
                st.manual_ended = True
                self.storage.add_event(now, "override", "info", f"{c.name}: manual override expired", "CIRC_MANUAL_EXPIRED",
                                       {"circulator": c.id})
            if not sim:
                actual, verified = self._circ_switch_verified(c.switch_id, now)
                if actual != st.is_on:
                    st.last_change = now
                    self._circ_transition(c, actual, now)
                st.is_on, st.verified = actual, verified
            else:
                st.verified = True
            commanding = cs.mode == "auto" and c.auto_enabled and c.commissioned
            use = st
            if not commanding and st.manual is None:
                use = self.circ_shadow.setdefault(c.id, circulation.CircState(is_on=st.is_on, last_change=st.last_change))
                use.verified = st.verified or sim or not c.switch_id
            d = circulation.decide_circulator(c, cfg, self.zone_status, self.surface_status, use, now, exterior,
                                              self.dehumidifier_running(), local_hour)
            prev = self.circ_decisions.get(c.id)
            self.circ_decisions[c.id] = d
            if use is not st:  # shadow: track what would have happened
                if d.target_on != use.is_on:
                    use.is_on, use.last_change = d.target_on, now
                use.last_reason = d.reason_code
                # a fan the automation turned on earlier is switched off when automation stops commanding,
                # and a manual ON that ended returns the fan to OFF (shadow mode never switches it on)
                if (c.id in self._circ_auto_on or getattr(st, "manual_ended", False)) and st.is_on and not sim:
                    await self._command_circ(c, st, False, "CIRCULATION_AUTOMATION_STOPPED" if c.id in
                                             self._circ_auto_on else "CIRC_MANUAL_EXPIRED", now)
                    self._circ_auto_on.discard(c.id)
            else:
                st.last_reason = d.reason_code
                if sim:
                    if d.target_on != st.is_on:
                        st.is_on, st.last_change = d.target_on, now
                elif d.target_on != st.is_on or (st.manual is None and d.target_on is False and st.is_on):
                    if not (d.safety and d.reason_code == "PLUG_UNVERIFIED" and not st.is_on):
                        await self._command_circ(c, st, d.target_on, d.reason_code, now)
                        if d.target_on and st.manual is None:
                            self._circ_auto_on.add(c.id)
                        elif not d.target_on:
                            self._circ_auto_on.discard(c.id)
            if getattr(st, "manual_ended", False) and not st.is_on:
                st.manual_ended = False
            if prev is None or prev.reason_code != d.reason_code or prev.target_on != d.target_on:
                shadow = use is not st
                text = d.explanation
                if shadow:
                    text = f"{c.name} (shadow, not switched) {'would turn ON' if d.target_on else 'would stay OFF'}: " + \
                        text.split(": ", 1)[-1]
                else:
                    text = f"{c.name} {text}"
                self.storage.add_event(now, "circulation", "warning" if d.benefit == "adverse" else "info", text,
                                       "CIRC:" + d.reason_code,
                                       {"circulator": c.id, "target_on": d.target_on, "shadow": shadow,
                                        "benefit": d.benefit, "reasons": d.reasons,
                                        "predictions": d.predictions})

    async def _command_circ(self, c, st, on: bool, reason: str, now: datetime) -> None:
        if not c.switch_id:
            return
        failed = self._command_failures.get(c.switch_id)
        if failed and failed[2] == on and now < failed[1]:
            return  # locked out after a failed command: no rapid retries
        res = await self.switches.set(c.switch_id, on)
        self._record_status(res, now, commanded=on)
        ok = res.reachable and res.on == on
        details = {"switch": c.switch_id, "circulator": c.id, "command": "on" if on else "off",
                   "confirmed_state": res.on, "reachable": res.reachable, "error": res.error, "reason_code": reason}
        if ok:
            self._command_failures.pop(c.switch_id, None)
            self.storage.add_event(now, "command", "info", f"{c.name} switched {'ON' if on else 'OFF'}", reason, details)
            if on != st.is_on:
                st.last_change = now
                self._circ_transition(c, on, now)
            st.is_on = on
        else:
            msg = res.error or f"device reports {'ON' if res.on else 'OFF'} after command"
            self.storage.add_event(now, "fault", "critical",
                                   f"Command failure: {c.name} did not confirm {'ON' if on else 'OFF'} ({msg}). Its "
                                   f"actual state is uncertain; retrying in {COMMAND_LOCKOUT_MIN} minutes.",
                                   "COMMAND_FAILURE", details)
            self._command_failures[c.switch_id] = (msg, now + timedelta(minutes=COMMAND_LOCKOUT_MIN), on)
            st.verified = False

    def _zone_snapshot(self) -> dict:
        return {zid: {"t_c": z.air.t_c, "rh_pct": z.air.rh * 100, "dew_point_c": z.air.dew_point_c,
                      "w_g_kg": z.air.w * 1000} for zid, z in self.zone_status.items() if z.air is not None}

    def _circ_transition(self, c, on: bool, now: datetime) -> None:
        """Record completed circulator runs with before/after conditions (for effectiveness learning)."""
        if on:
            self._circ_cycle[c.id] = {"ts": now, "start": self._zone_snapshot(),
                                      "reason": (self.circ_decisions.get(c.id).reason_code
                                                 if self.circ_decisions.get(c.id) else None),
                                      "surfaces": {s.sensor_id: s.margin_f for s in self.surface_status}}
            return
        start = self._circ_cycle.pop(c.id, None)
        if not start:
            return
        outd = next((d.metrics.get("outdoor") for d in self.decisions.values() if d.metrics.get("outdoor")), None)
        self.storage.add_circulation_cycle({
            "circulator_id": c.id, "ts_start": start["ts"], "ts_end": now, "reason": start["reason"],
            "start": start["start"], "end": self._zone_snapshot(),
            "context": {"exterior_on": any(gs.is_on for gs in self.groups.values()),
                        "dehumidifier_running": self.dehumidifier_running(), "outdoor": outd,
                        "surface_margins_start_f": start["surfaces"],
                        "surface_margins_end_f": {s.sensor_id: s.margin_f for s in self.surface_status},
                        "other_circulators_on": [k for k, v in self.circ_states.items() if v.is_on and k != c.id],
                        "connections": [k.model_dump() for k in self.config.connections if k.circulator_id == c.id]},
        })

    def set_circulator_manual(self, cid: str, state: str, minutes: float | None = None, who: str = "user") -> None:
        c = next((x for x in self.config.circulators if x.id == cid), None)
        if c is None:
            raise KeyError(cid)
        st = self.circ_states.setdefault(cid, circulation.CircState())
        now = self.clock.now()
        if state == "auto":
            if st.manual is not None:
                st.manual_ended = True
            st.manual, st.manual_until = None, None
            msg = f"{c.name}: returned to automatic control"
        else:
            dur = minutes or self.config.circulation.manual_override_minutes
            st.manual, st.manual_until = state == "on", now + timedelta(minutes=dur)
            msg = f"{c.name}: manual {state.upper()} for {dur:.0f} min"
        self.storage.add_event(now, "override", "info", msg, "CIRC_MANUAL", {"by": who, "circulator": cid,
                                                                          "state": state, "minutes": minutes})

    def set_circulation_mode(self, mode: str, who: str = "user") -> None:
        old = self.config.circulation.mode
        if old == mode:
            return
        self.config.circulation.mode = mode  # type: ignore[assignment]
        self.circ_shadow.clear()
        self.storage.save_config(self.config)
        self.storage.add_event(self.clock.now(), "config", "warning", f"Circulation automation changed from {old} to "
                               f"{mode}", "CIRCULATION_MODE", {"by": who, "from": old, "to": mode})

    # sensor side-by-side comparison (commissioning)
    def comparison_start(self, sensor_ids: list[str]) -> dict:
        now = self.clock.now()
        self.comparison = {"sensor_ids": sensor_ids, "started_at": now.isoformat()}
        self._cmp_samples = []
        self.storage.set_kv("sensor_comparison", self.comparison)
        self.storage.add_event(now, "config", "info", f"Sensor comparison started: {', '.join(sensor_ids)}. Their zone "
                               "readings are not used for control until it is stopped.", "SENSOR_COMPARISON_START")
        return self.comparison_status()

    def comparison_status(self) -> dict:
        if not self.comparison:
            return {"active": False}
        ids = self.comparison["sensor_ids"]
        samples = {sid: [tuple(r[sid]) for r in self._cmp_samples if all(i in r for i in ids)] for sid in ids}
        res = zones_mod.compare_sensors(samples, rh_threshold_pct=2 * self.config.circulation.rh_uncertainty_calibrated_pct)
        started = datetime.fromisoformat(self.comparison["started_at"])
        return {"active": True, **self.comparison, "hours": (self.clock.now() - started).total_seconds() / 3600,
                "result": res, "recommended_hours": [24, 48]}

    def comparison_stop(self, apply: bool = False) -> dict:
        res = self.comparison_status()
        now = self.clock.now()
        applied = []
        prev_offsets: list[dict] = []
        if apply and res.get("result", {}).get("ok"):
            for m in self.config.sensors.mappings:
                r = res["result"]["sensors"].get(m.sensor_id)
                if r and r.get("stable"):
                    prev_offsets.append({"sensor_id": m.sensor_id, "temp_offset_f": m.temp_offset_f,
                                         "rh_offset_pct": m.rh_offset_pct, "calibrated_at": m.calibrated_at})
                    m.temp_offset_f = round(max(-10, min(10, m.temp_offset_f + r["suggested_temp_offset_f"])), 1)
                    m.rh_offset_pct = round(max(-15, min(15, m.rh_offset_pct + r["suggested_rh_offset_pct"])), 1)
                    m.calibrated_at = now.isoformat()
                    applied.append(m.sensor_id)
            self.storage.save_config(self.config)
        self.storage.add_event(now, "config", "info",
                               "Sensor comparison stopped" + (f"; offsets applied to {', '.join(applied)}" if applied else ""),
                               "SENSOR_COMPARISON_STOP", {"result": res.get("result"), "applied": applied,
                                                          "previous_offsets": prev_offsets})
        if self._cmp_samples:
            self.storage.set_kv("sensor_comparison_last", {"result": res.get("result"), "ended_at": now.isoformat(),
                                                           "applied": applied})
        self.comparison = None
        self._cmp_samples = []
        for z in self.zone_status.values():
            z.colocated = False  # refreshed on the next reading
        self.storage.set_kv("sensor_comparison", None)
        self.storage.set_kv("sensor_comparison_samples", [])
        return {**res, "applied": applied}

    def circulator_runtime(self) -> dict:
        """Runtime today and this month per circulator from recorded history (cached for 5 minutes)."""
        now = self.clock.now()
        if self._runtime_cache and now - self._runtime_cache[0] < timedelta(minutes=5):
            return self._runtime_cache[1]
        tz = ZoneInfo(self.config.system.timezone)
        local = now.astimezone(tz)
        day0 = local.replace(hour=0, minute=0, second=0, microsecond=0)
        month0 = day0.replace(day=1)
        rows = self.storage.history(month0, now, max_points=10 ** 7)
        out = {c.id: {"today_h": 0.0, "month_h": 0.0, "month_kwh": 0.0} for c in self.config.circulators}
        for i in range(1, len(rows)):
            a, b = rows[i - 1], rows[i]
            ta, tb = datetime.fromisoformat(a["ts"]), datetime.fromisoformat(b["ts"])
            hrs = min((tb - ta).total_seconds() / 3600, 0.25)
            for cid, v in (a.get("circ") or {}).items():
                if cid in out and v.get("on"):
                    out[cid]["month_h"] += hrs
                    if ta >= day0:
                        out[cid]["today_h"] += hrs
                    c = next(x for x in self.config.circulators if x.id == cid)
                    out[cid]["month_kwh"] += (v.get("power_w") if v.get("power_w") else c.rated_w) * hrs / 1000
        self._runtime_cache = (now, out)
        return out

    # ------------------------------------------------------------ TAMO support
    def thermal_params(self) -> thermal.ThermalParams:
        return thermal.params_from_config(self.config, self.thermal_learned)

    def set_policy(self, policy: ControlPolicy, who: str = "user") -> None:
        old = self.config.control.policy
        if old == policy:
            return
        self.config.control.policy = policy
        self.shadow_groups.clear()
        self.shadow_decisions.clear()
        self._agree.clear()
        self.storage.save_config(self.config)
        self.storage.add_event(self.clock.now(), "config", "warning", f"Control policy changed from "
                               f"{_policy_name(old)} to {_policy_name(policy)}", "POLICY_CHANGE",
                               {"by": who, "from": old.value, "to": policy.value})

    def _shadow(self, g: FanGroup, cond, gs: GroupState, d: Decision, now: datetime, local_hour: float,
                tp: thermal.ThermalParams) -> None:
        cfg = self.config
        if not cfg.control.shadow_evaluation or cfg.mode not in (Mode.AUTO, Mode.SIMULATION):
            self.shadow_decisions.pop(g.id, None)
            return
        other = ControlPolicy.MOISTURE if cfg.control.policy == ControlPolicy.TAMO else ControlPolicy.TAMO
        sgs = self.shadow_groups.get(g.id)
        if sgs is None:
            sgs = self.shadow_groups[g.id] = GroupState(is_on=gs.is_on, verified=gs.verified,
                                                        last_change=gs.last_change)
        sgs.verified = gs.verified
        sd = decide(cond, sgs, cfg, g, now, mode=Mode.SIMULATION, local_hour=local_hour, policy=other,
                    thermal_params=tp)
        _track_deferral(sgs, sd, now)
        if sd.target_on != sgs.is_on:
            sgs.is_on = sd.target_on
            sgs.last_change = now
        self.shadow_decisions[g.id] = sd
        agree = sd.target_on == d.target_on
        if self._agree.get(g.id) != agree:
            self._agree[g.id] = agree
            self.storage.add_event(
                now, "shadow", "info",
                (f"{_policy_name(other)} (shadow) agrees: fans {'ON' if d.target_on else 'OFF'}" if agree else
                 f"{_policy_name(other)} (shadow) would turn fans {'ON' if sd.target_on else 'OFF'}: {sd.explanation}"),
                "SHADOW_AGREE" if agree else "SHADOW_DISAGREE",
                {"group": g.id, "in_control": d.policy, "in_control_reason": d.reason_code,
                 "shadow": sd.policy, "shadow_reason": sd.reason_code, "tamo": _tamo_summary(sd if sd.tamo else d)})

    def _record_prediction(self, gid: str, cond, d: Decision, now: datetime, tp: thermal.ThermalParams) -> None:
        t = d.tamo
        if not t or cond.indoor is None:
            return
        pr = t["prediction"]
        if THERMAL_CHECK_MIN not in pr["minutes"]:
            return
        i = pr["minutes"].index(THERMAL_CHECK_MIN)
        from .units import f_to_c
        row = {"ts_start": now, "ts_due": now + timedelta(minutes=THERMAL_CHECK_MIN), "group_id": gid,
               "horizon_min": THERMAL_CHECK_MIN, "start_t_c": cond.indoor.t_c, "inlet_t_c": t["inlet_t_c"],
               "predicted_c": f_to_c(pr["on_f"][i]), "predicted_conservative_c": f_to_c(pr["on_conservative_f"][i]),
               "model_version": calibration.THERMAL_MODEL_VERSION, "confidence": tp.confidence,
               "params": tp.to_dict()}
        row["id"] = self.storage.add_thermal_prediction(row)
        self._pending_preds.append(row)

    def _check_predictions(self, gid: str, gs: GroupState, cond, now: datetime) -> None:
        keep = []
        for p in self._pending_preds:
            if p["group_id"] != gid:
                keep.append(p)
                continue
            interrupted = not gs.is_on or (gs.last_change is not None and gs.last_change > p["ts_start"])
            if interrupted:
                self.storage.resolve_thermal_prediction(p["id"], None, None)
                continue
            if now < p["ts_due"]:
                keep.append(p)
                continue
            if cond.indoor is None:
                self.storage.resolve_thermal_prediction(p["id"], None, None)
                continue
            err_f = (cond.indoor.t_c - p["predicted_c"]) * 1.8  # negative = cooled more than predicted
            self.storage.resolve_thermal_prediction(p["id"], cond.indoor.t_c, err_f)
            self._thermal_error(err_f, cond.indoor.t_c < p["predicted_conservative_c"] - 0.15, now)
        self._pending_preds = keep

    def _thermal_error(self, err_f: float, beyond_conservative: bool, now: datetime) -> None:
        self._thermal_errors = (self._thermal_errors + [err_f])[-10:]
        learned = dict(self.thermal_learned or {})
        extra = float(learned.get("extra_uncertainty", 0.0))
        recent = self._thermal_errors[-3:]
        bad = beyond_conservative or (len(recent) >= 3 and abs(sum(recent) / 3) > 1.0)
        if bad:
            extra = min(0.6, extra + 0.2)
            self.storage.add_event(now, "thermal", "warning",
                                   f"Thermal prediction error: basement temperature changed {abs(err_f):.1f}°F "
                                   f"{'more' if err_f < 0 else 'less'} than predicted. Using more conservative limits "
                                   "and refitting the model.", "THERMAL_PREDICTION_ERROR",
                                   {"error_f": err_f, "recent_errors_f": self._thermal_errors,
                                    "extra_uncertainty": extra})
            self._last_fit = None  # refit at the next opportunity
        elif abs(err_f) < 0.5 and extra > 0:
            extra = max(0.0, extra - 0.05)
        learned["extra_uncertainty"] = extra
        learned["recent_errors_f"] = self._thermal_errors
        self.thermal_learned = learned
        self.storage.set_kv("thermal_model", learned)

    def _maybe_refit(self, now: datetime, force: bool = False) -> dict | None:
        cfg = self.config
        if not force and (not cfg.temperature.learning_enabled or cfg.mode == Mode.SIMULATION):
            return None
        if not force and self._last_fit and now - self._last_fit < timedelta(hours=THERMAL_REFIT_H):
            return None
        self._last_fit = now
        rows = self.storage.history(now - timedelta(days=60), now, max_points=10 ** 7)
        fit = calibration.fit_thermal(rows, cfg, cfg.temperature.horizon_min)
        learned = dict(self.thermal_learned or {})
        hist = list(learned.get("history", []))[-9:]
        if fit.get("coupling_w_k"):
            hist.append({"fitted_at": now.isoformat(), "coupling_w_k": fit["coupling_w_k"],
                         "confidence": fit["confidence"], "cycles": fit["cycles_used"]})
        learned.update({k: fit.get(k) for k in ("coupling_w_k", "low_w_k", "high_w_k", "uncertainty", "confidence",
                                                "cycles_used", "cycles_found", "observed", "version")})
        learned["fitted_at"] = now.isoformat()
        learned["history"] = hist
        self.thermal_learned = learned
        self.storage.set_kv("thermal_model", learned)
        sfit = surf_mod.fit_air_follow(rows)
        sfit["fitted_at"] = now.isoformat()
        self.surface_learned = sfit
        self.storage.set_kv("surface_model", sfit)
        return fit

    def refit_thermal(self) -> dict:
        return self._maybe_refit(self.clock.now(), force=True) or {}

    def rh_attribution(self) -> dict | None:
        if len(self._indoor_hist) < 2:
            return None
        now, cur = self._indoor_hist[-1]
        then = next(((t, a) for t, a in self._indoor_hist if now - t <= timedelta(minutes=65)), None)
        if then is None or then[0] == now:
            return None
        return {"minutes": (now - then[0]).total_seconds() / 60, **tamo.attribute_rh_change(then[1], cur)}

    async def _apply(self, gid: str, gs: GroupState, d: Decision, now: datetime) -> None:
        if self.config.mode == Mode.SIMULATION:
            if d.target_on != gs.is_on:
                gs.is_on = d.target_on
                gs.last_change = now
            return
        fans = [f for f in self.config.fans if f.group == gid and f.enabled]
        for f in fans:
            info = self.switch_info.setdefault(f.switch_id, SwitchInfo(f.switch_id))
            if info.confirmed == d.target_on and info.reachable and info.commanded == d.target_on:
                continue
            if info.confirmed == d.target_on and info.reachable and info.commanded is None:
                info.commanded = d.target_on
                continue
            failed = self._command_failures.get(f.switch_id)
            if failed and failed[2] == d.target_on and now < failed[1]:
                continue  # locked out after a failed command; no retry until the lockout ends
            res = await self.switches.set(f.switch_id, d.target_on)
            self._record_status(res, now, commanded=d.target_on)
            ok = res.reachable and res.on == d.target_on
            details = {"switch": f.switch_id, "fan": f.id, "command": "on" if d.target_on else "off",
                       "confirmed_state": res.on, "reachable": res.reachable, "error": res.error,
                       "reason_code": d.reason_code}
            if ok:
                if failed is None or failed[2] == d.target_on:
                    self._command_failures.pop(f.switch_id, None)
                self.storage.add_event(now, "command", "info",
                                       f"{f.name} switched {'ON' if d.target_on else 'OFF'}", d.reason_code, details)
            else:
                msg = res.error or f"device reports {'ON' if res.on else 'OFF'} after command"
                self.storage.add_event(now, "fault", "critical",
                                       f"Command failure: {f.name} did not confirm "
                                       f"{'ON' if d.target_on else 'OFF'} ({msg}). Automatic control of this "
                                       f"group is locked out for {COMMAND_LOCKOUT_MIN} minutes.",
                                       "COMMAND_FAILURE", details)
                self._command_failures[f.switch_id] = (msg, now + timedelta(minutes=COMMAND_LOCKOUT_MIN),
                                                       d.target_on)
        on, verified = self._group_switch_state(gid)
        if on != gs.is_on:
            gs.last_change = now
            if on:
                self._cycle_times.append(now)
        gs.is_on, gs.verified = on, verified

    async def _rearm_watchdogs(self, now: datetime) -> None:
        """Keep the plugs' hardware auto-off timers from expiring while fans should run.

        The adapter decides when a re-arm is actually needed. If this process stops,
        nobody re-arms the timers and the plugs switch off on their own.
        """
        rearm = getattr(self.switches, "rearm", None)
        if not self.config.switches.hardware_max_on_minutes or rearm is None or self.config.mode == Mode.SIMULATION:
            return
        for f in self.config.fans:
            info = self.switch_info.get(f.switch_id)
            if f.enabled and info and info.reachable and info.confirmed and info.commanded:
                await rearm(f.switch_id, now)

    async def _read_dehumidifier(self) -> None:
        dh = self.config.dehumidifier
        if dh.enabled and dh.monitor_switch_id:
            self.dehum_status = await self.switches.get(dh.monitor_switch_id)  # read only, never switched
        else:
            self.dehum_status = None

    def dehumidifier_running(self) -> bool | None:
        st = self.dehum_status
        if st is None or not st.reachable or st.power_w is None:
            return None
        return st.power_w >= self.config.dehumidifier.running_threshold_w

    # ------------------------------------------------------------ helpers
    def _switch_ids(self) -> list[str]:
        return sorted({f.switch_id for f in self.config.fans if f.switch_id} |
                      {c.switch_id for c in self.config.circulators if c.switch_id and c.enabled})

    def _record_status(self, st: SwitchStatus, now: datetime, commanded: bool | None = None) -> None:
        info = self.switch_info.setdefault(st.switch_id, SwitchInfo(st.switch_id))
        if commanded is not None:
            info.commanded = commanded
        info.reachable = st.reachable
        info.error = st.error
        if st.reachable:
            if st.on and not info.confirmed:
                info.on_since = now
            if not st.on:
                info.on_since = None
            info.confirmed = st.on
            info.power_w = st.power_w
            info.last_ok = now
        # unreachable: keep last confirmed value as "last known", but it is no longer verified

    def _group_switch_state(self, gid: str) -> tuple[bool, bool]:
        fans = [f for f in self.config.fans if f.group == gid and f.enabled]
        infos = [self.switch_info.get(f.switch_id) for f in fans]
        verified = all(i is not None and i.reachable and i.confirmed is not None for i in infos)
        on = any(i is not None and i.confirmed for i in infos)
        now = self.clock.now()
        if any(f.switch_id in self._command_failures and now < self._command_failures[f.switch_id][1] for f in fans):
            verified = False
        # a commanded state the device did not confirm also makes the group unverified
        if any(i is not None and i.commanded is not None and i.confirmed is not None and i.commanded != i.confirmed
               for i in infos):
            verified = False
        return on, verified

    def _expire_manual(self, now: datetime) -> None:
        if self.config.mode != Mode.MANUAL_ON:
            return
        for gs in self.groups.values():
            if gs.manual_until and now >= gs.manual_until:
                self.storage.add_event(now, "override", "info",
                                       f"Manual override expired; returning to {self.previous_mode.value.upper()}",
                                       "MANUAL_EXPIRED")
                self.config.mode = self.previous_mode
                for g in self.groups.values():
                    g.manual_until = None
                    g.maintenance = False
                self.storage.save_config(self.config)
                return

    def set_mode(self, mode: Mode, minutes: float | None = None, maintenance: bool = False, who: str = "user") -> None:
        now = self.clock.now()
        if mode == Mode.MANUAL_ON:
            if self.config.mode != Mode.MANUAL_ON:
                self.previous_mode = self.config.mode
            dur = minutes or self.config.control.manual_override_minutes
            for gs in self.groups.values():
                gs.manual_until = now + timedelta(minutes=dur)
                gs.maintenance = maintenance
            msg = f"Manual ON for {dur:.0f} min" + (" in MAINTENANCE mode (interlocks bypassed)" if maintenance else "")
            self.storage.add_event(now, "override", "warning" if maintenance else "info", msg, "MANUAL_ON",
                                   {"by": who, "minutes": dur, "maintenance": maintenance})
        else:
            for gs in self.groups.values():
                gs.manual_until = None
                gs.maintenance = False
            self.storage.add_event(now, "override", "info", f"Mode set to {mode.value.upper()}", "MODE_CHANGE",
                                   {"by": who, "from": self.config.mode.value})
        if mode == Mode.SIMULATION or self.config.mode == Mode.SIMULATION:
            for gs in self.groups.values():
                gs.last_change = None
        self.config.mode = mode
        self.storage.save_config(self.config)

    def apply_config(self, new: AppConfig) -> None:
        old_mode = self.config.mode
        self.config = new
        self.config.mode = old_mode if new.mode == old_mode else new.mode
        for g in new.groups:
            self.groups.setdefault(g.id, GroupState())
        self.storage.save_config(self.config)
        self.storage.add_event(self.clock.now(), "config", "info", "Settings updated", "CONFIG_CHANGE")

    # ------------------------------------------------------------ alerts
    def _set_alert(self, now: datetime, aid: str, active: bool, severity: str = "warning", message: str = "") -> None:
        if active and aid not in self.alerts:
            self.alerts[aid] = Alert(aid, severity, message, now)
            self.storage.add_event(now, "alert", severity, message, aid)
        elif active:
            self.alerts[aid].message = message
        elif not active and aid in self.alerts:
            del self.alerts[aid]
            self.storage.add_event(now, "alert", "info", f"Cleared: {aid}", aid + "_CLEARED")

    def _update_alerts(self, now: datetime) -> None:
        cfg = self.config
        snap = self.snapshot
        self._set_alert(now, "GATEWAY_ERROR", bool(self._gateway_error), "critical",
                        f"Sensor gateway unreachable: {self._gateway_error}")
        if snap:
            for r in snap.readings:
                bad = r.status in ("stale", "missing", "implausible")
                self._set_alert(now, f"SENSOR_OFFLINE:{r.mapping.sensor_id}", bad, "critical", r.message)
                self._set_alert(now, f"BATTERY_LOW:{r.mapping.sensor_id}", bool(r.battery_low), "warning",
                                f"{r.mapping.name}: battery low")
        for sid, info in self.switch_info.items():
            self._set_alert(now, f"SWITCH_OFFLINE:{sid}", not info.reachable, "critical",
                            f"Smart plug {sid} is not responding. Last known state: "
                            f"{'ON' if info.confirmed else 'OFF' if info.confirmed is not None else 'unknown'} (unverified).")
            fail = self._command_failures.get(sid)
            if fail and now >= fail[1]:
                fail = None
            self._set_alert(now, f"COMMAND_FAILURE:{sid}", fail is not None, "critical",
                            f"Fan command failure on {sid}: {fail[0] if fail else ''}. Check the plug and fan.")
            uncommanded = info.reachable and info.confirmed and info.commanded is False
            self._set_alert(now, f"UNCOMMANDED_RUN:{sid}", bool(uncommanded), "critical",
                            f"Fan on {sid} is running without a controller command")
            fans = [f for f in cfg.fans if f.switch_id == sid]
            rated = sum(f.rated_w for f in fans) + sum(c.rated_w for c in cfg.circulators if c.switch_id == sid)
            odd = (info.reachable and info.confirmed and info.power_w is not None and rated > 0
                   and not (0.3 * rated <= info.power_w <= 1.6 * rated))
            self._set_alert(now, f"UNEXPECTED_POWER:{sid}", bool(odd), "warning",
                            f"Unexpected power draw on {sid}: {info.power_w or 0:.0f} W (rated {rated:.0f} W)")

        # humidity duration alerts (per group indoor reading)
        for gid, d in self.decisions.items():
            ind = d.metrics.get("indoor")
            rh = ind["rh_pct"] if ind else None
            for thr, aid, hours, sev in ((cfg.control.upper_rh_pct, "RH_ABOVE_UPPER", 12, "warning"),
                                         (cfg.control.high_rh_alert_pct, "RH_ABOVE_HIGH", 6, "critical")):
                key = f"{aid}:{gid}"
                if rh is not None and rh > thr:
                    self._rh_above.setdefault(key, now)
                    since = self._rh_above[key] or now
                    dur = (now - since).total_seconds() / 3600
                    self._set_alert(now, key, dur >= hours, sev,
                                    f"Basement RH has been above {thr:.0f}% for {dur:.0f} hours. Mold risk rises with "
                                    "duration. If ventilation is not helping, consider a dehumidifier and check for "
                                    "water intrusion.")
                elif rh is not None:
                    self._rh_above.pop(key, None)
                    self._set_alert(now, key, False)
            surf_c = d.metrics.get("surface_c")
            zoned_probes = any(s.local_source == "zone" for s in self.surface_status)
            if ind and surf_c is not None and d.metrics.get("surface_source") == "measured" and not zoned_probes:
                # hysteresis: raise within 1°F of the dew point, clear only once 2°F clear
                active = f"CONDENSATION_SUSPECTED:{gid}" in self.alerts
                band = delta_f_to_c(2.0 if active else 1.0)
                risk = surf_c <= ind["dew_point_c"] + band
                self._set_alert(now, f"CONDENSATION_SUSPECTED:{gid}", risk, "critical",
                                "Measured surface temperature is at or near the basement dew point: "
                                "condensation likely on cool surfaces.")
            self._set_alert(now, f"CONDENSATION_UNVERIFIED:{gid}", d.reason_code.endswith("CONDENSATION_UNVERIFIED"),
                            "warning", "Automatic ventilation is blocked until a surface sensor or a conservative "
                            "minimum surface temperature is configured (Settings > Environmental).")
        for gid, d in self.decisions.items():
            prim = d.reason_code.split(":")[-1]
            for code, sev in SAFETY_ALERT_CODES.items():
                self._set_alert(now, f"{code}:{gid}", prim == code, sev, d.explanation)
            ind = d.metrics.get("indoor")
            rh = ind["rh_pct"] if ind else None
            key = f"HIGH_RH_NO_SAFE_DRYING:{gid}"
            stuck = rh is not None and rh > cfg.control.high_rh_alert_pct and not d.target_on and prim in NO_SAFE_DRYING_CODES
            if stuck:
                since = self._no_safe_since.setdefault(gid, now)
                hrs = (now - since).total_seconds() / 3600
                self._set_alert(now, key, hrs >= 3, "critical",
                                f"Basement RH is {rh:.0f}% and outdoor air has not offered safe, worthwhile drying for "
                                f"{hrs:.0f} hours (latest reason: {prim}). Ventilation cannot fix this on its own, and "
                                "internal circulation fans only move moisture between zones: run a dehumidifier and "
                                "look for the moisture source.")
            else:
                self._no_safe_since.pop(gid, None)
                self._set_alert(now, key, False)
        outd = next((d.metrics.get("outdoor") for d in self.decisions.values() if d.metrics.get("outdoor")), None)
        pipes = [m for m in cfg.sensors.mappings if m.role == SensorRole.PIPE and m.enabled]
        cold = outd is not None and outd["t_c"] * 1.8 + 32 <= cfg.temperature.freeze_warning_f
        self._set_alert(now, "PIPES_UNMONITORED", cold and not pipes, "warning",
                        "Outdoor air is near freezing and no pipe sensor is configured. The basement minimum protects "
                        "average air temperature only; pipes near vents can be colder.")
        for f in cfg.fans:
            self._set_alert(now, f"MOTOR_LOAD:{f.id}", f.enabled and not f.motor_load_ok, "warning",
                            f"{f.name}: smart switch is not confirmed as rated for this motor load.")
        self._surface_alerts(now)
        cs = cfg.circulation
        for zid, z in self.zone_status.items():
            crit = (z.minutes_above or {}).get("critical") or 0
            self._set_alert(now, f"ZONE_RH_CRITICAL:{zid}", crit >= cs.critical_persist_min, "warning",
                            f"{z.name}: RH has been above {cs.critical_rh_pct:.0f}% for {crit / 60:.1f} h. Circulation "
                            "can only redistribute moisture; check whether exterior ventilation or a dehumidifier can "
                            "remove it.")
        for c in cfg.circulators:
            self._set_alert(now, f"CIRCULATOR_NOT_COMMISSIONED:{c.id}",
                            cs.mode == "auto" and c.enabled and c.auto_enabled and not c.commissioned, "info",
                            f"{c.name}: automatic control waits until the fan is commissioned (restarts after power "
                            "loss, plug rated for the motor, kept above flood level). Decisions are shadow-only.")
        dh = cfg.dehumidifier
        if dh.enabled and dh.monitor_switch_id:
            st = self.dehum_status
            self._set_alert(now, "DEHUMIDIFIER_OFFLINE", st is not None and not st.reachable, "warning",
                            f"Dehumidifier plug {dh.monitor_switch_id} is not responding")
        poll_err = getattr(self.sensors, "last_poll_error", None)
        self._set_alert(now, "ECOWITT_POLL", bool(poll_err) and not self._gateway_error, "warning",
                        f"Ecowitt polling failed ({poll_err}); using pushed data only")
        self._cycle_times = [t for t in self._cycle_times if now - t <= timedelta(hours=1)]
        self._set_alert(now, "EXCESSIVE_SWITCHING", len(self._cycle_times) > cfg.control.max_cycles_per_hour_alert,
                        "warning", f"{len(self._cycle_times)} fan starts in the last hour")
        self._set_alert(now, "WATER_INTRUSION_LOCKOUT", cfg.control.water_intrusion_lockout, "warning",
                        "Water-intrusion lockout is active; ventilation is blocked.")
        watchdog = self.switches.supports_hardware_auto_off() and cfg.switches.hardware_max_on_minutes
        self._set_alert(now, "NO_HARDWARE_WATCHDOG", self.data_source != "mock" and not watchdog, "info",
                        "No hardware auto-off timer is configured on the smart plugs. If this computer stops, "
                        "fans that are ON will stay ON.")

    def _surface_alerts(self, now: datetime) -> None:
        ss = self.config.surfaces
        note = " This is a thermodynamic possibility, not a detection of liquid water."
        for s in self.surface_status:
            sid = s.sensor_id
            self._set_alert(now, f"SURFACE_PROBE_UNAVAILABLE:{sid}", s.t_c is None, "warning",
                            s.message or f"{s.name}: surface probe unavailable")
            have = s.conservative_margin_f is not None
            mins = s.minutes or {}
            adv = have and s.risk == "monitor" and (mins.get("below_monitor") or 0) >= ss.advisory_sustain_min
            m = f"{s.margin_f:.1f}°F (±{s.uncertainty_f:.1f}°F)" if have else ""
            cause = f" {s.cause}" if s.cause else ""
            self._set_alert(now, f"SURFACE_ADVISORY:{sid}", bool(adv), "info",
                            f"{s.name}: condensation margin {m} has been under {ss.monitor_margin_f:.0f}°F for a while."
                            f"{cause}")
            self._set_alert(now, f"SURFACE_WARNING:{sid}", have and s.risk == "elevated", "warning",
                            f"{s.name}: condensation margin {m} is under {ss.elevated_margin_f:.0f}°F.{cause}{note}")
            self._set_alert(now, f"SURFACE_HIGH_RISK:{sid}", have and s.risk == "possible", "critical",
                            f"{s.name}: surface temperature is at or below the local dew point allowing for sensor "
                            f"uncertainty (margin {m}). Condensation is possible.{cause}{note}")
            persist = have and (mins.get("near_saturation") or 0) >= ss.persistent_hours * 60
            self._set_alert(now, f"SURFACE_PERSISTENT:{sid}", bool(persist), "critical",
                            f"{s.name}: near-saturation conditions for over {ss.persistent_hours:.0f} h. Sustained damp "
                            "surfaces can grow mold before any water is visible.")
            v = s.ventilation or {}
            self._set_alert(now, f"OUTDOOR_AIR_SURFACE_RISK:{sid}",
                            have and bool(v.get("outdoor_air_would_condense")) and s.risk in ("lower", "monitor"),
                            "info", f"{s.name}: outdoor air (dew point {v.get('outdoor_dew_point_c', 0) * 1.8 + 32:.0f}°F) "
                            "would condense on this surface if brought in, so exterior ventilation is held off. "
                            "Condensation is not occurring now: the basement air is drier.")

    # ------------------------------------------------------------ history
    def _write_history(self, now: datetime) -> None:
        cfg = self.config
        if not cfg.groups or not self.snapshot or not self.decisions:
            return
        # basement conditions across all zones, regardless of fan grouping
        allc = conditions_for_group(self.snapshot, FanGroup(id="_all", name="all"), cfg)
        first = self.decisions.get(cfg.groups[0].id)
        ind = allc.indoor.to_dict() if allc.indoor else None
        out = allc.outdoor.to_dict() if allc.outdoor else None
        sim = cfg.mode == Mode.SIMULATION
        on_count = 0
        power = 0.0
        all_verified = True
        for f in cfg.fans:
            if not f.enabled:
                continue
            info = self.switch_info.get(f.switch_id)
            if sim:
                on_count += 1 if self.groups.get(f.group, GroupState()).is_on else 0
                continue
            if info and info.confirmed:
                on_count += 1
            if info and info.power_w:
                power += info.power_w
            if not (info and info.reachable):
                all_verified = False
        removal = potential = penalty = cond_cfm = 0.0
        have_potential = False
        groups = {}
        for g in cfg.groups:
            d = self.decisions.get(g.id)
            gs = self.groups.get(g.id, GroupState())
            if d is None:
                continue
            groups[g.id] = {"on": gs.is_on, "reason_code": d.reason_code}
            if d.effect:
                have_potential = True
                potential += d.effect.net_removal_g_h
                if gs.is_on:
                    removal += d.effect.net_removal_g_h
                    penalty += d.effect.conditioned_air_penalty_w
                    cond_cfm += d.effect.upstairs_path_cfm
        zones = {}
        for r in self.snapshot.readings:
            if r.mapping.role == SensorRole.BASEMENT and r.ok:
                zones[r.mapping.zone or r.mapping.sensor_id] = {"t_c": r.temp_c, "rh": r.rh}
        for zid, z in self.zone_status.items():
            if z.air is not None:
                zones[zid] = {"t_c": z.air.t_c, "rh": z.air.rh, "td_c": z.air.dew_point_c, "w": z.air.w,
                              "coloc": z.colocated or None}
        circ = {}
        for c in cfg.circulators:
            st = self.circ_states.get(c.id)
            dd = self.circ_decisions.get(c.id)
            info = self.switch_info.get(c.switch_id)
            circ[c.id] = {"on": int(bool(st and st.is_on)), "target": int(bool(dd and dd.target_on)),
                          "reason": dd.reason_code if dd else None, "commanding": bool(dd and dd.commanding),
                          "power_w": info.power_w if info and info.reachable and st and st.is_on else None}
        surfaces = {}
        for sst in self.surface_status:
            if sst.t_c is None:
                continue
            surfaces[sst.sensor_id] = {"t_c": sst.t_c, "dew_c": sst.local.dew_point_c if sst.local else None,
                                       "margin_f": sst.margin_f, "cons_margin_f": sst.conservative_margin_f,
                                       "srh": sst.surface_rh, "risk": sst.risk, "zone": sst.zone}
        dh = self.dehum_status
        self.storage.add_history(now, {
            "source": self.data_source,
            "indoor_t_c": ind["t_c"] if ind else None, "indoor_rh": ind["rh_pct"] if ind else None,
            "indoor_td_c": ind["dew_point_c"] if ind else None, "indoor_w": ind["w"] if ind else None,
            "outdoor_t_c": out["t_c"] if out else None, "outdoor_rh": out["rh_pct"] if out else None,
            "outdoor_td_c": out["dew_point_c"] if out else None, "outdoor_w": out["w"] if out else None,
            "surface_t_c": allc.surface_c, "surface_source": allc.surface_source,
            "fans_on": on_count, "fans_verified": 1 if all_verified else 0,
            "power_w": 0.0 if sim else power,
            "removal_g_h": removal,
            "potential_removal_g_h": potential if have_potential else None,
            "mode": cfg.mode.value, "reason_code": first.reason_code if first else None, "zones": zones,
            "penalty_w": penalty, "cond_air_cfm": cond_cfm,
            "dehum_power_w": dh.power_w if dh and dh.reachable else None,
            "dehum_on": None if self.dehumidifier_running() is None else int(bool(self.dehumidifier_running())),
            "groups": groups,
            "policy": first.policy if first else None,
            "utility_g_h": (first.tamo or {}).get("terms", {}).get("utility_g_h") if first else None,
            "urgency": (first.tamo or {}).get("urgency") if first else None,
            "thermal_penalty_g_h": (first.tamo or {}).get("terms", {}).get("thermal_penalty_g_h") if first else None,
            "energy_penalty_g_h": (first.tamo or {}).get("terms", {}).get("energy_penalty_g_h") if first else None,
            "pred_fan_dt_f": (first.tamo or {}).get("fan_effect_f") if first else None,
            "shadow_on": (int(self.shadow_decisions[cfg.groups[0].id].target_on)
                          if cfg.groups[0].id in self.shadow_decisions else None),
            "opportunity": first.opportunity if first else None,
            "pipe_t_c": allc.pipe_c,
            "circ": circ, "surfaces": surfaces,
        })

    # ------------------------------------------------------------ status
    def _circ_status(self, now: datetime) -> list[dict]:
        return _circ_status_impl(self, now)

    def status(self) -> dict:
        now = self.clock.now()
        groups = []
        for g in self.config.groups:
            gs = self.groups.get(g.id, GroupState())
            d = self.decisions.get(g.id)
            fans = []
            for f in group_fans(self.config, g):
                info = self.switch_info.get(f.switch_id, SwitchInfo(f.switch_id))
                fans.append({**f.model_dump(), "switch": info.to_dict(),
                             "running_s": (now - info.on_since).total_seconds() if info.on_since else None})
            groups.append({
                "id": g.id, "name": g.name, "zone": g.zone, "is_on": gs.is_on, "verified": gs.verified,
                "last_change": gs.last_change.isoformat() if gs.last_change else None,
                "manual_until": gs.manual_until.isoformat() if gs.manual_until else None,
                "maintenance": gs.maintenance,
                "decision": d.to_dict() if d else None, "fans": fans,
                "shadow": self.shadow_decisions[g.id].to_dict() if g.id in self.shadow_decisions else None,
                "deferring_since": gs.deferring_since.isoformat() if gs.deferring_since else None,
            })
        return {
            "now": now.isoformat(),
            "mode": self.config.mode.value,
            "energy_profile": self.config.control.energy_profile.value,
            "policy": self.config.control.policy.value,
            "shadow_evaluation": self.config.control.shadow_evaluation,
            "thermal_model": {**self.thermal_params().to_dict(),
                              "learned": {k: v for k, v in (self.thermal_learned or {}).items() if k != "history"}},
            "forecast": self.forecast.status(now, self.config) if self.forecast else
            {"enabled": self.config.temperature.forecast_enabled, "usable": False, "source": None},
            "rh_attribution": self.rh_attribution(),
            "data_source": self.data_source,
            "simulation_mode": self.config.mode == Mode.SIMULATION,
            "groups": groups,
            "sensors": self.snapshot.to_dict()["readings"] if self.snapshot else [],
            "alerts": [a.to_dict() for a in sorted(self.alerts.values(), key=lambda a: a.since, reverse=True)],
            "clock_speed": self.clock.speed,
            "hardware": {
                "sensor_provider": self.config.sensors.provider,
                "switch_provider": self.config.switches.provider,
                "hardware_auto_off_minutes": self.config.switches.hardware_max_on_minutes
                if self.switches.supports_hardware_auto_off() else None,
                "gateway_error": self._gateway_error,
                "switches": {s: i.to_dict() for s, i in self.switch_info.items()},
            },
            "zones": {k: z.to_dict() for k, z in self.zone_status.items()},
            "zone_summary": zones_mod.summary(self.zone_status, self.config) if self.zone_status else None,
            "surfaces": [x.to_dict() for x in self.surface_status],
            "surface_model": {"air_follow_fraction": surf_mod.air_follow_fraction(self.config, self.surface_learned)[0],
                              "source": surf_mod.air_follow_fraction(self.config, self.surface_learned)[1],
                              "learned": self.surface_learned},
            "circulation": {"mode": self.config.circulation.mode, "circulators": self._circ_status(now),
                            "elevated_rh_pct": self.config.circulation.elevated_rh_pct},
            "comparison": self.comparison_status() if self.comparison else {"active": False},
            "dehumidifier": None if not self.config.dehumidifier.enabled else {
                "name": self.config.dehumidifier.name,
                "power_w": self.dehum_status.power_w if self.dehum_status else None,
                "reachable": self.dehum_status.reachable if self.dehum_status else None,
                "running": self.dehumidifier_running(),
                "monitored": bool(self.config.dehumidifier.monitor_switch_id),
            },
        }


def _circ_status_impl(ctrl: "Controller", now: datetime) -> list[dict]:
    rt = ctrl.circulator_runtime()
    out = []
    for c in ctrl.config.circulators:
        st = ctrl.circ_states.get(c.id, circulation.CircState())
        info = ctrl.switch_info.get(c.switch_id, SwitchInfo(c.switch_id))
        d = ctrl.circ_decisions.get(c.id)
        conns = [k.model_dump() for k in ctrl.config.connections if k.circulator_id == c.id]
        state = ("on" if st.is_on else "off") if st.verified else "unknown"
        out.append({**c.model_dump(), "switch": info.to_dict(), "is_on": st.is_on if st.verified else None,
                    "state": state, "verified": st.verified,
                    "manual": st.manual, "manual_until": st.manual_until.isoformat() if st.manual_until else None,
                    "decision": d.to_dict() if d else None, "connections": conns,
                    "runtime": rt.get(c.id), "running_s": (now - info.on_since).total_seconds() if info.on_since else None,
                    "energy_label": "measured" if info.power_w else "estimated from rated watts"})
    return out


def _policy_name(p: ControlPolicy) -> str:
    return "Temperature-aware optimization" if p == ControlPolicy.TAMO else "Moisture-only control"


def _track_deferral(gs: GroupState, d: Decision, now: datetime) -> None:
    if d.reason_code.split(":")[-1] == "BETTER_FORECAST_WINDOW":
        gs.deferring_since = gs.deferring_since or now
    elif not d.reason_code.startswith("MIN_OFF_HOLD"):
        gs.deferring_since = None


def _tamo_summary(d: Decision) -> dict | None:
    t = d.tamo
    if not t:
        return None
    return {"terms": t["terms"], "urgency": t["urgency"], "fan_effect_f": t["fan_effect_f"],
            "removal_g_h": t["removal_g_h"], "opportunity": t.get("opportunity"), "in_control": t["in_control"]}
