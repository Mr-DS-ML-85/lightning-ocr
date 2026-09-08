# ⚡ lightning-ocr v3.0

> **Production-ready OCR hub** — GPU or CPU, runs everywhere from an Intel Core i5 2nd gen + 4 GB RAM
> to an **NVIDIA RTX 5090**. Modern glassmorphism UI, **MCP 2026-07-28 + 2025-03-26**,
> universal AI agent support, Tesseract fallback, persistent job history, and multi-language OCR.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square"/>
  <img src="https://img.shields.io/badge/FastAPI-0.115-green?style=flat-square"/>
  <img src="https://img.shields.io/badge/MCP-2026--07--28-purple?style=flat-square"/>
  <img src="https://img.shields.io/badge/version-3.0-orange?style=flat-square"/>
  <img src="https://img.shields.io/badge/license-Apache--2.0-lightgrey?style=flat-square"/>
</p>

---

## What's New in v3.0

- **MCP 2026-07-28 spec support** — stateless protocol, `server/discover` RPC, cache hints, per-request `_meta`
- **10 MCP tools** (was 5): added `ocr_batch`, `get_job`, `list_jobs`, `delete_job`, `describe_capabilities`
- **Discovery endpoint** with `transports` dict, `capabilities`, `protocol_versions`
- **Backward compatible** — 2025-03-26 handshake still works

### Previous Releases

- **v2.1** — Fixed REST endpoint, GLM-OCR conditional registration, multi-language Tesseract, Bengali OCR
- **v2.0** — Initial production release

---

## Features

| Feature | Details |
|---------|---------|
| **Modern 2026 UI** | Glassmorphism + neon accents, drag-and-drop, batch upload, dark mode |
| **Multi-backend OCR** | GLM-OCR (llama.cpp), DeepSeek-OCR-WebUI, Tesseract, EasyOCR |
| **Auto-fallback chain** | Primary AI → secondary AI → Tesseract → EasyOCR |
| **7 OCR modes** | Document→Markdown, General OCR, Plain text, Figure/Chart, Describe, Find term, Custom prompt |
| **Multi-language** | `TESSERACT_LANG=eng+ben` — add any Tesseract language pack |
| **NVIDIA CUDA** | Full / partial layer offload, flash-attn, KV cache quantisation |
| **CPU-only mode** | Works on 4 GB RAM with Q4_K_M quantisation |
| **Spec-compliant MCP** | JSON-RPC 2.0, spec 2026-07-28 + 2025-03-26, 10 tools, 4 transports (stdio, HTTP, SSE, WebSocket) |
| **Persistent storage** | SQLite job history survives restarts |
| **Security hardened** | SSRF protection, file type validation, bearer token auth, 50 MB size limits |
| **Full API docs** | Swagger `/docs` · ReDoc `/redoc` |

---

## Quick Start

### Docker (recommended)

#### Tesseract-only (lightweight, no AI model needed)

```bash
git clone https://github.com/youruser/lightning-ocr.git
cd lightning-ocr

docker run -d \
  --name lightning-ocr \
  -p 8000:8000 \
  -e GLM_OCR_BASE_URL="" \
  -e DEEPSEEK_OCR_BASE_URL="" \
  -e TESSERACT_ENABLED=true \
  -e TESSERACT_LANG="eng+ben" \
  -e MCP_ENABLED=true \
  -v lightning-ocr-data:/data \
  --restart unless-stopped \
  lightning-ocr-app:latest
```

#### Full stack with GLM-OCR (requires ~4 GB RAM for llama.cpp)

```bash
mkdir -p data
docker compose up --build
```

#### With DeepSeek-OCR-WebUI

```bash
docker compose --profile deepseek up --build
```

#### With NVIDIA GPU

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build

# Custom GPU offload:
N_GPU_LAYERS=99 CTX_SIZE=8192 \
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
```

### Manual Install (no Docker)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install python-magic

# Install Tesseract
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben tesseract-ocr-osd

cp .env.example .env
# Edit .env — set GLM_OCR_BASE_URL="" for Tesseract-only mode

mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Endpoints

### REST API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Dashboard UI |
| `GET` | `/api/health` | Health check all backends |
| `GET` | `/api/models` | List backends and models |
| `POST` | `/api/ocr` | Single-file OCR (multipart) |
| `GET` | `/api/history` | Paginated job history |
| `GET` | `/api/history/{id}` | Single job result |
| `DELETE` | `/api/history/{id}` | Delete job record |

### MCP Server

| Method | Path | Transport |
|--------|------|-----------|
| `POST` | `/mcp` | Streamable HTTP (primary) |
| `GET` | `/mcp/sse` | SSE (legacy) |
| `WS` | `/mcp/ws` | WebSocket |
| `GET` | `/.well-known/mcp` | Discovery manifest |

### MCP Tools (10)

| Tool | Description |
|------|-------------|
| `ocr_image` | OCR a single image, PDF, document, or TXT (via `image_base64` or `file_path`) |
| `ocr_batch` | OCR multiple files in one call |
| `extract_tables` | Extract tables from PDFs |
| `list_ocr_backends` | List available OCR backends |
| `list_templates` | List saved Smart Templates |
| `save_template` | Save a PDF layout as a template |
| `get_job` | Get a completed OCR job by ID |
| `list_jobs` | List recent OCR jobs |
| `delete_job` | Delete an OCR job |
| `describe_capabilities` | Describe server capabilities |

---

## API Examples

### REST — Single file OCR

```bash
curl -X POST http://localhost:8000/api/ocr \
  -F "file=@document.png" \
  -F "backend_id=tesseract" \
  -F "mode=document"
```

Response:
```json
{
  "text": "Extracted text here...",
  "backend": "tesseract",
  "duration_ms": 350,
  "fallback": false
}
```

### MCP — JSON-RPC call

```bash
B64=$(base64 -w0 image.png)

curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": 1,
    \"method\": \"tools/call\",
    \"params\": {
      \"name\": \"ocr_image\",
      \"arguments\": {
        \"image_base64\": \"$B64\",
        \"backend_id\": \"tesseract\",
        \"mode\": \"document\"
      }
    }
  }"
```

### MCP — List available tools

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

### OpenCode Setup

Config: `~/.config/opencode/config.json` (global) or `opencode.json` (project root)

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "lightning-ocr": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/path/to/lightning-ocr", "python", "-m", "connector.transport_stdio", "--legacy25"],
      "enabled": true
    }
  }
}
```

> Use `--legacy25` if your OpenCode only supports protocol `2025-03-26`. Omit it for `2026-07-28`. OpenCode currently requires this flag.

---

## OCR Modes

| Mode | Output | Best for |
|------|--------|----------|
| `document` | Clean Markdown | Invoices, reports, contracts |
| `ocr` | Plain text | Scanned pages, photocopies |
| `free` | Raw text | Quick reads, lowest latency |
| `figure` | Label + axis analysis | Charts, graphs, diagrams |
| `describe` | Visual description | Photos, mixed images |
| `find` | Context around term | Field lookup ("Total", "Date") |
| `freeform` | Custom prompt | Specialised extraction |

---

## Environment Variables

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `GLM_OCR_BASE_URL` | `http://llama:8080/v1` | GLM-OCR llama.cpp URL. **Set empty `""` to disable** |
| `GLM_OCR_MODEL` | `GLM-OCR` | Model name for GLM-OCR |
| `DEEPSEEK_OCR_BASE_URL` | `""` | DeepSeek-OCR-WebUI URL. Empty = disabled |
| `TESSERACT_ENABLED` | `true` | Enable Tesseract fallback |
| `TESSERACT_LANG` | `eng+ben` | Tesseract languages (e.g., `eng`, `eng+ben`, `eng+ara+chi_sim`) |
| `EASYOCR_ENABLED` | `false` | Enable EasyOCR (~1 GB RAM) |
| `MCP_ENABLED` | `true` | Enable MCP server |
| `API_KEY` | `""` | Bearer token for API auth (empty = no auth) |
| `DB_PATH` | `/data/lightning_ocr.db` | SQLite database path |

### Hardware

| Variable | Default | Description |
|----------|---------|-------------|
| `ACCEL_MODE` | `auto` | `auto`, `cuda`, `sycl`, `vulkan`, `cpu` |
| `N_GPU_LAYERS` | `auto` | GPU layers: `0` (CPU), `99` (all), or specific count |
| `CTX_SIZE` | `4096` | Context window (1024–32768) |
| `N_THREADS` | `4` | CPU threads |
| `FLASH_ATTN` | `true` | Flash attention (CUDA/Vulkan only) |
| `CACHE_TYPE_K` | `q8_0` | KV cache quantisation |
| `CACHE_TYPE_V` | `q8_0` | KV cache quantisation |

---

## Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Default CPU setup (GLM-OCR + Tesseract) |
| `docker-compose.gpu.yml` | NVIDIA CUDA GPU overlay |
| `docker-compose.intel.yml` | Intel Arc/UHD Vulkan overlay |

---

## Project Structure

```
lightning-ocr/
├── app/
│   ├── main.py          # FastAPI app, REST endpoints
│   ├── config.py         # Settings, backend registry
│   ├── ocr.py            # OCR dispatch + auto-fallback
│   ├── mcp.py            # MCP JSON-RPC server (10 tools)
│   ├── backends.py       # Backend health checks
│   ├── fallback.py       # Tesseract + EasyOCR engines
│   ├── storage.py        # SQLite job history
│   └── ui.py             # Dashboard HTML
├── connector/
│   ├── server.py         # MCP server core
│   ├── transport_sse.py  # SSE transport
│   ├── transport_ws.py   # WebSocket transport
│   ├── middleware.py      # Request middleware
│   └── auth.py           # Auth + OAuth
├── Dockerfile
├── docker-compose.yml
├── docker-compose.gpu.yml
├── docker-compose.intel.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Tesseract Language Packs

```bash
# List installed languages
tesseract --list-langs

# Install additional languages (Ubuntu/Debian)
sudo apt-get install -y tesseract-ocr-ben    # Bengali
sudo apt-get install -y tesseract-ocr-ara    # Arabic
sudo apt-get install -y tesseract-ocr-chi-sim # Chinese (Simplified)
sudo apt-get install -y tesseract-ocr-jpn    # Japanese
sudo apt-get install -y tesseract-ocr-kor    # Korean
sudo apt-get install -y tesseract-ocr-hin    # Hindi
sudo apt-get install -y tesseract-ocr-tha    # Thai
sudo apt-get install -y tesseract-ocr-deu    # German
sudo apt-get install -y tesseract-ocr-fra    # French
sudo apt-get install -y tesseract-ocr-spa    # Spanish

# Configure via env var (comma or + separated)
export TESSERACT_LANG="eng+ben+hin"
```

---

## Security

- **SSRF protection** — backend URLs validated against reserved IP ranges
- **File type validation** — python-magic checks MIME types
- **Size limits** — 50 MB max upload
- **Bearer auth** — optional `API_KEY` for production
- **SQLite safety** — parameterised queries, no injection
- **Image integrity** — PIL verify on upload

---

## Testing

```bash
# Health check
curl http://localhost:8000/api/health

# OCR test
curl -X POST http://localhost:8000/api/ocr \
  -F "file=@test.png" -F "backend_id=tesseract"

# MCP test
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Swagger UI
open http://localhost:8000/docs
```

---

## License

Apache-2.0
