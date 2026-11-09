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
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .clock import Clock
from . import calibration, tamo, thermal
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

        local = now.astimezone(ZoneInfo(cfg.system.timezone))
        local_hour = local.hour + local.minute / 60
        for g in cfg.groups:
            gs = self.groups.setdefault(g.id, GroupState())
            if cfg.mode != Mode.SIMULATION:
                gs.is_on, gs.verified = self._group_switch_state(g.id)
            cond = conditions_for_group(snap, g, cfg)
            cond.forecast, cond.forecast_age_h = fc, fc_age
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
        self._maybe_refit(now)

        await self._rearm_watchdogs(now)
        await self._read_dehumidifier()
        self._update_alerts(now)
        if self.last_history is None or (now - self.last_history) >= timedelta(minutes=cfg.system.history_minutes):
            self._write_history(now)
            self.last_history = now

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
        return sorted({f.switch_id for f in self.config.fans if f.switch_id})

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
            rated = sum(f.rated_w for f in fans)
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
            if ind and surf_c is not None and d.metrics.get("surface_source") == "measured":
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
                                f"{hrs:.0f} hours (latest reason: {prim}). Ventilation cannot fix this on its own: run "
                                "a dehumidifier and look for the moisture source.")
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
        })

    # ------------------------------------------------------------ status
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
            "dehumidifier": None if not self.config.dehumidifier.enabled else {
                "name": self.config.dehumidifier.name,
                "power_w": self.dehum_status.power_w if self.dehum_status else None,
                "reachable": self.dehum_status.reachable if self.dehum_status else None,
                "running": self.dehumidifier_running(),
                "monitored": bool(self.config.dehumidifier.monitor_switch_id),
            },
        }


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
