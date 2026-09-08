# ⚡ lightning-ocr — Market Feature Map & Gap Analysis

**Date:** 2026-09-08
**Purpose:** Map every feature from the top 15 OCR/document tools in the market, show where lightning-ocr stands, and define what to build.

---

## Competitors Researched

| # | Tool | Stars | License | Category |
|---|------|-------|---------|----------|
| 1 | PaddleOCR MCP (Baidu) | ~88K | Apache-2.0 | OCR engine + MCP |
| 2 | MinerU (OpenDataLab) | ~77K | Apache-based custom | Document parser + MCP |
| 3 | Docling (IBM/LF AI) | ~65K | MIT | Document parser + MCP |
| 4 | sandraschi/ocr-mcp | ~2K | — | Multi-engine OCR MCP |
| 5 | vision-mcp (Amengclass) | ~1K | — | VLM MCP server |
| 6 | Talonic MCP | — | — | Schema extraction MCP |
| 7 | Lido MCP | — | Proprietary | Structured extraction |
| 8 | MarkItDown (Microsoft) | ~50K | MIT | Format conversion |
| 9 | Surya (VikParuchuri) | ~15K | GPL-3.0 | OCR + layout |
| 10 | OCR Provenance MCP | — | — | OCR + provenance + RAG |
| 11 | Auto-Reader OCR | — | — | Arabic-first OCR API |
| 12 | aovestdipaperino/ocr-mcp | — | — | GLM-OCR single tool |
| 13 | Mistral OCR | — | Proprietary | Cloud OCR API |
| 14 | GOT-OCR 2.0 | ~5K | Apache-2.0 | Academic OCR VLM |
| 15 | Tesseract 5 | ~63K | Apache-2.0 | Classic OCR engine |

---

## FEATURE MATRIX — Where We Are

### Legend
- ✅ We have it
- 🔶 Partial / basic implementation
- ❌ Missing — need to build
- 🏆 Best-in-class competitor

---

### A. OCR CORE

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Text detection (bounding boxes) | 🔶 | PaddleOCR, Surya, Docling | HIGH | Tesseract does basic detection |
| Text recognition | ✅ | Everyone | — | Working via Tesseract/GLM-OCR |
| Multi-language OCR | 🔶 | PaddleOCR (111), Tesseract (100+), Surya (90+) | HIGH | We rely on Tesseract lang packs — manual install |
| RTL text (Arabic, Hebrew) | ❌ | PaddleOCR, Auto-Reader, Surya | HIGH | Critical for Arabic市场 |
| Handwriting recognition | ❌ | PaddleOCR-VL, DeepSeek-OCR, Chandra 2 | MEDIUM | Nice-to-have for forms |
| Diacritic handling | ❌ | PaddleOCR, Auto-Reader | HIGH | Arabic diacritics = critical |
| Mixed script detection | ❌ | PaddleOCR-VL (109 langs) | MEDIUM | Code-switched documents |
| Confidence scores per word | ✅ | Talonic, PaddleOCR, Surya | — | Tesseract + EasyOCR now return confidence |
| Per-page confidence | ✅ | OCR Provenance, Talonic | — | Multi-page results average confidence |

---

### B. VLM / AI-BACKED OCR (2026 Standard)

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Vision-Language Model OCR | ❌ | PaddleOCR-VL (96.3%), MinerU VLM (95.3%), DeepSeek-OCR | CRITICAL | THE gap — this is what 2026 demands |
| VLM-based layout understanding | ❌ | PaddleOCR-VL, MinerU, Docling+Granite | CRITICAL | Understanding page structure like a human |
| VLM image description | 🔶 | vision-mcp, OCR Provenance, Chandra | MEDIUM | We have `describe` mode but basic |
| VLM chart/graph reading | ❌ | MinerU, Chandra 2 (92.1% olmOCR-bench) | MEDIUM | Extract values from charts |
| VLM form understanding | ❌ | DeepSeek-OCR, dots.ocr | MEDIUM | Checkboxes, radio buttons, signatures |
| Model-agnostic VLM support | ❌ | vision-mcp (works with any OpenAI-compat VLM) | HIGH | Should work with Ollama, vLLM, LM Studio |
| Local VLM inference | ❌ | GOT-OCR (3GB VRAM), Granite-Docling (0.5GB) | HIGH | Privacy-first, no cloud needed |

---

