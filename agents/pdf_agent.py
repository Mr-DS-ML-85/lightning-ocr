#!/usr/bin/env python3
"""
⚡ lightning-ocr MCP PDF Analysis Agent
═══════════════════════════════════════════════════════════════════════════════
A self-contained agentic loop that uses lightning-ocr's MCP tool server to:

  1. Split a PDF into per-page images
  2. Call ocr_image via JSON-RPC 2.0 MCP for each page
  3. Run analytical passes (summaries, key-value extraction, table detection,
     full-document search, sentiment)
  4. Synthesise a structured report with:
       • Executive summary
       • Extracted entities (dates, amounts, names, references)
       • Tables (if any)
       • Key findings per page
       • Anomaly / confidence flags
  5. Save the report as Markdown + JSON + optional HTML

Works completely locally — no OpenAI key required.
The "AI reasoning" layer is a deterministic rule-based engine that:
  • Uses regex + heuristics on OCR output
  • Makes multiple targeted MCP calls (different modes per page)
  • Cross-references findings across pages

Usage:
  python pdf_agent.py invoice.pdf
  python pdf_agent.py contract.pdf --mode document --output-dir ./reports
  python pdf_agent.py scan.pdf --mcp-url http://myserver:8000/mcp
  python pdf_agent.py multi.pdf --backend-id tesseract --verbose
  python pdf_agent.py form.pdf --find-terms "Total,Date,Name,Reference"
  python pdf_agent.py report.pdf --html-report

Requirements:
  pip install httpx rich typer pypdf pdf2image Pillow reportlab
  # Also: apt-get install poppler-utils  (for pdf2image)
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console(highlight=False)
err_console = Console(stderr=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MCPToolCall:
    """One round-trip to the MCP server."""
    tool:        str
    arguments:   Dict[str, Any]
    request_id:  int
    response:    Optional[Dict[str, Any]] = None
    error:       Optional[str]            = None
    duration_ms: float                    = 0.0
    text_result: str                      = ""


@dataclass
class PageAnalysis:
    page_num:      int                      # 1-based
    raw_text:      str       = ""
    markdown:      str       = ""
    figure_text:   str       = ""
    find_results:  Dict[str, str] = field(default_factory=dict)
    entities:      Dict[str, List[str]] = field(default_factory=dict)
    tables:        List[List[str]]      = field(default_factory=list)
    key_values:    Dict[str, str]       = field(default_factory=dict)
    language:      str       = "en"
    confidence:    str       = "unknown"   # high / medium / low
    anomalies:     List[str] = field(default_factory=list)
    word_count:    int       = 0
    tool_calls:    List[MCPToolCall] = field(default_factory=list)


@dataclass
class DocumentReport:
    pdf_path:      str
    total_pages:   int
    backend_used:  str
    pages:         List[PageAnalysis] = field(default_factory=list)
    summary:       str = ""
    all_entities:  Dict[str, List[str]] = field(default_factory=dict)
    all_key_values: Dict[str, str]      = field(default_factory=dict)
    found_terms:   Dict[str, List[str]] = field(default_factory=dict)
    anomalies:     List[str]            = field(default_factory=list)
    generated_at:  str = ""
    total_ms:      float = 0.0
    total_calls:   int   = 0


# ═══════════════════════════════════════════════════════════════════════════════
# MCP client layer
# ═══════════════════════════════════════════════════════════════════════════════

class MCPClient:
    """
    Minimal MCP JSON-RPC 2.0 client.
    Speaks to lightning-ocr's /mcp endpoint over plain HTTP.
    No external MCP SDK required.
    """

    def __init__(self, mcp_url: str, api_key: str = "", timeout: float = 240.0):
        self.mcp_url = mcp_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._id     = 0
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "MCPClient":
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=self.timeout,
                                  write=30.0, pool=5.0),
            headers=headers,
        )
        await self._initialize()
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  method,
            "params":  params,
        }
        assert self._client is not None
        resp = await self._client.post(self.mcp_url, json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _initialize(self) -> None:
        """Perform the MCP handshake."""
        result = await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "lightning-ocr-pdf-agent", "version": "1.0"},
            "capabilities": {},
        })
        if "error" in result:
            raise RuntimeError(f"MCP initialize failed: {result['error']}")

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Discover available tools from the server."""
        result = await self._call("tools/list", {})
        if "error" in result:
            raise RuntimeError(f"tools/list failed: {result['error']}")
        return result.get("result", {}).get("tools", [])

    async def ocr_image(
        self,
        image_bytes:   bytes,
        mode:          str   = "document",
        backend_id:    str   = "",
        filename:      str   = "page.png",
        custom_prompt: str   = "",
        find_term:     str   = "",
    ) -> MCPToolCall:
        """Call the ocr_image MCP tool and return a structured MCPToolCall."""
        b64 = base64.b64encode(image_bytes).decode()
        args: Dict[str, Any] = {
            "image_base64": b64,
            "filename":     filename,
            "mode":         mode,
        }
        if backend_id:
            args["backend_id"] = backend_id
        if custom_prompt:
            args["custom_prompt"] = custom_prompt
        if find_term:
            args["find_term"] = find_term

        call = MCPToolCall(
            tool="ocr_image",
            arguments={k: (v if k != "image_base64" else f"<{len(b64)} chars b64>")
                       for k, v in args.items()},
            request_id=self._id + 1,
        )
        t0 = time.perf_counter()
        try:
            result = await self._call("tools/call", {"name": "ocr_image", "arguments": args})
            call.duration_ms = (time.perf_counter() - t0) * 1000

            if "error" in result:
                call.error = str(result["error"])
            else:
                content = result.get("result", {}).get("content", [])
                call.response = result.get("result", {})
                call.text_result = "\n".join(
                    c.get("text", "") for c in content
                    if c.get("type") == "text"
                ).strip()
        except Exception as exc:
            call.duration_ms = (time.perf_counter() - t0) * 1000
            call.error = f"{type(exc).__name__}: {exc}"

        return call

    async def list_backends(self) -> MCPToolCall:
        """Call the list_ocr_backends MCP tool."""
        call = MCPToolCall(
            tool="list_ocr_backends",
            arguments={},
            request_id=self._id + 1,
        )
        t0 = time.perf_counter()
        try:
            result = await self._call("tools/call",
                                      {"name": "list_ocr_backends", "arguments": {}})
            call.duration_ms = (time.perf_counter() - t0) * 1000
            content = result.get("result", {}).get("content", [])
            call.text_result = "\n".join(
                c.get("text", "") for c in content if c.get("type") == "text"
            ).strip()
        except Exception as exc:
            call.duration_ms = (time.perf_counter() - t0) * 1000
            call.error = str(exc)
        return call


