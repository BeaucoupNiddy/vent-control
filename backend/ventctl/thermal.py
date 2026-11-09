"""Basement thermal model: two coupled heat capacities (air and structure).

Why two nodes: the basement air is small and responds within minutes, while
the slab, masonry and wood floor assembly are large and respond over hours. A
cold-air run therefore drops the *air* temperature quickly by a fraction of
the indoor/outdoor difference, then cools the *structure* slowly. When the
fans stop, the air rebounds toward the structure temperature. A single
first-order model cannot show both.

    C_a dT_a/dt = G (T_src - T_a) + H (T_m - T_a)
    C_m dT_m/dt = H (T_a - T_m) + K (T_eq - T_m)

  T_a   basement air (what the sensors read)
  T_m   structure (slab, masonry, joists) - not measured
  G     incoming-air conductance = dry-air mass flow x c_p [W/K]
        (fans plus natural infiltration), T_src the mass-weighted inlet temperature
  H     air-to-structure heat transfer [W/K] (learned from fan cycles when possible)
  K     structure-to-ground/house coupling = C_m / tau [W/K], T_eq its equilibrium

The system is linear, so it is solved exactly (2x2 matrix exponential) instead
of stepped. The same functions drive the simulator, the mock basement and the
decision engine's predictions, so there is one thermal model in the app.

Default parameters are rough estimates for a 24,000 ft3 basement and scale
with volume. They are assumptions until the learning step fits H to observed
fan cycles. Predictions carry an uncertainty used for conservative safety
checks.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from . import psychro
from .units import M3_PER_FT3

REF_VOLUME_FT3 = 24000.0
DEFAULT_COUPLING_W_K = 2000.0  # ~720 m2 of surfaces x ~3 W/m2K natural convection
DEFAULT_STRUCTURE_MJ_K = 40.0  # wood floor assembly + top of slab + masonry that takes part over hours
AIR_CONTENTS_FACTOR = 3.0  # air plus fast-responding contents and surface layers
ASSUMED_UNCERTAINTY = 0.5  # +/- 50% on coupling and structure mass until learned


@dataclass
class ThermalParams:
    air_heat_capacity_j_k: float
    coupling_w_k: float
    structure_heat_capacity_j_k: float
    structure_tau_h: float
    source: str = "assumed"  # assumed | learned
    confidence: str = "low"  # low | medium | high
    uncertainty: float = ASSUMED_UNCERTAINTY  # fractional

    @property
    def k_w_k(self) -> float:
        return self.structure_heat_capacity_j_k / (self.structure_tau_h * 3600)

    def conservative(self) -> "ThermalParams":
        """Parameters that make fan-driven temperature change larger (for safety checks)."""
        f = max(0.05, 1 - self.uncertainty)
        return ThermalParams(self.air_heat_capacity_j_k * f, self.coupling_w_k * f,
                             self.structure_heat_capacity_j_k * f, self.structure_tau_h, self.source,
                             self.confidence, self.uncertainty)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["coupling_w_k"] = round(self.coupling_w_k, 1)
        d["structure_heat_capacity_mj_k"] = round(self.structure_heat_capacity_j_k / 1e6, 2)
        d["air_heat_capacity_mj_k"] = round(self.air_heat_capacity_j_k / 1e6, 3)
        return d


def default_params(volume_ft3: float, coupling_w_k: float | None = None, structure_mj_k: float | None = None,
                   structure_tau_h: float = 12.0) -> ThermalParams:
    vol_m3 = volume_ft3 * M3_PER_FT3
    scale = volume_ft3 / REF_VOLUME_FT3
    c_air = vol_m3 * 1.18 * psychro.CP_DA * 1000 * AIR_CONTENTS_FACTOR
    h = coupling_w_k if coupling_w_k else DEFAULT_COUPLING_W_K * scale ** (2 / 3)
    cm = (structure_mj_k if structure_mj_k else DEFAULT_STRUCTURE_MJ_K * scale) * 1e6
    source = "configured" if (coupling_w_k or structure_mj_k) else "assumed"
    return ThermalParams(c_air, h, cm, structure_tau_h, source, "low", ASSUMED_UNCERTAINTY)


def params_from_config(config, learned: dict | None = None) -> ThermalParams:
    t = config.temperature
    p = default_params(config.house.basement_volume_ft3, t.air_structure_coupling_w_k,
                       t.structure_heat_capacity_mj_k, t.structure_tau_h)
    if learned and t.learning_enabled and not t.air_structure_coupling_w_k:
        need = ("high",) if t.learning_confidence == "conservative" else ("medium", "high")
        if learned.get("confidence") in need and learned.get("coupling_w_k"):
            p.coupling_w_k = float(learned["coupling_w_k"])
            p.source, p.confidence = "learned", learned["confidence"]
            p.uncertainty = max(0.15, float(learned.get("uncertainty", 0.3)))
    if learned:
        p.uncertainty = min(0.8, p.uncertainty + float(learned.get("extra_uncertainty", 0.0)))
    return p


def conductance_w_k(dry_air_kg_s: float) -> float:
    return dry_air_kg_s * psychro.CP_DA * 1000


def _solve(p: ThermalParams, ta: float, tm: float, g: float, t_src: float, t_eq: float, t_s: float
           ) -> tuple[float, float]:
    """Exact solution after t_s seconds with constant inputs."""
    ca, cm, h, k = p.air_heat_capacity_j_k, p.structure_heat_capacity_j_k, p.coupling_w_k, p.k_w_k
    a11, a12 = -(g + h) / ca, h / ca
    a21, a22 = h / cm, -(h + k) / cm
    c1, c2 = g * t_src / ca, k * t_eq / cm
    det = a11 * a22 - a12 * a21
    # steady state x* = -A^-1 c
    xs1 = -(a22 * c1 - a12 * c2) / det
    xs2 = -(-a21 * c1 + a11 * c2) / det
    d1, d2 = ta - xs1, tm - xs2
    tr = a11 + a22
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
    l1, l2 = tr / 2 + disc, tr / 2 - disc
    if abs(l1 - l2) < 1e-15:
        e = math.exp(l1 * t_s)
        m11, m12, m21, m22 = e * (1 + t_s * (a11 - l1)), e * t_s * a12, e * t_s * a21, e * (1 + t_s * (a22 - l1))
    else:
        e1, e2 = math.exp(l1 * t_s), math.exp(l2 * t_s)
        f0 = (l1 * e2 - l2 * e1) / (l1 - l2)
        f1 = (e1 - e2) / (l1 - l2)
        m11, m12, m21, m22 = f0 + f1 * a11, f1 * a12, f1 * a21, f0 + f1 * a22
    return xs1 + m11 * d1 + m12 * d2, xs2 + m21 * d1 + m22 * d2


def step(p: ThermalParams, ta: float, tm: float, g: float, t_src: float, t_eq: float, dt_s: float
         ) -> tuple[float, float]:
    return _solve(p, ta, tm, g, t_src, t_eq, dt_s)


def structure_estimate(p: ThermalParams, ta: float, g_now: float, t_src_now: float) -> float:
    """Unmeasured structure temperature, assuming the air is near its quasi-steady state."""
    if g_now <= 0:
        return ta
    return (ta * (g_now + p.coupling_w_k) - g_now * t_src_now) / p.coupling_w_k


@dataclass
class Projection:
    minutes: list[int]
    off_c: list[float]
    on_c: list[float]
    on_conservative_c: list[float]
    structure_on_c: list[float]
    structure_off_c: list[float]

    def fan_effect_c(self, i: int) -> float:
        return self.on_c[i] - self.off_c[i]

    def to_dict(self) -> dict:
        return asdict(self)


def project(p: ThermalParams, ta: float, g_on: float, t_src_on: float, g_off: float, t_src_off: float,
            g_now: float, t_src_now: float, minutes: tuple[int, ...] = (15, 30, 60, 120),
            t_eq: float | None = None) -> Projection:
    """Predict basement air temperature with fans ON vs OFF from the current state."""
    tm = structure_estimate(p, ta, g_now, t_src_now)
    teq = tm if t_eq is None else t_eq
    pc = p.conservative()
    tm_c = structure_estimate(pc, ta, g_now, t_src_now)
    off, on, onc, son, soff = [], [], [], [], []
    for m in minutes:
        a, s = _solve(p, ta, tm, g_on, t_src_on, teq, m * 60)
        on.append(a)
        son.append(s)
        a, s = _solve(p, ta, tm, g_off, t_src_off, teq, m * 60)
        off.append(a)
        soff.append(s)
        onc.append(_solve(pc, ta, tm_c, g_on, t_src_on, tm_c if t_eq is None else t_eq, m * 60)[0])
    return Projection(list(minutes), off, on, onc, son, soff)


def sensible_heat_w(dry_air_kg_s: float, t_inlet_c: float, t_basement_c: float) -> float:
    """Sensible heat added to basement air by incoming air [W]. + = warming, - = cooling."""
    return dry_air_kg_s * psychro.CP_DA * 1000 * (t_inlet_c - t_basement_c)

