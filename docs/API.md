# ⚡ lightning-ocr — REST API Reference

**Base URL:** `http://localhost:8000`  
**OpenAPI docs:** `/docs` (Swagger) · `/redoc` (ReDoc)  
**OpenAPI schema:** `/openapi.json`

---

## Table of Contents

- [Authentication](#authentication)
- [Endpoints](#endpoints)
  - [GET / — Dashboard UI](#get----dashboard-ui)
  - [GET /api/health — Health check](#get-apihealth--health-check)
  - [GET /api/models — List backends](#get-apimodels--list-backends)
  - [POST /api/ocr — Single-file OCR](#post-apiocr--single-file-ocr)
  - [POST /api/ocr/batch — Batch OCR](#post-apiocrbatch--batch-ocr)
  - [GET /api/history — Job history](#get-apihistory--job-history)
  - [GET /api/history/{id} — Single job](#get-apihistoryid--single-job)
  - [DELETE /api/history/{id} — Delete job](#delete-apihistoryid--delete-job)
- [OCR Modes Reference](#ocr-modes-reference)
- [Fallback Chain](#fallback-chain)
- [Security Notes](#security-notes)
- [Error Responses](#error-responses)

---

## Authentication

When `API_KEY` is set, all `/api/*` endpoints require:

```http
Authorization: Bearer your-api-key
```

If `API_KEY` is empty (default), the API is open — **set it in production**.

```env
API_KEY=your-strong-secret-here
```

---

## Endpoints

### `GET /` — Dashboard UI

Returns the lightning-ocr glassmorphism dashboard (HTML).

```bash
curl http://localhost:8000/
```

---

### `GET /api/health` — Health check

Checks all configured OCR backends.

```bash
curl http://localhost:8000/api/health
```

**Response:**
```json
{
  "overall": "ok",
  "backends": [
    {
      "id": "glm-ocr",
      "label": "GLM-OCR (llama.cpp)",
      "kind": "openai_compatible",
      "status": "ok",
      "detail": {"models": 1}
    },
    {
      "id": "tesseract",
      "label": "Tesseract-OCR (local fallback)",
      "kind": "tesseract",
      "status": "ok",
      "detail": {"note": "local"}
    }
  ]
}
```

---

### `GET /api/models` — List backends

Lists all backends and their available model IDs.

```bash
curl http://localhost:8000/api/models
```

**Response:**
```json
{
  "backends": [
    {
      "id": "glm-ocr",
      "label": "GLM-OCR (llama.cpp)",
      "kind": "openai_compatible",
      "base_url": "http://llama:8080/v1",
      "preferred_model": "GLM-OCR",
      "priority": 0,
      "models": [{"id": "GLM-OCR", "object": "model"}]
    },
    {
      "id": "tesseract",
      "label": "Tesseract-OCR (local fallback)",
      "kind": "tesseract",
      "priority": 10,
      "models": [{"id": "tesseract", "object": "model", "owned_by": "local"}]
    }
  ]
}
```

---

### `POST /api/ocr` — Single-file OCR

Run OCR on a single image or PDF page.

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `file` | file | ✅ | — | PNG/JPG/WEBP/GIF/PDF (max 50 MB) |
| `backend_id` | string | ✅ | — | Backend ID from `/api/models` |
| `mode` | string | — | `document` | `document`/`ocr`/`free`/`figure`/`describe`/`find`/`freeform` |
| `find_term` | string | — | `""` | Term to locate (mode=`find`) |
| `custom_prompt` | string | — | `""` | Custom prompt (mode=`freeform`) |
| `auto_fallback` | boolean | — | `true` | Auto-retry on backend failure |

**Response:**
```json
{
  "job_id": 42,
  "backend": {
    "id": "glm-ocr",
    "label": "GLM-OCR (llama.cpp)",
    "kind": "openai_compatible"
  },
  "model": "GLM-OCR",
  "mode": "document",
  "text": "# Invoice\n\n**Total:** $1,234.56\n...",
  "fallback": false,
  "duration_ms": 1240
}
```

**cURL examples:**

```bash
# Basic document OCR
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=document" \
  -F "file=@invoice.png"

# Find term
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=find" \
  -F "find_term=Total" \
  -F "file=@invoice.png"

# Custom extraction
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=freeform" \
  -F 'custom_prompt=Extract invoice_number, date, total as JSON.' \
  -F "file=@invoice.png"

# Tesseract only (no AI backend needed)
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=tesseract" \
  -F "mode=ocr" \
  -F "file=@scan.jpg"

# With authentication
curl -X POST http://localhost:8000/api/ocr \
  -H "Authorization: Bearer your-api-key" \
  -F "backend_id=glm-ocr" \
  -F "mode=document" \
  -F "file=@doc.png"
```

---

### `POST /api/ocr/batch` — Batch OCR

Run OCR on multiple files in one request.

**Content-Type:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `files` | file[] | ✅ | Multiple image files (max 50 MB each) |
| `backend_id` | string | ✅ | Backend ID |
| `mode` | string | — | OCR mode |
| `auto_fallback` | boolean | — | Auto-fallback (default: true) |

**Response:**
```json
{
  "results": [
    {
      "file": "page1.png",
      "text": "...",
      "backend": "glm-ocr",
      "job_id": 43
    },
    {
      "file": "page2.png",
      "text": "...",
      "backend": "tesseract",
      "job_id": 44
    }
  ],
  "count": 2
}
```

**cURL example:**
```bash
curl -X POST http://localhost:8000/api/ocr/batch \
  -F "backend_id=glm-ocr" \
  -F "mode=ocr" \
  -F "files=@page1.png" \
  -F "files=@page2.png" \
  -F "files=@page3.png"
```

---

### `GET /api/history` — Job history

List past OCR jobs (paginated).

| Query param | Type | Default | Max |
|-------------|------|---------|-----|
| `limit` | integer | 50 | 200 |
| `offset` | integer | 0 | — |

**Response:**
```json
{
  "jobs": [
    {
      "id": 42,
      "created_at": 1748000000.0,
      "backend_id": "glm-ocr",
      "mode": "document",
      "filename": "invoice.png",
      "text_result": "# Invoice...",
      "error": null,
      "duration_ms": 1240,
      "meta": "{\"attempted_backend\": \"glm-ocr\", \"used_backend\": \"glm-ocr\"}"
    }
  ],
  "limit": 50,
  "offset": 0
}
```

---

### `GET /api/history/{id}` — Single job

Retrieve a single job record.

**Response:** Same structure as a single job object in the list above.

```bash
curl http://localhost:8000/api/history/42
```

---

### `DELETE /api/history/{id}` — Delete job

Delete a job record.

**Response:**
```json
{"deleted": 42}
```

```bash
curl -X DELETE http://localhost:8000/api/history/42
```

---

## OCR Modes Reference

| Mode | Prompt | Output Type | Best For |
|------|--------|-------------|----------|
| `document` | Convert to clean Markdown | Markdown | Invoices, reports, contracts, forms |
| `ocr` | Read all text accurately | Plain text | Scanned pages, mixed content |
| `free` | Return only the text | Raw text | Quick reads, lowest latency |
| `figure` | Parse labels, axes, text | Text description | Charts, graphs, diagrams, tables |
| `describe` | Comprehensive description | Text description | Photos, mixed images |
| `find` | Locate text around term | Context around match | Field lookup ("Total", "Date") |
| `freeform` | Custom prompt | Custom output | Specialised extraction |

> **GLM-OCR modes** — GLM-OCR is a vision-language model, not just OCR. It supports image description and custom instructions. However, it is prompt-sensitive: question-style prompts (*"Is the screen cracked?"*) may return wrong answers because the model tries to OCR the keyword instead of understanding the concept. Use open-ended prompts (*"What do you see?"*, *"Describe the condition of..."*) for reliable results.

---

## Fallback Chain

When `auto_fallback=true` (default), the system tries multiple backends in order:

```
1. GLM-OCR (llama.cpp)    priority 0   ← primary (requested backend)
2. DeepSeek-OCR-WebUI     priority 1   ← secondary (if enabled)
3. Tesseract (local)      priority 10  ← tertiary (always available)
4. EasyOCR (local)        priority 11  ← quaternary (if installed)
```

The first backend to return a valid result is used. All failures are logged to the job history.

To disable fallback (fail fast):
```bash
curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=document" \
  -F "auto_fallback=false" \
  -F "file=@invoice.png"
```

---

## Security Notes

### Input Validation

Every file uploaded to the REST API is validated:
- **Size limit:** 50 MB maximum per file
- **File type:** Must be an image (PNG, JPEG, WebP, GIF) or PDF
- **Integrity:** Validated using Pillow's `verify()` method

### SSRF Protection

All backend URLs are validated against reserved/internal IP ranges:
- `10.x.x.x`, `192.168.x.x`, `172.16-31.x.x` — private networks
- `169.254.x.x` — link-local/metadata IPs
- `127.x.x.x`, `localhost` — loopback
- `.local`, `.internal`, `.onion` — restricted TLDs/hostnames

### Concurrency Control

The server limits concurrent OCR operations to **8** to prevent:
- GPU out-of-memory (OOM) crashes
- CPU thread exhaustion
- llama.cpp KV cache corruption

---

## Error Responses

All errors follow RFC 9457 Problem Details format:

```json
{"detail": "Human-readable error message"}
```

| HTTP | Code | Meaning |
|------|------|---------|
| 400 | BAD_REQUEST | Empty file, invalid params, unsupported file type |
| 400 | BAD_REQUEST | File too large (> 50 MB) |
| 400 | BAD_REQUEST | Invalid or corrupted file |
| 401 | UNAUTHORIZED | Missing API key |
| 403 | FORBIDDEN | Invalid API key |
| 404 | NOT_FOUND | Unknown backend or job ID |
| 422 | UNPROCESSABLE | Validation error (FastAPI) |
| 429 | TOO_MANY_REQUESTS | Rate limit exceeded (future) |
| 500 | INTERNAL_ERROR | All OCR backends failed |
| 503 | SERVICE_UNAVAILABLE | MCP disabled or llama.cpp down