"""
app.py - the user interface (run with:  streamlit run app.py)

This file only shows screens and collects your answers. The real work is in
the `core` folder:
    parser.py   reads your words          (never produces nutrition numbers)
    foods.py    finds foods in the database
    units.py    converts amounts to grams
    engine.py   does all the arithmetic
    diary.py    saves meals to your diary
"""

import datetime as dt
import itertools
import os

import pandas as pd
import streamlit as st
from PIL import Image

from core import barcode, diary, engine, foods, importers, openfoodfacts, parser, settings, units, vision
from core import database
from core.database import DATA_DIR, connect, food_count

st.set_page_config(page_title="My Nutrition", page_icon="🥗", layout="wide")

MASS_VOLUME_UNITS = ["g", "kg", "oz", "lb", "ml", "l", "tsp", "tbsp", "cup", "fl oz"]


def is_public() -> bool:
    """
    Public "try-it" mode is switched on by the secret  public_mode = true  in
    Streamlit Community Cloud (or NUTRITION_PUBLIC=1 for tests). On your own
    computer there is no such secret, so the app runs in personal mode.
    """
    if os.environ.get("NUTRITION_PUBLIC") == "1":
        return True
    try:
        return bool(st.secrets.get("public_mode", False))
    except Exception:                                  # noqa: BLE001 - no secrets file = personal mode
        return False


