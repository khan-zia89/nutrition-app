"""
importers.py - load the official food datasets into the local database.

You download two free files yourself (links in README.md) and put them in
the `data` folder. These functions read them and fill the `foods` and
`portions` tables. Each food keeps its own source; nothing is blended.

  * UK CoFID 2021 (Excel)  -> source 'cofid2021'   (region UK)
  * USDA SR Legacy (CSV zip) -> source 'usda_sr'    (region US)
  * USDA Foundation Foods (CSV zip) -> source 'usda_foundation' (region US)

Values the dataset leaves blank or marks 'N' (present but not measured) are
stored as NULL = unknown. 'Tr' (trace) is stored as 0 and noted.
Running an import again replaces that source's foods cleanly.
"""

import datetime as dt
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from core.database import DATA_DIR

NOW = lambda: dt.datetime.now().isoformat(timespec="seconds")  # noqa: E731


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _register_source(conn, sid, name, region, licence, url, version):
    conn.execute(
        """INSERT OR REPLACE INTO sources (id, name, region, licence, url, version, imported_at)
           VALUES (?,?,?,?,?,?,?)""", (sid, name, region, licence, url, version, NOW()))


def _insert_foods(conn, source_id: str, foods: list[dict], portions: dict | None = None) -> int:
    """Replace all foods for one source. portions: source_code -> list of (label, grams)."""
    conn.execute("DELETE FROM foods WHERE source_id = ?", (source_id,))
    cols = ["source_id", "source_code", "name", "category", "energy_kcal", "energy_kj",
            "protein_g", "carbohydrate_g", "carb_basis", "fat_g", "saturates_g", "sugars_g",
            "fibre_g", "fibre_method", "salt_g", "notes"]
    sql = f"INSERT INTO foods ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    count = 0
    for f in foods:
        f["source_id"] = source_id
        cur = conn.execute(sql, [f.get(c) for c in cols])
        count += 1
        if portions and f["source_code"] in portions:
            for label, grams in portions[f["source_code"]]:
                conn.execute("INSERT INTO portions (food_id, label, grams, source_id) VALUES (?,?,?,?)",
                             (cur.lastrowid, label, grams, source_id))
    conn.commit()
    return count


# ---------------------------------------------------------------------------
# UK CoFID (McCance and Widdowson's Composition of Foods Integrated Dataset)
# ---------------------------------------------------------------------------

# CoFID worksheets carry short field codes in one of their header rows.
COFID_CODES = {
    "energy_kcal": ["KCALS"], "energy_kj": ["KJ"], "protein_g": ["PROT"], "fat_g": ["FAT"],
    "carbohydrate_g": ["CHO"], "sugars_g": ["TOTSUG"], "aoac": ["AOACFIB"], "nsp": ["ENGFIB"],
    "sodium_mg": ["NA"], "saturates_g": ["SATFOD"],
}
# Fallback: words in the long column names, if the code row is not found.
COFID_WORDS = {
    "energy_kcal": [("energy", "kcal")], "energy_kj": [("energy", "kj")],
    "protein_g": [("protein",)], "fat_g": [("fat (g)",), ("fat",)],
    "carbohydrate_g": [("carbohydrate",)], "sugars_g": [("total sugars",)],
    "aoac": [("aoac",)], "nsp": [("nsp",)], "sodium_mg": [("sodium",)],
    "saturates_g": [("satd",), ("saturated",)],
}
CODE_PATTERN = re.compile(r"^\d{2,}-\d{2,}$")       # CoFID food codes look like '11-001'


def find_cofid_file(folder: Path = DATA_DIR) -> Path | None:
    for path in sorted(folder.glob("*.xls*")):
        try:
            names = [s.lower() for s in pd.ExcelFile(path).sheet_names]
        except Exception:                               # noqa: BLE001
            continue
        if any("proximates" in n for n in names):
            return path
    return None


