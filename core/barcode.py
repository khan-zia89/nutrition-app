"""
barcode.py - read a barcode from a photo (webcam snapshot or uploaded image).

Uses zxing-cpp, a free barcode-reading library. It works offline.
"""

from PIL import Image, ImageOps
import zxingcpp

PRODUCT_FORMATS = {"EAN13", "EAN8", "UPCA", "UPCE", "EAN-13", "EAN-8", "UPC-A", "UPC-E"}


def read_barcodes(image: Image.Image) -> list[str]:
    """Return the product barcodes found in an image (may be empty)."""
    found = []
    # Try the image as-is, then a greyscale, contrast-boosted copy (helps with glare)
    for candidate in (image, ImageOps.autocontrast(ImageOps.grayscale(image))):
        for result in zxingcpp.read_barcodes(candidate):
            fmt = str(result.format).split(".")[-1].upper().replace("_", "")
            if result.text and (fmt.replace("-", "") in {f.replace("-", "") for f in PRODUCT_FORMATS}):
                if result.text not in found:
                    found.append(result.text)
        if found:
            break
    return found
