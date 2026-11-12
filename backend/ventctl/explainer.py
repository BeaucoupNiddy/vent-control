"""Plain-language "why are the fans on/off?" chat, using a model on OpenRouter.

Read-only by design: the model only receives a snapshot of the controller's state and
answers in text. Nothing it says is ever turned into a command.

The OpenRouter key lives in a small file next to the database (data/openrouter.json,
owner-only permissions). It is never written to the config, the database, logs, events
or any API response; the API reports only whether a key is saved and its last 4 characters.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

OPENROUTER = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
MAX_CONTEXT_CHARS = 60_000
MAX_TURNS = 20
SECRET_WORDS = ("password", "token", "secret", "api_key", "apikey", "username")

RULES = """\
HOW THE CONTROLLER DECIDES (checked every 60 seconds for each fan group)

Hardware: two Tjernlund V2D UnderAire exhaust fans (about 220 CFM and 40 W each) on smart
plugs blow basement air outside. Replacement air comes in through the vents from outdoors,
and a small share is pulled down from the conditioned upstairs. Separate circulation fans
(Honeywell HT-900) only stir air between basement zones; they never remove moisture.
Sensors are Ecowitt: the outdoor sensor is a WN31 on channel 1 (under cover), plus basement
sensors (the gateway's own sensor and extra WN31s in other rooms) and optionally a concrete
probe.

The key idea: running the fans only helps when the outdoor air holds LESS water than the
basement air. Humidity percentage (RH) alone is misleading because it depends on
temperature; the controller compares the actual amount of water in the air (humidity ratio,
grams of water per kg of air) and the dew point. Warm outdoor air at 60% RH can carry more
water than cool basement air at 75% RH. The controller never runs fans just because the
basement is humid.

Order of checks:
1. Mode. OFF means off. MANUAL ON runs for a limited time (still stopped by hard safety).
   SCHEDULE runs inside its time windows. AUTO uses all the logic below. SIMULATION decides
   but never switches the plugs.
2. Hard safety (stops fans at once, ignores minimum run/off times):
   - readings must be fresh (older than about 5 minutes, or the gateway unreachable, means OFF)
   - the plug's on/off state must be confirmed; a plug that fails a command locks that group
     out of automatic control for 30 minutes
   - interlocks (water intrusion, combustion appliance, depressurization) if configured
   - condensation: fans stop if running would make a surface wetter, either because
     outdoor air wetter than the basement would reach a cold surface, or because cold air
     would chill a surface close to its dew point (3 F margin)
   - temperature limits: fans stop if they are predicted to cool the basement below the
     minimum (55 F by default) or chill pipes toward freezing
3. Direction: if the outdoor air holds as much or more water than the basement air, fans
   stop immediately, because running them would ADD water.
4. Benefit, with on/off thresholds so the fans don't flicker (hysteresis):
   - basement RH must be above the target (start at target + 2%, stop at target). In a strong
     drying window (outdoor dew point 8 F or more lower) a running fan keeps going down to 50%
   - dew-point advantage: outdoor dew point must be at least 3 F lower than the basement's
     to start, and fans keep running while it is more than 1 F lower
   - the estimated water removed per hour must exceed a minimum
   - efficiency (water removed per kWh, counting fan power and the extra AC/heating cost of
     replacing upstairs air) must clear the energy profile's threshold: Maximum Drying has
     none, Balanced needs 1,500 g/kWh, Energy Saver 5,000 g/kWh. Stop threshold is 70% of start.
5. Minimum run and off times hold the current state for a while after a change (shown as
   MINIMUM_RUNTIME_ACTIVE / MINIMUM_OFF_TIME_ACTIVE).
6. Optional forecast: a start may be delayed up to 2 hours if a clearly better window is
   coming soon, but a running fan is never stopped for that.

Policies: "moisture" (the default, rules above) or "tamo" (temperature-aware: weighs water
removed against cooling the basement and energy). Whichever is not active runs in "shadow",
meaning it only reports what it would have done.

Opportunity labels: excellent, good, marginal, unfavorable, unsafe.
Each decision has a primary reason code (e.g. OUTDOOR_AIR_WETTER, ADVANTAGE_TOO_SMALL,
BELOW_TARGET, TARGET_REACHED, MOISTURE_BENEFIT_POSITIVE, EFFICIENCY_TOO_LOW, SENSOR_STALE,
SWITCH_UNVERIFIED, CONDENSATION_RISK, PREDICTED_MIN_TEMP_BREACH, MODE_OFF, MANUAL_ON),
a one-line explanation, and a list of checks (pass/fail). Prefer these over your own
calculations: they are what the controller actually used.

Things to keep in mind:
- Ventilation controls moisture exchange, not drainage; it cannot dry a wet basement alone.
- RH can rise while drying, because cooler air shows a higher RH with the same water.
- "Removal" numbers are modeled estimates, not measurements.
- Units in the data: temperatures with _c are Celsius, _f Fahrenheit; humidity ratio w is
  kg/kg (multiply by 1000 for g/kg) and w_g_kg is g/kg; removal is grams per hour.
"""

SYSTEM = """\
You explain a home basement ventilation controller to its owner, who is not technical.
Answer in plain, friendly language, short paragraphs, no jargon (or explain a term in a few
words when you must use it). Lead with the direct answer ("The fans are off because ...").
Use Fahrenheit for temperatures. Ground every claim in the CURRENT DATA below and say when
something is an estimate or when the data doesn't say. Never invent readings.

You cannot control the fans or change settings. If the owner wants a change, tell them which
part of the app to use (for example the Home or Dashboard page for mode, Settings for
targets and thresholds) and what the effect would be.

{rules}

CURRENT DATA (snapshot taken {now}, owner's time zone {tz}):
{context}
"""


# ------------------------------------------------------------------ key storage
class KeyStore:
    def __init__(self, directory: Path):
        self.path = directory / "openrouter.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def key(self) -> str:
        return str(self._read().get("api_key") or "")

    def model(self) -> str:
        return str(self._read().get("model") or DEFAULT_MODEL)

    def update(self, api_key: str | None = None, model: str | None = None) -> None:
        d = self._read()
        if api_key is not None:
            d["api_key"] = api_key.strip()
        if model:
            d["model"] = model.strip()
        self._write(d)

    def public(self) -> dict:
        k = self.key()
        return {"has_key": bool(k), "key_hint": f"…{k[-4:]}" if len(k) >= 8 else None, "model": self.model()}


# ------------------------------------------------------------------ context
def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[hidden]" if v and any(w in k.lower() for w in SECRET_WORDS) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def _r(v, n=2):
    return round(v, n) if isinstance(v, float) else v


def _f(c):
    return round(c * 9 / 5 + 32, 1) if isinstance(c, (int, float)) else None


HIST_KEYS = ("indoor_rh", "outdoor_rh", "avg_rh", "fans_on", "mode", "reason_code", "removal_g_h", "opportunity")


def recent_history(rows: list[dict], tz: ZoneInfo, every_min: int = 20) -> list[dict]:
    """Compact trend: one row every `every_min` minutes, temperatures in F."""
    out, last = [], None
    for r in rows:
        t = datetime.fromisoformat(r["ts"])
        if last and (t - last) < timedelta(minutes=every_min):
            continue
        last = t
        row = {"time": t.astimezone(tz).strftime("%a %H:%M"),
               "indoor_t_f": _f(r.get("indoor_t_c")), "indoor_dew_f": _f(r.get("indoor_td_c")),
               "outdoor_t_f": _f(r.get("outdoor_t_c")), "outdoor_dew_f": _f(r.get("outdoor_td_c"))}
        row.update({k: _r(r.get(k), 1) for k in HIST_KEYS})
        out.append(row)
    return out


def build_context(status: dict, config: dict, events: list[dict], history: list[dict], tz: ZoneInfo) -> str:
    keep = ("now", "mode", "policy", "energy_profile", "data_source", "simulation_mode", "groups", "sensors",
            "alerts", "zone_summary", "basement_average", "surfaces", "circulation", "forecast", "dehumidifier",
            "rh_attribution", "hardware")
    snap = {k: status.get(k) for k in keep if k in status}
    # friendly copies of the key temperatures, since the model is asked to answer in F
    for s in snap.get("sensors") or []:
        if isinstance(s, dict):
            for k in list(s):
                if k.endswith("_c") and isinstance(s[k], (int, float)):
                    s[k[:-2] + "_f"] = _f(s[k])
    ev = [{"time": datetime.fromisoformat(e["ts"]).astimezone(tz).strftime("%a %H:%M") if e.get("ts") else None,
           "kind": e.get("kind"), "severity": e.get("severity"), "message": e.get("message"),
           "reason": e.get("reason_code")} for e in events]
    cfg = {k: config.get(k) for k in ("mode", "control", "temperature", "house", "fans", "groups", "zones",
                                      "circulation", "circulators", "dehumidifier", "schedule", "sensors")
           if k in config}
    parts = [
        ("Controller status right now", redact(snap)),
        ("Recent trend (last 6 hours, newest last)", history),
        ("Recent events (newest first)", ev),
        ("Settings", redact(cfg)),
    ]
    text = ""
    for title, data in parts:
        chunk = f"### {title}\n{json.dumps(data, default=str, separators=(',', ':'))}\n"
        if len(text) + len(chunk) > MAX_CONTEXT_CHARS:
            chunk = chunk[: max(0, MAX_CONTEXT_CHARS - len(text))] + "\n[truncated]\n"
        text += chunk
        if len(text) >= MAX_CONTEXT_CHARS:
            break
    return text


def system_prompt(context: str, now: datetime, tz: ZoneInfo) -> str:
    return SYSTEM.format(rules=RULES, now=now.astimezone(tz).strftime("%A %b %d, %I:%M %p"), tz=str(tz),
                         context=context)


# ------------------------------------------------------------------ OpenRouter
class OpenRouterError(Exception):
    pass


def _http(url: str, key: str | None, body: dict | None, timeout: float) -> dict:
    headers = {"Content-Type": "application/json", "HTTP-Referer": "http://localhost", "X-Title": "Basement Ventilation"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message")
        except Exception:  # noqa: BLE001
            msg = None
        friendly = {401: "OpenRouter didn't accept the API key. Check it and paste it again.",
                    402: "Your OpenRouter account is out of credits.",
                    429: "OpenRouter is rate-limiting requests. Wait a minute and try again."}
        raise OpenRouterError(friendly.get(e.code) or f"OpenRouter error {e.code}: {msg or e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise OpenRouterError(f"Couldn't reach OpenRouter ({getattr(e, 'reason', e)}). Check the internet connection.") from None
    except ValueError:
        raise OpenRouterError("OpenRouter sent a response that isn't JSON.") from None


_models_cache: tuple[float, list[dict]] | None = None


async def list_models() -> list[dict]:
    global _models_cache
    if _models_cache and time.time() - _models_cache[0] < 3600:
        return _models_cache[1]
    data = await asyncio.to_thread(_http, f"{OPENROUTER}/models", None, None, 20)
    models = []
    for m in data.get("data", []):
        p = m.get("pricing") or {}
        try:
            prompt, completion = float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            prompt = completion = None
        if "text" not in ((m.get("architecture") or {}).get("output_modalities") or ["text"]):
            continue
        models.append({"id": m.get("id"), "name": m.get("name") or m.get("id"),
                       "context_length": m.get("context_length"),
                       "prompt_per_m": _r(prompt), "completion_per_m": _r(completion)})
    models.sort(key=lambda m: (m["name"] or "").lower())
    _models_cache = (time.time(), models)
    return models


async def chat(key: str, model: str, system: str, messages: list[dict]) -> dict:
    msgs = [{"role": m["role"], "content": str(m["content"])[:8000]}
            for m in messages[-MAX_TURNS:] if m.get("role") in ("user", "assistant") and m.get("content")]
    body = {"model": model, "messages": [{"role": "system", "content": system}] + msgs, "temperature": 0.3}
    data = await asyncio.to_thread(_http, f"{OPENROUTER}/chat/completions", key, body, 120)
    if data.get("error"):
        raise OpenRouterError(f"OpenRouter error: {data['error'].get('message', data['error'])}")
    try:
        reply = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise OpenRouterError("OpenRouter returned no answer.") from None
    return {"reply": reply.strip(), "model": data.get("model", model), "usage": data.get("usage")}
