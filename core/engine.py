"""
engine.py - the calculation engine.

This file does ONLY arithmetic. It never looks anything up, never calls an
AI model and never talks to the database. Give it per-100 g values and gram
weights; it gives back numbers. That makes every result reproducible and
easy to test (see tests/test_engine.py).

Rules it follows
----------------
* A missing value is None, never 0. A total that includes a missing value is
  reported as "incomplete" and lists which ingredients are missing it.
* Full precision is kept; rounding happens only in display_value().
* kJ are converted to kcal only when kcal is missing: kcal = kJ / 4.184.
"""

from dataclasses import dataclass, field

KJ_PER_KCAL = 4.184

# The nutrients the app tracks, in display order: (key, label, unit, decimals)
NUTRIENTS = [
    ("energy_kcal", "Calories", "kcal", 0),
    ("protein_g", "Protein", "g", 1),
    ("carbohydrate_g", "Carbohydrate", "g", 1),
    ("fat_g", "Fat", "g", 1),
    ("fibre_g", "Fibre", "g", 1),
    ("sugars_g", "Sugars", "g", 1),
    ("saturates_g", "Saturates", "g", 1),
    ("salt_g", "Salt", "g", 2),
]
NUTRIENT_KEYS = [n[0] for n in NUTRIENTS]

# How uncertain each way of getting a quantity is (used for the range shown
# beside the total). These are starting values to tune, not scientific facts.
UNCERTAINTY = {
    "weighed": 0.10,      # you weighed it; database values still vary ~10%
    "label": 0.05,        # weighed + manufacturer label
    "portion": 0.20,      # household measure or standard portion (1 cup, 1 large)
    "default": 0.50,      # "I don't know" - a default was used
}


@dataclass
class Line:
    """One ingredient line of a meal."""
    name: str                      # what the user sees
    grams: float                   # grams actually eaten
    per100: dict                   # nutrient key -> value per 100 g (or None)
    source_label: str = ""         # e.g. 'CoFID 2021'
    basis: str = "weighed"         # one of the UNCERTAINTY keys
    assumptions: list = field(default_factory=list)


def fill_energy(per100: dict) -> dict:
    """Return a copy of per100 where kcal is filled from kJ if kcal is missing."""
    values = dict(per100)
    if values.get("energy_kcal") is None and values.get("energy_kj") is not None:
        values["energy_kcal"] = values["energy_kj"] / KJ_PER_KCAL
    return values


def scale(per100: dict, grams: float) -> dict:
    """Nutrients in `grams` of a food: value_per_100g * grams / 100."""
    if grams is None or grams < 0:
        raise ValueError("grams must be zero or more")
    per100 = fill_energy(per100)
    result = {}
    for key in NUTRIENT_KEYS:
        value = per100.get(key)
        result[key] = None if value is None else value * grams / 100.0
    return result


def per_serving_to_per100(per_serving: dict, serving_g: float) -> dict:
    """Convert label values given 'per serving' into per-100 g values."""
    if not serving_g or serving_g <= 0:
        raise ValueError("serving size must be more than 0 g")
    return {k: (None if v is None else v * 100.0 / serving_g) for k, v in per_serving.items()}


def calculate(lines: list, fraction_eaten: float = 1.0) -> dict:
    """
    Calculate a whole meal.

    Returns a dictionary with:
      lines      - each ingredient with its nutrient values
      totals     - meal totals per nutrient (None if nothing known)
      incomplete - nutrient key -> names of ingredients missing that value
      total_grams, kcal_low, kcal_high, confidence, assumptions
    """
    if not 0 < fraction_eaten <= 1:
        raise ValueError("fraction eaten must be more than 0 and at most 1")

    out_lines = []
    totals = {k: None for k in NUTRIENT_KEYS}
    incomplete = {k: [] for k in NUTRIENT_KEYS}
    kcal_spread = 0.0
    estimated_kcal = 0.0
    total_grams = 0.0
    assumptions = []

    for line in lines:
        grams = line.grams * fraction_eaten
        values = scale(line.per100, grams)
        total_grams += grams
        for key in NUTRIENT_KEYS:
            if values[key] is None:
                incomplete[key].append(line.name)
            else:
                totals[key] = (totals[key] or 0.0) + values[key]
        kcal = values["energy_kcal"] or 0.0
        band = UNCERTAINTY.get(line.basis, UNCERTAINTY["default"])
        kcal_spread += kcal * band
        if line.basis in ("portion", "default"):
            estimated_kcal += kcal
        for a in line.assumptions:
            assumptions.append(f"{line.name}: {a}")
        out_lines.append({
            "name": line.name,
            "grams": grams,
            "source": line.source_label,
            "basis": line.basis,
            "values": values,
        })

    kcal_total = totals["energy_kcal"] or 0.0
    if kcal_total == 0:
        confidence = "unknown"
    elif estimated_kcal / kcal_total > 0.5:
        confidence = "low"
    elif estimated_kcal / kcal_total > 0.2:
        confidence = "moderate"
    else:
        confidence = "high"

    if fraction_eaten < 1:
        assumptions.append(f"You ate {fraction_eaten:.0%} of the meal; every line is scaled by that.")

    return {
        "lines": out_lines,
        "totals": totals,
        "incomplete": {k: v for k, v in incomplete.items() if v},
        "total_grams": total_grams,
        "kcal_low": max(kcal_total - kcal_spread, 0.0),
        "kcal_high": kcal_total + kcal_spread,
        "confidence": confidence,
        "assumptions": assumptions,
    }


def per_100g_of_meal(result: dict) -> dict:
    """Nutrients per 100 g of the whole meal (based on ingredient weights)."""
    g = result["total_grams"]
    if g <= 0:
        return {k: None for k in NUTRIENT_KEYS}
    return {k: (None if v is None else v * 100.0 / g) for k, v in result["totals"].items()}


def display_value(value, decimals: int) -> str:
    """Round only for display. None becomes 'n/a' (not available), never 0."""
    if value is None:
        return "n/a"
    if decimals == 0:
        return f"{round(value):,}"
    return f"{value:,.{decimals}f}"
