# Basement Smart Ventilation Controller

A local web app that decides when exchanging basement air with outdoor air actually helps, and runs the fans only then. It compares the **moisture content** (humidity ratio) of basement and outdoor air rather than relative humidity. It checks condensation risk, fan electricity and the cost of pulling conditioned air down from upstairs, and it explains every decision in plain language.

**Status: Phases 1–4 built.** The decision engine, psychrometrics, dynamic moisture simulator, dashboard, history and replay (Phase 1). Real hardware: Ecowitt GW1200 sensors (poll or push), TP-Link Tapo, Shelly and Home Assistant plugs with plug-side auto-off watchdogs (Phase 2). Model calibration, dehumidifier monitoring and multiple fan groups (Phase 3). NOAA weather import, seasonal reports and an annual estimate (Phase 4). With no hardware configured, everything runs on mock hardware driven by a physical basement model, and every mock value is labeled.

> Ventilation controls moisture exchange, not drainage. It cannot keep a wet New Orleans basement dry on its own.

---

## Quick start

### macOS (easiest)

```bash
# one-time: get the code with git (needed for automatic updates) and run the installer
git clone https://github.com/BeaucoupNiddy/vent-control.git ~/Projects/vent-control
bash ~/Projects/vent-control/deploy/install-mac.sh
```

The installer adds Python 3.12 and Node with Homebrew if they're missing, builds the dashboard, sets up Python, and opens http://localhost:8000. It also installs two LaunchAgents:

- `com.ventctl` starts the controller at login and restarts it if it stops.
- `com.ventctl.update` checks GitHub every 30 minutes. When something new is merged to `main`, it downloads it, rebuilds, runs the tests, and restarts the controller only if they pass. Otherwise it stays on the running version. The log is in `data/update.log`. To update immediately, run `deploy/update.sh --force`.

So the update path is: a change is merged on GitHub, and within about 30 minutes the Mac is running it. When it restarts, the controller reads every plug and turns running fans OFF before resuming, as on any startup.

Set the Mac to never sleep and to restart after a power failure (System Settings, Energy).

### macOS (by hand)

```bash
brew install python@3.12 node
cd frontend && npm ci && npm run build && cd ..   # builds the dashboard into backend/ventctl/static
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# run
python -m ventctl                        # http://localhost:8000  (and http://<mac-ip>:8000 from your phone)
```

On first start the app fills in 48 hours of **mock** history so the charts have something to show. Every mock value is labeled "mock".

Useful environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `VENTCTL_HOST` | `0.0.0.0` | Listen address (`127.0.0.1` = this computer only) |
| `VENTCTL_PORT` | `8000` | Port |
| `VENTCTL_DB` | `data/ventctl.db` | SQLite file (settings + history + events) |
| `VENTCTL_DEMO_SPEED` | `1` | Speeds up mock time, e.g. `60` = one hour per minute |
| `VENTCTL_BACKFILL_HOURS` | `48` | Mock history created on first start (0 = none) |

**Start automatically by hand (macOS):** `deploy/install-mac.sh` does this for you. To do it by hand, edit the paths in `deploy/com.ventctl.plist`, copy it to `~/Library/LaunchAgents/` and run `launchctl load ~/Library/LaunchAgents/com.ventctl.plist`.

**Dashboard development:** run `python -m ventctl` in one terminal and `cd frontend && npm run dev` in another. Then open http://localhost:5173, which proxies `/api` to the backend.

### Docker Compose

```bash
docker compose up -d --build        # http://localhost:8000
VENTCTL_DEMO_SPEED=60 docker compose up -d   # demo with fast mock time
```

The data lives in the `ventctl-data` volume. The container restarts automatically.

### Tests

```bash
cd backend && python -m pytest -q
```

There are 89 tests. They cover every case in the spec's test list (section 18) plus checks against ASHRAE reference values. See *Test map* below.

---

## How the controller decides

Every 60 seconds, for each fan group:

1. **Mode.** OFF and MANUAL ON obey the user. Manual is time-limited (60 minutes by default) and still subject to hard interlocks. SCHEDULE runs inside its windows but still fails safe. AUTO and SIMULATION run the full logic below. SIMULATION never sends commands.
2. **Hard safety.** These stop the fans immediately and ignore minimum run and off times:
   - indoor and outdoor readings are current (stale after 5 minutes, or the gateway is unreachable)
   - the fan switch state can be verified
   - interlocks: water-intrusion lockout, combustion-appliance lockout for exhaust-only setups, and a depressurization limit if a pressure sensor exists
   - **condensation:** would running make a surface wetter? Two ways it can:
     - outdoor air that is *wetter* than the basement reaches a cold surface (outdoor dew point within the 3 °F margin of the coldest surface). Drier outdoor air lowers the dew point next to every surface, so it never trips this.
     - cold air chills a surface toward the dew point of the air around it: the predicted margin with fans on is below 3 °F and worse than with fans off.
     - with no surface sensor and no conservative surface estimate, auto still runs with drier outdoor air that stays warmer than the basement dew point plus the margin, and blocks air cold enough to chill surfaces where it enters.