def secret(name: str, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:                                  # noqa: BLE001
        return default


PUBLIC = is_public()
if PUBLIC:   # photo recognition needs Ollama on your own PC, so it is hidden online
    PAGES = ["Calculate a meal", "Scan a barcode", "Diary", "About the data"]
else:
    PAGES = ["Calculate a meal", "Scan a barcode", "Photo of a meal", "Diary", "Data & settings"]


# ---------------------------------------------------------------------------
# Shared resources (created once, reused on every screen refresh)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_conn():
    return connect()


@st.cache_resource(show_spinner="Loading the food databases - the first start takes about a minute…")
def load_food_data() -> list[str]:
    """If the database has no foods yet, import whatever downloads are in the data folder."""
    c = get_conn()
    if c.execute("SELECT COUNT(*) FROM foods").fetchone()[0]:
        return []
    reports = []
    cofid = importers.find_cofid_file()
    if cofid:
        try:
            reports.append(importers.import_cofid(c, cofid))
        except Exception as exc:                       # noqa: BLE001
            reports.append(f"CoFID import failed: {exc}")
    for path in importers.find_usda_files():
        try:
            reports.append(importers.import_usda(c, path))
        except Exception as exc:                       # noqa: BLE001
            reports.append(f"{path.name}: {exc}")
    return reports


@st.cache_resource
def get_index():
    return foods.FoodIndex(get_conn())


conn = get_conn()
load_reports = load_food_data()
index = get_index()
prefs = settings.load()
if PUBLIC:
    # each visitor gets their own settings and their own temporary diary
    prefs.update(st.session_state.setdefault("public_prefs", {"region": "UK"}))
    prefs.update({"use_ai_parser": False, "ollama_model": ""})


def diary_conn():
    """Your diary database. Online, each visitor gets a private in-memory one."""
    if not PUBLIC:
        return conn
    if "diary_db" not in st.session_state:
        st.session_state.diary_db = database.connect(":memory:")
    return st.session_state.diary_db
state = st.session_state
state.setdefault("meal", [])            # the meal being built
state.setdefault("page", PAGES[0])
if "goto" in state:                       # another screen asked to switch page
    state.page = state.pop("goto")
state.setdefault("food_cache", {})
state.setdefault("sentence", "")
counter = state.setdefault("counter", itertools.count(1))


def food_data(ref: str) -> dict | None:
    """Food details (with portions) cached for this session."""
    if ref not in state.food_cache:
        food = foods.get_food(conn, ref)
        if food and not food["portions"] and ref.startswith("food:"):
            food["portions"] = foods.borrow_portions(conn, index, food["name"])
            food["borrowed_portions"] = bool(food["portions"])
        state.food_cache[ref] = food
    return state.food_cache[ref]


def portion_label(p: dict) -> str:
    return f"{p['label']} ({p['grams']:g} g)"


# ---------------------------------------------------------------------------
# Building meal items
# ---------------------------------------------------------------------------

AUTO_PICK_SCORE = 75     # a match this good is chosen automatically (and shown, so you can change it)


def new_item(query: str, amount=None, unit=None, text=None, ref=None, fat_separate=False):
    """Create a meal item and fill in everything the description already told us."""
    item_id = next(counter)
    if ref:
        food = food_data(ref)
        candidates = [{"ref": ref, "name": food["name"], "source": food["source_label"], "score": 100}]
    else:
        candidates = index.search(query, prefs["region"], unit=unit, fat_separate=fat_separate)
    chosen = ref or (candidates[0]["ref"] if candidates and candidates[0]["score"] >= AUTO_PICK_SCORE else None)
    item = {"id": item_id, "text": text or query, "query": query, "candidates": candidates,
            "parsed_amount": amount, "parsed_unit": unit, "fat_separate": fat_separate}
    state[f"food_{item_id}"] = chosen
    state[f"amount_{item_id}"] = float(amount) if amount else None
    state[f"unit_{item_id}"] = None
    state[f"idk_{item_id}"] = False
    state.meal.append(item)
    return item


def unit_options(food: dict) -> list[str]:
    return MASS_VOLUME_UNITS + [portion_label(p) for p in food.get("portions", [])]


def ensure_portion(food: dict, unit: str):
    """Make sure a food has a portion for `unit` (e.g. 'slice'), borrowing one if needed."""
    if units.find_portion(unit, food.get("portions", [])) or not food["ref"].startswith("food:"):
        return
    extra = foods.borrow_portions(conn, index, food["name"], unit)
    have = {p["label"] for p in food["portions"]}
    added = [p for p in extra if p["label"] not in have]
    if added:
        food["portions"] = food["portions"] + added
        food["borrowed_portions"] = True


def match_parsed_unit(item: dict, food: dict):
    """Turn the unit the description gave ('medium', 'slice', 'tsp') into a unit option."""
    key = f"unit_{item['id']}"
    if state.get(key) or not item["parsed_unit"]:
        return
    unit = item["parsed_unit"]
    kind = units.unit_kind(unit)
    if kind == "count":
        ensure_portion(food, unit)
    portion = units.find_portion(unit, food.get("portions", []))
    if kind == "count":
        state[key] = portion_label(portion) if portion else None
    elif kind == "volume" and portion:
        state[key] = portion_label(portion)
    else:
        state[key] = unit


def resolve(item: dict) -> dict:
    """Work out what we know about an item and what is still missing."""
    iid = item["id"]
    out = {"ref": state.get(f"food_{iid}"), "missing": None, "grams": None,
           "basis": None, "notes": [], "food": None, "warning": None}
    if not out["ref"]:
        out["missing"] = "food"
        return out
    food = food_data(out["ref"])
    out["food"] = food
    match_parsed_unit(item, food)
    amount, unit = state.get(f"amount_{iid}"), state.get(f"unit_{iid}")

    if state.get(f"idk_{iid}"):
        portions = food.get("portions", [])
        default = units.find_portion("medium", portions) or units.find_portion("serving", portions) \
            or (portions[0] if portions else None)
        if default:
            out.update(grams=default["grams"], basis="default")
            out["notes"].append(f"amount unknown - assumed one standard portion: {portion_label(default)}, "
                                f"from {default['source']}")
            return out
        out["missing"] = "amount_no_default"
        return out

    if not amount or not unit:
        out["missing"] = "amount"
        return out
    portion = next((p for p in food.get("portions", []) if portion_label(p) == unit), None)
    try:
        if portion:
            out["grams"] = amount * portion["grams"]
            out["basis"] = "portion"
            out["notes"].append(f"{amount:g} × {portion_label(portion)}, portion weight from {portion['source']}")
        else:
            grams, basis, note = units.to_grams(amount, unit, [])
            out.update(grams=grams, basis=basis)
            if note:
                out["notes"].append(note)
    except ValueError:
        out["missing"] = "amount"
        return out
    if food["ref"].startswith("product:") and out["basis"] == "weighed":
        out["basis"] = "label"
    if item.get("fat_separate"):
        out["notes"].append("cooking fat counted as its own ingredient, so this uses the plain food's values")
    # Sanity check: a cup or ml of something very energy-dense is probably a powder/dry record
    kcal100 = engine.fill_energy(food["per100"]).get("energy_kcal")
    if unit in ("cup", "ml", "l", "fl oz") and kcal100 and kcal100 > 150:
        out["warning"] = (f"{food['name']} has {kcal100:.0f} kcal per 100 g - is that the drink, or a "
                          "dry/powder version? Check the food match.")
    return out


def add_from_sentence(sentence: str):
    items, used = parser.parse(sentence, prefs["use_ai_parser"], prefs["ollama_model"])
    for p in items:
        new_item(p.food, p.amount, p.unit, p.text, fat_separate=getattr(p, "fat_separate", False))
    state.parsed_with = used
    state.ask_fat = parser.mentions_cooking_in_fat(sentence, items)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("My Nutrition")
    st.radio("Go to", PAGES, key="page", label_visibility="collapsed")
    st.divider()
    region = st.radio("Prefer foods from", ["UK", "US"], index=0 if prefs["region"] == "UK" else 1,
                      horizontal=True, help="Both databases are always searched; this decides which "
                                            "comes first when both have a match.")
    if region != prefs["region"]:
        if PUBLIC:
            state.public_prefs["region"] = region
        else:
            settings.save({"region": region})
        st.rerun()
    if not index.entries:
        st.warning("No foods loaded yet. Open **Data & settings** to import the databases.")
    if PUBLIC:
        st.info("Demo version: your diary is private to you and kept only while this tab is open.")
    st.caption("Estimates only, not medical advice. Food data: UK CoFID (gov.uk), "
               "USDA FoodData Central, Open Food Facts (ODbL).")


# ---------------------------------------------------------------------------
# Page: Calculate a meal  (a short conversation)
# ---------------------------------------------------------------------------

def describe_amount(item, r) -> str:
    a, u = item.get("parsed_amount"), item.get("parsed_unit")
    if u in ("slice", "cup") and a and a > 1:
        u += "s"
    said = f"{a:g} {u} " if a and u and u != "item" else (f"{a:g} " if a else "")
    return f"{said}({r['grams']:.0f} g)" if r["grams"] else said.strip()


def page_calculate():
    st.header("What did you eat?")
    meal_text = st.chat_input("e.g. two medium eggs fried in 1 tsp butter, two slices of wholemeal toast")
    if meal_text:
        state.meal = []
        state.ask_fat = False
        state.meal_text = meal_text
        add_from_sentence(meal_text)
        st.rerun()

    if not state.meal:
        with st.chat_message("assistant"):
            st.write("Tell me what you had, as you'd say it, including amounts if you know them. "
                     "I'll only ask about anything I can't work out.")
        return

    with st.chat_message("user"):
        st.write(state.get("meal_text", ""))

    resolved = [(item, resolve(item)) for item in state.meal]
    pending = [(i, r) for i, r in resolved if r["missing"]]

    with st.chat_message("assistant"):
        if pending:
            ask_one_question(pending, len(resolved))
            return
        if state.get("ask_fat"):
            ask_cooking_fat()
            return
        show_results(resolved)


def ask_one_question(pending, n_items):
    """Ask only the first thing that is still missing."""
    item, r = pending[0]
    iid = item["id"]
    if len(pending) > 1:
        st.caption(f"{len(pending)} quick questions")
    if r["missing"] == "food":
        st.markdown(f"Which **{item['text']}** did you mean?")
        options = [c["ref"] for c in item["candidates"][:6]]
        labels = {c["ref"]: f"{c['name']}  ·  {c['source']}" for c in item["candidates"]}
        if options:
            choice = st.radio("Closest matches", options, format_func=lambda x: labels[x], index=None,
                              key=f"ask_food_{iid}", label_visibility="collapsed")
            if choice:
                state[f"food_{iid}"] = choice
                st.rerun()
        else:
            st.write(f"I couldn't find '{item['query']}' in the food databases.")
        with st.form(f"search_{iid}", border=False):
            q = st.text_input("Or describe it differently", placeholder="e.g. wholemeal bread")
            c1, c2 = st.columns(2)
            if c1.form_submit_button("Search") and q.strip():
                item["query"] = q
                item["candidates"] = index.search(q, prefs["region"], unit=item["parsed_unit"])
                st.rerun()
            if c2.form_submit_button("Leave it out"):
                state.meal = [i for i in state.meal if i["id"] != iid]
                st.rerun()
        return

    food = r["food"]
    st.markdown(f"How much **{food['name']}**?")
    if r["missing"] == "amount_no_default":
        st.caption("There's no standard portion for this food, so I need an amount.")
    with st.form(f"amount_{iid}_form", border=False):
        c1, c2 = st.columns([1, 2])
        amount = c1.number_input("Amount", min_value=0.0, step=0.5, value=state.get(f"amount_{iid}"))
        opts = unit_options(food)
        current = state.get(f"unit_{iid}")
        unit = c2.selectbox("Unit", opts, index=opts.index(current) if current in opts else 0)
        b1, b2, b3 = st.columns(3)
        ok = b1.form_submit_button("OK", type="primary")
        idk = b2.form_submit_button("I don't know", disabled=not food.get("portions"))
        drop = b3.form_submit_button("Leave it out")
    if ok and amount:
        state[f"amount_{iid}"], state[f"unit_{iid}"], state[f"idk_{iid}"] = float(amount), unit, False
        st.rerun()
    if idk:
        state[f"idk_{iid}"] = True
        st.rerun()
    if drop:
        state.meal = [i for i in state.meal if i["id"] != iid]
        st.rerun()


def ask_cooking_fat():
    st.markdown("That sounds fried or roasted. **Was any oil, butter or ghee used?**")
    c1, c2, c3 = st.columns(3)
    if c1.button("Yes, oil"):
        new_item("oil, vegetable"); state.ask_fat = False; st.rerun()
    if c2.button("Yes, butter"):
        new_item("butter"); state.ask_fat = False; st.rerun()
    if c3.button("No"):
        state.ask_fat = False; st.rerun()


def show_results(resolved):
    lines = [engine.Line(name=r["food"]["name"], grams=r["grams"], per100=r["food"]["per100"],
                         source_label=r["food"]["source_label"], basis=r["basis"], assumptions=r["notes"])
             for _, r in resolved]
    fraction = state.get("fraction", 100) / 100
    result = engine.calculate(lines, fraction)
    t = result["totals"]

    # The answer first
    kcal = engine.display_value(t["energy_kcal"], 0)
    if result["confidence"] in ("moderate", "low"):
        st.markdown(f"### About {kcal} kcal")
        st.caption(f"Likely between {result['kcal_low']:.0f} and {result['kcal_high']:.0f} kcal, "
                   "because some amounts are standard portions rather than weighed.")
    else:
        st.markdown(f"### {kcal} kcal")
    st.markdown("  ·  ".join(f"{label} **{_fmt(t[key], dec, u)}**" + ("*" if key in result["incomplete"] else "")
                             for key, label, u, dec in engine.NUTRIENTS[1:5]))
    if result["incomplete"]:
        st.caption("* not every ingredient has a value for this nutrient, so the total may be a little low.")

    # Short breakdown
    rows = [{"Food": item["text"], "Matched to": ln["name"], "Amount": describe_amount(item, r),
             "kcal": None if ln["values"]["energy_kcal"] is None else round(ln["values"]["energy_kcal"]),
             "Est.": "yes" if ln["basis"] in ("portion", "default") else ""}
            for (item, r), ln in zip(resolved, result["lines"])]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for item, r in resolved:
        if r["warning"]:
            st.warning(r["warning"])

    # Save
    with st.form("save", border=False):
        c1, c2, c3 = st.columns([1, 1, 1])
        slot = c1.selectbox("Meal", diary.SLOTS, index=_default_slot(), label_visibility="collapsed")
        day = c2.date_input("Date", dt.date.today(), label_visibility="collapsed")
        saved = c3.form_submit_button("Save to diary", type="primary")
    if saved:
        to_save = [{"name": ln.name, "ref": r["ref"], "source": ln.source_label, "grams": r["grams"] * fraction,
                    "per100": engine.fill_energy(ln.per100), "basis": ln.basis, "assumptions": ln.assumptions}
                   for (item, r), ln in zip(resolved, lines)]
        name = ", ".join(i["text"] for i, _ in resolved)[:80]
        diary.save_meal(diary_conn(), day.isoformat(), slot, name or "Meal", to_save)
        st.success(f"Saved to {slot}, {day:%d %b}.")

    # Details only for those who want them
    with st.expander("How I worked it out"):
        for ln, (item, r) in zip(result["lines"], resolved):
            p100 = engine.fill_energy(r["food"]["per100"])["energy_kcal"]
            if p100 is not None:
                st.markdown(f"- **{ln['name']}** ({ln['source']}): {p100:.1f} kcal/100 g × {ln['grams']:.1f} g "
                            f"÷ 100 = {ln['values']['energy_kcal']:.2f} kcal")
            else:
                st.markdown(f"- **{ln['name']}**: no energy value in {ln['source']}")
            for n in r["notes"]:
                st.caption(f"   {n}")
        if {"available", "total"} <= {r["food"].get("carb_basis") for _, r in resolved}:
            st.caption("Carbohydrate: UK CoFID excludes fibre, USDA includes it; this meal mixes both.")
        extra = "  ·  ".join(f"{label} {_fmt(t[key], dec, u)}" for key, label, u, dec in engine.NUTRIENTS[5:])
        st.caption(f"Also: {extra}  ·  total weight {result['total_grams']:.0f} g")

    with st.expander("Something not right? Change it"):
        state.fraction = st.slider("How much of it did you eat?", 10, 100, state.get("fraction", 100), 5,
                                   format="%d%%")
        for item, r in resolved:
            render_item(item, r)
        with st.form("add_more", clear_on_submit=True, border=False):
            extra_text = st.text_input("Add something you forgot", placeholder="e.g. 1 tbsp ketchup")
            if st.form_submit_button("Add") and extra_text.strip():
                add_from_sentence(extra_text)
                st.rerun()


def render_item(item: dict, r: dict):
    """Compact editor for one ingredient (food match, amount, unit)."""
    iid = item["id"]
    food = r["food"]
    st.markdown(f"**{item['text']}**")
    c1, c2, c3, c4 = st.columns([3, 1, 2, 1])
    options = [c["ref"] for c in item["candidates"]]
    labels = {c["ref"]: f"{c['name']} · {c['source']}" for c in item["candidates"]}
    c1.selectbox("Food", options, key=f"food_{iid}", format_func=lambda x: labels.get(x, x),
                 label_visibility="collapsed")
    clear_idk = lambda: state.update({f"idk_{iid}": False})  # noqa: E731 - an edit replaces "I don't know"
    c2.number_input("Amount", min_value=0.0, step=0.5, key=f"amount_{iid}", label_visibility="collapsed",
                    on_change=clear_idk)
    opts = unit_options(food)
    if state.get(f"unit_{iid}") not in opts:
        state[f"unit_{iid}"] = None
    c3.selectbox("Unit", opts, key=f"unit_{iid}", label_visibility="collapsed", on_change=clear_idk)
    if c4.button("Remove", key=f"rm_{iid}"):
        state.meal = [i for i in state.meal if i["id"] != iid]
        st.rerun()


def _fmt(value, decimals, unit_):
    return "n/a" if value is None else f"{engine.display_value(value, decimals)} {unit_}"


def _r(v):
    return None if v is None else round(v, 1)


def _default_slot():
    h = dt.datetime.now().hour
    return 0 if h < 11 else 1 if h < 15 else 2 if h < 21 else 3


# ---------------------------------------------------------------------------
# Page: Scan a barcode
# ---------------------------------------------------------------------------

def page_barcode():
    st.header("Scan a barcode")
    st.write("Take a photo of the barcode with your webcam, upload a photo, or type the number.")
    tab1, tab2, tab3 = st.tabs(["Webcam", "Upload photo", "Type number"])
    code = None
    with tab1:
        shot = st.camera_input("Hold the barcode up to the camera")
        if shot:
            code = _decode(Image.open(shot))
    with tab2:
        up = st.file_uploader("Photo of a barcode", type=["jpg", "jpeg", "png", "webp"])
        if up:
            code = _decode(Image.open(up))
    with tab3:
        typed = st.text_input("Barcode number (digits under the bars)")
        if typed:
            code = typed.strip()

    if code and code != state.get("bc_code"):
        state.bc_code = code
        local = conn.execute("SELECT * FROM products WHERE barcode = ?", (code,)).fetchone()
        if local:
            state.bc_product, state.bc_origin = dict(local), local["origin"]
        else:
            try:
                state.bc_product = openfoodfacts.lookup(code, secret("contact_email") if PUBLIC else None)
                state.bc_origin = "openfoodfacts"
            except Exception as exc:                  # noqa: BLE001
                st.error(f"Could not reach Open Food Facts ({exc.__class__.__name__}). "
                         "Check your internet connection, or enter the label below.")
                state.bc_product = None

    if state.get("bc_code"):
        if state.get("bc_product"):
            st.success(f"Found barcode {state.bc_code}. Check the values against the pack, "
                       "correct anything wrong, then save.")
            label_form(state.bc_product, state.bc_origin)
        else:
            st.warning(f"Barcode {state.bc_code} was not found. Enter the values from the label.")
            label_form({"barcode": state.bc_code}, "my_label")
    else:
        with st.expander("No barcode? Enter a nutrition label by hand"):
            label_form({"barcode": openfoodfacts.next_manual_code(conn)}, "my_label")


def _decode(img):
    codes = barcode.read_barcodes(img)
    if not codes:
        st.error("No barcode found in that picture. Try closer, flatter and with less glare, "
                 "or type the number.")
        return None
    return codes[0]


def label_form(p: dict, origin: str):
    fields = [("energy_kcal", "Energy (kcal)"), ("energy_kj", "Energy (kJ)"), ("protein_g", "Protein (g)"),
              ("carbohydrate_g", "Carbohydrate (g)"), ("sugars_g", "of which sugars (g)"),
              ("fat_g", "Fat (g)"), ("saturates_g", "of which saturates (g)"),
              ("fibre_g", "Fibre (g)"), ("salt_g", "Salt (g)")]
    with st.form(f"label_{p.get('barcode')}"):
        c1, c2 = st.columns(2)
        name = c1.text_input("Product name", p.get("name", ""))
        brand = c2.text_input("Brand", p.get("brand", "") or "")
        c1, c2 = st.columns(2)
        basis = c1.radio("Values below are", ["per 100 g / 100 ml", "per serving"], horizontal=True)
        serving = c2.number_input("Serving size (g)", min_value=0.0, value=float(p.get("serving_g") or 0))
        st.caption("Leave a box empty if the label does not show it (empty = unknown, not zero).")
        values = {}
        cols = st.columns(3)
        for i, (key, label) in enumerate(fields):
            current = p.get(key)
            text = cols[i % 3].text_input(label, "" if current is None else f"{current:g}", key=f"lf_{key}_{p.get('barcode')}")
            try:
                values[key] = float(text) if text.strip() else None
            except ValueError:
                values[key] = None
                st.error(f"'{text}' is not a number ({label}).")
        if origin == "openfoodfacts":
            st.caption("Data from Open Food Facts (openfoodfacts.org), ODbL licence. "
                       "Entered by volunteers - please check it.")
        submitted = st.form_submit_button("Save product and add to my meal", type="primary")
    if submitted:
        if not name.strip():
            st.error("Please give the product a name.")
            return
        if values["energy_kcal"] is None and values["energy_kj"] is None:
            st.error("Enter energy in kcal or kJ (from the label).")
            return
        if basis == "per serving":
            if serving <= 0:
                st.error("Enter the serving size in grams to convert per-serving values.")
                return
            values = engine.per_serving_to_per100(values, serving)
        product = {"barcode": p["barcode"], "name": name.strip(), "brand": brand.strip(),
                   "serving_g": serving or None, **values}
        openfoodfacts.save_product(conn, product, origin, confirmed=True)
        index.refresh()
        state.food_cache.pop(f"product:{p['barcode']}", None)
        if not state.meal:
            state.meal_text = ""
        state.meal_text = (state.get("meal_text", "") + f", {name} (scanned)").lstrip(", ")
        new_item(name, ref=f"product:{p['barcode']}")
        state.bc_code = None
        state.goto = PAGES[0]
        st.rerun()


# ---------------------------------------------------------------------------
# Page: Photo of a meal (optional, needs Ollama)
# ---------------------------------------------------------------------------

def page_photo():
    st.header("Photo of a meal")
    st.write("A free AI model on your own computer suggests which foods it sees. "
             "**You confirm them, and the app then asks for each portion size** - "
             "a photo cannot tell weight.")
    models = vision.ollama_models()
    if not models:
        st.info("Photo recognition needs **Ollama** (free) running on this computer, with a vision "
                "model installed. See step 7 in the build guide. Everything else works without it.")
        return
    default = prefs["ollama_model"] if prefs["ollama_model"] in models else models[0]
    model = st.selectbox("Vision model", models, index=models.index(default))
    tab1, tab2 = st.tabs(["Webcam", "Upload photo"])
    img = None
    with tab1:
        shot = st.camera_input("Photo of your plate")
        if shot:
            img = Image.open(shot)
    with tab2:
        up = st.file_uploader("Meal photo", type=["jpg", "jpeg", "png", "webp"])
        if up:
            img = Image.open(up)
    if img is not None and st.button("Identify foods", type="primary"):
        with st.spinner("Looking at the photo (can take a minute on a laptop)…"):
            try:
                state.photo_foods = vision.suggest_foods(img, model)
            except Exception as exc:                  # noqa: BLE001
                st.error(f"The model could not process the photo: {exc}")
                state.photo_foods = []
    if state.get("photo_foods"):
        st.subheader("Is this right?")
        keep = []
        for i, f in enumerate(state.photo_foods):
            c1, c2 = st.columns([1, 4])
            ok = c1.checkbox("Include", value=f["confidence"] != "low", key=f"ph_ok_{i}")
            name = c2.text_input(f"Food ({f['confidence']} confidence)", f["name"], key=f"ph_name_{i}")
            if ok and name.strip():
                keep.append(name.strip())
        extra = st.text_input("Anything the photo missed? (e.g. oil, butter, sauce)")
        if st.button("Use these foods", type="primary"):
            chosen = keep + ([extra] if extra.strip() else [])
            state.meal = []
            state.meal_text = "From my photo: " + ", ".join(chosen)
            for name in chosen:
                new_item(name)                        # no amount -> the app will ask
            state.photo_foods = []
            state.goto = PAGES[0]
            st.rerun()


# ---------------------------------------------------------------------------
# Page: Diary
# ---------------------------------------------------------------------------

def page_diary():
    st.header("Food diary")
    day = st.date_input("Day", dt.date.today())
    entries = diary.day_entries(diary_conn(), day.isoformat())
    totals = diary.day_totals(entries)
    cols = st.columns(5)
    for col, (key, label, unit_, dec) in zip(cols, engine.NUTRIENTS[:5]):
        mark = "*" if key in totals["incomplete"] else ""
        col.metric(f"{label} today", _fmt(totals["totals"][key], dec, unit_) + mark)
    if not entries:
        st.info("Nothing recorded for this day. Calculate a meal and press **Save to diary**.")
        return
    for slot in diary.SLOTS:
        for e in [x for x in entries if x["entry"]["slot"] == slot]:
            res = e["result"]
            kcal = engine.display_value(res["totals"]["energy_kcal"], 0) if res else "0"
            with st.expander(f"{slot}: {e['entry']['meal_name']} — {kcal} kcal"):
                df = pd.DataFrame([{"id": i["id"], "Ingredient": i["display_name"], "Grams": i["grams"],
                                    "kcal": _r(l["values"]["energy_kcal"]),
                                    "Protein g": _r(l["values"]["protein_g"]),
                                    "Carbs g": _r(l["values"]["carbohydrate_g"]),
                                    "Fat g": _r(l["values"]["fat_g"]), "Fibre g": _r(l["values"]["fibre_g"]),
                                    "Est.": "yes" if i["estimated"] else "", "Source": i["source_label"]}
                                   for i, l in zip(e["items"], res["lines"])])
                edited = st.data_editor(df, hide_index=True, width="stretch",
                                        column_config={"id": None},
                                        disabled=[c for c in df.columns if c != "Grams"],
                                        key=f"diary_{e['entry']['id']}")
                c1, c2 = st.columns(2)
                if c1.button("Save changed grams", key=f"dsave_{e['entry']['id']}"):
                    for _, row in edited.iterrows():
                        if row["Grams"] and row["Grams"] > 0:
                            diary.update_item_grams(diary_conn(), int(row["id"]), float(row["Grams"]))
                    st.rerun()
                if c2.button("Delete this meal", key=f"ddel_{e['entry']['id']}"):
                    diary.delete_entry(diary_conn(), e["entry"]["id"])
                    st.rerun()


# ---------------------------------------------------------------------------
# Page: Data & settings
# ---------------------------------------------------------------------------

def page_about():
    st.header("About the data")
    st.write("Calories and nutrients come only from these databases (or a label you typed in); "
             "plain code does the arithmetic. Results are estimates, not medical advice.")
    counts = food_count(conn)
    if counts:
        st.dataframe(pd.DataFrame(counts).rename(columns={"n": "foods"}), hide_index=True, width="stretch")
    else:
        st.warning("No food databases are loaded on this server yet.")
    for r in load_reports:
        st.caption(r)
    st.markdown("- **UK CoFID 2021**: McCance and Widdowson's Composition of Foods Integrated Dataset, gov.uk\n"
                "- **USDA FoodData Central**: U.S. Department of Agriculture, public domain\n"
                "- **Open Food Facts**: openfoodfacts.org, Open Database Licence (barcode lookups)")
    st.caption("Packaged products added in this demo are shared by all visitors and are cleared "
               "whenever the app restarts.")


def page_data():
    st.header("Data & settings")
    st.subheader("Food databases on this computer")
    counts = food_count(conn)
    if counts:
        st.dataframe(pd.DataFrame(counts).rename(columns={"n": "foods"}), hide_index=True,
                     width="stretch")
    n_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    st.caption(f"Saved packaged products: {n_products}")

    st.subheader("Import")
    st.write(f"Put the downloaded files in this folder, then press Import:  `{DATA_DIR}`")
    cofid = importers.find_cofid_file()
    usda = importers.find_usda_files()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**UK CoFID** (Excel file)")
        st.caption(cofid.name if cofid else "Not found in the data folder yet.")
        if cofid and st.button("Import CoFID"):
            with st.spinner("Importing…"):
                try:
                    st.success(importers.import_cofid(conn, cofid))
                except Exception as exc:              # noqa: BLE001
                    st.error(f"Import failed: {exc}")
            index.refresh(); state.food_cache = {}
    with c2:
        st.markdown("**USDA FoodData Central** (CSV zip files)")
        st.caption(", ".join(p.name for p in usda) if usda else "Not found in the data folder yet.")
        if usda and st.button("Import USDA"):
            for path in usda:
                with st.spinner(f"Importing {path.name} (can take a minute)…"):
                    try:
                        st.success(importers.import_usda(conn, path))
                    except Exception as exc:          # noqa: BLE001
                        st.error(f"{path.name}: {exc}")
            index.refresh(); state.food_cache = {}

    st.subheader("Settings")
    with st.form("settings"):
        email = st.text_input("Your email for Open Food Facts (optional)", prefs["contact_email"],
                              help="Open Food Facts asks apps to identify themselves. It is sent only "
                                   "to them, only when you look up a barcode.")
        models = vision.ollama_models()
        use_ai = st.checkbox("Use a local AI model (Ollama) to read meal descriptions",
                             prefs["use_ai_parser"], disabled=not models,
                             help="Off = built-in rules. The AI only splits text into foods and amounts; "
                                  "it never supplies nutrition values.")
        model = st.selectbox("Ollama model", models or ["(Ollama not running)"],
                             index=models.index(prefs["ollama_model"]) if prefs["ollama_model"] in models else 0)
        if st.form_submit_button("Save settings"):
            settings.save({"contact_email": email.strip(), "use_ai_parser": bool(use_ai and models),
                           "ollama_model": model if models else ""})
            st.success("Saved.")
            st.rerun()


if state.page not in PAGES:
    state.goto = PAGES[0]
    st.rerun()
{"Calculate a meal": page_calculate, "Scan a barcode": page_barcode, "Photo of a meal": page_photo,
 "Diary": page_diary, "Data & settings": page_data, "About the data": page_about}[state.page]()
