"""
lightning-ocr · Local fallback OCR engines
Tesseract-OCR and EasyOCR — no network required.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional, Tuple

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


def tesseract_ocr_with_confidence(image_bytes: bytes, lang: str = "") -> Dict[str, Any]:
    """Run pytesseract and return text + per-word confidence data."""
    try:
        from app.config import settings
        if not lang:
            lang = settings.TESSERACT_LANG or "eng"
        import pytesseract
        img = _pil_from_bytes(image_bytes)

        # Get text
        text = pytesseract.image_to_string(img, lang=lang).strip()

        # Get per-word confidence data
        try:
            data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
            confidences = [
                int(c) for c, t in zip(data["conf"], data["text"])
                if int(c) > 0 and t.strip()
            ]
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            word_count = len(confidences)
        except Exception:
            avg_confidence = 0.0
            word_count = 0

        # Detect language if auto-detect
        detected_lang = lang
        if "+" not in lang and lang != "eng":
            try:
                osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
                detected_lang = osd.get("lang", lang)
            except Exception:
                pass

        return {
            "text": text,
            "confidence": round(avg_confidence, 2),
            "word_count": word_count,
            "detected_lang": detected_lang,
        }
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


def easyocr_ocr_with_confidence(image_bytes: bytes, langs: str = "en") -> Dict[str, Any]:
    """Run EasyOCR and return text + per-word confidence data."""
    try:
        lang_list = [l.strip() for l in langs.split(",")]
        reader = _get_easyocr_reader(lang_list)
        results = reader.readtext(image_bytes, detail=1)

        lines = []
        confidences = []
        for bbox, text, conf in results:
            lines.append(text)
            confidences.append(conf)

        text = "\n".join(lines).strip()
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "text": text,
            "confidence": round(avg_confidence * 100, 2),  # EasyOCR returns 0-1
            "word_count": len(confidences),
            "detected_lang": langs.split(",")[0] if langs else "en",
        }
    except ImportError:
        raise RuntimeError("easyocr not installed.")
    except Exception as exc:
        log.error("EasyOCR failed: %s", exc)
        raise