3. **Direction.** If outdoor air holds more moisture (ΔW ≤ 0), the fans stop immediately, because running them adds water.
4. **Benefit**, with hysteresis and minimum on/off times:
   - basement RH is above target (start at target + 2 %, stop at target). During a strong drying window (outdoor dew point ≥ 8 °F lower) a running fan keeps going down to 50 %, because the wood and concrete hold far more water than the air and stopping at the air target leaves it in them
   - dew-point advantage: start at ≥ 3 °F, keep running while > 1 °F
   - estimated removal = dry-air mass flow × (W_basement − W_incoming) must exceed a minimum. W_basement is the air at the fan (the ventilation-path zones), not the wettest zone
   - efficiency = net removal ÷ (fan watts + extra HVAC watts to replace conditioned air pulled from upstairs) must clear the energy profile's threshold. Maximum Drying has no minimum. Balanced needs 1,500 g/kWh and Energy Saver 5,000 g/kWh. The stop threshold is 70 % of the start threshold.

It never runs fans just because the basement is humid. A 90 % basement with outdoor air that would be 80 % at basement temperature still runs, because that is a real drying benefit. A 65 % basement with outdoor air that would be 85 % stays off.

### Airflow model

Exhaust fans push basement air out. When exhaust exceeds intake, a configurable fraction of the shortfall is assumed to come from the conditioned upstairs through the floor (10 % by default). The rest comes from outdoors through the vents. Air drawn from upstairs is replaced upstairs by outdoor air, which the AC has to cool and dry. That cost is the "conditioned-air penalty" (enthalpy difference ÷ COP). It is charged only while the HVAC is assumed to run (heating below 55 °F outdoors, AC above 78 °F, both adjustable). On mild days with heat and AC off the house floats and the replacement air costs nothing.

The upstairs fraction starts as an assumption. With an upstairs T/RH sensor the app learns it from fan starts: just after a start, the basement's moisture trend changes by what the fan air brings in, and comparing days when upstairs air is drier or wetter than outdoor air separates the two paths (`calibration.fit_upstairs_fraction`). Once the estimate reaches medium confidence it replaces the assumption (Diagnostics shows both).

### Psychrometrics

The formulas are from ASHRAE Fundamentals 2017, Chapter 1. Saturation pressure uses Hyland-Wexler over liquid water, which matches how consumer RH sensors report. Humidity ratio uses `W = 0.621945·pw/(p−pw)`. Specific volume and enthalpy use the standard moist-air equations. Dew point comes from numerically inverting the same saturation equation, so the two always agree. Everything internal is SI and converted only for display. The ideal-gas humidity ratio differs from ASHRAE's tabulated values by about 0.4 % (the enhancement factor), which is far below sensor accuracy.

### Simulation

The model is a single well-mixed air zone with a fixed dry-air mass and 1-minute steps:

```
M·dW/dt = fan outdoor flow·(W_out−W) + fan upstairs flow·(W_up−W) + infiltration·(W_out−W)
          + moisture sources − dehumidifier + buffer exchange − condensation
```

- The **buffer** is a simple wood and concrete reservoir: flux to the air = (S − capacity·RH) ÷ τ. Set capacity to 0 to turn it off. The default is 400 kg with τ = 36 h: the air in 24,000 ft³ holds only about 10 kg of water, while the floor framing, subfloor and concrete exchange hundreds of kilograms between dry and humid seasons. Installs that still had the old 25 kg / 12 h defaults are moved to the new ones once.
- Basement **temperature** uses the same two-node air and structure model as the live controller (see TAMO below). The structure relaxes toward a daily profile, and RH is always derived from W at the simulated temperature.
- Vapor above saturation condenses and is tracked. Every flow goes into a ledger, and the tests check mass conservation to 1e-6 kg.

The ten strategies are 24/7, daytime, nighttime, dew-point controlled (the engine with the Maximum Drying profile), efficiency optimized (the engine with your profile), TAMO, TAMO with forecast, aggressive drying (TAMO, Maximum Drying weights), temperature preservation (TAMO, Temperature Preservation weights) and off. All run under identical conditions, and the simulated controller decides every 5 minutes. TAMO with forecast sees the scenario's own future weather, so it is a retrospective **upper bound**. Results include temperature outcomes: mean, minimum, standard deviation, hours outside the preferred range, hours below the minimum and the largest fan-caused temperature change.

