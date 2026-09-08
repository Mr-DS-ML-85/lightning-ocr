#!/usr/bin/env python3
"""
NemoClaw — Auto-classify document type then run best pipeline.

Usage:
  python nemo_auto.py unknown_doc.png
  python nemo_auto.py scan.jpg --output result.json --verbose
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description="NemoClaw auto pipeline")
    p.add_argument("file")
    p.add_argument("--mcp-url", default="http://localhost:8000/mcp")
    p.add_argument("--api-key", default="")
    p.add_argument("--output",  default="")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr); sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    print(f"Auto-processing {path.name}...", file=sys.stderr)

    with httpx.Client(timeout=400.0) as client:
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"nemoclaw-auto","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })
        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"nemo_auto","arguments":{
                "image_base64": b64,
                "filename":     path.name,
            }},
        })

    result  = r.json().get("result",{})
    text    = "\n".join(c.get("text","") for c in result.get("content",[])
                         if c.get("type")=="text")
    context = result.get("context", {})
    errors  = result.get("errors", [])

    if args.verbose:
        print(text)
    else:
        # First line of text usually contains classification
        first_line = text.split("\n")[0] if text else ""
        print(first_line)
        for k, v in list(context.items())[:6]:
            print(f"  {k:<25} {str(v)[:100]}")
        if errors:
            print(f"Errors: {errors}")

    if args.output:
        Path(args.output).write_text(
            json.dumps({"file": path.name, "context": context, "errors": errors},
                       indent=2, ensure_ascii=False)
        )
        print(f"Saved to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()