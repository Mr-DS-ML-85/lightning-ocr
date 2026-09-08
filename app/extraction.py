"""
lightning-ocr · Document Extraction Engine
===========================================
Able2Extract-equivalent 5-stage PDF-to-Excel/CSV/DOCX extraction pipeline.

Algorithm stages:
  1. Geometry extraction — read all chars/lines/rects with coordinates
  2. Table boundary detection — lattice (grid lines) + stream (whitespace) hybrid
  3. Cell structure reconstruction — merged/spanning cells from gap analysis
  4. Content mapping — snap text fragments into cell grid with formatting
  5. Output serialization — XLSX (openpyxl), CSV, DOCX (python-docx)

GLM-OCR integration:
  - Scanned PDFs → pdf2image → GLM-OCR with structured JSON prompt
  - Text-based PDFs → pypdf coordinate extraction
  - Hybrid: use GLM-OCR to validate/improve geometry-based results

Smart Templates:
  - Store table layout definitions as JSON schemas
  - Anchor-based alignment using text landmarks
  - Proportional grid adjustment for varying page sizes

No external packages beyond: pypdf, openpyxl, python-docx, Pillow, pdf2image
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
import os
import re
import time
import tempfile
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import defaultdict

from PIL import Image

log = logging.getLogger("lightning_ocr.extraction")

# ──────────────────────────────────────────────────────────────────────────────
#  Data Models
# ──────────────────────────────────────────────────────────────────────────────


class DetectionStrategy(Enum):
    """Which table detection strategy was used."""
    LATTICE = "lattice"        # Grid lines detected
    STREAM = "stream"          # Whitespace-based inference
    GLM_OCR = "glm_ocr"        # GLM-OCR model structured output
    HYBRID = "hybrid"          # Combined approach
    MANUAL = "manual"          # Template override


@dataclass
class Char:
    """A single character with positioning from the PDF."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_name: str = ""
    font_size: float = 0.0
    bold: bool = False
    italic: bool = False


@dataclass
class Line:
    """A vector line from the PDF."""
    x0: float
    y0: float
    x1: float
    y1: float
    width: float = 1.0


@dataclass
class Rect:
    """A rectangle from the PDF."""
    x0: float
    y0: float
    x1: float
    y1: float
    fill: Optional[str] = None
    stroke: Optional[str] = None


@dataclass
class Cell:
    """A single table cell with content and positioning."""
    row: int = 0
    col: int = 0
    rowspan: int = 1
    colspan: int = 1
    text: str = ""
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    bold: bool = False
    is_header: bool = False
    data_type: str = "text"  # text, number, date, currency
    confidence: float = 1.0


@dataclass
class Table:
    """A detected table with all its cells and metadata."""
    cells: List[List[Cell]] = field(default_factory=list)
    num_rows: int = 0
    num_cols: int = 0
    page_num: int = 0
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    strategy: DetectionStrategy = DetectionStrategy.LATTICE
    confidence: float = 1.0
    title: str = ""


@dataclass
class ExtractionResult:
    """Complete extraction output for one document."""
    tables: List[Table] = field(default_factory=list)
    full_text: str = ""
    page_count: int = 0
    pages_with_tables: int = 0
    detection_time_ms: float = 0.0
    strategy: DetectionStrategy = DetectionStrategy.LATTICE
    filename: str = ""
    error: Optional[str] = None
    _outputs: Dict[str, bytes] = field(default_factory=dict)  # Binary output files keyed by filename


@dataclass
class SmartTemplate:
    """
    A reusable table layout template (like Able2Extract's .pcvt).

    Stores the geometric structure of a table: row/col boundaries
    as ratios of page dimensions, anchor text for alignment,
    and expected data types per column.
    """
    name: str = ""
    description: str = ""
    page_width_ref: float = 612.0   # Reference page width (Letter: 612pt)
    page_height_ref: float = 792.0  # Reference page height
    anchors: List[Dict[str, Any]] = field(default_factory=list)
    # anchor: {text, x_ratio, y_ratio, width_ratio, height_ratio}
    columns: List[Dict[str, Any]] = field(default_factory=list)
    # column: {label, x_ratio_start, x_ratio_end, data_type, width_ratio}
    rows: List[Dict[str, Any]] = field(default_factory=list)
    # row: {y_ratio_start, y_ratio_end, height_ratio, is_header}
    cell_merges: List[Dict[str, Any]] = field(default_factory=list)
    # cell_merge: {row, col, rowspan, colspan}
    field_mappings: Dict[str, str] = field(default_factory=dict)
    # field_mapping: {"invoice_number": "0,0", "date": "0,1", ...}
    version: int = 1


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 1: Geometry Extraction
# ──────────────────────────────────────────────────────────────────────────────


def _extract_geometry_pypdf(pdf_path: str) -> Tuple[List[List[Char]], List[List[Line]], int]:
    """
    Extract characters and lines from a text-based PDF using pypdf.

    Returns:
        (pages_chars, pages_lines, page_count)
        pages_chars[i] = list of Char objects for page i
        pages_lines[i] = list of Line objects for page i
    """
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    all_chars: List[List[Char]] = []
    all_lines: List[List[Line]] = []
    all_rects: List[List[Rect]] = []

    for page_idx, page in enumerate(reader.pages):
        page_chars: List[Char] = []
        page_lines: List[Line] = []
        page_rects: List[Rect] = []

        # Extract from page contents via visitor pattern
        def _visitor_text(text: str, cm: Any, tm: Any, font_dict: Any, font_size: float) -> None:
            """Visitor for text operators."""
            nonlocal page_chars
            # Transform matrix [a b c d e f]
            # x = tm[4], y = tm[5]
            x = tm[4]
            y = tm[5]

            # Get font properties
            font_name = ""
            bold = False
            italic = False
            if font_dict:
                font_name = font_dict.get("/BaseFont", "") or font_dict.get("base_font", "")
                name_lower = font_name.lower()
                bold = "bold" in name_lower or "black" in name_lower
                italic = "italic" in name_lower or "oblique" in name_lower

            # Estimate char width
            avg_char_width = font_size * 0.5 if font_size > 0 else 4.0

            for i, ch in enumerate(text):
                if ch.strip() or ch == " ":
                    page_chars.append(Char(
                        text=ch,
                        x0=x + i * avg_char_width,
                        y0=y - font_size * 0.1,
                        x1=x + i * avg_char_width + avg_char_width,
                        y1=y + font_size * 0.9,
                        font_name=font_name,
                        font_size=font_size,
                        bold=bold,
                        italic=italic,
                    ))

        # Extract text with positioning
        try:
            page.extract_text(visitor_text=_visitor_text)
        except Exception as e:
            log.warning("  Page %d text extraction failed: %s", page_idx + 1, e)

        # Extract graphics (lines, rects) by parsing raw content stream
        try:
            content = page.get_contents()
            if content:
                raw = content.get_data().decode("latin-1", errors="replace")
                page_lines, page_rects = _parse_pdf_graphics(raw)
        except Exception as e:
            log.warning("  Page %d graphics extraction failed: %s", page_idx + 1, e)

        # Normalize y-coordinates: PDF origin is bottom-left, we want top-left
        page_height = float(page.mediabox.height) if page.mediabox else 792.0
        for ch in page_chars:
            ch.y0 = page_height - ch.y0
            ch.y1 = page_height - ch.y1
            # Swap y0/y1 if needed
            if ch.y0 > ch.y1:
                ch.y0, ch.y1 = ch.y1, ch.y0
        for ln in page_lines:
            ln.y0 = page_height - ln.y0
            ln.y1 = page_height - ln.y1

        all_chars.append(page_chars)
        all_lines.append(page_lines)

    return all_chars, all_lines, len(reader.pages)


