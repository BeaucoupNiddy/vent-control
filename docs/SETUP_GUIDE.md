# Setup guide: basement ventilation controller

This guide takes you from a box of parts to a basement the app looks after on its own. It has three parts:

1. **Plan and install the hardware** (sections 1-4): where each device goes and how to set it up in its own app.
2. **Connect it to the controller app** (section 5): the app's **Setup** tab walks you through this and checks each item.
3. **The first weeks** (sections 6-7): what to watch, and fixes for common problems.

Allow an afternoon for the physical work and about an hour for the app. You can stop at any point: the app's guided setup has a **Pause** button and saves everything on the controller, so you can resume from your Mac or your phone.

> **Worksheet.** Open the app, go to **Setup → Worksheet**, and print it (or fill it in on screen; it saves as you type). Write each item down as you install it. A plain copy is at the end of this guide.

---

## 1. What you need

| Item | How many | Job |
|---|---|---|
| Ecowitt **GW1200** gateway | 1 | Receives every sensor's radio signal and shares the readings on your Wi-Fi |
| Ecowitt temperature + humidity sensors (WN31 / WH31) | 3 indoor + 1 outdoor | One per basement zone, one outside |
| Ecowitt **WN30BL** probe thermometer | 1 | Measures the concrete wall's temperature, to warn before condensation |
| **Tjernlund V2D UnderAire** | 1 | The exterior fan: pulls damp basement air out through a foundation vent (2 × 110 CFM, about 40 W) |
| **Honeywell HT-900** fans | 2 | Circulation fans: move air between basement areas |
| TP-Link **Tapo P125M** smart plugs | 3 used (you have 6) | Let the app switch each fan on and off |
| Mac that stays on | 1 | Runs the controller app (already installed) |
| AA batteries (lithium recommended outdoors), painter's tape, a small square of foam board, cable ties | | |

The app works only on your home network. Nothing needs a cloud service, and you don't need a TP-Link account.

## 2. How it fits together

```
 Sensors ──radio──▶ GW1200 gateway ──Wi-Fi──▶ Mac (controller app) ──Wi-Fi──▶ Tapo plugs ──▶ fans
 (basement,                                     decides when the                 (V2D exterior fan,
  outdoor, probe)                               fans should run                   2 × HT-900)
```

The app runs the exterior fan only when the outdoor air holds less water than the basement air, and stops it before any surface gets close to condensation. The circulation fans start in **shadow mode**: the app records what it would do without switching them, so you can review its choices first.

## 3. Plan where everything goes

Walk the basement with the worksheet and decide on three **zones**:

- **Main basement**: the area with the exterior fan.
- **Remote pocket**: the part farthest from the fan, where air goes stale.
- **Moisture-prone area**: where it smells or feels dampest, often a corner against the soil.

Rename them in the app to whatever you call them. For each zone, measure floor area × ceiling height to get its volume. The three should add up to roughly the whole basement (3,000 ft² × 8 ft = 24,000 ft³).

Then mark on a rough sketch:

- one sensor spot in each zone,
- the coldest stretch of outside wall in the damp zone (for the concrete probe),
- the foundation vent for the exterior fan,
- where each circulation fan will sit and which way it blows (normally from the main basement towards the remote pocket, and from the pocket into the damp area),
- which outlet each plug will use. Basements need **GFCI-protected** outlets.

## 4. Physical installation

Work in this order: gateway first, so you can watch each sensor appear as you go.

### 4.1 Ecowitt GW1200 gateway

1. Plug the gateway into its USB power adapter **upstairs**, somewhere central and within about 100 ft (through floors) of the basement sensors. Near the basement stairs is ideal.
2. On your phone, install the **Ecowitt** app, create an account, then **+ → Add device → GW1200**. Follow the steps to join it to your Wi-Fi. It needs the **2.4 GHz** network. If your router combines 2.4 and 5 GHz under one name, that usually works; if pairing fails, temporarily separate them in your router app.
3. Find the gateway's **address (IP)**: in the Ecowitt app open the gateway → **More → Device info**, or look in your router app's device list. Write it on the worksheet.
4. In your router app, **reserve that address** for the gateway ("DHCP reservation", "fixed IP" or "reserved IP"). Otherwise it can change after a power cut and the controller loses it.
5. Optional: update the gateway firmware from the Ecowitt app.

The GW1200 has its own temperature and humidity sensor. If it sits upstairs, the app can use it as the **upstairs** reading.

### 4.2 Basement temperature and humidity sensors (WN31 / WH31)

Each sensor has a **channel switch** inside its battery door. Each sensor must be on a different channel.

1. Open the battery door, set the channel **before** inserting batteries: CH1 for the main basement, CH2 for the remote pocket, CH3 for the moisture-prone area (CH4 if your outdoor sensor is also a WN31). Write the channel on the worksheet and on a strip of tape on the sensor.
2. Insert the batteries. Within a few minutes the sensor appears in the Ecowitt app under the gateway.
3. Place each sensor:
   - about chest height (4-5 ft), on a post, shelf or hook, not touching an outside wall,
   - out of the direct air stream of any fan, the dehumidifier exhaust, the water heater or dryer,
   - not on the floor and not in direct sun from a window.
4. Check in the Ecowitt app that all three report.

### 4.3 Outdoor sensor

1. Set its channel (if it's a WN31) and insert batteries. Lithium AAs last much longer outdoors.
2. Mount it outside on the **north or shaded side**, under an eave, about 5 ft up, out of rain and sun. Keep it away from the AC condenser, dryer vent and the exterior fan's outlet: each of these blows air that isn't representative of the outdoors.
3. A small radiation shield (or an open-bottomed box) helps if no shade is available.

### 4.4 WN30BL concrete probe

This measures the wall's surface temperature so the app can tell how close the wall is to "sweating".

1. Choose the **coldest stretch of outside wall** in the moisture-prone zone, typically low on a wall that is below ground, about 1 ft above the floor. Avoid spots near the water heater or sunlit areas.
2. Clean a small patch of bare concrete (no paint flakes or dust).
3. Lay the probe's tip flat against the concrete and hold it with painter's tape.
4. Cover the tip with a small square of foam board (2-3 in) and tape round it, so the probe reads the wall and not the air.
5. Mount the transmitter box higher up on the wall or a post, cable loose, away from where water could collect.
6. Insert batteries and check it appears in the Ecowitt app. It shows a temperature only (no humidity).

### 4.5 Tjernlund V2D exterior fan

Follow Tjernlund's installation instructions for your foundation vent. Points that matter for this app:

1. Mount it so it **exhausts**: air goes from the basement to outside.
2. Seal round the housing so outside air can't loop straight back in.
3. Leave other foundation vents as they are unless you've decided otherwise; the fan draws replacement air through the house's natural leaks.
4. Run its cord to the outlet you planned for the **exterior-fan plug** (step 4.6). Keep the cord off the floor.
5. If there is a gas or oil water heater, furnace or boiler in the basement that vents up a chimney or metal flue, read section 5, step 10 before running the fan unattended.

### 4.6 Tapo P125M smart plugs

You'll use three: one for the exterior fan and one for each circulation fan.

**No TP-Link account needed.** The P125M is a Matter plug, so you can put it on Wi-Fi with the **Home** app on your iPhone instead of the Tapo app. A plug set up this way isn't linked to any account, and the controller app talks to it directly on your network with no login.

1. Plug the P125M in. Its light should blink (if it doesn't, hold its button about 5 seconds to reset it).
2. On your iPhone, open the **Home** app: **+ → Add Accessory**, and scan the Matter code on the plug or on its box. Choose your Wi-Fi when asked. The phone must be on the same Wi-Fi as the Mac.
3. Do this for each plug. You don't need to name them in the Home app; the controller app has its own names.
4. Leave the plugs' own schedules and timers off, and don't add automations for them in the Home app. The controller app sets each plug's auto-off timer itself, as a safety backstop.
5. Plug each fan into its plug and the plug into a GFCI outlet.
6. Later, in the controller app's setup (step 7 of section 5), press **Find plugs on my Wi-Fi**. It lists each plug with its address; press **Add** on each one. To tell them apart, press the button on one plug and search again: the one that changed on/off is that plug.
7. Optional but recommended: in your router app, **reserve** each plug's address so it never changes. Write the address and MAC on the worksheet.

*Already added a plug in the Tapo app?* That's fine too. The app then needs the email and password of that TP-Link account in step 7, and in the Tapo app turn on **Me → Third-Party Services → Third-Party Compatibility** if you see it. To go account-free instead, remove the plug in the Tapo app, reset it (hold the button about 5 seconds), and add it with the Home app as above.

### 4.7 Honeywell HT-900 circulation fans

1. Place each fan where you planned, **off the floor** (on a shelf, a block, or wall-mounted using its built-in hanger), pointed along the path from the drier area to the damper one.
2. Choose a speed on the fan's own dial (Low usually suffices and is quiet). Write it on the worksheet. The app can only turn the plug on and off; it can't change speed.
3. Leave the fan's own switch **ON**. When the plug turns on, the fan should start by itself. Test this once by pressing the plug's button.
4. Keep the cords clear of any spot that gets wet.

### 4.8 Mac

The app is already installed and starts automatically. Make sure it can keep running:

- **System Settings → Battery → Options** (or **Energy**): turn on **Prevent automatic sleeping on power adapter when the display is off**.
- Keep it plugged in and on the same Wi-Fi as the gateway and plugs.
- Updates arrive by themselves from GitHub every 30 minutes.

---

## 5. Connect everything in the app

Open the app (on the Mac: http://localhost:8000, or from your phone at the Mac's address with `:8000`) and press **Start setup** on the Home page, or open the **Setup** tab.

Each step has a short explanation, the settings for that step, and a **checklist**. Green ticks are fine, amber is worth a look, red must be fixed, and grey boxes are things only you can confirm, so tick them yourself. **Save and check** saves your changes and re-tests the hardware. **Done, next step** unlocks once nothing is red or unticked. **Skip for now** moves on and leaves the step marked as skipped. **Pause** saves your place.

| # | Step | What you do | What the app checks |
|---|---|---|---|
| 1 | Before you start | Confirm the worksheet, Mac sleep setting and router reservations | Your answers |
| 2 | Install the hardware | Tick each item from section 4 | Your answers |
| 3 | Your home | Check time zone (America/Chicago), location and basement volume | Valid time zone, volume entered |
| 4 | Ecowitt gateway | Choose **Ecowitt gateway**, type its address, save | The gateway answers and how many sensors it hears |
| 5 | Sensors | Press **Add** on each new channel, name it, choose where it is and its zone, remove the simulated sensors | One outdoor sensor, an air sensor in every zone, the concrete probe, all reporting fresh readings, no unused channels, battery warnings |
| 6 | Basement zones | Rename the zones, enter sizes, tick the zone with the exterior fan | Sizes add up to the basement, a fan zone is set, dampness shares add to 100 % |
| 7 | Smart plugs | Choose **TP-Link Tapo**, leave the TP-Link email empty, press **Find plugs on my Wi-Fi** and **Add** each plug, choose which plug powers which fan | Valid unique addresses, every fan on its own real plug, each plug answers |
| 8 | Exterior fan | Check the fan details (220 CFM, 40 W), press **Run 2 minutes**, listen, check outside that air blows out | Fan details, plug rated for the motor, your test |
| 9 | Circulation fans | Set location and speed, test each, tick the three checks, press **Mark commissioned**; leave automation on **Shadow** | Speed set, your checks, commissioned, Shadow mode |
| 10 | Safety | Answer the gas appliance question, confirm the CO alarm and that no flood vent is blocked | Gas safety block on if needed, plug safety timer on, plugs confirmed the timer |
| 11 | Comfort targets | Review humidity target (55 %) and temperature range | Target in a sensible range |
| 12 | Turn it on | **Switch to Automatic**, then **Finish setup** | Every earlier step ready, real (not simulated) data, no critical alerts |

Tips for the trickier steps:

- **Which sensor is which?** Hold one sensor in your hand or breathe on it for a minute and press **Refresh live readings**: the one whose humidity jumps is the one you're holding. Ecowitt channel names: `ecowitt:outdoor` is a dedicated outdoor sensor, `ecowitt:ch1`…`ch8` are the WN31 channels, `ecowitt:tf1` is usually the probe, `ecowitt:indoor` is the gateway's own sensor.
- **Faster sensor updates (optional).** In the Ecowitt app: gateway → **Others → DIY Upload Servers / Customized**: turn on, protocol **Ecowitt**, server = the Mac's address, port **8000**, path **/data/report/**, interval **60** s.
- **Gas appliances.** An exhaust fan lowers the basement's air pressure. A water heater or furnace that vents up a chimney or metal flue can then spill fumes into the basement ("backdraft"). If you have one, the app blocks the exhaust fan until a technician does a backdraft test with the fan running. Sealed appliances that vent through plastic pipes straight outside are fine.
- **Your TP-Link password** is stored only in the controller's database on your Mac and is used only to set up the local connection to the plugs.

## 6. The first weeks

- **Day 1**: check the Home page now and then. The fan should run only when the outdoor sensor shows drier air (lower dew point) than the basement.
- **Week 1**: on **Diagnostics**, run a **side-by-side comparison**: put all the T/RH sensors together on a shelf for a few hours and let the app work out small calibration offsets. Then put them back.
- **Weeks 1-2**: circulation fans stay in **Shadow**. On the **Dashboard**, the circulation section shows what the app would have done. If it looks sensible, set circulation to **Automatic** in Settings (only commissioned fans are switched).
- **Monthly**: check sensor batteries (the app warns when one is low) and that the probe is still taped flat with its foam cover.

## 7. Troubleshooting

| Problem | Try |
|---|---|
| "Can't reach the gateway" | Check the address hasn't changed (router app), the Mac is on the same Wi-Fi, and the gateway's light is normal. Open `http://<gateway address>` in a browser on the Mac: it should show the gateway's page. |
| A sensor shows "no fresh reading" | Batteries, distance from the gateway, or the channel changed. Check it reports in the Ecowitt app. |
| A plug "isn't answering" | Press **Find plugs on my Wi-Fi** again: its address may have changed (reserve it in the router). If it says the plug is linked to a TP-Link account, either enter that account's email and password or reset the plug and add it with the Home app. A Tapo firmware update can break local control: update the app (it updates its plug library), or ask for help switching the plug to Home Assistant/Matter. |
| Fan doesn't start on test | Wait a full minute; check the plug turns on with its own button; for the HT-900 check its own switch is on. |
| Fan blinks off for a second every ~15 minutes | Expected: the app re-arms the plug's safety timer while the fan should keep running. |
| Automatic never runs the fan | Usually the outdoor air is wetter than the basement (common in New Orleans summers), or a safety block is active. The Home page and Dashboard say why. |
| Want to start setup again | Setup tab → each step stays editable. Developers can reset progress with `POST /api/setup/reset`. |

---

## Appendix: worksheet (plain copy)

The app's **Setup → Worksheet** is the easiest version: it saves as you type and prints neatly. This copy is for paper.

**Network**
- Wi-Fi name (2.4 GHz): ____________________  Router app: ____________________
- Mac's address: ____________________  Mac set never to sleep? Y / N
- TP-Link account email: ____________________ (never write the password here)

**Ecowitt GW1200**
- Location: ____________________  Address (IP): ____________________  Reserved? Y / N

**Sensors**

| Location | Model | Channel | Exact spot | Batteries in (date) |
|---|---|---|---|---|
| Outdoor | | | | |
| Main basement | | | | |
| Remote pocket | | | | |
| Moisture-prone area | | | | |
| Concrete probe (WN30BL) | | | | |
| Upstairs (gateway) | | | | |

**Smart plugs (Tapo P125M)**

| # | Name in the controller app | Powers | Address (IP) | MAC | Reserved? |
|---|---|---|---|---|---|
| 1 | exterior-fan | Tjernlund V2D | | | |
| 2 | circ-fan-1 | HT-900 #1 | | | |
| 3 | circ-fan-2 | HT-900 #2 | | | |
| 4 | spare | | | | |
| 5 | spare | | | | |
| 6 | spare | | | | |

**Fans**

| Fan | Where | Blows from → to | Speed / airflow | Watts |
|---|---|---|---|---|
| Tjernlund V2D | | basement → outside | 220 CFM | 40 |
| HT-900 #1 | | | | |
| HT-900 #2 | | | | |

**Zones**

| Zone | Your name | Floor area (ft²) | Ceiling (ft) | Volume (ft³) |
|---|---|---|---|---|
| Main basement | | | | |
| Remote pocket | | | | |
| Moisture-prone area | | | | |
| Whole basement | | 3,000 | 8 | 24,000 |

**Safety**
- Gas/oil appliances in the basement: ____________________  How they vent: ____________________
- CO alarm in basement? Y / N   Flood vents clear? Y / N   Standing water now? Y / N
- Dehumidifier (model, setpoint): ____________________
