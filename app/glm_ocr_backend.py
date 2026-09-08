"""
GLM-OCR Backend for Lightning-OCR
===================================
Integrated inference engine using transformers + PyMuPDF layout detection.
Drop-in replacement for the llama.cpp proxy.

Usage:
    from app.glm_ocr_backend import GLM_OCR_Engine
    engine = GLM_OCR_Engine()
    text = engine.ocr_page(image_bytes, prompt="Text Recognition:")

Features:
  - Pure transformers inference (no llama.cpp, no vLLM)
  - PyMuPDF layout detection (text blocks, images, structure)
  - Optional PaddleX PP-DocLayout-V3 when PaddlePaddle is available
  - Parallel page processing via ThreadPoolExecutor
  - Batch region recognition within pages
"""

from __future__ import annotations

import io
import os
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from PIL import Image

log = logging.getLogger("lightning_ocr.glm_ocr")

MODEL_PATH = os.environ.get(
    "GLM_OCR_MODEL_PATH",
    os.path.expanduser("~/models/GLM-OCR"),
)
MAX_WORKERS = int(os.environ.get("GLM_OCR_WORKERS", "4"))
DEVICE = os.environ.get("GLM_OCR_DEVICE", "cuda")
DTYPE = os.environ.get("GLM_OCR_DTYPE", "float16")


# ── Safe env loader ───────────────────────────────────────────────────────────

def _resolve_venv_lib() -> Optional[str]:
    """Find the cu13 lib directory for NVRTC if in a venv."""
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        return None
    candidate = os.path.join(venv, "lib/python3.11/site-packages/nvidia/cu13/lib")
    if os.path.isdir(candidate):
        return candidate
    return None


def _ensure_ld_path() -> None:
    """Inject NVRTC path if missing (for CUDA JIT kernels)."""
    cu13 = _resolve_venv_lib()
    if cu13 and cu13 not in os.environ.get("LD_LIBRARY_PATH", ""):
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = f"{cu13}:{existing}" if existing else cu13
        log.info("LD_LIBRARY_PATH patched: %s", cu13)


# ── Layout analysis ───────────────────────────────────────────────────────────

@dataclass
class Region:
    """A document region detected by layout analysis."""
    bbox: Tuple[float, float, float, float]  # x0, y0, x1, y1
    type: str  # "text", "title", "table", "figure", "list", "header", "footer", "page_number"
    text: str = ""
    confidence: float = 0.0


def _layout_pymupdf(page_image: Image.Image, page_pixmap=None) -> List[Region]:
    """
    Detect layout regions using PyMuPDF's embedded text detection.
    Works best when the PDF has selectable text layer.
    """
    regions: List[Region] = []
    if page_pixmap is None:
        return regions

    try:
        page = page_pixmap  # already a fitz.Page
        blocks = page.get_text("dict")["blocks"]

        page_h = page.rect.height
        for b in blocks:
            x0, y0, x2, y2 = b["bbox"]
            # Normalise bbox (fitz uses float coordinates)
            # Determine region type
            if b["type"] == 1:
                rtype = "figure"
            else:
                # Check if it's a header/footer based on vertical position
                rel_y = y0 / page_h if page_h > 0 else 0.5
                if rel_y < 0.08:
                    rtype = "header"
                elif rel_y > 0.92:
                    rtype = "footer"
                else:
                    # Check if it looks like a title (short, bold, top)
                    lines = b.get("lines", [])
                    if lines and len(lines) <= 2:
                        spans = lines[0].get("spans", [])
                        if spans and (spans[0].get("size", 0) > 14 or spans[0].get("flags", 0) & 2):
                            if rel_y < 0.15:
                                rtype = "title"
                            else:
                                rtype = "text"
                        else:
                            rtype = "text"
                    else:
                        rtype = "text"

                # Extract text from this block
                block_text = ""
                for line in lines:
                    for span in line.get("spans", []):
                        block_text += span.get("text", "")
                    block_text += "\n"
                if block_text.strip():
                    regions.append(Region(
                        bbox=(x0, y0, x2, y2),
                        type=rtype,
                        text=block_text.strip(),
                        confidence=1.0,
                    ))
                    continue

            regions.append(Region(
                bbox=(x0, y0, x2, y2),
                type=rtype,
                confidence=1.0,
            ))
    except Exception as e:
        log.warning("PyMuPDF layout detection failed: %s", e)

    return regions


