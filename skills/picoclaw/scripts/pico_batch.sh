#!/usr/bin/env bash
# PicoClaw — Bash batch wrapper
# Processes all images in a directory via pico_ocr.py
# Usage:
#   ./pico_batch.sh /path/to/images/
#   ./pico_batch.sh /path/to/images/ --mode document
#   ./pico_batch.sh /path/to/images/ --output-dir ./results/

set -euo pipefail

DIR="${1:-.}"
shift || true

MODE="ocr"
OUTPUT_DIR=""
EXT="png jpg jpeg webp"
MCP_URL="${LIGHTNING_OCR_MCP_URL:-http://localhost:8000/mcp}"

# Parse optional args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)         MODE="$2";       shift 2 ;;
    --output-dir)   OUTPUT_DIR="$2"; shift 2 ;;
    --ext)          EXT="$2";        shift 2 ;;
    *) shift ;;
  esac
done

if [[ -n "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_DIR"
fi

TOTAL=0
OK=0
FAIL=0

echo "⚡ PicoClaw Batch — $DIR (mode=$MODE)" >&2

for ext in $EXT; do
  while IFS= read -r -d '' file; do
    TOTAL=$((TOTAL+1))
    fname=$(basename "$file" ".$ext")

    if [[ -n "$OUTPUT_DIR" ]]; then
      outfile="$OUTPUT_DIR/${fname}.txt"
      if python3 "$(dirname "$0")/pico_ocr.py" \
          --mode "$MODE" --output "$outfile" "$file" 2>/dev/null; then
        echo "  ✓ $fname" >&2
        OK=$((OK+1))
      else
        echo "  ✗ $fname" >&2
        FAIL=$((FAIL+1))
      fi
    else
      echo "=== $fname ==="
      python3 "$(dirname "$0")/pico_ocr.py" --mode "$MODE" "$file" || true
      echo ""
    fi
  done < <(find "$DIR" -maxdepth 1 -name "*.$ext" -print0 2>/dev/null)
done

echo "Done: $OK/$TOTAL succeeded, $FAIL failed" >&2