**Climate profiles.** Five built-in New Orleans profiles: typical summer, very humid summer, a 3-day summer dry-air intrusion, fall and winter. They are **synthetic and illustrative**, not NOAA observations or normals, and the app labels them that way. You can also paste a custom hourly CSV or use the app's own recorded outdoor history. `examples/synthetic_nola_summer_day.csv` is a sample file, also labeled synthetic.

### Historical replay

- **Level 1 (History page).** Re-runs each policy over recorded conditions and reports runtime, transfer opportunity, hours adding moisture and condensation-risk hours. It is clearly labeled as **not** being what humidity would have been.
- **Level 2 (Simulation page, "Historical recorded conditions").** Runs the dynamic model on recorded outdoor data. It is labeled modeled.

### Three things kept separate

1. **Moisture-removal opportunity:** vapor the exchanged air could carry (modeled).
2. **Actual basement improvement:** what the measured RH does, which concrete, wood and leaks can overwhelm.
3. **Energy efficiency:** gallons per kWh, including the assumed conditioned-air loss.


## Temperature-aware moisture optimization (TAMO)

TAMO is an optional second control policy inside the same decision engine. It still dries the basement, but it also weighs what the incoming air does to basement temperature. There is one engine, one psychrometric library and one thermal model. The policy only changes how the benefit and costs are weighed.

**Which controller is in charge.** `control.policy` is `moisture` (the original logic, the default) or `tamo`. The policy that is not in charge runs in **shadow mode** every tick. It records what it would have done (`SHADOW_AGREE` / `SHADOW_DISAGREE` events and a `shadow_on` history column) and never switches anything. The Dashboard shows which controller is commanding and what the shadow would do. One click switches between them, which is also the rollback. The History page shows how often they agreed.

### One coupled model for live control and simulation

`coupled.py` is the only calculation library. The live engine, the simulator and the mock basement all call it. Its state is basement air temperature, structure temperature (slab, masonry, joists; not measured), humidity ratio W and the water held in the materials buffer. Each step:

1. dry-air exchange: fan outdoor path, fan upstairs path, natural infiltration
2. water vapor carried with that air, by humidity ratio (never by RH)
3. moisture sources and the materials buffer
4. new W
5. new air and structure temperatures (two-node model, solved exactly)
6. condensation of any vapor above saturation
7. RH and dew point derived from the resulting temperature and W

For every decision the engine runs this forward with fans ON and fans OFF for 15, 30, 60 and 120 minutes, holding outdoor conditions constant. Drying benefit, condensation risk and the temperature penalty are all scored on those **predicted** states. So cold, dry air shows both effects at once: the temperature drop (RH up) and the lower moisture content (RH down). The engine does not reject cold air for its cooling effect while ignoring that it is also removing water.

**Thermal model.** Two heat capacities: the air plus fast contents, and the structure.

```
C_a dT_a/dt = G (T_in − T_a) + H (T_m − T_a)
C_m dT_m/dt = H (T_a − T_m) + K (T_eq − T_m)
```

`G` is incoming dry-air mass flow × c_p, `H` the air-to-structure coupling, `K = C_m / τ`. A single first-order model can't show the quick air drop during a cold run followed by the rebound when fans stop, so two nodes are used. Defaults for a 24,000 ft³ basement: H = 2,000 W/K, C_m = 40 MJ/K, τ = 12 h, scaled with volume. These are **assumptions** (±50 %) until learning replaces them.

**Learning.** Every 6 hours, and on demand from the History page, the app fits H to recorded fan starts with at least 8 °F between basement and incoming air. Confidence is low with fewer than 3 cycles, medium at 3 or more with ±50 %, high at 8 or more with ±25 %. By default a learned value is used only at high confidence. Each fan start also records a prediction that is checked 30 minutes later. If observed cooling exceeds the conservative prediction, or the average error is over 1 °F, the model's uncertainty is raised and a re-fit is triggered.

### Utility, in drying-equivalent grams per hour

All terms share one unit, grams of water per hour, so they can be compared:

```
utility = benefit − thermal penalty − energy penalty − condensation risk − switching cost

benefit  = U × (½ · water removed + ½ · RH relief)            only if water removed > 0
           RH relief = (RH_off − RH_on at the horizon) × dW/d%RH × dry-air mass ÷ hours
thermal  = k_eff × [ max(0, Δd)^n × (1 + d_on / soft tolerance)
                     + swing weight × max(0, |ΔT| − tolerance)^n ]
           d = distance outside the preferred range, Δd = d_on − d_off at the horizon
           moving toward the preferred range earns a credit, capped at half the benefit
k_eff    = k (low 1,000 / medium 3,000 / high 8,000) × profile multiplier × (1 − 0.8·U)
energy   = (fan W + HVAC W to replace conditioned air) × your profile's efficiency threshold in g/kWh (0 for Maximum Drying)
risk     = weight × increase in max(0, 5 °F − predicted surface margin)²
U        = smoothstep from 50 % RH (0) to 70 % RH (1)
```

