# Hermes Schema Extraction Examples

## Invoice schema
```json
{
  "invoice_number": "string",
  "date":           "string",
  "due_date":       "string",
  "vendor_name":    "string",
  "client_name":    "string",
  "subtotal":       "number",
  "tax_rate":       "number",
  "tax_amount":     "number",
  "total":          "number",
  "currency":       "string",
  "payment_terms":  "string"
}

## Contract schema

{
  "parties":          ["string"],
  "effective_date":   "string",
  "expiry_date":      "string",
  "jurisdiction":     "string",
  "governing_law":    "string",
  "term_months":      "number",
  "key_obligations":  ["string"]
}

## Medical form schema

{
  "patient_name":   "string",
  "date_of_birth":  "string",
  "doctor_name":    "string",
  "visit_date":     "string",
  "diagnoses":      ["string"],
  "medications":    ["string"],
  "follow_up_date": "string"
}


## Receipt schema

{
  "merchant":   "string",
  "date":       "string",
  "items":      [{"name":"string","price":"number"}],
  "subtotal":   "number",
  "tax":        "number",
  "total":      "number",
  "payment_method": "string"
}

