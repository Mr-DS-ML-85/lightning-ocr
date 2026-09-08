"""
lightning-ocr · Document format converters
DOCX, PPTX, XLSX → images for OCR processing.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
from typing import List

log = logging.getLogger("lightning_ocr.converters")


def rasterize_svg(svg_bytes: bytes, dpi: int = 200) -> bytes:
    """
    Rasterize an SVG document to PNG bytes for OCR.
    Uses rsvg-convert (librsvg) if available, else raises a clear error.
    """
    try:
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp_in:
            tmp_in.write(svg_bytes)
            in_path = tmp_in.name
        tmp_out = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        out_path = tmp_out.name
        tmp_out.close()
        try:
            subprocess.run(
                ["rsvg-convert", "-d", str(dpi), "-p", str(dpi), "-o", out_path, in_path],
                check=True, capture_output=True, timeout=60,
            )
            with open(out_path, "rb") as f:
                return f.read()
        finally:
            for p in (in_path, out_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    except FileNotFoundError as exc:
        raise RuntimeError("SVG rasterization requires rsvg-convert (librsvg). Install librsvg2-bin.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"SVG rasterization failed: {exc.stderr.decode(errors='replace').strip()}") from exc


def docx_to_images(docx_bytes: bytes, dpi: int = 200) -> List[bytes]:
    """
    Convert DOCX to PNG images (one per page).
    Uses python-docx + Pillow for rendering via LibreOffice if available,
    or falls back to text extraction + image generation.
    """
    try:
        # Try LibreOffice headless conversion (best quality)
        import subprocess
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(docx_bytes)
            tmp_docx = f.name

        tmp_dir = tempfile.mkdtemp()
        try:
            subprocess.run([
                "libreoffice", "--headless", "--convert-to", "png",
                "--outdir", tmp_dir, tmp_docx
            ], capture_output=True, timeout=30, check=True)

            images = []
            for fname in sorted(os.listdir(tmp_dir)):
                if fname.endswith(".png"):
                    with open(os.path.join(tmp_dir, fname), "rb") as f:
                        images.append(f.read())
            if images:
                return images
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        finally:
            os.unlink(tmp_docx)
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass

    # Fallback: extract text and render as images
    return _text_to_images(_extract_docx_text(docx_bytes), "DOCX")


def pptx_to_images(pptx_bytes: bytes, dpi: int = 200) -> List[bytes]:
    """
    Convert PPTX slides to PNG images.
    Uses python-pptx for text extraction, renders as images.
    """
    return _text_to_images(_extract_pptx_text(pptx_bytes), "PPTX")


def xlsx_to_images(xlsx_bytes: bytes, dpi: int = 200) -> List[bytes]:
    """
    Convert XLSX sheets to PNG images (one per sheet).
    Renders table data as text-based images.
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        images = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            lines = [f"=== Sheet: {sheet_name} ==="]
            for row in ws.iter_rows(values_only=True):
                row_text = "\t".join(str(c) if c is not None else "" for c in row)
                if row_text.strip():
                    lines.append(row_text)
            images.extend(_text_to_images("\n".join(lines), f"XLSX:{sheet_name}"))

        wb.close()
        return images
    except ImportError:
        log.warning("openpyxl not installed, cannot convert XLSX")
        return _text_to_images("[XLSX conversion requires openpyxl]", "XLSX")
    except Exception as exc:
        log.error("XLSX conversion failed: %s", exc)
        return _text_to_images(f"[XLSX conversion error: {exc}]", "XLSX")


def docx_to_markdown(docx_bytes: bytes) -> str:
    """Extract DOCX as clean Markdown (headings, lists, tables)."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(docx_bytes))
        parts: List[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name.lower()
            if style.startswith("heading"):
                level = min(int(style.replace("heading", "").strip() or "1"), 6)
                parts.append(f"{'#' * level} {text}")
            elif para.style.name and "list" in style.lower():
                parts.append(f"- {text}")
            else:
                parts.append(text)
        for table in doc.tables:
            rows = []
            for row in table.rows:
                rows.append("| " + " | ".join(cell.text.strip() for cell in row.cells) + " |")
            if rows:
                if len(rows) > 1:
                    rows.insert(1, "| " + " | ".join(["---"] * len(table.columns)) + " |")
                parts.append("\n".join(rows))
        return "\n\n".join(p for p in parts if p.strip())
    except ImportError:
        log.warning("python-docx not installed")
        return "[DOCX conversion requires python-docx]"
    except Exception as exc:
        return f"[DOCX extraction error: {exc}]"


def pptx_to_markdown(pptx_bytes: bytes) -> str:
    """Extract PPTX as clean Markdown (one ## slide per slide)."""
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(pptx_bytes))
        parts: List[str] = []
        for i, slide in enumerate(prs.slides, 1):
            slide_lines = ["", f"## Slide {i}", ""]
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
                    text = shape.text_frame.text.strip()
                    for line in text.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        slide_lines.append(f"- {line}" if not line.startswith(("#", "-", "|")) else line)
                elif getattr(shape, "has_table", False):
                    tbl = shape.table
                    rows = ["| " + " | ".join(c.text.strip() for c in row.cells) + " |" for row in tbl.rows]
                    if rows:
                        slide_lines.append(rows[0])
                        slide_lines.append("| " + " | ".join(["---"] * len(tbl.columns)) + " |")
                        slide_lines.extend(rows[1:])
            parts.append("\n".join(slide_lines))
        return "\n".join(parts).strip()
    except ImportError:
        log.warning("python-pptx not installed")
        return "[PPTX conversion requires python-pptx]"
    except Exception as exc:
        return f"[PPTX extraction error: {exc}]"


