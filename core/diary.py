"""
diary.py - saving meals to your daily food diary and reading them back.

Each diary item stores the grams AND a frozen copy of the per-100 g values
that were used. Totals are always recalculated by the engine from those two
things, so editing grams later gives correct numbers, and a future database
update never silently changes your past days.
"""

import datetime as dt
import json

from core import engine

SLOTS = ["Breakfast", "Lunch", "Dinner", "Snack", "Drink"]


def save_meal(conn, entry_date: str, slot: str, meal_name: str, lines: list[dict]) -> int:
    """
    lines: list of dicts with name, ref, source, grams, per100, basis, assumptions.
    Returns the new diary entry id.
    """
    cur = conn.execute(
        "INSERT INTO diary_entries (entry_date, slot, meal_name, created_at) VALUES (?,?,?,?)",
        (entry_date, slot, meal_name, dt.datetime.now().isoformat(timespec="seconds")),
    )
    entry_id = cur.lastrowid
    for line in lines:
        conn.execute(
            """INSERT INTO diary_items (entry_id, display_name, food_ref, source_label, grams,
                                        per100_json, estimated, assumptions)
               VALUES (?,?,?,?,?,?,?,?)""",
            (entry_id, line["name"], line["ref"], line["source"], line["grams"],
             json.dumps(line["per100"]), 1 if line["basis"] in ("portion", "default") else 0,
             "; ".join(line.get("assumptions", []))),
        )
    conn.commit()
    return entry_id


def day_entries(conn, entry_date: str) -> list[dict]:
    """All meals for a date, each with its items and engine-calculated totals."""
    entries = conn.execute(
        "SELECT * FROM diary_entries WHERE entry_date = ? ORDER BY id", (entry_date,)
    ).fetchall()
    out = []
    for e in entries:
        items = conn.execute("SELECT * FROM diary_items WHERE entry_id = ? ORDER BY id",
                             (e["id"],)).fetchall()
        lines = [
            engine.Line(name=i["display_name"], grams=i["grams"], per100=json.loads(i["per100_json"]),
                        source_label=i["source_label"],
                        basis="portion" if i["estimated"] else "weighed")
            for i in items
        ]
        out.append({"entry": dict(e), "items": [dict(i) for i in items],
                    "result": engine.calculate(lines) if lines else None})
    return out


def day_totals(entries: list[dict]) -> dict:
    """Add up the totals of all meals in a day (None stays None if nothing known)."""
    totals = {k: None for k in engine.NUTRIENT_KEYS}
    incomplete = set()
    for e in entries:
        if not e["result"]:
            continue
        for k, v in e["result"]["totals"].items():
            if v is not None:
                totals[k] = (totals[k] or 0.0) + v
        incomplete.update(e["result"]["incomplete"].keys())
    return {"totals": totals, "incomplete": incomplete}


def update_item_grams(conn, item_id: int, grams: float):
    if grams <= 0:
        raise ValueError("grams must be more than 0")
    conn.execute("UPDATE diary_items SET grams = ? WHERE id = ?", (grams, item_id))
    conn.commit()


def delete_entry(conn, entry_id: int):
    conn.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
    conn.commit()