Profile multipliers: Maximum Drying 0.4, Balanced and Energy Saver 1, Temperature Preservation 2.5. Fans start at utility ≥ 150 and stop below 0. A humidity-ratio advantage is also needed: 0.6 g/kg to start, 0.25 g/kg to keep running. The weights are **heuristics**, labeled as such on the Dashboard. Water removed, predictions and HVAC cost are labeled estimated. Temperatures, RH and fan state are labeled measured.

### Hard limits (both policies)

These block or stop the fans regardless of utility and minimum run times:

- **Minimum basement temperature** (55 °F by default), checked with a *conservative* prediction: thermal parameters pushed toward a bigger temperature drop by their uncertainty, at the decision horizon plus one decision interval (at least 60 minutes while confidence is low).
- **Pipe freeze.** A sensor with role `pipe` blocks cold-air runs below the freeze warning (40 °F) plus the sensor margin. Without one, the Dashboard says pipes near vents aren't monitored, and the min-temperature limit only protects the basement air on average.
- **Surface condensation:** see the condensation rule above (wetter outdoor air reaching a cold surface, or cold air chilling a surface toward its local dew point). The surface is assumed to follow 20 % of the air-temperature change within the horizon until fan cycles teach the real value (concrete is slow).

If RH stays above 70 % for 3 hours and no safe drying exists, the app raises an alert that recommends a dehumidifier.

### Forecast (optional)

When enabled, the app fetches a 2-day hourly forecast from Open-Meteo (no account) every 30 minutes and ignores data older than 3 hours. Without internet it uses live sensors only. If a window within 4 hours offers at least 25 % more utility (and at least 50 g/h more), a start can be deferred, but never longer than 120 minutes, never when urgency is ≥ 0.6, and never by stopping a fan that is already running. Mock hardware uses the mock world's own weather, labeled mock.

### Reason codes and opportunity

Each decision carries a list of structured reasons, primary first. Examples: `MOISTURE_BENEFIT_POSITIVE`, `HUMIDITY_URGENCY_HIGH`, `THERMAL_PENALTY_TOO_HIGH`, `EFFICIENCY_TOO_LOW`, `PREDICTED_MIN_TEMP_BREACH`, `LOCALIZED_FREEZE_RISK`, `BETTER_FORECAST_WINDOW`, `RH_RISES_DURING_DRYING`. The opportunity indicator is excellent, good, marginal, unfavorable or unsafe. It is never better than unsafe while a hard limit blocks.

### RH can rise while drying

Cooling raises RH without adding water. The Dashboard splits each recent RH change into the part from moisture content and the part from temperature, and TAMO flags `RH_RISES_DURING_DRYING` when a run lowers W but leaves RH higher. Material-risk urgency still uses the actual RH, because surfaces respond to RH, not W.

### Limitations

- Predictions hold outdoor conditions constant over the horizon and use a lumped structure. Sun, upstairs HVAC cycles and occupants aren't modeled.
- The structure temperature is estimated from a quasi-steady balance, not measured. A surface or pipe sensor improves safety more than any parameter tuning.
- The utility weights are judgement, not physics. Shadow mode exists so you can compare both controllers on your basement before switching.
- The V2 specification text received was cut off in section 29.B ("Relative humidity can increase during effective drying …"). Anything after that point wasn't implemented.

## Version 3: zones, concrete probe and circulation fans

Version 3 treats the basement as connected zones instead of one well-mixed room. It keeps three jobs separate: exterior ventilation brings drier air in, circulation fans move air between zones, and the concrete probe watches one cold surface for condensation. Exterior ventilation still runs on the V1/V2 engine. V3 changes it only where the probe now feeds the TAMO surface prediction.

### Zones

- A zone is a named part of the basement (Settings, Zones). The defaults are *Main basement* (on the exterior-fan air path), *Remote pocket* and *Moisture-prone area*. Assign each air sensor and the concrete probe to a zone in Settings, Sensors.
- Each zone card shows temperature, RH, dew point, humidity ratio W (with uncertainty), absolute humidity, RH and dew-point trends, time above the circulation thresholds, and sensor quality.
- Zones are compared by **moisture content**, not RH. A difference in W counts as real only when it is larger than the combined uncertainty of the two sensors, which comes from ±3 % RH and ±0.5 °F before a side-by-side comparison and ±1.5 % RH after one. A cooler zone with higher RH but the same W is reported as "RH differs because of temperature, not moisture" (`RH_DIFFERENCE_FROM_TEMPERATURE`).

