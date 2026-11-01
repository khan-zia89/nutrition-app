"""The online 'try-it' mode: data loads by itself, diaries are private per visitor."""
import os
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from core import database
from tests import test_importers

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")


@pytest.fixture
def public(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    test_importers.make_cofid(data / "cofid.xlsx")
    test_importers.make_usda(data / "FoodData_Central_sr_legacy_food_csv_2018-04.zip")
    monkeypatch.setenv("NUTRITION_PUBLIC", "1")
    monkeypatch.setenv("NUTRITION_DATA_DIR", str(data))
    monkeypatch.setenv("NUTRITION_DB", str(data / "nutrition.db"))
    monkeypatch.setattr(database, "DB_PATH", data / "nutrition.db")
    yield data
    shutil.rmtree(data, ignore_errors=True)


def run_meal(text):
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.chat_input[0].set_value(text).run()
    return at


def test_public_mode(public):
    at = run_meal("2 large eggs")
    assert not at.exception
    assert "Photo of a meal" not in at.sidebar.radio[0].options          # needs Ollama, hidden online
    assert at.dataframe, "food data should have been imported automatically"
    [b for b in at.button if b.label == "Save to diary"][0].click().run()
    at.sidebar.radio[0].set_value("Diary").run()
    assert any("today" in m.label and "kcal" in m.value and m.value != "n/a" for m in at.metric)

    # A different visitor sees an empty diary
    other = AppTest.from_file(APP, default_timeout=60)
    other.run()
    other.sidebar.radio[0].set_value("Diary").run()
    assert any("Nothing recorded" in i.value for i in other.info)
