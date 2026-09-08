# OpenClaw Backend Reference

## Available Backends

### glm-ocr (default, priority 0)
- Type: openai_compatible (llama.cpp)
- Best quality for structured documents and complex layouts
- Requires: llama.cpp server running at GLM_OCR_BASE_URL
- Model: GLM-OCR-GGUF (Q4_K_M on CPU, Q8_0 on GPU)

### deepseek-ocr (priority 1, optional)
- Type: deepseek_webui
- Good for: Asian language documents, dense text
- Requires: DeepSeek-OCR-WebUI container running
- Enable with: `docker compose --profile deepseek up`

### tesseract (priority 10, always available)
- Type: local fallback
- Good for: simple text, printed documents, offline use
- Always available — no network required
- Lower quality on complex layouts

### easyocr (priority 11, optional)
- Type: local fallback
- Good for: multi-language documents
- Enable with: `EASYOCR_ENABLED=true` in environment
- Heavy: requires ~1 GB RAM

## Checking Backend Health

Use `list_ocr_backends` tool or:
```bash
curl http://localhost:8000/api/health


# Forcing a Specific Backend
## Pass backend_id to any tool call:
```
backend_id=glm-ocr — force GLM-OCR
backend_id=tesseract — force Tesseract (fastest, most reliable)
backend_id=easyocr — force EasyOCR
Leave backend_id empty for automatic selection + fallback.

```