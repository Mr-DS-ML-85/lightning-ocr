
# PicoClaw Quick Reference

## One-liners

```bash
# OCR any image
python agents/picoclaw/scripts/pico_ocr.py image.png

# Get Markdown
python agents/picoclaw/scripts/pico_ocr.py doc.pdf --mode document

# Find Total in invoice
python agents/picoclaw/scripts/pico_ocr.py invoice.png --find "Total"

# Extract JSON with schema
python agents/picoclaw/scripts/pico_ocr.py form.png --json \
  --schema '{"name":"string","date":"string","total":"number"}'

# Batch all PNGs in current dir
python agents/picoclaw/scripts/pico_ocr.py *.png --batch --output results.json

# Describe an image
python agents/picoclaw/scripts/pico_ocr.py photo.jpg --describe

# Check server health
python agents/picoclaw/scripts/pico_ocr.py --backends

### 🌍 **MCP URL Override**

You can point **PicoClaw** to a remote server by setting the `LIGHTNING_OCR_MCP_URL` environment variable:

```bash
LIGHTNING_OCR_MCP_URL=http://myserver:8000/mcp \
python agents/picoclaw/scripts/pico_ocr.py scan.png

```

---

### 🛑 **Exit Codes**

| Code | Meaning |
| --- | --- |
| **0** | **Success** — OCR completed and output sent to stdout. |
| **1** | **OCR Error** — Failed to process or file not found. |
| **2** | **Connection Error** — Could not reach the lightning-ocr server. |

---

### 🔗 **Pipe Examples**

PicoClaw is built for the shell. Use standard Unix pipes to process text in real-time:

```bash
# OCR + Filter (Search for "total" in a receipt)
python agents/picoclaw/scripts/pico_ocr.py receipt.png | grep -i total

# OCR + Analytics (Count total words in a document)
python agents/picoclaw/scripts/pico_ocr.py page.png | wc -w

# OCR + JSON Parsing (Extract specific fields with jq)
python agents/picoclaw/scripts/pico_ocr.py form.png --json | jq .total

```