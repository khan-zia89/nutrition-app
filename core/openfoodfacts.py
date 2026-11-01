"""
openfoodfacts.py - look up a packaged product by barcode.

Open Food Facts (OFF) is a free, crowd-sourced product database. Its data is
under the Open Database Licence (ODbL): you must credit Open Food Facts, and
data is "share-alike". This app keeps OFF products in their own `products`
table and labels them, so they are never mixed into the CoFID/USDA foods.

OFF data is entered by volunteers and may contain mistakes, so the app
always shows the values for you to check against the pack before use.
"""

import datetime as dt

import requests

from core import settings

API = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"
FIELDS = "product_name,product_name_en,brands,nutriments,serving_quantity,serving_size"


def user_agent(contact: str | None = None) -> str:
    """OFF asks every app to identify itself: 'AppName/Version (contact email)'."""
    contact = contact or settings.load().get("contact_email") or "personal use"
    return f"PersonalNutritionApp/0.1 ({contact})"


def _num(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def lookup(barcode: str, contact: str | None = None) -> dict | None:
    """Return product details from OFF, or None if not found. Raises on network errors."""
    barcode = "".join(ch for ch in barcode if ch.isdigit())
    if not barcode:
        raise ValueError("A barcode should contain digits")
    response = requests.get(API.format(barcode=barcode), params={"fields": FIELDS},
                            headers={"User-Agent": user_agent(contact)}, timeout=15)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    if data.get("status") != 1:
        return None
    p = data.get("product", {})
    n = p.get("nutriments", {})
    kcal = _num(n.get("energy-kcal_100g"))
    kj = _num(n.get("energy-kj_100g"))
    if kj is None and n.get("energy_unit", "kJ") == "kJ":
        kj = _num(n.get("energy_100g"))          # OFF's generic energy field is in kJ
    return {
        "barcode": barcode,
        "name": p.get("product_name_en") or p.get("product_name") or "Unnamed product",
        "brand": (p.get("brands") or "").split(",")[0].strip(),
        "serving_g": _num(p.get("serving_quantity")),
        "energy_kcal": kcal,
        "energy_kj": kj,
        "protein_g": _num(n.get("proteins_100g")),
        "carbohydrate_g": _num(n.get("carbohydrates_100g")),
        "fat_g": _num(n.get("fat_100g")),
        "saturates_g": _num(n.get("saturated-fat_100g")),
        "sugars_g": _num(n.get("sugars_100g")),
        "fibre_g": _num(n.get("fiber_100g")),
        "salt_g": _num(n.get("salt_100g")),
    }


def save_product(conn, product: dict, origin: str, confirmed: bool):
    """Store (or update) a product in the local products table."""
    cols = ["barcode", "name", "brand", "origin", "serving_g", "energy_kcal", "energy_kj",
            "protein_g", "carbohydrate_g", "fat_g", "saturates_g", "sugars_g", "fibre_g",
            "salt_g", "confirmed", "retrieved_at"]
    row = dict(product)
    row["origin"] = origin
    row["confirmed"] = 1 if confirmed else 0
    row["retrieved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        f"INSERT OR REPLACE INTO products ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [row.get(c) for c in cols],
    )
    conn.commit()


def next_manual_code(conn) -> str:
    n = conn.execute("SELECT COUNT(*) FROM products WHERE barcode LIKE 'manual-%'").fetchone()[0]
    return f"manual-{n + 1}"
