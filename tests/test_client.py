#!/usr/bin/env python3
"""
⚡ lightning-ocr — Full API Test Client
═══════════════════════════════════════════════════════════════════════════════
Tests every endpoint with full debug output, timing, assertion reporting,
coloured Rich console output, JSON dump option, and HTML report generation.

Usage examples:
  python test_client.py                          # test localhost:8000, all tests
  python test_client.py --url http://myhost:8000 # custom host
  python test_client.py --test health            # single test
  python test_client.py --image myfile.png       # use real image
  python test_client.py --api-key mysecret       # auth header
  python test_client.py --json-output results.json
  python test_client.py --html-report report.html
  python test_client.py --verbose                # show full response bodies
  python test_client.py --no-color               # plain output (CI mode)

Covered endpoints:
  GET  /                          HTML dashboard
  GET  /api/models                Backend list
  GET  /api/health                Health check all backends
  POST /api/ocr                   Single-file OCR (all 7 modes)
  POST /api/ocr/batch             Batch OCR (multiple files)
  GET  /api/history               Job history (pagination)
  GET  /api/history/{id}          Single job fetch
  DELETE /api/history/{id}        Delete job
  POST /mcp                       MCP initialize handshake
  POST /mcp (tools/list)          MCP tool discovery
  POST /mcp (tools/call ocr)      MCP OCR tool call
  POST /mcp (list_backends)       MCP backend list tool
  GET  /.well-known/mcp           MCP discovery manifest
  GET  /docs                      Swagger UI
  GET  /openapi.json              OpenAPI schema
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import typer
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


# ── Theme ─────────────────────────────────────────────────────────────────────
THEME = Theme({
    "pass":   "bold green",
    "fail":   "bold red",
    "skip":   "bold yellow",
    "warn":   "yellow",
    "info":   "bold cyan",
    "debug":  "dim white",
    "header": "bold white on blue",
    "url":    "underline cyan",
    "method": "bold magenta",
    "ms":     "bold green",
    "title":  "bold white",
})

console = Console(theme=THEME, highlight=False)
err_console = Console(stderr=True, theme=THEME)


# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class TestResult:
    name:        str
    endpoint:    str
    method:      str
    status_code: Optional[int]  = None
    passed:      bool           = False
    skipped:     bool           = False
    skip_reason: str            = ""
    duration_ms: float          = 0.0
    error:       Optional[str]  = None
    assertions:  List[Tuple[str, bool, str]] = field(default_factory=list)
    request_headers:  Dict[str, str]  = field(default_factory=dict)
    request_body:     Any             = None
    response_headers: Dict[str, str]  = field(default_factory=dict)
    response_body:    Any             = None
    warnings:    List[str]      = field(default_factory=list)

    @property
    def icon(self) -> str:
        if self.skipped: return "⏭"
        return "✅" if self.passed else "❌"

    @property
    def style(self) -> str:
        if self.skipped: return "skip"
        return "pass" if self.passed else "fail"


@dataclass
class Suite:
    url:     str
    api_key: str
    results: List[TestResult] = field(default_factory=list)

    @property
    def passed(self)  -> int: return sum(1 for r in self.results if r.passed)
    @property
    def failed(self)  -> int: return sum(1 for r in self.results if not r.passed and not r.skipped)
    @property
    def skipped(self) -> int: return sum(1 for r in self.results if r.skipped)
    @property
    def total(self)   -> int: return len(self.results)
    @property
    def total_ms(self)-> float: return sum(r.duration_ms for r in self.results)


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _headers(api_key: str) -> Dict[str, str]:
    h: Dict[str, str] = {"User-Agent": "lightning-ocr-test-client/1.0"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


async def _request(
    client:  httpx.AsyncClient,
    method:  str,
    url:     str,
    result:  TestResult,
    **kwargs: Any,
) -> Optional[httpx.Response]:
    """Make a request, capture timing, headers, body into result."""
    t0 = time.perf_counter()
    try:
        resp = await client.request(method, url, **kwargs)
        result.duration_ms   = (time.perf_counter() - t0) * 1000
        result.status_code   = resp.status_code
        result.response_headers = dict(resp.headers)
        ct = resp.headers.get("content-type", "")
        if "json" in ct:
            try:
                result.response_body = resp.json()
            except Exception:
                result.response_body = resp.text
        elif "html" in ct:
            result.response_body = f"<HTML {len(resp.content)} bytes>"
        else:
            result.response_body = f"<{ct} {len(resp.content)} bytes>"
        return resp
    except httpx.ConnectError as exc:
        result.duration_ms = (time.perf_counter() - t0) * 1000
        result.error = f"Connection refused — is lightning-ocr running at {url}? ({exc})"
        return None
    except httpx.TimeoutException as exc:
        result.duration_ms = (time.perf_counter() - t0) * 1000
        result.error = f"Request timed out ({exc})"
        return None
    except Exception as exc:
        result.duration_ms = (time.perf_counter() - t0) * 1000
        result.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        return None


def _assert(result: TestResult, label: str, cond: bool, detail: str = "") -> bool:
    result.assertions.append((label, cond, detail))
    return cond


# ── Image / PDF fixtures ──────────────────────────────────────────────────────
def _make_test_png(text: str = "lightning-ocr TEST IMAGE\nHello World 123") -> bytes:
    """Generate a minimal valid PNG with text using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        img = Image.new("RGB", (480, 160), color=(255, 255, 255))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        except OSError:
            font = ImageFont.load_default()
        draw.text((20, 30), text, fill=(20, 20, 20), font=font)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Fallback: 1×1 white PNG
        return (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
            b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
            b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )


