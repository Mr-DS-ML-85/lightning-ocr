#!/usr/bin/env python3
"""
NemoClaw — Contract analysis pipeline.

Usage:
  python nemo_contract.py contract.png
  python nemo_contract.py agreement.pdf --output analysis.json --verbose
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description="NemoClaw contract pipeline")
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

    print(f"Running contract pipeline on {path.name}...", file=sys.stderr)

    with httpx.Client(timeout=400.0) as client:
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"nemoclaw-contract","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })
        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"nemo_run_pipeline","arguments":{
                "image_base64": b64,
                "filename":     path.name,
                "pipeline":     "contract",
            }},
        })

    result = r.json().get("result",{})
    text   = "\n".join(c.get("text","") for c in result.get("content",[])
                        if c.get("type")=="text")
    context = result.get("context", {})
    errors  = result.get("errors", [])

    if args.verbose:
        print(text)
    else:
        print(f"Contract: {path.name}")
        print(f"Errors: {errors if errors else 'none'}")
        for k, v in context.items():
            print(f"  {k:<25} {str(v)[:100]}")

    if args.output:
        Path(args.output).write_text(
            json.dumps({"file": path.name, "context": context, "errors": errors},
                       indent=2, ensure_ascii=False)
        )
        print(f"Saved to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()