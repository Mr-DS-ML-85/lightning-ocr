---
name: hermes-ocr
description: >-
  LLM-guided OCR and document reasoning using the Hermes agent.
  Use when the user wants intelligent document analysis that goes beyond
  simple text extraction: answering questions about a document, extracting
  structured data to a custom JSON schema, multi-step reasoning over a document,
  summarizing complex documents, or when the best OCR mode is unclear and
  the LLM should decide.
  Requires a running llama.cpp server with a Hermes-family model.
  Do NOT use for simple text extraction (use openclaw or picoclaw instead) —
  Hermes adds LLM overhead and is best for tasks requiring reasoning.
license: Apache-2.0
compatibility: >-
  Requires lightning-ocr at http://localhost:8000 AND
  llama.cpp Hermes server at http://localhost:8080.
metadata:
  author: lightning-ocr
  version: "1.0.0"
  mcp_server: http://localhost:8001/mcp
  llm_server: http://localhost:8080/v1
  llm_model: hermes-3
allowed-tools:
  - Bash(python:*)
  - Read
  - Write
  - mcp__hermes-ocr__hermes_extract
  - mcp__hermes-ocr__hermes_qa
  - mcp__hermes-ocr__hermes_extract_schema
  - mcp__hermes-ocr__hermes_react
  - mcp__hermes-ocr__hermes_summarize
---

# Hermes — LLM-Guided OCR and Document Reasoning

Hermes combines a Hermes-family LLM (via llama.cpp) with lightning-ocr's
OCR backends to provide intelligent document analysis. The LLM selects the
best OCR mode, interprets results, and can reason across multiple passes.

## When to use Hermes

Trigger this skill when the user:
- **Asks a question about a document** ("What is the payment due date?", "Who are the parties in this contract?")
- **Wants structured data extracted** to a schema they define
- **Needs multi-step reasoning** ("Find all dates and tell me if any are overdue")
- **Wants a document summarized** with specific length or style
- **Doesn't know which OCR mode to use** (Hermes decides automatically)
- The task requires **combining OCR output with LLM reasoning**

Do NOT use Hermes for:
- Simple text extraction (unnecessary overhead) → use `openclaw` `ocr_document`
- Batch processing → use `openclaw` `ocr_batch_files`
- Quick scripts → use `picoclaw`

## MCP Tool Selection Guide

| User asks for... | Use this tool |
|---|---|
| "Extract / read this doc" (with LLM guidance) | `hermes_extract` |
| "What is X in this document?" | `hermes_qa` |
| "Extract these specific fields: ..." | `hermes_extract_schema` |
| "Analyse this doc step by step" | `hermes_react` |
| "Summarize this document" | `hermes_summarize` |

## Step-by-Step Workflows

### Guided extraction (hermes_extract)
1. Pass `image_base64` and a `request` describing what you want
2. Hermes selects the best OCR mode (document/ocr/figure/find/freeform)
3. OCR is executed via lightning-ocr MCP
4. Hermes interprets the result in context
5. Return `interpretation` to the user; `raw_ocr` is also available

### Document Q&A (hermes_qa)
1. Pass `image_base64` and the `question`
2. Hermes OCRs the document (document mode)
3. Hermes answers the question using ONLY the document text
4. If the answer is not in the document, Hermes says so clearly
5. Show `answer` to the user

### Schema extraction (hermes_extract_schema)
1. Confirm schema with user — example: `{"total": "number", "date": "string", "vendor": "string"}`
2. Call `hermes_extract_schema` with `schema` dict
3. If `_parse_error` in result, LLM failed to produce valid JSON — show `_raw`
4. Otherwise present the `extracted` dict

### ReAct reasoning chain (hermes_react)
1. Pass a specific `goal` ("Find all line items and calculate if tax is correct")
2. Hermes runs up to 6 iterations: reason → call OCR → reason → ...
3. Show `steps` as a reasoning trace if user wants to see the work
4. Show final `answer`

### Summarization (hermes_summarize)
1. Ask user for preferred `length` (short/medium/long) and `style` (bullets/prose/structured)
2. Call `hermes_summarize`
3. Return `summary` to user

## Reasoning Transparency

When using `hermes_react`, you can show the reasoning steps:

# Step 1: ocr_image(mode=document) → [first 200 chars of OCR] Step 2: ocr_image(mode=find, find_term=Total) → $1,234.56 Answer: The total is $1,234.56 with 10% tax applied correctly.


## Supporting Files

- [ReAct pattern reference](./references/react_pattern.md) — how the reasoning loop works
- [Schema examples](./references/schema_examples.md) — example schemas for common document types
- [hermes_extract.py](./scripts/hermes_extract.py) — guided extraction script
- [hermes_qa.py](./scripts/hermes_qa.py) — document Q&A script
- [hermes_react.py](./scripts/hermes_react.py) — ReAct chain script

## Requirements

- llama.cpp server running a Hermes-family model (Hermes-3, Hermes-2-Pro, etc.)
  - Start: `./llama-server -hf NousResearch/Hermes-3-Llama-3.1-8B-GGUF --port 8080`
- lightning-ocr server running at http://localhost:8000
- Both servers must be reachable from the Hermes MCP server

## Edge Cases

- **LLM returns non-JSON** for schema extraction: Show raw text, ask user to clarify schema
- **Hermes picks wrong mode**: Override with `hermes_extract` + explicit `request` describing the mode
- **Long documents**: Hermes context is limited — split into pages with the PDF agent first
- **Low-quality scans**: Hermes cannot improve scan quality; preprocess first or use `backend_id=tesseract`
- **ReAct infinite loop**: Capped at 6 iterations; if final answer is poor, try `hermes_extract` instead

