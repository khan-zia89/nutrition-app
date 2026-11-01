"""
database.py - creates and opens the local SQLite database.

SQLite is a database stored in a single file (data/nutrition.db). It comes
built into Python, so there is nothing extra to install.

Tables
------
sources        one row per dataset (CoFID 2021, USDA SR Legacy, ...)
foods          generic reference foods, nutrients per 100 g, with their source
portions       household portion weights (e.g. "1 large egg = 50 g"), with source
products       packaged products (Open Food Facts or labels you typed in);
               kept separate from `foods` so that differently licensed data
               is never mixed together
diary_entries  one row per meal you record (date + slot + name)
diary_items    the ingredient lines of that meal, with a frozen copy of the
               per-100 g values used, so old diary days never change
"""

import os
import sqlite3
from pathlib import Path

# The app folder is the parent of this "core" folder.
APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NUTRITION_DATA_DIR", APP_DIR / "data"))
# NUTRITION_DB lets the automated tests use a separate, temporary database
DB_PATH = Path(os.environ.get("NUTRITION_DB", DATA_DIR / "nutrition.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,      -- short code, e.g. 'cofid2021'
    name        TEXT NOT NULL,
    region      TEXT NOT NULL,         -- 'UK', 'US' or 'WORLD'
    licence     TEXT NOT NULL,
    url         TEXT,
    version     TEXT,
    imported_at TEXT
);

CREATE TABLE IF NOT EXISTS foods (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL REFERENCES sources(id),
    source_code     TEXT NOT NULL,     -- the food's ID inside its own dataset
    name            TEXT NOT NULL,
    category        TEXT,
    -- nutrients per 100 g; NULL means "unknown", never zero
    energy_kcal     REAL,
    energy_kj       REAL,
    protein_g       REAL,
    carbohydrate_g  REAL,
    carb_basis      TEXT,              -- 'available' (UK) or 'total' (US, includes fibre)
    fat_g           REAL,
    saturates_g     REAL,
    sugars_g        REAL,
    fibre_g         REAL,
    fibre_method    TEXT,              -- 'AOAC', 'NSP' or 'total dietary'
    salt_g          REAL,
    notes           TEXT,              -- e.g. which values were 'trace' or 'N'
    UNIQUE (source_id, source_code)
);

CREATE TABLE IF NOT EXISTS portions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id     INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,         -- e.g. '1 large', '1 cup, chopped'
    grams       REAL NOT NULL,
    source_id   TEXT NOT NULL REFERENCES sources(id)
);
CREATE INDEX IF NOT EXISTS idx_portions_food ON portions(food_id);

CREATE TABLE IF NOT EXISTS products (
    barcode         TEXT PRIMARY KEY,  -- or 'manual-<n>' for labels without a barcode
    name            TEXT NOT NULL,
    brand           TEXT,
    origin          TEXT NOT NULL,     -- 'openfoodfacts' or 'my_label'
    serving_g       REAL,
    energy_kcal     REAL,
    energy_kj       REAL,
    protein_g       REAL,
    carbohydrate_g  REAL,
    fat_g           REAL,
    saturates_g     REAL,
    sugars_g        REAL,
    fibre_g         REAL,
    salt_g          REAL,
    confirmed       INTEGER DEFAULT 0, -- 1 once you have checked it against the pack
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date  TEXT NOT NULL,         -- 'YYYY-MM-DD'
    slot        TEXT NOT NULL,         -- Breakfast, Lunch, Dinner, Snack, Drink
    meal_name   TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diary_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        INTEGER NOT NULL REFERENCES diary_entries(id) ON DELETE CASCADE,
    display_name    TEXT NOT NULL,
    food_ref        TEXT NOT NULL,     -- 'food:<id>' or 'product:<barcode>'
    source_label    TEXT NOT NULL,
    grams           REAL NOT NULL,
    per100_json     TEXT NOT NULL,     -- frozen per-100 g values used
    estimated       INTEGER NOT NULL DEFAULT 0,
    assumptions     TEXT
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (and if needed create) the database and return a connection."""
    db_path = Path(db_path or DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows behave like dictionaries
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def food_count(conn: sqlite3.Connection) -> dict:
    """How many foods each source has - used on the Data page."""
    rows = conn.execute(
        """SELECT s.id, s.name, s.region, s.licence, s.version, COUNT(f.id) AS n
           FROM sources s LEFT JOIN foods f ON f.source_id = s.id
           GROUP BY s.id ORDER BY s.region"""
    ).fetchall()
    return [dict(r) for r in rows]
