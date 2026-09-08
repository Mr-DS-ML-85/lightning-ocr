"""
lightning-ocr · Configuration
All settings come from environment variables with sane defaults.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BackendKind = Literal["openai_compatible", "deepseek_webui", "tesseract", "easyocr", "glm_engine"]
AccelMode  = Literal["auto", "cuda", "sycl", "vulkan", "cpu"]


class BackendConfig(BaseModel):
    """One OCR backend service."""
    id:           str        = Field(...,  description="Unique backend id")
    label:        str        = Field(...,  description="Human-friendly label")
    kind:         BackendKind= Field(...,  description="Backend type")
    base_url:     str        = Field("",   description="Base URL (remote kinds)")
    api_key:      str        = Field("sk-no-key-required")
    model:        str        = Field("",   description="Preferred model id")
    timeout:      float      = Field(180.0)
    priority:     int        = Field(0,    description="Lower = tried first in fallback chain")
    enabled:      bool       = Field(True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── llama.cpp server ────────────────────────────────────────────────────
    GLM_OCR_BASE_URL:   str = "http://llama:8080/v1"
    GLM_OCR_API_KEY:    str = "sk-no-key-required"
    GLM_OCR_MODEL:      str = "GLM-OCR"

    # ── DeepSeek WebUI ──────────────────────────────────────────────────────
    DEEPSEEK_OCR_BASE_URL: str = "http://deepseek-webui:8000"
    DEEPSEEK_OCR_API_KEY:  str = ""
    DEEPSEEK_OCR_MODEL:    str = ""

    # ── hardware / offload ──────────────────────────────────────────────────
    ACCEL_MODE:         AccelMode = "auto"   # auto | cuda | sycl | vulkan | cpu
    N_GPU_LAYERS:       str       = "auto"   # int, 'auto', 'all', or '0' for cpu-only
    CTX_SIZE:           int       = 4096     # keep small for 4 GB RAM
    N_THREADS:          int       = 4
    FLASH_ATTN:         bool      = True
    MLOCK:              bool      = False    # set True if RAM >= 8 GB
    CACHE_TYPE_K:       str       = "q8_0"  # KV cache quantisation
    CACHE_TYPE_V:       str       = "q8_0"

    # ── fallback OCR engines ────────────────────────────────────────────────
    TESSERACT_ENABLED:  bool = True
    TESSERACT_LANG:     str  = "eng+ben"   # Tesseract languages (tesseract --list-langs)
    EASYOCR_ENABLED:    bool = False         # heavier, ~1 GB RAM
    EASYOCR_LANGS:      str  = "en"

    # ── persistence ─────────────────────────────────────────────────────────
    DB_PATH:            str = "./data/lightning_ocr.db"

    # ── custom backend list (JSON) ──────────────────────────────────────────
    OCR_BACKENDS:       str = ""

    # ── API security ────────────────────────────────────────────────────────
    API_KEY:            str = ""             # optional bearer token for /api/*
    ADMIN_KEY:          str = ""

    # ── MCP ─────────────────────────────────────────────────────────────────
    MCP_ENABLED:        bool = True


def load_backends(s: Settings) -> List[BackendConfig]:
    """Load backend list from OCR_BACKENDS JSON or build from individual envs."""
    raw = s.OCR_BACKENDS.strip()
    if raw:
        try:
            data = json.loads(raw)
            return [BackendConfig(**item) for item in data]
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Invalid OCR_BACKENDS JSON: {exc}") from exc

    backends: List[BackendConfig] = []

    # Primary: GLM-OCR via transformers engine (local, no server needed)
    backends.append(BackendConfig(
        id="glm-ocr-engine", label="GLM-OCR Engine (transformers)",
        kind="glm_engine",
        priority=0,
    ))

    # Secondary: GLM-OCR via llama.cpp (skip if no URL configured)
    if s.GLM_OCR_BASE_URL:
        backends.append(BackendConfig(
            id="glm-ocr", label="GLM-OCR (llama.cpp)",
            kind="openai_compatible",
            base_url=s.GLM_OCR_BASE_URL,
            api_key=s.GLM_OCR_API_KEY,
            model=s.GLM_OCR_MODEL,
            priority=1,
        ))

    # Tertiary: DeepSeek-OCR-WebUI (optional, profile-gated)
    if s.DEEPSEEK_OCR_BASE_URL:
        backends.append(BackendConfig(
            id="deepseek-ocr", label="DeepSeek-OCR-WebUI",
            kind="deepseek_webui",
            base_url=s.DEEPSEEK_OCR_BASE_URL,
            api_key=s.DEEPSEEK_OCR_API_KEY,
            model=s.DEEPSEEK_OCR_MODEL,
            priority=1,
        ))

    # Tertiary: Tesseract (always-local fallback)
    if s.TESSERACT_ENABLED:
        backends.append(BackendConfig(
            id="tesseract", label="Tesseract-OCR (local fallback)",
            kind="tesseract",
            priority=10,
        ))

    # Quaternary: EasyOCR (heavier local fallback)
    if s.EASYOCR_ENABLED:
        backends.append(BackendConfig(
            id="easyocr", label="EasyOCR (local fallback)",
            kind="easyocr",
            priority=11,
        ))

    return backends


settings = Settings()
BACKENDS: List[BackendConfig] = load_backends(settings)