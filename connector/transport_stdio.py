"""
lightning-ocr · MCP Transport - stdio
Standard I/O transport for local MCP clients (Claude Desktop, Cursor, etc.).
Reads JSON-RPC messages from stdin, writes responses to stdout.
Specs: 2025-03-26 (stateful) · 2026-07-28 (stateless)

Usage:
  python -m connector.transport_stdio
  python -m connector.transport_stdio --legacy25
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any, Dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,  # MUST NOT write to stdout — it's the JSON-RPC channel
)
log = logging.getLogger("lightning_ocr.stdio")

from app.ocr import MAX_UPLOAD_BYTES, MAX_BATCH_FILES, detect_content_type, run_ocr


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="lightning-ocr stdio MCP transport")
    parser.add_argument(
        "--legacy25",
        action="store_true",
        default=False,
        help="Only advertise 2025-03-26 protocol (for clients that don't support 2026-07-28)",
    )
    return parser.parse_args()


_args = _parse_args()

if _args.legacy25:
    SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26"]
    LATEST_PROTOCOL_VERSION = "2025-03-26"
else:
    SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2026-07-28"]
    LATEST_PROTOCOL_VERSION = "2026-07-28"


def _error(code: int, message: str, req_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _ok(result: Any, req_id: Any = None) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _send(msg: Dict[str, Any]) -> None:
    """Write a JSON-RPC message to stdout (newline-delimited)."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


