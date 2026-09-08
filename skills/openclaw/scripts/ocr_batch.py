#!/usr/bin/env python3
"""
OpenClaw — Batch OCR multiple images via lightning-ocr MCP.

Usage:
  python ocr_batch.py *.png
  python ocr_batch.py images/ --glob "*.jpg" --mode ocr
  python ocr_batch.py img1.png img2.png img3.png --output results.json
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description="OpenClaw batch OCR")
    parser.add_argument("files",  nargs="+", help="Image paths or directory")
    parser.add_argument("--mode", default="ocr",
                        choices=["document","ocr","free","describe"])
    parser.add_argument("--glob",       default="*.png")
    parser.add_argument("--backend-id", default="")
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--mcp-url",    default="http://localhost:8000/mcp")
    parser.add_argument("--api-key",    default="")
    parser.add_argument("--output",     default="")
    args = parser.parse_args()

    # Expand directories
    file_paths = []
    for f in args.files:
        p = Path(f)
        if p.is_dir():
            file_paths.extend(sorted(p.glob(args.glob)))
        elif p.exists():
            file_paths.append(p)
        else:
            print(f"Warning: not found: {f}", file=sys.stderr)

    if not file_paths:
        print("Error: no valid files found.", file=sys.stderr)
        sys.exit(1)

    print(f"Processing {len(file_paths)} files...", file=sys.stderr)

    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    all_results = []

    with httpx.Client(timeout=600.0) as client:
        # Handshake once
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"openclaw-batch","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })

        _id = 10
        for i in range(0, len(file_paths), args.chunk_size):
            chunk = file_paths[i: i + args.chunk_size]
            images = []
            skipped = []
            for fp in chunk:
                try:
                    images.append({
                        "image_base64": base64.b64encode(fp.read_bytes()).decode(),
                        "filename": fp.name,
                        "mode": args.mode,
                    })
                except Exception as exc:
                    skipped.append({"filename": fp.name, "text":"", "error": str(exc)})

            if images:
                _id += 1
                r = client.post(args.mcp_url, headers=hdrs, json={
                    "jsonrpc":"2.0","id":_id,"method":"tools/call",
                    "params":{"name":"ocr_batch","arguments":{
                        "images":       images,
                        "backend_id":   args.backend_id,
                        "auto_fallback": True,
                    }},
                })
                result = r.json().get("result",{})
                batch_results = result.get("results", [])
                all_results.extend(batch_results)

                ok  = sum(1 for r in batch_results if not r.get("error"))
                err = len(batch_results) - ok
                print(f"  Chunk {i//args.chunk_size+1}: {ok} ok, {err} errors",
                      file=sys.stderr)
            all_results.extend(skipped)

    out_str = json.dumps(all_results, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(out_str)
        print(f"Saved to: {args.output}", file=sys.stderr)
    else:
        for r in all_results:
            fname = r.get("filename","?")
            text  = r.get("text","")
            err   = r.get("error","")
            if err:
                print(f"[FAIL] {fname}: {err}")
            else:
                print(f"[OK]   {fname}: {text[:80]}{'...' if len(text)>80 else ''}")

    ok_count = sum(1 for r in all_results if not r.get("error"))
    print(f"\nTotal: {ok_count}/{len(all_results)} succeeded.", file=sys.stderr)


if __name__ == "__main__":
    main()