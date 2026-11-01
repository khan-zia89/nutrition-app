"""
parser.py - turns ordinary language into a list of items.

    "2 eggs, 30g cheddar and a slice of toast with butter"
      -> [ {amount: 2,  unit: 'item',  food: 'eggs'},
           {amount: 30, unit: 'g',     food: 'cheddar'},
           {amount: 1,  unit: 'slice', food: 'toast'},
           {amount: None, unit: None,  food: 'butter'} ]

IMPORTANT: the parser only reads words. It never produces nutrition values.
Quantities it cannot find are left as None, and the app asks you for them.

Two parsers are available:
  * parse_rules()  - built-in rules, free, instant, works offline (default)
  * parse_with_ollama() - optional; uses a free local AI model through Ollama
    for messier sentences. Its answer is checked and, if anything looks
    wrong, the app falls back to parse_rules().
"""

import json
import re
from dataclasses import dataclass, asdict

import requests

from core import units

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "half": 0.5, "quarter": 0.25, "couple": 2, "dozen": 12,
}
UNICODE_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}

# Words that mean "some unknown amount" - the app will ask.
VAGUE = ["some", "a bit of", "a little", "a knob of", "knob of", "a splash of", "splash of",
         "a dash of", "dash of", "a handful of", "handful of", "a few", "a drizzle of", "drizzle of",
         "a bowl of", "bowl of", "a plate of", "plate of", "lots of", "a lot of"]

# Words that suggest something was cooked in fat (used to ask about oil/butter).
COOKED_IN_FAT = ["fried", "fry", "sauteed", "sautéed", "roast", "roasted", "omelette", "omelet",
                 "stir-fry", "stir fry", "scrambled", "pan-fried", "curry"]
FATS = ["oil", "butter", "ghee", "lard", "margarine", "spread"]

SPLIT_PATTERN = re.compile(r",|;|\n|\+|&|\band\b|\bwith\b|\bplus\b|\bthen\b|\bon\b", re.IGNORECASE)
NUMBER = r"(\d+(?:\.\d+)?(?:\s*/\s*\d+)?|\d*\s*[½¼¾⅓⅔])"


@dataclass
class ParsedItem:
    text: str                 # the original words for this item
    food: str                 # the words used to search the database
    amount: float | None      # None = not stated
    unit: str | None          # normalised unit, e.g. 'g', 'tbsp', 'item'
    vague: bool = False       # true if the user said 'some', 'a bit of', ...
    fat_separate: bool = False  # cooked in a fat that is listed as its own item

    def to_dict(self):
        return asdict(self)


def _to_number(text: str) -> float | None:
    text = text.strip()
    for sym, val in UNICODE_FRACTIONS.items():
        if sym in text:
            whole = text.replace(sym, "").strip()
            return (float(whole) if whole else 0.0) + val
    if "/" in text:
        top, bottom = [t.strip() for t in text.split("/", 1)]
        return float(top) / float(bottom) if float(bottom) else None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_chunk(chunk: str) -> ParsedItem | None:
    original = chunk.strip(" .")
    text = original.lower()
    if not text:
        return None

    vague = False
    for v in sorted(VAGUE, key=len, reverse=True):
        if text.startswith(v + " ") or text == v:
            text = text[len(v):].strip()
            vague = True
            break

    amount, unit = None, None

    # e.g. "250g chicken", "2 x 30 g", "1.5 cups rice", "½ tbsp oil"
    m = re.match(rf"^{NUMBER}\s*(?:x\s*)?([a-z]+(?:\s?oz)?)?\.?\s*(?:of\s+)?(.*)$", text)
    if m and _to_number(m.group(1)) is not None:
        amount = _to_number(m.group(1))
        maybe_unit = units.normalise_unit(m.group(2)) if m.group(2) else None
        if maybe_unit:
            unit, food = maybe_unit, m.group(3)
        else:
            unit, food = "item", ((m.group(2) or "") + " " + m.group(3)).strip()
    else:
        # e.g. "two eggs", "a slice of toast", "half an avocado"
        words = text.split()
        if words and words[0] in NUMBER_WORDS:
            amount = NUMBER_WORDS[words[0]]
            rest = words[1:]
            if rest and rest[0] in ("a", "an", "of"):        # "half a ...", "couple of ..."
                rest = rest[1:]
            if rest and rest[0] == "of":
                rest = rest[1:]
            maybe_unit = units.normalise_unit(rest[0]) if rest else None
            if maybe_unit and len(rest) > 1:
                unit = maybe_unit
                rest = rest[1:]
                if rest and rest[0] == "of":
                    rest = rest[1:]
            else:
                unit = "item"
            food = " ".join(rest)
        else:
            food = text

    # A size word in front of the food ("2 large eggs") becomes the unit.
    size = re.match(r"^(extra large|small|medium|large|big)\s+(.*)$", food)
    if size and unit in (None, "item"):
        unit, food = units.normalise_unit(size.group(1)) or size.group(1), size.group(2)
    # ...unless a real unit follows it ("2 medium slices of bread" -> unit 'slice')
    if unit in ("small", "medium", "large", "extra large", "item"):
        nxt = re.match(r"^([a-z]+)\s+(?:of\s+)?(.+)$", food)
        if nxt and units.normalise_unit(nxt.group(1)) not in (None, "small", "medium", "large", "item"):
            unit, food = units.normalise_unit(nxt.group(1)), nxt.group(2)

    food = re.sub(r"^(of|the|my)\s+", "", food).strip()
    if not food:
        return None
    if vague:
        amount, unit = None, None
    return ParsedItem(text=original, food=food, amount=amount, unit=unit, vague=vague)


