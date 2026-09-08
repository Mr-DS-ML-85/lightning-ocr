"""
lightning-ocr · MCP Transport - stdio
Standard I/O transport for local MCP clients (Claude Desktop, Cursor, etc.).
Reads JSON-RPC messages from stdin, writes responses to stdout.
Spec: 2025-03-26

Usage:
  python -m connector.transport_stdio
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,  # MUST NOT write to stdout — it's the JSON-RPC channel
)
log = logging.getLogger("lightning_ocr.stdio")


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

    # ── initialize ──────────────────────────────────────────────────────────
    if method == "initialize":
        _send(_ok({
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lightning-ocr", "version": "2.0.0"},
        }, req_id))
        return

    # ── notifications/initialized ───────────────────────────────────────────
    if method == "notifications/initialized":
        # Acknowledgment from client — no response needed
        return

    # ── ping ────────────────────────────────────────────────────────────────
    if method == "ping":
        _send(_ok({}, req_id))
        return

    # ── tools/list ──────────────────────────────────────────────────────────
    if method == "tools/list":
        from app.mcp import TOOL_LIST
        _send(_ok({"tools": TOOL_LIST}, req_id))
        return

    # ── tools/call ──────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args: Dict[str, Any] = params.get("arguments", {})

        if tool_name == "list_ocr_backends":
            from app.backends import health_all
            health = await health_all()
            _send(_ok({"content": [{"type": "text", "text": str(health)}]}, req_id))
            return

        if tool_name == "ocr_image":
            import base64
            from app.config import BACKENDS
            from app.backends import get_backend
            from app.mcp import _validate_backend_url

            b64 = args.get("image_base64", "")
            if not b64:
                _send(_error(-32602, "image_base64 is required", req_id))
                return

            try:
                image_bytes = base64.b64decode(b64)
            except Exception:
                _send(_error(-32602, "Invalid base64 data", req_id))
                return

            MAX_UPLOAD_BYTES = 50 * 1024 * 1024
            if len(image_bytes) > MAX_UPLOAD_BYTES:
                _send(_error(-32602, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", req_id))
                return

            try:
                import magic
                content_type = magic.from_buffer(image_bytes, mime=True)
                if not content_type or not content_type.startswith(("image/", "application/pdf")):
                    _send(_error(-32602, f"Invalid file type: {content_type}", req_id))
                    return
            except ImportError:
                log.warning("python-magic not installed, skipping file type validation")
                content_type = "image/png"
            except Exception:
                _send(_error(-32602, "Invalid file: cannot determine type", req_id))
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
                )
                _send(_ok({
                    "content": [{"type": "text", "text": result["text"]}],
                    "meta": {
                        "backend": result["backend"]["id"],
                        "duration_ms": result["duration_ms"],
                        "fallback": result["fallback"],
                    },
                }, req_id))
            except Exception as exc:
                _send(_error(-32000, str(exc), req_id))
            return

        _send(_error(-32601, f"Unknown tool: {tool_name!r}", req_id))
        return

    # ── Unknown method ──────────────────────────────────────────────────────
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
