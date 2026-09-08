"""
lightning-ocr — Production-ready OCR hub
Rename from GLM-OCR-Hub → lightning-ocr

Features:
  • Modern 2026 glassmorphism UI
  • Multi-backend OCR (GLM via llama.cpp, DeepSeek-WebUI, Tesseract, EasyOCR)
  • CPU + CUDA + Intel SYCL GPU offload
  • llama.cpp low-memory optimizations (Q4, flash-attn, KV quant, mlock)
  • Auto-fallback chain (AI → Tesseract → EasyOCR)
  • Persistent SQLite job history
  • Universal MCP tool server (any AI agent)
  • Full OpenAPI docs (/docs, /redoc)
"""
from __future__ import annotations

import logging
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from fastapi.responses import HTMLResponse, JSONResponse

from app.backends import fetch_models_for_backend, health_all
from app.config import BACKENDS, settings
from app.ocr import run_ocr
from app.storage import delete_job, get_job, list_jobs
from app.ui import HTML
from app.extraction import extract_tables_from_bytes, extract_tables, list_templates, save_table_as_template, ExtractionResult

# ── Connector imports ─────────────────────────────────────────────────────────
from connector.server import SERVER_NAME, SERVER_VERSION, PROTOCOL_VERSION, PROTOCOL_VERSION2
from connector.transport_sse import router as sse_router
from connector.transport_ws import ws_endpoint
from connector.middleware import add_middleware
from connector.auth import require_auth, oauth_metadata

# ── All tools are auto-registered on import ───────────────────────────────────
import connector.server  # noqa: F401 — registers all tools

# ── MCP endpoint (from app/mcp.py) ────────────────────────────────────────────
from app.mcp import router as mcp_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("lightning_ocr")

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("⚡ lightning-ocr v%s starting", SERVER_VERSION)
    log.info("  MCP transports: stdio | SSE | Streamable HTTP | WebSocket")
    log.info("  Backends: %s", [b.id for b in BACKENDS if b.enabled])
    log.info("  Accel: %s | GPU layers: %s | CTX: %s",
             settings.ACCEL_MODE, settings.N_GPU_LAYERS, settings.CTX_SIZE)
    yield


