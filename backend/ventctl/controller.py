"""Runtime controller: acquisition -> validation -> decision -> switching -> logging.

Fail-safe rules implemented here:
  * sensor gateway errors or stale readings -> engine forces OFF, we command OFF
  * a switch that cannot be read or does not confirm a command marks the group
    unverified; the engine then forces OFF and we keep retrying OFF
  * last *commanded* state is kept separately from *verified* device state
  * at startup we read every switch and command anything ON to OFF before
    automatic control resumes (safe state after a power outage)
  * manual overrides are time-limited and logged
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .clock import Clock
from .config import AppConfig, Mode, SensorRole
from .engine import Decision, GroupState, decide, group_fans
from .hardware.base import HardwareError, SensorSource, SwitchController, SwitchStatus
from .sensors import SensorReading, Snapshot, conditions_for_group, validate
from .storage import Storage
from .units import delta_f_to_c

log = logging.getLogger("ventctl")
COMMAND_LOCKOUT_MIN = 30


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
    _command_failures: dict[str, str] = field(default_factory=dict)
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
            snap.readings.append(validate(m, r, now, stale_s, self._prev_ts.get(m.sensor_id)))
            if r:
                self._prev_ts[m.sensor_id] = r.ts
        self.snapshot = snap

        # verify switch states every tick
        for sid in self._switch_ids():
            self._record_status(await self.switches.get(sid), now)

        local = now.astimezone(ZoneInfo(cfg.system.timezone))
        local_hour = local.hour + local.minute / 60
        for g in cfg.groups:
            gs = self.groups.setdefault(g.id, GroupState())
            if cfg.mode != Mode.SIMULATION:
                gs.is_on, gs.verified = self._group_switch_state(g.id)
            cond = conditions_for_group(snap, g, cfg)
            d = decide(cond, gs, cfg, g, now, local_hour=local_hour)
            prev = self.decisions.get(g.id)
            self.decisions[g.id] = d
            await self._apply(g.id, gs, d, now)
            if prev is None or prev.reason_code != d.reason_code or prev.target_on != d.target_on:
                self.storage.add_event(now, "decision", "warning" if d.safety else "info", d.explanation,
                                       d.reason_code, {"group": g.id, "target_on": d.target_on,
                                                       "readings": snap.to_dict()["readings"],
                                                       "metrics": d.metrics,
                                                       "switches": {s: i.to_dict() for s, i in self.switch_info.items()}})

        self._update_alerts(now)
        if self.last_history is None or (now - self.last_history) >= timedelta(minutes=cfg.system.history_minutes):
            self._write_history(now)
            self.last_history = now

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
        if not self.config.groups:
            return
        gid = self.config.groups[0].id
        d = self.decisions.get(gid)
        if d is None:
            return
        m = d.metrics
        ind, out = m.get("indoor"), m.get("outdoor")
        on_count = 0
        power = 0.0
        all_verified = True
        for f in self.config.fans:
            info = self.switch_info.get(f.switch_id)
            if self.config.mode == Mode.SIMULATION:
                if self.groups.get(f.group, GroupState()).is_on and f.enabled:
                    on_count += 1
                continue
            if info and info.confirmed and f.enabled:
                on_count += 1
            if info and info.power_w:
                power += info.power_w
            if not (info and info.reachable):
                all_verified = False
        pot = d.effect.net_removal_g_h if d.effect else None
        zones = {}
        if self.snapshot:
            for r in self.snapshot.readings:
                if r.mapping.role == SensorRole.BASEMENT and r.ok:
                    zones[r.mapping.zone or r.mapping.sensor_id] = {"t_c": r.temp_c, "rh": r.rh}
        is_on = on_count > 0
        self.storage.add_history(now, {
            "source": self.data_source,
            "indoor_t_c": ind["t_c"] if ind else None, "indoor_rh": ind["rh_pct"] if ind else None,
            "indoor_td_c": ind["dew_point_c"] if ind else None, "indoor_w": ind["w"] if ind else None,
            "outdoor_t_c": out["t_c"] if out else None, "outdoor_rh": out["rh_pct"] if out else None,
            "outdoor_td_c": out["dew_point_c"] if out else None, "outdoor_w": out["w"] if out else None,
            "surface_t_c": m.get("surface_c"), "surface_source": m.get("surface_source"),
            "fans_on": on_count, "fans_verified": 1 if all_verified else 0,
            "power_w": power if self.config.mode != Mode.SIMULATION else 0.0,
            "removal_g_h": pot if (is_on and pot is not None) else 0.0,
            "potential_removal_g_h": pot,
            "mode": self.config.mode.value, "reason_code": d.reason_code, "zones": zones,
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
            })
        return {
            "now": now.isoformat(),
            "mode": self.config.mode.value,
            "energy_profile": self.config.control.energy_profile.value,
            "data_source": self.data_source,
            "simulation_mode": self.config.mode == Mode.SIMULATION,
            "groups": groups,
            "sensors": self.snapshot.to_dict()["readings"] if self.snapshot else [],
            "alerts": [a.to_dict() for a in sorted(self.alerts.values(), key=lambda a: a.since, reverse=True)],
            "clock_speed": self.clock.speed,
        }