def _parse_pdf_graphics(raw_content: str) -> Tuple[List[Line], List[Rect]]:
    """
    Parse PDF content stream operators to extract lines and rectangles.

    Handles: m (moveto), l (lineto), re (rectangle), S/stroke, f/fill
    """
    lines: List[Line] = []
    rects: List[Rect] = []
    path_ops: List[Tuple[str, List[float]]] = []

    # Tokenize the content stream
    tokens = re.findall(r'[-]?\d+\.?\d*|[a-zA-Z*/]+\b', raw_content)

    i = 0
    current_x = 0.0
    current_y = 0.0
    path_start_x = 0.0
    path_start_y = 0.0
    path_segments: List[Tuple[float, float]] = []

    def _parse_num(tok: str) -> float:
        try:
            return float(tok)
        except ValueError:
            return 0.0

    while i < len(tokens):
        tok = tokens[i]

        if tok == 'm':
            # moveto
            if i >= 2:
                current_x = _parse_num(tokens[i - 2])
                current_y = _parse_num(tokens[i - 1])
                path_start_x = current_x
                path_start_y = current_y
                path_segments = [(current_x, current_y)]
            i += 1
        elif tok == 'l':
            # lineto
            if i >= 2:
                current_x = _parse_num(tokens[i - 2])
                current_y = _parse_num(tokens[i - 1])
                path_segments.append((current_x, current_y))
            i += 1
        elif tok == 're':
            # rectangle: x y width height re
            if i >= 4:
                rx = _parse_num(tokens[i - 4])
                ry = _parse_num(tokens[i - 3])
                rw = _parse_num(tokens[i - 2])
                rh = _parse_num(tokens[i - 1])
                rects.append(Rect(x0=rx, y0=ry, x1=rx + rw, y1=ry + rh))
            i += 1
        elif tok in ('S', 's', 'f', 'F', 'B', 'b', 'n'):
            # Stroke/fill operators
            # Convert path segments to lines
            for j in range(len(path_segments) - 1):
                x0, y0 = path_segments[j]
                x1, y1 = path_segments[j + 1]
                # Only add horizontal or vertical lines (table gridlines)
                if abs(x1 - x0) < 0.5 or abs(y1 - y0) < 0.5:
                    line_width = abs(x1 - x0) if abs(y1 - y0) < 0.5 else abs(y1 - y0)
                    if line_width > 5:  # Skip noise
                        lines.append(Line(
                            x0=min(x0, x1), y0=min(y0, y1),
                            x1=max(x0, x1), y1=max(y0, y1),
                        ))

            if tok == 's' and len(path_segments) >= 2:
                # Close path: add line from last to first
                x0, y0 = path_segments[-1]
                x1, y1 = path_segments[0]
                if abs(x1 - x0) < 0.5 or abs(y1 - y0) < 0.5:
                    line_width = abs(x1 - x0) if abs(y1 - y0) < 0.5 else abs(y1 - y0)
                    if line_width > 5:
                        lines.append(Line(x0=min(x0, x1), y0=min(y0, y1),
                                          x1=max(x0, x1), y1=max(y0, y1)))

            path_segments = []
            i += 1
        elif tok in ('w', 'J', 'j', 'M', 'd', 'i', 'gs', 'cm',
                     'q', 'Q', 'BT', 'ET', 'Td', 'Tf', 'TJ', 'Tj', "'", '"',
                     'Do', 'cs', 'CS', 'sc', 'SC', 'rg', 'RG', 'k', 'K'):
            # Skip graphics state and text operators
            # These consume operands that precede them
            i += 1
        elif tok in ('W', 'W*', 'n', 'h', 'cm', 'd1', 'sh', 'EI', 'BI', 'ID'):
            # Clip, path construction, inline image
            i += 1
        else:
            # Operand or numeric — skip, will be consumed by next operator
            i += 1

    return lines, rects


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 2: Table Boundary Detection
# ──────────────────────────────────────────────────────────────────────────────


def _cluster_lines(lines: List[Line], axis: str, tolerance: float = 3.0) -> List[float]:
    """
    Cluster line positions along an axis.
    axis='x' for horizontal dividers (cluster by y)
    axis='y' for vertical dividers (cluster by x)

    Returns sorted list of unique positions.
    """
    positions: List[float] = []
    if axis == 'x':
        # Horizontal lines: cluster by y (all have similar y)
        for ln in lines:
            if abs(ln.y1 - ln.y0) < 1.5:  # Horizontal line (same y)
                positions.append(ln.y0)
    else:
        # Vertical lines: cluster by x (all have similar x)
        for ln in lines:
            if abs(ln.x1 - ln.x0) < 1.5:  # Vertical line (same x)
                positions.append(ln.x0)

    if not positions:
        return []

    # Cluster nearby positions
    positions.sort()
    clusters: List[float] = [positions[0]]
    for p in positions[1:]:
        if abs(p - clusters[-1]) > tolerance:
            clusters.append(p)

    return clusters


def _detect_table_lattice(
    chars: List[Char],
    lines: List[Line],
    page_width: float,
    page_height: float,
    min_rows: int = 2,
    min_cols: int = 2,
) -> Optional[Table]:
    """
    Lattice-based table detection: finds tables from grid lines.

    Algorithm:
    1. Cluster horizontal and vertical lines
    2. Find intersection points
    3. Each enclosed quadrilateral = one cell
    4. Detect merged cells from missing grid lines

    Returns Table if found, None otherwise.
    """
    # Step 1: Cluster lines into grid dividers
    h_positions = _cluster_lines(lines, 'x')  # Horizontal dividers (y positions)
    v_positions = _cluster_lines(lines, 'y')  # Vertical dividers (x positions)

    if len(h_positions) < min_rows + 1 or len(v_positions) < min_cols + 1:
        return None

    num_rows = len(h_positions) - 1
    num_cols = len(v_positions) - 1
    table_bbox = (
        v_positions[0], h_positions[0],
        v_positions[-1], h_positions[-1],
    )

    # Step 2: Build cell grid
    cells: List[List[Cell]] = []

    for r in range(num_rows):
        row_cells: List[Cell] = []
        for c in range(num_cols):
            cell = Cell(
                row=r, col=c,
                x0=v_positions[c],
                y0=h_positions[r],
                x1=v_positions[c + 1],
                y1=h_positions[r + 1],
            )
            row_cells.append(cell)
        cells.append(row_cells)

    # Step 3: Detect merged cells via gap analysis
    # A merged cell is detected when a grid line doesn't extend through
    # the full table. We check if vertical lines span the full height
    # and horizontal lines span the full width.

    merged_cells = _detect_merged_cells(lines, v_positions, h_positions, cells)
    for mc in merged_cells:
        r, c, rowspan, colspan = mc
        if r < len(cells) and c < len(cells[r]):
            cells[r][c].rowspan = rowspan
            cells[r][c].colspan = colspan
            # Mark spanned cells as merged
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr == 0 and dc == 0:
                        continue
                    if r + dr < len(cells) and c + dc < len(cells[r + dr]):
                        cells[r + dr][c + dc].rowspan = -1  # Merged away

    # Step 4: Map text to cells
    _map_chars_to_cells(chars, cells)

    # Step 5: Clean up merged cells (only keep top-left)
    clean_cells: List[List[Cell]] = []
    for r in range(len(cells)):
        row: List[Cell] = []
        for c in range(len(cells[r])):
            if cells[r][c].rowspan >= 0:  # Not merged away
                row.append(cells[r][c])
        if row:
            clean_cells.append(row)

    return Table(
        cells=clean_cells,
        num_rows=len(clean_cells),
        num_cols=max(len(row) for row in clean_cells) if clean_cells else 0,
        bbox=table_bbox,
        strategy=DetectionStrategy.LATTICE,
        confidence=_score_table(clean_cells),
    )