### Concrete surface probe (Ecowitt WN30 / WN30BL)

- The WN30 is a wired temperature probe on a WN31-style transmitter. It shares the gateway's 8 temperature/RH channels (CH1–8) with the WN31s and reports temperature only, so its humidity field is empty or `--`. Stated accuracy is about ±1 °C (±2 °F). Map its channel (`ecowitt:chN`) with role **surface** and the zone it sits in.
- Install it with the tip in firm contact with the concrete, secured mechanically and covered with a small piece of insulation, and keep the transmitter away from water. Put an air sensor in the same zone, because the margin uses that zone's dew point.
- **Margin** = surface temperature − local air dew point. The risk class uses the *conservative* margin, which subtracts the combined probe and dew-point uncertainty (about 1.4 °F by default). The defaults are: more than 5 °F lower risk, 3–5 °F monitor, 0–3 °F elevated, 0 °F or below condensation possible. Improving a class needs an extra 0.5 °F (hysteresis).
- **Estimated surface RH** = local vapor pressure / saturation pressure at the surface temperature. The display is capped at 100 %, while the uncapped value is kept and recorded. It is not a measurement of moisture inside the concrete.
- The monitor reports three things separately:
  1. **Now:** current conditions from measured indoor air.
  2. **If outdoor air reached the surface:** the ventilation scenario.
  3. **Next 12 hours:** the highest forecast outdoor dew point, a potential future exposure.
  Humid outdoor air alone is never reported as condensation happening now.
- **TAMO** predicts each probe's temperature with and without the exterior fans using the same coupled model. The surface follows a fraction *f* of the air-temperature change (0.5 until learned from data) and the rest of the structure change. TAMO then compares that with the zone's predicted dew point. Cold, dry air is credited when the dew point falls faster than the concrete cools.
- **Alerts:** advisory (sustained conservative margin below 5 °F), warning (below 3 °F), high risk (at or below 0 °F) and persistent (near saturation for 6 h by default). Each alert explains the likely cause, for example "Concrete temperature has fallen while the basement dew point stays elevated". No alert ever says the surface is wet, because the probe cannot detect water.
- If the probe fails, probe-based predictions stop, the general dew-point protection keeps working, and an alert says direct surface monitoring is unavailable.

### Circulation fans (Honeywell HT-900 on smart plugs)

- Each fan has its own plug. The plug uses the same adapter as the exterior fans (Tapo, Shelly, Home Assistant or mock), and a circulator may not share a plug with an exterior fan. Control is ON/OFF only, with the speed set on the fan. Energy is measured where the plug meters power and estimated from rated watts otherwise.
- **Zone connections** (Settings) describe which zones each fan links, the intended direction, assumed exchange flows, and where the fan sits. They describe intended circulation, not proven airflow.
- **Decision priority** for each fan:
  1. Electrical safety: disabled, water-intrusion lockout, or plug state unconfirmed.
  2. Manual override.
  3. Condensation restriction: the fan is blocked when mixing would raise the dew point at a monitored surface (`CIRCULATION_WORSENS_SURFACE_RISK`), and the controller only warns when a surface is at risk but a zone reading is missing.
  4. Surface protection: mixing would bring lower-dew-point air to a surface at risk.
  5. Local humidity and beneficial mixing, triggered by persistent high RH or a moisture difference beyond sensor uncertainty. The fan runs only when the predicted RH drop in the target zone is worthwhile and does not just move the problem.
  6. Exterior-ventilation support, for assigned fans only, plus a short run afterwards.
  7. Optional scheduled mixing (10 min on, 30 min off by default, experimental).
  8. Energy optimization.

  Minimum on/off times and hysteresis apply.
- Circulation **never counts as moisture removal**. When every zone is humid and neither ventilation nor a dehumidifier is removing water, the decision says so (`NO_MOISTURE_REMOVAL_PATH`).
- **Shadow mode is the default.** The controller decides and logs what each fan would do but never switches it. Manual ON/OFF always works except under the water-intrusion lockout. Automatic mode switches only fans marked **commissioned**, and the API refuses automatic mode until at least one fan is.
- **Plug failures:** an unreachable plug shows its state as *unknown*, never as ON or OFF. A command that isn't confirmed is logged and locked out for 30 minutes instead of being retried rapidly. Exterior ventilation and the other fan keep running.

### Commissioning checklist (before automatic circulation)

