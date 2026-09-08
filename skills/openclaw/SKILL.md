---
name: openclaw
description: >-
  OCR and document intelligence using the OpenClaw agent via lightning-ocr MCP.
  Use when the user wants to extract text from images or PDFs, parse invoices,
  search for terms in documents, analyse charts or figures, batch-process
  multiple images, or generate structured document reports.
  Do NOT use for non-OCR tasks, web browsing, or code generation.
license: Apache-2.0
compatibility: Requires lightning-ocr server running at http://localhost:8000
metadata:
  author: lightning-ocr
  version: "1.0.0"
  mcp_server: http://localhost:8000/mcp
  mcp_tools:
    - ocr_document
    - ocr_invoice
    - ocr_batch_files
    - search_terms
    - analyse_figure
    - describe_image
    - generate_report
    - extract_json
allowed-tools:
  - Bash(python:*)
  - Read
  - Write
  - mcp__openclaw__ocr_document
  - mcp__openclaw__ocr_invoice
  - mcp__openclaw__ocr_batch_files
  - mcp__openclaw__search_terms
  - mcp__openclaw__analyse_figure
  - mcp__openclaw__describe_image
  - mcp__openclaw__generate_report
  - mcp__openclaw__extract_json
---

# OpenClaw — OCR and Document Intelligence

OpenClaw is the primary OCR and document intelligence agent for lightning-ocr.
It provides 8 specialist tools via MCP. Always prefer MCP tool calls over
running scripts directly unless the user explicitly asks to run a script.

## When to use OpenClaw

Trigger this skill whenever the user:
- Uploads an image, PDF, or scan and asks to "read", "extract", "parse", or "OCR" it
- Says "get the text from", "what does this say", "read this invoice/receipt/contract"
- Asks to find a specific term or value in a document
- Wants to analyse a chart, graph, table, or figure
- Needs to process multiple files at once ("batch", "all these images", "folder of scans")
- Asks for a "report" or "analysis" of a document
- Wants structured JSON extracted from a form or document

## MCP Tool Selection Guide

Pick the right tool for the task:

| User asks for... | Use this tool |
|---|---|
| "Extract text / read this document" | `ocr_document` mode=document |
| "Parse this invoice / receipt" | `ocr_invoice` |
| "Process these N files" | `ocr_batch_files` |
| "Find Total / Date / Name in this doc" | `search_terms` |
| "Analyse this chart / graph / figure" | `analyse_figure` |
| "Describe what's in this image" | `describe_image` |
| "Generate a report for this document" | `generate_report` |
| "Extract fields as JSON / fill this schema" | `extract_json` |

## Step-by-Step Workflow

### Single document extraction
1. Confirm you have the file path or base-64 encoded image
2. Call `ocr_document` with appropriate `mode`:
   - `document` → clean Markdown (default, good for structured docs)
   - `ocr` → plain text (good for scans)
   - `free` → raw text, no formatting
3. Present the result to the user
4. If confidence looks low (garbled text, symbols), retry with `mode=ocr` and Tesseract fallback

### Invoice parsing
1. Call `ocr_invoice` — it runs two internal passes automatically
2. Review `fields` dict: invoice_number, date, total, vendor, client
3. Check `confidence` field — if "low", warn the user and show `missing` fields
4. Show line items separately if present

### Batch processing
1. Collect all file paths into a list
2. Call `ocr_batch_files` with the list and appropriate `mode`
3. Report per-file results — flag any with `error` field

### Term search
1. Call `search_terms` with the file path and a `terms` list
2. Present context found for each term
3. If a term is not found, say so clearly

### Figure analysis
1. Call `analyse_figure` — it runs figure mode + describe mode internally
2. Present `figure_text` (labels, axes, data) and `description` separately
3. Note any data trends visible in the figure analysis

### Report generation
1. Ask user for report_type if unclear: general | invoice | contract | receipt
2. Call `generate_report` with the correct type and any `find_terms`
3. Render the returned Markdown directly to the user

### JSON schema extraction
1. Confirm the schema with the user if not provided
2. Call `extract_json` with `schema` dict
3. If `_parse_error` is in the result, tell the user the model couldn't produce valid JSON
   and show the `_raw` field instead

## Modes Reference

| Mode | Output | Best for |
|---|---|---|
| `document` | Clean Markdown | Reports, invoices, structured docs |
| `ocr` | Plain text | Scanned pages, handwriting |
| `free` | Raw text | Quick reads, OCR debugging |
| `figure` | Label + axis analysis | Charts, graphs, diagrams |
| `describe` | Visual description | Photos, mixed content |
| `find` | Context around term | Specific field lookup |
| `freeform` | Custom prompt output | Specialised extraction |

## Error Handling

- **isError=true in tool response**: The backend failed. Check backend health with `list_ocr_backends`
- **"fallback used" in meta**: Primary backend was unreachable; Tesseract was used automatically
- **Empty text result**: Image may be too small, blurry, or a blank page. Try `describe_image` to confirm
- **Connection refused**: lightning-ocr server is not running. Ask the user to start it with `docker compose up`

## Backend Priority Order

1. GLM-OCR via llama.cpp (AI, best quality)
2. DeepSeek-OCR-WebUI (if enabled)
3. Tesseract-OCR (always available, local fallback)
4. EasyOCR (if enabled, heavier)

Set `auto_fallback=true` (default) to try all in order automatically.

## Supporting Files

- [OCR modes reference](./references/modes.md) — detailed mode descriptions with examples
- [Backend configuration](./references/backends.md) — how to configure and troubleshoot backends
- [Extract document script](./scripts/ocr_document.py) — standalone extraction script
- [Invoice parser script](./scripts/ocr_invoice.py) — standalone invoice parsing script
- [Batch processing script](./scripts/ocr_batch.py) — batch OCR script
- [Report generator script](./scripts/ocr_report.py) — standalone report script

## Common Edge Cases

- **Multi-page PDFs**: Use the PDF agent (`python agents/pdf_agent.py`) for multi-page work; individual MCP tools handle single pages
- **Rotated scans**: `ocr` mode handles rotation better than `document` mode
- **Tables**: `document` mode preserves Markdown tables; `figure` mode extracts table data
- **Handwriting**: Tesseract backend is often better for handwriting than GLM-OCR; use `backend_id=tesseract`
- **Very low resolution**: Preprocessing is not supported — ask the user to provide a higher-resolution scan
- **Mixed languages**: Pass `backend_id=tesseract` and configure `EASYOCR_LANGS` for multi-language support