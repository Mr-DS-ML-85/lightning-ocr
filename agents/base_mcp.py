"""
lightning-ocr · Base MCP Server
Shared MCP server implementation for all agents.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

log = logging.getLogger("lightning_ocr.agents.base_mcp")


class BaseMCPServer:
    """Base MCP server for agents."""
    
    name: str = "base-agent"
    version: str = "1.0.0"
    tools: List[Dict[str, Any]] = []
    
    def __init__(self):
        self.app = FastAPI(title=self.name, version=self.version)
        self.setup_routes()
    
    def setup_routes(self):
        """Setup FastAPI routes."""
        
        @self.app.post("/mcp")
        async def mcp_endpoint(request: Dict[str, Any]):
            """MCP HTTP endpoint."""
            method = request.get("method", "")
            req_id = request.get("id")
            
            if method == "initialize":
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": self.name, "version": self.version},
                    },
                })
            
            elif method == "tools/list":
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"tools": self.tools},
                })
            
            elif method == "tools/call":
                tool_name = request.get("params", {}).get("name", "")
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Tool {tool_name} not implemented"},
                })
            
            else:
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method {method} not found"},
                })
        
        @self.app.websocket("/mcp/ws")
        async def ws_endpoint(websocket: WebSocket):
            """MCP WebSocket endpoint."""
            await websocket.accept()
            try:
                while True:
                    data = await websocket.receive_text()
                    message: Dict[str, Any] = json.loads(data)
                    
                    method = message.get("method", "")
                    req_id = message.get("id")
                    
                    if method == "initialize":
                        await websocket.send_json({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {"tools": {}},
                                "serverInfo": {"name": self.name, "version": self.version},
                            },
                        })
                    
                    elif method == "tools/list":
                        await websocket.send_json({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {"tools": self.tools},
                        })
                    
                    elif method == "tools/call":
                        tool_name = message.get("params", {}).get("name", "")
                        await websocket.send_json({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32601, "message": f"Tool {tool_name} not implemented"},
                        })
                    
                    else:
                        await websocket.send_json({
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32601, "message": f"Method {method} not found"},
                        })
            
            except WebSocketDisconnect:
                log.info("WebSocket client disconnected")
    
    def run(self, host: str = "0.0.0.0", port: int = 8000):
        """Run the server."""
        import uvicorn
        uvicorn.run(self.app, host=host, port=port)