def xlsx_to_markdown(xlsx_bytes: bytes) -> str:
    """Extract XLSX as Markdown tables (one ## sheet per sheet)."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        parts: List[str] = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                vals = ["" if c is None else str(c) for c in row]
                if any(v.strip() for v in vals):
                    rows.append("| " + " | ".join(vals) + " |")
            if rows:
                if len(rows) > 1:
                    rows.insert(1, "| " + " | ".join(["---"] * (len(rows[0].split("|")) - 2)) + " |")
                parts.append(f"## Sheet: {sheet_name}\n\n" + "\n".join(rows))
        wb.close()
        return "\n\n".join(parts).strip()
    except ImportError:
        log.warning("openpyxl not installed, cannot convert XLSX")
        return "[XLSX conversion requires openpyxl]"
    except Exception as exc:
        return f"[XLSX conversion error: {exc}]"


def _extract_docx_text(docx_bytes: bytes) -> str:
    """Extract text from DOCX using python-docx."""
    try:
        from docx import Document
        doc = Document(io.BytesIO(docx_bytes))
        paragraphs = []
        for para in doc.paragraphs:
            if para.text.strip():
                prefix = "#" * min(para.style.name.count("Heading"), 6) + " " if "Heading" in para.style.name else ""
                paragraphs.append(prefix + para.text)

        # Extract tables
        for table in doc.tables:
            rows = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                # Add header separator
                if len(rows) > 1:
                    sep = "| " + " | ".join(["---"] * len(table.rows[0].cells)) + " |"
                    rows.insert(1, sep)
                paragraphs.append("\n".join(rows))

        return "\n\n".join(paragraphs)
    except ImportError:
        log.warning("python-docx not installed")
        return "[DOCX conversion requires python-docx]"
    except Exception as exc:
        return f"[DOCX extraction error: {exc}]"


def _extract_pptx_text(pptx_bytes: bytes) -> str:
    """Extract text from PPTX using python-pptx."""
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(pptx_bytes))
        slides_text = []

        for i, slide in enumerate(prs.slides, 1):
            slide_lines = [f"--- Slide {i} ---"]
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_lines.append(shape.text.strip())
            slides_text.append("\n".join(slide_lines))

        return "\n\n".join(slides_text)
    except ImportError:
        log.warning("python-pptx not installed")
        return "[PPTX conversion requires python-pptx]"
    except Exception as exc:
        return f"[PPTX extraction error: {exc}]"


def _text_to_images(text: str, label: str = "Document") -> List[bytes]:
    """Render text as PNG images (one page per ~50 lines)."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        lines = text.split("\n")
        page_height = 1100
        lines_per_page = 45
        margin = 40
        y_step = 22

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
            font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        except OSError:
            font = ImageFont.load_default()
            font_bold = font

        images = []
        for page_start in range(0, max(len(lines), 1), lines_per_page):
            page_lines = lines[page_start:page_start + lines_per_page]
            img = Image.new("RGB", (800, page_height), (255, 255, 255))
            draw = ImageDraw.Draw(img)

            y = margin
            for line in page_lines:
                if y > page_height - margin:
                    break
                use_font = font_bold if line.startswith("#") else font
                draw.text((margin, y), line[:95], fill=(20, 20, 20), font=use_font)
                y += y_step

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append(buf.getvalue())

        return images if images else [_text_to_images_single(text, label)]
    except Exception:
        return [_text_to_images_single(text, label)]


def _text_to_images_single(text: str, label: str) -> bytes:
    """Fallback: render any text as a single image."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (800, 1100), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except OSError:
            font = ImageFont.load_default()

        y = 40
        for line in text.split("\n"):
            if y > 1060:
                break
            draw.text((40, y), line[:95], fill=(20, 20, 20), font=font)
            y += 22

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Absolute fallback
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (800, 200), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.text((40, 80), f"[{label}] Text extraction failed", fill=(200, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
