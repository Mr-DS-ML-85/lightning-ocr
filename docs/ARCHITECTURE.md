# ⚡ lightning-ocr — System Architecture

**Version:** 3.0.0  
**Protocol:** MCP 2026-07-28 (stateless) + 2025-03-26 (stateful)  
**Backend Backends:** GLM-OCR (llama.cpp), DeepSeek-OCR-WebUI, Tesseract, EasyOCR  
**Transports:** Streamable HTTP · SSE (legacy) · WebSocket · stdio

---

## Table of Contents

- [High-Level Overview](#high-level-overview)
- [End-to-End Flow](#end-to-end-flow)
  - [Step-by-Step Walkthrough](#step-by-step-walkthrough)
  - [The Fallback Chain](#the-fallback-chain)
- [System Components](#system-components)
- [Directory Structure](#directory-structure)
- [Module Dependencies](#module-dependencies)
- [Data Flow](#data-flow)
  - [REST Path](#rest-path)
  - [MCP Path](#mcp-path)
  - [WebSocket Path](#websocket-path)
- [Backend Architecture](#backend-architecture)
  - [Backend Registry](#backend-registry)
  - [Auto-Fallback Chain](#auto-fallback-chain)
  - [Concurrency Control](#concurrency-control)
- [Security Architecture](#security-architecture)
  - [SSRF Protection](#ssrf-protection)
  - [Input Validation](#input-validation)
  - [Authentication](#authentication)
  - [Injection Protection](#injection-protection)
- [MCP Server Architecture](#mcp-server-architecture)
  - [Transport Layer](#transport-layer)
  - [JSON-RPC Dispatch](#json-rpc-dispatch)
  - [Tool Registration](#tool-registration)
- [AI Agent Architecture](#ai-agent-architecture)
  - [Agent Types](#agent-types)
  - [Agent MCP Servers](#agent-mcp-servers)
  - [PDF Agent](#pdf-agent)
- [Storage Architecture](#storage-architecture)
- [GPU Architecture](#gpu-architecture)
  - [Layer Offloading](#layer-offloading)
  - [VRAM Budget](#vram-budget)
- [Scaling and Performance](#scaling-and-performance)

---

## High-Level Overview

lightning-ocr is a **microservice-based OCR hub** that wraps multiple OCR backends behind a unified REST API and MCP (Model Context Protocol) server. It is designed to be:

- **Hardware-agnostic** — runs on CPU, NVIDIA CUDA, Intel SYCL/Vulkan, and Apple Metal
- **Backend-redundant** — auto-fallback chain ensures OCR never fails
- **Protocol-flexible** — supports REST, MCP Streamable HTTP, SSE (legacy), WebSocket, and stdio
- **Security-hardened** — SSRF protection, input validation, bearer auth, SQLite injection protection

```
                          ┌─────────────────────────────────────┐
                          │         External Clients            │
                          │  Claude · Cursor · Copilot · ADK   │
                          │  Browser · curl · Custom Scripts   │
                          └──────────┬──────────────────────────┘
                                     │
                      ┌──────────────┼──────────────┐
                      │              │              │
                   HTTP/REST      MCP/JSON-RPC    WebSocket
                      │              │              │
                      ▼              ▼              ▼
               ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
               │  FastAPI    │ │  MCP Core   │ │  WebSocket  │
               │  /api/*     │ │  /mcp       │ │  /mcp/ws    │
               └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
                      │              │              │
                      └──────────────┼──────────────┘
                                     ▼
                          ┌─────────────────────┐
                          │   OCR Dispatcher    │
                          │   app/ocr.py        │
                          │   [Semaphore: max 8]│
                          └──────┬──────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
                 ▼               ▼               ▼
          ┌────────────┐ ┌────────────┐ ┌────────────┐
          │  llama.cpp │ │ DeepSeek   │ │ Tesseract  │
          │  GLM-OCR   │ │ WebUI      │ │ (local)    │
          └────────────┘ └────────────┘ └────────────┘
```

---

## End-to-End Flow

### Step-by-Step Walkthrough

**When you upload an image to `/api/ocr`:**

```
Step 1: HTTP POST /api/ocr
        - Multipart form-data: file + backend_id + mode + optional params

Step 2: FastAPI route handler
        - Extracts file bytes and parameters
        - Sets content_type from file extension or python-magic
        - Calls run_ocr()

Step 3: Security validation (app/ocr.py)
        - Check: file not empty
        - Check: file size < 50 MB
        - Check: file type is image/* or application/pdf (python-magic)
        - Check: image integrity (PIL verify)

Step 4: Backend lookup (app/backends.py)
        - Find backend by backend_id from BACKENDS list
        - Return 404 if not found or disabled

Step 5: Build fallback chain
        - Primary: requested backend
        - Fallbacks: all enabled backends sorted by priority
        - Tesseract is ALWAYS added as final fallback if enabled

Step 6: Concurrency semaphore (asyncio.Semaphore)
        - Max 8 concurrent OCR jobs
        - Prevents GPU OOM on high load

Step 7: Try each backend in chain
        - Each backend has a try/except handler
        - On success: save job to SQLite, return result
        - On failure: log error, save failure to SQLite, try next backend

Step 8: Return result
        - JSON: {job_id, backend, model, mode, text, fallback, duration_ms}

Step 9: All backends failed?
        - Return HTTP 500 with last error message
```

### The Fallback Chain

```
Request: backend_id="glm-ocr", auto_fallback=true

Chain:
  1. GLM-OCR (llama.cpp)     priority 0   ← primary
  2. DeepSeek-OCR-WebUI      priority 1   ← secondary
  3. Tesseract (local)       priority 10  ← tertiary (always available)
  4. EasyOCR (local)         priority 11  ← quaternary (optional)

If GLM-OCR fails (server down, timeout, bad image):
  → Try DeepSeek-OCR
If DeepSeek also fails:
  → Try Tesseract (always works, needs zero model RAM)
If Tesseract fails:
  → Try EasyOCR (if installed)
If ALL fail:
  → Return HTTP 500
```

> **Key design decision:** Tesseract is always added as the final fallback regardless of `TESSERACT_ENABLED` — this ensures the system never returns "all backends failed" when Tesseract is available.

---

## System Components

| Component | File | Role |
|-----------|------|------|
| **Main App** | `app/main.py` | FastAPI application, route registration, middleware, startup |
| **OCR Dispatch** | `app/ocr.py` | Core OCR logic — input validation, backend routing, fallback chain, concurrency |
| **MCP Server** | `app/mcp.py` | JSON-RPC 2.0 MCP endpoint — tools/list, tools/call, initialize |
| **Backend Registry** | `app/backends.py` | Backend configuration, health checks, model listing |
| **Fallback Engines** | `app/fallback.py` | Tesseract and EasyOCR local implementations |
| **Storage** | `app/storage.py` | SQLite job history — CRUD operations, injection protection |
| **Config** | `app/config.py` | pydantic-settings based configuration, backend loader |
| **UI** | `app/ui.py` | Single-file 2026 glassmorphism HTML dashboard |
| **SSE Transport** | `connector/transport_sse.py` | Legacy SSE MCP transport |
| **WS Transport** | `connector/transport_ws.py` | WebSocket MCP transport |
| **Middleware** | `connector/middleware.py` | Security headers, request logging |
| **Auth** | `connector/auth.py` | Bearer token authentication, OAuth metadata |
| **Server Constants** | `connector/server.py` | MCP protocol version, server info |
| **PDF Agent** | `agents/pdf_agent.py` | Multi-page PDF analysis via MCP |
| **Base Agent** | `agents/base_mcp.py` | Shared MCP server base for all agents |

---

## Directory Structure

```
lightning-ocr/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI app, routes, middleware
│   ├── mcp.py            # MCP JSON-RPC 2.0 server (security-hardened)
│   ├── ocr.py            # OCR dispatch + fallback + concurrency
│   ├── backends.py       # Backend registry + health checks
│   ├── storage.py        # SQLite job history (injection-protected)
│   ├── fallback.py       # Tesseract + EasyOCR local fallbacks
│   ├── config.py         # pydantic-settings configuration
│   └── ui.py             # Glassmorphism HTML dashboard
├── connector/
│   ├── __init__.py
│   ├── server.py         # MCP server constants
│   ├── transport_sse.py  # SSE transport
│   ├── transport_ws.py   # WebSocket transport
│   ├── middleware.py     # Security headers + request logging
│   └── auth.py           # Bearer token auth + OAuth metadata
├── agents/
│   ├── __init__.py       # Base MCP agent class
│   ├── base_mcp.py       # Base MCP server for agents
│   ├── pdf_agent.py      # PDF analysis agent
│   ├── openclaw/         # Document intelligence agent
│   ├── picoclaw/         # Minimal sync agent
│   ├── hermes/           # LLM-guided agent
│   └── nemoclaw/         # Pipeline agent
├── configs/              # MCP configs for every supported AI agent
├── docs/
│   ├── INSTALL.md        # Hardware-specific install guide
│   ├── API.md            # REST API reference
│   ├── MCP.md            # MCP protocol reference
│   └── ARCHITECTURE.md   # This document
├── tests/
│   └── test_client.py    # 28-endpoint integration test suite
├── docker-compose.yml     # CPU-first Docker stack
├── docker-compose.gpu.yml # NVIDIA CUDA override
├── docker-compose.intel.yml # Intel SYCL/Vulkan override
├── Dockerfile             # Multi-stage build
├── requirements.txt       # Python dependencies
└── .env.example           # Environment template with profiles
```

---

## Module Dependencies

```
app/main.py
  ├── app/config.py   (BACKENDS, settings)
  ├── app/ocr.py      (run_ocr)
  ├── app/backends.py (fetch_models_for_backend, health_all)
  ├── app/storage.py  (delete_job, get_job, list_jobs)
  ├── app/ui.py       (HTML template)
  ├── app/mcp.py      (tool registration — imported via connector imports)
  └── connector/*     (transports, middleware, auth)

app/mcp.py (MCP server)
  ├── app/config.py   (BACKENDS, settings)
  ├── app/backends.py (get_backend, health_all)
  └── app/ocr.py      (run_ocr)

app/ocr.py (OCR dispatch)
  ├── app/backends.py (get_backend)
  ├── app/config.py   (BACKENDS, BackendConfig, settings)
  ├── app/storage.py  (save_job)
  └── app/fallback.py (tesseract_ocr, easyocr_ocr)

app/storage.py (SQLite)
  └── app/config.py   (settings.DB_PATH)

app/backends.py (backend registry)
  ├── app/config.py   (BACKENDS, BackendConfig)
  └── httpx           (async HTTP client)

app/fallback.py (local OCR)
  ├── pytesseract
  ├── Pillow (Image)
  └── easyocr (optional)
```

---

## Data Flow

### REST Path

```
User → POST /api/ocr (multipart)
  → FastAPI route handler (app/main.py)
    → Extracts file bytes, backend_id, mode, params
    → Calls run_ocr(image_bytes, ...)
      → Security validation: size, type, integrity
      → Backend lookup: get_backend(backend_id)
      → Build fallback chain: [primary] + sorted([BACKENDS - primary])
      → Acquire semaphore (max 8 concurrent)
      → For each backend in chain:
        → _run_openai() or _run_deepseek_webui() or _run_tesseract() or _run_easyocr()
        → Success: save_job() → return {job_id, backend, text, ...}
        → Failure: save_job(error=exc) → try next backend
      → All failed: raise HTTPException(500)
    → JSONResponse to user
```

### MCP Path

```
AI Agent → POST /mcp (JSON-RPC 2.0)
  → FastAPI route handler (app/mcp.py)
    → Auth check: verify_api_key() [if API_KEY set]
    → Parse JSON-RPC body: method, params, id
    → Dispatch by method:
      "initialize" → return protocolVersion, capabilities, serverInfo
      "tools/list" → return tool list
      "tools/call" → dispatch to tool handler
        "ocr_image" → decode base64 → validate → run_ocr → return result
        "list_ocr_backends" → health_all → return formatted list
    → JSONResponse
```

### WebSocket Path

```
Client → WS /mcp/ws
  → FastAPI WebSocket handler (connector/transport_ws.py)
    → Accept WebSocket connection
    → Loop: receive JSON → dispatch → send JSON
      "initialize" → return capabilities
      "tools/list" → return tool list
      "tools/call" → call tool via OCR dispatch
    → On disconnect: log and exit loop
```

---

## Backend Architecture

### Backend Registry

Backends are configured in `app/config.py` via the `load_backends()` function. There are two configuration modes:

**1. Automatic (default):** Builds backend list from individual environment variables:
- `GLM_OCR_BASE_URL` → `openai_compatible` backend
- `DEEPSEEK_OCR_BASE_URL` → `deepseek_webui` backend
- `TESSERACT_ENABLED` → `tesseract` backend
- `EASYOCR_ENABLED` → `easyocr` backend

**2. Manual override:** Set `OCR_BACKENDS` to a JSON array that fully replaces the backend list:

```json
[
  {"id":"my-backend", "label":"Custom", "kind":"openai_compatible",
   "base_url":"http://custom:8080/v1", "model":"my-model", "priority":0}
]
```

### Backend Types

| `kind` | Runner | Priority | Requires |
|--------|--------|----------|----------|
| `openai_compatible` | `_run_openai()` | 0 | llama.cpp, vLLM, or any OpenAI-compatible server |
| `deepseek_webui` | `_run_deepseek_webui()` | 1 | DeepSeek-OCR-WebUI container |
| `tesseract` | `_run_tesseract()` | 10 | Tesseract system package |
| `easyocr` | `_run_easyocr()` | 11 | EasyOCR Python package |

### Auto-Fallback Chain

The fallback chain is built dynamically per OCR request:

```python
chain = [primary_backend]
if auto_fallback:
    # All other enabled backends, sorted by priority
    others = sorted([b for b in BACKENDS if b.enabled and b.id != backend_id],
                    key=lambda b: b.priority)
    # Always ensure Tesseract as final fallback
    if TESSERACT_ENABLED and tesseract not in others:
        others.append(tesseract)
    chain.extend(others)
```

Each backend in the chain is tried in order. If one succeeds, the result is returned immediately and the rest are skipped.

### Concurrency Control

```python
_MAX_CONCURRENT_OCR = 8
_ocr_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_OCR)

async def run_ocr(...):
    async with _ocr_semaphore:
        # OCR operations here
```

The semaphore limits concurrent OCR operations to 8. This prevents:
- GPU out-of-memory (OOM) on multiple parallel OCR requests
- llama.cpp KV cache corruption under load
- CPU thread exhaustion on CPU-only setups

---

## Security Architecture

### SSRF Protection

All backend URLs are validated against reserved IP ranges:

```python
_RESERVED_IPS = re.compile(
    r'^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|169\.254\.|127\.|0\.|'
    r'localhost|\.local|\.internal|\.onion|192\.0\.2\.)',
    re.IGNORECASE
)
```

Blocked ranges:
- **10.x** — private class A
- **192.168.x** — private class C
- **172.16-31.x** — private class B
- **169.254.x** — link-local (metadata services)
- **127.x** — loopback
- **localhost** — hostname loopback
- **.local** / **.internal** — mDNS / corporate internal
- **.onion** — Tor network

### Input Validation

Every upload passes through:

1. **Magic bytes check** (`python-magic`): verifies file signature matches image/PDF MIME types
2. **Size limit** (50 MB): prevents zip bombs and large upload attacks
3. **PIL verify**: confirms image integrity and rejects corrupted files
4. **Content type whitelist**: only `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `application/pdf`

### Authentication

Bearer token authentication via `API_KEY` environment variable:

- When `API_KEY` is empty → no auth (development mode)
- When `API_KEY` is set → all `/mcp` endpoints require `Authorization: Bearer <API_KEY>`
- Auth implemented as FastAPI dependency: `Depends(verify_api_key)`
- Applied to both MCP (`/mcp`) and connector endpoints (`/mcp/sse`, `/mcp/message`)

### Injection Protection

SQLite meta field is protected by **whitelist-only key validation**:

```python
ALLOWED_META_KEYS = {"attempted_backend", "used_backend", "fallback_chain", "gpu_layers"}

def sanitize_meta(meta):
    return {k: v for k, v in meta.items() if k in ALLOWED_META_KEYS}
```

Any extra keys passed in `meta` are silently dropped.

---

## MCP Server Architecture

### Transport Layer

The MCP server supports 4 transports:

| Transport | Endpoint | Implementation |
|-----------|----------|----------------|
| **Streamable HTTP** | `POST /mcp` | `app/mcp.py` — primary endpoint |
| **SSE (legacy)** | `GET /mcp/sse` + `POST /mcp/message` | `connector/transport_sse.py` |
| **WebSocket** | `WS /mcp/ws` | `connector/transport_ws.py` |
| **stdio** | subprocess | `app/main.py` CMD |

All transports share the same JSON-RPC dispatch logic.

### JSON-RPC Dispatch

```
POST /mcp
Body: {"jsonrpc": "2.0", "id": N, "method": "<method>", "params": {...}}

Dispatch:
  "initialize"       → {protocolVersion: "2025-03-26", capabilities: {tools: {}}, serverInfo}
  "tools/list"       → {tools: [{name, description, inputSchema}]}
  "tools/call"       → route to tool handler
    ocr_image        → decode base64 → validate → run_ocr → return text
    list_ocr_backends→ health_all → return formatted list
  Any other  → {"error": {"code": -32601, "message": "Method not found"}}
```

### Tool Registration

Tools are defined in `TOOL_LIST` in `app/mcp.py`:

```python
TOOL_LIST = [
    {
        "name": "ocr_image",
        "description": "Extract text from an image...",
        "inputSchema": {
            "type": "object",
            "required": ["image_base64"],
            "properties": {
                "image_base64": {"type": "string", "description": "Base-64-encoded image"},
                "mode": {"type": "string", "enum": ["document", "ocr", ...], "default": "document"},
                ...
            }
        }
    },
    {
        "name": "list_ocr_backends",
        "description": "List all available OCR backends...",
        "inputSchema": {"type": "object", "properties": {}}
    }
]
```

---

## AI Agent Architecture

### Agent Types

| Agent | MCP Port | Tools | Style | Best For |
|-------|----------|-------|-------|----------|
| **OpenClaw** | 8001 | 8 | Full async | Document intelligence, batch processing |
| **PicoClaw** | 8002 | 6 | Sync/edge | CI pipelines, scripts, quick reads |
| **Hermes** | 8003 | 5 | LLM-guided | Q&A, reasoning, intelligent extraction |
| **NemoClaw** | 8004 | 4 | Pipelines | Enterprise workflows, invoice processing |

### Agent MCP Servers

Each agent runs its own MCP server and connects back to lightning-ocr:

```
Agent MCP Server (e.g., OpenClaw on :8001)
  │
  │── calls lightning-ocr core ──▶ POST http://localhost:8000/mcp
  │                                │── initialize
  │                                │── tools/call ocr_image
  │◀── result ────────────────────│
  │
  │── serves own tools to its clients
  │                                │
  Agent's Client (e.g., Claude) ──▶ Agent MCP Server
```

### PDF Agent

The PDF agent is a standalone Python script that:

1. Takes a PDF file path and optional parameters
2. Converts PDF pages to images using `pdf2image`
3. Sends each page to the lightning-ocr MCP server
4. Collects results and optionally generates JSON/HTML reports

```
python agents/pdf_agent.py document.pdf
  → Convert PDF to pages (200 DPI)
  → For each page:
    → POST /mcp: tools/call ocr_image(base64(page))
    → Collect text result
  → Generate JSON / HTML report
  → Print summary: "Processed 5 pages from document.pdf"
```

---

## Storage Architecture

**Database:** SQLite (`/data/lightning_ocr.db`)  
**Schema:**

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL    NOT NULL,
    backend_id  TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    filename    TEXT,
    text_result TEXT,
    error       TEXT,
    duration_ms INTEGER,
    meta        TEXT    -- JSON blob (sanitized — whitelisted keys only)
);

CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
```

**Persistence:** The Docker volume `ocr-data` maps to `./data/` on the host filesystem, so job history survives container restarts.

---

## GPU Architecture

### Layer Offloading

llama.cpp uses a hybrid approach:

- **GPU layers** (`--n-gpu-layers N`): The first N layers of the neural network run on GPU
- **CPU layers**: Remaining layers run on CPU threads
- **Attention / KV cache**: Always on GPU (if available)
- **Embeddings**: Usually on GPU

```
Model Layers: [Layer 1][Layer 2]...[Layer N]...[Layer 32]
                  GPU        GPU         GPU    CPU
              ←───── GPU acceleration zone ────→←─ CPU ─→
```

For GLM-OCR (32 layers total):
- `N_GPU_LAYERS=32`: All layers on GPU (requires enough VRAM)
- `N_GPU_LAYERS=16`: Half on GPU, half on CPU (best for 6 GB VRAM)
- `N_GPU_LAYERS=0`: Pure CPU (works on any hardware)

### VRAM Budget

The VRAM budget for a request is:

```
VRAM = model_weights_in_GPU + KV_cache + batch_buffers + overhead

For GLM-OCR Q4_K_M (~3.8 GB model, 4096 context):
  Model:           ~2.5 GB (on GPU layers)
  KV Cache (q8_0): ~0.3 GB
  Batch:           ~0.5 GB
  Overhead:        ~0.2 GB
  Total:           ~3.5 GB  → fits in 6 GB VRAM with N_GPU_LAYERS=24

For GLM-OCR Q8_0 (~6.5 GB model, 8192 context):
  Model:           ~6.5 GB (on GPU layers)
  KV Cache (q8_0): ~0.6 GB
  Batch:           ~0.5 GB
  Overhead:        ~0.2 GB
  Total:           ~7.8 GB  → fits in 10+ GB VRAM only
```

---

## Scaling and Performance

### GPU Speedup Factors

| Mode | 4 GB CPU | 6 GB GPU | 12 GB GPU | 24 GB GPU |
|------|----------|----------|-----------|-----------|
| Document (4096 ctx) | 1x | 8x | 15x | 25x |
| Plain OCR | 1x | 6x | 12x | 20x |
| Find term | 1x | 5x | 10x | 18x |

### Performance Tips

1. **Reduce `CTX_SIZE`** — smaller context = less memory = more GPU layers
2. **Use `CACHE_TYPE_K=q4_0`** — halves KV cache memory at minimal quality loss
3. **Increase `N_THREADS`** — helps CPU-only setups and CPU offloaded layers
4. **Batch your OCRs** — `POST /api/ocr/batch` processes multiple files in one call
5. **Use Tesseract when quality doesn't matter** — zero model RAM, instant startup
6. **Set `MAX_CONCURRENT_OCR=4` on low-RAM GPUs** — prevents OOM under load