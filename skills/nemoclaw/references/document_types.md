# NemoClaw Document Type Classification Rules

The `nemo_classify` tool runs plain OCR then matches keyword patterns.

## Classification rules (in priority order)

| Type | Trigger keywords |
|---|---|
| invoice | invoice, bill, receipt, payment due, amount due |
| contract | agreement, contract, terms and conditions, clause, hereby |
| medical | patient, diagnosis, prescription, physician, medication |
| report | report, analysis, summary, findings, conclusion |
| letter | dear, sincerely, regards, subject: |
| form | form, checkbox, signature, fill in, please complete |
| academic | abstract, methodology, references, hypothesis |
| general | (default — no strong keyword matches) |

## Confidence levels

- **high**: 3+ keyword matches for the winning type
- **medium**: 1-2 matches
- **low**: 0 matches — document type ambiguous

## When confidence is low

Try a different approach:
1. Use `nemo_run_pipeline pipeline=general` as a safe default
2. Look at the `scores` dict to see if two types scored similarly
3. Use `openclaw` `ocr_document` to read the raw text and classify manually