# "1. ", "2) ", "- ", "* ", "• " at the start of a line are list markers, not amounts.
LIST_MARKER = re.compile(r"^\s*(?:\d+\s*[.)]|[-*•])\s+", re.MULTILINE)
# "eggs fried in a teaspoon of butter" -> "eggs" + "a teaspoon of butter"
COOKED_IN = re.compile(
    r"\b(?:fried|cooked|sauteed|sautéed|scrambled|roasted|roast|pan[- ]fried|stir[- ]fried|done|made)"
    r"\s+(?:in|with|using)\s+", re.IGNORECASE)
# Cooking words removed from a food's search words when its fat is listed separately,
# so we use the plain food's values and count the fat once (no double counting).
METHOD_WORDS = re.compile(r"\b(fried|pan-fried|sauteed|sautéed|scrambled|roasted|roast)\b")


def parse_rules(sentence: str) -> list[ParsedItem]:
    """Split a sentence into items using simple, predictable rules."""
    text = LIST_MARKER.sub("", sentence or "")
    fat_listed = any(f in text.lower() for f in FATS)
    text = COOKED_IN.sub(", ", text)
    items = []
    for chunk in SPLIT_PATTERN.split(text):
        item = _parse_chunk(chunk)
        if not item:
            continue
        if fat_listed and not any(f in item.food for f in FATS):
            cleaned = re.sub(r"\s+", " ", METHOD_WORDS.sub("", item.food)).strip()
            if cleaned != item.food or COOKED_IN.search(sentence or ""):
                item.fat_separate = True
            item.food = cleaned or item.food
        items.append(item)
    return items


def mentions_cooking_in_fat(sentence: str, items: list[ParsedItem]) -> bool:
    """True if the meal sounds fried/roasted but no oil or butter was listed."""
    text = (sentence or "").lower()
    cooked = any(w in text for w in COOKED_IN_FAT)
    has_fat = any(any(f in i.food for f in FATS) for i in items)
    return cooked and not has_fat


# ---------------------------------------------------------------------------
# Optional: local AI parser through Ollama (free, runs on your own computer)
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434/api/chat"

PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "food": {"type": "string"},
                    "amount": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                },
                "required": ["food", "amount", "unit"],
            },
        }
    },
    "required": ["items"],
}

PARSE_PROMPT = (
    "Split this meal description into separate foods. For each food give the food name, "
    "the amount ONLY if the user stated it, and the unit ONLY if stated "
    "(g, kg, oz, lb, ml, l, tsp, tbsp, cup, slice, item, small, medium, large). "
    "Use null when not stated. Never guess amounts. Never give calories or nutrients.\n\n"
    "Meal: "
)


def parse_with_ollama(sentence: str, model: str) -> list[ParsedItem]:
    """Ask a local Ollama model to split the sentence. Raises on any problem."""
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "messages": [{"role": "user", "content": PARSE_PROMPT + sentence}],
            "format": PARSE_SCHEMA,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=120,
    )
    response.raise_for_status()
    data = json.loads(response.json()["message"]["content"])
    items = []
    for raw in data.get("items", []):
        food = str(raw.get("food", "")).strip()
        if not food:
            continue
        amount = raw.get("amount")
        if amount is not None:
            amount = float(amount)
            if amount <= 0 or amount > 10000:
                raise ValueError(f"Unbelievable amount from AI: {amount}")
            # Only keep an amount if a number really appears in what the user typed
            if not re.search(r"\d|" + "|".join(NUMBER_WORDS), sentence.lower()):
                amount = None
        unit = units.normalise_unit(raw.get("unit")) if raw.get("unit") else None
        if amount is not None and unit is None:
            unit = "item"
        items.append(ParsedItem(text=food, food=food.lower(), amount=amount, unit=unit))
    if not items:
        raise ValueError("AI returned no items")
    return items


def parse(sentence: str, use_ai: bool = False, model: str = "") -> tuple[list[ParsedItem], str]:
    """Parse a sentence. Returns (items, which parser was used)."""
    if use_ai and model:
        try:
            return parse_with_ollama(sentence, model), f"local AI ({model})"
        except Exception as exc:                      # noqa: BLE001 - fall back on any failure
            return parse_rules(sentence), f"built-in rules (AI unavailable: {exc.__class__.__name__})"
    return parse_rules(sentence), "built-in rules"
