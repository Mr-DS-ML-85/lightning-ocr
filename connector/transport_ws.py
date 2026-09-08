"""
lightning-ocr · MCP Transport - WebSocket
Real-time transport for MCP clients.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, Dict

from fastapi import WebSocket, WebSocketDisconnect

from app.config import settings
from app.mcp import _error, _ok, TOOL_LIST

log = logging.getLogger("lightning_ocr.connector.ws")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


async def ws_endpoint(websocket: WebSocket):
    """
    WebSocket transport for MCP clients.
    """
    if not settings.MCP_ENABLED:
        await websocket.close(code=1003)
        return
    
    await websocket.accept()
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message: Dict[str, Any] = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json(_error(-32700, "Parse error"))
                continue
            
            method = message.get("method", "")
            params = message.get("params", {})
            req_id = message.get("id")
            
            # Handle initialize
            if method == "initialize":
                await websocket.send_json(_ok({
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "lightning-ocr", "version": "3.0.0"},
                }, req_id))
            
            # Handle tools/list
            elif method == "tools/list":
                await websocket.send_json(_ok({"tools": TOOL_LIST}, req_id))
            
            # Handle tools/call
            elif method == "tools/call":
                tool_name = params.get("name", "")
                args: Dict[str, Any] = params.get("arguments", {})

                if tool_name == "list_ocr_backends":
                    from app.backends import health_all
                    health = await health_all()
                    await websocket.send_json(_ok({
                        "content": [{"type": "text", "text": str(health)}]
                    }, req_id))

                elif tool_name == "ocr_image":
                    from app.config import BACKENDS
                    from app.backends import get_backend
                    from app.mcp import _validate_backend_url

                    b64 = args.get("image_base64", "")
                    file_path = args.get("file_path", "")
                    if not b64 and not file_path:
                        await websocket.send_json(_error(-32602, "image_base64 or file_path is required", req_id))
                        continue

                    filename = args.get("filename", "image.png")
                    try:
                        if file_path:
                            file_size = os.path.getsize(file_path)
                            if file_size > MAX_UPLOAD_BYTES:
                                await websocket.send_json(_error(-32602, f"File too large: {file_size} > {MAX_UPLOAD_BYTES} bytes", req_id))
                                continue
                            image_bytes = open(file_path, "rb").read()
                            if not filename or filename == "image.png":
                                filename = os.path.basename(file_path)
                        else:
                            image_bytes = base64.b64decode(b64)
                    except Exception:
                        await websocket.send_json(_error(-32602, "Invalid base64 data or unreadable file_path", req_id))
                        continue

                    if len(image_bytes) > MAX_UPLOAD_BYTES:
                        await websocket.send_json(_error(-32602, f"File too large: {len(image_bytes)} > {MAX_UPLOAD_BYTES} bytes", req_id))
                        continue

                    from app.ocr import detect_content_type
                    try:
                        content_type = detect_content_type(image_bytes, filename)
                    except Exception as exc:
                        await websocket.send_json(_error(-32602, str(exc), req_id))
                        continue

                    backend_id = args.get("backend_id") or (BACKENDS[0].id if BACKENDS else "tesseract")
                    validated_backend = get_backend(backend_id)
                    if validated_backend is None:
                        await websocket.send_json(_error(-32602, f"Invalid backend_id: {backend_id}", req_id))
                        continue

                    if validated_backend.base_url and not _validate_backend_url(validated_backend.base_url):
                        await websocket.send_json(_error(-32602, f"Backend URL blocked: {validated_backend.base_url}", req_id))
                        continue

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
                        await websocket.send_json(_ok({
                            "content": [{"type": "text", "text": result["text"]}],
                            "meta": {
                                "backend": result["backend"]["id"],
                                "duration_ms": result["duration_ms"],
                                "fallback": result["fallback"],
                            },
                        }, req_id))
                    except Exception as exc:
                        await websocket.send_json(_error(-32000, str(exc), req_id))

                else:
                    await websocket.send_json(_error(-32601, f"Unknown tool: {tool_name}", req_id))
            
            else:
                await websocket.send_json(_error(-32601, f"Method not found: {method}", req_id))
    
    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
