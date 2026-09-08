"""lightning-ocr · Backend registry + health checks"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from app.config import BACKENDS, BackendConfig


def get_backend(backend_id: str) -> Optional[BackendConfig]:
    return next((b for b in BACKENDS if b.id == backend_id and b.enabled), None)


async def fetch_models_for_backend(backend: BackendConfig) -> List[Dict[str, Any]]:
    if backend.kind not in ("openai_compatible",):
        return [{"id": backend.model or backend.id, "object": "model", "owned_by": backend.label}]
    url = backend.base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {backend.api_key}"} if backend.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json().get("data", [])
            if data:
                return data
    except httpx.HTTPError:
        pass
    return [{"id": backend.model or backend.id, "object": "model", "owned_by": backend.label}]


async def health_check_backend(backend: BackendConfig) -> Dict[str, Any]:
    if backend.kind == "tesseract":
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "ok", "detail": {"note": "local"}}
        except ImportError as e:
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "error", "detail": {"error": str(e)}}

    if backend.kind == "easyocr":
        try:
            import easyocr  # noqa: F401
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "ok", "detail": {"note": "local"}}
        except ImportError as e:
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "error", "detail": {"error": str(e)}}

    if backend.kind == "glm_engine":
        try:
            from app.glm_ocr_backend import get_engine
            engine = get_engine()
            engine.load()
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "ok", "detail": {"note": "transformers engine"}}
        except ImportError as e:
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "error", "detail": {"error": f"engine import failed: {e}"}}
        except Exception as e:
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "degraded", "detail": {"error": str(e)}}

    if backend.kind == "glm_maas":
        status, detail = "unknown", {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {"Authorization": f"Bearer {backend.api_key}"} if backend.api_key else {}
                r = await client.get("https://open.bigmodel.cn", timeout=5.0, headers=headers)
                status = "ok" if r.status_code < 500 else "degraded"
                detail = {"api": "zhipu_maas"}
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": status, "detail": detail}
        except httpx.HTTPError as e:
            return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                    "status": "error", "detail": {"error": str(e)}}

    # Generic HTTP-based backend check (openai_compatible, deepseek_webui)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            if backend.kind == "openai_compatible":
                url = backend.base_url.rstrip("/") + "/models"
                headers = {"Authorization": f"Bearer {backend.api_key}"} if backend.api_key else {}
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                        "status": "ok", "detail": {"models": len(r.json().get("data", []))}}
            elif backend.kind == "deepseek_webui":
                url = backend.base_url.rstrip("/") + "/health"
                r = await client.get(url)
                ok = r.status_code < 400
                return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                        "status": "ok" if ok else "error", "detail": {}}
    except (ImportError, httpx.HTTPError) as e:
        return {"id": backend.id, "label": backend.label, "kind": backend.kind,
                "status": "error", "detail": {"error": str(e)}}

    return {"id": backend.id, "label": backend.label, "kind": backend.kind,
            "status": "unknown", "detail": {}}


async def health_all() -> List[Dict[str, Any]]:
    return await asyncio.gather(*[health_check_backend(b) for b in BACKENDS])
