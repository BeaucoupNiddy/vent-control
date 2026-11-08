# Basement Smart Ventilation Controller

A local web app that decides when exchanging basement air with outdoor air actually helps, and runs the fans only then. It compares the **moisture content** (humidity ratio) of basement and outdoor air rather than relative humidity. It checks condensation risk, fan electricity and the cost of pulling conditioned air down from upstairs, and it explains every decision in plain language.

**Status: Phase 1.** This includes the decision engine, psychrometrics, the dynamic moisture simulator, mock sensors and plugs, the dashboard, history, Level 1 replay and tests. Real Ecowitt and Shelly hardware comes in Phase 2. Until then the app runs entirely on mock hardware driven by a physical basement model, so mock fans really change mock humidity.

> Ventilation controls moisture exchange, not drainage. It cannot keep a wet New Orleans basement dry on its own.

---

## Quick start

### macOS (native Python)

```bash
# one-time
brew install python@3.12 node            # or use python.org / nvm
git clone <this repo> vent-control && cd vent-control
cd frontend && npm ci && npm run build && cd ..   # builds the dashboard into backend/ventctl/static
cd backend
python3 -m venv .venv && source .venv/bin/activate
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

**Start automatically and restart on failure (macOS):** edit the paths in `deploy/com.ventctl.plist`, copy it to `~/Library/LaunchAgents/` and run `launchctl load ~/Library/LaunchAgents/com.ventctl.plist`. Also set the Mac to restart after a power failure and to never sleep (System Settings, Energy).

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

There are 64 tests. They cover every case in the spec's test list (section 18) plus checks against ASHRAE reference values. See *Test map* below.

---

## How the controller decides

Every 60 seconds, for each fan group:

1. **Mode.** OFF and MANUAL ON obey the user. Manual is time-limited (60 minutes by default) and still subject to hard interlocks. SCHEDULE runs inside its windows but still fails safe. AUTO and SIMULATION run the full logic below. SIMULATION never sends commands.
2. **Hard safety.** These stop the fans immediately and ignore minimum run and off times:
   - indoor and outdoor readings are current (stale after 5 minutes, or the gateway is unreachable)
   - the fan switch state can be verified
   - interlocks: water-intrusion lockout, combustion-appliance lockout for exhaust-only setups, and a depressurization limit if a pressure sensor exists
   - **condensation:** `coldest surface − outdoor dew point ≥ margin` (3 °F). If there is no surface sensor and no user-approved conservative surface estimate, automatic ventilation stays off and the app says condensation safety is unverified.
3. **Direction.** If outdoor air holds more moisture (ΔW ≤ 0), the fans stop immediately, because running them adds water.
4. **Benefit**, with hysteresis and minimum on/off times:
   - basement RH is above target (start at target + 2 %, stop at target)
   - dew-point advantage: start at ≥ 3 °F, keep running while > 1 °F
   - estimated removal = dry-air mass flow × (W_basement − W_incoming) must exceed a minimum
   - efficiency = net removal ÷ (fan watts + extra HVAC watts to replace conditioned air pulled from upstairs) must clear the energy profile's threshold. Maximum Drying has no minimum. Balanced needs 1,500 g/kWh and Energy Saver 5,000 g/kWh. The stop threshold is 70 % of the start threshold.

It never runs fans just because the basement is humid. A 90 % basement with outdoor air that would be 80 % at basement temperature still runs, because that is a real drying benefit. A 65 % basement with outdoor air that would be 85 % stays off.

### Airflow model

Exhaust fans push basement air out. When exhaust exceeds intake, a configurable fraction of the shortfall is assumed to come from the conditioned upstairs through the floor (10 % by default). The rest comes from outdoors through the vents. Air drawn from upstairs is replaced upstairs by outdoor air, which the AC has to cool and dry. That cost is the "conditioned-air penalty" (enthalpy difference ÷ COP). The upstairs fraction is always shown as an assumption, because nothing measures it yet.

### Psychrometrics

The formulas are from ASHRAE Fundamentals 2017, Chapter 1. Saturation pressure uses Hyland-Wexler over liquid water, which matches how consumer RH sensors report. Humidity ratio uses `W = 0.621945·pw/(p−pw)`. Specific volume and enthalpy use the standard moist-air equations. Dew point comes from numerically inverting the same saturation equation, so the two always agree. Everything internal is SI and converted only for display. The ideal-gas humidity ratio differs from ASHRAE's tabulated values by about 0.4 % (the enhancement factor), which is far below sensor accuracy.

### Simulation

The model is a single well-mixed air zone with a fixed dry-air mass and 1-minute steps:

```
M·dW/dt = fan outdoor flow·(W_out−W) + fan upstairs flow·(W_up−W) + infiltration·(W_out−W)
          + moisture sources − dehumidifier + buffer exchange − condensation