### C. DOCUMENT PARSING & FORMATS

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| PDF input | ✅ | Everyone | — | Working |
| Image input (PNG/JPG/WebP/GIF) | ✅ | Most tools | — | Working |
| DOCX input | ✅ | Docling (20+ formats), MinerU, MarkItDown (29+) | — | python-docx + text-to-image rendering |
| PPTX input | ✅ | Docling, MinerU, MarkItDown | — | python-pptx + text-to-image rendering |
| XLSX input | ✅ | Docling, MinerU, MarkItDown | — | openpyxl + table-to-image rendering |
| HTML input | ❌ | Docling, MarkItDown | LOW | Web pages |
| EPUB input | ❌ | MarkItDown, Docling | LOW | E-books |
| CSV/TSV input | ❌ | — | LOW | Data files |
| Output: Markdown | 🔶 | MinerU, Docling, Marker, MarkItDown | HIGH | ✅ improve_markdown_output() post-processes headings, lists, tables |
| Output: JSON (structured) | ❌ | Docling (DoclingDocument), MinerU, Talonic | HIGH | ✅ to_structured_json() with metadata, backend, confidence, languages |
| Output: HTML tables | ❌ | MinerU | MEDIUM | For table preservation |
| Output: LaTeX | ❌ | MinerU, GOT-OCR | MEDIUM | Scientific documents |
| Output: DOCX export | ❌ | PaddleOCR v3.5 | LOW | Editable output |

---

### D. TABLE EXTRACTION

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Basic table detection | ✅ | Docling, MinerU, PaddleOCR | — | We have extract_tables |
| Table structure recognition | 🔶 | Docling (TableFormer), MinerU, PaddleOCR-VL | HIGH | Cell boundaries, merged cells |
| Merged cell handling | ❌ | Docling, MinerU | HIGH | Tables with rowspan/colspan |
| Cross-page table merging | ❌ | MinerU | MEDIUM | Tables spanning multiple pages |
| Table-to-HTML output | ❌ | MinerU | HIGH | Preserves structure |
| Table-to-CSV output | ❌ | — | LOW | Simple export |
| Table confidence scores | ❌ | Talonic | MEDIUM | Per-cell confidence |

---

### E. LAYOUT ANALYSIS

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Reading order detection | ❌ | Docling, MinerU, Surya, PaddleOCR-VL | CRITICAL | Multi-column layouts |
| Section/heading detection | ❌ | Docling, MinerU, Marker | HIGH | Document structure |
| Paragraph detection | ❌ | Docling, PaddleOCR-VL | HIGH | Block-level parsing |
| Image/figure detection | ❌ | Docling, MinerU | MEDIUM | Find embedded images |
| Header/footer detection | ❌ | MinerU (strips them), Docling | MEDIUM | Clean output |
| Page number detection | ❌ | MinerU | LOW | Remove from output |
| Caption detection | ❌ | MinerU, Docling | MEDIUM | Figure/table captions |
| List detection | ❌ | Docling, MinerU | MEDIUM | Bullet/numbered lists |
| Footnote detection | ❌ | Docling | LOW | Academic docs |
| Watermark handling | ❌ | Umi-OCR | LOW | Ignore watermarks |
| Orientation classification | ❌ | PaddleOCR | LOW | Rotated pages |

---

### F. FORMULA & SPECIAL CONTENT

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| LaTeX formula recognition | ❌ | MinerU, GOT-OCR, PaddleOCR-VL | HIGH | Scientific papers |
| Chemistry notation | ❌ | GOT-OCR | LOW | Chemistry documents |
| Music notation | ❌ | GOT-OCR | LOW | Sheet music |
| Mathematical equations | ❌ | MinerU, GOT-OCR | HIGH | Academic use |
| Seal/stamp recognition | ❌ | PaddleOCR | LOW | Official documents |
| Barcode/QR reading | ❌ | — | LOW | Logistics |
| Signature detection | ❌ | sandraschi/ocr-mcp | LOW | Form processing |

---

### G. PREPROCESSING & QUALITY

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Image deskew | ✅ | sandraschi/ocr-mcp, Surya | — | Projection profile method, auto-applied |
| Image enhancement | ✅ | sandraschi/ocr-mcp | — | Auto contrast, sharpen, denoise, contrast boost |
| Noise removal | ✅ | Surya, sandraschi | — | Median filter in enhance pipeline |
| Binarization | ✅ | Tesseract (internal), OCRmyPDF | — | Otsu + adaptive threshold methods |
| Image unwarping | ❌ | PaddleOCR | MEDIUM | Curved pages (book scans) |
| Crop/region selection | ❌ | sandraschi/ocr-mcp | LOW | Partial OCR |
| Contrast enhancement | ✅ | sandraschi | — | Auto-contrast + 1.3x contrast boost |
| DPI normalization | ❌ | Docling, MinerU | MEDIUM | Consistent input |
| Quality assessment | ❌ | OCR Provenance, sandraschi | MEDIUM | Auto-reject bad scans |
| Auto-preprocess pipeline | ✅ | — | — | deskew → enhance → OCR, configurable via `preprocess` param |

