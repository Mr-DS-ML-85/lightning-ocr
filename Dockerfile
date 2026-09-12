# ── lightning-ocr Dockerfile ──────────────────────────────────────────────────
# Multi-stage: deps layer cached separately for fast rebuilds.
# Works on CPU-only, CUDA, and Intel SYCL (via compose profiles).
FROM python:3.12-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
 tesseract-ocr \
 tesseract-ocr-eng \
 tesseract-ocr-ben \
 tesseract-ocr-osd \
 libgl1 \
 libglib2.0-0 \
 curl \
 libmagic1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY app/ ./app/
COPY connector/ ./connector/

# Persistent storage mount point
RUN mkdir -p /data

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--timeout-keep-alive", "120"]