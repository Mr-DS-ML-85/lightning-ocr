# OpenClaw OCR Modes — Detailed Reference

## document
Converts the image to clean, structured Markdown.
- Preserves headings, paragraphs, bullet lists, numbered lists, and tables
- Best for: invoices, reports, contracts, forms, structured PDFs
- Output example: `# Invoice\n**Total:** $1,234.56\n\n| Item | Price |`

## ocr
General text extraction. Returns plain text, no Markdown formatting.
- Best for: scanned pages, photocopies, mixed content
- Faster and more reliable than `document` mode for simple text reads
- Output example: `Invoice 12345\nTotal 1234.56\nDate 2026-01-01`

## free
Raw text only. No post-processing, no formatting, no structure.
- Best for: quick single-field reads, OCR debugging
- Lowest latency mode

## figure
Extracts and explains labels, axes, legends, and data from charts/figures.
- Best for: bar charts, line graphs, scatter plots, diagrams, data tables
- Returns both extracted labels AND interpretation of the figure
- Output example: `X-axis: Q1-Q4 2025\nY-axis: Revenue ($M)\nBar 1: Q1=$2.1M`

## describe
Full visual description of the image.
- Best for: photos, mixed images, when you need to understand what's visible
- Does NOT focus on text extraction — focuses on visual content
- Output example: `The image shows a white A4 document with a company logo...`

## find
Locates text near a specific search term.
- Best for: finding "Total", "Date", "Name", "Reference Number" in a document
- Returns the text context surrounding the found term
- Requires `find_term` parameter

## freeform
Uses a custom prompt for extraction.
- Best for: specialised extraction that doesn't fit other modes
- Requires `custom_prompt` parameter
- Most flexible but requires clear prompt writing