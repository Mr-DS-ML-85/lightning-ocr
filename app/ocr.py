"""
lightning-ocr · Core OCR dispatch
Tries primary backend → auto-falls back through priority chain → Tesseract last resort.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import io
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.backends import get_backend
from app.config import BACKENDS, BackendConfig, settings
from app.storage import save_job

log = logging.getLogger("lightning_ocr.ocr")

# ── Supported document formats ──────────────────────────────────────────────
DOCX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}
PPTX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
}
XLSX_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}
DOCUMENT_CONTENT_TYPES = DOCX_CONTENT_TYPES | PPTX_CONTENT_TYPES | XLSX_CONTENT_TYPES
TEXT_CONTENT_TYPES = {"text/plain"}

# ── Extension-based content-type map (magic cannot identify office zip bundles) ─
_EXT_TO_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpe": "image/jpeg",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".txt": "text/plain",
    ".md": "text/plain",
}

# ── Security: SSRF protection for backend URLs ──────────────────────────────
_RESERVED_IPS = re.compile(
    r'^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.|127\.|0\.|'
    r'localhost(?:\.|$)|\.(?:local|internal|onion)(?:\.|$)|192\.0\.2\.)',
    re.IGNORECASE
)

# Known-safe local backends — explicitly allowed even though they match _RESERVED_IPS
# (these are local llama.cpp/Ollama servers we trust)
_KNOWN_LOCAL_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    # Add other trusted local hosts here, e.g. "llama", "ollama"
    # Wildcards not supported — list specific hostnames only.
})

def _is_reserved_ip(url: str) -> bool:
    """Check if URL points to reserved/internal IP (SSRF protection)."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or parsed.netloc or "").lower().rstrip(".")
        if host in _KNOWN_LOCAL_HOSTS:
            return False
        return bool(_RESERVED_IPS.search(host))
    except Exception:
        return False

def validate_backend_url(url: str) -> bool:
    """Validate backend URL is safe (no SSRF)."""
    if not url:
        return True
    return not _is_reserved_ip(url)

# ── Concurrency control ────────────────────────────────────────────────────
_MAX_CONCURRENT_OCR = 8  # Prevent GPU OOM
_ocr_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_OCR)

# ── Input validation ────────────────────────────────────────────────────────
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
ALLOWED_CONTENT_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "image/bmp", "image/tiff", "image/x-icon", "image/avif", "image/svg+xml",
    "application/pdf",
} | DOCUMENT_CONTENT_TYPES | TEXT_CONTENT_TYPES


def detect_content_type(image_bytes: bytes, filename: str = "") -> str:
    """
    Reliably detect content type: filename extension first (magic cannot
    identify Office zip bundles), then magic sniffing as fallback.
    Any image/* is accepted (mirrors the WebUI's accept="image/*,application/pdf").
    Raises HTTPException(400) for unsupported content.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    ext_ct = _EXT_TO_CONTENT_TYPE.get(ext, "")

    try:
        import magic
        sniffed = magic.from_buffer(image_bytes, mime=True) or ""
    except ImportError:
        log.warning("python-magic not installed, relying on extension")
        sniffed = ""

    # Extension wins when the sniffed type is generic (zip / octet-stream / xml)
    if ext_ct and sniffed in {"", "application/zip", "application/octet-stream", "text/xml", "text/plain"}:
        if ext_ct in ALLOWED_CONTENT_TYPES:
            return ext_ct

    if sniffed.startswith("image/") or sniffed in ALLOWED_CONTENT_TYPES:
        return sniffed
    if ext_ct in ALLOWED_CONTENT_TYPES:
        return ext_ct

    raise HTTPException(400, f"Invalid file type: {sniffed or 'unknown'}")


def validate_image_bytes(image_bytes: bytes) -> str:
    """Validate image/document bytes and return content type."""
    if not image_bytes:
        raise HTTPException(400, "Empty file")
    
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes")
    
    try:
        import magic
        content_type = magic.from_buffer(image_bytes, mime=True)
        if not content_type or not content_type.startswith((
            "image/", "application/pdf",
            "application/vnd.openxmlformats",
            "application/vnd.ms-",
            "application/msword",
            "application/vnd.ms-excel",
            "application/vnd.ms-powerpoint",
        )):
            raise HTTPException(400, f"Invalid file type: {content_type}")
        return content_type
    except ImportError:
        log.warning("python-magic not installed, skipping file type validation")
        return "image/png"
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid file: cannot determine type")


def validate_image_safety(image_bytes: bytes) -> None:
    """Check for embedded malicious content in image."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()  # Verify image integrity
    except Exception as e:
        raise HTTPException(400, f"Invalid or corrupted image: {e}")