```

- The **buffer** is a simple wood and concrete reservoir: flux to the air = (S − capacity·RH) ÷ τ. Set capacity to 0 to turn it off.
- Basement **temperature** relaxes toward a daily profile and is nudged by incoming air, so RH is always derived from W at the simulated temperature.
- Vapor above saturation condenses and is tracked. Every flow goes into a ledger, and the tests check mass conservation to 1e-6 kg.

The six strategies are 24/7, daytime, nighttime, dew-point controlled (the engine with the Maximum Drying profile), efficiency optimized (the engine with your profile) and off. All of them run under identical conditions.

**Climate profiles.** Five built-in New Orleans profiles: typical summer, very humid summer, a 3-day summer dry-air intrusion, fall and winter. They are **synthetic and illustrative**, not NOAA observations or normals, and the app labels them that way. You can also paste a custom hourly CSV or use the app's own recorded outdoor history. `examples/synthetic_nola_summer_day.csv` is a sample file, also labeled synthetic.

### Historical replay

- **Level 1 (History page).** Re-runs each policy over recorded conditions and reports runtime, transfer opportunity, hours adding moisture and condensation-risk hours. It is clearly labeled as **not** being what humidity would have been.
- **Level 2 (Simulation page, "Historical recorded conditions").** Runs the dynamic model on recorded outdoor data. It is labeled modeled.

### Three things kept separate

1. **Moisture-removal opportunity:** vapor the exchanged air could carry (modeled).
2. **Actual basement improvement:** what the measured RH does, which concrete, wood and leaks can overwhelm.
3. **Energy efficiency:** gallons per kWh, including the assumed conditioned-air loss.

---

## Fail-safe behavior

| Situation | What happens |
|---|---|
| Sensor stale, missing or implausible | AUTO and SCHEDULE command fans OFF, a red alert appears, polling continues. Old readings are never reused as current. |
| Sensor gateway unreachable | Same as above, plus a gateway alert |
| Plug doesn't confirm a command | Command-failure event and alert. The group is locked out of automatic control for 30 minutes and the controller keeps trying to turn it OFF. |
| Plug unreachable | Last known state shown separately from verified state, group marked unverified, other fans in the group turned off |
| Startup or power restore | Reads every plug, turns anything that was ON to OFF, logs the reconciliation, then resumes |
| Controller computer dies | **Software can't help here.** Configure the plug's own auto-off timer (Shelly `auto_off`) in Phase 2 so a stuck-ON fan stops by itself. |
| Manual override | Time-limited and logged. Maintenance mode bypasses interlocks only on purpose and shows a warning. |

Every decision change, command, fault, alert and override is logged with readings, the decision, the reason code, the command and the confirmed switch state (Events page).

The **Diagnostics** page lets you inject mock faults (a frozen sensor, gateway down, an unreachable or stuck plug) and watch the fail-safes work.

## Calibration

- **Sensors:** place all the WN31s together for a few hours, then enter temperature and RH offsets per sensor (Settings, Sensors).
- **Surface temperature:** without a surface probe, measure the coldest slab or wall spot with an IR thermometer in the warmest, most humid week. Enter a value a few °F *below* that as the conservative estimate. Auto mode stays off until you do this or add a probe.
- **Fan airflow:** rated CFM overstates installed airflow, often by 20–40 % with ducting and grilles. Enter your best estimate as "actual CFM".
- **Upstairs fraction:** start with 10–25 % for an old tongue-and-groove floor. A differential-pressure sensor (Phase 3) can replace the guess.

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
  controller.py   runtime loop, switching, verification, fail-safes, alerts, history
  simulation.py   dynamic moisture model + six strategies + insights
  climate.py      synthetic profiles, CSV import
  analysis.py     statistics, Level 1 replay, recorded profiles
  storage.py      SQLite schema + migrations
  hardware/       adapter interfaces, mock hardware, Phase 2 placeholders
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
| units | `test_psychro.py` (ASHRAE table values, dew point, humidity ratio, conversions, calibration) |

## Roadmap

- **Phase 2:** Ecowitt GW1200 client (built from a captured response from your gateway), sensor discovery and mapping, Shelly RPC switching with hardware auto-off, live switch health.
- **Phase 3:** multiple zones and fan groups in the UI, surface and pressure sensing, dehumidifier monitoring, model calibration with uncertainty ranges.
- **Phase 4:** weather-data imports (with local caching), seasonal reports, annual estimates.