1. Plug each HT-900 straight into an outlet and check that it runs. Set its speed and confirm the switch stays there.
2. Put it on its smart plug. Switch the plug off and on from the dashboard and confirm the fan restarts by itself.
3. Check the plug's rating against the fan motor (an HT-900 draws roughly 20–40 W).
4. Run **Diagnostics, Sensor side-by-side comparison** with all three air sensors together for 24–48 hours, then apply the stable offsets. Raw readings are kept, and the applied offsets and date are recorded.
5. Check the concrete probe against a reference thermometer taped next to it.
6. Leave circulation in **Shadow** for a week or two. Then look at **History, Circulation effectiveness**, which compares fan-on and fan-off periods under similar exterior ventilation and is informational only.
7. Tick **Commissioned** for each fan (Settings, Circulation fans) and switch circulation to **Automatic**.

**Water and electrical safety:** don't energize fans or plugs in or near standing water. Keep them above known flood levels and use GFCI-protected outlets. A smart plug doesn't make a household fan suitable for a wet location. Sensors can't replace these precautions.

### Multi-zone simulation

The Simulation page compares eight circulation strategies with the same exterior ventilation: none, fan 1, fan 2, both, humidity-triggered, ventilation-assisted, combined adaptive and periodic. Zones exchange air through a mass-balanced two-way exchange, so total water is conserved to rounding (shown as "conserved" in each row). The only water changes come from modeled sources, ventilation, condensation on the concrete node and dehumidification. Results include per-zone mean and max RH, time above target, zone moisture differences, surface risk hours, fan hours, kWh and water removed by exterior ventilation.

### V3 test map

| Spec scenario | Test (`tests/test_v3.py`) |
|---|---|
| A isolated humid pocket | `test_scenario_a_isolated_humid_pocket_turns_on_fan1` |
| B cold pocket, high RH | `test_scenario_b_cold_pocket_high_rh_is_not_called_wetter` |
| C concrete below dew point | `test_scenario_c_concrete_below_dew_point_alerts_and_evaluates_fans` |
| D humid outdoors, cold concrete | `test_scenario_d_humid_outdoors_cold_concrete_flags_outdoor_air_only` |
| E cold dry ventilation | `test_scenario_e_cold_dry_ventilation_uses_probe_in_tamo` |
| F ventilation helps remote pocket | `test_scenario_f_exterior_ventilation_support_turns_on_assigned_fan_only` |
| G fan creates adverse conditions | `test_scenario_g_fan_would_carry_high_dew_point_air_to_cold_wall` |
| H sensor disagreement | `test_scenario_h_colocated_sensor_disagreement`, `…_controller_comparison_and_apply_offsets` |
| I smart plug failure | `test_scenario_i_plug_failure_is_reported_and_not_retried_rapidly` |
| J no removal path | `test_scenario_j_no_removal_path_is_not_called_moisture_removal` |
| physics | internal mixing conserves water, multi-zone simulation conserves water, gradient vs uncertainty, surface RH and classes with hysteresis |
| safety | shadow mode never switches, manual override and expiry, water lockout blocks manual, zone sensor failure, probe failure |
| learning | `test_effectiveness_learning_detects_a_fan_that_mixes` |
| API | `test_api.py::test_v3_zone_surface_circulation_endpoints` |

---

## Fail-safe behavior

| Situation | What happens |
|---|---|
| Sensor stale, missing or implausible | AUTO and SCHEDULE command fans OFF, a red alert appears, polling continues. Old readings are never reused as current. |
| Sensor gateway unreachable | Same as above, plus a gateway alert |
| Plug doesn't confirm a command | Command-failure event and alert. The group is locked out of automatic control for 30 minutes and the controller keeps trying to turn it OFF. |
| Plug unreachable | Last known state shown separately from verified state, group marked unverified, other fans in the group turned off |
| Startup or power restore | Reads every plug, turns anything that was ON to OFF, logs the reconciliation, then resumes |
| Controller computer dies | The plug's own auto-off timer stops the fan within `hardware_max_on_minutes` (30 by default). The controller re-arms it every tick while a fan should run. A dashboard alert appears if the plug has no working timer. |
| Zone sensor fails (V3) | That zone shows as unavailable; no value is extrapolated; circulation rules that need it pause; exterior control continues |
| Concrete probe fails (V3) | Probe predictions stop, general dew-point protection stays, alert shown |
| Circulation plug fails (V3) | State shown as unknown, one logged failure then a 30-minute lockout, other equipment unaffected |
| Manual override | Time-limited and logged. Maintenance mode bypasses interlocks only on purpose and shows a warning. |

Every decision change, command, fault, alert and override is logged with readings, the decision, the reason code, the command and the confirmed switch state (Events page).

The **Diagnostics** page lets you inject mock faults (a frozen sensor, gateway down, an unreachable or stuck plug) and watch the fail-safes work.

## Hardware setup (Settings, Hardware)