def convert_pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> List[bytes]:
    """Convert PDF pages to PNG images using pdf2image (poppler) or pypdf fallback."""
    try:
        from pdf2image import convert_from_bytes
        pages = convert_from_bytes(pdf_bytes, dpi=dpi, fmt="png")
        result = []
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            result.append(buf.getvalue())
        return result
    except ImportError:
        # pdf2image not available, try pypdf for text extraction
        try:
            import pypdf
            from PIL import Image, ImageDraw, ImageFont
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            images: List[bytes] = []
            for page in reader.pages:
                text = page.extract_text() or "(no text extracted)"
                img = Image.new("RGB", (800, 1100), (255, 255, 255))
                draw = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
                except OSError:
                    font = ImageFont.load_default()
                y = 20
                for line in text.split("\n"):
                    if y > 1060:
                        break
                    draw.text((20, y), line[:100], fill=(20, 20, 20), font=font)
                    y += 22
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                images.append(buf.getvalue())
            return images
        except ImportError:
            raise HTTPException(400, "PDF processing requires pdf2image/pypdf. Install: pip install pdf2image pypdf && apt install poppler-utils")
        except Exception as exc:
            raise HTTPException(400, f"PDF conversion failed: {exc}")
    except Exception as exc:
        raise HTTPException(400, f"PDF conversion failed: {exc}")


# ── Prompt templates ──────────────────────────────────────────────────────────

_PROMPTS: Dict[str, str] = {
    "document": "Convert this document to clean Markdown. Preserve headings, paragraphs, lists, and tables.",
    "ocr": "Read all text in this image accurately. Return plain text only.",
    "free": "OCR this image. Return only the text, no commentary.",
    "figure": "Parse the figure and explain all visible labels, axes, and text.",
    "describe": "Describe this image in comprehensive detail.",
    "freeform": "OCR this image.",  # overridden by custom_prompt
}


def mode_prompt(mode: str, custom_prompt: str = "", find_term: str = "") -> str:
    if mode == "find":
        return f"Locate and return the text around: {find_term.strip() or 'Total'}"
    if mode == "freeform":
        return custom_prompt.strip() or _PROMPTS["freeform"]
    return _PROMPTS.get(mode, _PROMPTS["document"])


# ── Language detection ───────────────────────────────────────────────────────


