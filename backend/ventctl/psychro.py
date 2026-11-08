"""Psychrometric calculations for moist air.

Formulas follow ASHRAE Handbook - Fundamentals (2017), Chapter 1:
  * saturation vapor pressure over liquid water: Hyland-Wexler (eq. 6)
  * humidity ratio: W = 0.621945 * pw / (p - pw)  (eq. 20 / 22)
  * moist-air specific volume per kg dry air (eq. 26)
  * moist-air enthalpy per kg dry air (eq. 30)

Relative humidity from consumer sensors (Ecowitt etc.) is reported relative to
liquid water, so saturation over water is used at all temperatures and the dew
point below 0 C is the (supercooled) dew point, not the frost point.

Units: temperature deg C, pressure kPa, humidity ratio kg water / kg dry air,
RH as a fraction 0..1 unless a name says "pct".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .units import STD_PRESSURE_KPA, c_to_f

# Hyland-Wexler coefficients, saturation over liquid water, 0..200 C (ASHRAE eq. 6)
_C8 = -5.8002206e3
_C9 = 1.3914993
_C10 = -4.8640239e-2
_C11 = 4.1764768e-5
_C12 = -1.4452093e-8
_C13 = 6.5459673

MW_RATIO = 0.621945  # molecular mass ratio water / dry air
R_DA = 0.287042  # kJ/(kg K), dry air gas constant
CP_DA = 1.006  # kJ/(kg K)
CP_V = 1.86  # kJ/(kg K)
H_FG0 = 2501.0  # kJ/kg


def saturation_pressure(t_c: float) -> float:
    """Saturation vapor pressure over liquid water [kPa] at t_c [deg C]."""
    if t_c < -60 or t_c > 120:
        raise ValueError(f"temperature {t_c} C outside supported range")
    t = t_c + 273.15
    ln_p = _C8 / t + _C9 + _C10 * t + _C11 * t**2 + _C12 * t**3 + _C13 * math.log(t)
    return math.exp(ln_p) / 1000.0  # Pa -> kPa


def vapor_pressure(t_c: float, rh: float) -> float:
    """Partial pressure of water vapor [kPa]."""
    _check_rh(rh)
    return rh * saturation_pressure(t_c)


def humidity_ratio_from_pw(pw: float, p: float = STD_PRESSURE_KPA) -> float:
    if pw >= p:
        raise ValueError("vapor pressure must be below total pressure")
    return MW_RATIO * pw / (p - pw)


def pw_from_humidity_ratio(w: float, p: float = STD_PRESSURE_KPA) -> float:
    if w < 0:
        raise ValueError("humidity ratio must be >= 0")
    return p * w / (MW_RATIO + w)


def humidity_ratio(t_c: float, rh: float, p: float = STD_PRESSURE_KPA) -> float:
    """Humidity ratio W [kg water / kg dry air]."""
    return humidity_ratio_from_pw(vapor_pressure(t_c, rh), p)


def dew_point_from_pw(pw: float) -> float:
    """Dew point [deg C] for a vapor pressure [kPa].

    Inverts the Hyland-Wexler equation numerically (Newton with bisection
    fallback) so dew point is exactly consistent with saturation_pressure().
    """
    if pw <= 0:
        raise ValueError("vapor pressure must be positive")
    lo, hi = -60.0, 120.0
    if pw <= saturation_pressure(lo):
        return lo
    if pw >= saturation_pressure(hi):
        return hi
    # Magnus initial guess
    g = math.log(pw / 0.61094)
    t = 243.04 * g / (17.625 - g)
    t = min(max(t, lo), hi)
    for _ in range(50):
        f = saturation_pressure(t) - pw
        if abs(f) < 1e-10:
            return t
        if f > 0:
            hi = t
        else:
            lo = t
        dt = 0.001
        deriv = (saturation_pressure(t + dt) - saturation_pressure(t - dt)) / (2 * dt)
        t_new = t - f / deriv if deriv > 0 else (lo + hi) / 2
        if not (lo < t_new < hi):
            t_new = (lo + hi) / 2
        if abs(t_new - t) < 1e-9:
            return t_new
        t = t_new
    return t


def dew_point(t_c: float, rh: float) -> float:
    """Dew point temperature [deg C]."""
    return dew_point_from_pw(vapor_pressure(t_c, rh))


def rh_from_dew_point(t_c: float, td_c: float) -> float:
    """RH fraction for air at t_c with dew point td_c (capped at 1)."""
    return min(1.0, saturation_pressure(td_c) / saturation_pressure(t_c))


def rh_from_humidity_ratio(t_c: float, w: float, p: float = STD_PRESSURE_KPA) -> float:
    """RH fraction for air at t_c holding humidity ratio w (may exceed 1 = supersaturated)."""
    return pw_from_humidity_ratio(w, p) / saturation_pressure(t_c)


def humidity_ratio_from_dew_point(td_c: float, p: float = STD_PRESSURE_KPA) -> float:
    return humidity_ratio_from_pw(saturation_pressure(td_c), p)


def specific_volume(t_c: float, w: float, p: float = STD_PRESSURE_KPA) -> float:
    """Moist-air specific volume [m3 per kg dry air]."""
    return R_DA * (t_c + 273.15) * (1 + 1.607858 * w) / p


def dry_air_density(t_c: float, w: float, p: float = STD_PRESSURE_KPA) -> float:
    """kg of dry air per m3 of moist air."""
    return 1.0 / specific_volume(t_c, w, p)


def enthalpy(t_c: float, w: float) -> float:
    """Moist-air enthalpy [kJ per kg dry air]."""
    return CP_DA * t_c + w * (H_FG0 + CP_V * t_c)


def absolute_humidity(t_c: float, rh: float, p: float = STD_PRESSURE_KPA) -> float:
    """Water vapor density [g/m3]."""
    w = humidity_ratio(t_c, rh, p)
    return w * dry_air_density(t_c, w, p) * 1000.0


def _check_rh(rh: float) -> None:
    if not (0.0 <= rh <= 1.0):
        raise ValueError(f"RH must be a fraction 0..1, got {rh}")


@dataclass(frozen=True)
class AirState:
    """A fully-derived moist air state. Construct with AirState.from_t_rh()."""

    t_c: float
    rh: float
    p_kpa: float
    pw_kpa: float
    w: float
    dew_point_c: float
    abs_humidity_g_m3: float
    enthalpy_kj_kg: float

    @classmethod
    def from_t_rh(cls, t_c: float, rh: float, p_kpa: float | None = None) -> "AirState":
        p = p_kpa or STD_PRESSURE_KPA
        pw = vapor_pressure(t_c, rh)
        w = humidity_ratio_from_pw(pw, p)
        return cls(
            t_c=t_c,
            rh=rh,
            p_kpa=p,
            pw_kpa=pw,
            w=w,
            dew_point_c=dew_point_from_pw(pw),
            abs_humidity_g_m3=w * dry_air_density(t_c, w, p) * 1000.0,
            enthalpy_kj_kg=enthalpy(t_c, w),
        )

    @classmethod
    def from_t_w(cls, t_c: float, w: float, p_kpa: float | None = None) -> "AirState":
        p = p_kpa or STD_PRESSURE_KPA
        pw = pw_from_humidity_ratio(w, p)
        # Supersaturated input (fog) is capped at saturation.
        rh = min(1.0, pw / saturation_pressure(t_c))
        return cls.from_t_rh(t_c, rh, p)

    def rh_at(self, t_c: float) -> float:
        """RH this air would have if warmed/cooled (no moisture change) to t_c. May exceed 1."""
        return rh_from_humidity_ratio(t_c, self.w, self.p_kpa)

    def to_dict(self) -> dict:
        return {
            "t_c": self.t_c,
            "t_f": c_to_f(self.t_c),
            "rh_pct": self.rh * 100.0,
            "p_kpa": self.p_kpa,
            "pw_kpa": self.pw_kpa,
            "w": self.w,
            "w_g_per_kg": self.w * 1000.0,
            "dew_point_c": self.dew_point_c,
            "dew_point_f": c_to_f(self.dew_point_c),
            "abs_humidity_g_m3": self.abs_humidity_g_m3,
            "enthalpy_kj_kg": self.enthalpy_kj_kg,
        }
