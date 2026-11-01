"""
End-to-end tests of the meal conversation, using food names styled like the
real databases (see realistic_fixture.py; nutrient numbers are test values).
"""
import os

import pytest
from streamlit.testing.v1 import AppTest

from core import database, foods, parser
from tests import realistic_fixture

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")
MEAL = """1. two medium eggs fried in a teaspoon of butter
2. two medium slices of wholemeal bread
3. 1 cup of coffee with 1 teaspoon of extra thick cream"""


def test_numbered_list_is_parsed_correctly():
    got = [(i.food, i.amount, i.unit) for i in parser.parse_rules(MEAL)]
    assert got == [("eggs", 2, "medium"), ("butter", 1, "tsp"), ("wholemeal bread", 2, "slice"),
                   ("coffee", 1, "cup"), ("extra thick cream", 1, "tsp")]
    assert parser.parse_rules(MEAL)[0].fat_separate      # butter is its own item


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "flow.db"
    monkeypatch.setenv("NUTRITION_DB", str(path))
    monkeypatch.setattr(database, "DB_PATH", path)
    conn = database.connect(path)
    realistic_fixture.build(conn)
    return conn


def test_matching_avoids_double_counting_and_powders(db):
    index = foods.FoodIndex(db)
    assert index.search("eggs", "UK", fat_separate=True)[0]["name"] == "Eggs, chicken, whole, raw"
    assert index.search("coffee", "UK", unit="cup")[0]["name"] == "Coffee, infusion, average"
    portions = foods.borrow_portions(db, index, "Bread, wholemeal, average", "slice")
    assert portions and "whole-wheat" in portions[0]["source"]


def test_specific_meal_needs_no_questions(db):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.chat_input[0].set_value(MEAL).run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table["Amount"]) == ["2 medium (88 g)", "1 tsp (5 g)", "2 slices (64 g)",
                                     "1 cup (240 g)", "1 tsp (5 g)"]
    # 140*88/100 + 740*5/100 + 220*64/100 + 2*240/100 + 440*5/100 = 327.8 (test values)
    assert any("328 kcal" in m.value for m in at.markdown)


def test_missing_amount_asks_one_question(db):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.chat_input[0].set_value("wholemeal bread").run()
    assert any("How much" in m.value for m in at.markdown)
    assert not at.dataframe                                # no result until answered
