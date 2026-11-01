"""
Importer tests using small files laid out like the real CoFID Excel file and
USDA CSV zip. All nutrient numbers here are invented test values.
"""

import io
import zipfile

import pandas as pd
import pytest

from core import database, diary, engine, foods, importers


@pytest.fixture
def conn(tmp_path):
    return database.connect(tmp_path / "test.db")


def make_cofid(path):
    # CoFID sheets have 3 header rows (long names, short codes, units), then data.
    prox = [
        ["Food Code", "Food Name", "Group", "Protein (g)", "Fat (g)", "Carbohydrate (g)",
         "Energy (kcal) (kcal)", "Energy (kJ) (kJ)", "Total sugars (g)", "AOAC fibre (g)", "NSP (g)"],
        ["NUMB", "NAME", "GROUP", "PROT", "FAT", "CHO", "KCALS", "KJ", "TOTSUG", "AOACFIB", "ENGFIB"],
        ["", "", "", "g", "g", "g", "kcal", "kJ", "g", "g", "g"],
        ["12-001", "Test egg, whole, raw", "CA", 12.0, 10.0, "Tr", 140, 586, "Tr", "N", "N"],
        ["11-001", "Test bread, wholemeal", "AB", 9.0, 2.0, 40.0, 220, 930, 3.0, 7.0, 5.0],
    ]
    inorg = [["Food Code", "Food Name", "Sodium (mg)"], ["NUMB", "NAME", "NA"], ["", "", "mg"],
             ["12-001", "Test egg, whole, raw", 140], ["11-001", "Test bread, wholemeal", 400]]
    satd = [["Food Code", "Food Name", "Satd FA /100g fd (g)"], ["NUMB", "NAME", "SATFOD"], ["", "", "g"],
            ["12-001", "Test egg, whole, raw", 2.5]]
    with pd.ExcelWriter(path) as w:
        pd.DataFrame(prox).to_excel(w, sheet_name="1.3 Proximates", header=False, index=False)
        pd.DataFrame(inorg).to_excel(w, sheet_name="1.4 Inorganics", header=False, index=False)
        pd.DataFrame(satd).to_excel(w, sheet_name="1.8 SFA per 100g food",
                                    header=False, index=False)