def detect_languages(image_bytes: bytes, top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Detect document languages using Tesseract's OSD.
    Returns up to `top_k` languages with confidence scores.
    Falls back to single-lang detection if OSD fails.
    """
    try:
        import pytesseract
        from PIL import Image
        import io as _io

        img = Image.open(_io.BytesIO(image_bytes))

        # Try OSD first (language + orientation detection)
        osd_data = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
        lang_codes = osd_data.get("lang", "eng")

        # OSD may return space-separated lang codes
        results = []
        seen = set()
        for lang_code in lang_codes.split():
            if lang_code not in seen:
                seen.add(lang_code)
                results.append({"lang": lang_code, "confidence": 1.0})

        if results:
            return results[:top_k]

        # Fallback: single language detection
        lang = pytesseract.image_to_string(img, config="--psm 0").strip().split("\n")[0]
        return [{"lang": lang or "eng", "confidence": 0.8}]

    except Exception as exc:
        log.warning("Language detection failed: %s", exc)
        return [{"lang": "eng", "confidence": 0.0}]


# ── Markdown output formatting ──────────────────────────────────────────────


def improve_markdown_output(text: str) -> str:
    """
    Post-process OCR text to produce cleaner Markdown.
    Handles headings, lists, tables, and code blocks.
    """
    if not text.strip():
        return text

    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Detect heading patterns (ALL CAPS or Title Case line followed by separator)
        if (
            stripped
            and len(stripped) < 100
            and stripped.upper() == stripped
            and not stripped.startswith("|")
            and not stripped.startswith("-")
            and not stripped.startswith("#")
            and any(c.isalpha() for c in stripped)
            and len(stripped.split()) <= 12
        ):
            # Check if next line is a separator
            if i + 1 < len(lines) and lines[i + 1].strip().startswith(("=", "-", "─")):
                result.append(f"## {stripped.title()}")
                result.append("")
                i += 2
                continue

        # Detect markdown table structure
        if stripped.startswith("|") and "|" in stripped[1:]:
            table_lines = [stripped]
            while i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
                i += 1
                table_lines.append(lines[i].strip())

            # Ensure proper Markdown table alignment
            if len(table_lines) >= 2:
                result.extend(table_lines)
            else:
                result.extend(table_lines)
            result.append("")
            i += 1
            continue

        # Detect numbered lists
        if stripped and len(stripped) > 2:
            import re
            m = re.match(r"^(\d+)[.\)]\s+(.*)", stripped)
            if m:
                result.append(f"{m.group(1)}. {m.group(2)}")
                i += 1
                continue

        # Detect bullet points
        if stripped.startswith(("- ", "• ", "· ")):
            result.append(f"- {stripped[2:]}")
            i += 1
            continue

        # Detect code blocks
        if stripped.startswith("```"):
            result.append(stripped)
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                result.append(lines[i])
                i += 1
            if i < len(lines):
                result.append(lines[i])
            i += 1
            continue

        result.append(line)
        i += 1

    return "\n".join(result)


# ── Structured JSON output ──────────────────────────────────────────────────


def to_structured_json(
    text: str,
    confidence: float,
    backend: str,
    mode: str,
    duration_ms: int,
    filename: str,
    pages: int = 1,
    languages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Convert raw OCR text into a structured JSON object with metadata.
    """
    return {
        "text": text,
        "metadata": {
            "backend": backend,
            "mode": mode,
            "confidence": confidence,
            "duration_ms": duration_ms,
            "filename": filename,
            "pages": pages,
            "languages": languages or [],
            "word_count": len(text.split()),
            "char_count": len(text),
        },
    }


# ── OpenAI-compatible message builder ────────────────────────────────────────


def openai_messages(image_bytes: bytes, prompt: str) -> List[Dict[str, Any]]:
    b64 = base64.b64encode(image_bytes).decode()
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }]


# ── Single-backend runners ────────────────────────────────────────────────────


async def _run_openai(backend: BackendConfig, image_bytes: bytes, prompt: str) -> str:
    # ── Security: Validate backend URL ────────────────────────────────────
    if backend.base_url and not validate_backend_url(backend.base_url):
        raise HTTPException(400, f"Backend URL blocked: {backend.base_url}")
    
    url = backend.base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {backend.api_key}"} if backend.api_key else {}
    payload = {
        "model": backend.model or backend.id,
        "messages": openai_messages(image_bytes, prompt),
        "temperature": 0,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=backend.timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )


async def _run_deepseek_webui(
    backend: BackendConfig,
    image_bytes: bytes,
    filename: str,
    content_type: str,
    mode: str,
    find_term: str,
    custom_prompt: str,
) -> str:
    # ── Security: Validate backend URL ────────────────────────────────────
    if backend.base_url and not validate_backend_url(backend.base_url):
        raise HTTPException(400, f"Backend URL blocked: {backend.base_url}")
    
    url = backend.base_url.rstrip("/") + "/ocr"
    files = {"file": (filename, image_bytes, content_type)}
    form = {
        "prompt_type": mode,
        "find_term": find_term,
        "custom_prompt": custom_prompt,
        "grounding": "true" if mode in {"document", "find"} else "false",
    }
    async with httpx.AsyncClient(timeout=backend.timeout) as client:
        r = await client.post(url, files=files, data=form)
        r.raise_for_status()
        data = r.json()
        return data.get("text") or data.get("result") or json.dumps(data, ensure_ascii=False, indent=2)


def _run_tesseract(image_bytes: bytes) -> str:
    from app.fallback import tesseract_ocr
    return tesseract_ocr(image_bytes)


def _run_easyocr(image_bytes: bytes) -> str:
    from app.fallback import easyocr_ocr
    return easyocr_ocr(image_bytes, langs=settings.EASYOCR_LANGS)


# Async wrappers to avoid blocking the event loop
async def _run_tesseract_async(image_bytes: bytes) -> str:
    return await asyncio.to_thread(_run_tesseract, image_bytes)


async def _run_easyocr_async(image_bytes: bytes) -> str:
    return await asyncio.to_thread(_run_easyocr, image_bytes)