---

### H. MCP SERVER & AGENT INTEGRATION

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| MCP 2026-07-28 spec | ✅ | We're among the first | — | stateless, server/discover |
| MCP 2025-03-26 spec | ✅ | PaddleOCR, Docling, MinerU | — | Backward compat |
| MCP tools count | ✅ (10) | Talonic (11), sandraschi (14), OCR Provenance (153) | — | Good count |
| stdio transport | ✅ | All MCP servers | — | Standard |
| HTTP transport | ✅ | Few have this | — | Advantage |
| SSE transport | ✅ | Most have this | — | Standard |
| WebSocket transport | ✅ | Rare — only us | — | Advantage |
| Discovery endpoint | ✅ | Rare — only us | — | Advantage |
| `server/discover` RPC | ✅ | We're early | — | 2026-07-28 feature |
| Cache hints (ttlMs) | ✅ | We're early | — | 2026-07-28 feature |
| Bearer token auth | ✅ | Most have basic auth | — | Standard |
| OAuth support | 🔶 | Talonic, Lido | MEDIUM | For enterprise |
| Batch OCR tool | ✅ | sandraschi | — | `ocr_batch` |
| Job history tools | ✅ | OCR Provenance | — | `get_job`, `list_jobs`, `delete_job` |
| Capability description | ✅ | Rare | — | `describe_capabilities` |
| Schema-validated extraction | ❌ | Talonic (11 tools) | HIGH | Extract specific fields with schema |
| Document search | ❌ | OCR Provenance (7 search tools) | MEDIUM | BM25 + semantic search |
| Provenance tracking | ❌ | OCR Provenance (6 tools) | LOW | SHA-256 chain of custody |
| RAG-ready chunking | ❌ | OCR Provenance, RAGFlow | MEDIUM | Auto-chunk for embeddings |
| Vector embeddings | ❌ | OCR Provenance (nomic, ModernBERT) | LOW | Built-in embedding generation |
| Document clustering | ❌ | OCR Provenance | LOW | Auto-group similar docs |
| Prompt templates | ❌ | — | LOW | Pre-built prompts |
| Sampling defaults | ❌ | sandraschi (Ollama) | LOW | Use host LLM for reasoning |

---

### I. WEB UI & EXPERIENCE

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Dashboard UI | ✅ | Most don't have one | — | Our advantage |
| Drag-and-drop upload | ✅ | Lido, some SaaS | — | Working |
| Batch upload UI | ✅ | — | — | Working |
| Dark mode | ✅ | — | — | Working |
| Real-time OCR progress | ❌ | — | MEDIUM | Show processing status |
| Before/after preview | ❌ | — | LOW | Show original vs OCR output |
| Confidence visualization | ❌ | — | MEDIUM | Highlight low-confidence areas |
| Bounding box overlay | ❌ | Surya, PaddleOCR | MEDIUM | Show detected text regions |
| Multi-page preview | ❌ | — | LOW | PDF page navigation |
| Export results | 🔶 | — | MEDIUM | Download as JSON/CSV/Markdown |
| Comparison view | ❌ | OCR Provenance | LOW | Side-by-side results |
| Settings UI | ❌ | — | MEDIUM | Configure backends via UI |

---

### J. DEPLOYMENT & INFRASTRUCTURE

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Docker support | ✅ | Most have this | — | Dockerfile + compose |
| Docker Compose | ✅ | Docling, MinerU | — | 3 compose files |
| NVIDIA GPU support | ✅ | PaddleOCR, MinerU, Docling | — | CUDA offload |
| Intel SYCL support | ✅ | — | — | Intel Arc/UHD |
| Vulkan support | ✅ | — | — | Cross-vendor GPU |
| CPU-only mode | ✅ | Docling, Tesseract | — | 4 GB RAM minimum |
| Apple Silicon support | ❌ | GOT-OCR, vision-mcp | MEDIUM | macOS ARM |
| ARM/Raspberry Pi | ❌ | — | LOW | Edge deployment |
| Kubernetes/Helm | ❌ | Docling, MinerU | MEDIUM | Enterprise deploy |
| Auto-scaling | ❌ | MinerU (router) | LOW | Multi-worker |
| Load balancing | ❌ | MinerU (mineru-router) | LOW | Production scale |
| Health check endpoint | ✅ | Most have this | — | `/api/health` |
| Prometheus metrics | ❌ | Docling | LOW | Monitoring |
| OpenTelemetry tracing | ❌ | — | LOW | Observability |
| Graceful shutdown | ❌ | — | LOW | Clean stop |
| Rate limiting | ❌ | Lido, Talonic | MEDIUM | API protection |
| Webhook notifications | ❌ | OCR Provenance | LOW | Async result delivery |

