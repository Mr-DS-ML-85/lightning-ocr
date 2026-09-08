"""
lightning-ocr · Local fallback OCR engines
Tesseract-OCR and EasyOCR — no network required.
"""
from __future__ import annotations

import io
import logging
from typing import List

log = logging.getLogger("lightning_ocr.fallback")


def _pil_from_bytes(data: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(data)).convert("RGB")


# ── Tesseract ────────────────────────────────────────────────────────────────

def tesseract_ocr(image_bytes: bytes, lang: str = "") -> str:
    """Run pytesseract on raw image bytes."""
    try:
        from app.config import settings
        if not lang:
            lang = settings.TESSERACT_LANG or "eng"
        import pytesseract
        img = _pil_from_bytes(image_bytes)
        return pytesseract.image_to_string(img, lang=lang).strip()
    except ImportError:
        raise RuntimeError(
            "pytesseract not installed. Add pytesseract + tesseract-ocr to your image."
        )
    except Exception as exc:
        log.error("Tesseract failed: %s", exc)
        raise


# ── EasyOCR ──────────────────────────────────────────────────────────────────

_easyocr_reader = None


def _get_easyocr_reader(langs: List[str]):
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(langs, gpu=False)
    return _easyocr_reader


def easyocr_ocr(image_bytes: bytes, langs: str = "en") -> str:
    """Run EasyOCR on raw image bytes."""
    try:
        lang_list = [l.strip() for l in langs.split(",")]
        reader = _get_easyocr_reader(lang_list)
        results = reader.readtext(image_bytes, detail=0)
        return "\n".join(results).strip()
    except ImportError:
        raise RuntimeError("easyocr not installed.")
    except Exception as exc:
        log.error("EasyOCR failed: %s", exc)
        raise