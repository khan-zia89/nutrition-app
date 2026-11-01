"""
A small database with food NAMES styled like the real CoFID and USDA files,
used to test matching and the question flow. Nutrient numbers are invented
test values - never shown to a user and not real food data.
"""
from core import database, importers


def build(conn):
    importers._register_source(conn, "cofid2021", "UK CoFID 2021", "UK", "test", "", "test")
    importers._register_source(conn, "usda_sr", "USDA SR Legacy", "US", "test", "", "test")
    cofid = [
        ("12-001", "Eggs, chicken, whole, raw", 140), ("12-002", "Eggs, chicken, whole, fried in sunflower oil", 180),
        ("12-003", "Eggs, chicken, whole, boiled", 140), ("17-001", "Butter, salted", 740),
        ("11-001", "Bread, wholemeal, average", 220), ("11-002", "Bread, wholemeal, toasted", 250),
        ("14-001", "Coffee, infusion, average", 2), ("14-002", "Coffee, instant, powder", 350),
        ("14-003", "Coffee, latte, semi-skimmed milk", 50),
        ("12-100", "Cream, fresh, extra thick double", 440), ("12-101", "Cream, fresh, single", 190),
    ]
    importers._insert_foods(conn, "cofid2021", [
        {"source_code": c, "name": n, "energy_kcal": k, "protein_g": 1.0, "carbohydrate_g": 1.0,
         "fat_g": 1.0, "fibre_g": None, "carb_basis": "available"} for c, n, k in cofid])
    usda = [
        ("1", "Egg, whole, raw, fresh", 143, [("1 large", 50.0), ("1 medium", 44.0), ("1 small", 38.0)]),
        ("2", "Bread, whole-wheat, commercially prepared", 250, [("1 slice", 32.0)]),
        ("3", "Beverages, coffee, brewed, prepared with tap water", 1, [("1 cup (8 fl oz)", 237.0)]),
        ("4", "Croutons, plain", 400, [("1 cup", 30.0)]),
        ("5", "Cream, fluid, heavy whipping", 340, [("1 tbsp", 15.0)]),
    ]
    importers._insert_foods(conn, "usda_sr", [
        {"source_code": c, "name": n, "energy_kcal": k, "carb_basis": "total"} for c, n, k, _ in usda],
        {c: p for c, _, _, p in usda})
    return conn


if __name__ == "__main__":
    build(database.connect())
    print("realistic test database built")
