#!/bin/sh
# entrypoint.sh — GLM-OCR launcher
# Checks if local model exists; if not, downloads from HuggingFace.

MODEL_DIR="/models"
MODEL="$MODEL_DIR/GLM-OCR-Q8_0.gguf"
MMPROJ="$MODEL_DIR/mmproj-GLM-OCR-Q8_0.gguf"
SERVER="/app/llama-server"

if [ -f "$MODEL" ] && [ -f "$MMPROJ" ]; then
  echo "✅ Local model found, using /models/"
  exec "$SERVER" \
    -m "$MODEL" \
    --mmproj "$MMPROJ" \
    "$@"
else
  echo "📥 Model not found locally, downloading from HuggingFace..."
  exec "$SERVER" \
    -hf ggml-org/GLM-OCR-GGUF:Q8_0 \
    "$@"
fi
