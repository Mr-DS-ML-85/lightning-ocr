"""
lightning-ocr · Universal MCP (Model Context Protocol) tool server
Exposes OCR as an MCP tool callable by any AI agent / LLM that speaks MCP.

Spec: https://spec.modelcontextprotocol.io/
Endpoint: POST /mcp (JSON-RPC 2.0)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import JSONResponse

from app.config import BACKENDS, settings
from app.ocr import validate_backend_url as _validate_backend_url

log = logging.getLogger("lightning_ocr.mcp")
router = APIRouter()

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
            "required": ["image_base64"],
            "properties": {
                "image_base64": {
                    "type": "string",
                    "description": "Base-64-encoded image or PDF bytes.",
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
    Supports: tools/list, tools/call, initialize
    Spec: 2025-03-26
    """
    if not settings.MCP_ENABLED:
        return JSONResponse({"error": "MCP disabled"}, status_code=503)

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(_error(-32700, "Parse error"), status_code=400)

    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse(_ok({"tools": TOOL_LIST}, req_id))

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args: Dict[str, Any] = params.get("arguments", {})

        if tool_name == "list_ocr_backends":
            from app.backends import health_all
            health = await health_all()
            return JSONResponse(_ok({"content": [{"type": "text", "text": str(health)}]}, req_id))

        if tool_name == "ocr_image":
            b64 = args.get("image_base64", "")
            if not b64:
                return JSONResponse(_error(-32602, "image_base64 is required", req_id))

            # ── Security: Validate base64 length and content ────────────────────
            try:
                image_bytes = base64.b64decode(b64)
            except Exception:
                return JSONResponse(_error(-32602, "Invalid base64 data", req_id))

            # ── Security: Limit upload size (50 MB) ──────────────────────────────
            MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
            if len(image_bytes) > MAX_UPLOAD_BYTES:
                return JSONResponse(_error(-32602, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", req_id))

            # ── Security: Validate image magic bytes ────────────────────────────
            try:
                import magic
                content_type = magic.from_buffer(image_bytes, mime=True)
                if not content_type or not content_type.startswith(("image/", "application/pdf")):
                    return JSONResponse(_error(-32602, f"Invalid file type: {content_type}", req_id))
            except ImportError:
                # magic not installed, skip validation (but log warning)
                log.warning("python-magic not installed, skipping file type validation")
                content_type = "image/png"
            except Exception:
                return JSONResponse(_error(-32602, "Invalid file: cannot determine type", req_id))

            backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
            filename = args.get("filename", "image.png")

            # ── Security: Validate backend_id exists ────────────────────────────
            from app.backends import get_backend
            validated_backend = get_backend(backend_id)
            if validated_backend is None:
                return JSONResponse(_error(-32602, f"Invalid backend_id: {backend_id}", req_id))

            # ── Security: Validate backend URL for SSRF ──────────────────────────
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
                )
                return JSONResponse(_ok({
                    "content": [{"type": "text", "text": result["text"]}],
                    "meta": {
                        "backend": result["backend"]["id"],
                        "duration_ms": result["duration_ms"],
                        "fallback": result["fallback"],
                    },
                }, req_id))
            except Exception as exc:
                return JSONResponse(_error(-32000, str(exc), req_id))

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

        if tool_name == "list_templates":
            from app.extraction import list_templates
            templates = list_templates()
            return JSONResponse(_ok({
                "content": [{"type": "text", "text": json.dumps({"templates": templates})}],
            }, req_id))

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

        return JSONResponse(_error(-32601, f"Unknown tool: {tool_name!r}", req_id))

    # ── initialize (MCP handshake) ────────────────────────────────────────────
    if method == "initialize":
        return JSONResponse(_ok({
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lightning-ocr", "version": "2.0.0"},
        }, req_id))

    return JSONResponse(_error(-32601, f"Method not found: {method!r}", req_id))