def _cofid_value(raw):
    """Turn a CoFID cell into (number or None, note or '')."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, ""
    if isinstance(raw, (int, float)):
        return float(raw), ""
    text = str(raw).strip()
    if text == "":
        return None, ""
    if text.lower() == "tr":
        return 0.0, "trace"
    if text.upper() == "N":
        return None, "N (present, amount unknown)"
    try:
        return float(text.strip("()")), ""
    except ValueError:
        return None, f"unreadable value '{text}'"


def _read_cofid_sheet(path: Path, sheet: str, wanted: list[str]) -> dict:
    """Return {food_code: {'name':..., 'group':..., key: (value, note)}} for wanted keys."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object,
                        keep_default_na=False)  # keep the code "NA" (sodium) as text
    head = raw.head(6).fillna("").astype(str)

    # Locate columns: by short code first, then by words in the long names.
    columns = {}
    for key in wanted:
        for r in range(len(head)):
            for c, cell in enumerate(head.iloc[r]):
                if cell.strip().upper() in COFID_CODES[key]:
                    columns[key] = c
                    break
            if key in columns:
                break
        if key not in columns:
            for words in COFID_WORDS[key]:
                for c in range(head.shape[1]):
                    text = " ".join(head.iloc[:, c]).lower()
                    if all(w in text for w in words):
                        columns[key] = c
                        break
                if key in columns:
                    break

    # Locate the food code and name columns from the data itself.
    code_col = name_col = group_col = None
    for c in range(raw.shape[1]):
        sample = raw.iloc[3:60, c].dropna().astype(str).str.strip()
        sample = sample[sample != ""]
        if code_col is None and len(sample) and sample.map(lambda s: bool(CODE_PATTERN.match(s.strip()))).mean() > 0.8:
            code_col = c
    if code_col is None:
        raise ValueError(f"Could not find the food code column in sheet '{sheet}'")
    for c in range(raw.shape[1]):
        label = " ".join(head.iloc[:, c]).lower()
        if name_col is None and ("food name" in label or label.strip().startswith("name")):
            name_col = c
        if group_col is None and "group" in label:
            group_col = c
    if name_col is None:
        name_col = code_col + 1

    out = {}
    for _, row in raw.iterrows():
        code = str(row[code_col]).strip()
        if not CODE_PATTERN.match(code):
            continue
        rec = {"name": str(row[name_col]).strip(),
               "group": str(row[group_col]).strip() if group_col is not None else None}
        for key, c in columns.items():
            rec[key] = _cofid_value(row[c])
        out[code] = rec
    return {"rows": out, "found": sorted(columns)}


def _sheet_with_code(path: Path, sheets: list[str], code: str) -> str | None:
    """Name of the first worksheet whose header rows contain a CoFID field code."""
    for sheet in sheets:
        head = pd.read_excel(path, sheet_name=sheet, header=None, nrows=5, dtype=object,
                             keep_default_na=False)
        cells = {str(v).strip().upper() for v in head.to_numpy().ravel() if pd.notna(v)}
        if code in cells:
            return sheet
    return None


def import_cofid(conn, path: Path) -> str:
    """Import the CoFID Excel file. Returns a short report."""
    sheets = pd.ExcelFile(path).sheet_names
    prox = next((s for s in sheets if "proximates" in s.lower()), None)
    # Sheet names are shortened in the real file (Excel allows 31 characters),
    # so the other sheets are found by the short codes in their header rows.
    inorg = _sheet_with_code(path, sheets, "NA")
    satd = _sheet_with_code(path, sheets, "SATFOD")
    if not prox:
        raise ValueError("This file has no 'Proximates' worksheet - is it the CoFID Excel file?")

    p = _read_cofid_sheet(path, prox, ["energy_kcal", "energy_kj", "protein_g", "fat_g",
                                        "carbohydrate_g", "sugars_g", "aoac", "nsp"])
    missing = {"energy_kcal", "protein_g", "fat_g", "carbohydrate_g"} - set(p["found"])
    if missing:
        raise ValueError(f"Could not find these columns in Proximates: {sorted(missing)}")
    na = _read_cofid_sheet(path, inorg, ["sodium_mg"])["rows"] if inorg else {}
    sf = _read_cofid_sheet(path, satd, ["saturates_g"])["rows"] if satd else {}

    foods = []
    for code, rec in p["rows"].items():
        notes = []

        def val(key, source=rec):
            v, note = source.get(key, (None, ""))
            if note:
                notes.append(f"{key}: {note}")
            return v

        fibre, method = val("aoac"), "AOAC"
        if fibre is None:
            fibre, method = val("nsp"), "NSP"
        if fibre is None:
            method = None
        sodium = val("sodium_mg", na.get(code, {}))
        foods.append({
            "source_code": code, "name": rec["name"], "category": rec["group"],
            "energy_kcal": val("energy_kcal"), "energy_kj": val("energy_kj"),
            "protein_g": val("protein_g"), "carbohydrate_g": val("carbohydrate_g"),
            "carb_basis": "available", "fat_g": val("fat_g"),
            "saturates_g": val("saturates_g", sf.get(code, {})),
            "sugars_g": val("sugars_g"), "fibre_g": fibre, "fibre_method": method,
            "salt_g": None if sodium is None else sodium * 2.5 / 1000.0,
            "notes": "; ".join(notes) or None,
        })

    _register_source(conn, "cofid2021", "UK CoFID 2021", "UK",
                     "Open Government Licence (gov.uk download) - check terms on the download page",
                     "https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid",
                     path.name)
    n = _insert_foods(conn, "cofid2021", foods)
    return f"Imported {n} UK CoFID foods from {path.name} (columns found: {', '.join(p['found'])})."