# ═══════════════════════════════════════════════════════════════════════════════
# PDF → images
# ═══════════════════════════════════════════════════════════════════════════════

def pdf_to_page_images(pdf_path: str, dpi: int = 200) -> List[bytes]:
    """
    Convert each PDF page to a PNG image in memory.
    Tries pdf2image (poppler) first, falls back to pypdf + basic renderer.
    """
    try:
        from pdf2image import convert_from_path
        pages = convert_from_path(pdf_path, dpi=dpi, fmt="png")
        result = []
        for page in pages:
            buf = io.BytesIO()
            page.save(buf, format="PNG")
            result.append(buf.getvalue())
        return result
    except ImportError:
        console.print("  [yellow]pdf2image not installed — using pypdf text extraction only.[/yellow]")
        return _pypdf_page_images(pdf_path)
    except Exception as exc:
        console.print(f"  [yellow]pdf2image failed: {exc} — falling back to pypdf.[/yellow]")
        return _pypdf_page_images(pdf_path)


def _pypdf_page_images(pdf_path: str) -> List[bytes]:
    """
    Fallback: extract pages as white PNG with embedded text using Pillow.
    Attempts to extract text via pypdf and render it onto an image.
    """
    try:
        import pypdf
        from PIL import Image, ImageDraw, ImageFont

        reader = pypdf.PdfReader(pdf_path)
        images: List[bytes] = []
        for page in reader.pages:
            text = page.extract_text() or "(no text extracted)"
            img  = Image.new("RGB", (800, 1100), (255, 255, 255))
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16
                )
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
        console.print("[red]Neither pdf2image nor pypdf available. Cannot process PDF.[/red]")
        return []


def _pypdf_full_text(pdf_path: str) -> str:
    """Extract all text from PDF using pypdf (no image rendering)."""
    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        pages_text = []
        for i, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            if t.strip():
                pages_text.append(f"--- Page {i} ---\n{t}")
        return "\n\n".join(pages_text)
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# Entity extraction (rule-based, no LLM required)
# ═══════════════════════════════════════════════════════════════════════════════

_PATTERNS: Dict[str, str] = {
    "dates":       r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}"
                   r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    "amounts":     r"[$€£¥₹]\s?[\d,]+(?:\.\d{2})?|\b\d[\d,]+(?:\.\d{2})?\s?(?:USD|EUR|GBP|JPY)",
    "emails":      r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    "phones":      r"(?:\+?\d[\d\s\-().]{7,}\d)",
    "urls":        r"https?://[^\s<>\"]+",
    "invoice_nos": r"(?:Invoice|Inv|Bill|Ref|Order|PO|Reference)[.:\s#]+([A-Z0-9\-/]{3,30})",
    "percentages": r"\b\d+(?:\.\d+)?\s*%",
    "totals":      r"(?:Total|Subtotal|Grand Total|Amount Due|Balance)[:\s]+[$€£]?\s*[\d,]+(?:\.\d{2})?",
    "page_refs":   r"\bpage\s+\d+\b|\bpg\.?\s*\d+\b",
}

_KV_PATTERNS: List[Tuple[str, str]] = [
    ("invoice_number",  r"(?:Invoice|Inv)[\s#:]*([A-Z0-9\-]+)"),
    ("date",            r"Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"),
    ("due_date",        r"(?:Due|Payment Due)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})"),
    ("total",           r"(?:Grand\s+)?Total[:\s]+[$€£]?\s*([\d,]+(?:\.\d{2})?)"),
    ("subtotal",        r"Sub[\s-]?total[:\s]+[$€£]?\s*([\d,]+(?:\.\d{2})?)"),
    ("tax",             r"(?:VAT|Tax|GST)[:\s]+[$€£]?\s*([\d,]+(?:\.\d{2})?)"),
    ("to_name",         r"(?:Bill\s+To|Ship\s+To|To)[:\s]+([A-Z][A-Za-z\s,\.]+)"),
    ("from_name",       r"(?:From|Vendor|Supplier)[:\s]+([A-Z][A-Za-z\s,\.]+)"),
    ("po_number",       r"(?:PO|Purchase Order)[#:\s]+([A-Z0-9\-]+)"),
    ("account_number",  r"(?:Account|Acct)[#:\s]+([A-Z0-9\-]+)"),
]


