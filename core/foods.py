"""
foods.py - finding foods in the local database.

Search uses RapidFuzz (fuzzy text matching), so 'cheddar' finds
'Cheese, Cheddar, English' and small typos still match. Results from your
preferred region (UK or US) get a small boost, and every result carries its
source so you always know where a number came from.
"""

import re
import sqlite3

from rapidfuzz import fuzz, process

from core.engine import NUTRIENT_KEYS

STOP = {"of", "the", "a", "an", "and", "with", "in", "my", "some"}
LIQUID_UNITS = {"cup", "ml", "l", "fl oz"}
LIQUID_WORDS = ["infusion", "brewed", "made up", "prepared", "drink", "juice", "milk", "beverage", "fluid"]
DRY_WORDS = ["powder", "granules", "dry ", "dried", "concentrate", "beans, roasted", "ground"]

# UK words -> US words, used when borrowing portion weights from USDA foods
UK_TO_US = {
    "wholemeal": "whole wheat", "wholewheat": "whole wheat", "aubergine": "eggplant",
    "courgette": "zucchini", "coriander": "cilantro", "crisps": "potato chips",
    "mince": "ground", "double cream": "heavy cream", "extra thick cream": "heavy cream",
    "single cream": "light cream", "spring onion": "scallion", "rocket": "arugula",
    "prawn": "shrimp", "biscuit": "cookie", "porridge": "oatmeal", "semi skimmed": "reduced fat",
    "skimmed": "nonfat", "swede": "rutabaga", "chickpea": "chickpeas garbanzo",
}


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalise(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return " ".join(_singular(w) for w in words if w not in STOP)


class FoodIndex:
    """Holds every food name in memory for fast fuzzy search (~11,000 foods)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.refresh()

    def refresh(self):
        rows = self.conn.execute(
            """SELECT f.id, f.name, f.source_id, s.name AS source_name, s.region
               FROM foods f JOIN sources s ON s.id = f.source_id"""
        ).fetchall()
        products = self.conn.execute(
            "SELECT barcode, name, brand, origin FROM products"
        ).fetchall()
        self.entries = []      # (ref, display name, source label, region)
        for r in rows:
            self.entries.append((f"food:{r['id']}", r["name"], r["source_name"], r["region"]))
        for p in products:
            name = f"{p['name']} ({p['brand']})" if p["brand"] else p["name"]
            label = "Open Food Facts" if p["origin"] == "openfoodfacts" else "My label"
            self.entries.append((f"product:{p['barcode']}", name, label, "ANY"))
        self.choices = [normalise(e[1]) for e in self.entries]

    def search(self, query: str, region: str = "UK", limit: int = 8,
               unit: str | None = None, fat_separate: bool = False) -> list[dict]:
        """
        Return the best matches, best first.
        unit: the unit the user gave; a cup/ml of something means a drink, not a powder.
        fat_separate: the cooking fat is its own item, so avoid 'fried in oil' records
                      (they already contain fat - counting both would double count).
        """
        q = normalise(query)
        if not q or not self.entries:
            return []
        hits = process.extract(q, self.choices, scorer=fuzz.WRatio, limit=60)
        results = []
        q_words = set(q.split())
        for _choice, score, idx in hits:
            ref, name, source, food_region = self.entries[idx]
            name_words = normalise(name).split()
            # Prefer names whose FIRST word is the query word (e.g. 'Egg, whole, raw')
            if name_words and name_words[0] in q_words:
                score += 6
            # Prefer every query word appearing in the name
            if q_words.issubset(set(name_words)):
                score += 6
            if food_region == region:
                score += 5
            if ref.startswith("product:"):
                score += 3          # your own confirmed products are usually what you mean
            low = name.lower()
            if unit in LIQUID_UNITS:
                if any(w in low for w in LIQUID_WORDS):
                    score += 8
                if any(w in low for w in DRY_WORDS):
                    score -= 15
            if fat_separate:
                if "fried" in low or "roasted in" in low or "with fat" in low:
                    score -= 12
                if "raw" in low:
                    score += 4
            if "average" in low:
                score += 3          # CoFID's typical, mixed-brand version of a food
            # Slightly prefer shorter, plainer names
            score -= min(len(name_words), 12) * 0.4
            results.append({"ref": ref, "name": name, "source": source, "region": food_region,
                            "score": round(score, 1)})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]


def get_food(conn: sqlite3.Connection, ref: str) -> dict | None:
    """Fetch a food or product by its ref ('food:12' or 'product:5000...')."""
    kind, key = ref.split(":", 1)
    if kind == "food":
        row = conn.execute(
            """SELECT f.*, s.name AS source_name, s.region, s.licence
               FROM foods f JOIN sources s ON s.id = f.source_id WHERE f.id = ?""", (int(key),)
        ).fetchone()
        if not row:
            return None
        food = dict(row)
        food["ref"] = ref
        food["source_label"] = row["source_name"]
        food["portions"] = get_portions(conn, int(key))
    else:
        row = conn.execute("SELECT * FROM products WHERE barcode = ?", (key,)).fetchone()
        if not row:
            return None
        food = dict(row)
        food["ref"] = ref
        food["name"] = f"{row['name']} ({row['brand']})" if row["brand"] else row["name"]
        food["source_label"] = "Open Food Facts" if row["origin"] == "openfoodfacts" else "My label"
        food["carb_basis"] = "label"
        food["portions"] = []
        if row["serving_g"]:
            food["portions"].append({"label": "1 serving (pack label)", "grams": row["serving_g"],
                                     "source": food["source_label"]})
    food["per100"] = {k: food.get(k) for k in NUTRIENT_KEYS + ["energy_kj"]}
    return food


def get_portions(conn: sqlite3.Connection, food_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT p.label, p.grams, s.name AS source FROM portions p
           JOIN sources s ON s.id = p.source_id WHERE p.food_id = ? ORDER BY p.id""", (food_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def borrow_portions(conn: sqlite3.Connection, index: FoodIndex, food_name: str,
                    unit: str | None = None) -> list[dict]:
    """
    CoFID has no household portion weights. Portion weight is a physical
    property (how much one medium egg or one slice of bread weighs), so we
    borrow it from the most similar USDA food that has a matching portion -
    and label it clearly as borrowed.
    """
    from core import units as unit_mod           # local import avoids a circular import

    name = normalise(food_name)
    for uk, us in UK_TO_US.items():
        name = name.replace(normalise(uk), us)
    words = [w for w in name.split() if w not in {"average", "raw", "fresh", "chicken", "whole"}]
    if not words:
        return []
    key = words[0]                                   # the food noun, e.g. 'egg', 'bread', 'coffee'
    rows = conn.execute(
        """SELECT DISTINCT f.id, f.name FROM foods f JOIN portions p ON p.food_id = f.id
           WHERE lower(f.name) LIKE ?""", (f"%{key}%",)
    ).fetchall()
    best, best_score = None, 0.0
    for r in rows:
        portions = get_portions(conn, r["id"])
        if unit and not unit_mod.find_portion(unit, portions):
            continue
        score = fuzz.token_set_ratio(name, normalise(r["name"]))
        if normalise(r["name"]).split()[:1] == [key]:
            score += 10                               # 'Bread, whole-wheat' beats 'Croutons, bread'
        if score > best_score:
            best, best_score = (r, portions), score
    if not best or best_score < 50:
        return []
    donor, portions = best
    for p in portions:
        p["source"] = f"{p['source']}, borrowed from '{donor['name']}'"
    return portions