# ---------------------------------------------------------------------------
# USDA FoodData Central (SR Legacy and Foundation Foods, CSV downloads)
# ---------------------------------------------------------------------------

USDA_NUTRIENTS = {           # our key -> list of (nutrient name, unit) in order of preference
    "energy_kcal": [("Energy", "KCAL"), ("Energy (Atwater Specific Factors)", "KCAL"),
                    ("Energy (Atwater General Factors)", "KCAL")],
    "energy_kj": [("Energy", "kJ")],
    "protein_g": [("Protein", "G")],
    "fat_g": [("Total lipid (fat)", "G")],
    "carbohydrate_g": [("Carbohydrate, by difference", "G")],
    "fibre_g": [("Fiber, total dietary", "G")],
    "sugars_g": [("Sugars, total including NLEA", "G"), ("Sugars, Total", "G"), ("Total Sugars", "G")],
    "saturates_g": [("Fatty acids, total saturated", "G")],
    "sodium_mg": [("Sodium, Na", "MG")],
}


def find_usda_files(folder: Path = DATA_DIR) -> list[Path]:
    """USDA zips (or unzipped folders) in the data folder."""
    found = [p for p in sorted(folder.glob("FoodData_Central*")) if p.suffix == ".zip" or p.is_dir()]
    return found


# Standard USDA nutrient IDs, used if a download has no nutrient.csv.
USDA_FALLBACK_IDS = {
    "energy_kcal": [1008, 2048, 2047], "energy_kj": [1062], "protein_g": [1003], "fat_g": [1004],
    "carbohydrate_g": [1005], "fibre_g": [1079], "sugars_g": [2000, 1063], "saturates_g": [1258],
    "sodium_mg": [1093],
}


def _usda_reader(path: Path):
    """
    Return (read, listing). read('food.csv') opens a CSV from a zip or folder,
    matching names case-insensitively, ignoring Mac '__MACOSX' clutter and
    looking inside a zip that is itself inside the download.
    """
    files = {}                                    # lower-case file name -> function returning bytes

    def add_zip(z: zipfile.ZipFile):
        for n in z.namelist():
            base = Path(n).name
            if not base or "__MACOSX" in n or base.startswith("._"):
                continue
            if base.lower().endswith(".zip"):
                add_zip(zipfile.ZipFile(io.BytesIO(z.read(n))))
            else:
                files.setdefault(base.lower(), (lambda z=z, n=n: z.read(n)))

    if path.suffix.lower() == ".zip":
        add_zip(zipfile.ZipFile(path))
    else:
        for f in path.rglob("*"):
            if f.is_file() and "__MACOSX" not in str(f) and not f.name.startswith("._"):
                files.setdefault(f.name.lower(), (lambda f=f: f.read_bytes()))

    def read(name, **kw):
        getter = files.get(name.lower())
        if getter is None:
            return None
        df = pd.read_csv(io.BytesIO(getter()), low_memory=False, **kw)
        df.columns = [str(c).strip().lower() for c in df.columns]
        return df

    return read, sorted(files)