# ── Main dispatch ─────────────────────────────────────────────────────────────


async def _process_image_with_chain(
    image_bytes: bytes,
    filename: str,
    content_type: str,
    prompt: str,
    chain: List[BackendConfig],
    mode: str,
    find_term: str,
    custom_prompt: str,
    original_backend_id: str,
) -> Dict[str, Any]:
    """Process a single image through the backend chain. Returns text + confidence."""
    last_error: Optional[Exception] = None

    for backend in chain:
        t0 = time.perf_counter()
        try:
            if backend.kind == "openai_compatible":
                text = await _run_openai(backend, image_bytes, prompt)
                confidence = 0.0  # VLM backends don't provide word-level confidence
            elif backend.kind == "deepseek_webui":
                text = await _run_deepseek_webui(
                    backend, image_bytes, filename, content_type,
                    mode, find_term, custom_prompt,
                )
                confidence = 0.0
            elif backend.kind == "tesseract":
                from app.fallback import tesseract_ocr_with_confidence
                result = await asyncio.to_thread(tesseract_ocr_with_confidence, image_bytes)
                text = result["text"]
                confidence = result["confidence"]
            elif backend.kind == "easyocr":
                from app.fallback import easyocr_ocr_with_confidence
                from app.config import settings
                result = await asyncio.to_thread(
                    easyocr_ocr_with_confidence, image_bytes, settings.EASYOCR_LANGS
                )
                text = result["text"]
                confidence = result["confidence"]
            elif backend.kind == "glm_engine":
                from app.glm_ocr_backend import ocr_image as glm_ocr_image
                text = await glm_ocr_image(image_bytes, prompt=prompt, use_layout=True)
                confidence = 0.0
            else:
                raise ValueError(f"Unsupported backend kind: {backend.kind!r}")

            duration_ms = int((time.perf_counter() - t0) * 1000)
            job_id = await save_job(
                backend_id=backend.id, mode=mode, filename=filename,
                text_result=text, error=None, duration_ms=duration_ms,
                meta={"attempted_backend": original_backend_id, "used_backend": backend.id},
            )
            return {
                "text": text,
                "confidence": confidence,
                "backend": backend,
                "duration_ms": duration_ms,
                "fallback": backend.id != original_backend_id,
            }
        except (httpx.HTTPStatusError, httpx.HTTPError, RuntimeError, Exception) as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            last_error = exc
            log.warning("Backend %r failed (%dms): %s", backend.id, duration_ms, exc)
            await save_job(
                backend_id=backend.id, mode=mode, filename=filename,
                text_result=None, error=str(exc), duration_ms=duration_ms,
            )

    code = 500
    if isinstance(last_error, httpx.HTTPStatusError):
        code = last_error.response.status_code
    raise HTTPException(code, f"All OCR backends failed. Last error: {last_error}")