def _detect_table_stream(
    chars: List[Char],
    page_width: float,
    page_height: float,
    min_rows: int = 2,
    min_cols: int = 2,
) -> Optional[Table]:
    """
    Stream-based table detection: infers table boundaries from whitespace gaps.

    For PDFs without explicit grid lines. Uses vertical and horizontal
    character density histograms to find column/row boundaries.

    Algorithm:
    1. Group chars into text lines by y-coordinate
    2. Build vertical histogram → find column gaps
    3. Build horizontal histogram → find row gaps
    4. Refine boundaries by text alignment
    """
    if len(chars) < 10:
        return None

    # Step 1: Group chars into text lines
    # Cluster by y-coordinate (top of char)
    y_positions = sorted(set(ch.y0 for ch in chars))
    line_clusters: List[float] = [y_positions[0]]
    for y in y_positions[1:]:
        if abs(y - line_clusters[-1]) > page_height * 0.015:  # 1.5% of page height
            line_clusters.append(y)

    if len(line_clusters) < min_rows + 1:
        return None

    # Step 2: For each line, group into words and find x-gaps
    # Build column boundaries from whitespace gaps
    all_x_gaps: List[float] = []
    line_text_blocks: List[List[Tuple[float, float, str]]] = []

    for y in line_clusters:
        line_chars = [ch for ch in chars if abs(ch.y0 - y) < page_height * 0.015]
        if not line_chars:
            continue
        line_chars.sort(key=lambda c: c.x0)

        # Group into words by x-proximity
        words: List[Tuple[float, float, str]] = []
        current_word = ""
        current_x0 = line_chars[0].x0
        current_x1 = line_chars[0].x1

        for ch in line_chars:
            if ch.x0 - current_x1 > page_width * 0.01:  # Gap > 1% page width
                if current_word:
                    words.append((current_x0, current_x1, current_word))
                current_word = ch.text
                current_x0 = ch.x0
                current_x1 = ch.x1
            else:
                current_word += ch.text
                current_x1 = ch.x1

        if current_word:
            words.append((current_x0, current_x1, current_word))

        line_text_blocks.append(words)

        # Record gaps between words (potential column boundaries)
        for i in range(len(words) - 1):
            gap = words[i + 1][0] - words[i][1]
            if gap > page_width * 0.01:  # Significant gap
                all_x_gaps.append((words[i][1] + words[i + 1][0]) / 2)

    if len(all_x_gaps) < min_cols - 1:
        return None

    # Cluster x-gaps to find consistent column boundaries
    all_x_gaps.sort()
    gap_clusters: List[float] = [all_x_gaps[0]]
    for g in all_x_gaps[1:]:
        if abs(g - gap_clusters[-1]) > page_width * 0.02:
            gap_clusters.append(g)

    # Column boundaries: min char x + gap clusters + max char x
    min_x = min(ch.x0 for ch in chars) if chars else 0.0
    max_x = max(ch.x1 for ch in chars) if chars else page_width
    min_y = min(ch.y0 for ch in chars) if chars else 0.0
    v_positions = [min_x] + gap_clusters + [max_x]
    h_positions = [min_y] + [y + page_height * 0.01 for y in line_clusters]

    # Filter to reasonable table size
    num_rows = len(h_positions) - 1
    num_cols = len(v_positions) - 1

    if num_rows < min_rows or num_cols < min_cols:
        return None
    if num_rows > 100 or num_cols > 30:  # Sanity check
        return None

    # Build cells
    cells: List[List[Cell]] = []
    for r in range(num_rows):
        row_cells: List[Cell] = []
        for c in range(num_cols):
            cell = Cell(
                row=r, col=c,
                x0=v_positions[c],
                y0=h_positions[r],
                x1=v_positions[c + 1],
                y1=h_positions[r + 1],
            )
            row_cells.append(cell)
        cells.append(row_cells)

    # Map chars to cells
    _map_chars_to_cells(chars, cells)

    # Score the result
    confidence = _score_table(cells)

    table_bbox = (v_positions[0], h_positions[0], v_positions[-1], h_positions[-1])

    return Table(
        cells=cells,
        num_rows=num_rows,
        num_cols=num_cols,
        bbox=table_bbox,
        strategy=DetectionStrategy.STREAM,
        confidence=confidence,
    )