---

### K. SECURITY & COMPLIANCE

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| SSRF protection | ✅ | Rare | — | Our advantage |
| File type validation | ✅ | Most have basic | — | python-magic |
| Size limits | ✅ | Most have this | — | 50 MB |
| Bearer token auth | ✅ | Most have this | — | Working |
| Input sanitization | 🔶 | — | MEDIUM | Need more |
| Rate limiting | ❌ | Lido, Talonic | MEDIUM | Per-IP/per-key |
| Audit logging | ❌ | OCR Provenance | LOW | Compliance |
| SOC 2 compliance | ❌ | Lido | LOW | Enterprise |
| GDPR compliance | ❌ | Auto-Reader (PDPL) | LOW | EU market |
| Data encryption at rest | ❌ | — | LOW | SQLite encryption |
| Data encryption in transit | ✅ | — | — | HTTPS via reverse proxy |
| API key rotation | ❌ | — | LOW | Security best practice |
| CORS configuration | 🔶 | — | MEDIUM | Needs proper config |

---

### L. DOCUMENT INTELLIGENCE (Advanced)

| Feature | Status | Who Has It | Priority | Notes |
|---------|--------|-----------|----------|-------|
| Invoice extraction | ❌ | Koncile (24 tools), Talonic | MEDIUM | Structured field extraction |
| Contract analysis | ❌ | OCR Provenance (9 tools) | LOW | Clause extraction |
| Form field extraction | ❌ | sandraschi (form detection) | MEDIUM | Checkboxes, text fields |
| Document comparison | ❌ | OCR Provenance (6 tools) | LOW | Diff between documents |
| Document summarization | ❌ | Agenson Horrowitz MCP | MEDIUM | AI summarization |
| Keyword extraction | ❌ | OCR Provenance | LOW | Auto-tagging |
| Document classification | ❌ | — | MEDIUM | Auto-categorize |
| Entity extraction | ❌ | — | MEDIUM | Names, dates, amounts |
| Compliance checking | ❌ | OCR Provenance (SOC2, HIPAA) | LOW | Regulatory |

---

## PRIORITY ROADMAP

### Phase 1 — CATCH UP (Next Release)
*Close the gap with the market standard*

| # | Feature | Effort | Impact | Status |
|---|---------|--------|--------|--------|
| 1 | **VLM backend integration** (PaddleOCR-VL or Qwen2.5-VL via OpenAI-compat API) | HIGH | CRITICAL — closes the #1 gap | 🔶 PaddleOCR-VL configured, not tested with GPU |
| 2 | **Confidence scores** per word/page in OCR output | LOW | HIGH — agents need this | ✅ Tesseract + EasyOCR return per-page confidence |
| 3 | **DOCX/PPTX/XLSX input** via python-docx, python-pptx, openpyxl | MEDIUM | HIGH — format coverage | ✅ converters.py + ocr.py integration |
| 4 | **Deskew preprocessing** | LOW | HIGH — real-world scans | ✅ preprocess.py with auto_preprocess pipeline |
| 5 | **Better Markdown output** (headings, lists, tables preserved) | MEDIUM | HIGH — RAG pipelines | ✅ improve_markdown_output() in ocr.py |
| 6 | **Multi-language auto-detection** | MEDIUM | HIGH — global market | ✅ detect_languages() via pytesseract OSD |
| 7 | **JSON structured output** (not just text) | MEDIUM | HIGH — agent workflows | ✅ to_structured_json() + output_format param |

### Phase 2 — COMPETE (v3.1)
*Match the top tier*

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 8 | **Layout analysis** (reading order, sections, headings) | HIGH | CRITICAL |
| 9 | **Table structure recognition** (merged cells, cell boundaries) | HIGH | HIGH |
| 10 | **LaTeX formula recognition** | HIGH | HIGH — academic market |
| 11 | **Image enhancement preprocessing** (contrast, noise, binarization) | MEDIUM | MEDIUM |
| 12 | **RTL text support** (Arabic, Hebrew) | MEDIUM | HIGH — MENA market |
| 13 | **Schema-validated extraction** (like Talonic) | MEDIUM | HIGH — enterprise |
| 14 | **Batch processing with progress** | MEDIUM | MEDIUM |

