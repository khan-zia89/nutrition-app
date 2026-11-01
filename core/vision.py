"""
vision.py - OPTIONAL food photo recognition with a free local AI model.

How it works
  1. You take or upload a photo of your meal.
  2. A vision model running on YOUR computer (through Ollama) suggests which
     foods it can see. It is told NOT to estimate weights or calories.
  3. You confirm, remove or rename the suggestions.
  4. The confirmed names go through the normal flow: database match,
     then the app asks you for each portion size.

A photo can never establish portion weight, so every amount is asked.
If Ollama is not installed or not running, the rest of the app still works.
"""

import base64
import io
import json

import requests
from PIL import Image

OLLAMA_URL = "http://localhost:11434"

SCHEMA = {
    "type": "object",
    "properties": {
        "foods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["name", "confidence"],
            },
        }
    },
    "required": ["foods"],
}

PROMPT = (
    "List the separate foods and drinks visible in this photo, using short plain names a "
    "nutrition database would use (e.g. 'boiled rice', 'fried egg', 'baked beans'). Include "
    "visible sauces or spreads. Do NOT estimate weights, portions or calories. "
    "Give a confidence for each item."
)


def ollama_models() -> list[str]:
    """Names of models installed in Ollama, or [] if Ollama is not running."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:                                  # noqa: BLE001
        return []


def suggest_foods(image: Image.Image, model: str) -> list[dict]:
    """Ask the local vision model what foods it sees. Raises if it fails."""
    image = image.convert("RGB")
    image.thumbnail((1024, 1024))                      # smaller = faster, same accuracy
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
            "format": SCHEMA,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=300,
    )
    r.raise_for_status()
    data = json.loads(r.json()["message"]["content"])
    foods = []
    for f in data.get("foods", []):
        name = str(f.get("name", "")).strip()
        if name and name.lower() not in [x["name"].lower() for x in foods]:
            foods.append({"name": name, "confidence": f.get("confidence", "low")})
    return foods
