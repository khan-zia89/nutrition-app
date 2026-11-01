"""
settings.py - small personal settings saved in data/settings.json.
"""

import json

from core.database import DATA_DIR

PATH = DATA_DIR / "settings.json"
DEFAULTS = {
    "region": "UK",            # which database to prefer when both have a match
    "contact_email": "",       # sent to Open Food Facts to identify the app (optional)
    "use_ai_parser": False,    # use a local Ollama model to read meal text
    "ollama_model": "",        # e.g. 'gemma3:4b'
}


def load() -> dict:
    data = dict(DEFAULTS)
    if PATH.exists():
        try:
            data.update(json.loads(PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    return data


def save(values: dict):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    data = load()
    data.update(values)
    PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