### Phase 3 — LEAD (v4.0)
*Unique features no one else has*

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 15 | **Auto-fallback with VLM escalation** (cheap pipeline → VLM for hard pages) | HIGH | UNIQUE — no one does this |
| 16 | **Confidence-based routing** (auto-reject low-quality, escalate to VLM) | MEDIUM | UNIQUE |
| 17 | **Document search** (BM25 + vector hybrid) | HIGH | MEDIUM |
| 18 | **RAG-ready chunking** (auto-chunk with metadata) | MEDIUM | HIGH |
| 19 | **Bounding box visualization** in web UI | MEDIUM | MEDIUM |
| 20 | **Real-time OCR progress** in web UI | LOW | MEDIUM |
| 21 | **Apple Silicon / ARM support** | MEDIUM | MEDIUM |
| 22 | **Prometheus metrics + OpenTelemetry** | MEDIUM | LOW — enterprise |
| 23 | **Kubernetes Helm chart** | MEDIUM | LOW — enterprise |

---

## COMPETITIVE POSITIONING

### What We Are
```
lightning-ocr = OCR Hub + MCP Server + Web UI + Job History
                 (wraps Tesseract, GLM-OCR, EasyOCR + future VLM)
```

### What We're NOT (Yet)
```
We are NOT a document parser like Docling/MinerU
We are NOT a VLM like PaddleOCR-VL/DeepSeek-OCR
We are NOT an extraction service like Talonic/Lido
```

### What We Should Become
```
lightning-ocr = Universal OCR Hub
                + MCP 2026-07-28 server (best-in-class)
                + VLM-powered accuracy (via plugin backends)
                + Smart routing (cheap → expensive based on difficulty)
                + Web UI (best UX in the market)
                + Job history + search + RAG pipeline
```

### Unique Selling Points (USPs)
1. **Best MCP implementation** in the OCR space (10 tools, 4 transports, 2026-07-28)
2. **Only OCR server with a web UI** that also has full MCP support
3. **Auto-fallback chain** — no other tool does AI → Tesseract → EasyOCR automatically
4. **Zero-config CPU mode** — works on 4 GB RAM, no GPU needed
5. **Job history + persistence** — no other OCR MCP server has this
6. **Security hardened** — SSRF protection, auth, validation (rare in open-source OCR)

---

## MARKET OPPORTUNITY

### Underserved Niches
1. **MCP-first OCR** — Most OCR tools add MCP as an afterthought. We're MCP-native.
2. **Self-hosted with UI** — Most self-hosted options are CLI-only. We have a dashboard.
3. **Multi-backend with fallback** — No one else chains multiple engines automatically.
4. **Small team / edge deployment** — MinerU needs 4GB VRAM, PaddleOCR-VL needs 8GB. We run on 4GB RAM total.
5. **Agent-friendly** — 10 MCP tools with proper schemas, batch processing, job history.

### Target Users
- AI agent developers (Claude, Cursor, Copilot users)
- Small teams without GPU infrastructure
- Privacy-sensitive deployments (no cloud APIs)
- RAG pipeline builders who need OCR → chunk → embed
- Enterprise document processing (with security requirements)

---

## BENCHMARK COMPARISON (Published)

| Engine | OmniDocBench v1.6 | Speed (L4 GPU) | VRAM | License |
|--------|-------------------|----------------|------|---------|
| PaddleOCR-VL-1.6 | **96.33%** | 3.6 s/page | ~2 GB | Apache-2.0 |
| MinerU VLM | 95.30% | 4.7 s/page | ~8 GB | Apache-based |
| MinerU hybrid | 95.26% | — | ~8 GB | Apache-based |
| MinerU pipeline | 86.47% | — | ~4 GB | Apache-based |
| DeepSeek-OCR 2 | 90.25% | — | ~8 GB | MIT |
| dots.ocr | 90.77% | — | ~3.5 GB | MIT |
| Docling (pipeline) | ~64% (digital only) | 2.8 s/page | CPU OK | MIT |
| Tesseract 5 | ~85-90% (estimated) | 1.2 s/page | CPU OK | Apache-2.0 |
| **lightning-ocr (Tesseract)** | **~85-90%** | **~0.2 s/page** | **CPU OK** | **Apache-2.0** |
| **lightning-ocr (GLM-OCR)** | **TBD** | **TBD** | **~4 GB** | **Apache-2.0** |

**Our accuracy gap:** ~6-11% behind PaddleOCR-VL on complex documents.
**Our speed advantage:** 6-18x faster than VLM-based tools on CPU.
**Our VRAM advantage:** 0 GB vs 2-8 GB for VLM competitors.

---

*This document is a living spec. Update as features are implemented.*