### Sensors: Ecowitt GW1200

- **Polling:** enter the gateway's IP. The app reads `http://<gateway>/get_livedata_info` every `poll_seconds`.
- **Push (optional, faster):** in the Ecowitt app, Weather Services, Customized: protocol *Ecowitt*, server = this computer's IP, port 8000, path `/data/report/`, interval 60 s. Push and poll readings merge.
- Click **Discover sensors** to list every channel the gateway reports, then map each one (outdoor, basement rooms, surface probe) and enter calibration offsets.
- Firmware differs between gateways. `python -m ventctl.tools.capture_ecowitt <ip>` saves a raw response you can attach to a bug report.

### Fans: TP-Link Tapo (Matter mini plugs)

- Provider **tapo**. Enter each plug's IP (give it a DHCP reservation) and your TP-Link account email and password. They are used only to derive the local KLAP key. Control stays on your network, and Matter pairing doesn't interfere.
- **Watchdog:** the app turns on the plug's built-in auto-off timer. Re-sending ON doesn't restart that timer, so while a fan should keep running the app switches the plug OFF and ON for about a second every N/2 minutes. The timer also applies when you use the Tapo app.
- **This house:** Tapo P125M minis (Patrick has six): one for the Tjernlund V2D, one for each HT-900 circulation fan, three spare. The P125M is rated 15 A / 1800 W, far above these fans. It has no power meter, so runtime is measured and energy is estimated from each fan's rated watts.
- **Limits:** the P125M/P100M minis don't measure power, so "unexpected power" and motor-load alerts are unavailable (the P110M and P115 do measure it). TP-Link firmware updates have broken local access before. If that happens, update python-kasa or switch to provider **homeassistant** and control the plug over Matter. Check the plug's rating against the fan motor.
- Use **Test switch** to confirm ON, OFF and the readback before enabling AUTO.

### Fans: Shelly or Home Assistant

- **Shelly** Gen1 and Gen2/Plus: each command carries a `toggle_after`/`timer`, so the plug turns itself off if the controller stops.
- **Home Assistant:** URL plus a long-lived token. Sensor and switch entities map to the app's ids. HA has no plug-side timer, so the dashboard shows that the watchdog is missing.

### Dehumidifier and fan groups

- A dehumidifier on a metering plug is monitored read-only: runtime, kWh and estimated water. With *require ventilation to beat dehumidifier* on, fans run only when their g/kWh beats the dehumidifier's.
- Each fan group has its own zones, fans, mode and decision. History aggregates every zone and keeps per-group detail.

## Model calibration (Reports page)

Fits natural air changes and effective fan CFM to how basement humidity actually responded, using 30-minute blocks and least squares with 95 % ranges. Results are suggestions only. You click Apply, and safety limits are never touched. If the fans rarely switched, the fit says the parameters are uncertain and to keep your assumptions.

## Weather data and reports

- **NOAA:** on the Simulation page, enter a station (MSY is `72231012916`, Lakefront is `72231753917`; both are in the station list) and dates, then download. Hourly observations are cached in SQLite and can drive simulations and the annual estimate. Small gaps are filled and large gaps are refused.
- **Seasonal report:** mean and max RH, hours above 60 % and 70 %, dew points, runtime, modeled vapor moved, fan kWh, conditioned-air cost and dehumidifier use, by month or season.
- **Annual estimate:** simulates a representative 6-day period per month under off, 24/7 and optimized control, then scales to the month. It uses NOAA data where cached and synthetic profiles elsewhere, and labels each month.

## Calibrating sensors and assumptions

- **Sensors:** use Diagnostics, Sensor side-by-side comparison: place the air sensors together for 24–48 hours, then apply the suggested offsets. You can also type offsets in Settings, Sensors.
- **Surface temperature:** without a surface probe, measure the coldest slab or wall spot with an IR thermometer in the warmest, most humid week. Enter a value a few °F *below* that as the conservative estimate. Auto mode stays off until you do this or add a probe.
- **Fan airflow:** rated CFM overstates installed airflow, often by 20–40 % with ducting and grilles. Enter your best estimate as "actual CFM".
- **Defaults for this house:** one Tjernlund V2D UnderAire (two 110 CFM fans, 220 CFM and about 40 W total, on one plug) exhausting a 24,000 ft³ basement. That is about 0.55 air changes per hour from the fan. The V2D sits in a vent opening with no ducting, so the default takes the full 220 CFM as installed airflow. The calibration on the Reports page can check that once a few weeks of data exist.
- **Upstairs fraction:** start with 10–25 % for an old tongue-and-groove floor. A differential-pressure sensor can replace the guess.

## Safety limitations

