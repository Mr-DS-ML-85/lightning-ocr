#!/usr/bin/env python3
"""
OpenClaw — Parse an invoice image and extract structured fields.

Usage:
  python ocr_invoice.py invoice.png
  python ocr_invoice.py receipt.jpg --output fields.json
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description="OpenClaw invoice parser")
    parser.add_argument("file")
    parser.add_argument("--mcp-url",  default="http://localhost:8000/mcp")
    parser.add_argument("--api-key",  default="")
    parser.add_argument("--output",   default="")
    parser.add_argument("--json-only",action="store_true")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    with httpx.Client(timeout=300.0) as client:
        # Initialize
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"openclaw-invoice","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })

        # Pass 1: freeform JSON extraction
        r1 = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"ocr_image","arguments":{
                "image_base64":  b64,
                "filename":      path.name,
                "mode":          "freeform",
                "custom_prompt": (
                    "Extract all invoice fields as a JSON object with keys: "
                    "invoice_number, date, due_date, vendor_name, client_name, "
                    "subtotal, tax_rate, tax_amount, total, currency, payment_terms, "
                    "line_items (array of {description, qty, unit_price, amount}). "
                    "Omit keys not present. Return ONLY valid JSON."
                ),
                "auto_fallback": True,
            }},
        })

        # Pass 2: plain OCR for raw text fallback
        r2 = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":3,"method":"tools/call",
            "params":{"name":"ocr_image","arguments":{
                "image_base64": b64, "filename": path.name,
                "mode": "ocr", "auto_fallback": True,
            }},
        })

    # Parse JSON from freeform result
    import re
    json_text = "\n".join(
        c.get("text","") for c in r1.json().get("result",{}).get("content",[])
        if c.get("type")=="text"
    )
    raw_text  = "\n".join(
        c.get("text","") for c in r2.json().get("result",{}).get("content",[])
        if c.get("type")=="text"
    )

    fields = {}
    m = re.search(r"(\{.*\})", json_text, re.DOTALL)
    if m:
        try:
            fields = json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Regex fallbacks for common fields
    fallbacks = {
        "invoice_number": r"(?:Invoice|Inv)[.\s#:]*([A-Z0-9\-]+)",
        "date":           r"Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        "total":          r"(?:Grand\s+)?Total[:\s$€£]*\s*([\d,]+(?:\.\d{2})?)",
    }
    for key, pattern in fallbacks.items():
        if not fields.get(key):
            fm = re.search(pattern, raw_text, re.IGNORECASE)
            if fm:
                fields[key] = fm.group(1).strip()

    line_items = fields.pop("line_items", [])
    mandatory  = ["invoice_number","date","total","vendor_name","client_name"]
    missing    = [k for k in mandatory if not fields.get(k)]
    found      = sum(1 for k in ["invoice_number","date","due_date","vendor_name",
                                   "client_name","subtotal","tax_amount","total"]
                     if fields.get(k))
    confidence = "high" if found >= 6 else ("medium" if found >= 3 else "low")

    output = {
        "file":       path.name,
        "fields":     fields,
        "line_items": line_items if isinstance(line_items, list) else [],
        "confidence": confidence,
        "missing":    missing,
    }

    out_str = json.dumps(output, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(out_str)
        print(f"Saved to: {args.output}", file=sys.stderr)

    if args.json_only:
        print(out_str)
    else:
        print(f"\n{'='*50}")
        print(f"Invoice: {path.name}")
        print(f"Confidence: {confidence} | Missing: {missing}")
        print(f"{'='*50}")
        for k, v in fields.items():
            print(f"  {k:<20} {v}")
        if line_items:
            print(f"\nLine items ({len(line_items)}):")
            for item in line_items:
                desc  = item.get("description","?")
                price = item.get("amount","?")
                print(f"  {desc:<40} {price}")


if __name__ == "__main__":
    main()