def _layout_paddlex(page_image: Image.Image) -> List[Region]:
    """
    Detect layout using PaddleX PP-DocLayout-V3.
    Falls back gracefully if PaddlePaddle is not available.
    """
    try:
        import paddle
        from paddlex import create_model
        model = create_model("PP-DocLayout-V3")
        result = model.predict(page_image)
        regions: List[Region] = []
        for item in result:
            boxes = item.get("boxes", [])
            for box in boxes:
                regions.append(Region(
                    bbox=tuple(box["bbox"]),
                    type=box.get("label", "text"),
                    confidence=box.get("score", 0.0),
                ))
        if regions:
            log.info("PaddleX layout: %d regions detected", len(regions))
            return regions
    except ImportError:
        log.info("PaddlePaddle not installed, using PyMuPDF layout fallback")
    except Exception as e:
        log.warning("PaddleX layout failed: %s, using PyMuPDF fallback", e)
    return []


def detect_layout(page_image: Image.Image, _page_pixmap=None) -> List[Region]:
    """Detect document layout using best available method."""
    # Try PaddleX first if available
    regions = _layout_paddlex(page_image)
    if regions:
        return regions
    # Fall back to PyMuPDF (works with text-layer PDFs) - not used here since we pre-compute layouts
    return [Region(bbox=(0, 0, page_image.width, page_image.height), type="text", confidence=1.0)]


# ── GLM-OCR Engine ────────────────────────────────────────────────────────────


class GLM_OCR_Engine:
    """
    GLM-OCR inference engine using HuggingFace transformers.
    Single model instance shared across all calls.
    """

    def __init__(self, model_path: str = MODEL_PATH):
        _ensure_ld_path()
        self.model_path = model_path
        self._model = None
        self._processor = None
        self._device = None
        self._dtype = None

    # ── Model lifecycle ────────────────────────────────────────────────────

    def load(self, device: str = DEVICE, dtype: str = DTYPE) -> None:
        """Load the model onto the specified device."""
        import torch
        import transformers

        if self._model is not None:
            return  # Already loaded

        t0 = time.perf_counter()
        log.info("Loading GLM-OCR from %s on %s (%s)...", self.model_path, device, dtype)

        dt = getattr(torch, dtype, torch.float16)
        self._device = device if device == "cuda" and torch.cuda.is_available() else "cpu"
        self._dtype = dt

        kw = {
            "dtype": dt,
            "trust_remote_code": True,
        }
        if self._device == "cuda":
            kw["device_map"] = "auto"

        self._processor = transformers.AutoProcessor.from_pretrained(
            self.model_path, trust_remote_code=True
        )
        self._model = transformers.AutoModelForImageTextToText.from_pretrained(
            self.model_path, **kw
        )
        self._model.eval()

        n_params = sum(p.numel() for p in self._model.parameters() if p.requires_grad)
        elapsed = time.perf_counter() - t0
        log.info(
            "Loaded: %.1fM params on %s (%.1fs)",
            n_params / 1e6, self._device, elapsed,
        )

    def unload(self) -> None:
        """Free GPU memory."""
        import torch
        if self._model is not None:
            del self._model
            self._model = None
        if self._processor is not None:
            del self._processor
            self._processor = None
        if self._device == "cuda":
            torch.cuda.empty_cache()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    # ── Core OCR ───────────────────────────────────────────────────────────

    def ocr(self, image: Image.Image, prompt: str = "Text Recognition:") -> str:
        """Run GLM-OCR on a single image region."""
        import torch

        if self._model is None:
            self.load()

        inputs = self._processor(
            images=image,
            text=f"<|begin_of_image|><|image|><|end_of_image|>{prompt}",
            return_tensors="pt",
        ).to(self._model.device)

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=2048,
                do_sample=False,  # Deterministic for OCR
                num_beams=1,
                use_cache=True,
            )

        text = self._processor.decode(outputs[0], skip_special_tokens=True)
        # Strip multimodal prefix tokens and prompt from output
        img_token_str = "<|image|>"
        begin_img = "<|begin_of_image|>"
        end_img = "<|end_of_image|>"
        # Remove all image placeholder tokens
        while img_token_str in text:
            text = text.replace(img_token_str, "")
        # Remove begin/end image markers
        text = text.replace(begin_img, "").replace(end_img, "")
        # Strip the prompt prefix if it appears in output
        if text.startswith(prompt):
            text = text[len(prompt):]
        text = text.strip()
        # If the model echoed the prompt elsewhere, remove it
        if prompt in text:
            text = text.replace(prompt, "").strip()
        # Cleanup GPU tensors to avoid OOM on sequential pages
        import gc
        del outputs, inputs
        gc.collect()
        torch.cuda.empty_cache()
        return text.strip()

    # ── Page-level OCR ─────────────────────────────────────────────────────

    def ocr_page(
        self,
        image: Image.Image,
        regions: Optional[List[Region]] = None,
        prompt: str = "Text Recognition:",
        use_layout: bool = True,
    ) -> str:
        """
        OCR a single page image.
        If use_layout=True and no regions provided, detect them automatically.
        If regions are provided (pre-computed layout), use them directly.
        Otherwise, OCR the full page as one.
        """
        if not use_layout:
            return self.ocr(image, prompt=prompt)

        # Use provided regions or detect them
        if regions is None:
            regions = detect_layout(image, None)

        if len(regions) <= 1:
            # Single region — just OCR the full page
            return self.ocr(image, prompt=prompt)

        # OCR each region and reconstruct with structure
        parts: List[str] = []
        for region in regions:
            x0, y0, x2, y2 = region.bbox
            # Clip to image bounds
            x0 = max(0, int(x0))
            y0 = max(0, int(y0))
            x2 = min(image.width, int(x2))
            y2 = min(image.height, int(y2))

            if x2 <= x0 or y2 <= y0 or (x2 - x0) * (y2 - y0) < 500:
                continue  # Skip tiny regions

            # If PyMuPDF already extracted text, use it directly
            if region.text:
                parts.append(region.text)
                continue

            # Crop and OCR
            try:
                crop = image.crop((x0, y0, x2, y2))
                text = self.ocr(crop, prompt=prompt)
                if text.strip():
                    # Add structural markers
                    if region.type == "title":
                        parts.append(f"\n## {text}\n")
                    elif region.type == "header":
                        parts.append(f"\n[{text}]")
                    elif region.type == "footer":
                        parts.append(f"\n({text})")
                    else:
                        parts.append(text)
            except Exception as e:
                log.warning("Region OCR failed (%s): %s", region.type, e)

        return "\n".join(parts)

    # ── PDF processing with parallel workers ───────────────────────────────

    def process_pdf(
        self,
        pdf_path: str,
        output_prefix: str = "",
        prompt: str = "Text Recognition:",
        use_layout: bool = True,
        workers: int = MAX_WORKERS,
    ) -> Dict[str, Any]:
        """
        Process a PDF with parallel page processing.
        Returns dict with text, page_count, total_chars, timing.
        """
        import torch
        from pdf2image import convert_from_path

        t0 = time.perf_counter()
        log.info("Converting PDF: %s", pdf_path)

        # Convert PDF to images using all cores
        images = convert_from_path(pdf_path, dpi=200, fmt="png")
        n_pages = len(images)
        log.info("PDF converted: %d pages", n_pages)

        # Try to get PyMuPDF layout data before closing (pages invalidated after close)
        page_layouts: Dict[int, List[Region]] = {}
        if use_layout:
            try:
                import fitz
                doc = fitz.open(pdf_path)
                for i in range(len(doc)):
                    page = doc[i]
                    blocks = page.get_text("dict")["blocks"]
                    page_h = page.rect.height
                    regions = []
                    for b in blocks:
                        x0, y0, x2, y2 = b["bbox"]
                        if b["type"] == 1:
                            regions.append(Region(bbox=(x0, y0, x2, y2), type="figure", confidence=1.0))
                        else:
                            rel_y = y0 / page_h if page_h > 0 else 0.5
                            rtype = "header" if rel_y < 0.08 else ("footer" if rel_y > 0.92 else "text")
                            block_text = ""
                            for line in b.get("lines", []):
                                for span in line.get("spans", []):
                                    block_text += span.get("text", "")
                            if block_text.strip():
                                rtype = "title" if len(b.get("lines",[])) <= 2 and rel_y < 0.15 else rtype
                                regions.append(Region(bbox=(x0, y0, x2, y2), type=rtype, text=block_text.strip(), confidence=1.0))
                            else:
                                regions.append(Region(bbox=(x0, y0, x2, y2), type=rtype, confidence=1.0))
                    page_layouts[i] = regions
                doc.close()
                log.info("PyMuPDF layout: %d/%d pages have layout data", len(page_layouts), len(images))
            except Exception as e:
                log.warning("PyMuPDF layout extraction failed: %s", e)

        # Process pages - use sequential for GPU-bound model (parallel would OOM)
        # Parallelism is used for CPU-bound prep work (PDF conversion, image loading)
        results: Dict[int, str] = {}
        batch_t0 = time.perf_counter()

        for i in range(n_pages):
            try:
                text = self.ocr_page(
                    images[i],
                    page_layouts.get(i),
                    prompt=prompt,
                    use_layout=use_layout,
                )
                results[i] = text
                log.info("  Page %d/%d: %d chars", i + 1, n_pages, len(text))
            except Exception as e:
                results[i] = f"[Error: {e}]"
                log.error("  Page %d failed: %s", i + 1, e)
            # Clear GPU cache between pages to prevent fragmentation
            if self._device == "cuda":
                import torch
                torch.cuda.empty_cache()

        batch_elapsed = time.perf_counter() - batch_t0

        # Assemble in page order
        all_text = "\n\n".join(
            f"--- Page {i + 1} ---\n{results.get(i, '')}"
            for i in range(n_pages)
        )
        total_chars = len(all_text)
        elapsed = time.perf_counter() - t0

        # Save outputs
        if output_prefix:
            txt_path = f"{output_prefix} - OCR.txt"
            docx_path = f"{output_prefix} - OCR.docx"

            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(all_text)

            try:
                from docx import Document
                from docx.shared import Pt, RGBColor, Inches
                doc = Document()
                for line in all_text.split("\n"):
                    if line.startswith("--- Page"):
                        doc.add_heading(line, level=1)
                    elif line.startswith("## "):
                        doc.add_heading(line[3:], level=2)
                    elif line.strip():
                        doc.add_paragraph(line)
                doc.save(docx_path)
            except Exception as e:
                log.warning("DOCX save failed: %s", e)
                docx_path = None

            log.info("TXT: %s", txt_path)
            if docx_path:
                log.info("DOCX: %s", docx_path)

        info = {
            "text": all_text,
            "page_count": n_pages,
            "total_chars": total_chars,
            "chars_per_page": total_chars // max(n_pages, 1),
            "total_time_s": round(elapsed, 1),
            "time_per_page_s": round(elapsed / max(n_pages, 1), 1),
            "workers": workers,
            "layout": bool(use_layout),
        }
        return info


