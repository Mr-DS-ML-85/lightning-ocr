# NemoClaw Pipeline Reference

## invoice pipeline (3 steps)

### Step 1: markdown_extraction
- Tool: `ocr_image` mode=document
- Output: Full invoice as Markdown
- Required: YES — pipeline fails if this step fails

### Step 2: find_total
- Tool: `ocr_image` mode=find, find_term=Total
- Output: Raw text around "Total" + extracted total amount (post-processed)
- Required: NO

### Step 3: structured_fields
- Tool: `ocr_image` mode=freeform
- Prompt: Extract invoice_number, date, due_date, vendor_name, client_name, total, tax, currency as JSON
- Output: JSON string of extracted fields
- Required: NO

---

## contract pipeline (3 steps)

### Step 1: full_text
- Tool: `ocr_image` mode=document
- Required: YES

### Step 2: parties
- Tool: `ocr_image` mode=freeform
- Prompt: Extract parties, effective_date, jurisdiction, term_months, governing_law as JSON
- Required: NO

### Step 3: key_clauses
- Tool: `ocr_image` mode=freeform
- Prompt: List 5 most important clauses as JSON array of {title, summary}
- Required: NO

---

## medical pipeline (2 steps)

### Step 1: raw_ocr
- Tool: `ocr_image` mode=ocr
- Required: YES

### Step 2: patient_info
- Tool: `ocr_image` mode=freeform
- Prompt: Extract patient_name, dob, doctor_name, date, medications, diagnoses (REDACT SSN/insurance#)
- Required: NO

---

## general pipeline (2 steps)

### Step 1: document_markdown
- Tool: `ocr_image` mode=document
- Required: YES

### Step 2: visual_description
- Tool: `ocr_image` mode=describe
- Required: NO