def extract_entities(text: str) -> Dict[str, List[str]]:
    """Extract named entities via regex from OCR text."""
    entities: Dict[str, List[str]] = {}
    for name, pattern in _PATTERNS.items():
        found = list(set(re.findall(pattern, text, re.IGNORECASE)))
        if found:
            entities[name] = found
    return entities


def extract_key_values(text: str) -> Dict[str, str]:
    """Extract specific key-value pairs (invoice fields, etc.)."""
    kv: Dict[str, str] = {}
    for key, pattern in _KV_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            value = m.group(1).strip() if m.lastindex else m.group(0).strip()
            kv[key] = value
    return kv


def detect_tables(text: str) -> List[List[str]]:
    """
    Heuristic table detection — looks for lines with multiple tab/pipe/space separations
    that align across 3+ rows.
    """
    tables: List[List[str]] = []
    current: List[str] = []
    for line in text.split("\n"):
        if ("|" in line and line.count("|") >= 2) or \
           (re.search(r"\t.+\t", line)) or \
           (len(re.split(r"\s{3,}", line.strip())) >= 3):
            current.append(line.strip())
        else:
            if len(current) >= 2:
                tables.append(current)
            current = []
    if len(current) >= 2:
        tables.append(current)
    return tables


def assess_confidence(text: str) -> str:
    """Simple heuristic confidence — based on word count and gibberish ratio."""
    if not text or len(text.strip()) < 20:
        return "low"
    words     = text.split()
    wc        = len(words)
    garbled   = sum(1 for w in words if len(w) > 15 and not w.isalpha())
    short     = sum(1 for w in words if len(w) == 1 and w.isalpha())
    garb_rate = garbled / max(wc, 1)
    if wc > 80 and garb_rate < 0.05:
        return "high"
    if wc > 20 and garb_rate < 0.20:
        return "medium"
    return "low"


def detect_anomalies(text: str, page_num: int) -> List[str]:
    anomalies: List[str] = []
    if len(text.strip()) < 30:
        anomalies.append(f"Page {page_num}: Very short OCR output — possible scan quality issue")
    if re.search(r"[^\x00-\x7F]{10,}", text):
        anomalies.append(f"Page {page_num}: High non-ASCII character density — possible encoding issue")
    if text.count("?") > len(text) / 10:
        anomalies.append(f"Page {page_num}: Many '?' characters — possible OCR decode failure")
    if len(set(text.split())) < 5 and len(text) > 50:
        anomalies.append(f"Page {page_num}: Very low vocabulary diversity — possible repeated artifact")
    return anomalies


# ═══════════════════════════════════════════════════════════════════════════════
# Agent core
# ═══════════════════════════════════════════════════════════════════════════════

