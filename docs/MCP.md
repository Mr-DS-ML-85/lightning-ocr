# ⚡ lightning-ocr — Universal MCP Connector Reference

**Protocol:** JSON-RPC 2.0  
**Spec versions:** `2025-03-26` (Streamable HTTP — primary) · `2024-11-05` (SSE — legacy)  
**Security:** Bearer token auth via `Authorization: Bearer <API_KEY>` (when `API_KEY` is set)

---

## Table of Contents

- [Transports](#transports)
- [Security](#security)
- [Quick Setup Per Agent](#quick-setup-per-agent)
- [MCP Lifecycle (all transports)](#mcp-lifecycle-all-transports)
- [Tools Reference](#tools-reference)
- [Prompts](#prompts)
- [Resources](#resources)
- [MCP Discovery](#mcp-discovery)
- [JSON-RPC 2.0 Quick Reference](#json-rpc-20-quick-reference)
- [Auto-Install](#auto-install)

---

## Transports

| Transport | Endpoint | Spec | Used by |
|-----------|----------|------|---------|
| **stdio** | subprocess stdin/stdout | 2024-11-05 | Claude Desktop, Claude Code, Cursor, Copilot, Continue, Cline, Kilocode, OpenCode, Codex, OpenClaw, PicoClaw |
| **Streamable HTTP** | `POST /mcp` | **2025-03-26** | Cline (remote), Kilocode (remote), RooCode, NemoClaw, Hermes, OpenAI Agents SDK |
| **SSE (legacy)** | `GET /mcp/sse` + `POST /mcp/message` | 2024-11-05 | ADK (McpToolset), older clients, spec 2024-11-05 |
| **WebSocket** | `WS /mcp/ws` | custom | Hermes Agent, NemoClaw, custom browser agents |

---

## Security

### Authentication

All MCP endpoints enforce bearer token authentication when `API_KEY` is configured:

```http
Authorization: Bearer your-api-key
```

**Without auth header (when API_KEY is set):**
```json
{"jsonrpc": "2.0", "id": null, "error": {"code": -32000, "message": "Missing Authorization header"}}
```

**With wrong key:**
```json
{"jsonrpc": "2.0", "id": null, "error": {"code": -32000, "message": "Invalid API key"}}
```

### Input Validation

The MCP server validates all image uploads:

- **File type:** Must be image (png/jpg/webp/gif) or PDF via `python-magic` magic bytes
- **Size limit:** Maximum 50 MB per upload
- **Image integrity:** Validated via Pillow `verify()`
- **Backend validation:** Backend ID must exist in registry
- **SSRF protection:** Backend URLs must not point to reserved IP ranges

### MCP Endpoints Requiring Auth

| Endpoint | Auth Required | Notes |
|----------|--------------|-------|
| `POST /mcp` | ✅ | Primary endpoint |
| `GET /mcp/sse` | ❌ | SSE streaming (open) |
| `POST /mcp/message` | ❌ | SSE messages (open) |
| `WS /mcp/ws` | ❌ | WebSocket (open at transport level) |
| `GET /.well-known/mcp` | ❌ | Discovery must work without auth |

---

## Quick Setup Per Agent

### Claude Desktop

Config: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)  
Config: `%APPDATA%\Claude\claude_desktop_config.json` (Windows)

```json
{
  "mcpServers": {
    "lightning-ocr": {
      "command": "python",
      "args": ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
      "cwd": "/path/to/lightning-ocr",
      "env": {"PYTHONPATH": "/path/to/lightning-ocr"}
    }
  }
}
```

### Cursor

Config: `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project)

```json
{
  "mcpServers": {
    "lightning-ocr": {"url": "http://localhost:8000/mcp"}
  }
}
```

### GitHub Copilot (VS Code)

Config: `github/copilot/mcp.json` in repo root

```json
{
  "mcpServers": {
    "lightning-ocr": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

> **Note:** Copilot uses `"servers"` as the root key, not `"mcpServers"`.

### Cline (VS Code)

Settings > Cline > MCP Servers, or edit `cline_mcp_settings.json`:

```json
{
  "mcpServers": {
    "lightning-ocr": {
      "url": "http://localhost:8000/mcp",
      "alwaysAllow": ["ocr_image", "list_ocr_backends"],
      "disabled": false
    }
  }
}
```

### Google ADK

```python
from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseServerParams

agent = LlmAgent(
    model="gemini-2.0-flash",
    tools=[McpToolset(
        connection_params=SseServerParams(
            url="http://localhost:8000/mcp/sse"
        ),
        tool_filter=["ocr_image", "list_ocr_backends"],
    )]
)
```

### OpenAI Agents SDK

```python
from agents.mcp import MCPServerStreamableHttp

server = MCPServerStreamableHttp(
    url="http://localhost:8000/mcp",
    headers={"Authorization": "Bearer your-api-key"},
)
```

---

## MCP Lifecycle (all transports)

```
Client                           Server
  │                                │
  │── initialize ─────────────────▶│
  │◀── result (capabilities) ──────│
  │── notifications/initialized ──▶│  (no response)
  │── tools/list ─────────────────▶│
  │◀── result {tools:[...]} ───────│
  │── tools/call ocr_image ───────▶│
  │◀── result {content:[...]} ─────│
  │── ping ───────────────────────▶│
  │◀── result {} ──────────────────│
```

### Initialize Response

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "lightning-ocr", "version": "2.0.0"}
  }
}
```

---

## Tools Reference

### `ocr_image`

Extract text from a single image or PDF.

| Argument | Type | Required | Default | Description |
|----------|------|----------|---------|-------------|
| `image_base64` | string | ✅ | — | Base-64 encoded image bytes (max 50 MB) |
| `filename` | string | — | `image.png` | Filename hint for content-type detection |
| `mode` | string | — | `document` | OCR mode: `document`, `ocr`, `free`, `figure`, `describe`, `find`, `freeform` |
| `backend_id` | string | — | auto | Specific backend ID (e.g., `glm-ocr`, `tesseract`) |
| `find_term` | string | — | `""` | Term to locate (mode=`find`) |
| `custom_prompt` | string | — | `""` | Custom prompt (mode=`freeform`) |

**Response:**
```json
{
  "content": [{"type": "text", "text": "extracted text here..."}],
  "meta": {
    "backend": "glm-ocr",
    "model": "GLM-OCR",
    "mode": "document",
    "duration_ms": 1240,
    "fallback": false,
    "job_id": 42
  }
}
```

**Error response:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "error": {
    "code": -32602,
    "message": "Invalid file type: application/x-msdownload"
  }
}
```

### `list_ocr_backends`

No arguments. Returns all configured backends with their health status.

**Response:**
```json
{
  "content": [{
    "type": "text",
    "text": "[{\"id\": \"glm-ocr\", \"status\": \"ok\", ...}]"
  }]
}
```

---

## Prompts

Pre-built prompts (for `prompts/get`):

| Prompt | Arguments | Description |
|--------|-----------|-------------|
| `extract_document` | `filename?`, `backend_id?` | OCR document and return Markdown |
| `analyse_invoice` | `filename` | OCR invoice and extract financial fields into JSON |

---

## Resources

| URI | MIME | Description |
|-----|------|-------------|
| `lightning-ocr://config` | `application/json` | Current server config (sanitised — API key hidden) |
| `lightning-ocr://backends` | `application/json` | Live backend health status |

---

## MCP Discovery

```http
GET /.well-known/mcp
```

```json
{
  "name": "lightning-ocr",
  "version": "2.0.0",
  "description": "Universal OCR MCP server for all AI agents",
  "protocol": "2025-03-26",
  "protocol_latest": "2025-03-26",
  "mcp_endpoint": "/mcp",
  "sse_endpoint": "/mcp/sse",
  "ws_endpoint": "ws://localhost:8000/mcp/ws",
  "tools": [
    "ocr_image", "list_ocr_backends"
  ],
  "prompts": ["extract_document", "analyse_invoice"],
  "resources": ["lightning-ocr://config"],
  "auth": {
    "type": "bearer",
    "header": "Authorization",
    "format": "Bearer <token>"
  }
}
```

---

## JSON-RPC 2.0 Quick Reference

```bash
# Discovery
curl http://localhost:8000/.well-known/mcp

# Initialize handshake
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"my-agent","version":"1.0"},"capabilities":{}}}'

# List tools
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# OCR an image (with auth)
B64=$(base64 -w0 scan.png)
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",
       \"params\":{\"name\":\"ocr_image\",
                   \"arguments\":{\"image_base64\":\"$B64\",\"mode\":\"document\"}}}"

# List backends
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"tools/call",
       "params":{"name":"list_ocr_backends","arguments":{}}}'
```

---

## Auto-Install

Run the installer script to write configs for all supported agents automatically:

```bash
chmod +x install.sh
./install.sh
```

Or manually copy from `configs/` directory.