def _make_test_pdf() -> bytes:
    """Generate a minimal valid multi-page PDF using reportlab."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=A4)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(72, 750, "⚡ lightning-ocr PDF Test")
        c.setFont("Helvetica", 13)
        c.drawString(72, 710, "Invoice #12345")
        c.drawString(72, 690, "Total: $1,234.56")
        c.drawString(72, 670, "Date: 2026-01-01")
        c.showPage()
        c.setFont("Helvetica-Bold", 16)
        c.drawString(72, 750, "Page 2 — Appendix")
        c.setFont("Helvetica", 13)
        c.drawString(72, 710, "Item A   $100.00")
        c.drawString(72, 690, "Item B   $200.00")
        c.showPage()
        c.save()
        return buf.getvalue()
    except ImportError:
        # Minimal hand-crafted 1-page PDF
        return (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
            b"/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"
            b">>>>>endobj\nxref\n0 4\n0000000000 65535 f\n"
            b"0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n266\n%%EOF\n"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Individual test functions
# Each returns TestResult
# ═══════════════════════════════════════════════════════════════════════════════

async def test_ui_dashboard(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("UI Dashboard", "/", "GET")
    resp = await _request(client, "GET", f"{s.url}/", r)
    if resp is None: return r
    ok = all([
        _assert(r, "status 200",        resp.status_code == 200),
        _assert(r, "HTML content-type", "text/html" in resp.headers.get("content-type","")),
        _assert(r, "contains lightning-ocr", b"lightning-ocr" in resp.content),
        _assert(r, "non-empty body",    len(resp.content) > 5000,
                f"body size: {len(resp.content)} bytes"),
    ])
    r.passed = ok
    return r


async def test_openapi_schema(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("OpenAPI Schema", "/openapi.json", "GET")
    resp = await _request(client, "GET", f"{s.url}/openapi.json", r)
    if resp is None: return r
    data = resp.json() if "json" in resp.headers.get("content-type","") else {}
    ok = all([
        _assert(r, "status 200",          resp.status_code == 200),
        _assert(r, "has openapi field",   "openapi" in data,      str(list(data.keys())[:5])),
        _assert(r, "has paths",           "paths" in data),
        _assert(r, "has /api/ocr path",   "/api/ocr" in data.get("paths", {})),
        _assert(r, "has /mcp path",       "/mcp" in data.get("paths", {})),
        _assert(r, "title is lightning",  "lightning" in data.get("info",{}).get("title","").lower()),
    ])
    r.passed = ok
    return r


async def test_swagger_ui(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("Swagger UI (/docs)", "/docs", "GET")
    resp = await _request(client, "GET", f"{s.url}/docs", r)
    if resp is None: return r
    ok = all([
        _assert(r, "status 200",   resp.status_code == 200),
        _assert(r, "HTML body",    b"swagger" in resp.content.lower()),
    ])
    r.passed = ok
    return r


async def test_health(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("Health Check", "/api/health", "GET")
    resp = await _request(client, "GET", f"{s.url}/api/health", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",          resp.status_code == 200),
        _assert(r, "has overall field",   "overall" in data,
                f"keys: {list(data.keys())}"),
        _assert(r, "has backends list",   isinstance(data.get("backends"), list)),
        _assert(r, "backends not empty",  len(data.get("backends", [])) > 0,
                f"count: {len(data.get('backends',[]))}"),
    ])
    # Warn about unhealthy backends but don't fail the test
    for b in data.get("backends", []):
        if b.get("status") != "ok":
            r.warnings.append(f"Backend '{b.get('id')}' status={b.get('status')} "
                               f"— {b.get('detail',{})}")
    r.passed = ok
    return r


async def test_models(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("List Models/Backends", "/api/models", "GET")
    resp = await _request(client, "GET", f"{s.url}/api/models", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    backends = data.get("backends", [])
    ok = all([
        _assert(r, "status 200",           resp.status_code == 200),
        _assert(r, "has backends key",     "backends" in data),
        _assert(r, "backends is list",     isinstance(backends, list)),
        _assert(r, "at least 1 backend",   len(backends) >= 1,
                f"count: {len(backends)}"),
    ])
    for b in backends:
        _assert(r, f"backend {b.get('id')} has label",  "label"    in b)
        _assert(r, f"backend {b.get('id')} has kind",   "kind"     in b)
        _assert(r, f"backend {b.get('id')} has models", "models"   in b)
    r.passed = ok and all(a[1] for a in r.assertions)
    return r


async def _ocr_single(
    client:    httpx.AsyncClient,
    s:         Suite,
    image:     bytes,
    filename:  str,
    mode:      str,
    backend_id: str,
    ct:        str  = "image/png",
    extra_form: Optional[Dict[str, str]] = None,
) -> TestResult:
    name = f"OCR mode={mode} backend={backend_id}"
    r = TestResult(name, "/api/ocr", "POST")
    files = {"file": (filename, image, ct)}
    form  = {"backend_id": backend_id, "mode": mode, "auto_fallback": "true"}
    if extra_form:
        form.update(extra_form)
    r.request_body = {"form": form, "file": filename}
    resp = await _request(client, "POST", f"{s.url}/api/ocr", r,
                          files=files, data=form,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",       resp.status_code == 200,
                f"got {resp.status_code} — {data.get('detail','')}"),
        _assert(r, "has text field",   "text"    in data),
        _assert(r, "has backend",      "backend" in data),
        _assert(r, "has mode",         "mode"    in data),
        _assert(r, "has duration_ms",  "duration_ms" in data),
        _assert(r, "has job_id",       "job_id"  in data),
        _assert(r, "text is string",   isinstance(data.get("text"), str)),
    ])
    if data.get("fallback"):
        r.warnings.append(f"Fallback was used — "
                          f"primary backend unreachable, fell back to {data.get('backend',{}).get('id')}")
    r.passed = ok
    return r


async def test_ocr_document(client: httpx.AsyncClient, s: Suite,
                             backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "document", backend_id)


async def test_ocr_plain(client: httpx.AsyncClient, s: Suite,
                          backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "ocr", backend_id)


async def test_ocr_free(client: httpx.AsyncClient, s: Suite,
                         backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "free", backend_id)


async def test_ocr_figure(client: httpx.AsyncClient, s: Suite,
                           backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "figure", backend_id)


async def test_ocr_describe(client: httpx.AsyncClient, s: Suite,
                             backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "describe", backend_id)


async def test_ocr_find(client: httpx.AsyncClient, s: Suite,
                         backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "find", backend_id,
                              extra_form={"find_term": "Hello"})


async def test_ocr_freeform(client: httpx.AsyncClient, s: Suite,
                             backend_id: str, img: bytes) -> TestResult:
    return await _ocr_single(client, s, img, "test.png", "freeform", backend_id,
                              extra_form={"custom_prompt": "List every word you see."})


async def test_ocr_pdf(client: httpx.AsyncClient, s: Suite,
                        backend_id: str, pdf: bytes) -> TestResult:
    r = await _ocr_single(client, s, pdf, "test.pdf", "document", backend_id,
                           ct="application/pdf")
    r.name = f"OCR PDF backend={backend_id}"
    return r


async def test_ocr_empty_file(client: httpx.AsyncClient, s: Suite,
                               backend_id: str) -> TestResult:
    r = TestResult("OCR empty file (expect 400)", "/api/ocr", "POST")
    files = {"file": ("empty.png", b"", "image/png")}
    form  = {"backend_id": backend_id, "mode": "ocr"}
    resp = await _request(client, "POST", f"{s.url}/api/ocr", r,
                          files=files, data=form,
                          headers=_headers(s.api_key))
    if resp is None: return r
    r.passed = _assert(r, "status 400",
                       resp.status_code == 400,
                       f"got {resp.status_code}")
    return r


async def test_ocr_bad_backend(client: httpx.AsyncClient, s: Suite,
                                img: bytes) -> TestResult:
    r = TestResult("OCR unknown backend (expect 404)", "/api/ocr", "POST")
    files = {"file": ("test.png", img, "image/png")}
    form  = {"backend_id": "DOES_NOT_EXIST_9999", "mode": "ocr"}
    resp = await _request(client, "POST", f"{s.url}/api/ocr", r,
                          files=files, data=form,
                          headers=_headers(s.api_key))
    if resp is None: return r
    r.passed = _assert(r, "status 404",
                       resp.status_code == 404,
                       f"got {resp.status_code}")
    return r


async def test_ocr_batch(client: httpx.AsyncClient, s: Suite,
                          backend_id: str, img: bytes) -> TestResult:
    r = TestResult("OCR Batch (3 files)", "/api/ocr/batch", "POST")
    files = [
        ("files", ("img1.png", img, "image/png")),
        ("files", ("img2.png", img, "image/png")),
        ("files", ("img3.png", img, "image/png")),
    ]
    form = {"backend_id": backend_id, "mode": "ocr", "auto_fallback": "true"}
    resp = await _request(client, "POST", f"{s.url}/api/ocr/batch", r,
                          files=files, data=form,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",       resp.status_code == 200,
                f"got {resp.status_code} — {data.get('detail','')}"),
        _assert(r, "has results list", isinstance(data.get("results"), list)),
        _assert(r, "count == 3",       data.get("count") == 3,
                f"got {data.get('count')}"),
        _assert(r, "each has file",    all("file" in x for x in data.get("results", []))),
    ])
    r.passed = ok
    return r


async def test_history_list(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("History List", "/api/history", "GET")
    resp = await _request(client, "GET", f"{s.url}/api/history?limit=10&offset=0", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",      resp.status_code == 200),
        _assert(r, "has jobs key",    "jobs"   in data,    str(list(data.keys()))),
        _assert(r, "has limit key",   "limit"  in data),
        _assert(r, "has offset key",  "offset" in data),
        _assert(r, "jobs is list",    isinstance(data.get("jobs"), list)),
    ])
    r.passed = ok
    return r


async def test_history_get_first(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("History Get First Job", "/api/history/{id}", "GET")
    # First, get the list
    list_resp = await client.get(f"{s.url}/api/history?limit=1",
                                 headers=_headers(s.api_key))
    if list_resp.status_code != 200:
        r.skipped = True
        r.skip_reason = "History list returned non-200; skipping single-job fetch"
        return r
    jobs = list_resp.json().get("jobs", [])
    if not jobs:
        r.skipped = True
        r.skip_reason = "No jobs in history yet (run OCR tests first)"
        return r
    job_id = jobs[0]["id"]
    r.endpoint = f"/api/history/{job_id}"
    resp = await _request(client, "GET", f"{s.url}/api/history/{job_id}", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",         resp.status_code == 200),
        _assert(r, "has id field",       "id"         in data),
        _assert(r, "id matches request", data.get("id") == job_id,
                f"expected {job_id} got {data.get('id')}"),
        _assert(r, "has backend_id",     "backend_id" in data),
        _assert(r, "has mode",           "mode"       in data),
        _assert(r, "has created_at",     "created_at" in data),
    ])
    r.passed = ok
    return r


async def test_history_delete(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("History Delete Job", "/api/history/{id}", "DELETE")

    # Create a job first by running a quick OCR, so we have something to delete
    img = _make_test_png("delete me")
    # Get a backend to use
    mr = await client.get(f"{s.url}/api/models", headers=_headers(s.api_key))
    if mr.status_code != 200 or not mr.json().get("backends"):
        r.skipped = True
        r.skip_reason = "Cannot get backends to create a test job"
        return r
    bid = mr.json()["backends"][0]["id"]

    cr = await client.post(
        f"{s.url}/api/ocr",
        files={"file": ("del_test.png", img, "image/png")},
        data={"backend_id": bid, "mode": "ocr", "auto_fallback": "true"},
        headers=_headers(s.api_key),
    )
    if cr.status_code != 200:
        r.skipped = True
        r.skip_reason = f"OCR call to create test job failed ({cr.status_code})"
        return r

    job_id = cr.json().get("job_id")
    if not job_id:
        r.skipped = True
        r.skip_reason = "OCR response had no job_id"
        return r

    r.endpoint = f"/api/history/{job_id}"
    resp = await _request(client, "DELETE", f"{s.url}/api/history/{job_id}", r,
                          headers=_headers(s.api_key))
    if resp is None: return r

    # Confirm 404 after delete
    verify = await client.get(f"{s.url}/api/history/{job_id}",
                              headers=_headers(s.api_key))
    ok = all([
        _assert(r, "delete status 200", resp.status_code == 200),
        _assert(r, "deleted key in response",
                r.response_body and r.response_body.get("deleted") == job_id,
                str(r.response_body)),
        _assert(r, "job gone (404 after delete)", verify.status_code == 404,
                f"got {verify.status_code}"),
    ])
    r.passed = ok
    return r


async def test_history_404(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("History 404 on missing job", "/api/history/999999999", "GET")
    resp = await _request(client, "GET", f"{s.url}/api/history/999999999", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    r.passed = _assert(r, "status 404",
                       resp.status_code == 404,
                       f"got {resp.status_code}")
    return r


# ── MCP tests ─────────────────────────────────────────────────────────────────

async def _mcp(client: httpx.AsyncClient, s: Suite,
               payload: Dict[str, Any]) -> Tuple[Optional[httpx.Response], Any]:
    resp = await client.post(
        f"{s.url}/mcp",
        json=payload,
        headers={**_headers(s.api_key), "Content-Type": "application/json"},
    )
    data = None
    try:
        data = resp.json()
    except Exception:
        pass
    return resp, data


async def test_mcp_initialize(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP Initialize Handshake", "/mcp", "POST")
    payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "lightning-ocr-test", "version": "1.0"},
            "capabilities": {},
        },
    }
    r.request_body = payload
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",              resp.status_code == 200),
        _assert(r, "jsonrpc 2.0",             data.get("jsonrpc") == "2.0"),
        _assert(r, "id matches",              data.get("id") == 1),
        _assert(r, "no error",                "error" not in data,
                str(data.get("error"))),
        _assert(r, "has result",              "result" in data),
        _assert(r, "has protocolVersion",
                "protocolVersion" in data.get("result", {})),
        _assert(r, "has serverInfo",
                "serverInfo" in data.get("result", {})),
        _assert(r, "server name is lightning-ocr",
                data.get("result", {}).get("serverInfo", {}).get("name") == "lightning-ocr"),
    ])
    r.passed = ok
    return r


async def test_mcp_tools_list(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP tools/list", "/mcp", "POST")
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    r.request_body = payload
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data  = r.response_body or {}
    tools = data.get("result", {}).get("tools", [])
    names = [t.get("name") for t in tools]
    ok = all([
        _assert(r, "status 200",               resp.status_code == 200),
        _assert(r, "jsonrpc 2.0",              data.get("jsonrpc") == "2.0"),
        _assert(r, "no error",                 "error" not in data,    str(data.get("error"))),
        _assert(r, "has tools list",           isinstance(tools, list)),
        _assert(r, "at least 2 tools",         len(tools) >= 2,        f"got {len(tools)}"),
        _assert(r, "has ocr_image tool",       "ocr_image" in names,
                f"tools: {names}"),
        _assert(r, "has list_ocr_backends",    "list_ocr_backends" in names,
                f"tools: {names}"),
    ])
    for t in tools:
        _assert(r, f"tool {t.get('name')} has inputSchema",
                "inputSchema" in t)
        _assert(r, f"tool {t.get('name')} has description",
                "description" in t and len(t["description"]) > 0)
    r.passed = ok and all(a[1] for a in r.assertions)
    return r


async def test_mcp_tool_list_backends(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP tools/call list_ocr_backends", "/mcp", "POST")
    payload = {
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {"name": "list_ocr_backends", "arguments": {}},
    }
    r.request_body = payload
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data    = r.response_body or {}
    content = data.get("result", {}).get("content", [])
    ok = all([
        _assert(r, "status 200",        resp.status_code == 200),
        _assert(r, "no error",          "error" not in data,   str(data.get("error"))),
        _assert(r, "has content list",  isinstance(content, list)),
        _assert(r, "content not empty", len(content) > 0),
    ])
    r.passed = ok
    return r


async def test_mcp_ocr_image(client: httpx.AsyncClient, s: Suite,
                              img: bytes) -> TestResult:
    r = TestResult("MCP tools/call ocr_image", "/mcp", "POST")
    b64 = base64.b64encode(img).decode()
    payload = {
        "jsonrpc": "2.0", "id": 4,
        "method": "tools/call",
        "params": {
            "name": "ocr_image",
            "arguments": {
                "image_base64": b64,
                "filename":     "mcp_test.png",
                "mode":         "ocr",
            },
        },
    }
    r.request_body = {"jsonrpc": "2.0", "method": "tools/call",
                      "params": {"name": "ocr_image", "arguments": {"mode": "ocr",
                                 "image_base64": f"<{len(b64)} bytes b64>"}}}
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data    = r.response_body or {}
    result  = data.get("result", {})
    content = result.get("content", [])
    ok = all([
        _assert(r, "status 200",             resp.status_code == 200),
        _assert(r, "no rpc error",           "error" not in data,
                str(data.get("error"))),
        _assert(r, "has content list",       isinstance(content, list)),
        _assert(r, "content not empty",      len(content) > 0),
        _assert(r, "first content has text",
                content and content[0].get("type") == "text"),
        _assert(r, "has meta",               "meta" in result),
    ])
    r.passed = ok
    return r


async def test_mcp_invalid_tool(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP unknown tool (expect error)", "/mcp", "POST")
    payload = {
        "jsonrpc": "2.0", "id": 99,
        "method": "tools/call",
        "params": {"name": "DOES_NOT_EXIST", "arguments": {}},
    }
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data = r.response_body or {}
    r.passed = _assert(r, "has error field",
                       "error" in data,
                       f"keys: {list(data.keys())}")
    return r


async def test_mcp_unknown_method(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP unknown method (expect -32601)", "/mcp", "POST")
    payload = {"jsonrpc": "2.0", "id": 100, "method": "totally/fake", "params": {}}
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          json=payload,
                          headers={**_headers(s.api_key), "Content-Type": "application/json"})
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "has error",          "error" in data),
        _assert(r, "code -32601",        data.get("error", {}).get("code") == -32601,
                f"code: {data.get('error',{}).get('code')}"),
    ])
    r.passed = ok
    return r


async def test_mcp_discovery(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP Discovery /.well-known/mcp", "/.well-known/mcp", "GET")
    resp = await _request(client, "GET", f"{s.url}/.well-known/mcp", r,
                          headers=_headers(s.api_key))
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 200",         resp.status_code == 200),
        _assert(r, "has name",           "name"         in data),
        _assert(r, "has mcp_endpoint",   "mcp_endpoint" in data),
        _assert(r, "has tools list",     "tools"        in data),
        _assert(r, "mcp_endpoint is /mcp",
                data.get("mcp_endpoint") == "/mcp"),
    ])
    r.passed = ok
    return r


async def test_mcp_bad_json(client: httpx.AsyncClient, s: Suite) -> TestResult:
    r = TestResult("MCP malformed JSON (expect parse error)", "/mcp", "POST")
    resp = await _request(client, "POST", f"{s.url}/mcp", r,
                          content=b"{this is not json !!!}",
                          headers={**_headers(s.api_key),
                                   "Content-Type": "application/json"})
    if resp is None: return r
    data = r.response_body or {}
    ok = all([
        _assert(r, "status 400",  resp.status_code == 400,
                f"got {resp.status_code}"),
        _assert(r, "has error",   "error" in data,
                f"body: {str(data)[:200]}"),
    ])
    r.passed = ok
    return r


# ═══════════════════════════════════════════════════════════════════════════════
# Output rendering
# ═══════════════════════════════════════════════════════════════════════════════

def _print_result(r: TestResult, verbose: bool) -> None:
    # One-liner status
    status_text = Text()
    status_text.append(f"  {r.icon}  ", style=r.style)
    status_text.append(f"{r.name:<50}", style="title")
    status_text.append(f" {r.method} {r.endpoint} ", style="debug")
    if r.skipped:
        status_text.append(f"  SKIP: {r.skip_reason}", style="skip")
    else:
        style = "ms" if r.duration_ms < 500 else ("warn" if r.duration_ms < 2000 else "fail")
        status_text.append(f"  {r.duration_ms:>7.1f}ms", style=style)
        if r.status_code is not None:
            sc_style = "pass" if r.status_code < 400 else "fail"
            status_text.append(f"  HTTP {r.status_code}", style=sc_style)
    console.print(status_text)

    # Assertions
    if not r.skipped:
        for (label, ok, detail) in r.assertions:
            icon  = "    ✓" if ok else "    ✗"
            style = "debug" if ok else "fail"
            line  = f"{icon}  {label}"
            if detail and (not ok or verbose):
                line += f"  [{escape(detail[:120])}]"
            console.print(line, style=style)

    # Warnings
    for w in r.warnings:
        console.print(f"    ⚠  {w}", style="warn")

    # Error
    if r.error:
        console.print(Panel(
            escape(r.error),
            title="[fail]Error[/fail]",
            border_style="red",
            expand=False,
        ))

    # Verbose: dump full request/response
    if verbose and not r.skipped:
        if r.request_body:
            try:
                body_str = json.dumps(r.request_body, indent=2, ensure_ascii=False)
            except Exception:
                body_str = str(r.request_body)
            console.print(Syntax(body_str[:2000], "json",
                                 theme="monokai", line_numbers=False,
                                 background_color="default"),
                          Panel("Request Body", border_style="blue"))

        if r.response_body:
            try:
                body_str = json.dumps(r.response_body, indent=2, ensure_ascii=False)
            except Exception:
                body_str = str(r.response_body)
            console.print(Syntax(body_str[:3000], "json",
                                 theme="monokai", line_numbers=False,
                                 background_color="default"),
                          Panel("Response Body", border_style="green"))


def _print_summary(suite: Suite) -> None:
    console.rule("[bold white]Test Summary[/bold white]")

    table = Table(box=box.ROUNDED, header_style="bold white", show_lines=True)
    table.add_column("Test",        style="white",      min_width=40)
    table.add_column("Method",      style="method",     width=8,  justify="center")
    table.add_column("Endpoint",    style="url",        min_width=28)
    table.add_column("Status",      width=8,  justify="center")
    table.add_column("Time (ms)",   width=10, justify="right")
    table.add_column("Assertions",  width=14, justify="center")
    table.add_column("Result",      width=8,  justify="center")

    for r in suite.results:
        a_pass = sum(1 for a in r.assertions if a[1])
        a_fail = sum(1 for a in r.assertions if not a[1])
        a_str  = f"[pass]{a_pass}✓[/pass]"
        if a_fail:
            a_str += f" [fail]{a_fail}✗[/fail]"

        sc_str = f"[pass]{r.status_code}[/pass]" if r.status_code and r.status_code < 400 \
                 else (f"[fail]{r.status_code}[/fail]" if r.status_code else "—")
        ms_str = f"{r.duration_ms:>7.1f}"

        if r.skipped:
            icon = "⏭"
            result_style = "skip"
        elif r.passed:
            icon = "✅"
            result_style = "pass"
        else:
            icon = "❌"
            result_style = "fail"

        table.add_row(
            r.name, r.method, r.endpoint, sc_str, ms_str, a_str,
            f"[{result_style}]{icon}[/{result_style}]",
        )
    console.print(table)

    # Totals bar
    total_bar = Text()
    total_bar.append(f"  Total: {suite.total}", style="bold white")
    total_bar.append(f"  ✅ {suite.passed} passed", style="pass")
    total_bar.append(f"  ❌ {suite.failed} failed", style="fail")
    if suite.skipped:
        total_bar.append(f"  ⏭ {suite.skipped} skipped", style="skip")
    total_bar.append(f"  ⏱ {suite.total_ms:.0f}ms total", style="ms")
    console.print(total_bar)


def _json_report(suite: Suite) -> Dict[str, Any]:
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "server_url":   suite.url,
        "summary": {
            "total":   suite.total,
            "passed":  suite.passed,
            "failed":  suite.failed,
            "skipped": suite.skipped,
            "total_ms": suite.total_ms,
        },
        "results": [
            {
                "name":        r.name,
                "endpoint":    r.endpoint,
                "method":      r.method,
                "passed":      r.passed,
                "skipped":     r.skipped,
                "skip_reason": r.skip_reason,
                "status_code": r.status_code,
                "duration_ms": r.duration_ms,
                "error":       r.error,
                "warnings":    r.warnings,
                "assertions": [
                    {"label": a[0], "passed": a[1], "detail": a[2]}
                    for a in r.assertions
                ],
                "response_body": r.response_body,
            }
            for r in suite.results
        ],
    }


def _html_report(suite: Suite) -> str:
    rows = ""
    for r in suite.results:
        if r.skipped:
            badge = '<span style="color:#f5a623">⏭ SKIP</span>'
        elif r.passed:
            badge = '<span style="color:#27c93f">✅ PASS</span>'
        else:
            badge = '<span style="color:#ff5f56">❌ FAIL</span>'

        assertions_html = "".join(
            f'<div style="color:{"#27c93f" if a[1] else "#ff5f56"};margin-left:16px">'
            f'{"✓" if a[1] else "✗"} {a[0]}'
            f'{(" — "+a[2]) if a[2] and (not a[1]) else ""}</div>'
            for a in r.assertions
        )
        warnings_html = "".join(
            f'<div style="color:#f5a623;margin-left:16px">⚠ {w}</div>'
            for w in r.warnings
        )
        error_html = (
            f'<pre style="color:#ff5f56;font-size:11px;margin-left:16px">{r.error}</pre>'
            if r.error else ""
        )

        rows += f"""
        <tr>
          <td>{badge}</td>
          <td><strong>{r.name}</strong>
              {assertions_html}{warnings_html}{error_html}</td>
          <td><code>{r.method}</code></td>
          <td><code>{r.endpoint}</code></td>
          <td style="text-align:right">{r.status_code or "—"}</td>
          <td style="text-align:right">{r.duration_ms:.1f}ms</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>⚡ lightning-ocr Test Report</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{background:#0b1020;color:#dde8ff;font-family:"Inter","Segoe UI",system-ui,sans-serif;
         font-size:14px;padding:32px}}
    h1{{font-size:28px;margin-bottom:8px;background:linear-gradient(135deg,#4f8eff,#a259ff);
        -webkit-background-clip:text;-webkit-text-fill-color:transparent}}
    .meta{{color:#7a90c4;margin-bottom:24px;font-size:12px}}
    .stats{{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}}
    .stat{{background:rgba(12,16,40,.8);border:1px solid rgba(80,120,255,.2);
           border-radius:12px;padding:12px 20px;text-align:center}}
    .stat .n{{font-size:28px;font-weight:800}}
    .stat .l{{font-size:11px;color:#7a90c4;text-transform:uppercase;letter-spacing:.5px}}
    .pass{{color:#27c93f}} .fail{{color:#ff5f56}} .skip{{color:#f5a623}}
    table{{width:100%;border-collapse:collapse;background:rgba(12,16,40,.7);
           border-radius:16px;overflow:hidden}}
    th{{background:rgba(30,45,90,.8);padding:12px 14px;text-align:left;
        font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#7a90c4}}
    td{{padding:10px 14px;border-bottom:1px solid rgba(80,120,255,.1);vertical-align:top}}
    tr:hover td{{background:rgba(79,142,255,.04)}}
    code{{background:rgba(79,142,255,.12);border-radius:4px;padding:2px 6px;font-size:12px}}
    pre{{background:rgba(5,6,15,.6);border-radius:8px;padding:8px;font-size:11px;overflow-x:auto}}
  </style>
</head>
<body>
  <h1>⚡ lightning-ocr Test Report</h1>
  <div class="meta">Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')} |
    Server: {suite.url}</div>
  <div class="stats">
    <div class="stat"><div class="n">{suite.total}</div><div class="l">Total</div></div>
    <div class="stat"><div class="n pass">{suite.passed}</div><div class="l">Passed</div></div>
    <div class="stat"><div class="n fail">{suite.failed}</div><div class="l">Failed</div></div>
    <div class="stat"><div class="n skip">{suite.skipped}</div><div class="l">Skipped</div></div>
    <div class="stat"><div class="n">{suite.total_ms:.0f}ms</div><div class="l">Total Time</div></div>
  </div>
  <table>
    <thead><tr><th>Result</th><th>Test</th><th>Method</th>
                <th>Endpoint</th><th>HTTP</th><th>Time</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════════════
# Test runner
# ═══════════════════════════════════════════════════════════════════════════════

ALL_TEST_NAMES = [
    "health", "models", "ui", "openapi", "swagger",
    "ocr-document", "ocr-plain", "ocr-free", "ocr-figure",
    "ocr-describe", "ocr-find", "ocr-freeform", "ocr-pdf",
    "ocr-empty", "ocr-bad-backend", "ocr-batch",
    "history-list", "history-get", "history-delete", "history-404",
    "mcp-init", "mcp-tools-list", "mcp-list-backends",
    "mcp-ocr-image", "mcp-invalid-tool", "mcp-unknown-method",
    "mcp-discovery", "mcp-bad-json",
]


async def run_suite(
    url:         str,
    api_key:     str,
    image_path:  Optional[str],
    test_filter: Optional[str],
    verbose:     bool,
) -> Suite:
    suite  = Suite(url=url.rstrip("/"), api_key=api_key)

    # Fixtures
    if image_path and Path(image_path).exists():
        console.print(f"  Using custom image: [url]{image_path}[/url]")
        img_bytes = Path(image_path).read_bytes()
    else:
        if image_path:
            console.print(f"  [warn]Image not found: {image_path} — using generated test image[/warn]")
        img_bytes = _make_test_png()

    pdf_bytes = _make_test_pdf()

    # Determine first available backend id
    async with httpx.AsyncClient(timeout=10.0) as probe:
        try:
            mr = await probe.get(f"{suite.url}/api/models",
                                 headers=_headers(api_key))
            backends = mr.json().get("backends", []) if mr.status_code == 200 else []
        except Exception:
            backends = []

    backend_id = backends[0]["id"] if backends else "glm-ocr"
    console.print(f"  Primary backend for OCR tests: [info]{backend_id}[/info]\n")

    def _want(name: str) -> bool:
        if not test_filter:
            return True
        return test_filter.lower() in name.lower()

    # ── Define all tests as (name, coroutine) pairs ────────────────────────
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=240.0, write=30.0, pool=5.0),
        follow_redirects=True,
    ) as client:

        tests: List[Tuple[str, Any]] = [
            ("health",            test_health(client, suite)),
            ("models",            test_models(client, suite)),
            ("ui",                test_ui_dashboard(client, suite)),
            ("openapi",           test_openapi_schema(client, suite)),
            ("swagger",           test_swagger_ui(client, suite)),
            ("ocr-document",      test_ocr_document(client, suite, backend_id, img_bytes)),
            ("ocr-plain",         test_ocr_plain(client, suite, backend_id, img_bytes)),
            ("ocr-free",          test_ocr_free(client, suite, backend_id, img_bytes)),
            ("ocr-figure",        test_ocr_figure(client, suite, backend_id, img_bytes)),
            ("ocr-describe",      test_ocr_describe(client, suite, backend_id, img_bytes)),
            ("ocr-find",          test_ocr_find(client, suite, backend_id, img_bytes)),
            ("ocr-freeform",      test_ocr_freeform(client, suite, backend_id, img_bytes)),
            ("ocr-pdf",           test_ocr_pdf(client, suite, backend_id, pdf_bytes)),
            ("ocr-empty",         test_ocr_empty_file(client, suite, backend_id)),
            ("ocr-bad-backend",   test_ocr_bad_backend(client, suite, img_bytes)),
            ("ocr-batch",         test_ocr_batch(client, suite, backend_id, img_bytes)),
            ("history-list",      test_history_list(client, suite)),
            ("history-get",       test_history_get_first(client, suite)),
            ("history-delete",    test_history_delete(client, suite)),
            ("history-404",       test_history_404(client, suite)),
            ("mcp-init",          test_mcp_initialize(client, suite)),
            ("mcp-tools-list",    test_mcp_tools_list(client, suite)),
            ("mcp-list-backends", test_mcp_tool_list_backends(client, suite)),
            ("mcp-ocr-image",     test_mcp_ocr_image(client, suite, img_bytes)),
            ("mcp-invalid-tool",  test_mcp_invalid_tool(client, suite)),
            ("mcp-unknown-method",test_mcp_unknown_method(client, suite)),
            ("mcp-discovery",     test_mcp_discovery(client, suite)),
            ("mcp-bad-json",      test_mcp_bad_json(client, suite)),
        ]

        filtered = [(n, coro) for n, coro in tests if _want(n)]
        console.rule(f"[bold white]Running {len(filtered)}/{len(tests)} tests[/bold white]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=28),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Testing…", total=len(filtered))

            for name, coro in filtered:
                progress.update(task, description=f"[cyan]{name}[/cyan]")
                result = await coro
                suite.results.append(result)
                _print_result(result, verbose)
                progress.advance(task)

    return suite


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

app = typer.Typer(
    name="test_client",
    help="⚡ lightning-ocr Full API Test Client",
    add_completion=False,
    rich_markup_mode="rich",
)


@app.command()
def main(
    url: str = typer.Option(
        "http://localhost:8000",
        "--url", "-u",
        help="Base URL of the lightning-ocr server",
    ),
    api_key: str = typer.Option(
        "", "--api-key", "-k",
        envvar="LIGHTNING_OCR_API_KEY",
        help="Bearer token (leave empty if API_KEY not set)",
    ),
    image: Optional[str] = typer.Option(
        None, "--image", "-i",
        help="Path to a real image file to use instead of generated test image",
    ),
    test: Optional[str] = typer.Option(
        None, "--test", "-t",
        help=f"Run only tests whose name contains this string. Options: {', '.join(ALL_TEST_NAMES)}",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Print full request/response bodies for every test",
    ),
    json_output: Optional[str] = typer.Option(
        None, "--json-output", "-j",
        help="Write full JSON report to this file",
    ),
    html_report: Optional[str] = typer.Option(
        None, "--html-report",
        help="Write HTML test report to this file",
    ),
    no_color: bool = typer.Option(
        False, "--no-color",
        help="Disable Rich colour output (for CI pipelines)",
    ),
    fail_fast: bool = typer.Option(
        False, "--fail-fast",
        help="Stop on the first test failure",
    ),
) -> None:
    """
    ⚡ lightning-ocr — Comprehensive API test client.

    Runs every endpoint with assertions, timing, and detailed debug output.
    """
    if no_color:
        console._file = sys.stdout  # type: ignore[attr-defined]
        console.no_color = True

    console.print(Panel.fit(
        "[bold white]⚡ lightning-ocr API Test Suite[/bold white]\n"
        f"[url]{url}[/url]  [dim]api_key={'***' if api_key else 'none'}[/dim]",
        border_style="blue",
        padding=(1, 4),
    ))

    suite = asyncio.run(run_suite(
        url=url,
        api_key=api_key,
        image_path=image,
        test_filter=test,
        verbose=verbose,
    ))

    _print_summary(suite)

    if json_output:
        Path(json_output).write_text(
            json.dumps(_json_report(suite), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"\n  [info]JSON report written:[/info] [url]{json_output}[/url]")

    if html_report:
        Path(html_report).write_text(_html_report(suite), encoding="utf-8")
        console.print(f"  [info]HTML report written:[/info] [url]{html_report}[/url]")

    # Exit code
    sys.exit(0 if suite.failed == 0 else 1)


if __name__ == "__main__":
    app()
