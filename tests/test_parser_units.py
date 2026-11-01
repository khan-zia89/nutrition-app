"""Tests for reading meal text and converting units."""

import pytest

from core import parser, units


def items(text):
    return [(i.food, i.amount, i.unit) for i in parser.parse_rules(text)]


def test_basic_sentence():
    assert items("2 large eggs, 30g cheddar and 1 tsp butter") == [
        ("eggs", 2, "large"), ("cheddar", 30, "g"), ("butter", 1, "tsp")]


def test_number_words_and_slices():
    assert items("a slice of wholemeal toast") == [("wholemeal toast", 1, "slice")]
    assert items("half an avocado") == [("avocado", 0.5, "item")]
    assert items("two bananas") == [("bananas", 2, "item")]


def test_fractions_and_decimals():
    assert items("1/2 cup rice") == [("rice", 0.5, "cup")]
    assert items("½ tbsp olive oil") == [("olive oil", 0.5, "tbsp")]
    assert items("1.5 kg potatoes") == [("potatoes", 1.5, "kg")]


def test_unknown_amount_left_empty():
    assert items("toast") == [("toast", None, None)]
    assert items("some butter") == [("butter", None, None)]


def test_cooking_fat_prompt():
    found = parser.parse_rules("fried egg")
    assert parser.mentions_cooking_in_fat("fried egg", found)
    with_oil = parser.parse_rules("fried egg with 1 tsp oil")
    assert not parser.mentions_cooking_in_fat("fried egg with 1 tsp oil", with_oil)


def test_mass_conversion():
    assert units.to_grams(2, "oz")[0] == pytest.approx(56.69904625)
    assert units.to_grams(250, "g") == (250, "weighed", "")


def test_portion_conversion():
    portions = [{"label": "1 large", "grams": 50.0, "source": "USDA"}]
    grams, basis, _ = units.to_grams(2, "large", portions)
    assert grams == 100 and basis == "portion"


def test_count_without_portion_raises():
    with pytest.raises(ValueError):
        units.to_grams(1, "slice", [])


def test_volume_without_density_is_flagged():
    grams, basis, note = units.to_grams(1, "tbsp", [])
    assert grams == 15 and basis == "portion" and "assumed" in note