async def _handle_message(body: Dict[str, Any]) -> None:
    """Dispatch a single JSON-RPC message and write response to stdout."""
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    # Notifications (no id) get no response
    is_notification = req_id is None

    # ── server/discover (2026-07-28) ──────────────────────────────────────────
    if method == "server/discover":
        from app.mcp import TOOL_LIST
        _send(_ok({
            "name": "lightning-ocr",
            "version": "3.0.0",
            "description": "Universal OCR MCP server for all AI agents",
            "supportedProtocolVersions": SUPPORTED_PROTOCOL_VERSIONS,
            "capabilities": {"tools": {"listChanged": False}},
            "tools": [t["name"] for t in TOOL_LIST],
        }, req_id))
        return

    # ── initialize (2025-03-26 handshake, still supported) ────────────────────
    if method == "initialize":
        client_version = params.get("protocolVersion", LATEST_PROTOCOL_VERSION)
        negotiated = client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        _send(_ok({
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lightning-ocr", "version": "3.0.0"},
        }, req_id))
        return

    # ── notifications/initialized ─────────────────────────────────────────────
    if method == "notifications/initialized":
        return

    # ── ping ──────────────────────────────────────────────────────────────────
    if method == "ping":
        _send(_ok({}, req_id))
        return

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        from app.mcp import TOOL_LIST
        _send(_ok({"tools": TOOL_LIST, "ttlMs": 300000, "cacheScope": "server"}, req_id))
        return

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args: Dict[str, Any] = params.get("arguments", {})

        # ── ocr_image ─────────────────────────────────────────────────────────
        if tool_name == "ocr_image":
            import base64
            from app.config import BACKENDS
            from app.backends import get_backend
            from app.mcp import _validate_backend_url

            b64 = args.get("image_base64", "")
            file_path = args.get("file_path", "")
            if not b64 and not file_path:
                _send(_error(-32602, "image_base64 or file_path is required", req_id))
                return

            filename = args.get("filename", "image.png")
            try:
                if file_path:
                    file_size = os.path.getsize(file_path)
                    if file_size > MAX_UPLOAD_BYTES:
                        _send(_error(-32602, f"File too large: {file_size} > {MAX_UPLOAD_BYTES} bytes", req_id))
                        return
                    image_bytes = open(file_path, "rb").read()
                    if not filename or filename == "image.png":
                        filename = os.path.basename(file_path)
                else:
                    image_bytes = base64.b64decode(b64)
            except Exception:
                _send(_error(-32602, "Invalid base64 data or unreadable file_path", req_id))
                return

            if len(image_bytes) > MAX_UPLOAD_BYTES:
                _send(_error(-32602, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", req_id))
                return

            from app.ocr import detect_content_type
            try:
                content_type = detect_content_type(image_bytes, filename)
            except Exception as exc:
                _send(_error(-32602, str(exc), req_id))
                return

            backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
            validated_backend = get_backend(backend_id)
            if validated_backend is None:
                _send(_error(-32602, f"Invalid backend_id: {backend_id}", req_id))
                return

            if validated_backend.base_url and not _validate_backend_url(validated_backend.base_url):
                _send(_error(-32602, f"Backend URL blocked: {validated_backend.base_url}", req_id))
                return

            from app.ocr import run_ocr
            try:
                result = await run_ocr(
                    image_bytes=image_bytes,
                    filename=args.get("filename", "image.png"),
                    content_type=content_type,
                    backend_id=backend_id,
                    mode=args.get("mode", "document"),
                    find_term=args.get("find_term", ""),
                    custom_prompt=args.get("custom_prompt", ""),
                    auto_fallback=True,
                    preprocess=args.get("preprocess", True),
                    output_format=args.get("output_format", "text"),
                )
                _send(_ok({
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
                _send(_error(-32000, str(exc), req_id))
            return

        # ── ocr_batch ─────────────────────────────────────────────────────────
        if tool_name == "ocr_batch":
            import base64
            from app.config import BACKENDS
            from app.ocr import run_ocr, detect_content_type

            files = args.get("files", [])
            if not files:
                _send(_error(-32602, "files array is required", req_id))
                return
            if len(files) > MAX_BATCH_FILES:
                _send(_error(-32602, f"Too many files: {len(files)} > {MAX_BATCH_FILES} max", req_id))
                return

            backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
            mode = args.get("mode", "document")
            find_term = args.get("find_term", "")
            custom_prompt = args.get("custom_prompt", "")
            output_format = args.get("output_format", "text")

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
                        if fname == f"image_{i}.png" or not fname:
                            fname = os.path.basename(fpath)
                    else:
                        image_bytes = base64.b64decode(b64)
                except Exception:
                    prepared.append((fname, "Invalid base64 or unreadable file_path", None, None))
                    continue
                if len(image_bytes) > MAX_UPLOAD_BYTES:
                    prepared.append((fname, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", None, None))
                    continue
                try:
                    ct = detect_content_type(image_bytes, fname)
                except Exception as exc:
                    prepared.append((fname, str(exc), None, None))
                    continue
                prepared.append((fname, None, image_bytes, ct))

            async def _process_one(fname, err, image_bytes, ct):
                if err is not None:
                    return {"file": fname, "error": err}
                try:
                    r = await run_ocr(image_bytes=image_bytes, filename=fname, content_type=ct,
                                      backend_id=backend_id, mode=mode, find_term=find_term,
                                      custom_prompt=custom_prompt, auto_fallback=True,
                                      output_format=output_format)
                    return {"file": fname, "text": r["text"], "backend": r["backend"]["id"],
                            "duration_ms": r["duration_ms"], "confidence": r.get("confidence", 0.0)}
                except Exception as exc:
                    return {"file": fname, "error": str(exc)}

            results = await asyncio.gather(*[_process_one(*p) for p in prepared])

            content = []
            for r in results:
                if "error" in r:
                    content.append({"type": "text", "text": f"== {r['file']} ==\n[error] {r['error']}"})
                else:
                    content.append({"type": "text", "text": f"== {r['file']} ==\n{r['text']}"})

            _send(_ok({
                "content": content,
                "meta": {"count": len(results), "ok": sum(1 for r in results if "error" not in r), "backend": backend_id},
            }, req_id))
            return

        # ── list_ocr_backends ─────────────────────────────────────────────────
        if tool_name == "list_ocr_backends":
            from app.backends import health_all
            health = await health_all()
            _send(_ok({"content": [{"type": "text", "text": str(health)}]}, req_id))
            return

        # ── get_job ───────────────────────────────────────────────────────────
        if tool_name == "get_job":
            from app.storage import get_job
            job_id = args.get("job_id")
            if job_id is None:
                _send(_error(-32602, "job_id is required", req_id))
                return
            job = await get_job(int(job_id))
            if job is None:
                _send(_error(-32602, f"Job {job_id} not found", req_id))
                return
            _send(_ok({"content": [{"type": "text", "text": json.dumps(job, ensure_ascii=False)}]}, req_id))
            return

        # ── list_jobs ─────────────────────────────────────────────────────────
        if tool_name == "list_jobs":
            from app.storage import list_jobs
            limit = min(max(int(args.get("limit", 20)), 1), 200)
            offset = max(int(args.get("offset", 0)), 0)
            jobs = await list_jobs(limit=limit, offset=offset)
            _send(_ok({
                "content": [{"type": "text", "text": json.dumps({"jobs": jobs, "limit": limit, "offset": offset}, ensure_ascii=False)}],
            }, req_id))
            return

        # ── delete_job ────────────────────────────────────────────────────────
        if tool_name == "delete_job":
            from app.storage import delete_job
            job_id = args.get("job_id")
            if job_id is None:
                _send(_error(-32602, "job_id is required", req_id))
                return
            deleted = await delete_job(int(job_id))
            if not deleted:
                _send(_error(-32602, f"Job {job_id} not found", req_id))
                return
            _send(_ok({"content": [{"type": "text", "text": json.dumps({"deleted": True, "job_id": int(job_id)})}]}, req_id))
            return

        # ── describe_capabilities ─────────────────────────────────────────────
        if tool_name == "describe_capabilities":
            from app.mcp import TOOL_LIST
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
            _send(_ok({"content": [{"type": "text", "text": json.dumps(caps, ensure_ascii=False)}]}, req_id))
            return

        # ── extract_tables ────────────────────────────────────────────────────
        if tool_name == "extract_tables":
            import base64
            b64 = args.get("pdf_base64", "")
            if not b64:
                _send(_error(-32602, "pdf_base64 is required", req_id))
                return
            try:
                pdf_bytes = base64.b64decode(b64)
            except Exception:
                _send(_error(-32602, "Invalid base64 data", req_id))
                return
            from app.extraction import extract_tables_from_bytes
            try:
                result = await extract_tables_from_bytes(
                    pdf_bytes=pdf_bytes, filename=args.get("filename", "document.pdf"),
                    use_glm_ocr=args.get("use_glm_ocr", True),
                    use_templates=bool(args.get("template_name", "")),
                    template_name=args.get("template_name", ""), output_formats=[],
                )
                tables_data = []
                for table in result.tables:
                    rows = []
                    for row in table.cells:
                        rows.append([cell.text.strip() for cell in row if cell.rowspan >= 0])
                    tables_data.append({"num_rows": table.num_rows, "num_cols": table.num_cols,
                                        "title": table.title, "confidence": table.confidence,
                                        "strategy": table.strategy.value, "rows": rows})
                _send(_ok({"content": [{"type": "text", "text": json.dumps({
                    "tables": tables_data, "page_count": result.page_count,
                    "detection_time_ms": result.detection_time_ms,
                    "filename": result.filename or args.get("filename", ""),
                    "error": result.error,
                }, ensure_ascii=False)}]}, req_id))
            except Exception as exc:
                _send(_error(-32000, str(exc), req_id))
            return

        # ── list_templates ────────────────────────────────────────────────────
        if tool_name == "list_templates":
            from app.extraction import list_templates
            templates = list_templates()
            _send(_ok({"content": [{"type": "text", "text": json.dumps({"templates": templates})}]}, req_id))
            return

        # ── save_template ────────────────────────────────────────────────────
        if tool_name == "save_template":
            import base64, tempfile
            b64 = args.get("pdf_base64", "")
            if not b64:
                _send(_error(-32602, "pdf_base64 is required", req_id))
                return
            try:
                pdf_bytes = base64.b64decode(b64)
            except Exception:
                _send(_error(-32602, "Invalid base64 data", req_id))
                return
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_bytes)
                tmp_path = f.name
            try:
                from app.extraction import save_table_as_template
                name = await asyncio.to_thread(save_table_as_template, tmp_path,
                                                template_name=args.get("template_name", ""))
                if name is None:
                    _send(_error(-32000, "No table found to use as template", req_id))
                    return
                _send(_ok({"content": [{"type": "text", "text": json.dumps(
                    {"name": name, "message": f"Template '{name}' saved"}, ensure_ascii=False)}]}, req_id))
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return

        _send(_error(-32601, f"Unknown tool: {tool_name!r}", req_id))
        return

    # ── Unknown method ────────────────────────────────────────────────────────
    if not is_notification:
        _send(_error(-32601, f"Method not found: {method!r}", req_id))


async def _main() -> None:
    """Read JSON-RPC messages from stdin, dispatch, write responses to stdout."""
    log.info("lightning-ocr stdio transport starting")
    loop = asyncio.get_event_loop()

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            line = await reader.readline()
            if not line:
                # stdin closed — client disconnected
                log.info("stdin closed, shutting down")
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            try:
                body = json.loads(line_str)
            except json.JSONDecodeError:
                _send(_error(-32700, "Parse error"))
                continue

            # Handle batch requests (JSON-RPC array)
            if isinstance(body, list):
                for msg in body:
                    await _handle_message(msg)
            else:
                await _handle_message(body)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("Unexpected error: %s", exc, exc_info=True)

    log.info("lightning-ocr stdio transport stopped")


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
