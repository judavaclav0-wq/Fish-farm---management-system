"""
core/calculations.py
Pure calculation helpers — no Streamlit, no I/O.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Feed conversion & growth
# ---------------------------------------------------------------------------

def fcr(total_feed_kg: float, biomass_gain_kg: float) -> float | None:
    """Feed Conversion Ratio = feed / biomass gain. Returns None if gain <= 0."""
    if biomass_gain_kg <= 0:
        return None
    return round(total_feed_kg / biomass_gain_kg, 3)


def sgr(initial_weight_g: float, final_weight_g: float, days: int) -> float | None:
    """Specific Growth Rate (% per day).
    SGR = 100 * (ln(W_final) - ln(W_initial)) / days
    """
    import math
    if initial_weight_g <= 0 or final_weight_g <= 0 or days <= 0:
        return None
    return round(100 * (math.log(final_weight_g) - math.log(initial_weight_g)) / days, 3)


# ---------------------------------------------------------------------------
# Biomass
# ---------------------------------------------------------------------------

def biomass_kg(fish_count: int, avg_weight_g: float) -> float:
    """Total biomass in kg from fish count and average weight in grams."""
    return round(fish_count * avg_weight_g / 1000, 3)


def tank_load_pct(biomass_kg_: float, max_biomass_kg: float) -> float:
    """Tank load as a percentage of maximum allowed biomass."""
    if max_biomass_kg <= 0:
        return 0.0
    return round(biomass_kg_ / max_biomass_kg * 100, 1)


def stocking_density(biomass_kg_: float, volume_m3: float) -> float:
    """Stocking density in kg/m³."""
    if volume_m3 <= 0:
        return 0.0
    return round(biomass_kg_ / volume_m3, 2)


# ---------------------------------------------------------------------------
# Mortality
# ---------------------------------------------------------------------------

def mortality_rate_pct(dead: int, initial: int) -> float:
    """Cumulative mortality as a percentage of initial stock."""
    if initial <= 0:
        return 0.0
    return round(dead / initial * 100, 2)


def daily_mortality_rate_pct(dead_today: int, fish_count_today: int) -> float:
    """Daily mortality rate as a percentage of current fish count."""
    if fish_count_today <= 0:
        return 0.0
    return round(dead_today / fish_count_today * 100, 3)


def estimated_fish_count(biomass_kg: float, avg_weight_g: float) -> int:
    """Estimate fish count from biomass and average weight.
    Used in Daily Log to compute mortality % without needing the full stock engine.
    """
    if avg_weight_g <= 0:
        return 0
    return int(biomass_kg / (avg_weight_g / 1000))


def daily_mortality_pct(mortality_fish: int, biomass_kg: float, avg_weight_g: float) -> float:
    """Calculate daily mortality as % of estimated fish count.

    Formula (per spec):
        estimated_count = biomass_kg / (avg_weight_g / 1000)
        pct = mortality_fish / estimated_count * 100
    """
    count = estimated_fish_count(biomass_kg, avg_weight_g)
    if count <= 0:
        return 0.0
    return round(mortality_fish / count * 100, 3)


# ---------------------------------------------------------------------------
# Water quality thresholds (generic RAS defaults)
# ---------------------------------------------------------------------------

# (parameter, warn_below, warn_above, critical_below, critical_above)
WQ_THRESHOLDS: dict[str, dict] = {
    "temperature":  {"warn": (10, 20),  "critical": (6, 24),  "unit": "°C"},
    "oxygen":       {"warn": (7, None), "critical": (6, None), "unit": "mg/L"},
    "ph":           {"warn": (6.8, 7.8),"critical": (6.5, 8.2),"unit": ""},
    "nh4":          {"warn": (None, 0.3),"critical": (None, 0.7),"unit": "mg/L"},
    "no2":          {"warn": (None, 0.1),"critical": (None, 0.3),"unit": "mg/L"},
    "no3":          {"warn": (None, 80), "critical": (None, 150),"unit": "mg/L"},
}


def check_wq_param(param: str, value: float) -> str:
    """Return 'ok', 'warn', or 'critical' for a water quality reading.
    Uses the built-in global thresholds (backward compat, used by stock_engine).
    """
    cfg = WQ_THRESHOLDS.get(param)
    if cfg is None or value is None:
        return "ok"
    warn_lo, warn_hi = cfg["warn"]
    crit_lo, crit_hi = cfg["critical"]

    if (crit_lo is not None and value < crit_lo) or \
       (crit_hi is not None and value > crit_hi):
        return "critical"
    if (warn_lo is not None and value < warn_lo) or \
       (warn_hi is not None and value > warn_hi):
        return "warn"
    return "ok"


def check_wq_against_thresholds(param: str, value: float, thresholds: dict) -> str:
    """Check a WQ reading against system-specific min/max thresholds from Farm Setup.

    *thresholds* is the dict stored on a system:
        {"oxygen": {"active": True, "min": 6.0, "max": 12.0}, ...}

    Returns 'ok' or 'warn'.  (System thresholds use only min/max, no separate
    critical level — that distinction lives in the global WQ_THRESHOLDS above.)
    """
    cfg = thresholds.get(param)
    if not cfg or not cfg.get("active") or value is None:
        return "ok"
    lo = cfg.get("min")
    hi = cfg.get("max")
    if lo is not None and value < lo:
        return "warn"
    if hi is not None and value > hi:
        return "warn"
    return "ok"

# ---------------------------------------------------------------------------
# Safe conversion helpers
# ---------------------------------------------------------------------------

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Compatibility wrappers for new stock engine
# ---------------------------------------------------------------------------

def calculate_biomass_kg(fish_count, avg_weight_g):
    fish_count = safe_float(fish_count, 0.0)
    avg_weight_g = safe_float(avg_weight_g, 0.0)
    return biomass_kg(fish_count, avg_weight_g)


def calculate_density_kg_m3(biomass, tank_volume_m3):
    biomass = safe_float(biomass, 0.0)
    tank_volume_m3 = safe_float(tank_volume_m3, 0.0)
    return stocking_density(biomass, tank_volume_m3)