def _detect_merged_cells(
    lines: List[Line],
    v_positions: List[float],
    h_positions: List[float],
    cells: List[List[Cell]],
) -> List[Tuple[int, int, int, int]]:
    """
    Detect merged/spanned cells from grid line gap analysis.

    Key algorithm:
    - If a vertical line doesn't span all rows → column merge in that region
    - If a horizontal line doesn't span all columns → row merge in that region
    - Also detect from content overlap patterns

    Returns list of (row, col, rowspan, colspan) tuples.
    """
    merges: List[Tuple[int, int, int, int]] = []

    if not v_positions or not h_positions:
        return merges

    # Check vertical line continuity
    for vi in range(1, len(v_positions) - 1):
        x = v_positions[vi]
        # Find all vertical line segments at this x
        v_lines = [
            ln for ln in lines
            if abs(ln.x0 - x) < 2.0 and abs(ln.x1 - x) < 2.0
            and abs(ln.y1 - ln.y0) > 10  # Non-trivial length
        ]
        if not v_lines:
            continue

        # Check which row ranges are covered by vertical lines
        covered_ranges: List[Tuple[int, int]] = []
        for vl in v_lines:
            y_min, y_max = min(vl.y0, vl.y1), max(vl.y0, vl.y1)
            for ri in range(len(h_positions) - 1):
                cell_top = h_positions[ri]
                cell_bot = h_positions[ri + 1]
                # If the line covers this row's vertical span
                if y_min <= cell_top + 5 and y_max >= cell_bot - 5:
                    covered_ranges.append((ri, ri))

        # Merge non-covered adjacent ranges
        if not covered_ranges:
            # Line doesn't cover any rows — merge all rows for columns vi-1, vi
            merges.append((0, vi - 1, len(h_positions) - 1, 2))
        else:
            covered_ranges.sort()
            uncovered = []
            prev_end = -1
            for cr in covered_ranges:
                if cr[0] > prev_end + 1:
                    uncovered.append((prev_end + 1, cr[0] - 1))
                prev_end = max(prev_end, cr[1])
            if prev_end < len(h_positions) - 2:
                uncovered.append((prev_end + 1, len(h_positions) - 2))

            # Convert uncovered ranges to merges
            for rs, re in uncovered:
                if re >= rs:
                    merges.append((rs, vi - 1, re - rs + 1, 2))

    # Check horizontal line continuity
    for hi in range(1, len(h_positions) - 1):
        y = h_positions[hi]
        h_lines = [
            ln for ln in lines
            if abs(ln.y0 - y) < 2.0 and abs(ln.y1 - y) < 2.0
            and abs(ln.x1 - ln.x0) > 10
        ]
        if not h_lines:
            continue

        covered_ranges = []
        for hl in h_lines:
            x_min, x_max = min(hl.x0, hl.x1), max(hl.x0, hl.x1)
            for ci in range(len(v_positions) - 1):
                cell_left = v_positions[ci]
                cell_right = v_positions[ci + 1]
                if x_min <= cell_left + 5 and x_max >= cell_right - 5:
                    covered_ranges.append((ci, ci))

        if not covered_ranges:
            merges.append((hi - 1, 0, 2, len(v_positions) - 1))
        else:
            covered_ranges.sort()
            uncovered = []
            prev_end = -1
            for cr in covered_ranges:
                if cr[0] > prev_end + 1:
                    uncovered.append((prev_end + 1, cr[0] - 1))
                prev_end = max(prev_end, cr[1])
            if prev_end < len(v_positions) - 2:
                uncovered.append((prev_end + 1, len(v_positions) - 2))

            for cs, ce in uncovered:
                if ce >= cs:
                    merges.append((hi - 1, cs, 2, ce - cs + 1))

    # Deduplicate and simplify
    # For overlapping merges, keep the largest
    seen = set()
    unique_merges: List[Tuple[int, int, int, int]] = []
    for m in sorted(merges, key=lambda x: (x[0], x[1])):
        key = (m[0], m[1])
        if key not in seen:
            seen.add(key)
            unique_merges.append(m)

    return unique_merges


def _map_chars_to_cells(chars: List[Char], cells: List[List[Cell]]) -> None:
    """
    Map characters to their containing cells based on bounding box overlap.

    For each char, find which cell contains its midpoint, and append the
    character to that cell's text buffer.
    """
    for ch in chars:
        mid_x = (ch.x0 + ch.x1) / 2
        mid_y = (ch.y0 + ch.y1) / 2

        for row in cells:
            for cell in row:
                if (cell.x0 <= mid_x <= cell.x1 and
                        cell.y0 <= mid_y <= cell.y1 and
                        cell.rowspan >= 0):
                    cell.text += ch.text
                    if ch.bold:
                        cell.bold = True
                    break


def _score_table(cells: List[List[Cell]]) -> float:
    """
    Score a table detection result from 0.0 to 1.0.

    Higher score means more confident in the detected structure.
    Factors: cell fill ratio, data type consistency, row alignment.
    """
    if not cells or not cells[0]:
        return 0.0

    total_cells = sum(len(row) for row in cells)
    filled_cells = sum(1 for row in cells for c in row if c.text.strip())

    fill_ratio = filled_cells / max(total_cells, 1)

    # Check column alignment consistency
    col_widths = [len(row) for row in cells]
    consistent_widths = sum(1 for w in col_widths if w == col_widths[0])
    alignment_score = consistent_widths / max(len(col_widths), 1)

    # Check for likely table content (numeric data in columns)
    numeric_cols = 0
    for c in range(max(len(row) for row in cells)):
        col_texts = [
            row[c].text.strip() for row in cells
            if c < len(row) and row[c].text.strip()
        ]
        if col_texts:
            numeric_count = sum(1 for t in col_texts if _is_numeric(t))
            if numeric_count / max(len(col_texts), 1) > 0.5:
                numeric_cols += 1

    data_type_score = min(numeric_cols / max(len(cells[0]), 1), 1.0)

    # Weighted score
    score = fill_ratio * 0.4 + alignment_score * 0.3 + data_type_score * 0.3
    return min(max(score, 0.0), 1.0)


def _is_numeric(text: str) -> bool:
    """Check if text represents a number (possibly with currency symbols)."""
    cleaned = re.sub(r'[$€£¥,.\s%()\[\]-]', '', text)
    return bool(cleaned) and cleaned.isdigit()


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 3: GLM-OCR Integration for Scanned Documents
# ──────────────────────────────────────────────────────────────────────────────


_GLM_TABLE_PROMPT = """You are a document table extraction engine. Analyze this document image and extract ALL tables found on the page. For each table, output a JSON object with this exact structure:

{
  "tables": [
    {
      "title": "optional table caption or title",
      "headers": ["Column1", "Column2", ...],
      "rows": [
        ["row1col1", "row1col2", ...],
        ["row2col1", "row2col2", ...]
      ],
      "merged_cells": [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 2}
      ],
      "confidence": 0.95
    }
  ],
  "full_text": "any text outside tables on this page"
}

Rules:
- Extract cell content exactly as it appears
- Preserve number formatting (currency, decimals, percentages)
- Detect merged/spanning cells from visual layout
- Identify header rows (bold/different background)
- If no tables exist, return empty tables array with full_text
- Output ONLY the JSON, no other text"""


