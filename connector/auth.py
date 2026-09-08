"""
lightning-ocr · Connector Auth
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Header

from app.config import settings

log = logging.getLogger("lightning_ocr.connector.auth")


async def require_auth(
    authorization: Optional[str] = Header(None, alias="Authorization"),
) -> bool:
    """Require API key authentication."""
    if not settings.API_KEY:
        return True
    
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
    
    provided_key = authorization[7:].strip()
    if provided_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    
    return True


def oauth_metadata(base_url: str) -> Dict[str, Any]:
    """Generate OAuth 2.1 resource metadata."""
    return {
        "resource": "lightning-ocr",
        "scopes": ["ocr:read", "ocr:write"],
        "authentication": {
            "type": "bearer" if settings.API_KEY else "none",
            "header": "Authorization",
            "format": "Bearer <token>",
        },
        "endpoints": {
            "discovery": f"{base_url}/.well-known/mcp",
            "oauth": f"{base_url}/.well-known/oauth-protected-resource",
        },
    }
