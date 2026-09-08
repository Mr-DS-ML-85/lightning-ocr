# lightning-ocr — Agent Guide

## What this is

FastAPI-based OCR hub. Serves both a REST API and an MCP (Model Context Protocol) server with 4 transports (stdio, HTTP/SSE, WebSocket). Multiple OCR backends with auto-fallback: GLM-OCR (llama.cpp) → DeepSeek → Tesseract → EasyOCR.

## Quick start

```bash
cp .env.example .env
# For Tesseract-only (no GPU/model needed): set GLM_OCR_BASE_URL=""
mkdir -p data
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Docker: `docker compose up --build` (CPU) or `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build` (GPU).

## Key files

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app, REST endpoints, mounts MCP/SSE/WS routers |
| `app/config.py` | Settings (pydantic-settings from `.env`), backend registry |
| `app/ocr.py` | OCR dispatch + fallback chain. Core business logic. |
| `app/fallback.py` | Tesseract and EasyOCR engines |
| `app/storage.py` | SQLite job history (DB_PATH env var) |
| `connector/server.py` | MCP server constants (name, version, protocol) |
| `connector/transport_sse.py` | SSE transport router |
| `connector/transport_ws.py` | WebSocket transport |
| `connector/auth.py` | Bearer auth + OAuth metadata |
| `connector/middleware.py` | Request middleware |

## Entrypoint and package boundary

`app/` and `connector/` are **sibling packages**, not nested. `app/main.py` imports from both. The `connector/` package provides the MCP protocol layer (JSON-RPC handling, transports, auth). When adding MCP tools, they register via `connector/server.py` — the import in `main.py` line 37 (`import connector.server`) triggers auto-registration.

## Testing

No pytest/unit test suite. Tests are integration tests against a running server:

```bash
# Install test deps
pip install -r tests/requirements.txt

# Start server in another terminal first, then:
python tests/test_client.py                    # all tests
python tests/test_client.py --test health      # single test
python tests/test_client.py --api-key mykey    # with auth
```

Tests generate synthetic PNGs/PDFs — no fixture files needed. The test client uses `httpx`, `typer`, and `rich` for output.

## Critical env vars

- `GLM_OCR_BASE_URL` — set to `""` (empty string) to disable GLM-OCR and run Tesseract-only. Leaving it at the default `http://llama:8080/v1` when no llama.cpp server is running causes `"degraded"` health.
- `TESSERACT_LANG` — default `eng+ben`. Must match installed tesseract-ocr language packs.
- `OCR_BACKENDS` — optional JSON array to override the entire backend list.
- `DB_PATH` — SQLite path. Docker volume maps to `/data/`, local default is `./data/lightning_ocr.db`.

## Common pitfalls

- `python-magic` is required for file type validation on uploads. If missing, validation is silently skipped (logged as warning). Install: `pip install python-magic` + `libmagic1` system package.
- PDF processing needs `pdf2image` + `poppler-utils` (`apt install poppler-utils`). Falls back to `pypdf` text extraction if poppler unavailable.
- The SSRF protection in `app/ocr.py` blocks backend URLs pointing to reserved IPs. `localhost`, `127.0.0.1`, `0.0.0.0` are whitelisted explicitly in `_KNOWN_LOCAL_HOSTS`.
- Max upload size: 50 MB (hardcoded in `app/ocr.py`).
- Concurrency limited to 8 simultaneous OCR requests (`_ocr_semaphore` in `app/ocr.py`).

## MCP config for agents

Pre-built configs are in `configs/` for Claude Desktop, Cursor, Copilot, Codex, Continue, Kilo Code, OpenCode, Cline, and ADK. The `install.sh` script copies them to the correct locations. For stdio transport, the connector runs as `python -m connector.transport_stdio`.

## No lint/typecheck

This repo has no configured linter, type checker, or formatter. Follow existing code style (Python 3.11+, type hints, pydantic models).
