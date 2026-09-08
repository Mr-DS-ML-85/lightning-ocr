---
name: picoclaw
description: >-
  Minimal, zero-overhead OCR for CI pipelines, shell scripts, and edge devices.
  Use when the user needs a quick single-command OCR with plain text output,
  wants to pipe OCR results to other tools, is working in a script or automation,
  or needs OCR without any async/event-loop overhead.
  Do NOT use when rich structured output, invoice parsing, or multi-pass analysis is needed —
  use openclaw for those tasks.
license: Apache-2.0
compatibility: Requires lightning-ocr server running at http://localhost:8000
metadata:
  author: lightning-ocr
  version: "1.0.0"
  mcp_server: http://localhost:8000/mcp
allowed-tools:
  - Bash(python:*)
  - Bash(python3:*)
  - Read
  - Write
  - mcp__picoclaw__pico_ocr
  - mcp__picoclaw__pico_find
  - mcp__picoclaw__pico_json
  - mcp__picoclaw__pico_batch
  - mcp__picoclaw__pico_backends
  - mcp__picoclaw__pico_describe
---

# PicoClaw — Minimal OCR Agent

PicoClaw is the lightest possible OCR interface to lightning-ocr.
It is synchronous, has zero async overhead, and outputs plain text or JSON to stdout.
Ideal for CI/CD, shell pipelines, edge devices, and one-liner scripts.

## When to use PicoClaw

Use PicoClaw when the user:
- Needs a quick one-liner OCR command
- Is writing a shell script or bash pipeline
- Is on an edge device or Raspberry Pi with limited RAM
- Needs OCR output piped to another tool (grep, jq, sed)
- Wants the simplest possible interaction: file in → text out
- Is running in a CI/CD pipeline (GitHub Actions, Jenkins, etc.)

Use **openclaw** instead when:
- Complex invoice parsing is needed
- Multiple passes or report generation is required
- Structured output with confidence scoring is needed

## Quick Commands

```bash
# Plain OCR
python agents/picoclaw/scripts/pico_ocr.py scan.png

# OCR to Markdown
python agents/picoclaw/scripts/pico_ocr.py doc.png --mode document

# Find a term
python agents/picoclaw/scripts/pico_ocr.py invoice.png --find "Total"

# Extract JSON
python agents/picoclaw/scripts/pico_ocr.py form.png --json

# Batch OCR → one text per line
python agents/picoclaw/scripts/pico_ocr.py *.png --batch

# Check backends
python agents/picoclaw/scripts/pico_ocr.py --backends

# Shell pipeline: OCR and grep
python agents/picoclaw/scripts/pico_ocr.py receipt.png | grep -i "total"

# Shell pipeline: OCR and save
python agents/picoclaw/scripts/pico_ocr.py scan.png > output.txt

---

### 🧩 **MCP Tool Selection**

| Task | Tool |
| --- | --- |
| **Plain text from image** | `pico_ocr mode=ocr` |
| **Markdown from document** | `pico_ocr mode=document` |
| **Find text near term** | `pico_find` |
| **Extract JSON fields** | `pico_json` |
| **Multiple images** | `pico_batch` |
| **Backend health** | `pico_backends` |
| **Visual description** | `pico_describe` |

---

### 📡 **Output Format**

All PicoClaw tools return **plain UTF-8 text** to `stdout`.

* **JSON Support:** Use the `--json` flag to receive machine-readable output.
* **Pipe Integrity:** Status messages and logs are routed to `stderr`, ensuring `stdout` remains clean and pipe-safe for shell automation.

---

### 📂 **Supporting Files**

* **Quick reference:** Command cheatsheet for common workflows.
* **`pico_ocr.py` script:** The primary all-in-one OCR entry point.
* **`pico_batch.sh` script:** High-performance Bash wrapper for batch processing.

---

### ⚠️ **Edge Cases**

* **Empty output:** The file may be blank or unreadable; try `pico_describe` to verify visual content.
* **"Connection refused":** The core server is likely down; run `docker compose up`.
* **Slow on first run:** Model initialization takes **30–60s**; subsequent calls are near-instant.
* **Non-ASCII text:** For specialized scripts, use `--backend-id tesseract` with the appropriate language packs installed.