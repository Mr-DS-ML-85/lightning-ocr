"""
lightning-ocr · Universal MCP (Model Context Protocol) tool server
Exposes OCR as an MCP tool callable by any AI agent / LLM that speaks MCP.

Specs supported:
  - 2025-03-26 (stateful, initialize handshake)
  - 2026-07-28 (stateless, per-request _meta)

Endpoint: POST /mcp (JSON-RPC 2.0)
"""
from __future__ import annotations

import asyncio
import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.config import BACKENDS, settings
from app.ocr import validate_backend_url as _validate_backend_url
from app.ocr import MAX_UPLOAD_BYTES, MAX_BATCH_FILES

log = logging.getLogger("lightning_ocr.mcp")
router = APIRouter()

# ── Supported protocol versions ────────────────────────────────────────────────
SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2026-07-28"]
LATEST_PROTOCOL_VERSION = "2026-07-28"

# ── Auth Dependency ────────────────────────────────────────────────────────────
async def verify_api_key(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> bool:
    """Verify API key if configured."""
    if not settings.API_KEY:
        return True  # No auth configured, allow all
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    provided_key = authorization[7:].strip()
    if provided_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True

# ── Tool manifest ─────────────────────────────────────────────────────────────

TOOL_LIST = [
    {
        "name": "ocr_image",
        "description": (
            "Extract text or structured content from an image or PDF using lightning-ocr. "
            "Returns plain text, Markdown, or JSON depending on the mode."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image_base64": {
                    "type": "string",
                    "description": "Base-64-encoded image, PDF, DOCX, PPTX, XLSX, or TXT bytes. Optional if file_path is given.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Local path to an image, PDF, DOCX, PPTX, XLSX, or TXT file to OCR directly. Optional if image_base64 is given.",
                },
                "filename": {"type": "string", "default": "image.png"},
                "mode": {
                    "type": "string",
                    "enum": ["document", "ocr", "free", "figure", "describe", "find", "freeform"],
                    "default": "document",
                },
                "backend_id": {
                    "type": "string",
                    "description": "Preferred backend ID. Falls back automatically if omitted.",
                },
                "custom_prompt": {"type": "string", "default": ""},
                "find_term": {"type": "string", "default": ""},
                "preprocess": {
                    "type": "boolean",
                    "default": True,
                    "description": "Apply deskew + enhance preprocessing (improves accuracy on scans).",
                },
                "output_format": {
                    "type": "string",
                    "enum": ["text", "markdown", "json"],
                    "default": "text",
                    "description": "Output format: text, markdown (cleaned up), or structured JSON with metadata.",
                },
            },
        },
    },
    {
        "name": "list_ocr_backends",
        "description": "List all available OCR backends and their health status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "extract_tables",
        "description": (
            "Extract tables from a PDF and convert to structured data. "
            "Like Able2Extract's PDF-to-Excel conversion. "
            "Uses GLM-OCR for scanned documents and geometry analysis for text-based PDFs. "
            "Returns table data as JSON with cell contents, row/col positions, and merge info."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["pdf_base64"],
            "properties": {
                "pdf_base64": {
                    "type": "string",
                    "description": "Base-64-encoded PDF bytes.",
                },
                "filename": {"type": "string", "default": "document.pdf"},
                "use_glm_ocr": {
                    "type": "boolean",
                    "default": True,
                    "description": "Use GLM-OCR for scanned documents",
                },
                "template_name": {
                    "type": "string",
                    "default": "",
                    "description": "Smart Template name to apply",
                },
            },
        },
    },
    {
        "name": "list_templates",
        "description": "List all saved Smart Templates for table extraction.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_template",
        "description": (
            "Upload a PDF with a known table layout and save it as a Smart Template. "
            "Future documents with the same layout will be automatically extracted. "
            "Like Able2Extract's 'Create Template' feature."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["pdf_base64"],
            "properties": {
                "pdf_base64": {
                    "type": "string",
                    "description": "Base-64-encoded PDF bytes with the table layout.",
                },
                "template_name": {
                    "type": "string",
                    "default": "",
                    "description": "Name for the new template (auto-generated if empty).",
                },
                "filename": {"type": "string", "default": "template_ref.pdf"},
            },
        },
    },
    {
        "name": "ocr_batch",
        "description": (
            "Run OCR on up to 50 images, PDFs, or documents in ONE parallel call. "
            "Accepts PNG/JPEG/WebP/GIF/BMP/TIFF/ICO/AVIF/SVG images, PDF, "
            "DOCX/DOC, PPTX/PPT, XLSX/XLS, and TXT/MD. Each entry takes base64 "
            "bytes (image_base64) or a local file path (file_path). Files are "
            "processed concurrently; the result contains one content block per "
            "file (100 MB max per file)."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["files"],
            "properties": {
                "files": {
                    "type": "array",
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "image_base64": {
                                "type": "string",
                                "description": "Base-64-encoded file bytes. Optional if file_path is given.",
                            },
                            "file_path": {
                                "type": "string",
                                "description": "Local path to a file to OCR directly. Optional if image_base64 is given.",
                            },
                            "filename": {"type": "string", "default": "image.png"},
                        },
                    },
                    "description": "Array of files via image_base64 or file_path (max 50).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["document", "ocr", "free", "figure", "describe", "find", "freeform"],
                    "default": "document",
                },
                "backend_id": {"type": "string"},
                "find_term": {"type": "string", "default": ""},
                "custom_prompt": {"type": "string", "default": ""},
                "output_format": {
                    "type": "string",
                    "enum": ["text", "markdown", "json"],
                    "default": "text",
                    "description": "text, markdown, or json. For DOCX/PPTX/XLSX, markdown/json extracts source text directly (no OCR).",
                },
            },
        },
    },
    {
        "name": "get_job",
        "description": "Retrieve a previously completed OCR job by its ID.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "integer", "description": "The job ID to retrieve."},
            },
        },
    },
    {
        "name": "list_jobs",
        "description": "List recent OCR jobs from history. Returns paginated results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Max jobs to return (1-200)."},
                "offset": {"type": "integer", "default": 0, "description": "Pagination offset."},
            },
        },
    },
    {
        "name": "delete_job",
        "description": "Delete an OCR job from history by its ID.",
        "inputSchema": {
            "type": "object",
            "required": ["job_id"],
            "properties": {
                "job_id": {"type": "integer", "description": "The job ID to delete."},
            },
        },
    },
    {
        "name": "describe_capabilities",
        "description": (
            "Describe the server's capabilities, supported protocols, backends, and features. "
            "Useful for agents to understand what this server can do."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ── JSON-RPC 2.0 dispatcher ───────────────────────────────────────────────────

def _error(code: int, message: str, req_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(result: Any, req_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


@router.post("/mcp", tags=["MCP"], dependencies=[Depends(verify_api_key)])
async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    Universal MCP endpoint (JSON-RPC 2.0).
    Supports both 2025-03-26 (stateful) and 2026-07-28 (stateless) specs.
    """
    if not settings.MCP_ENABLED:
        return JSONResponse({"error": "MCP disabled"}, status_code=503)

    # ── 2026-07-28: Validate required HTTP headers ────────────────────────────
    proto_version = request.headers.get("mcp-protocol-version", "")
    mc_method = request.headers.get("mcp-method", "")
    mc_name = request.headers.get("mcp-name", "")

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(_error(-32700, "Parse error"), status_code=400)

    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    # ── 2026-07-28: server/discover RPC ───────────────────────────────────────
    if method == "server/discover":
        return JSONResponse(_ok({
            "name": "lightning-ocr",
            "version": "3.0.0",
            "description": "Universal OCR MCP server for all AI agents",
            "supportedProtocolVersions": SUPPORTED_PROTOCOL_VERSIONS,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "tools": [t["name"] for t in TOOL_LIST],
        }, req_id))

    # ── initialize (2025-03-26 handshake, still supported) ────────────────────
    if method == "initialize":
        return JSONResponse(_ok({
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lightning-ocr", "version": "3.0.0"},
        }, req_id))

    # ── notifications/initialized (2025-03-26) ────────────────────────────────
    if method == "notifications/initialized":
        return JSONResponse(_ok({}, req_id))

    # ── ping ───────────────────────────────────────────────────────────────────
    if method == "ping":
        return JSONResponse(_ok({}, req_id))

    # ── tools/list ─────────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(_ok({
            "tools": TOOL_LIST,
            "ttlMs": 300000,
            "cacheScope": "server",
        }, req_id))

    # ── tools/call ─────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args: Dict[str, Any] = params.get("arguments", {})

        # ── ocr_image ──────────────────────────────────────────────────────────
        if tool_name == "ocr_image":
            b64 = args.get("image_base64", "")
            file_path = args.get("file_path", "")
            if not b64 and not file_path:
                return JSONResponse(_error(-32602, "image_base64 or file_path is required", req_id))

            filename = args.get("filename", "image.png")
            try:
                if file_path:
                    file_size = os.path.getsize(file_path)
                    if file_size > MAX_UPLOAD_BYTES:
                        return JSONResponse(_error(-32602, f"File too large: {file_size} > {MAX_UPLOAD_BYTES} bytes", req_id))
                    image_bytes = open(file_path, "rb").read()
                    if not filename or filename == "image.png":
                        filename = os.path.basename(file_path)
                else:
                    image_bytes = base64.b64decode(b64)
            except Exception:
                return JSONResponse(_error(-32602, "Invalid base64 data or unreadable file_path", req_id))

            if len(image_bytes) > MAX_UPLOAD_BYTES:
                return JSONResponse(_error(-32602, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", req_id))

            from app.ocr import detect_content_type
            try:
                content_type = detect_content_type(image_bytes, filename)
            except Exception as exc:
                return JSONResponse(_error(-32602, str(exc), req_id))

            backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
            filename = args.get("filename", "image.png")

            from app.backends import get_backend
            validated_backend = get_backend(backend_id)
            if validated_backend is None:
                return JSONResponse(_error(-32602, f"Invalid backend_id: {backend_id}", req_id))

            if validated_backend.base_url and not _validate_backend_url(validated_backend.base_url):
                return JSONResponse(_error(-32602, f"Backend URL blocked: {validated_backend.base_url}", req_id))

            from app.ocr import run_ocr
            try:
                result = await run_ocr(
                    image_bytes=image_bytes,
                    filename=filename,
                    content_type=content_type,
                    backend_id=backend_id,
                    mode=args.get("mode", "document"),
                    find_term=args.get("find_term", ""),
                    custom_prompt=args.get("custom_prompt", ""),
                    auto_fallback=True,
                    preprocess=args.get("preprocess", True),
                    output_format=args.get("output_format", "text"),
                )
                return JSONResponse(_ok({
                    "content": [{"type": "text", "text": result["text"]}],
                    "meta": {
                        "backend": result["backend"]["id"],
                        "duration_ms": result["duration_ms"],
                        "fallback": result["fallback"],
                        "confidence": result.get("confidence", 0.0),
                        "languages": result.get("languages", []),
                    },
                }, req_id))
            except Exception as exc:
                return JSONResponse(_error(-32000, str(exc), req_id))

        # ── ocr_batch ──────────────────────────────────────────────────────────
        if tool_name == "ocr_batch":
            files = args.get("files", [])
            if not files:
                return JSONResponse(_error(-32602, "files array is required", req_id))
            if len(files) > MAX_BATCH_FILES:
                return JSONResponse(_error(
                    -32602, f"Too many files: {len(files)} > {MAX_BATCH_FILES} max", req_id))

            from app.ocr import run_ocr, detect_content_type
            from app.backends import get_backend

            backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
            mode = args.get("mode", "document")
            find_term = args.get("find_term", "")
            custom_prompt = args.get("custom_prompt", "")
            output_format = args.get("output_format", "text")

            # Load + validate each entry up front (parallel-safe, no I/O overlap)
            prepared = []
            for i, file_entry in enumerate(files):
                b64 = file_entry.get("image_base64", "")
                fpath = file_entry.get("file_path", "")
                fname = file_entry.get("filename", f"image_{i}.png") or f"image_{i}.png"

                if not b64 and not fpath:
                    prepared.append((fname, "image_base64 or file_path is required", None, None))
                    continue
                try:
                    if fpath:
                        image_bytes = open(fpath, "rb").read()
                        fresh = os.path.basename(fpath)
                        if fname == f"image_{i}.png" or not fname:
                            fname = fresh
                    else:
                        image_bytes = base64.b64decode(b64)
                except Exception:
                    prepared.append((fname, "Invalid base64 data or unreadable file_path", None, None))
                    continue
                if len(image_bytes) > MAX_UPLOAD_BYTES:
                    prepared.append((fname, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", None, None))
                    continue
                try:
                    content_type = detect_content_type(image_bytes, fname)
                except Exception as exc:
                    prepared.append((fname, str(exc), None, None))
                    continue
                prepared.append((fname, None, image_bytes, content_type))

            # Process files in parallel
            async def _process_one(fname, err, image_bytes, content_type):
                if err is not None:
                    return {"file": fname, "error": err}
                try:
                    result = await run_ocr(
                        image_bytes=image_bytes,
                        filename=fname,
                        content_type=content_type,
                        backend_id=backend_id,
                        mode=mode,
                        find_term=find_term,
                        custom_prompt=custom_prompt,
                        auto_fallback=True,
                        output_format=output_format,
                    )
                    return {
                        "file": fname,
                        "text": result["text"],
                        "backend": result["backend"]["id"],
                        "duration_ms": result["duration_ms"],
                        "fallback": result["fallback"],
                        "confidence": result.get("confidence", 0.0),
                    }
                except Exception as exc:
                    return {"file": fname, "error": str(exc)}

            results = await asyncio.gather(
                *[_process_one(*p) for p in prepared]
            )

            # Per-file content blocks (agent-friendly, no giant JSON blob)
            content = []
            for r in results:
                if "error" in r:
                    block = f"== {r['file']} ==\n[error] {r['error']}"
                else:
                    block = f"== {r['file']} ==\n{r['text']}"
                content.append({"type": "text", "text": block})

            return JSONResponse(_ok({
                "content": content,
                "meta": {
                    "count": len(results),
                    "ok": sum(1 for r in results if "error" not in r),
                    "backend": backend_id,
                },
            }, req_id))

        # ── list_ocr_backends ──────────────────────────────────────────────────
        if tool_name == "list_ocr_backends":
            from app.backends import health_all
            health = await health_all()
            return JSONResponse(_ok({"content": [{"type": "text", "text": str(health)}]}, req_id))

        # ── extract_tables ─────────────────────────────────────────────────────
        if tool_name == "extract_tables":
            b64 = args.get("pdf_base64", "")
            if not b64:
                return JSONResponse(_error(-32602, "pdf_base64 is required", req_id))
            try:
                pdf_bytes = base64.b64decode(b64)
            except Exception:
                return JSONResponse(_error(-32602, "Invalid base64 data", req_id))

            from app.extraction import extract_tables_from_bytes
            try:
                result = await extract_tables_from_bytes(
                    pdf_bytes=pdf_bytes,
                    filename=args.get("filename", "document.pdf"),
                    use_glm_ocr=args.get("use_glm_ocr", True),
                    use_templates=bool(args.get("template_name", "")),
                    template_name=args.get("template_name", ""),
                    output_formats=[],
                )

                tables_data = []
                for table in result.tables:
                    rows = []
                    for row in table.cells:
                        rows.append([cell.text.strip() for cell in row if cell.rowspan >= 0])
                    tables_data.append({
                        "num_rows": table.num_rows,
                        "num_cols": table.num_cols,
                        "title": table.title,
                        "confidence": table.confidence,
                        "strategy": table.strategy.value,
                        "rows": rows,
                    })

                return JSONResponse(_ok({
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "tables": tables_data,
                            "page_count": result.page_count,
                            "detection_time_ms": result.detection_time_ms,
                            "filename": result.filename or args.get("filename", ""),
                            "error": result.error,
                        }, ensure_ascii=False),
                    }],
                }, req_id))
            except Exception as exc:
                return JSONResponse(_error(-32000, str(exc), req_id))

        # ── list_templates ──────────────────────────────────────────────────────
        if tool_name == "list_templates":
            from app.extraction import list_templates
            templates = list_templates()
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps({"templates": templates})}],
            }, req_id))

        # ── save_template ──────────────────────────────────────────────────────
        if tool_name == "save_template":
            b64 = args.get("pdf_base64", "")
            if not b64:
                return JSONResponse(_error(-32602, "pdf_base64 is required", req_id))
            try:
                pdf_bytes = base64.b64decode(b64)
            except Exception:
                return JSONResponse(_error(-32602, "Invalid base64 data", req_id))

            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                tmp_path = f.name

            try:
                from app.extraction import save_table_as_template
                name = await asyncio.to_thread(
                    save_table_as_template,
                    tmp_path,
                    template_name=args.get("template_name", ""),
                )
                if name is None:
                    return JSONResponse(_error(-32000, "No table found to use as template", req_id))
                return JSONResponse(_ok({
                    "content": [{"type": "text", "text": json.dumps({"name": name, "message": f"Template '{name}' saved"}, ensure_ascii=False)}],
                }, req_id))
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        # ── get_job ────────────────────────────────────────────────────────────
        if tool_name == "get_job":
            job_id = args.get("job_id")
            if job_id is None:
                return JSONResponse(_error(-32602, "job_id is required", req_id))
            from app.storage import get_job
            job = await get_job(int(job_id))
            if job is None:
                return JSONResponse(_error(-32602, f"Job {job_id} not found", req_id))
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps(job, ensure_ascii=False)}],
            }, req_id))

        # ── list_jobs ──────────────────────────────────────────────────────────
        if tool_name == "list_jobs":
            limit = min(max(int(args.get("limit", 20)), 1), 200)
            offset = max(int(args.get("offset", 0)), 0)
            from app.storage import list_jobs
            jobs = await list_jobs(limit=limit, offset=offset)
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps({"jobs": jobs, "limit": limit, "offset": offset}, ensure_ascii=False)}],
            }, req_id))

        # ── delete_job ─────────────────────────────────────────────────────────
        if tool_name == "delete_job":
            job_id = args.get("job_id")
            if job_id is None:
                return JSONResponse(_error(-32602, "job_id is required", req_id))
            from app.storage import delete_job
            deleted = await delete_job(int(job_id))
            if not deleted:
                return JSONResponse(_error(-32602, f"Job {job_id} not found", req_id))
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps({"deleted": True, "job_id": int(job_id)})}],
            }, req_id))

        # ── describe_capabilities ──────────────────────────────────────────────
        if tool_name == "describe_capabilities":
            from app.backends import health_all
            health = await health_all()
            caps = {
                "name": "lightning-ocr",
                "version": "3.0.0",
                "protocol_versions": SUPPORTED_PROTOCOL_VERSIONS,
                "transports": ["stdio", "http", "sse", "websocket"],
                "tools": [t["name"] for t in TOOL_LIST],
                "ocr_modes": ["document", "ocr", "free", "figure", "describe", "find", "freeform"],
                "backends": [{"id": b["id"], "status": b["status"]} for b in health],
                "features": {
                    "pdf_support": True,
                    "batch_ocr": True,
                    "table_extraction": True,
                    "smart_templates": True,
                    "auto_fallback": True,
                    "job_history": True,
                    "max_upload_mb": 50,
                    "concurrent_limit": 8,
                },
            }
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps(caps, ensure_ascii=False)}],
            }, req_id))

        return JSONResponse(_error(-32601, f"Unknown tool: {tool_name!r}", req_id))

    # ── Unknown method ─────────────────────────────────────────────────────────
    return JSONResponse(_error(-32601, f"Method not found: {method!r}", req_id))