async def run_ocr(
    image_bytes: bytes,
    filename: str,
    content_type: str,
    backend_id: str,
    mode: str = "document",
    find_term: str = "",
    custom_prompt: str = "",
    auto_fallback: bool = True,
    preprocess: bool = True,
    output_format: str = "text",
) -> Dict[str, Any]:
    """
    Run OCR on image_bytes using the named backend.
    If auto_fallback=True and the backend fails, retries down the priority chain.
    Supports images, PDFs, DOCX, PPTX, XLSX.
    output_format: "text", "markdown", or "json".
    """
    # ── Input validation ──────────────────────────────────────────────────
    if not image_bytes:
        raise HTTPException(400, "Empty file uploaded")

    # Validate content type if not already validated
    if content_type not in ALLOWED_CONTENT_TYPES:
        content_type = validate_image_bytes(image_bytes)

    # ── Text files: no OCR needed, return content directly ─────────────────
    if content_type in TEXT_CONTENT_TYPES:
        try:
            direct_text = image_bytes.decode("utf-8").strip()
        except Exception:
            direct_text = image_bytes.decode("latin-1", errors="replace").strip()
        duration_ms = 0
        job_id = await save_job(
            backend_id=backend_id, mode=mode, filename=filename,
            text_result=direct_text, error=None, duration_ms=duration_ms,
            meta={"source_format": "text", "used_backend": "direct-text"},
        )
        out = {
            "job_id": job_id,
            "backend": {
                "id": backend_id, "kind": "text", "label": "Direct text",
                "model": None, "enabled": True, "priority": 0,
                "base_url": None, "api_key_env": None,
            },
            "model": "",
            "mode": mode,
            "text": direct_text,
            "confidence": 100.0,
            "fallback": False,
            "duration_ms": duration_ms,
            "languages": [],
            "source_format": "text",
            "pages": 1,
        }
        if output_format == "markdown":
            out["text"] = improve_markdown_output(direct_text)
        if output_format == "json":
            structured = to_structured_json(
                text=out["text"], confidence=100.0, backend=backend_id, mode=mode,
                duration_ms=duration_ms, filename=filename, pages=1, languages=[],
            )
            out["structured"] = structured
            out["text"] = json.dumps(structured, ensure_ascii=False, indent=2)
        return out

    # Build ordered attempt list (before PDF processing needs it)
    primary = get_backend(backend_id)
    if primary is None:
        raise HTTPException(404, f"Unknown or disabled backend: {backend_id!r}")

    chain = [primary]
    if auto_fallback:
        others = sorted(
            [b for b in BACKENDS if b.enabled and b.id != backend_id],
            key=lambda b: b.priority,
        )

        # Ensure Tesseract is always last if enabled
        if settings.TESSERACT_ENABLED:
            tesseract_backend = get_backend("tesseract")
            if tesseract_backend and tesseract_backend not in others:
                others.append(tesseract_backend)
            # Remove any duplicates
            seen = set()
            others = [b for b in others if not (b.id in seen or seen.add(b.id))]

        chain.extend(others)

    prompt = mode_prompt(mode, custom_prompt=custom_prompt, find_term=find_term)

    # ── Rasterize SVG images (OCR engines can't read vector graphics) ─────
    if content_type == "image/svg+xml":
        from app.converters import rasterize_svg
        image_bytes = await asyncio.to_thread(rasterize_svg, image_bytes)
        content_type = "image/png"

    # ── Language detection (images only, skip for documents) ──────────────
    languages: List[Dict[str, Any]] = []
    if content_type.startswith("image/"):
        try:
            languages = detect_languages(image_bytes)
            # Adjust prompt if language detected
            if languages and languages[0]["lang"] != "eng":
                lang = languages[0]["lang"]
                prompt += f"\n\nPlease recognize text in the following language(s): {lang}"
        except Exception as exc:
            log.warning("Language detection skipped: %s", exc)

    # ── Handle DOCX files ────────────────────────────────────────────────
    if content_type in DOCX_CONTENT_TYPES:
        try:
            from app.converters import docx_to_images
            doc_images = await asyncio.to_thread(docx_to_images, image_bytes)
            if not doc_images:
                raise HTTPException(400, "DOCX has no extractable content")
            return await _process_multi_page(
                doc_images, filename, prompt, chain, mode,
                find_term, custom_prompt, backend_id, preprocess, "DOCX",
                output_format=output_format, languages=languages,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"DOCX processing failed: {exc}")

    # ── Handle PPTX files ────────────────────────────────────────────────
    if content_type in PPTX_CONTENT_TYPES:
        try:
            from app.converters import pptx_to_images
            ppt_images = await asyncio.to_thread(pptx_to_images, image_bytes)
            if not ppt_images:
                raise HTTPException(400, "PPTX has no extractable content")
            return await _process_multi_page(
                ppt_images, filename, prompt, chain, mode,
                find_term, custom_prompt, backend_id, preprocess, "PPTX",
                output_format=output_format, languages=languages,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"PPTX processing failed: {exc}")

    # ── Handle XLSX files ────────────────────────────────────────────────
    if content_type in XLSX_CONTENT_TYPES:
        try:
            from app.converters import xlsx_to_images
            xls_images = await asyncio.to_thread(xlsx_to_images, image_bytes)
            if not xls_images:
                raise HTTPException(400, "XLSX has no extractable content")
            return await _process_multi_page(
                xls_images, filename, prompt, chain, mode,
                find_term, custom_prompt, backend_id, preprocess, "XLSX",
                output_format=output_format, languages=languages,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"XLSX processing failed: {exc}")

    # ── Handle PDF files: convert to images first ─────────────────────────
    if content_type == "application/pdf":
        try:
            pdf_t0 = time.perf_counter()
            pdf_images = convert_pdf_to_images(image_bytes)
            if not pdf_images:
                raise HTTPException(400, "PDF has no extractable pages")
            return await _process_multi_page(
                pdf_images, filename, prompt, chain, mode,
                find_term, custom_prompt, backend_id, preprocess, "PDF",
                output_format=output_format, languages=languages,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"PDF processing failed: {exc}")

    # ── Preprocess image (deskew + enhance) ───────────────────────────────
    if preprocess:
        try:
            from app.preprocess import auto_preprocess
            image_bytes = await asyncio.to_thread(auto_preprocess, image_bytes)
        except Exception as exc:
            log.warning("Preprocessing failed: %s, using original", exc)

    # Safety check for images
    validate_image_safety(image_bytes)

    # ── Process single image ───────────────────────────────────────────────
    async with _ocr_semaphore:
        result = await _process_image_with_chain(
            image_bytes, filename, content_type,
            prompt, chain, mode, find_term, custom_prompt, backend_id
        )

        text = result["text"]
        if output_format == "markdown":
            text = improve_markdown_output(text)

        job_id = await save_job(
            backend_id=result["backend"].id, mode=mode, filename=filename,
            text_result=text, error=None, duration_ms=result["duration_ms"],
            meta={"attempted_backend": backend_id, "used_backend": result["backend"].id},
        )

        out = {
            "job_id": job_id,
            "backend": result["backend"].model_dump(),
            "model": result["backend"].model or result["backend"].id,
            "mode": mode,
            "text": text,
            "confidence": result.get("confidence", 0.0),
            "fallback": result["fallback"],
            "duration_ms": result["duration_ms"],
            "languages": languages,
        }

        if output_format == "json":
            structured = to_structured_json(
                text=text,
                confidence=result.get("confidence", 0.0),
                backend=result["backend"].id,
                mode=mode,
                duration_ms=result["duration_ms"],
                filename=filename,
                languages=languages,
            )
            out["structured"] = structured
            out["text"] = json.dumps(structured, ensure_ascii=False, indent=2)

        return out