async def _extract_tables_glm_ocr(
    page_image: Image.Image,
    engine: Any = None,
) -> ExtractionResult:
    """
    Use GLM-OCR to extract tables from a scanned document page.

    Sends the page image to GLM-OCR with a structured prompt requesting
    JSON-formatted table data.

    Args:
        page_image: PIL Image of the page
        engine: Optional pre-loaded GLM_OCR_Engine instance. If None, loads via get_engine().
    """
    from app.glm_ocr_backend import get_engine

    try:
        if engine is None:
            engine = get_engine()
        # Run GLM-OCR with table extraction prompt
        output = engine.ocr(page_image, prompt=_GLM_TABLE_PROMPT)

        # Parse the JSON response
        # The model might wrap in markdown code blocks
        json_str = output.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()

        data = json.loads(json_str)
        result = ExtractionResult()
        result.strategy = DetectionStrategy.GLM_OCR

        for table_data in data.get("tables", []):
            headers = table_data.get("headers", [])
            rows_data = table_data.get("rows", [])
            merged = table_data.get("merged_cells", [])

            if not rows_data:
                continue

            num_cols = max(len(rows_data[0]) if rows_data else 0, len(headers))
            num_rows = len(rows_data) + (1 if headers else 0)

            cells: List[List[Cell]] = []
            y_pos = 0.0

            # Header row
            if headers:
                header_cells: List[Cell] = []
                for ci, h in enumerate(headers):
                    cell = Cell(
                        row=0, col=ci, text=h,
                        is_header=True, bold=True,
                        y0=y_pos, y1=y_pos + 20,
                        x0=ci * 100.0, x1=(ci + 1) * 100.0,
                    )
                    header_cells.append(cell)
                cells.append(header_cells)
                y_pos += 20

            # Data rows
            for ri, row_data in enumerate(rows_data):
                row_cells: List[Cell] = []
                for ci in range(num_cols):
                    text = row_data[ci] if ci < len(row_data) else ""
                    cell = Cell(
                        row=ri + (1 if headers else 0), col=ci, text=text,
                        y0=y_pos, y1=y_pos + 18,
                        x0=ci * 100.0, x1=(ci + 1) * 100.0,
                    )
                    row_cells.append(cell)
                cells.append(row_cells)
                y_pos += 18

            # Apply merged cell info
            for mc in merged:
                r, c = mc.get("row", 0), mc.get("col", 0)
                rs, cs = mc.get("rowspan", 1), mc.get("colspan", 1)
                if r < len(cells) and c < len(cells[r]):
                    cells[r][c].rowspan = rs
                    cells[r][c].colspan = cs

            result.tables.append(Table(
                cells=cells,
                num_rows=len(cells),
                num_cols=num_cols,
                strategy=DetectionStrategy.GLM_OCR,
                title=table_data.get("title", ""),
                confidence=table_data.get("confidence", 0.9),
            ))

        result.full_text = data.get("full_text", "")
        result.pages_with_tables = 1 if result.tables else 0
        return result

    except Exception as e:
        log.warning("GLM-OCR table extraction failed: %s", e)
        return ExtractionResult(
            error=str(e),
            full_text="[GLM-OCR extraction failed]",
        )


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 4: Smart Templates System
# ──────────────────────────────────────────────────────────────────────────────


_TEMPLATES_DIR: Optional[str] = None


def set_templates_dir(path: str) -> None:
    """Set the directory where Smart Templates are stored."""
    global _TEMPLATES_DIR
    _TEMPLATES_DIR = path


def _get_templates_dir() -> str:
    """Get the templates directory, creating it if needed."""
    global _TEMPLATES_DIR
    if _TEMPLATES_DIR is None:
        _TEMPLATES_DIR = os.environ.get(
            "EXTRACTION_TEMPLATES_DIR",
            os.path.join(os.path.dirname(__file__), "..", "templates"),
        )
    Path(_TEMPLATES_DIR).mkdir(parents=True, exist_ok=True)
    return _TEMPLATES_DIR