- The app never overrides required combustion air, flood openings or code-required ventilation.
- Exhaust-only ventilation depressurizes the basement. Have any naturally vented gas appliance checked for backdrafting before running unattended, and turn on the combustion interlock if one exists.
- Make sure each smart plug is rated for the fan's motor load.
- Don't use a smart plug in place of a dehumidifier's own compressor controls.
- All modeled numbers (moisture transfer, conditioned-air loss, simulations) are estimates. Measured values come only from sensors and plugs.

## Project layout

```
backend/ventctl/
  psychro.py      ASHRAE moist-air equations
  airflow.py      fan flow paths, moisture transfer, energy and conditioned-air penalty
  engine.py       pure decision engine (no I/O): modes, safety, hysteresis, explanations
  sensors.py      readings, validation, calibration, zone aggregation
  zones.py        V3 zone status, moisture gradient vs sensor uncertainty, sensor comparison
  surfaces.py     V3 concrete probe margin, surface RH, risk classes, alerts' causes, follow-fraction fit
  circulation.py  V3 circulation-fan decision engine and two-box mixing physics
  zonesim.py      V3 multi-zone simulation and the eight circulation strategies
  controller.py   runtime loop, switching, verification, fail-safes, alerts, history
  coupled.py      coupled temperature-moisture model shared by engine, simulator and mock
  thermal.py      two-node air/structure thermal model (exact solution)
  tamo.py         temperature-aware utility, urgency, predictions, opportunity, forecast choice
  forecast.py     optional Open-Meteo forecast with cache and expiry
  simulation.py   dynamic model + ten strategies + insights
  climate.py      synthetic profiles, CSV import
  analysis.py     statistics, Level 1 replay, recorded profiles
  calibration.py  least-squares fit of air changes and fan CFM; thermal coupling fit
  weather.py      NOAA global-hourly download, parsing, cache
  reports.py      seasonal report and annual estimate
  storage.py      SQLite schema + migrations
  hardware/       mock, Ecowitt, Tapo, Shelly, Home Assistant adapters
  tools/          Ecowitt capture tool
  app.py          FastAPI REST API + static dashboard
frontend/src/     React + TypeScript + Recharts dashboard
examples/         example config, synthetic sample day
deploy/           macOS LaunchAgent
```

## Test map (spec section 18)

| # | Test |
|---|---|
| 1, 2 | `test_engine.py::test_outside_rh_higher_but_dewpoint_lower_is_eligible`, `…rh_lower_but_dewpoint_higher_stays_off` |
| 3, 4 | `…79f_95pct…positive_drying`, `…79f_70pct…moisture_addition` |
| 5 | `…dewpoint_above_cold_concrete_blocks_ventilation` |
| 6 | `…fluctuating_around_threshold_does_not_cycle`, min on/off tests |
| 7, 18 | `test_controller.py::test_outdoor_sensor_stops_updating…`, `…gateway_outage…`, engine `…missing_outdoor_reading…` |
| 8 | `test_controller.py::test_fan_not_responding_records_command_failure` |
| 9 | `test_controller.py::test_startup_reconciles_and_turns_running_fans_off` |
| 10, 11 | `…target_reached…`, `…high_indoor_rh_but_wetter_outside…` |
| 12 | `…energy_saver_leaves_fans_off…` |
| 13 | `…multi_room_zone_selection_and_aggregation` |
| 14 | `test_simulation.py::test_simulation_conserves_moisture_mass` (4 scenarios × 6 strategies) |
| 15 | `…continuous_and_scheduled_runtimes` |
| 16 | `…replay_is_labeled_as_opportunity_not_counterfactual` |
| 17 | `…negative_removal_is_reported_as_moisture_addition`, simulation insights |
| hardware | `test_hardware.py` (Ecowitt poll/push parsing, Shelly, Home Assistant, Tapo auto-off and re-arm, watchdog when controller stops) |
| phase 3–4 | `test_calibration.py`, `test_phase4.py` (NOAA parsing and gaps, seasonal and annual reports, API) |
| units | `test_psychro.py` (ASHRAE table values, dew point, humidity ratio, conversions, calibration) |
| TAMO 1–12 | `test_tamo.py::test_s1_…` to `test_s12_…` (plus s4b, s8b, s11b) |
| TAMO physics | `test_tamo.py`: coupled credit for drying despite cooling, shared model, continuity and monotonicity, hysteresis, pipe freeze, shadow mode, thermal learning, pressure and elevation, subfreezing, zero airflow, imperial formula, 1.08 rule, two-node energy balance |

## Not yet tested on real hardware

The adapters are built from documented formats and tested against fake devices. The first run against your gateway and plugs may need small fixes. NOAA downloads couldn't be tried from the build sandbox. The Docker image wasn't built there either.
