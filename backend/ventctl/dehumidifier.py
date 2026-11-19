"""Dehumidifier switched by a smart plug (e.g. a Tapo P125M).

The plug cuts and restores power, so the dehumidifier itself must be set to run whenever it has
power (its own humidistat at the lowest setting or "continuous") and to restart on its own after a
power cut. The app then acts as the humidistat, reading the room it sits in:

* Auto: ON when that room's RH reaches the target plus the start margin, OFF at the target.
  Minimum run and rest times protect the compressor. Automatic switching starts only once the
  dehumidifier is marked commissioned; before that the app shows what it would do.
* On: run now for a set time, then back to Auto.  Off: hold off until set back to Auto.

A water-intrusion lockout always switches it off.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .config import AppConfig


@dataclass
class DehumState:
    is_on: bool = False
    verified: bool = False
    last_change: datetime | None = None
    shadow_on: bool = False  # what Auto would be doing while it is not allowed to switch
    shadow_change: datetime | None = None


@dataclass
class DehumDecision:
    target_on: bool
    reason_code: str
    explanation: str
    commanding: bool  # this decision actually switches the plug
    control_rh_pct: float | None = None
    control_source: str = ""
    start_rh_pct: float | None = None
    stop_rh_pct: float | None = None
    held: bool = False
    next_eligible_switch: datetime | None = None
    safety: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["next_eligible_switch"] = self.next_eligible_switch.isoformat() if self.next_eligible_switch else None
        return d


def control_rh(config: AppConfig, zones: dict, basement_avg: dict | None) -> tuple[float | None, str]:
    """RH (%) the dehumidifier works from: its own room, else the whole-basement average."""
    dh = config.dehumidifier
    if dh.zone:
        z = zones.get(dh.zone)
        if z is not None and z.ok:
            return z.air.rh * 100, z.name
        if dh.zone_only:
            return None, config.zone_name(dh.zone)
    if basement_avg and basement_avg.get("rh_pct") is not None:
        return float(basement_avg["rh_pct"]), "basement average"
    return None, config.zone_name(dh.zone) if dh.zone else "basement"


def decide(config: AppConfig, state: DehumState, override: dict | None, zones: dict, basement_avg: dict | None,
           now: datetime) -> DehumDecision:
    dh = config.dehumidifier
    rh, src = control_rh(config, zones, basement_avg)
    stop = dh.setpoint_rh_pct
    start = dh.setpoint_rh_pct + dh.start_margin_pct
    auto_commanding = bool(dh.enabled and dh.switch_id and dh.commissioned)

    def out(on: bool, code: str, text: str, commanding: bool, safety: bool = False, immediate: bool = False,
            current: bool | None = None, since: datetime | None = None) -> DehumDecision:
        d = DehumDecision(on, code, text, commanding, rh, src, start, stop, safety=safety)
        cur = state.is_on if current is None else current
        last = state.last_change if since is None else since
        if not (safety or immediate) and last is not None and on != cur:
            need = dh.min_on_minutes if cur else dh.min_off_minutes
            elig = last + timedelta(minutes=need)
            if now < elig:
                d.held, d.target_on, d.next_eligible_switch = True, cur, elig
                d.explanation = (f"Holding {'ON' if cur else 'OFF'} until {elig.strftime('%H:%M')} (minimum "
                                 f"{'run' if cur else 'rest'} time protects the compressor). Next: {text}")
        return d

    if not dh.enabled or not dh.switch_id:
        return out(False, "NOT_SET_UP", "Not switched by the app: no smart plug assigned.", False, immediate=True)
    if config.control.water_intrusion_lockout:
        return out(False, "WATER_INTRUSION_LOCKOUT", "OFF: water-intrusion lockout is active.", True, safety=True)
    if not state.verified:
        return out(False, "PLUG_UNVERIFIED", "The smart plug is not responding, so its state is uncertain.", False,
                   safety=True)

    o = override or {}
    if o.get("state") == "off":
        return out(False, "HELD_OFF", "OFF: held off by you until set back to Auto.", True, immediate=True)
    if o.get("state") == "on":
        until = o.get("until")
        txt = f" until {datetime.fromisoformat(until).strftime('%H:%M')}" if until else ""
        return out(True, "MANUAL_ON", f"ON: turned on by you{txt}.", True, immediate=True)

    # Auto. Before commissioning it is judged against its own hypothetical state and never switched.
    cur = state.is_on if auto_commanding else state.shadow_on
    since = state.last_change if auto_commanding else state.shadow_change
    prefix = "" if auto_commanding else "Not commissioned yet, so the app does not switch it. Would be: "
    if rh is None:
        return out(False, "NO_READING", prefix + f"OFF: no humidity reading for {src}.", auto_commanding,
                   immediate=True, current=cur, since=since)
    if cur and rh <= stop:
        return out(False, "TARGET_REACHED", prefix + f"OFF: {src} is at {rh:.0f}% RH, down to the {stop:.0f}% "
                   "target.", auto_commanding, current=cur, since=since)
    if cur:
        return out(True, "DRYING", prefix + f"ON: {src} is at {rh:.0f}% RH; runs until it reaches {stop:.0f}%.",
                   auto_commanding, current=cur, since=since)
    if rh >= start:
        return out(True, "ABOVE_TARGET", prefix + f"ON: {src} is at {rh:.0f}% RH, at or above the {start:.0f}% "
                   f"start point; runs until {stop:.0f}%.", auto_commanding, current=cur, since=since)
    return out(False, "BELOW_START", prefix + f"OFF: {src} is at {rh:.0f}% RH; starts at {start:.0f}%.",
               auto_commanding, current=cur, since=since)