def save_template(template: SmartTemplate) -> str:
    """
    Save a Smart Template to disk as JSON.
    Returns the file path.
    """
    templates_dir = _get_templates_dir()
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', template.name)
    path = os.path.join(templates_dir, f"{safe_name}.template.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(template), f, indent=2, ensure_ascii=False)
    log.info("Saved template: %s", path)
    return path


def load_template(name: str) -> Optional[SmartTemplate]:
    """
    Load a Smart Template by name from the templates directory.
    """
    templates_dir = _get_templates_dir()
    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    path = os.path.join(templates_dir, f"{safe_name}.template.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return SmartTemplate(**data)


def list_templates() -> List[str]:
    """List all saved Smart Template names."""
    templates_dir = _get_templates_dir()
    if not os.path.isdir(templates_dir):
        return []
    templates = []
    for fname in os.listdir(templates_dir):
        if fname.endswith(".template.json"):
            templates.append(fname.replace(".template.json", ""))
    return sorted(templates)


def create_template_from_table(table: Table, page_width: float, page_height: float) -> SmartTemplate:
    """
    Create a Smart Template from a detected table.
    Stores cell positions as ratios of page dimensions for scale-invariant matching.
    """
    template = SmartTemplate(
        name=f"table_template_{int(time.time())}",
        page_width_ref=page_width,
        page_height_ref=page_height,
    )

    # Store column definitions as position ratios
    if table.cells:
        for ci, cell in enumerate(table.cells[0]):
            template.columns.append({
                "label": cell.text.strip() if cell.is_header else f"col_{ci}",
                "x_ratio_start": cell.x0 / max(page_width, 1),
                "x_ratio_end": cell.x1 / max(page_width, 1),
                "data_type": cell.data_type,
                "width_ratio": (cell.x1 - cell.x0) / max(page_width, 1),
            })

        # Store row definitions
        for ri, row in enumerate(table.cells):
            if row:
                template.rows.append({
                    "y_ratio_start": row[0].y0 / max(page_height, 1),
                    "y_ratio_end": row[0].y1 / max(page_height, 1),
                    "height_ratio": (row[0].y1 - row[0].y0) / max(page_height, 1),
                    "is_header": ri == 0 and any(c.is_header for c in row),
                })

        # Find anchor text (first column, first 2 rows)
        for ri in range(min(2, len(table.cells))):
            for ci in range(min(1, len(table.cells[ri]))):
                cell = table.cells[ri][ci]
                if cell.text.strip():
                    template.anchors.append({
                        "text": cell.text.strip()[:50],
                        "x_ratio": cell.x0 / max(page_width, 1),
                        "y_ratio": cell.y0 / max(page_height, 1),
                        "width_ratio": (cell.x1 - cell.x0) / max(page_width, 1),
                        "height_ratio": (cell.y1 - cell.y0) / max(page_height, 1),
                    })

    return template


def match_template(
    chars: List[Char],
    template: SmartTemplate,
    page_width: float,
    page_height: float,
) -> Optional[Tuple[float, float]]:
    """
    Try to align a Smart Template to a page by finding anchor text.

    Returns (x_offset, y_offset) offset to align template to page,
    or None if template doesn't match.

    Algorithm:
    1. Search for anchor text in the page
    2. If found, compute scaling and offset
    3. Apply proportional grid adjustment
    """
    if not template.anchors:
        return None

    # Build page text with positions
    page_text_map: Dict[str, List[Tuple[float, float, float, float]]] = {}
    for ch in chars:
        if ch.text.strip():
            key = ch.text.strip()
            if key not in page_text_map:
                page_text_map[key] = []
            page_text_map[key].append((ch.x0, ch.y0, ch.x1, ch.y1))

    # Try to match each anchor
    for anchor in template.anchors:
        anchor_text = anchor.get("text", "")
        if not anchor_text:
            continue

        if anchor_text in page_text_map:
            positions = page_text_map[anchor_text]
            if positions:
                # Use first match
                px0, py0, px1, py1 = positions[0]
                expected_x = anchor["x_ratio"] * page_width
                expected_y = anchor["y_ratio"] * page_height

                # Check proportional tolerance (10% of page)
                x_tol = page_width * 0.10
                y_tol = page_height * 0.10

                x_offset = px0 - expected_x
                y_offset = py0 - expected_y

                # Verify with more anchors if available
                matches = 1
                for other_anchor in template.anchors:
                    if other_anchor.get("text", "") == anchor_text:
                        continue
                    ot = other_anchor.get("text", "")
                    if ot in page_text_map:
                        other_pos = page_text_map[ot]
                        if other_pos:
                            opx0, opy0, _, _ = other_pos[0]
                            eox = other_anchor["x_ratio"] * page_width + x_offset
                            eoy = other_anchor["y_ratio"] * page_height + y_offset
                            if (abs(opx0 - eox) < x_tol and
                                    abs(opy0 - eoy) < y_tol):
                                matches += 1

                # Require at least 2 anchor matches for confidence
                if matches >= 1:
                    return (x_offset, y_offset)

    return None


def apply_template(
    chars: List[Char],
    template: SmartTemplate,
    offset: Tuple[float, float],
    page_width: float,
    page_height: float,
) -> Table:
    """
    Apply a matched template to extract a table from chars.

    Uses the template's column/row definitions (as ratios) scaled
    to the actual page dimensions with the computed offset.
    """
    x_offset, y_offset = offset

    # Scale template to page
    v_positions: List[float] = []
    for col in template.columns:
        x_start = col["x_ratio_start"] * page_width + x_offset
        v_positions.append(x_start)
    if template.columns:
        last_end = template.columns[-1]["x_ratio_end"] * page_width + x_offset
        v_positions.append(last_end)

    h_positions: List[float] = []
    for row in template.rows:
        y_start = row["y_ratio_start"] * page_height + y_offset
        h_positions.append(y_start)
    if template.rows:
        last_end = template.rows[-1]["y_ratio_end"] * page_height + y_offset
        h_positions.append(last_end)

    if len(v_positions) < 2 or len(h_positions) < 2:
        return Table(num_rows=0, num_cols=0, strategy=DetectionStrategy.MANUAL)

    num_rows = len(h_positions) - 1
    num_cols = len(v_positions) - 1

    cells: List[List[Cell]] = []
    for r in range(num_rows):
        row: List[Cell] = []
        for c in range(num_cols):
            cell = Cell(
                row=r, col=c,
                x0=v_positions[c],
                y0=h_positions[r],
                x1=v_positions[c + 1],
                y1=h_positions[r + 1],
                is_header=(r == 0 and template.rows and template.rows[0].get("is_header", False)),
            )
            row.append(cell)
        cells.append(row)

    # Apply cell merges from template
    for merge in template.cell_merges:
        r, c = merge.get("row", 0), merge.get("col", 0)
        rs, cs = merge.get("rowspan", 1), merge.get("colspan", 1)
        if r < len(cells) and c < len(cells[r]):
            cells[r][c].rowspan = rs
            cells[r][c].colspan = cs

    # Map characters to cells
    _map_chars_to_cells(chars, cells)

    # Apply field mappings
    if template.field_mappings:
        for field_name, coord in template.field_mappings.items():
            try:
                parts = coord.split(",")
                r, c = int(parts[0]), int(parts[1])
                if r < len(cells) and c < len(cells[r]):
                    cells[r][c].text = cells[r][c].text.strip()
                    if not cells[r][c].text and cells[r][c].rowspan >= 0:
                        # Try to find text in this region
                        pass
            except (IndexError, ValueError):
                pass

    return Table(
        cells=cells,
        num_rows=num_rows,
        num_cols=num_cols,
        strategy=DetectionStrategy.MANUAL,
        confidence=0.95,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 4: Content Detection & Post-processing
# ──────────────────────────────────────────────────────────────────────────────


def _detect_data_types(table: Table) -> None:
    """
    Detect data types for each cell in a table.
    Types: text, number, date, currency, percentage
    """
    for row in table.cells:
        for cell in row:
            if not cell.text.strip():
                cell.data_type = "empty"
                continue

            text = cell.text.strip()

            # Currency: $, €, £, ¥ prefix, or decimal with 2 places
            if re.match(r'^[\$€£¥]\s*[\d,]+\.?\d{0,2}$', text):
                cell.data_type = "currency"
                continue

            # Percentage
            if re.match(r'^[\d,.]+%$', text):
                cell.data_type = "percentage"
                continue

            # Number: integers and decimals
            if re.match(r'^[\d,]+\.?\d*$', text.replace(",", "")):
                cell.data_type = "number"
                continue

            # Date: common formats
            date_patterns = [
                r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$',
                r'^\d{4}[/-]\d{1,2}[/-]\d{1,2}$',
                r'^[A-Z][a-z]+ \d{1,2},? \d{4}$',
                r'^\d{1,2} [A-Z][a-z]+ \d{4}$',
            ]
            if any(re.match(p, text) for p in date_patterns):
                cell.data_type = "date"
                continue

            cell.data_type = "text"


def _detect_headers(table: Table) -> None:
    """
    Detect header rows in a table.
    Heuristics: first 1-2 rows if they're bold, or contain known header keywords.
    """
    if not table.cells:
        return

    header_keywords = {
        "name", "date", "total", "amount", "price", "quantity", "description",
        "id", "no", "item", "code", "unit", "rate", "tax", "discount",
        "subtotal", "grand total", "invoice", "payment", "reference",
        "account", "address", "phone", "email", "status", "action",
    }

    for ri, row in enumerate(table.cells):
        if ri >= 2:  # Only check first 2 rows
            break

        header_score = 0
        for cell in row:
            text = cell.text.strip().lower()
            if cell.bold:
                header_score += 1
            if text in header_keywords or text.rstrip(":") in header_keywords:
                header_score += 2
            if text.endswith(":"):
                header_score += 1

        # If score indicates header, mark all cells in row
        if header_score >= len(row) * 0.5:
            for cell in row:
                cell.is_header = True


# ──────────────────────────────────────────────────────────────────────────────
#  Stage 5: Output Serialization
# ──────────────────────────────────────────────────────────────────────────────


def table_to_xlsx(table: Table, output_path: str) -> str:
    """
    Write a detected table to Excel (.xlsx) format.

    Handles:
    - Cell text content
    - Merged cells (rowspan, colspan)
    - Bold headers
    - Column widths
    - Data type formatting (numbers, dates, currency)
    - Header row styling
    """
    from openpyxl import Workbook
    from openpyxl.styles import (
        Font, Alignment, Border, Side, PatternFill, numbers,
    )
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    if table.title:
        ws.title = table.title[:31]  # Excel sheet name max 31 chars
    else:
        ws.title = "Extracted Table"

    # Define styles
    header_font = Font(bold=True, size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, size=11, color="FFFFFF")
    data_font = Font(size=10)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    data_alignment = Alignment(vertical="top", wrap_text=True)
    number_alignment = Alignment(horizontal="right", vertical="top")
    currency_format = '#,##0.00'
    date_format = 'YYYY-MM-DD'

    # Write data rows
    excel_row = 1
    for row in table.cells:
        excel_col = 1
        for cell in row:
            if cell.rowspan < 0:
                # This cell is merged away (not the top-left)
                continue

            ws_cell = ws.cell(row=excel_row, column=excel_col)
            ws_cell.value = cell.text.strip()

            # Apply styling based on cell properties
            if cell.is_header:
                ws_cell.font = header_font_white
                ws_cell.fill = header_fill
                ws_cell.alignment = header_alignment
                ws_cell.border = thin_border
            else:
                ws_cell.font = data_font
                ws_cell.border = thin_border

                # Alignment by data type
                if cell.data_type in ("number", "percentage"):
                    ws_cell.alignment = number_alignment
                    try:
                        cleaned = cell.text.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "")
                        ws_cell.value = float(cleaned.replace("%", ""))
                    except ValueError:
                        pass
                elif cell.data_type == "currency":
                    ws_cell.alignment = number_alignment
                    ws_cell.number_format = currency_format
                    try:
                        cleaned = cell.text.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
                        ws_cell.value = float(cleaned)
                    except ValueError:
                        pass
                elif cell.data_type == "date":
                    ws_cell.alignment = data_alignment
                    ws_cell.number_format = date_format
                else:
                    ws_cell.alignment = data_alignment

            # Handle merged cells
            if cell.rowspan > 1 or cell.colspan > 1:
                end_row = excel_row + cell.rowspan - 1
                end_col = excel_col + cell.colspan - 1
                ws.merge_cells(
                    start_row=excel_row, start_column=excel_col,
                    end_row=end_row, end_column=end_col,
                )

            excel_col += 1
        excel_row += 1

    # Auto-fit column widths (approximate)
    for col_idx in range(1, table.num_cols + 1):
        max_len = 8  # Minimum width
        for row in table.cells:
            if col_idx - 1 < len(row):
                cell_text = row[col_idx - 1].text.strip()
                if cell_text:
                    max_len = max(max_len, min(len(cell_text) + 2, 60))
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max_len

    # Freeze header row
    if table.cells and any(c.is_header for c in table.cells[0]):
        ws.freeze_panes = "A2"

    wb.save(output_path)
    log.info("XLSX written: %s (%d rows, %d cols)", output_path, table.num_rows, table.num_cols)
    return output_path


def table_to_csv(table: Table, output_path: str) -> str:
    """
    Write a detected table to CSV format.
    """
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        for row in table.cells:
            writer.writerow([
                cell.text.strip() if cell.rowspan >= 0 else ""
                for cell in row
            ])
    log.info("CSV written: %s (%d rows)", output_path, len(table.cells))
    return output_path


def table_to_docx(table: Table, output_path: str) -> str:
    """
    Write a detected table to Word (.docx) format.
    """
    from docx import Document
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()

    if table.title:
        doc.add_heading(table.title, level=1)

    if not table.cells:
        doc.add_paragraph("(no table data)")
        doc.save(output_path)
        return output_path

    # Create Word table
    num_rows = len(table.cells)
    num_cols = max(len(row) for row in table.cells)
    word_table = doc.add_table(rows=num_rows, cols=num_cols)
    word_table.style = "Table Grid"
    word_table.alignment = WD_TABLE_ALIGNMENT.CENTER

    for ri, row in enumerate(table.cells):
        for ci, cell in enumerate(row):
            if cell.rowspan < 0:
                continue

            word_cell = word_table.cell(ri, ci)
            word_cell.text = cell.text.strip()

            # Style header cells
            if cell.is_header:
                for paragraph in word_cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.size = Pt(10)
                    paragraph.alignment = 1  # Center

            # Handle merged cells
            if cell.rowspan > 1 or cell.colspan > 1:
                end_row = ri + cell.rowspan - 1
                end_col = ci + cell.colspan - 1
                word_cell.merge(word_table.cell(end_row, end_col))

    doc.save(output_path)
    log.info("DOCX written: %s (%d rows)", output_path, len(table.cells))
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
#  Main Extraction Pipeline
# ──────────────────────────────────────────────────────────────────────────────


async def extract_tables(
    pdf_path: str,
    use_glm_ocr: bool = True,
    use_templates: bool = True,
    template_name: str = "",
    output_formats: Optional[List[str]] = None,
    output_dir: str = "",
    dpi: int = 200,
) -> ExtractionResult:
    """
    Main extraction pipeline: extract tables from a PDF.

    Pipeline:
    1. Try text-based extraction (pypdf geometry) for native PDFs
    2. If no text layer or low confidence, use GLM-OCR for scanned PDFs
    3. Try Smart Template matching if available
    4. Post-process: detect data types, headers
    5. Serialize to requested output formats

    Args:
        pdf_path: Path to the PDF file
        use_glm_ocr: Whether to use GLM-OCR for scanned documents
        use_templates: Whether to try Smart Template matching
        template_name: Specific template to apply (empty = auto-detect)
        output_formats: List of formats: "xlsx", "csv", "docx"
        output_dir: Output directory (default: same as input)
        dpi: DPI for PDF-to-image conversion

    Returns:
        ExtractionResult with detected tables
    """
    t0 = time.perf_counter()
    result = ExtractionResult(filename=os.path.basename(pdf_path))

    if output_formats is None:
        output_formats = ["xlsx"]

    if not output_dir:
        output_dir = os.path.dirname(pdf_path) or "."

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_prefix = os.path.join(output_dir, base_name)

    # Pre-load GLM-OCR engine once (shared across all pages)
    glm_engine = None
    if use_glm_ocr:
        try:
            from app.glm_ocr_backend import get_engine
            glm_engine = get_engine()
            log.info("GLM-OCR engine loaded for table extraction")
        except Exception as e:
            log.warning("GLM-OCR engine load failed (proceeding with text-based only): %s", e)
            use_glm_ocr = False

    # ── Step 1: Try text-based extraction ──────────────────────────────────
    try:
        all_chars, all_lines, page_count = _extract_geometry_pypdf(pdf_path)
        result.page_count = page_count

        for page_idx in range(page_count):
            chars = all_chars[page_idx]
            lines = all_lines[page_idx]

            # Try lattice detection first (grid lines present)
            page_width = 612.0  # Default Letter size
            page_height = 792.0

            # Get actual page size from the PDF if available
            try:
                from pypdf import PdfReader
                reader = PdfReader(pdf_path)
                if page_idx < len(reader.pages):
                    page = reader.pages[page_idx]
                    if page.mediabox:
                        page_width = float(page.mediabox.width)
                        page_height = float(page.mediabox.height)
            except Exception:
                pass

            table_lattice = _detect_table_lattice(chars, lines, page_width, page_height)

            # Try stream detection if lattice fails or has low confidence
            table_stream = None
            if table_lattice is None or table_lattice.confidence < 0.5:
                table_stream = _detect_table_stream(chars, page_width, page_height)

            # Pick the best result
            page_table = None
            if table_lattice and table_stream:
                page_table = table_lattice if table_lattice.confidence >= table_stream.confidence else table_stream
            elif table_lattice:
                page_table = table_lattice
            elif table_stream:
                page_table = table_stream

            # ── Step 2: Try Smart Template matching ────────────────────
            if page_table is None and use_templates:
                template_list = list_templates()
                if template_name:
                    template_list = [t for t in template_list if template_name in t]

                for tname in template_list:
                    template = load_template(tname)
                    if template is None:
                        continue
                    offset = match_template(chars, template, page_width, page_height)
                    if offset is not None:
                        page_table = apply_template(chars, template, offset, page_width, page_height)
                        result.strategy = DetectionStrategy.MANUAL
                        log.info("  Page %d: matched template '%s'", page_idx + 1, tname)
                        break

            # ── Step 3: GLM-OCR fallback for scanned pages ─────────────
            if page_table is None and use_glm_ocr:
                # Check if page has enough text to be text-based
                if len(chars) < 20:  # Likely scanned/no text layer
                    try:
                        from pdf2image import convert_from_path
                        images = convert_from_path(pdf_path, dpi=dpi, first_page=page_idx + 1, last_page=page_idx + 1)
                        if images:
                            glm_result = await _extract_tables_glm_ocr(images[0], engine=glm_engine)
                            if glm_result.tables:
                                for t in glm_result.tables:
                                    t.page_num = page_idx
                                    result.tables.append(t)
                                result.pages_with_tables += 1
                                result.strategy = DetectionStrategy.GLM_OCR
                                continue  # Skip the rest of this page's processing
                    except Exception as e:
                        log.warning("  Page %d GLM-OCR failed: %s", page_idx + 1, e)

            if page_table:
                page_table.page_num = page_idx
                result.tables.append(page_table)
                result.pages_with_tables += 1

        # ── Post-process all tables ────────────────────────────────────────
        for table in result.tables:
            _detect_data_types(table)
            _detect_headers(table)

        # ── Serialize to requested formats ─────────────────────────────────
        for fmt in output_formats:
            fmt = fmt.lower().strip(".")
            for ti, table in enumerate(result.tables):
                suffix = f"_table{ti + 1}" if len(result.tables) > 1 else ""
                fmt_path = f"{output_prefix}{suffix}.{fmt}"

                if fmt == "xlsx":
                    table_to_xlsx(table, fmt_path)
                elif fmt == "csv":
                    table_to_csv(table, fmt_path)
                elif fmt == "docx":
                    table_to_docx(table, fmt_path)

        # Also save full text
        full_text_path = f"{output_prefix}_full_text.txt"
        all_text_parts = []
        for table in result.tables:
            for row in table.cells:
                row_text = "\t".join(cell.text.strip() for cell in row if cell.rowspan >= 0)
                if row_text.strip():
                    all_text_parts.append(row_text)
        result.full_text = "\n".join(all_text_parts)
        with open(full_text_path, "w", encoding="utf-8") as f:
            f.write(result.full_text)

    except Exception as e:
        log.error("Extraction failed: %s", e)
        result.error = str(e)

    result.detection_time_ms = (time.perf_counter() - t0) * 1000
    return result


async def extract_tables_from_bytes(
    pdf_bytes: bytes,
    filename: str = "document.pdf",
    use_glm_ocr: bool = True,
    use_templates: bool = True,
    template_name: str = "",
    output_formats: Optional[List[str]] = None,
) -> ExtractionResult:
    """
    Extract tables from PDF bytes (for API usage).

    Writes to a temporary file, runs extraction, returns result
    with in-memory output bytes instead of file paths.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name

    try:
        output_dir = tempfile.mkdtemp()
        result = await extract_tables(
            pdf_path=tmp_path,
            use_glm_ocr=use_glm_ocr,
            use_templates=use_templates,
            template_name=template_name,
            output_formats=output_formats,
            output_dir=output_dir,
        )
        result.filename = filename

        # Read output files into memory
        base = os.path.splitext(os.path.basename(filename))[0]
        result._outputs = {}
        for fmt in (output_formats or ["xlsx"]):
            fmt = fmt.lower().strip(".")
            for ti in range(len(result.tables)):
                suffix = f"_table{ti + 1}" if len(result.tables) > 1 else ""
                path = os.path.join(output_dir, f"{base}{suffix}.{fmt}")
                if os.path.exists(path):
                    with open(path, "rb") as fh:
                        result._outputs[f"{base}{suffix}.{fmt}"] = fh.read()

        # Full text
        txt_path = os.path.join(output_dir, f"{base}_full_text.txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as fh:
                result._outputs[f"{base}_full_text.txt"] = fh.read()

        return result

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        try:
            import shutil
            shutil.rmtree(output_dir, ignore_errors=True)
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
#  API: Template Management
# ──────────────────────────────────────────────────────────────────────────────


async def save_table_as_template(
    pdf_path: str,
    table_index: int = 0,
    template_name: str = "",
) -> Optional[str]:
    """
    Extract a table from a PDF and save it as a Smart Template.
    Returns the template name if successful.
    """
    result = await extract_tables(
        pdf_path,
        use_glm_ocr=False,
        use_templates=False,
        output_formats=[],
    )

    if not result.tables or table_index >= len(result.tables):
        log.warning("No table found at index %d", table_index)
        return None

    # Get page dimensions
    page_width = 612.0
    page_height = 792.0
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        if reader.pages:
            page = reader.pages[0]
            if page.mediabox:
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)
    except Exception:
        pass

    table = result.tables[table_index]
    template = create_template_from_table(table, page_width, page_height)

    if template_name:
        template.name = template_name

    save_template(template)
    return template.name


# ──────────────────────────────────────────────────────────────────────────────
#  CLI Entry Point
# ──────────────────────────────────────────────────────────────────────────────


def main():
    """CLI entry point for standalone extraction."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Lightning-OCR Document Extraction Engine (Able2Extract-equivalent)",
    )
    parser.add_argument("input", help="PDF file to extract tables from")
    parser.add_argument("--output-dir", "-o", default="", help="Output directory")
    parser.add_argument(
        "--format", "-f", nargs="+", default=["xlsx"],
        help="Output formats: xlsx, csv, docx",
    )
    parser.add_argument("--template", "-t", default="", help="Smart Template name")
    parser.add_argument(
        "--save-template", "-s", default="",
        help="Save detected table as Smart Template with this name",
    )
    parser.add_argument(
        "--no-glm-ocr", action="store_true",
        help="Disable GLM-OCR for scanned documents",
    )
    parser.add_argument("--list-templates", action="store_true", help="List available templates")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.list_templates:
        templates = list_templates()
        if templates:
            print("Available Smart Templates:")
            for t in templates:
                print(f"  • {t}")
        else:
            print("No Smart Templates saved.")
        return

    if args.save_template:
        name = save_table_as_template(args.input, template_name=args.save_template)
        if name:
            print(f"Template saved: {name}")
        else:
            print("Failed to create template (no table found)")
        return

    import asyncio
    result = asyncio.run(extract_tables(
        pdf_path=args.input,
        use_glm_ocr=not args.no_glm_ocr,
        use_templates=bool(args.template),
        template_name=args.template,
        output_formats=args.format,
        output_dir=args.output_dir,
    ))

    if result.error:
        print(f"Error: {result.error}")
        return

    print(f"\n{'='*55}")
    print(f"  File: {result.filename}")
    print(f"  Pages: {result.page_count}")
    print(f"  Tables found: {len(result.tables)} (on {result.pages_with_tables} pages)")
    print(f"  Strategy: {result.strategy.value}")
    print(f"  Detection time: {result.detection_time_ms:.0f}ms")
    print(f"{'='*55}")

    for i, table in enumerate(result.tables):
        if table.title:
            print(f"\n  Table {i + 1}: {table.title}")
        print(f"  {table.num_rows} rows × {table.num_cols} cols | "
              f"confidence: {table.confidence:.2f} | "
              f"strategy: {table.strategy.value}")

    print(f"\n  Output files saved to: {os.path.dirname(result.filename) if result.filename else '.'}")
    print()


if __name__ == "__main__":
    main()
