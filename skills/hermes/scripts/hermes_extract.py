#!/usr/bin/env python3
"""
Hermes — LLM-guided document extraction.

Usage:
  python hermes_extract.py invoice.png "Extract all financial fields"
  python hermes_extract.py scan.png "Read all text" --output result.md
  python hermes_extract.py doc.png "Find and list all dates mentioned"
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description="Hermes guided extraction")
    p.add_argument("file")
    p.add_argument("request", nargs="?", default="Extract all text from this document.")
    p.add_argument("--mcp-url", default="http://localhost:8001/mcp")
    p.add_argument("--api-key", default="")
    p.add_argument("--output",  default="")
    p.add_argument("--raw",     action="store_true", help="Show raw OCR instead of interpretation")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr); sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    with httpx.Client(timeout=360.0) as client:
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"hermes-extract","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })
        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"hermes_extract","arguments":{
                "image_base64": b64,
                "filename":     path.name,
                "request":      args.request,
            }},
        })

    result = r.json().get("result", {})
    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        print(f"Error: {err}", file=sys.stderr); sys.exit(1)

    # Extract structured meta from result
    text     = "\n".join(c.get("text","") for c in result.get("content",[])
                          if c.get("type")=="text")
    mode     = result.get("mode_used", "?")
    raw_ocr  = result.get("raw_ocr", "")

    print(f"Mode selected by Hermes: {mode}", file=sys.stderr)

    output = raw_ocr if args.raw else text
    if args.output:
        Path(args.output).write_text(output)
        print(f"Saved to: {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()