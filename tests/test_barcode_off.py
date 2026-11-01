"""Barcode reading (offline) and Open Food Facts parsing (with a fake response)."""

import pytest
import zxingcpp
from PIL import Image

from core import barcode, openfoodfacts


def test_reads_ean13_from_image():
    try:
        img = zxingcpp.write_barcode(zxingcpp.BarcodeFormat.EAN13, "5000159484695", width=400, height=150)
    except AttributeError:
        bc = zxingcpp.create_barcode("5000159484695", zxingcpp.BarcodeFormat.EAN13)
        img = bc.to_image(scale=4)
    image = Image.fromarray(img) if not isinstance(img, Image.Image) else img
    canvas = Image.new("L", (image.width + 80, image.height + 80), 255)
    canvas.paste(image.convert("L"), (40, 40))
    assert barcode.read_barcodes(canvas) == ["5000159484695"]


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"status": 1, "product": {
            "product_name": "Test crisps", "brands": "TestBrand, Other",
            "serving_quantity": "25",
            "nutriments": {"energy-kcal_100g": 520, "proteins_100g": 6, "carbohydrates_100g": 52,
                           "fat_100g": 31, "fiber_100g": "", "salt_100g": 1.3}}}


def test_off_parsing(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        seen["ua"] = headers["User-Agent"]
        return FakeResponse()

    monkeypatch.setattr(openfoodfacts.requests, "get", fake_get)
    p = openfoodfacts.lookup("5000159484695")
    assert p["name"] == "Test crisps" and p["brand"] == "TestBrand"
    assert p["energy_kcal"] == 520 and p["serving_g"] == 25
    assert p["fibre_g"] is None                     # blank on OFF -> unknown, not zero
    assert seen["ua"].startswith("PersonalNutritionApp/")
