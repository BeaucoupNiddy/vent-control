"""Guided setup walkthrough.

The walkthrough is a list of steps. Each step has checks that look at the saved
configuration, what the hardware is reporting right now, and answers the person
gave in the walkthrough (things the app cannot see, such as "is there a gas
water heater?"). Progress and answers are saved in the database, so setup can
be paused on one device and resumed on another.

Check states:
  ok    looks right
  warn  works, but worth a look
  fail  must be fixed before this step is ready
  todo  waiting for an answer from the person
"""

from __future__ import annotations

import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AppConfig, Mode, SensorRole

PROGRESS_KEY = "setup_progress"

STEPS: list[dict] = [
    {"id": "prepare", "title": "Before you start",
     "summary": "Print or open the worksheet, and make sure this computer stays on."},
    {"id": "install", "title": "Install the hardware",
     "summary": "Put up the gateway, sensors, concrete probe, fans and plugs (the setup guide has pictures-in-words "
                "for each)."},
    {"id": "location", "title": "Your home",
     "summary": "Time zone, location and basement size."},
    {"id": "gateway", "title": "Ecowitt gateway",
     "summary": "Connect the app to the GW1200 so it can read the sensors."},
    {"id": "sensors", "title": "Sensors",
     "summary": "Tell the app which sensor is where: outdoor, each basement zone, the concrete probe."},
    {"id": "zones", "title": "Basement zones",
     "summary": "Check the zone names and sizes match your basement."},
    {"id": "plugs", "title": "Smart plugs",
     "summary": "Connect the Tapo plugs and say which plug powers which fan."},
    {"id": "exhaust", "title": "Exterior fan",
     "summary": "Check the Tjernlund fan's details and test it from the app."},
    {"id": "circulators", "title": "Circulation fans",
     "summary": "Set up and test the two Honeywell fans."},
    {"id": "safety", "title": "Safety",
     "summary": "Gas appliances, water, and the plug safety timer."},
    {"id": "targets", "title": "Comfort and humidity targets",
     "summary": "Review the humidity target and the temperature range you want to keep."},
    {"id": "golive", "title": "Turn it on",
     "summary": "Final check, then switch the controller to Automatic."},
    {"id": "verify", "title": "Prove it's working",
     "summary": "First weeks: compare sensors side by side, check cold surfaces and airflow, and plan for muggy "
                "weather."},
]
STEP_IDS = [s["id"] for s in STEPS]


def empty_progress() -> dict:
    return {"started_at": None, "updated_at": None, "current_step": STEP_IDS[0], "paused": False,
            "steps": {}, "answers": {}, "finished_at": None}


def load_progress(storage) -> dict:
    p = storage.get_kv(PROGRESS_KEY) or {}
    out = empty_progress()
    out.update({k: v for k, v in p.items() if k in out})
    return out


def update_progress(storage, now: datetime, change: dict) -> dict:
    """Merge a change from the walkthrough into the saved progress."""
    p = load_progress(storage)
    p["started_at"] = p["started_at"] or now.isoformat()
    p["updated_at"] = now.isoformat()
    if change.get("current_step") in STEP_IDS:
        p["current_step"] = change["current_step"]
    if "paused" in change:
        p["paused"] = bool(change["paused"])
    if isinstance(change.get("answers"), dict):
        p["answers"] = {**p["answers"], **change["answers"]}
    mark = change.get("mark")
    if isinstance(mark, dict) and mark.get("step") in STEP_IDS:
        if mark.get("state") in ("done", "skipped"):
            p["steps"][mark["step"]] = {"state": mark["state"], "at": now.isoformat()}
        elif mark.get("state") is None:
            p["steps"].pop(mark["step"], None)
    if change.get("finished"):
        p["finished_at"] = now.isoformat()
        p["paused"] = False
    storage.set_kv(PROGRESS_KEY, p)
    return p


# ---------------------------------------------------------------- checks


def _c(cid: str, status: str, label: str, detail: str = "", ask: str | None = None) -> dict:
    """ask: how the person answers this check in the walkthrough ("confirm" tick box, "gas" choice), if at all."""
    return {"id": cid, "status": status, "label": label, "detail": detail, "ask": ask}