def make_usda(path):
    files = {
        "food.csv": "fdc_id,data_type,description,food_category_id,publication_date\n"
                    "1001,sr_legacy_food,\"Test egg, whole, raw, fresh\",1,2019-04-01\n"
                    "1002,sr_legacy_food,\"Test oil, olive\",4,2019-04-01\n",
        "nutrient.csv": "id,name,unit_name,nutrient_nbr,rank\n"
                        "1008,Energy,KCAL,208,300\n1062,Energy,kJ,268,400\n1003,Protein,G,203,600\n"
                        "1004,Total lipid (fat),G,204,800\n1005,\"Carbohydrate, by difference\",G,205,1110\n"
                        "1079,\"Fiber, total dietary\",G,291,1200\n1093,\"Sodium, Na\",MG,307,5800\n",
        "food_nutrient.csv": "id,fdc_id,nutrient_id,amount\n1,1001,1008,143\n2,1001,1003,12.5\n"
                             "3,1001,1004,9.5\n4,1001,1005,0.7\n5,1001,1093,142\n"
                             "6,1002,1008,884\n7,1002,1004,100\n",
        "food_portion.csv": "id,fdc_id,seq_num,amount,measure_unit_id,portion_description,modifier,gram_weight\n"
                            "1,1001,1,1,9999,,large,50\n2,1001,2,1,9999,,medium,44\n"
                            "3,1002,1,1,9999,,tbsp,13.5\n",
        "measure_unit.csv": "id,name,abbreviation\n9999,undetermined,undetermined\n",
        "food_category.csv": "id,code,description\n1,0100,Dairy and Egg Products\n4,0400,Fats and Oils\n",
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(f"FoodData_Central_sr_legacy_food_csv_2018-04/{name}", text)


def test_cofid_import(conn, tmp_path):
    path = tmp_path / "cofid.xlsx"
    make_cofid(path)
    report = importers.import_cofid(conn, path)
    assert "Imported 2" in report
    egg = conn.execute("SELECT * FROM foods WHERE source_code='12-001'").fetchone()
    assert egg["energy_kcal"] == 140 and egg["carbohydrate_g"] == 0.0     # Tr -> 0
    assert egg["fibre_g"] is None                                         # N -> unknown
    assert egg["saturates_g"] == 2.5
    assert egg["salt_g"] == pytest.approx(0.35)                            # 140 mg Na x 2.5
    bread = conn.execute("SELECT * FROM foods WHERE source_code='11-001'").fetchone()
    assert bread["fibre_g"] == 7.0 and bread["fibre_method"] == "AOAC"
    assert bread["saturates_g"] is None                                   # not in sheet
    # re-import replaces, does not duplicate
    importers.import_cofid(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0] == 2


def test_usda_import_and_portions(conn, tmp_path):
    path = tmp_path / "FoodData_Central_sr_legacy_food_csv_2018-04.zip"
    make_usda(path)
    report = importers.import_usda(conn, path)
    assert "Imported 2 foods and 3 portion" in report
    egg = conn.execute("SELECT * FROM foods WHERE source_code='1001'").fetchone()
    assert egg["energy_kcal"] == 143 and egg["carb_basis"] == "total"
    assert egg["fibre_g"] is None
    portions = foods.get_portions(conn, egg["id"])
    assert {p["label"]: p["grams"] for p in portions} == {"1 large": 50.0, "1 medium": 44.0}


def test_search_and_region_and_borrowing(conn, tmp_path):
    make_cofid(tmp_path / "c.xlsx"); importers.import_cofid(conn, tmp_path / "c.xlsx")
    make_usda(tmp_path / "u.zip"); importers.import_usda(conn, tmp_path / "u.zip")
    index = foods.FoodIndex(conn)
    uk = index.search("eggs", "UK")
    us = index.search("eggs", "US")
    assert uk[0]["source"] == "UK CoFID 2021"
    assert us[0]["source"] == "USDA SR Legacy"
    uk_egg = foods.get_food(conn, uk[0]["ref"])
    borrowed = foods.borrow_portions(conn, index, uk_egg["name"])
    assert borrowed and "borrowed" in borrowed[0]["source"]


def test_diary_round_trip(conn):
    lines = [{"name": "Test bread", "ref": "food:1", "source": "test", "grams": 72,
              "per100": {"energy_kcal": 228}, "basis": "weighed"}]
    entry = diary.save_meal(conn, "2026-11-01", "Breakfast", "Toast", lines)
    day = diary.day_entries(conn, "2026-11-01")
    assert day[0]["result"]["totals"]["energy_kcal"] == pytest.approx(164.16)
    item_id = day[0]["items"][0]["id"]
    diary.update_item_grams(conn, item_id, 36)
    day = diary.day_entries(conn, "2026-11-01")
    assert diary.day_totals(day)["totals"]["energy_kcal"] == pytest.approx(82.08)
    diary.delete_entry(conn, entry)
    assert diary.day_entries(conn, "2026-11-01") == []
    _ = engine


def test_foundation_zip_variations(conn, tmp_path):
    """Nested zip, upper-case names, Mac clutter, no nutrient.csv, Atwater energy only."""
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("Food.csv", "fdc_id,data_type,description,food_category_id\n"
                               "2001,foundation_food,\"Test lentils, dry\",16\n2002,sample_food,sample,16\n")
        z.writestr("food_nutrient.csv", "id,fdc_id,nutrient_id,amount\n1,2001,2048,352\n2,2001,1003,24.6\n")
    outer = tmp_path / "FoodData_Central_foundation_food_csv_2026-10-29.zip"
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("__MACOSX/._food.csv", "junk")
        z.writestr("FoodData_Central_foundation_food_csv_2026-10-29/inner.zip", inner.getvalue())
    report = importers.import_usda(conn, outer)
    assert "Imported 1 foods" in report and "Foundation" in report
    lentil = conn.execute("SELECT * FROM foods WHERE source_code='2001'").fetchone()
    assert lentil["energy_kcal"] == 352 and lentil["protein_g"] == 24.6


def test_unrecognised_zip_lists_its_contents(conn, tmp_path):
    bad = tmp_path / "FoodData_Central_wrong.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("foods.json", "{}")
    with pytest.raises(ValueError, match="foods.json"):
        importers.import_usda(conn, bad)