# ── Singleton ─────────────────────────────────────────────────────────────────

_ENGINE: Optional[GLM_OCR_Engine] = None


def get_engine() -> GLM_OCR_Engine:
    """Get or create the singleton engine instance."""
    global _ENGINE
    if _ENGINE is None:
        # Clean GPU memory from any previous processes first
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        _ENGINE = GLM_OCR_Engine()
        _ENGINE.load()
    return _ENGINE


# ── Async wrapper for Lightning-OCR ───────────────────────────────────────────


async def ocr_image(
    image_bytes: bytes,
    prompt: str = "Text Recognition:",
    use_layout: bool = True,
) -> str:
    """
    OCR an image (as bytes) using the GLM-OCR engine.
    Async wrapper for use with FastAPI/Starlette.
    """
    import asyncio
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    engine = get_engine()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, engine.ocr_page, image, None, prompt, use_layout,
    )


async def ocr_pdf(
    pdf_bytes: bytes,
    output_prefix: str = "",
    prompt: str = "Text Recognition:",
    use_layout: bool = True,
    workers: int = MAX_WORKERS,
) -> Dict[str, Any]:
    """
    OCR a PDF (as bytes) using the GLM-OCR engine.
    Saves to temporary file and processes.
    """
    import asyncio
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name

    try:
        engine = get_engine()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            engine.process_pdf,
            tmp_path, output_prefix, prompt, use_layout, workers,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ── CLI entry point ───────────────────────────────────────────────────────────


def main():
    """CLI entry point for standalone use."""
    import argparse

    parser = argparse.ArgumentParser(description="GLM-OCR Engine")
    parser.add_argument("input", help="PDF or image file to OCR")
    parser.add_argument("--output", "-o", help="Output path prefix", default="")
    parser.add_argument(
        "--prompt", "-p", default="Text Recognition:",
        help="Prompt for the model (default: 'Text Recognition:')",
    )
    parser.add_argument(
        "--no-layout", action="store_true",
        help="Disable layout detection (full-page OCR)",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=MAX_WORKERS,
        help="Number of parallel workers (default: 4)",
    )
    parser.add_argument(
        "--device", "-d", default=DEVICE,
        help="Device: cuda or cpu (default: cuda)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    engine = GLM_OCR_Engine()
    engine.load(device=args.device)

    # Determine if it's a PDF
    is_pdf = args.input.lower().endswith(".pdf")

    if is_pdf:
        info = engine.process_pdf(
            args.input,
            output_prefix=args.output or args.input.replace(".pdf", ""),
            prompt=args.prompt,
            use_layout=not args.no_layout,
            workers=args.workers,
        )

        print(f"\n{'='*55}")
        print(f"  Done: {info['page_count']} pages, {info['total_chars']} chars")
        print(f"  Time: {info['total_time_s']}s ({info['time_per_page_s']}s/page)")
        print(f"  Workers: {info['workers']} | Layout: {info['layout']}")
        print(f"{'='*55}")
        print(f"  TXT: {args.output or args.input.replace('.pdf', '')} - OCR.txt")
        print(f"  DOCX: {args.output or args.input.replace('.pdf', '')} - OCR.docx")
    else:
        # Single image
        image = Image.open(args.input).convert("RGB")
        text = engine.ocr_page(image, use_layout=not args.no_layout)
        print(text)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
