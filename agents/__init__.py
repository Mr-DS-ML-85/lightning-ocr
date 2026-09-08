"""
lightning-ocr · Base MCP Agent
Shared MCP engine for all agents.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("lightning_ocr.agents.base")


class BaseMCPAgent:
    """Base class for all MCP agents."""
    
    name: str = "base-agent"
    version: str = "1.0.0"
    tools: List[Dict[str, Any]] = []
    
    def __init__(self, mcp_url: str = "http://localhost:8000/mcp", api_key: str = ""):
        self.mcp_url = mcp_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def initialize(self) -> Dict[str, Any]:
        """Initialize MCP connection."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "clientInfo": {"name": self.name, "version": self.version},
                "capabilities": {},
            },
        }
        
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        response = await self.client.post(
            f"{self.mcp_url}/mcp",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()
    
    async def list_tools(self) -> List[Dict[str, Any]]:
        """List available tools."""
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        
        response = await self.client.post(
            f"{self.mcp_url}/mcp",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("result", {}).get("tools", [])
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Call an MCP tool."""
        payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }
        
        response = await self.client.post(
            f"{self.mcp_url}/mcp",
            json=payload,
        )
        response.raise_for_status()
        return response.json()
    
    async def close(self):
        """Close the connection."""
        await self.client.aclose()
