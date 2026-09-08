"""
lightning-ocr · Connector Middleware
"""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import FastAPI, Request, Response

log = logging.getLogger("lightning_ocr.connector.middleware")


def add_middleware(app: FastAPI) -> None:
    """Add common middleware to the FastAPI app."""
    from fastapi.middleware.cors import CORSMiddleware

    # ── CORS ─────────────────────────────────────────────────────────────────
    # allow_origins=["*"] with allow_credentials=True is invalid per CORS spec.
    # Bearer tokens do NOT require allow_credentials=True (only cookies do).
    # So we use wildcard origins without credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable) -> Response:
        log.info(f"Request: {request.method} {request.url}")
        response = await call_next(request)
        log.info(f"Response: {response.status_code}")
        return response

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response
