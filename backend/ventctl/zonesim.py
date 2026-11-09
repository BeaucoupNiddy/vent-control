"""Multi-zone basement model and internal-circulation strategy comparison.

Zones are connected air volumes. Each zone runs the same coupled
temperature-moisture step as the single-zone model (coupled.advance): exterior fan
air enters only zones on the ventilation path, every zone has its own infiltration,
moisture sources, materials buffer and structure temperature. Between zone steps,
every connection exchanges dry air both ways (natural exchange, plus the fan's
exchange when its plug is on) with the exact two-box solution in circulation.py.

Water is conserved. Internal mixing only moves water: the total changes only through
outdoor exchange, sources, the dehumidifier, and condensation (in the air, or on the
modeled cold concrete surface). The ledger tracks every flow and a test checks the
balance.

The concrete surface is a single node: it relaxes toward the local air and toward the
ground with separate time constants. When the local dew point is above it, water
condenses onto it at a rate set by a mass-transfer coefficient (Lewis analogy).
These are illustrative assumptions, labeled modeled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import circulation, coupled, psychro, thermal
from .airflow import ventilation_effect
from .climate import ClimateProfile
from .config import AppConfig, Circulator, Fan, Zone, ZoneConnection
from .psychro import AirState
from .simulation import BasementModel, SimParams
from .units import M3_PER_FT3, c_to_f, delta_f_to_c, f_to_c, grams_to_gallons, pints_per_day_to_g_per_h

SURFACE_TAU_AIR_H = 4.0  # concrete surface relaxation toward local air
SURFACE_TAU_GROUND_H = 8.0  # ... and toward the ground behind it
SURFACE_AREA_M2 = 15.0  # cold area that exchanges vapor with the zone air
MASS_TRANSFER_KG_M2S = 0.003  # h_c / c_p with h_c ~ 3 W/m2K (Lewis analogy), per unit humidity-ratio difference


@dataclass
class Satellite:
    zone: Zone
    st: coupled.State
    mp: coupled.ModelParams
    m_da: float
    t_offset_c: float


@dataclass
class SurfaceNode:
    zone: str
    t_c: float
    ground_c: float
    condensed_kg: float = 0.0


class MultiZoneModel:
    """Main zone = the existing BasementModel (fans, dehumidifier, ledger); other zones are satellites."""

    def __init__(self, params: SimParams, profile: ClimateProfile, fans: list[Fan], house, zones: list[Zone],
                 connections: list[ZoneConnection], circulators: list[Circulator],
                 surface_zone: str | None = None, zone_temp_offsets_f: dict[str, float] | None = None,
                 zone_rh_offsets_pct: dict[str, float] | None = None, surface_offset_f: float = -2.0):
        self.zones = zones
        self.connections = connections
        self.circulators = {c.id: c for c in circulators}
        main = next((z for z in zones if z.on_ventilation_path), zones[0])
        self.main_id = main.id
        shares = {z.id: z.moisture_source_share for z in zones}
        tot = sum(shares.values()) or 1.0
        mp = params.model_copy(update={
            "volume_ft3": main.volume_ft3,
            "moisture_source_pints_day": params.moisture_source_pints_day * shares[main.id] / tot,
            "buffer_capacity_kg": params.buffer_capacity_kg * main.volume_ft3 / max(1, params.volume_ft3),
        })
        self.main = BasementModel(mp, profile, fans, house)
        self.params = params
        offs = zone_temp_offsets_f or {}
        rhoffs = zone_rh_offsets_pct or {}
        self.sats: dict[str, Satellite] = {}
        others = [z for z in zones if z.id != main.id]
        for i, z in enumerate(others):
            off = delta_f_to_c(offs.get(z.id, -1.5 * (i + 1)))
            t = self.main.t_c + off
            rh = min(0.98, self.main.rh + rhoffs.get(z.id, 0.0) / 100)
            w = psychro.humidity_ratio(t, rh)
            vol = z.volume_ft3 * M3_PER_FT3
            frac = z.volume_ft3 / max(1, params.volume_ft3)
            tp = thermal.default_params(z.volume_ft3, None, None, params.structure_tau_h)
            zmp = coupled.ModelParams(tp, vol, params.natural_ach * 0.5,
                                      pints_per_day_to_g_per_h(params.moisture_source_pints_day) * shares[z.id] / tot,
                                      params.buffer_capacity_kg * frac, params.buffer_tau_h)
            st = coupled.State(t, t, w, zmp.buffer_capacity_kg * rh)
            self.sats[z.id] = Satellite(z, st, zmp, vol * psychro.dry_air_density(t, w), off)
        self.surface: SurfaceNode | None = None
        if surface_zone:
            t0 = self.zone_air(surface_zone).t_c + delta_f_to_c(surface_offset_f)
            self.surface = SurfaceNode(surface_zone, t0, self.main.mean_t_c + delta_f_to_c(surface_offset_f - 2.0))
        self.sat_ledger = {"sources": 0.0, "infiltration": 0.0, "condensed": 0.0}
        self.circ_on: dict[str, bool] = {}

    # -------------------------------------------------------------- state access
    def zone_air(self, zid: str) -> AirState:
        if zid == self.main_id:
            return self.main.air()
        s = self.sats[zid].st
        return AirState.from_t_w(s.t_c, s.w)

    def zone_mass(self, zid: str) -> float:
        return self.main.m_da if zid == self.main_id else self.sats[zid].m_da

    def _set(self, zid: str, t_c: float, w: float) -> None:
        if zid == self.main_id:
            self.main.t_c, self.main.w = t_c, w
        else:
            self.sats[zid].st.t_c, self.sats[zid].st.w = t_c, w

    def water_inventory(self) -> float:
        tot = self.main.water_inventory()
        for s in self.sats.values():
            tot += s.m_da * s.st.w + s.st.buffer_kg
        return tot

    def net_external_water(self) -> float:
        L = self.main.ledger
        return (L.fan_outdoor + L.fan_upstairs + L.infiltration + L.sources + L.dehumidifier + L.condensed
                + self.sat_ledger["sources"] + self.sat_ledger["infiltration"] + self.sat_ledger["condensed"])

    # -------------------------------------------------------------- step
    def step(self, hour: float, dt_s: float, fans_on: list[Fan], circ_on: dict[str, bool] | None = None,
             outdoor: AirState | None = None) -> dict:
        circ_on = circ_on if circ_on is not None else self.circ_on
        out = outdoor or self.main.outdoor_at(hour)
        info = self.main.step(hour, dt_s, fans_on, out)
        t_eq = self.main.t_eq_at(hour)
        up = AirState.from_t_rh(f_to_c(self.main.house.upstairs_temp_f), self.main.house.upstairs_rh_pct / 100)
        for s in self.sats.values():
            bd = coupled.Boundary(out, up, 0.0, 0.0, t_eq + s.t_offset_c)
            r = coupled.advance(s.mp, s.st, bd, dt_s, s.m_da)
            self.sat_ledger["sources"] += r.d_src
            self.sat_ledger["infiltration"] += r.d_inf
            self.sat_ledger["condensed"] += r.d_cond
        # internal exchange between zones (water-conserving)
        for k in self.connections:
            if k.zone_a not in self._ids() or k.zone_b not in self._ids():
                continue
            cfm = k.natural_cfm + (k.fan_exchange_cfm if (k.circulator_id and circ_on.get(k.circulator_id)) else 0.0)
            a, b = self.zone_air(k.zone_a), self.zone_air(k.zone_b)
            ma, mb = self.zone_mass(k.zone_a), self.zone_mass(k.zone_b)
            ta, wa, tb, wb = circulation.mix_masses(a.t_c, a.w, ma, b.t_c, b.w, mb,
                                                    circulation.exchange_kg_s(cfm, a, b), dt_s)
            self._set(k.zone_a, ta, wa)
            self._set(k.zone_b, tb, wb)
        # concrete surface node and condensation onto it
        if self.surface is not None:
            sn = self.surface
            loc = self.zone_air(sn.zone)
            sn.t_c += dt_s / 3600 * ((loc.t_c - sn.t_c) / SURFACE_TAU_AIR_H + (sn.ground_c - sn.t_c) / SURFACE_TAU_GROUND_H)
            w_sat = psychro.humidity_ratio(sn.t_c, 1.0)
            if loc.w > w_sat:
                m = self.zone_mass(sn.zone)
                dm = min((loc.w - w_sat) * m, MASS_TRANSFER_KG_M2S * SURFACE_AREA_M2 * (loc.w - w_sat) * dt_s)
                self._set(sn.zone, loc.t_c, loc.w - dm / m)
                sn.condensed_kg += dm
                self.sat_ledger["condensed"] -= dm
        return info

    def _ids(self) -> set[str]:
        return {self.main_id, *self.sats}


# ---------------------------------------------------------------- strategy comparison
CIRC_STRATEGIES = {
    "none": ("1. No internal circulation", "Circulation fans stay off."),
    "fan1": ("2. Fan 1 continuous", "First circulation fan runs all the time."),
    "fan2": ("3. Fan 2 continuous", "Second circulation fan runs all the time."),
    "both": ("4. Both continuous", "Both circulation fans run all the time."),
    "humidity": ("5. Humidity-triggered", "Fans run when a zone has persistent high RH or a reliable moisture "
                 "difference that mixing would improve (no ventilation support)."),
    "ventilation": ("6. Ventilation-assisted", "Fans assigned to ventilation support run with the exterior fans."),
    "adaptive": ("7. Combined adaptive", "The full circulation engine: humidity, surface protection and ventilation "
                 "support."),
    "periodic": ("8. Periodic mixing", "Every fan runs 10 minutes, then rests 30 minutes."),
}


def _strategy_cfg(base: AppConfig, strategy: str) -> AppConfig:
    cfg = base.model_copy(deep=True)
    cs = cfg.circulation
    cs.mode = "auto"
    for c in cfg.circulators:
        c.commissioned = True
        c.auto_enabled = True
    if strategy == "humidity":
        cs.ventilation_support = False
        cs.periodic_mixing = False
    elif strategy == "adaptive":
        cs.ventilation_support = True
        cs.periodic_mixing = False
    elif strategy == "periodic":
        cs.periodic_mixing, cs.mixing_on_min, cs.mixing_off_min = True, 10, 30
        cs.mixing_windows, cs.mixing_fans = [], []
        cs.energy_optimization = False
    return cfg


def run_circulation_strategy(strategy: str, params: SimParams, profile: ClimateProfile, base: AppConfig,
                             exterior_strategy: str = "optimized", surface_zone: str | None = None) -> dict:
    from .engine import GroupState, decide
    from .config import FanGroup
    from .sensors import Conditions
    from .simulation import _sim_config, sim_fans
    from .surfaces import SurfaceStatus, classify, surface_rh
    from .zones import ZoneStatus, ZoneTracker, rh_level, w_uncertainty

    cfg = _strategy_cfg(base, strategy)
    fans = sim_fans(params)
    ext_cfg = _sim_config(cfg, params, fans, exterior_strategy)
    group = ext_cfg.groups[0]
    zones = cfg.zones
    if surface_zone is None:
        surface_zone = next((m.zone for m in cfg.sensors.mappings if m.role.value == "surface" and m.zone), None)
        if surface_zone not in {z.id for z in zones}:
            surface_zone = zones[-1].id
    model = MultiZoneModel(params, profile, fans, cfg.house, zones, cfg.connections, cfg.circulators,
                           surface_zone=surface_zone)
    inv0 = model.water_inventory()
    ext0 = model.net_external_water()
    circs = cfg.circulators
    states = {c.id: circulation.CircState() for c in circs}
    gstate = GroupState(is_on=False, verified=True)
    tracker = ZoneTracker()
    ss, cs = cfg.surfaces, cfg.circulation
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dt = params.step_s
    steps = int(round(params.duration_h * 3600 / dt))
    every = max(1, int(round(params.decision_every_s / dt)))
    zs_stats = {z.id: {"rh_sum": 0.0, "rh_max": 0.0, "t_sum": 0.0, "t_min": 1e9, "above_h": 0.0} for z in zones}
    grad_sum = {k.id: 0.0 for k in cfg.connections}
    risk_h = possible_h = 0.0
    s_min, s_sum = 1e9, 0.0
    circ_h = {c.id: 0.0 for c in circs}
    ext_h = 0.0
    kwh = 0.0
    series = []
    hrs = dt / 3600
    target = cfg.control.upper_rh_pct
    for k_step in range(steps):
        now = t0 + timedelta(seconds=k_step * dt)
        hour = params.start_hour + k_step * dt / 3600
        out = model.main.outdoor_at(hour)
        if k_step % every == 0:
            # zone statuses as the live controller would see them (exact sensors in the simulation)
            zst = {}
            for z in zones:
                a = model.zone_air(z.id)
                mins = tracker.update(now, z.id, a, cs)
                zst[z.id] = ZoneStatus(z.id, z.name, a, [], "ok", "good", cs.rh_uncertainty_calibrated_pct,
                                       w_uncertainty(a, cs.rh_uncertainty_calibrated_pct, cs.temp_uncertainty_f),
                                       rh_level(a.rh * 100, cs), {}, mins, False, z.volume_ft3,
                                       z.on_ventilation_path, z.ventilation_exposure)
            surfs = []
            if model.surface:
                loc = model.zone_air(model.surface.zone)
                u = math.hypot(ss.probe_uncertainty_f, ss.dew_point_uncertainty_f)
                m = (model.surface.t_c - loc.dew_point_c) * 1.8
                sst = SurfaceStatus("sim-surface", "Concrete surface", model.surface.zone, model.surface.zone,
                                    model.surface.t_c, "ok", loc, "zone", m, u, m - u, surface_rh(loc, model.surface.t_c))
                sst.risk = classify(m - u, ss)
                surfs.append(sst)
            # exterior ventilation: the regular engine on the wettest zone (as configured)
            wettest = max((zst[z.id].air for z in zones), key=lambda a: a.rh)
            cond = Conditions(indoor=wettest, indoor_label="sim", indoor_problem=None, outdoor=out,
                              outdoor_problem=None, surface_c=model.surface.t_c if model.surface else model.main.surface_c,
                              surface_source="measured", surface_label="sim", upstairs=None, diff_pressure_pa=None,
                              surfaces=[{"name": "Concrete surface", "t_c": model.surface.t_c, "zone": model.surface.zone,
                                         "local": model.zone_air(model.surface.zone), "exposure": 1.0}]
                              if model.surface else [])
            if exterior_strategy == "off":
                ext_on = False
            else:
                d = decide(cond, gstate, ext_cfg, group, now, detail=False)
                if d.target_on != gstate.is_on:
                    gstate.last_change = now
                gstate.is_on = d.target_on
                ext_on = gstate.is_on
            exterior = {"on": ext_on, "drying": ext_on}
            for c in circs:
                stt = states[c.id]
                if strategy == "none":
                    on = False
                elif strategy in ("fan1", "fan2", "both"):
                    idx = circs.index(c)
                    on = strategy == "both" or (strategy == "fan1" and idx == 0) or (strategy == "fan2" and idx == 1)
                elif strategy == "ventilation":
                    on = bool(c.ventilation_support and ext_on)
                else:
                    cd = circulation.decide_circulator(c, cfg, zst, surfs, stt, now, exterior, None,
                                                       local_hour=hour % 24)
                    on = cd.target_on
                    stt.last_reason = cd.reason_code
                if on != stt.is_on:
                    stt.last_change = now
                stt.is_on = on
            model.circ_on = {c.id: states[c.id].is_on for c in circs}
        ext_fans = fans if gstate.is_on and exterior_strategy != "off" else []
        model.step(hour, dt, ext_fans, model.circ_on, out)
        # accounting
        for z in zones:
            a = model.zone_air(z.id)
            s = zs_stats[z.id]
            rh = a.rh * 100
            s["rh_sum"] += rh
            s["rh_max"] = max(s["rh_max"], rh)
            s["t_sum"] += c_to_f(a.t_c)
            s["t_min"] = min(s["t_min"], c_to_f(a.t_c))
            if rh > target:
                s["above_h"] += hrs
        for k in cfg.connections:
            grad_sum[k.id] += abs(model.zone_air(k.zone_a).w - model.zone_air(k.zone_b).w) * 1000
        if model.surface:
            loc = model.zone_air(model.surface.zone)
            m = (model.surface.t_c - loc.dew_point_c) * 1.8 - math.hypot(ss.probe_uncertainty_f,
                                                                         ss.dew_point_uncertainty_f)
            if m < ss.elevated_margin_f:
                risk_h += hrs
            if m <= ss.possible_margin_f:
                possible_h += hrs
            s_min = min(s_min, c_to_f(model.surface.t_c))
            s_sum += c_to_f(model.surface.t_c)
        if ext_fans:
            ext_h += hrs
            kwh += sum(f.rated_w for f in ext_fans) * hrs / 1000
        for c in circs:
            if model.circ_on.get(c.id):
                circ_h[c.id] += hrs
                kwh += c.rated_w * hrs / 1000
        if k_step % max(1, int(params.output_every_min * 60 / dt)) == 0:
            pt = {"h": round(k_step * dt / 3600, 3), "ext_on": 1 if ext_fans else 0}
            for z in zones:
                a = model.zone_air(z.id)
                pt[f"rh_{z.id}"] = round(a.rh * 100, 2)
                pt[f"td_{z.id}"] = round(c_to_f(a.dew_point_c), 2)
            if model.surface:
                pt["surface_f"] = round(c_to_f(model.surface.t_c), 2)
            for c in circs:
                pt[f"on_{c.id}"] = 1 if model.circ_on.get(c.id) else 0
            series.append(pt)
    n = max(1, steps)
    inv1 = model.water_inventory()
    balance_err = (inv1 - inv0) - (model.net_external_water() - ext0)
    L = model.main.ledger
    removed = -(L.fan_outdoor + L.fan_upstairs)
    name, desc = CIRC_STRATEGIES[strategy]
    return {
        "id": strategy, "name": name, "description": desc,
        "summary": {
            "zones": {z.id: {"name": z.name, "mean_rh": zs_stats[z.id]["rh_sum"] / n, "max_rh": zs_stats[z.id]["rh_max"],
                             "hours_above_target": zs_stats[z.id]["above_h"],
                             "mean_temp_f": zs_stats[z.id]["t_sum"] / n, "min_temp_f": zs_stats[z.id]["t_min"]}
                      for z in zones},
            "worst_zone_mean_rh": max(zs_stats[z.id]["rh_sum"] / n for z in zones),
            "max_zone_rh": max(zs_stats[z.id]["rh_max"] for z in zones),
            "mean_gradient_g_kg": {k: v / n for k, v in grad_sum.items()},
            "surface_risk_hours": risk_h, "condensation_possible_hours": possible_h,
            "surface_condensed_kg": model.surface.condensed_kg if model.surface else 0.0,
            "surface_min_f": s_min if model.surface else None,
            "surface_mean_f": s_sum / n if model.surface else None,
            "circulator_hours": circ_h, "exterior_hours": ext_h, "kwh": kwh,
            "kwh_circulation": sum(circ_h[c.id] * c.rated_w / 1000 for c in circs),
            "exterior_removed_gal": grams_to_gallons(removed * 1000),
            "circulation_removed_gal": 0.0,  # internal fans move water; they never remove it
            "water_balance_error_kg": balance_err,
        },
        "series": series,
    }


def compare_circulation(params: SimParams, profile: ClimateProfile, base: AppConfig,
                        strategies: list[str] | None = None, exterior_strategy: str = "optimized") -> dict:
    if len(base.zones) < 2 or not base.connections:
        raise ValueError("Multi-zone simulation needs at least two zones and one connection (Settings > Zones)")
    ids = strategies or list(CIRC_STRATEGIES)
    results = [run_circulation_strategy(s, params, profile, base, exterior_strategy) for s in ids]
    return {"results": results, "zones": [z.model_dump() for z in base.zones],
            "connections": [k.model_dump() for k in base.connections],
            "circulators": [c.model_dump() for c in base.circulators],
            "label": ("Modeled. Zone exchange flows, zone temperature offsets and the concrete surface response are "
                      "assumptions until measured. Internal fans never remove water; only exterior ventilation and a "
                      "dehumidifier do."),
            "insights": circulation_insights(results)}


def circulation_insights(results: list[dict]) -> list[str]:
    out = []
    by = {r["id"]: r["summary"] for r in results}
    if "none" in by:
        base = by["none"]
        best = min(results, key=lambda r: r["summary"]["worst_zone_mean_rh"])
        if best["id"] != "none":
            d = base["worst_zone_mean_rh"] - best["summary"]["worst_zone_mean_rh"]
            out.append(f"Lowest average RH in the wettest zone: {best['name']} ({d:.1f}% below no circulation).")
        else:
            out.append("No circulation strategy lowered the wettest zone's average RH in this scenario.")
        for r in results:
            s = r["summary"]
            if r["id"] != "none" and s["condensation_possible_hours"] > base["condensation_possible_hours"] + 0.25:
                out.append(f"{r['name']} increased time with condensation possible at the concrete surface "
                           f"({s['condensation_possible_hours']:.1f} h vs {base['condensation_possible_hours']:.1f} h).")
    out.append("Internal circulation moved water between zones; the water actually removed came only from exterior "
               "ventilation.")
    return out
