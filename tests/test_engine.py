"""
Tests for the calculation engine. Run all tests with:   python -m pytest
Expected values were worked out by hand, not produced by the engine.
The nutrient values here are made-up test numbers, not real food data.
"""

import pytest

from core import engine

BREAD = {"energy_kcal": 228, "protein_g": 9.0, "carbohydrate_g": 44.0, "fat_g": 2.0, "fibre_g": None}


def test_per_100g():
    # 228 kcal/100 g x 72 g / 100 = 164.16
    assert engine.scale(BREAD, 72)["energy_kcal"] == pytest.approx(164.16)


def test_missing_value_is_none_not_zero():
    assert engine.scale(BREAD, 72)["fibre_g"] is None


def test_kj_only_converts_to_kcal():
    # 1046 kJ / 4.184 = 250 kcal per 100 g
    assert engine.scale({"energy_kj": 1046}, 100)["energy_kcal"] == pytest.approx(250.0)


def test_kcal_preferred_over_kj():
    assert engine.scale({"energy_kcal": 100, "energy_kj": 999}, 100)["energy_kcal"] == 100


def test_per_serving_conversion():
    # 190 kcal per 30 g serving -> 633.33 per 100 g; 45 g eaten -> 285 kcal
    per100 = engine.per_serving_to_per100({"energy_kcal": 190}, 30)
    assert engine.scale(per100, 45)["energy_kcal"] == pytest.approx(285.0)


def test_recipe_totals():
    lines = [
        engine.Line("chicken", 500, {"energy_kcal": 150}),
        engine.Line("onion", 300, {"energy_kcal": 36}),
        engine.Line("tomatoes", 400, {"energy_kcal": 20}),
        engine.Line("oil", 42, {"energy_kcal": 899}),
        engine.Line("coconut milk", 200, {"energy_kcal": 169}),
    ]
    result = engine.calculate(lines)
    assert result["totals"]["energy_kcal"] == pytest.approx(1653.58)
    assert result["total_grams"] == pytest.approx(1442)


def test_fraction_eaten_scales_everything():
    full = engine.calculate([engine.Line("bread", 72, BREAD)])
    half = engine.calculate([engine.Line("bread", 72, BREAD)], fraction_eaten=0.5)
    assert half["totals"]["energy_kcal"] == pytest.approx(full["totals"]["energy_kcal"] / 2)
    assert half["totals"]["protein_g"] == pytest.approx(full["totals"]["protein_g"] / 2)


def test_incomplete_nutrient_is_flagged():
    result = engine.calculate([engine.Line("bread", 72, BREAD), engine.Line("x", 10, {"energy_kcal": 10, "fibre_g": 1})])
    assert result["incomplete"]["fibre_g"] == ["bread"]
    assert result["totals"]["fibre_g"] == pytest.approx(0.1)


def test_total_equals_sum_of_lines():
    lines = [engine.Line("a", 123.4, {"energy_kcal": 321}), engine.Line("b", 56.7, {"energy_kcal": 89})]
    r = engine.calculate(lines)
    assert r["totals"]["energy_kcal"] == pytest.approx(sum(l["values"]["energy_kcal"] for l in r["lines"]))


def test_confidence_and_range():
    r = engine.calculate([engine.Line("butter", 10, {"energy_kcal": 740}, basis="default")])
    assert r["confidence"] == "low"
    assert r["kcal_low"] == pytest.approx(37.0) and r["kcal_high"] == pytest.approx(111.0)


@pytest.mark.parametrize("bad", [-1, None])
def test_rejects_bad_grams(bad):
    with pytest.raises(ValueError):
        engine.scale(BREAD, bad)


def test_display_rounding():
    assert engine.display_value(164.16, 0) == "164"
    assert engine.display_value(None, 1) == "n/a"
