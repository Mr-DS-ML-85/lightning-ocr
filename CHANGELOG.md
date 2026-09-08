# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.1] — 2026-09-08

### Added
- **MCP `file_path` uploads** — `ocr_image` and `ocr_batch` now accept a local `file_path`
  in addition to base64 bytes, avoiding param truncation on large files
  ([`9564232`](https://github.com/youruser/lightning-ocr/commit/9564232)).
- **Full format parity** with the WebUI (`detect_content_type`, `app/ocr.py:detect_content_type`):
  - Images: PNG, JPEG, WebP, GIF, BMP, TIFF, ICO, AVIF
  - **SVG auto-rasterization** via `rsvg-convert` (`app/converters.py:rasterize_svg`)
  - Documents: PDF, DOC/DOCX, PPT/PPTX, XLS/XLSX, TXT/MD
- **Parallel batch** — `ocr_batch` now processes files concurrently (`asyncio.gather`)
  and returns **one content block per file** plus a `meta {count, ok, backend}` summary
  (HTTP gateway, stdio, and REST `/api/ocr/batch`).
- **Bigger limits** — `MAX_UPLOAD_BYTES` raised to **100 MB**, `MAX_BATCH_FILES` = **50**
  (centralized in `app/ocr.py`, imported by gateway/stdio/ws).
- **Office → Markdown/JSON source extraction** — `converter_doc_*` extractors
  (`app/converters.py`) read the real DOCX/PPTX/XLSX content (headings, lists, tables)
  when `output_format=markdown|json`, instead of OCR-ing a render (`_extract_document_output`).
- **Confidence-scored language detection** — `detect_languages` (`app/ocr.py`) uses Tesseract
  OSD first, then per-word confidence scoring across `TESSERACT_LANG`; Bengali vs English is
  now detected reliably where OSD alone failed.
- `output_format` passthrough on `ocr_batch` (`text` | `markdown` | `json`).
- Declared runtime deps `python-docx`, `python-pptx`, `openpyxl`, `numpy`
  ([`19c1aa8`](https://github.com/youruser/lightning-ocr/commit/19c1aa8)).

### Changed
- REST `/api/ocr` and `/api/ocr/batch` use `detect_content_type` (extension-first, any `image/*`).
- WebUI file picker `accept` broadened to match the supported formats.
- MCP tool `ocr_batch` schema documents `maxItems: 50` and the new `output_format` property.
- OpenCode note: `--legacy25` is documented for OpenCode's `2025-03-26` protocol support.

### Fixed
- `UnboundLocalError` in `app/mcp.py` and `connector/transport_stdio.py` — module-level `os`
  shadowed by function-local `import os` inside `save_template` handlers
  ([`9564232`](https://github.com/youruser/lightning-ocr/commit/9564232)).
- Deskew preprocessing now actually runs (dependency `numpy` was missing).
- DOCX Markdown heading level bug — `min(int(...), 6)` instead of raw compare.
- stdio transport now forwards `preprocess` to the OCR pipeline
  ([`e23e77b`](https://github.com/youruser/lightning-ocr/commit/e23e77b)).
- MCP protocol version negotiation for OpenCode clients pedantic about `2025-03-26`
  ([`13a0565`](https://github.com/youruser/lightning-ocr/commit/13a0565)).
- Standard result shape preserved for `output_format=json` even when auto-fallback
  "unrecognized" heuristics kick in
  ([`7dc3a49`](https://github.com/youruser/lightning-ocr/commit/7dc3a49)), plus an adaptive
  median filter improves scan quality.

## [3.0] — 2026

### Added
- MCP 2026-07-28 spec support — stateless protocol, `server/discover` RPC, cache hints,
  per-request `_meta`, transport-specific response framing.
- 10 MCP tools (was 5): `ocr_batch`, `get_job`, `list_jobs`, `delete_job`, `describe_capabilities`.
- Discovery endpoint `/.well-known/mcp` with `transports` dict, `capabilities`,
  `protocol_versions`.
- Streamable HTTP, SSE (legacy), WebSocket, and stdio transports.

### Fixed
- Backward compatibility with the `2025-03-26` (OpenCode) handshake.
- REST endpoint behavior fixes (`/api/ocr` response shape).

## [2.1] — 2026

### Added
- GLM-OCR conditional registration.
- Multi-language Tesseract support.
- Bengali OCR.

### Fixed
- REST endpoint correctness for multipart uploads.

## [2.0] — 2026

Initial production release.