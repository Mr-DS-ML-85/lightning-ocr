"""
lightning-ocr · MCP Transport - SSE (Server-Sent Events)
Legacy transport for compatibility with older MCP clients.
Spec: 2024-11-05
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.mcp import _error, _ok, TOOL_LIST

log = logging.getLogger("lightning_ocr.connector.sse")
router = APIRouter()


@router.get("/mcp/sse", tags=["MCP-SSE"])
async def sse_connect(request: Request):
    """
    SSE connect endpoint for legacy MCP clients.
    Returns a stream of SSE events.
    """
    if not settings.MCP_ENABLED:
        return JSONResponse({"error": "MCP disabled"}, status_code=503)
    
    async def event_stream():
        # Send initial handshake
        yield 'data: {"type": "serverInfo", "name": "lightning-ocr", "version": "2.0.0"}\n\n'
        
        # Keep connection alive
        while True:
            yield 'data: {"type": "ping"}\n\n'
            await asyncio.sleep(30)
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/mcp/message", tags=["MCP-SSE"])
async def sse_message(request: Request):
    """
    SSE message endpoint for legacy MCP clients.
    """
    if not settings.MCP_ENABLED:
        return JSONResponse({"error": "MCP disabled"}, status_code=503)
    
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse(_error(-32700, "Parse error"), status_code=400)
    
    # Handle tools/list
    if body.get("type") == "tools/list":
        return JSONResponse({"type": "tools", "tools": TOOL_LIST})
    
    return JSONResponse(_error(-32601, "Unknown message type"), status_code=400)
