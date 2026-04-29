# Place at: utils/ocr_utils.py
"""
utils/ocr_utils.py — OCR wrapper.
Falls back gracefully if Tesseract is not installed rather than crashing.
"""
from __future__ import annotations

import io
import logging
from PIL import Image

logger = logging.getLogger(__name__)

_TESSERACT_OK: bool | None = None


def _check_tesseract() -> bool:
    global _TESSERACT_OK
    if _TESSERACT_OK is not None:
        return _TESSERACT_OK
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        _TESSERACT_OK = True
        logger.info("Tesseract OCR available")
    except Exception as e:
        _TESSERACT_OK = False
        logger.warning(
            "Tesseract not available — OCR disabled for scanned PDFs/images. "
            "Install: apt-get install tesseract-ocr  |  error: %s", e
        )
    return _TESSERACT_OK


def ocr_pil_image(img: Image.Image) -> str:
    """OCR a PIL image. Returns empty string if Tesseract unavailable."""
    if not _check_tesseract():
        return "[OCR unavailable — Tesseract not installed]"
    import pytesseract
    text = pytesseract.image_to_string(img).strip()
    logger.debug("OCR produced %d chars", len(text))
    return text


def ocr_image_bytes(raw_bytes: bytes) -> str:
    """OCR raw image bytes."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        return ocr_pil_image(img)
    except Exception as exc:
        logger.error("ocr_image_bytes failed: %s", exc)
        return ""