async def _process_multi_page(
    page_images: List[bytes],
    filename: str,
    prompt: str,
    chain: List[BackendConfig],
    mode: str,
    find_term: str,
    custom_prompt: str,
    backend_id: str,
    preprocess: bool,
    source_format: str,
    output_format: str = "text",
    languages: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Process multiple page images (PDF, DOCX, PPTX, XLSX)."""
    t0 = time.perf_counter()
    all_text_parts = []
    all_confidences = []

    async with _ocr_semaphore:
        for page_idx, page_bytes in enumerate(page_images):
            if preprocess:
                try:
                    from app.preprocess import auto_preprocess
                    page_bytes = await asyncio.to_thread(auto_preprocess, page_bytes)
                except Exception:
                    pass

            page_result = await _process_image_with_chain(
                page_bytes, f"{filename}_page{page_idx+1}", "image/png",
                prompt, chain, mode, find_term, custom_prompt, backend_id
            )
            all_text_parts.append(f"--- Page {page_idx + 1} ---\n{page_result['text']}")
            all_confidences.append(page_result.get("confidence", 0.0))

    combined_text = "\n\n".join(all_text_parts)
    if output_format == "markdown":
        combined_text = improve_markdown_output(combined_text)
    avg_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
    total_duration_ms = int((time.perf_counter() - t0) * 1000)

    job_id = await save_job(
        backend_id=chain[0].id, mode=mode, filename=filename,
        text_result=combined_text, error=None, duration_ms=total_duration_ms,
        meta={"pages": len(page_images), "attempted_backend": backend_id, "source_format": source_format},
    )

    out = {
        "job_id": job_id,
        "backend": chain[0].model_dump(),
        "model": chain[0].model or chain[0].id,
        "mode": mode,
        "text": combined_text,
        "confidence": round(avg_confidence, 2),
        "fallback": False,
        "duration_ms": total_duration_ms,
        "pages": len(page_images),
        "source_format": source_format,
        "languages": languages or [],
    }

    if output_format == "json":
        structured = to_structured_json(
            text=combined_text,
            confidence=round(avg_confidence, 2),
            backend=chain[0].id,
            mode=mode,
            duration_ms=total_duration_ms,
            filename=filename,
            pages=len(page_images),
            languages=languages or [],
        )
        out["structured"] = structured
        out["text"] = json.dumps(structured, ensure_ascii=False, indent=2)

    return out