def _ask(answers: dict, key: str, label: str, detail: str = "") -> dict:
    """A yes/no the person confirms in the walkthrough."""
    return _c(key, "ok" if answers.get(key) is True else "todo", label, detail, ask="confirm")


def _valid_host(host: str) -> bool:
    h = (host or "").strip()
    if not h:
        return False
    if h.count(":") == 1:
        h, port = h.split(":")
        if not port.isdigit():
            return False
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return all(part.replace("-", "").isalnum() for part in h.split(".")) and "." in h or h.endswith(".local")


def _readings(status: dict) -> dict[str, dict]:
    return {r["sensor_id"]: r for r in status.get("sensors", [])}


def check_prepare(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    return [
        _ask(ans, "worksheet_ready", "I have the worksheet filled in (or open next to me)",
             "The worksheet lists everything to write down before you start: sensor channels, plug addresses, "
             "fan details."),
        _ask(ans, "mac_stays_on", "This computer is set to never sleep and is plugged in",
             "System Settings → Battery (or Energy) → Options: turn on 'Prevent automatic sleeping when the "
             "display is off'. The app can only control the fans while this computer is awake."),
        _ask(ans, "router_reservations", "My router gives the gateway and each plug a fixed address",
             "In your router's app, look for 'DHCP reservation' or 'fixed IP'. Without it a plug's address can "
             "change after a power cut and the app loses it."),
    ]


def check_install(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    items = [
        ("inst_gateway", "The GW1200 gateway is plugged in upstairs and shows up in the Ecowitt app",
         "Ecowitt app → Add device → GW1200, then join it to your 2.4 GHz Wi-Fi. Keep it within about "
         "100 ft of the sensors."),
        ("inst_sensors", "Each T/RH sensor has batteries, its channel set, and is placed in its zone",
         "Set the channel switch inside the battery door before inserting batteries (CH1 main, CH2 pocket, "
         "CH3 damp area). Write the channel on the worksheet."),
        ("inst_outdoor", "The outdoor sensor is outside in shade and out of the rain",
         "North side, under an eave, about 5 ft up, away from the dryer vent and AC unit."),
        ("inst_probe", "The WN30BL probe is taped flat to the coldest concrete wall",
         "Tip pressed against bare concrete, covered with a small piece of foam and tape so it reads the "
         "wall, not the air. Low on an outside wall, about 1 ft above the floor."),
        ("inst_fan", "The Tjernlund fan is mounted in the foundation vent, blowing out",
         "Follow Tjernlund's instructions. Seal round the housing so air doesn't loop back."),
        ("inst_plugs", "Each fan is plugged into its own Tapo plug, and each plug is on your Wi-Fi",
         "Join each plug to Wi-Fi with Apple Home (no TP-Link account needed) or the Tapo app, then press 'Find plugs' on the plugs step."),
        ("inst_circ", "The two Honeywell fans are set up, speed chosen, power switch left ON",
         "Off the floor, pointed along the path on the worksheet. The plug does the switching."),
    ]
    return [_ask(ans, k, label, detail) for k, label, detail in items]


def check_location(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    out = []
    try:
        ZoneInfo(cfg.system.timezone)
        out.append(_c("timezone", "ok", f"Time zone: {cfg.system.timezone}"))
    except (ZoneInfoNotFoundError, ValueError):
        out.append(_c("timezone", "fail", "Time zone is not valid",
                      "Use a name like America/Chicago (New Orleans uses Central time)."))
    t = cfg.temperature
    if t.forecast_latitude == 0 and t.forecast_longitude == 0:
        out.append(_c("latlon", "warn", "Location is not set", "Used for the weather forecast."))
    else:
        out.append(_c("latlon", "ok", f"Location: {t.forecast_latitude:.2f}, {t.forecast_longitude:.2f}"))
    vol = cfg.house.basement_volume_ft3
    if vol <= 0:
        out.append(_c("volume", "fail", "Basement air volume is missing"))
    else:
        out.append(_c("volume", "ok", f"Basement air volume: {vol:,.0f} ft³",
                      "Floor area × ceiling height. 3,000 ft² × 8 ft = 24,000 ft³."))
    out.append(_ask(ans, "location_reviewed", "These details are right for my house"))
    return out


def check_gateway(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    s = cfg.sensors
    out = []
    if s.provider != "ecowitt":
        out.append(_c("provider", "fail", "Sensors are still set to the simulated basement",
                      "Choose 'Ecowitt gateway' below and save."))
        return out
    out.append(_c("provider", "ok", "Sensor source: Ecowitt gateway"))
    if not s.ecowitt_host:
        out.append(_c("host", "fail", "Gateway address is empty",
                      "Find it in the Ecowitt app (device page → More → Device info) or your router's device list."))
        return out
    if not _valid_host(s.ecowitt_host):
        out.append(_c("host", "fail", f"'{s.ecowitt_host}' does not look like an address",
                      "It should look like 192.168.0.50"))
        return out
    out.append(_c("host", "ok", f"Gateway address: {s.ecowitt_host}"))
    err = (status.get("hardware") or {}).get("gateway_error")
    channels = ctx.get("channels")
    if err:
        out.append(_c("reach", "fail", "The app can't reach the gateway",
                      f"{err}. Check the address, and that this computer and the gateway are on the same Wi-Fi."))
    elif channels is None:
        out.append(_c("reach", "warn", "No reply from the gateway yet",
                      ctx.get("discover_error") or "Wait a minute after saving, then press Check again."))
    else:
        out.append(_c("reach", "ok", f"Gateway is answering: {len(channels)} sensor channels found"))
    return out


def check_sensors(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    out = []
    maps = [m for m in cfg.sensors.mappings if m.enabled and m.role != SensorRole.IGNORE]
    if cfg.sensors.provider == "ecowitt":
        mock = [m.sensor_id for m in maps if m.sensor_id.startswith("mock-")]
        if mock:
            out.append(_c("mock", "fail", f"{len(mock)} simulated sensors are still in the list",
                          "Remove the 'mock-' rows (or set their location to 'ignore') and add your real sensors."))
    readings = _readings(status)

    def fresh(sid: str) -> bool:
        r = readings.get(sid)
        return bool(r) and r.get("status") == "ok"

    outdoor = [m for m in maps if m.role == SensorRole.OUTDOOR]
    if not outdoor:
        out.append(_c("outdoor", "fail", "No outdoor sensor",
                      "The fan only helps when outdoor air is drier, so the app must know outdoor conditions."))
    elif len(outdoor) > 1:
        out.append(_c("outdoor", "warn", f"{len(outdoor)} outdoor sensors", "The app uses the first one."))
    else:
        o = outdoor[0]
        out.append(_c("outdoor", "ok" if fresh(o.sensor_id) else "fail",
                      f"Outdoor sensor: {o.name}" + ("" if fresh(o.sensor_id) else " (no fresh reading)")))
    for z in cfg.zones:
        zs = [m for m in maps if m.role == SensorRole.BASEMENT and m.zone == z.id]
        if not zs:
            out.append(_c(f"zone-{z.id}", "fail", f"No air sensor in '{z.name}'",
                          "Every zone needs one temperature and humidity sensor."))
        else:
            ok = all(fresh(m.sensor_id) for m in zs)
            out.append(_c(f"zone-{z.id}", "ok" if ok else "fail",
                          f"'{z.name}': {', '.join(m.name for m in zs)}" + ("" if ok else " (no fresh reading)")))
    if not cfg.zones and not any(m.role == SensorRole.BASEMENT for m in maps):
        out.append(_c("basement", "fail", "No basement sensor"))
    surf = [m for m in maps if m.role == SensorRole.SURFACE]
    if surf:
        s = surf[0]
        out.append(_c("probe", "ok" if fresh(s.sensor_id) else "fail",
                      f"Concrete probe: {s.name}" + ("" if fresh(s.sensor_id) else " (no fresh reading)")))
        if cfg.zones and not s.zone:
            out.append(_c("probe-zone", "warn", "The concrete probe has no zone",
                          "Set the zone it sits in, so it is compared with the right room's air."))
    elif cfg.control.surface_temp_estimate_f is not None:
        out.append(_c("probe", "warn", "No concrete probe; using your estimated surface temperature"))
    else:
        out.append(_c("probe", "warn", "No concrete probe (WN30BL) set up",
                      "Without it the app can't see how damp the slab is, and it plays safe by skipping outdoor air "
                      "colder than the basement's dew point (many cool, dry nights). Map the probe (its id looks "
                      "like ecowitt:tf1)."))
    up = [m for m in maps if m.role == SensorRole.UPSTAIRS]
    if not up:
        out.append(_c("upstairs", "warn", "No upstairs sensor (recommended)",
                      "The exhaust fans pull some air down from the house. An upstairs T/RH sensor lets the app "
                      "measure how much instead of assuming 10%, and price the AC cost properly. The gateway's own "
                      "indoor sensor (ecowitt:indoor) works if the gateway is upstairs."))
    names = [m.name.strip().lower() for m in maps]
    if len(names) != len(set(names)):
        out.append(_c("names", "warn", "Two sensors have the same name", "Give each a name you'll recognise."))
    channels = ctx.get("channels")
    if channels is not None:
        mapped = {m.sensor_id for m in cfg.sensors.mappings}
        extra = [c["id"] for c in channels if c["id"] not in mapped]
        if extra:
            out.append(_c("unmapped", "warn", f"{len(extra)} channel(s) not used: {', '.join(extra)}",
                          "Add them, or ignore this if they are sensors you don't need."))
    low = [r["name"] for r in readings.values() if r.get("battery_low")]
    if low:
        out.append(_c("battery", "warn", f"Low battery: {', '.join(low)}"))
    out.append(_ask(ans, "sensors_placed", "Each sensor is placed as the worksheet says",
                    "Away from the fan's air stream, about chest height, not touching a wall. The outdoor sensor "
                    "in shade, out of the rain."))
    return out


def check_zones(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    out = []
    if not cfg.zones:
        out.append(_c("zones", "warn", "No zones: the basement is treated as one room"))
        return out + [_ask(ans, "zones_reviewed", "The zones match my basement")]
    total = sum(z.volume_ft3 for z in cfg.zones)
    house = cfg.house.basement_volume_ft3
    diff = abs(total - house) / house if house else 1
    out.append(_c("volume", "ok" if diff <= 0.15 else "warn",
                  f"Zone sizes add up to {total:,.0f} ft³ (basement is {house:,.0f} ft³)",
                  "" if diff <= 0.15 else "Adjust the zone sizes so they roughly add up to the whole basement."))
    if not any(z.on_ventilation_path for z in cfg.zones):
        out.append(_c("path", "fail", "No zone is marked as having the exterior fan",
                      "Tick 'exterior fan here' on the zone with the Tjernlund fan."))
    else:
        out.append(_c("path", "ok", "Exterior fan zone: " + ", ".join(z.name for z in cfg.zones
                                                                      if z.on_ventilation_path)))
    share = sum(z.moisture_source_share for z in cfg.zones)
    if abs(share - 1) > 0.05:
        out.append(_c("share", "warn", f"Moisture shares add up to {share:.0%}, not 100%",
                      "These are rough guesses of where the dampness comes from; make them add to 100%."))
    for z in cfg.zones:
        ms = [m for m in cfg.sensors.mappings if m.enabled and m.role == SensorRole.BASEMENT and m.zone == z.id]
        if len(ms) > 1:
            out.append(_c(f"shared-{z.id}", "warn", f"'{z.name}' has {len(ms)} sensors: "
                          + ", ".join(m.name for m in ms),
                          "If they sit in separate rooms, give each room its own zone so a damp room can't hide in "
                          "an average. Keep the one nearest the exhaust fans in the zone marked 'exterior fan here'."))
    out.append(_ask(ans, "zones_reviewed", "The zone names and sizes match my basement"))
    return out


def _plug_ids(cfg: AppConfig) -> list[str]:
    sw = cfg.switches
    if sw.provider == "tapo":
        return [d.switch_id for d in sw.tapo_devices]
    if sw.provider == "shelly":
        return [d.switch_id for d in sw.shelly_devices]
    return []


def check_plugs(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    sw = cfg.switches
    out = []
    if sw.provider == "mock":
        return [_c("provider", "fail", "Plugs are still set to simulated",
                   "Choose 'TP-Link Tapo' below and save.")]
    out.append(_c("provider", "ok", f"Plug type: {sw.provider}"))
    if sw.provider == "tapo":
        if sw.tapo_username and not sw.tapo_password:
            out.append(_c("creds", "fail", "TP-Link email entered without a password",
                          "Enter the password too, or clear the email if the plugs were set up without the Tapo app."))
        elif sw.tapo_username:
            out.append(_c("creds", "ok", "TP-Link login entered"))
        else:
            out.append(_c("creds", "ok", "No TP-Link account used",
                          "Fine for plugs set up through Matter (e.g. Apple Home). Plugs added in the Tapo app "
                          "need that account's email and password here."))
        devs = sw.tapo_devices
        if not devs:
            out.append(_c("devices", "fail", "No plugs added", "Add one row per plug with its address."))
        hosts = [d.host.strip() for d in devs]
        for d in devs:
            if not _valid_host(d.host):
                out.append(_c(f"host-{d.switch_id}", "fail", f"Plug '{d.switch_id}' has no valid address",
                              "Press 'Find plugs' on this step, or look in your router's device list."))
        if len(hosts) != len(set(hosts)):
            out.append(_c("dupe", "fail", "Two plugs have the same address"))
    known = set(_plug_ids(cfg)) if sw.provider in ("tapo", "shelly") else None
    users = [(f.name, f.switch_id) for f in cfg.fans if f.enabled] + \
            [(c.name, c.switch_id) for c in cfg.circulators if c.enabled]
    used = [s for _, s in users if s]
    for name, sid in users:
        if not sid:
            out.append(_c(f"assign-{name}", "fail", f"'{name}' has no plug chosen"))
        elif sid.startswith("mock-"):
            out.append(_c(f"assign-{name}", "fail", f"'{name}' is still on a simulated plug"))
        elif known is not None and sid not in known:
            out.append(_c(f"assign-{name}", "fail", f"'{name}' uses plug '{sid}', which isn't in the plug list"))
    if len(used) != len(set(used)):
        out.append(_c("shared", "fail", "Two fans are on the same plug",
                      "Each fan needs its own plug so the app can switch them separately."))
    hw = (status.get("hardware") or {}).get("switches") or {}
    probes = ctx.get("plug_probe") or {}
    for name, sid in users:
        if not sid or sid.startswith("mock-"):
            continue
        p = probes.get(sid) or hw.get(sid)
        if not p:
            out.append(_c(f"reach-{sid}", "warn", f"'{name}': not contacted yet", "Press Check again."))
        elif p.get("reachable"):
            out.append(_c(f"reach-{sid}", "ok", f"'{name}' plug is answering"))
        else:
            out.append(_c(f"reach-{sid}", "fail", f"'{name}' plug isn't answering",
                          (p.get("error") or "") + ". Check the address and that the plug has power."))
    return out


def check_exhaust(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    fans = [f for f in cfg.fans if f.enabled]
    if not fans:
        return [_c("fan", "fail", "No exterior fan is set up")]
    out = []
    for f in fans:
        out.append(_c(f"fan-{f.id}", "ok", f"{f.name}: {f.type.value}, {f.actual_cfm:.0f} CFM, {f.rated_w:.0f} W"))
        if f.actual_cfm > f.rated_cfm:
            out.append(_c(f"cfm-{f.id}", "warn", "Airflow is set higher than the fan's rating"))
        if not f.motor_load_ok:
            out.append(_c(f"load-{f.id}", "fail", "Plug isn't marked as rated for this fan",
                          "The Tapo P125M is rated 15 A; a 40 W fan is fine. Tick the box once you've checked."))
    if any(f.type.value == "exhaust" for f in fans):
        out.append(_ask(ans, "exhaust_direction", "The fan blows air OUT of the basement",
                        "With the fan running, hold a tissue near the vent outside: it should blow away from the "
                        "house."))
    out.append(_ask(ans, "exhaust_tested", "I turned the fan on and off from this app and heard it change",
                    "Use the Test buttons below. The app switches within about a minute."))
    return out


def check_circulators(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    circs = [c for c in cfg.circulators if c.enabled]
    if not circs:
        return [_c("none", "warn", "No circulation fans set up (optional)")]
    out = []
    for c in circs:
        if not c.location:
            out.append(_c(f"loc-{c.id}", "warn", f"'{c.name}': location is empty"))
        if c.physical_speed == "unknown":
            out.append(_c(f"speed-{c.id}", "fail", f"'{c.name}': set the speed you chose on the fan's own switch",
                          "Pick a speed on the fan and leave it there; the plug can only turn it on and off."))
        for key, label in (("restart", "turns itself back on when the plug turns on (test it)"),
                           ("dry", "is kept off the floor and away from water"),
                           ("tested", "switched on and off from this app")):
            k = f"circ_{c.id}_{key}"
            out.append(_ask(ans, k, f"'{c.name}' {label}"))
        all_ok = all(ans.get(f"circ_{c.id}_{k}") for k in ("restart", "dry", "tested"))
        if all_ok and not c.commissioned:
            out.append(_c(f"comm-{c.id}", "warn", f"'{c.name}' is ready but not yet marked commissioned",
                          "Press 'Mark commissioned' so Automatic circulation is allowed to use it later."))
        elif c.commissioned:
            out.append(_c(f"comm-{c.id}", "ok", f"'{c.name}' commissioned"))
    mode = cfg.circulation.mode
    out.append(_c("mode", "ok" if mode == "shadow" else "warn", f"Circulation automation: {mode}",
                  "" if mode == "shadow" else "Shadow (log only) is the recommended start: the app learns before "
                                                "it switches anything."))
    return out


def check_safety(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    out = []
    gas = ans.get("gas_appliance")
    if gas is None:
        out.append(_c("gas_appliance", "todo", "Is there a gas or oil appliance in the basement?",
                      "Water heater, furnace or boiler. If it vents through a chimney or a metal flue (not a sealed "
                      "plastic pipe), an exhaust fan can pull fumes back in.", ask="gas"))
    elif gas == "natural":
        if cfg.control.combustion_interlock:
            out.append(_c("gas_appliance", "warn", "Chimney-vented appliance: exhaust-only fan is blocked",
                          "Have a technician do a backdraft test before turning this off.", ask="gas"))
        else:
            out.append(_c("gas_appliance", "fail", "Chimney-vented appliance but the gas safety block is off",
                          "Turn on 'Block exhaust-only (gas appliance present)' until a technician does a "
                          "backdraft test.", ask="gas"))
    elif gas == "tested":
        out.append(_c("gas_appliance", "ok", "Backdraft test passed with the fan running", ask="gas"))
    else:
        out.append(_c("gas_appliance", "ok", "No chimney-vented gas appliance in the basement", ask="gas"))
    out.append(_ask(ans, "co_alarm", "There is a working carbon monoxide alarm in the basement",
                    "Recommended whenever a fan changes air pressure near fuel-burning appliances."))
    if cfg.control.water_intrusion_lockout:
        out.append(_c("water", "warn", "Water lockout is ON: the fan won't run",
                      "Leave it on only while there is standing water."))
    else:
        out.append(_c("water", "ok", "No water lockout"))
    dm = cfg.switches.hardware_max_on_minutes
    if not dm:
        out.append(_c("deadman", "warn", "Plug safety timer is off",
                      "If this computer stops, the fan would stay on. 30 minutes is recommended."))
    else:
        out.append(_c("deadman", "ok", f"Plug safety timer: {dm:.0f} min"))
        hw = status.get("hardware") or {}
        if cfg.switches.provider == "tapo" and hw.get("hardware_auto_off_minutes") is None:
            out.append(_c("watchdog", "warn", "The plugs haven't confirmed their safety timer yet",
                          "This confirms once the app has talked to every plug. Check again after testing fans."))
    out.append(_ask(ans, "openings_ok", "The fan doesn't block a flood vent or required combustion-air opening"))
    return out


def check_targets(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    c, t = cfg.control, cfg.temperature
    out = [_c("rh", "ok" if 45 <= c.target_rh_pct <= 60 else "warn", f"Humidity target: {c.target_rh_pct:.0f}%",
              "" if 45 <= c.target_rh_pct <= 60 else "50-55% is the usual basement target; mould grows above 60%.")]
    if c.surface_demand_enabled:
        out.append(_c("surface_rh", "ok", f"Slab and wall humidity target: {c.surface_target_rh_pct:.0f}%",
                      "With a concrete probe the fans also run when the coldest surface is damp, even if the room "
                      "air looks fine. About 70% leaves headroom below where mold tends to start."))
    out.append(_c("temp", "ok", f"Keep the basement between {t.preferred_min_f:.0f}°F and {t.preferred_max_f:.0f}°F "
                                f"(never below {t.min_allowed_f:.0f}°F)"))
    if cfg.dehumidifier.enabled:
        out.append(_c("dehum", "ok", f"Dehumidifier noted: {cfg.dehumidifier.name}"))
    out.append(_ask(ans, "targets_reviewed", "These targets suit me"))
    return out


def check_golive(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    out = []
    blockers = [s["title"] for s in ctx.get("earlier", []) if not s["ready"] and s["state"] != "done"]
    if blockers:
        out.append(_c("earlier", "fail", "Some steps still need attention: " + ", ".join(blockers),
                      "Go back to them, or skip this check if you know why."))
    else:
        out.append(_c("earlier", "ok", "All earlier steps are ready"))
    if status.get("data_source") == "mock":
        out.append(_c("mock", "fail", "The app is still reading the simulated basement"))
    crit = [a["message"] for a in status.get("alerts", []) if a.get("severity") == "critical"]
    if crit:
        out.append(_c("alerts", "fail", "Critical alert: " + crit[0]))
    if cfg.mode == Mode.AUTO:
        out.append(_c("mode", "ok", "Controller is in Automatic"))
    else:
        out.append(_c("mode", "todo", f"Controller is in {cfg.mode.value.replace('_', ' ')}",
                      "Press 'Switch to Automatic' when you're ready."))
    return out


def check_verify(cfg: AppConfig, status: dict, ans: dict, ctx: dict) -> list[dict]:
    """Commissioning checks from the 2026-11-12 physics review: things that turn assumptions into measurements."""
    out = []
    maps = [m for m in cfg.sensors.mappings if m.enabled and m.role in (SensorRole.BASEMENT, SensorRole.OUTDOOR,
                                                                        SensorRole.UPSTAIRS)]
    calibrated = [m for m in maps if m.calibrated_at]
    cmp = ctx.get("comparison") or {}
    if cmp.get("active"):
        out.append(_c("compare", "warn", f"Side-by-side comparison running ({cmp.get('hours', 0):.0f} h so far)",
                      "Leave the sensors together for 24 to 48 hours. While the outdoor sensor is inside, "
                      "Automatic keeps the exhaust fans off. Stop the comparison and put each sensor back when done."))
    elif calibrated or ans.get("sensors_compared"):
        out.append(_c("compare", "ok", "Sensors compared side by side",
                      f"{len(calibrated)} sensor(s) have offsets from a comparison." if calibrated else ""))
    else:
        out.append(_c("compare", "todo", "Compare all the T/RH sensors side by side",
                      "Different rooms really can differ, so don't adjust sensors to match where they hang. Put them "
                      "all together (outdoor one too) away from drafts, start the comparison on the Diagnostics tab, and leave "
                      "them 24 to 48 hours. Automatic pauses while the outdoor sensor is inside. Apply offsets only "
                      "for sensors that disagree when side by side, then put each one back.", ask="confirm"))
        out[-1]["id"] = "sensors_compared"
    damp = [z for z in cfg.zones if not any(m.role == SensorRole.BASEMENT and m.zone == z.id and m.enabled
                                            for m in cfg.sensors.mappings)]
    for z in damp:
        out.append(_c(f"nosensor-{z.id}", "warn", f"'{z.name}' has no air sensor",
                      "The damp-prone area is where a sensor matters most. Without one the app can't tell if it's "
                      "drying, and circulation fans won't run toward it."))
    if not any(m.role == SensorRole.SURFACE and m.enabled for m in cfg.sensors.mappings):
        out.append(_c("probe", "warn", "No slab or wall probe yet",
                      "Tape the WN30BL tip flat to bare concrete at the coldest spot (low on an outside wall or the "
                      "slab near it), under a foam pad so it reads the surface, not the air. Assign it the zone "
                      "whose air sensor is nearest."))
    out.append(_ask(ans, "probe_reference", "I checked the probe against another thermometer on the same spot",
                    "The WN30 is rated ±2°F. If it agrees within 1°F with a good thermometer, set Probe uncertainty "
                    "to 1°F in Settings so the app can be less cautious."))
    out.append(_ask(ans, "wood_checked", "I checked the coldest wood and corners (sill plate, joist ends, closets)",
                    "A cheap pin moisture meter works: wood under about 16% is fine. One probe can't see every cold "
                    "spot, and a temperature probe can't see water coming up through the concrete."))
    out.append(_ask(ans, "vents_open", "The passive vents opposite the fans are open, and I saw air move through "
                                      "the doorways",
                    "With the exhaust running, hold an incense stick or a strip of tissue at each opening between "
                    "rooms. Air should drift toward the fans. A room with no draft isn't being ventilated."))
    out.append(_ask(ans, "airflow_measured", "I measured the fans' real power (and airflow if I can)",
                    "A plug-in watt meter shows real power. Airflow is optional: a cheap anemometer across the "
                    "outside vent. The app assumes 220 CFM and 40 W per fan until told otherwise. Try one fan vs "
                    "two once: two fans rarely move twice the air through the far rooms."))
    if not any(m.role == SensorRole.UPSTAIRS and m.enabled for m in cfg.sensors.mappings):
        out.append(_c("upstairs", "warn", "No upstairs sensor",
                      "Needed to measure how much house air the fans pull down instead of assuming 10%."))
    circs = [c for c in cfg.circulators if c.enabled]
    if circs:
        out.append(_ask(ans, "circ_observed", "With a circulation fan on, the far room's dew point dropped",
                        "Watch History for that room during a run. A fan spinning in a room is not proof air is "
                        "being exchanged through it. Leave circulation in Shadow until this shows a benefit."))
    pol = cfg.control.policy.value
    out.append(_c("policy", "ok" if pol == "moisture" else "warn", f"Control logic: {pol}",
                  "" if pol == "moisture" else "Keep the moisture logic in control and the temperature-aware one "
                                                 "in shadow until real fan cycles back up its predictions."))
    out.append(_ask(ans, "dehum_plan", "I have a plan for muggy months (a dehumidifier on its own humidistat)",
                    "On muggy days outdoor air is wetter than the basement and the fans can't help. A dehumidifier "
                    "set to about 50% is the only thing that removes water then. Fix any leaks, gutters or grading "
                    "first: no fan keeps up with liquid water."))
    return out


CHECKS = {
    "prepare": check_prepare, "install": check_install, "location": check_location, "gateway": check_gateway, "sensors": check_sensors,
    "zones": check_zones, "plugs": check_plugs, "exhaust": check_exhaust, "circulators": check_circulators,
    "safety": check_safety, "targets": check_targets, "golive": check_golive, "verify": check_verify,
}


def evaluate(cfg: AppConfig, status: dict, progress: dict, ctx: dict | None = None) -> dict:
    """Run every step's checks and combine them with saved progress."""
    ctx = dict(ctx or {})
    ans = progress.get("answers", {})
    steps = []
    for s in STEPS:
        ctx["earlier"] = steps
        checks = CHECKS[s["id"]](cfg, status, ans, ctx)
        ready = not any(c["status"] in ("fail", "todo") for c in checks)
        state = (progress.get("steps", {}).get(s["id"]) or {}).get("state")
        steps.append({**s, "checks": checks, "ready": ready, "state": state,
                      "counts": {k: sum(1 for c in checks if c["status"] == k) for k in ("ok", "warn", "fail", "todo")}})
    done = sum(1 for s in steps if s["state"] == "done")
    return {"steps": steps, "progress": progress, "done": done, "total": len(steps),
            "finished": bool(progress.get("finished_at"))}
