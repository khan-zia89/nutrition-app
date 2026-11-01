"""
units.py - converting amounts like '2 tbsp' or '1 large' into grams.

Three kinds of unit:
  mass    g, kg, oz, lb                -> exact conversion
  volume  ml, l, tsp, tbsp, cup, fl oz -> needs a density (grams per ml)
  count   item, slice, small/medium/large ... -> needs a portion weight
          for that specific food (from the USDA portion table)
"""

import re

MASS_G = {"g": 1.0, "kg": 1000.0, "mg": 0.001, "oz": 28.349523125, "lb": 453.59237}

# Metric household measures. A US cup is 236.6 ml; a metric cup is 250 ml.
VOLUME_ML = {"ml": 1.0, "l": 1000.0, "tsp": 5.0, "tbsp": 15.0, "cup": 240.0, "fl oz": 29.5735}

COUNT_UNITS = ["item", "slice", "small", "medium", "large", "extra large", "serving"]

# Spellings people type -> the unit names above
ALIASES = {
    "g": "g", "gram": "g", "grams": "g", "gr": "g", "gm": "g", "gms": "g",
    "kg": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "mg": "mg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "ml": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "l": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
    "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsps": "tbsp", "tbs": "tbsp",
    "cup": "cup", "cups": "cup", "mug": "cup", "mugs": "cup",
    "fl oz": "fl oz", "floz": "fl oz",
    "slice": "slice", "slices": "slice",
    "piece": "item", "pieces": "item", "item": "item", "items": "item", "whole": "item",
    "small": "small", "medium": "medium", "large": "large", "big": "large",
    "serving": "serving", "servings": "serving", "portion": "serving", "portions": "serving",
}

# Liquids where density is about 1 g/ml (assumed when the food has no portion data).
DENSITY_NOTE = "assumed 1 ml = 1 g (no density data for this food)"


def normalise_unit(word: str | None) -> str | None:
    if not word:
        return None
    return ALIASES.get(word.strip().lower().rstrip("."))


def unit_kind(unit: str | None) -> str | None:
    if unit in MASS_G:
        return "mass"
    if unit in VOLUME_ML:
        return "volume"
    if unit in COUNT_UNITS:
        return "count"
    return None


def find_portion(unit: str, portions: list) -> dict | None:
    """
    Look for a portion that matches a unit, e.g. unit 'large' -> '1 large'
    or unit 'cup' -> '1 cup, chopped'. `portions` is a list of dicts with
    'label' and 'grams'. Returns the best match or None.
    """
    if not portions:
        return None
    words = {
        "item": ["medium", "whole", "large", "item", "piece", "each"],
        "slice": ["slice"],
        "small": ["small"],
        "medium": ["medium"],
        "large": ["large"],
        "extra large": ["extra large", "jumbo"],
        "cup": ["cup"],
        "tbsp": ["tbsp", "tablespoon"],
        "tsp": ["tsp", "teaspoon"],
        "serving": ["serving", "portion"],
        "fl oz": ["fl oz"],
    }.get(unit, [unit])
    for w in words:
        for p in portions:
            label = p["label"].lower()
            if re.search(rf"\b{re.escape(w)}\b", label):
                # skip things like 'small' matching 'extra small' when looking for small
                return p
    return None


def to_grams(amount: float, unit: str, portions: list | None = None) -> tuple[float, str, str]:
    """
    Convert an amount to grams.

    Returns (grams, basis, note):
      basis 'weighed'  - exact mass conversion
      basis 'portion'  - used a portion weight or a volume measure
    Raises ValueError if it cannot be converted (the app then asks you).
    """
    if amount is None or amount <= 0:
        raise ValueError("amount must be more than 0")
    unit = normalise_unit(unit) or unit
    kind = unit_kind(unit)

    if kind == "mass":
        return amount * MASS_G[unit], "weighed", ""

    portion = find_portion(unit, portions or [])
    if portion:
        # portion weights are stored per ONE unit (e.g. label '1 large', grams 50)
        per_unit = portion["grams"]
        return amount * per_unit, "portion", (
            f"1 {unit} = {per_unit:g} g (portion '{portion['label']}' from {portion['source']})")

    if kind == "volume":
        grams = amount * VOLUME_ML[unit]
        if unit in ("ml", "l"):
            return grams, "weighed", DENSITY_NOTE
        return grams, "portion", f"1 {unit} = {VOLUME_ML[unit]:g} ml; {DENSITY_NOTE}"

    raise ValueError(f"No weight known for '{unit}' of this food")
