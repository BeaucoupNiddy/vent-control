"""Unit conversions.

Everything inside the engine and simulator is SI (deg C, kPa, kg/kg, m3/s, W).
These helpers convert at the edges (settings, display).
"""

CFM_TO_M3S = 0.00047194745  # 1 ft3/min in m3/s
M3_PER_FT3 = 0.028316846592
FT2_TO_M2 = 0.09290304
FT_TO_M = 0.3048
KG_PER_LB = 0.45359237
LITERS_PER_GALLON = 3.785411784  # US gallon
LITERS_PER_PINT = 0.473176473  # US pint
STD_PRESSURE_KPA = 101.325


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def delta_f_to_c(df: float) -> float:
    """Convert a temperature *difference* in deg F to deg C (K)."""
    return df * 5.0 / 9.0


def delta_c_to_f(dc: float) -> float:
    return dc * 9.0 / 5.0


def cfm_to_m3s(cfm: float) -> float:
    return cfm * CFM_TO_M3S


def m3s_to_cfm(m3s: float) -> float:
    return m3s / CFM_TO_M3S


def grams_to_gallons(g: float) -> float:
    """Liquid-water-equivalent gallons for a mass of water vapor (1 L ~ 1 kg)."""
    return g / 1000.0 / LITERS_PER_GALLON


def grams_to_pounds(g: float) -> float:
    return g / 1000.0 / KG_PER_LB


def g_per_h_to_gal_per_day(gph: float) -> float:
    return grams_to_gallons(gph * 24.0)


def pints_per_day_to_g_per_h(ppd: float) -> float:
    return ppd * LITERS_PER_PINT * 1000.0 / 24.0


def gal_per_day_to_g_per_h(gpd: float) -> float:
    return gpd * LITERS_PER_GALLON * 1000.0 / 24.0


def inhg_to_kpa(inhg: float) -> float:
    return inhg * 3.386389


def hpa_to_kpa(hpa: float) -> float:
    return hpa / 10.0
