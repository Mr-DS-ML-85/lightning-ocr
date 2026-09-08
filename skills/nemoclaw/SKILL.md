---
name: nemoclaw
description: >-
  Pipeline-oriented document processing for enterprise workflows.
  Use when the user needs multi-step, structured document processing:
  invoice pipelines (fields + totals + line items), contract analysis
  (parties + clauses + dates), medical document processing,
  or when the document type is unknown and auto-classification is needed.
  NemoClaw runs declarative pipelines — multiple OCR steps in sequence
  with structured output at each stage.
  Use for enterprise batch workflows, ADK integration, or
  when structured per-step output is required.
license: Apache-2.0
compatibility: Requires lightning-ocr server running at http://localhost:8000
metadata:
  author: lightning-ocr
  version: "1.0.0"
  mcp_server: http://localhost:8000/mcp
  pipelines:
    - invoice
    - contract
    - medical
    - general
allowed-tools:
  - Bash(python:*)
  - Read
  - Write
  - mcp__nemoclaw__nemo_run_pipeline
  - mcp__nemoclaw__nemo_classify
  - mcp__nemoclaw__nemo_auto
  - mcp__nemoclaw__nemo_adk_tools
---

# NemoClaw — Pipeline Document Processing

NemoClaw processes documents through declarative multi-step pipelines.
Each pipeline runs a sequence of OCR passes and aggregates structured output.

## When to use NemoClaw

Use NemoClaw when the user:
- Has a **known document type** that needs structured multi-step processing
- Needs the **document type auto-detected** before processing
- Is building an **enterprise workflow** or batch processing pipeline
- Needs **ADK/Vertex AI** integration (NemoClaw exposes an ADK-compatible connector)
- Wants **pipeline step transparency** (see which step extracted what)
- Is processing invoices, contracts, or medical documents at scale

Use **openclaw** instead for:
- Single-file, single-mode extraction
- Interactive use

## Document Types → Pipeline

| Document type | Pipeline | Steps |
|---|---|---|
| Invoice / Bill / Receipt | `invoice` | markdown + find_total + structured_fields |
| Contract / Agreement | `contract` | full_text + parties + key_clauses |
| Medical / Prescription | `medical` | raw_ocr + patient_info |
| Unknown / General | `general` (default) | markdown + description |

## Step-by-Step Workflows

### Known document type
1. Call `nemo_run_pipeline` with `pipeline=invoice` (or contract/medical/general)
2. Review pipeline steps output — each step shows its tool and extracted text
3. Key context values are available in the result (`context` dict)
4. Check `errors` list — if a required step failed, `success=false`

### Unknown document type
1. Call `nemo_auto` — it classifies first, then runs the matching pipeline
2. The result includes the detected document type and confidence
3. Review the combined output

### Just classification
1. Call `nemo_classify` — fast, runs plain OCR and matches type patterns
2. Returns: `doc_type`, `confidence` (high/medium/low), `scores` per type

### ADK integration
1. Call `nemo_adk_tools` to get the MCP tool list in ADK-compatible JSON format
2. Use this to wire NemoClaw into a Google ADK LlmAgent

## Pipeline Output Structure

Each pipeline result includes:
```json
{
  "pipeline":   "invoice",
  "success":    true,
  "steps": [
    {"name": "markdown",          "text": "# Invoice...",  "tool": "ocr_image"},
    {"name": "find_total",        "text": "$1,234.56",     "tool": "ocr_image"},
    {"name": "structured_fields", "text": "{...json...}",  "tool": "ocr_image"}
  ],
  "context": {
    "markdown":          "# Invoice...",
    "find_total":        "$1,234.56",
    "structured_fields": "{...}"
  },
  "errors": []
}

### 📂 **Supporting Files**

* **Pipeline reference:** Comprehensive documentation detailing each processing step.
* **Document types:** A manual of classification rules used to categorize incoming files.
* **`nemo_invoice.py`:** Specialized pipeline for financial extraction (Totals, Tax, IBAN, etc.).
* **`nemo_contract.py`:** Specialized pipeline for legal document parsing (Clauses, Dates, Parties).
* **`nemo_auto.py`:** The "Smart" script—automatically classifies documents and triggers the correct pipeline.

---

### ⚠️ **Edge Cases**

| Condition | Meaning & Resolution |
| --- | --- |
| **`success=false`** | A required step failed. Inspect the `errors` list in the response. Usually indicates an unreachable OCR backend. |
| **Wrong pipeline selected** | If `nemo_auto` misclassifies, use `nemo_run_pipeline` with an explicit `pipeline=` override. |
| **`confidence=low`** | Document is ambiguous. Review the `scores` dictionary and manually select the correct type. |
| **Medical documents** | **Notice:** Output is for informational purposes and is **NOT** a substitute for professional medical review. |
| **Large multi-page docs** | Process pages individually via the **PDF Agent**, then pass the extracted text to **NemoClaw**. |