app = FastAPI(
    title="⚡ lightning-ocr",
    version=SERVER_VERSION,
    description=(
        "Production-ready OCR hub with universal MCP connector. "
        "Supports every AI agent: Claude, Cursor, Copilot, Continue, "
        "Cline, Kilocode, OpenCode, Codex, ADK, OpenClaw, PicoClaw, "
        "Hermes, NemoClaw, and any MCP-compatible agent."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────────────
add_middleware(app)

# ── Mount MCP router (Streamable HTTP) ────────────────────────────────────────
app.include_router(mcp_router)

# ── Mount SSE router ──────────────────────────────────────────────────────────
app.include_router(sse_router)

# ── WebSocket transport ───────────────────────────────────────────────────────
app.add_api_websocket_route("/mcp/ws", ws_endpoint)

# ── REST API ────────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["REST API"])
async def api_health():
    """Health check all OCR backends."""
    health = await health_all()
    overall = "ok" if all(h["status"] == "ok" for h in health) else "degraded"
    return JSONResponse({"overall": overall, "backends": health})


@app.get("/api/models", tags=["REST API"])
async def api_models():
    """List all backends and their available models."""
    from app.backends import fetch_models_for_backend
    results = []
    for b in BACKENDS:
        if not b.enabled:
            continue
        models = await fetch_models_for_backend(b)
        results.append({
            "id": b.id,
            "label": b.label,
            "kind": b.kind,
            "base_url": b.base_url,
            "preferred_model": b.model or b.id,
            "priority": b.priority,
            "models": models,
        })
    return JSONResponse({"backends": results})


@app.post("/api/ocr", tags=["REST API"])
async def api_ocr(
    file: UploadFile = File(...),
    backend_id: str = Form("glm-ocr-engine"),
    mode: str = Form("document"),
    find_term: str = Form(""),
    custom_prompt: str = Form(""),
    auto_fallback: bool = Form(True),
    preprocess: bool = Form(True),
    output_format: str = Form("text"),
):
    """Run OCR on an uploaded image/PDF/DOCX/PPTX/XLSX file via multipart."""
    from fastapi import HTTPException
    try:
        # Read uploaded file
        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(400, "Empty file uploaded")

        # Validate file type (mirrors MCP: images, PDF, Office docs, text)
        filename = file.filename or "uploaded_file"
        try:
            from app.ocr import detect_content_type
            mime = detect_content_type(image_bytes, filename)
        except HTTPException as exc:
            raise exc

        # Run OCR
        result = await run_ocr(
            image_bytes=image_bytes,
            filename=filename,
            content_type=mime,
            backend_id=backend_id,
            mode=mode,
            find_term=find_term,
            custom_prompt=custom_prompt,
            auto_fallback=auto_fallback,
            preprocess=preprocess,
            output_format=output_format,
        )
        return JSONResponse({
            "text": result["text"],
            "backend": result["backend"]["id"],
            "mode": result["mode"],
            "model": result.get("model", ""),
            "confidence": result.get("confidence", 0.0),
            "duration_ms": result["duration_ms"],
            "fallback": result["fallback"],
            "job_id": result["job_id"],
            "languages": result.get("languages", []),
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/ocr/batch", tags=["REST API"])
async def api_ocr_batch(
    files: list[UploadFile] = File(...),
    backend_id: str = Form("glm-ocr-engine"),
    mode: str = Form("document"),
    find_term: str = Form(""),
    custom_prompt: str = Form(""),
    auto_fallback: bool = Form(True),
):
    """Run OCR on multiple uploaded files via multipart (processed in parallel)."""
    from fastapi import HTTPException
    import asyncio as _asyncio

    # Read + validate all files first (parallel-safe)
    loaded = []
    for file in files:
        try:
            image_bytes = await file.read()
            if not image_bytes:
                loaded.append((file.filename or "file", "Empty file", None, None))
                continue
            try:
                from app.ocr import detect_content_type
                mime = detect_content_type(image_bytes, file.filename or "uploaded_file")
            except HTTPException as exc:
                loaded.append((file.filename or "file", str(exc.detail), None, None))
                continue
            loaded.append((file.filename or "uploaded_file", None, image_bytes, mime))
        except Exception as exc:
            loaded.append((file.filename or "file", str(exc), None, None))

    async def _process_one(fname, err, image_bytes, mime):
        if err is not None:
            return {"file": fname, "error": err}
        try:
            result = await run_ocr(
                image_bytes=image_bytes,
                filename=fname,
                content_type=mime,
                backend_id=backend_id,
                mode=mode,
                find_term=find_term,
                custom_prompt=custom_prompt,
                auto_fallback=auto_fallback,
            )
            return {
                "file": fname,
                "text": result["text"],
                "backend": result["backend"]["id"],
                "duration_ms": result["duration_ms"],
                "fallback": result["fallback"],
                "job_id": result["job_id"],
                "confidence": result.get("confidence", 0.0),
            }
        except Exception as exc:
            return {"file": fname, "error": str(exc)}

    results = await _asyncio.gather(*[_process_one(*p) for p in loaded])
    return JSONResponse({"count": len(results), "results": results})


@app.get("/api/history", tags=["REST API"])
async def api_history(limit: int = 50, offset: int = 0):
    """List job history."""
    from app.storage import list_jobs
    jobs = await list_jobs(limit=limit, offset=offset)
    return JSONResponse({"jobs": jobs, "limit": limit, "offset": offset})


@app.get("/api/history/{job_id}", tags=["REST API"])
async def api_history_detail(job_id: int):
    """Get a specific job by ID."""
    from app.storage import get_job
    job = await get_job(job_id)
    if job is None:
        from fastapi import HTTPException
        raise HTTPException(404, "Job not found")
    return JSONResponse(job)


@app.delete("/api/history/{job_id}", tags=["REST API"])
async def api_history_delete(job_id: int):
    """Delete a specific job."""
    from app.storage import delete_job
    deleted = await delete_job(job_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(404, "Job not found")
    return JSONResponse({"deleted": True})


# ── Table Extraction Endpoints ────────────────────────────────────────────────


@app.post("/api/extract-table", tags=["Table Extraction"])
async def api_extract_table(
    file: UploadFile = File(...),
    output_format: str = Form("xlsx"),
    use_glm_ocr: bool = Form(True),
    template_name: str = Form(""),
):
    """
    Extract tables from a PDF and convert to Excel/CSV/DOCX.

    Like Able2Extract's PDF-to-Excel conversion.
    Uses GLM-OCR for scanned documents and geometry analysis for text-based PDFs.

    Returns the output file as a download.
    """
    try:
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(400, "Empty file uploaded")

        if output_format not in ("xlsx", "csv", "docx"):
            raise HTTPException(400, f"Unsupported format: {output_format}")

        result = await extract_tables_from_bytes(
            pdf_bytes=pdf_bytes,
            filename=file.filename or "document.pdf",
            use_glm_ocr=use_glm_ocr,
            use_templates=bool(template_name),
            template_name=template_name,
            output_formats=[output_format],
        )

        if result.error:
            raise HTTPException(500, result.error)

        if not result.tables:
            raise HTTPException(404, "No tables found in the document")

        # Find the output file
        base = os.path.splitext(file.filename or "document")[0]
        output_key = None
        for key in [f"{base}.{output_format}", f"{base}_table1.{output_format}"]:
            if key in result._outputs:
                output_key = key
                break

        if output_key is None or output_key not in result._outputs:
            # Return JSON with table data instead
            tables_data = []
            for table in result.tables:
                rows = []
                for row in table.cells:
                    rows.append([cell.text.strip() for cell in row if cell.rowspan >= 0])
                tables_data.append({
                    "num_rows": table.num_rows,
                    "num_cols": table.num_cols,
                    "title": table.title,
                    "confidence": table.confidence,
                    "strategy": table.strategy.value,
                    "rows": rows,
                })
            return JSONResponse({
                "tables": tables_data,
                "page_count": result.page_count,
                "detection_time_ms": result.detection_time_ms,
            })

        content = result._outputs[output_key]
        media_types = {
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "csv": "text/csv",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }

        return StreamingResponse(
            io.BytesIO(content),
            media_type=media_types.get(output_format, "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{output_key}"',
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/api/extract-table/batch", tags=["Table Extraction"])
async def api_extract_table_batch(
    files: list[UploadFile] = File(...),
    output_format: str = Form("xlsx"),
    use_glm_ocr: bool = Form(True),
):
    """
    Extract tables from multiple PDFs (batch mode).
    Returns a JSON summary with download links for each file.
    """
    results = []
    for file in files:
        try:
            pdf_bytes = await file.read()
            if not pdf_bytes:
                results.append({"file": file.filename, "error": "Empty file"})
                continue

            result = await extract_tables_from_bytes(
                pdf_bytes=pdf_bytes,
                filename=file.filename or "document.pdf",
                use_glm_ocr=use_glm_ocr,
                output_formats=[output_format],
            )

            results.append({
                "file": file.filename,
                "tables_found": len(result.tables),
                "pages": result.page_count,
                "strategy": result.strategy.value,
                "detection_time_ms": result.detection_time_ms,
                "error": result.error,
            })
        except Exception as exc:
            results.append({"file": file.filename, "error": str(exc)})

    return JSONResponse({"count": len(results), "results": results})


@app.get("/api/templates", tags=["Table Extraction"])
async def api_list_templates():
    """List available Smart Templates."""
    templates = list_templates()
    return JSONResponse({"templates": templates})


@app.post("/api/templates/save", tags=["Table Extraction"])
async def api_save_template(
    file: UploadFile = File(...),
    template_name: str = Form(""),
):
    """
    Upload a PDF, detect its table, and save it as a Smart Template.
    Like Able2Extract's "Create Template" feature.
    """
    try:
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(400, "Empty file")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp_path = f.name

        try:
            name = await save_table_as_template(
                pdf_path=tmp_path,
                template_name=template_name,
            )
            if name is None:
                raise HTTPException(404, "No table found to use as template")
            return JSONResponse({"name": name, "message": f"Template '{name}' saved"})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Well-known endpoints ───────────────────────────────────────────────────────
@app.get("/.well-known/mcp", include_in_schema=False)
async def mcp_discovery():
    from app.mcp import TOOL_LIST
    base = os.getenv("PUBLIC_URL", "http://localhost:8000")
    return JSONResponse({
        "name":                   SERVER_NAME,
        "version":                SERVER_VERSION,
        "description":            "Universal OCR MCP server for all AI agents",
        "protocol_versions":      ["2025-03-26", "2026-07-28"],
        "protocol_latest":        "2026-07-28",
        "transports":             {
            "http": "/mcp",
            "sse":  "/mcp/sse",
            "ws":   "ws://localhost:8000/mcp/ws",
            "stdio": "python -m connector.transport_stdio",
        },
        "tools":                  [t["name"] for t in TOOL_LIST],
        "capabilities": {
            "tools":              {"listChanged": False},
            "server_discover":    True,
            "cache_hints":        True,
        },
        "auth": {
            "type":    "bearer" if settings.API_KEY else "none",
            "header":  "Authorization",
            "format":  "Bearer <token>",
        },
        "oauth_metadata": f"{base}/.well-known/oauth-protected-resource",
    })


@app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_resource_meta():
    base = os.getenv("PUBLIC_URL", "http://localhost:8000")
    return JSONResponse(oauth_metadata(base))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )