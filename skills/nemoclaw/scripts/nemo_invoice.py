#!/usr/bin/env python3
"""
NemoClaw — Invoice processing pipeline.

Usage:
  python nemo_invoice.py invoice.png
  python nemo_invoice.py receipt.jpg --output result.json
  python nemo_invoice.py bill.png --verbose
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def _init_and_call(client, mcp_url, api_key, tool, arguments, call_id):
    hdrs = {"Content-Type": "application/json"}
    if api_key:
        hdrs["Authorization"] = f"Bearer {api_key}"
    r = client.post(mcp_url, headers=hdrs, json={
        "jsonrpc":"2.0","id":call_id,"method":"tools/call",
        "params":{"name":tool,"arguments":arguments},
    })
    r.raise_for_status()
    return r.json().get("result",{})


def main():
    p = argparse.ArgumentParser(description="NemoClaw invoice pipeline")
    p.add_argument("file")
    p.add_argument("--mcp-url",  default="http://localhost:8000/mcp")
    p.add_argument("--api-key",  default="")
    p.add_argument("--output",   default="")
    p.add_argument("--verbose",  action="store_true")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr); sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    print(f"Running invoice pipeline on {path.name}...", file=sys.stderr)

    with httpx.Client(timeout=360.0) as client:
        # Handshake
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"nemoclaw-invoice","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })

        # Run pipeline
        result = _init_and_call(client, args.mcp_url, args.api_key,
                                 "nemo_run_pipeline", {
                                     "image_base64": b64,
                                     "filename":     path.name,
                                     "pipeline":     "invoice",
                                 }, 2)

    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        print(f"Pipeline error: {err}", file=sys.stderr); sys.exit(1)

    text    = "\n".join(c.get("text","") for c in result.get("content",[])
                         if c.get("type")=="text")
    success = result.get("success", True)
    errors  = result.get("errors", [])
    context = result.get("context", {})

    if args.verbose:
        print("\n--- Pipeline output ---")
        print(text)
        if errors:
            print(f"\n--- Errors ({len(errors)}) ---")
            for e in errors:
                print(f"  {e}")
    else:
        # Compact output
        print(f"Status: {'✓ success' if success else '✗ failed'}")
        if errors:
            print(f"Errors: {errors}")
        for k, v in context.items():
            val = str(v)[:80] if isinstance(v, str) else str(v)
            print(f"  {k:<25} {val}")

    if args.output:
        output_data = {
            "file":    path.name,
            "success": success,
            "errors":  errors,
            "context": context,
        }
        Path(args.output).write_text(
            json.dumps(output_data, indent=2, ensure_ascii=False)
        )
        print(f"\nSaved to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()