def import_usda(conn, path: Path) -> str:
    """Import an SR Legacy or Foundation Foods CSV download. Returns a short report."""
    read, listing = _usda_reader(path)
    food = read("food.csv")
    food_nutrient = read("food_nutrient.csv")
    nutrient = read("nutrient.csv")
    if food is None or food_nutrient is None:
        missing = [n for n, df in (("food.csv", food), ("food_nutrient.csv", food_nutrient)) if df is None]
        shown = ", ".join(listing[:25]) or "nothing readable"
        raise ValueError(f"{path.name}: could not find {', '.join(missing)}. "
                         f"Files inside: {shown}. Is this the CSV download (not JSON)?")
    for col in ("fdc_id", "nutrient_id", "amount"):
        if col not in food_nutrient.columns:
            raise ValueError(f"food_nutrient.csv has no '{col}' column; columns are {list(food_nutrient.columns)}")
    food_nutrient = food_nutrient[["fdc_id", "nutrient_id", "amount"]]

    types = set(food["data_type"].unique())
    if "sr_legacy_food" in types:
        data_type, sid, sname, version = "sr_legacy_food", "usda_sr", "USDA SR Legacy", "April 2018"
    elif "foundation_food" in types:
        data_type, sid, sname, version = "foundation_food", "usda_foundation", "USDA Foundation Foods", path.name
    else:
        raise ValueError(f"No SR Legacy or Foundation foods in {path.name}; found {sorted(types)}")
    food = food[food["data_type"] == data_type]

    # Map our nutrient keys to USDA nutrient ids by name + unit (robust to id changes);
    # if nutrient.csv is absent, use USDA's standard ids.
    id_for = {}
    if nutrient is not None and {"id", "name", "unit_name"} <= set(nutrient.columns):
        nutrient["key"] = list(zip(nutrient["name"].astype(str).str.strip(),
                                   nutrient["unit_name"].astype(str).str.strip().str.upper()))
        for key, options in USDA_NUTRIENTS.items():
            id_for[key] = [int(nutrient.loc[nutrient["key"] == (n, u.upper()), "id"].iloc[0])
                           for n, u in options if ((nutrient["key"] == (n, u.upper())).any())]
    for key, ids in USDA_FALLBACK_IDS.items():
        if not id_for.get(key):
            id_for[key] = ids

    fn = food_nutrient[food_nutrient["fdc_id"].isin(food["fdc_id"])]
    wide = fn.pivot_table(index="fdc_id", columns="nutrient_id", values="amount", aggfunc="first")

    categories = {}
    cat = read("food_category.csv")
    if cat is not None and "food_category_id" in food.columns:
        categories = dict(zip(cat["id"], cat["description"]))

    def pick(fdc_id, key):
        for nid in id_for.get(key, []):
            if nid in wide.columns and fdc_id in wide.index:
                v = wide.at[fdc_id, nid]
                if pd.notna(v):
                    return float(v)
        return None

    # Household portions (e.g. '1 large' = 50 g), stored per ONE unit.
    portions = {}
    fp = read("food_portion.csv")
    units = read("measure_unit.csv")
    unit_name = dict(zip(units["id"], units["name"])) if units is not None else {}
    if fp is not None:
        for _, r in fp[fp["fdc_id"].isin(food["fdc_id"])].iterrows():
            amount = r.get("amount")
            grams = r.get("gram_weight")
            if pd.isna(grams) or grams <= 0:
                continue
            amount = 1.0 if pd.isna(amount) or amount <= 0 else float(amount)
            unit = unit_name.get(r.get("measure_unit_id"), "")
            unit = "" if unit in ("undetermined", "", None) or pd.isna(unit) else unit
            desc = r.get("modifier") if pd.notna(r.get("modifier")) else r.get("portion_description")
            desc = "" if pd.isna(desc) else str(desc)
            label = " ".join(x for x in ["1", unit, desc] if x).strip()
            portions.setdefault(str(int(r["fdc_id"])), []).append((label, float(grams) / amount))

    foods = []
    for _, r in food.iterrows():
        fid = r["fdc_id"]
        sodium = pick(fid, "sodium_mg")
        foods.append({
            "source_code": str(int(fid)), "name": str(r["description"]).strip(),
            "category": categories.get(r.get("food_category_id")),
            "energy_kcal": pick(fid, "energy_kcal"), "energy_kj": pick(fid, "energy_kj"),
            "protein_g": pick(fid, "protein_g"), "carbohydrate_g": pick(fid, "carbohydrate_g"),
            "carb_basis": "total", "fat_g": pick(fid, "fat_g"),
            "saturates_g": pick(fid, "saturates_g"), "sugars_g": pick(fid, "sugars_g"),
            "fibre_g": pick(fid, "fibre_g"), "fibre_method": "total dietary",
            "salt_g": None if sodium is None else sodium * 2.5 / 1000.0, "notes": None,
        })

    _register_source(conn, sid, sname, "US", "Public domain (CC0 1.0); please credit USDA FoodData Central",
                     "https://fdc.nal.usda.gov/download-datasets", version)
    n = _insert_foods(conn, sid, foods, portions)
    n_portions = sum(len(v) for v in portions.values())
    return f"Imported {n} foods and {n_portions} portion weights from {path.name} ({sname})."