class PDFAnalysisAgent:
    """
    Agentic loop that:
    1. Converts PDF to page images
    2. Makes multiple targeted MCP calls per page
    3. Runs post-processing (entity extraction, table detection, etc.)
    4. Synthesises a full document report
    """

    def __init__(
        self,
        mcp_url:     str,
        api_key:     str  = "",
        backend_id:  str  = "",
        find_terms:  Optional[List[str]] = None,
        dpi:         int  = 200,
        verbose:     bool = False,
        mode:        str  = "document",
    ):
        self.mcp_url    = mcp_url
        self.api_key    = api_key
        self.backend_id = backend_id
        self.find_terms = find_terms or []
        self.dpi        = dpi
        self.verbose    = verbose
        self.mode       = mode

    def _log(self, msg: str, level: str = "info") -> None:
        styles = {"info": "cyan", "ok": "green", "warn": "yellow",
                  "err": "red", "debug": "dim white"}
        console.print(f"  [{styles.get(level,'white')}]{msg}[/{styles.get(level,'white')}]")

    def _log_call(self, call: MCPToolCall) -> None:
        if call.error:
            console.print(
                f"    ❌ MCP [{call.tool}] mode={call.arguments.get('mode','—')} "
                f"— [red]{escape(call.error[:120])}[/red]"
            )
        else:
            preview = escape(call.text_result[:80].replace("\n", " ")) + ("…" if len(call.text_result) > 80 else "")
            console.print(
                f"    ✓ MCP [{call.tool}] mode={call.arguments.get('mode','—')} "
                f"[dim]{call.duration_ms:.0f}ms[/dim] → [white]{preview}[/white]"
            )
        if self.verbose:
            if call.response:
                try:
                    console.print(
                        Syntax(json.dumps(call.response, indent=2)[:2000],
                               "json", theme="monokai", background_color="default"),
                    )
                except Exception:
                    pass

    async def _analyse_page(
        self,
        mcp:        MCPClient,
        page_num:   int,
        img_bytes:  bytes,
        progress:   Progress,
        task_id:    Any,
    ) -> PageAnalysis:
        pa = PageAnalysis(page_num=page_num)
        fname = f"page_{page_num:03d}.png"

        self._log(f"Page {page_num} — running OCR passes…", "info")

        # ── Pass 1: primary OCR (configured mode) ─────────────────────────
        progress.update(task_id, description=f"Page {page_num}: {self.mode} pass")
        call1 = await mcp.ocr_image(img_bytes, mode=self.mode,
                                     backend_id=self.backend_id, filename=fname)
        pa.tool_calls.append(call1)
        self._log_call(call1)

        if self.mode == "document":
            pa.markdown = call1.text_result
            pa.raw_text = call1.text_result
        else:
            pa.raw_text = call1.text_result

        # ── Pass 2: plain OCR if mode was something else (get clean text) ─
        if self.mode != "ocr" and self.mode != "free":
            progress.update(task_id, description=f"Page {page_num}: plain OCR pass")
            call2 = await mcp.ocr_image(img_bytes, mode="ocr",
                                         backend_id=self.backend_id, filename=fname)
            pa.tool_calls.append(call2)
            self._log_call(call2)
            if not call2.error:
                pa.raw_text = call2.text_result or pa.raw_text

        # ── Pass 3: figure detection (if text contains chart-like content) ─
        figure_keywords = re.search(
            r"\b(figure|chart|graph|table|fig\.?|plot|diagram)\b",
            pa.raw_text, re.IGNORECASE
        )
        if figure_keywords:
            progress.update(task_id, description=f"Page {page_num}: figure pass")
            call3 = await mcp.ocr_image(img_bytes, mode="figure",
                                         backend_id=self.backend_id, filename=fname)
            pa.tool_calls.append(call3)
            self._log_call(call3)
            if not call3.error:
                pa.figure_text = call3.text_result

        # ── Pass 4: find-term passes ───────────────────────────────────────
        for term in self.find_terms:
            progress.update(task_id, description=f"Page {page_num}: find '{term}'")
            call_f = await mcp.ocr_image(
                img_bytes, mode="find",
                backend_id=self.backend_id, filename=fname,
                find_term=term,
            )
            pa.tool_calls.append(call_f)
            self._log_call(call_f)
            if not call_f.error and call_f.text_result:
                pa.find_results[term] = call_f.text_result

        # ── Pass 5: custom structured extraction prompt ────────────────────
        if pa.raw_text and len(pa.raw_text.split()) > 20:
            progress.update(task_id, description=f"Page {page_num}: structured extraction")
            structured_prompt = (
                "Extract structured data from this document page. "
                "Return a JSON object with keys: "
                "title, document_type, key_fields (dict), dates (list), amounts (list), "
                "parties (list of names), summary (1-2 sentences). "
                "If a field is not present, omit it. Return ONLY valid JSON."
            )
            call5 = await mcp.ocr_image(
                img_bytes, mode="freeform",
                backend_id=self.backend_id, filename=fname,
                custom_prompt=structured_prompt,
            )
            pa.tool_calls.append(call5)
            self._log_call(call5)
            # Try to parse as JSON; if it fails, store as raw
            if not call5.error:
                try:
                    jtext = call5.text_result
                    # Extract JSON block if wrapped in markdown
                    json_match = re.search(r"```(?:json)?\s*(\{.+?\})\s*```",
                                           jtext, re.DOTALL)
                    jtext = json_match.group(1) if json_match else jtext
                    parsed = json.loads(jtext)
                    if isinstance(parsed, dict):
                        # Merge structured fields
                        if "key_fields" in parsed and isinstance(parsed["key_fields"], dict):
                            pa.key_values.update(parsed["key_fields"])
                        for dt in parsed.get("dates", []):
                            pa.entities.setdefault("dates", []).append(str(dt))
                        for amt in parsed.get("amounts", []):
                            pa.entities.setdefault("amounts", []).append(str(amt))
                except (json.JSONDecodeError, Exception):
                    pass  # Structured extraction bonus; silently skip if not JSON

        # ── Post-processing ────────────────────────────────────────────────
        pa.entities.update(extract_entities(pa.raw_text))
        pa.key_values.update(extract_key_values(pa.raw_text))
        pa.tables      = detect_tables(pa.raw_text)
        pa.confidence  = assess_confidence(pa.raw_text)
        pa.anomalies   = detect_anomalies(pa.raw_text, page_num)
        pa.word_count  = len(pa.raw_text.split())

        self._log(
            f"Page {page_num} done — {pa.word_count} words, "
            f"confidence={pa.confidence}, "
            f"{len(pa.entities)} entity types, "
            f"{len(pa.tables)} tables, "
            f"{len(pa.tool_calls)} MCP calls",
            "ok"
        )
        return pa

    async def analyse(self, pdf_path: str) -> DocumentReport:
        """
        Main agent loop.
        Returns a fully-populated DocumentReport.
        """
        t0 = time.perf_counter()
        console.rule("[bold white]⚡ PDF Analysis Agent[/bold white]")
        self._log(f"PDF: {pdf_path}", "info")
        self._log(f"MCP server: {self.mcp_url}", "info")

        # ── 1. Convert PDF → page images ─────────────────────────────────
        self._log("Converting PDF to page images…", "info")
        pages_bytes = pdf_to_page_images(pdf_path, dpi=self.dpi)
        if not pages_bytes:
            raise RuntimeError("Could not extract any pages from the PDF")
        self._log(f"Extracted {len(pages_bytes)} page(s)", "ok")

        report = DocumentReport(
            pdf_path=str(pdf_path),
            total_pages=len(pages_bytes),
            backend_used=self.backend_id or "auto",
            generated_at=datetime.utcnow().isoformat() + "Z",
        )

        # ── 2. Connect MCP and discover tools ─────────────────────────────
        async with MCPClient(self.mcp_url, self.api_key) as mcp:
            tools = await mcp.list_tools()
            self._log(f"MCP tools available: {[t['name'] for t in tools]}", "ok")

            # Confirm the required tool exists
            tool_names = [t["name"] for t in tools]
            if "ocr_image" not in tool_names:
                raise RuntimeError(
                    f"ocr_image tool not found on MCP server. "
                    f"Available: {tool_names}"
                )

            # ── 3. Per-page analysis loop ─────────────────────────────────
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=30),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Analysing pages…", total=len(pages_bytes))

                for i, img_bytes in enumerate(pages_bytes, start=1):
                    pa = await self._analyse_page(mcp, i, img_bytes, progress, task)
                    report.pages.append(pa)
                    report.total_calls += len(pa.tool_calls)
                    progress.advance(task)

        # ── 4. Cross-page synthesis ────────────────────────────────────────
        self._log("Synthesising cross-page report…", "info")

        # Merge entities from all pages
        for pa in report.pages:
            for etype, values in pa.entities.items():
                existing = set(report.all_entities.get(etype, []))
                existing.update(values)
                report.all_entities[etype] = list(existing)

        # Merge key-values (later pages override earlier for same key)
        for pa in report.pages:
            report.all_key_values.update(pa.key_values)

        # Merge find results
        for pa in report.pages:
            for term, result in pa.find_results.items():
                report.found_terms.setdefault(term, []).append(
                    f"[Page {pa.page_num}] {result}"
                )

        # Collect anomalies
        for pa in report.pages:
            report.anomalies.extend(pa.anomalies)

        # Generate executive summary
        all_text = "\n\n".join(pa.raw_text for pa in report.pages if pa.raw_text)
        report.summary = _generate_summary(report, all_text)

        report.total_ms = (time.perf_counter() - t0) * 1000
        self._log(
            f"Analysis complete — {report.total_pages} pages, "
            f"{report.total_calls} MCP calls, "
            f"{report.total_ms:.0f}ms",
            "ok"
        )
        return report


