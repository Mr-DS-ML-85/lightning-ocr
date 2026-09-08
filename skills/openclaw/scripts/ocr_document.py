
## agents/openclaw/scripts/ocr_document.py

#!/usr/bin/env python3
"""
OpenClaw — Extract text from a document image via lightning-ocr MCP.

Usage:
  python ocr_document.py image.png
  python ocr_document.py invoice.png --mode ocr
  python ocr_document.py scan.png --mode document --output result.md
  python ocr_document.py form.png --backend-id tesseract
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description="OpenClaw document OCR")
    parser.add_argument("file",         help="Image or PDF path")
    parser.add_argument("--mode",       default="document",
                        choices=["document","ocr","free","figure","describe","freeform"])
    parser.add_argument("--backend-id", default="")
    parser.add_argument("--mcp-url",    default="http://localhost:8000/mcp")
    parser.add_argument("--api-key",    default="")
    parser.add_argument("--output",     default="")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    # MCP handshake
    with httpx.Client(timeout=240.0) as client:
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"openclaw-script","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })

        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"ocr_image","arguments":{
                "image_base64": b64,
                "filename":     path.name,
                "mode":         args.mode,
                "backend_id":   args.backend_id,
                "auto_fallback": True,
            }},
        })

    data   = r.json()
    result = data.get("result", {})

    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        print(f"OCR Error: {err}", file=sys.stderr)
        sys.exit(1)

    text = "\n".join(
        c.get("text","") for c in result.get("content",[]) if c.get("type")=="text"
    )
    meta = result.get("meta", {})
    print(f"# Backend: {meta.get('backend','?')} | Mode: {meta.get('mode','?')} | {meta.get('duration_ms',0)}ms",
          file=sys.stderr)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Saved to: {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()