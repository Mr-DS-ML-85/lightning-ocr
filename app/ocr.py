"""
lightning-ocr · Core OCR dispatch
Tries primary backend → auto-falls back through priority chain → Tesseract last resort.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
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
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}


def validate_image_bytes(image_bytes: bytes) -> str:
    """Validate image bytes and return content type."""
    if not image_bytes:
        raise HTTPException(400, "Empty file")
    
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes")
    
    try:
        import magic
        content_type = magic.from_buffer(image_bytes, mime=True)
        if not content_type or not content_type.startswith(("image/", "application/pdf")):
            raise HTTPException(400, f"Invalid file type: {content_type}")
        return content_type
    except ImportError:
        # magic not installed, skip validation (but log warning)
        log.warning("python-magic not installed, skipping file type validation")
        return "image/png"
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
    """Process a single image through the backend chain."""
    last_error: Optional[Exception] = None

    for backend in chain:
        t0 = time.perf_counter()
        try:
            if backend.kind == "openai_compatible":
                text = await _run_openai(backend, image_bytes, prompt)
            elif backend.kind == "deepseek_webui":
                text = await _run_deepseek_webui(
                    backend, image_bytes, filename, content_type,
                    mode, find_term, custom_prompt,
                )
            elif backend.kind == "tesseract":
                text = await _run_tesseract_async(image_bytes)
            elif backend.kind == "easyocr":
                text = await _run_easyocr_async(image_bytes)
            elif backend.kind == "glm_engine":
                from app.glm_ocr_backend import ocr_image as glm_ocr_image
                text = await glm_ocr_image(image_bytes, prompt=prompt, use_layout=True)
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
) -> Dict[str, Any]:
    """
    Run OCR on image_bytes using the named backend.
    If auto_fallback=True and the backend fails, retries down the priority chain.
    """
    # ── Input validation ──────────────────────────────────────────────────
    if not image_bytes:
        raise HTTPException(400, "Empty file uploaded")

    # Validate content type if not already validated
    if content_type not in ALLOWED_CONTENT_TYPES:
        content_type = validate_image_bytes(image_bytes)

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

    # ── Handle PDF files: convert to images first ─────────────────────────
    if content_type == "application/pdf":
        try:
            pdf_t0 = time.perf_counter()
            pdf_images = convert_pdf_to_images(image_bytes)
            if not pdf_images:
                raise HTTPException(400, "PDF has no extractable pages")

            # Process each page and concatenate results
            all_text_parts = []
            async with _ocr_semaphore:
                for page_idx, page_bytes in enumerate(pdf_images):
                    page_result = await _process_image_with_chain(
                        page_bytes, f"{filename}_page{page_idx+1}", "image/png",
                        prompt, chain, mode, find_term, custom_prompt, backend_id
                    )
                    all_text_parts.append(f"--- Page {page_idx + 1} ---\n{page_result['text']}")

            # Combine all pages
            combined_text = "\n\n".join(all_text_parts)
            total_duration_ms = int((time.perf_counter() - pdf_t0) * 1000)
            job_id = await save_job(
                backend_id=chain[0].id, mode=mode, filename=filename,
                text_result=combined_text, error=None, duration_ms=total_duration_ms,
                meta={"pages": len(pdf_images), "attempted_backend": backend_id},
            )
            return {
                "job_id": job_id,
                "backend": chain[0].model_dump(),
                "model": chain[0].model or chain[0].id,
                "mode": mode,
                "text": combined_text,
                "fallback": False,
                "duration_ms": total_duration_ms,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"PDF processing failed: {exc}")

    # Safety check for images
    validate_image_safety(image_bytes)

    # ── Process single image ───────────────────────────────────────────────
    async with _ocr_semaphore:
        result = await _process_image_with_chain(
            image_bytes, filename, content_type,
            prompt, chain, mode, find_term, custom_prompt, backend_id
        )

        job_id = await save_job(
            backend_id=result["backend"].id, mode=mode, filename=filename,
            text_result=result["text"], error=None, duration_ms=result["duration_ms"],
            meta={"attempted_backend": backend_id, "used_backend": result["backend"].id},
        )

        return {
            "job_id": job_id,
            "backend": result["backend"].model_dump(),
            "model": result["backend"].model or result["backend"].id,
            "mode": mode,
            "text": result["text"],
            "fallback": result["fallback"],
            "duration_ms": result["duration_ms"],
        }