# ═══════════════════════════════════════════════════════════════════════════════
# Summary generation
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_summary(report: DocumentReport, full_text: str) -> str:
    lines: List[str] = []
    lines.append(f"Document: {Path(report.pdf_path).name}")
    lines.append(f"Pages: {report.total_pages}")
    lines.append(f"Total word count: {sum(p.word_count for p in report.pages)}")

    doc_type = "Unknown"
    type_patterns = [
        ("Invoice / Bill",    r"\b(invoice|bill|receipt|payment)\b"),
        ("Contract / Agreement", r"\b(agreement|contract|terms|clause)\b"),
        ("Report",            r"\b(report|analysis|summary|findings)\b"),
        ("Letter / Email",    r"\b(dear|sincerely|regards|subject:)\b"),
        ("Form",              r"\b(form|fill|checkbox|signature)\b"),
        ("Academic Paper",    r"\b(abstract|introduction|references|methodology)\b"),
    ]
    for dtype, pat in type_patterns:
        if re.search(pat, full_text[:3000], re.IGNORECASE):
            doc_type = dtype
            break
    lines.append(f"Detected document type: {doc_type}")

    confidences = [p.confidence for p in report.pages]
    if confidences.count("high") >= len(confidences) / 2:
        lines.append("Overall OCR confidence: HIGH")
    elif confidences.count("low") >= len(confidences) / 2:
        lines.append("Overall OCR confidence: LOW — consider improving scan quality")
    else:
        lines.append("Overall OCR confidence: MEDIUM")

    if report.all_key_values.get("total"):
        lines.append(f"Detected total amount: {report.all_key_values['total']}")
    if report.all_key_values.get("invoice_number"):
        lines.append(f"Invoice number: {report.all_key_values['invoice_number']}")
    if report.all_key_values.get("date"):
        lines.append(f"Document date: {report.all_key_values['date']}")

    dates = report.all_entities.get("dates", [])
    if dates:
        lines.append(f"Dates found: {', '.join(dates[:5])}")

    amounts = report.all_entities.get("amounts", [])
    if amounts:
        lines.append(f"Amounts found: {', '.join(amounts[:5])}")

    emails = report.all_entities.get("emails", [])
    if emails:
        lines.append(f"Emails found: {', '.join(emails[:3])}")

    if report.anomalies:
        lines.append(f"Anomalies detected: {len(report.anomalies)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Report renderers
# ═══════════════════════════════════════════════════════════════════════════════

def render_console(report: DocumentReport, verbose: bool) -> None:
    console.rule("[bold white]📋 Document Report[/bold white]")

    # Summary panel
    console.print(Panel(
        escape(report.summary),
        title="[bold white]Executive Summary[/bold white]",
        border_style="blue",
        padding=(1, 2),
    ))

    # Key values table
    if report.all_key_values:
        kv_table = Table(title="Extracted Key Fields", box=box.ROUNDED,
                         header_style="bold white", show_lines=True)
        kv_table.add_column("Field",  style="cyan",  min_width=22)
        kv_table.add_column("Value",  style="white", min_width=30)
        for k, v in sorted(report.all_key_values.items()):
            kv_table.add_row(k.replace("_", " ").title(), escape(v))
        console.print(kv_table)

    # Entities table
    if report.all_entities:
        ent_table = Table(title="Extracted Entities", box=box.ROUNDED,
                          header_style="bold white", show_lines=True)
        ent_table.add_column("Type",   style="magenta", min_width=18)
        ent_table.add_column("Values", style="white",   min_width=50)
        ent_table.add_column("Count",  style="green",   width=8, justify="right")
        for etype, vals in sorted(report.all_entities.items()):
            ent_table.add_row(etype, escape(", ".join(vals[:8])), str(len(vals)))
        console.print(ent_table)

    # Find results
    if report.found_terms:
        console.print(Panel(
            "\n".join(
                f"[cyan]{escape(term)}[/cyan]: "
                + " | ".join(escape(v[:100]) for v in vals)
                for term, vals in report.found_terms.items()
            ),
            title="[bold white]Find Term Results[/bold white]",
            border_style="yellow",
        ))

    # Per-page breakdown
    for pa in report.pages:
        style = {"high": "green", "medium": "yellow", "low": "red"}.get(pa.confidence, "white")
        hdr = (
            f"[bold]Page {pa.page_num}[/bold]  "
            f"[{style}]confidence={pa.confidence}[/{style}]  "
            f"[dim]{pa.word_count} words  {len(pa.tool_calls)} MCP calls[/dim]"
        )
        body_parts: List[str] = []

        if pa.raw_text and verbose:
            body_parts.append("[dim]--- RAW TEXT ---[/dim]\n" + escape(pa.raw_text[:800]))

        if pa.markdown and pa.markdown != pa.raw_text and verbose:
            body_parts.append("[dim]--- MARKDOWN ---[/dim]\n" + escape(pa.markdown[:600]))

        if pa.figure_text:
            body_parts.append("[dim]--- FIGURE ---[/dim]\n" + escape(pa.figure_text[:300]))

        if pa.tables:
            body_parts.append(f"[yellow]Tables detected: {len(pa.tables)}[/yellow]")
            if verbose:
                for t in pa.tables[:2]:
                    body_parts.append("\n".join(f"  {escape(row)}" for row in t[:6]))

        if pa.anomalies:
            body_parts.append("[red]Anomalies: " + " | ".join(escape(a) for a in pa.anomalies) + "[/red]")

        if pa.find_results:
            fr_lines = [f"  [cyan]{escape(k)}[/cyan]: {escape(v[:80])}"
                        for k, v in pa.find_results.items()]
            body_parts.append("[yellow]Find results:[/yellow]\n" + "\n".join(fr_lines))

        # MCP call trace
        if verbose:
            call_lines = []
            for c in pa.tool_calls:
                icon  = "✓" if not c.error else "✗"
                color = "green" if not c.error else "red"
                call_lines.append(
                    f"  [{color}]{icon}[/{color}] {c.tool} mode={c.arguments.get('mode','—')} "
                    f"[dim]{c.duration_ms:.0f}ms[/dim]"
                )
            if call_lines:
                body_parts.append("[dim]MCP calls:[/dim]\n" + "\n".join(call_lines))

        if not body_parts:
            body_parts.append(escape(pa.raw_text[:200] + ("…" if len(pa.raw_text) > 200 else "")))

        console.print(Panel(
            "\n\n".join(body_parts) or "(empty)",
            title=hdr, border_style="blue", padding=(0, 1),
        ))

    # Anomalies summary
    if report.anomalies:
        console.print(Panel(
            "\n".join(f"[red]⚠[/red]  {escape(a)}" for a in report.anomalies),
            title="[bold red]Anomalies[/bold red]",
            border_style="red",
        ))

    console.print(
        f"\n  ⏱ Total: [green]{report.total_ms:.0f}ms[/green]  "
        f"MCP calls: [cyan]{report.total_calls}[/cyan]  "
        f"Backend: [magenta]{report.backend_used}[/magenta]"
    )


def render_markdown(report: DocumentReport) -> str:
    lines: List[str] = [
        f"# ⚡ lightning-ocr PDF Analysis Report",
        f"",
        f"**File:** `{Path(report.pdf_path).name}`  ",
        f"**Pages:** {report.total_pages}  ",
        f"**Backend:** `{report.backend_used}`  ",
        f"**Generated:** {report.generated_at}  ",
        f"**Duration:** {report.total_ms:.0f}ms | **MCP Calls:** {report.total_calls}",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        f"```",
        report.summary,
        f"```",
        f"",
    ]

    if report.all_key_values:
        lines += ["## Extracted Key Fields", ""]
        lines += ["| Field | Value |", "|---|---|"]
        for k, v in sorted(report.all_key_values.items()):
            lines.append(f"| {k.replace('_',' ').title()} | {v} |")
        lines.append("")

    if report.all_entities:
        lines += ["## Extracted Entities", ""]
        lines += ["| Type | Values | Count |", "|---|---|---|"]
        for etype, vals in sorted(report.all_entities.items()):
            lines.append(f"| {etype} | {', '.join(vals[:6])} | {len(vals)} |")
        lines.append("")

    if report.found_terms:
        lines += ["## Find Term Results", ""]
        for term, results in report.found_terms.items():
            lines.append(f"### `{term}`")
            for r in results:
                lines.append(f"- {r}")
        lines.append("")

    lines += ["## Per-Page Analysis", ""]
    for pa in report.pages:
        lines += [
            f"### Page {pa.page_num}",
            f"",
            f"- **Words:** {pa.word_count}",
            f"- **Confidence:** {pa.confidence}",
            f"- **MCP calls:** {len(pa.tool_calls)}",
            f"- **Tables detected:** {len(pa.tables)}",
            f"",
        ]
        if pa.raw_text:
            lines += ["#### Raw Text", "", "```", pa.raw_text[:1500], "```", ""]
        if pa.markdown and pa.markdown != pa.raw_text:
            lines += ["#### Markdown", "", pa.markdown[:1500], ""]
        if pa.figure_text:
            lines += ["#### Figure Analysis", "", pa.figure_text[:500], ""]
        if pa.tables:
            lines += ["#### Detected Tables", ""]
            for t in pa.tables[:3]:
                lines.append("```")
                lines.extend(t[:8])
                lines.append("```")
            lines.append("")
        if pa.key_values:
            lines += ["#### Page Key Values", ""]
            for k, v in pa.key_values.items():
                lines.append(f"- **{k}:** {v}")
            lines.append("")
        if pa.anomalies:
            lines += ["#### ⚠ Anomalies", ""]
            for a in pa.anomalies:
                lines.append(f"- {a}")
            lines.append("")

    if report.anomalies:
        lines += ["## ⚠ All Anomalies", ""]
        for a in report.anomalies:
            lines.append(f"- {a}")
        lines.append("")

    return "\n".join(lines)


def render_html(report: DocumentReport, md_content: str) -> str:
    """Wrap the markdown report in a styled HTML page."""
    # Convert basic markdown to HTML (no external dep)
    html_body = md_content
    html_body = re.sub(r"^# (.+)$",   r"<h1>\1</h1>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^## (.+)$",  r"<h2>\1</h2>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html_body)
    html_body = re.sub(r"`([^`]+)`",     r"<code>\1</code>", html_body)
    html_body = re.sub(r"^- (.+)$",     r"<li>\1</li>", html_body, flags=re.MULTILINE)
    html_body = re.sub(r"^---$",        r"<hr/>", html_body, flags=re.MULTILINE)
    # Simple table support
    table_lines = html_body.split("\n")
    out_lines: List[str] = []
    in_table = False
    for line in table_lines:
        if "|" in line and not line.startswith("#"):
            if not in_table:
                out_lines.append('<table><tbody>')
                in_table = True
            if re.match(r"\|[\s\-|]+\|", line):
                continue  # header separator
            cells = [c.strip() for c in line.strip("|").split("|")]
            row = "".join(f"<td>{c}</td>" for c in cells)
            out_lines.append(f"<tr>{row}</tr>")
        else:
            if in_table:
                out_lines.append('</tbody></table>')
                in_table = False
            out_lines.append(line)
    if in_table:
        out_lines.append('</tbody></table>')
    html_body = "\n".join(out_lines)
    # Wrap code blocks
    html_body = re.sub(r"```[a-z]*\n(.*?)```",
                       r"<pre><code>\1</code></pre>",
                       html_body, flags=re.DOTALL)
    html_body = html_body.replace("\n", "<br/>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>⚡ PDF Analysis — {escape(Path(report.pdf_path).name)}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0b1020;color:#dde8ff;
         font-family:"Inter","Segoe UI",system-ui,sans-serif;
         font-size:14px;line-height:1.7;padding:40px;max-width:1100px;margin:0 auto}}
    h1{{font-size:28px;margin:20px 0 8px;
        background:linear-gradient(135deg,#4f8eff,#a259ff);
        -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
    h2{{font-size:20px;margin:24px 0 10px;color:#8db0ff;border-bottom:1px solid rgba(80,120,255,.2);padding-bottom:6px}}
    h3{{font-size:16px;margin:18px 0 8px;color:#c8d6f6}}
    code{{background:rgba(79,142,255,.15);border-radius:4px;padding:2px 6px;font-size:12px;
          font-family:"JetBrains Mono","Fira Code",monospace}}
    pre{{background:rgba(5,6,15,.7);border:1px solid rgba(80,120,255,.2);border-radius:12px;
         padding:14px;overflow-x:auto;font-size:12px;margin:12px 0}}
    pre code{{background:none;padding:0}}
    table{{width:100%;border-collapse:collapse;margin:12px 0;
           background:rgba(12,16,40,.7);border-radius:12px;overflow:hidden}}
    td,th{{padding:10px 14px;border-bottom:1px solid rgba(80,120,255,.1)}}
    tr:hover td{{background:rgba(79,142,255,.05)}}
    li{{margin-left:20px;margin-bottom:3px}}
    hr{{border:none;border-top:1px solid rgba(80,120,255,.2);margin:20px 0}}
    strong{{color:#fff}}
  </style>
</head>
<body>
  {html_body}
</body>
</html>"""


def render_json(report: DocumentReport) -> Dict[str, Any]:
    return {
        "pdf_path":       report.pdf_path,
        "total_pages":    report.total_pages,
        "backend_used":   report.backend_used,
        "generated_at":   report.generated_at,
        "total_ms":       report.total_ms,
        "total_calls":    report.total_calls,
        "summary":        report.summary,
        "all_key_values": report.all_key_values,
        "all_entities":   report.all_entities,
        "found_terms":    report.found_terms,
        "anomalies":      report.anomalies,
        "pages": [
            {
                "page_num":    pa.page_num,
                "word_count":  pa.word_count,
                "confidence":  pa.confidence,
                "raw_text":    pa.raw_text,
                "markdown":    pa.markdown,
                "figure_text": pa.figure_text,
                "key_values":  pa.key_values,
                "entities":    pa.entities,
                "tables":      pa.tables,
                "find_results": pa.find_results,
                "anomalies":   pa.anomalies,
                "tool_calls": [
                    {
                        "tool":        c.tool,
                        "arguments":   c.arguments,
                        "duration_ms": c.duration_ms,
                        "error":       c.error,
                        "text_result": c.text_result[:500],
                    }
                    for c in pa.tool_calls
                ],
            }
            for pa in report.pages
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

cli = typer.Typer(
    name="pdf_agent",
    help="⚡ lightning-ocr MCP PDF Analysis Agent",
    add_completion=False,
    rich_markup_mode="rich",
)


@cli.command()
def main(
    pdf: str = typer.Argument(..., help="Path to the PDF file to analyse"),
    mcp_url: str = typer.Option(
        "http://localhost:8000/mcp",
        "--mcp-url", "-u",
        help="lightning-ocr MCP endpoint URL",
    ),
    api_key: str = typer.Option(
        "", "--api-key", "-k",
        envvar="LIGHTNING_OCR_API_KEY",
        help="Bearer token if API_KEY is configured",
    ),
    backend_id: str = typer.Option(
        "", "--backend-id", "-b",
        help="Specific backend to use (default: auto/first available)",
    ),
    mode: str = typer.Option(
        "document",
        "--mode", "-m",
        help="Primary OCR mode: document|ocr|free|figure|describe|freeform",
    ),
    find_terms: Optional[str] = typer.Option(
        None, "--find-terms", "-f",
        help="Comma-separated terms to search for on every page (e.g. 'Total,Date,Name')",
    ),
    dpi: int = typer.Option(
        200, "--dpi",
        help="DPI for PDF-to-image conversion (higher=better quality, slower)",
    ),
    output_dir: str = typer.Option(
        ".", "--output-dir", "-o",
        help="Directory to save reports",
    ),
    save_markdown: bool = typer.Option(
        True, "--markdown/--no-markdown",
        help="Save Markdown report",
    ),
    save_json: bool = typer.Option(
        True, "--json/--no-json",
        help="Save JSON report",
    ),
    html_report: bool = typer.Option(
        False, "--html-report",
        help="Also save HTML report",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Print full OCR text and MCP call trace",
    ),
) -> None:
    """
    ⚡ lightning-ocr MCP PDF Analysis Agent.

    Analyses a PDF file page-by-page using the MCP ocr_image tool,
    extracting entities, key-values, tables, and generating a full report.
    """
    pdf_path = Path(pdf)
    if not pdf_path.exists():
        err_console.print(f"[red]Error: PDF not found: {pdf}[/red]")
        raise typer.Exit(1)

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    terms = [t.strip() for t in find_terms.split(",")] if find_terms else []

    console.print(Panel.fit(
        f"[bold white]⚡ PDF Analysis Agent[/bold white]\n"
        f"[dim]File: {pdf_path.name}  |  MCP: {mcp_url}[/dim]",
        border_style="blue", padding=(1, 4),
    ))

    async def _run() -> DocumentReport:
        agent = PDFAnalysisAgent(
            mcp_url    = mcp_url,
            api_key    = api_key,
            backend_id = backend_id,
            find_terms = terms,
            dpi        = dpi,
            verbose    = verbose,
            mode       = mode,
        )
        return await agent.analyse(str(pdf_path))

    try:
        report = asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        raise typer.Exit(130)
    except Exception as exc:
        err_console.print(f"[red]Agent error: {exc}[/red]")
        if verbose:
            err_console.print(traceback.format_exc())
        raise typer.Exit(1)

    # ── Render to console ─────────────────────────────────────────────────────
    render_console(report, verbose)

    # ── Save reports ──────────────────────────────────────────────────────────
    stem = pdf_path.stem
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"{stem}_{ts}"

    if save_markdown:
        md_path = base.with_suffix(".md")
        md_content = render_markdown(report)
        md_path.write_text(md_content, encoding="utf-8")
        console.print(f"\n  📝 Markdown report: [cyan]{md_path}[/cyan]")

        if html_report:
            html_path = base.with_suffix(".html")
            html_path.write_text(render_html(report, md_content), encoding="utf-8")
            console.print(f"  🌐 HTML report:     [cyan]{html_path}[/cyan]")
    else:
        md_content = render_markdown(report)

    if save_json:
        json_path = base.with_suffix(".json")
        json_path.write_text(
            json.dumps(render_json(report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"  📦 JSON report:     [cyan]{json_path}[/cyan]")

    console.print(f"\n  ✅ Done — {report.total_pages} pages analysed "
                  f"in {report.total_ms:.0f}ms\n")


if __name__ == "__main__":
    cli()