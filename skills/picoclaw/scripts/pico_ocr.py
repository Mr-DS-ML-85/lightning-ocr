#!/usr/bin/env python3
"""
PicoClaw — Minimal OCR. One script, all features.
stdout = result text (pipe-safe)
stderr = status messages

Usage:
  pico_ocr.py image.png
  pico_ocr.py invoice.png --mode document
  pico_ocr.py scan.png --find "Total"
  pico_ocr.py form.png --json --schema '{"name":"string","total":"number"}'
  pico_ocr.py img1.png img2.png --batch
  pico_ocr.py photo.jpg --describe
  pico_ocr.py --backends
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path

import httpx

MCP_URL = os.getenv("LIGHTNING_OCR_MCP_URL", "http://localhost:8000/mcp")
API_KEY = os.getenv("LIGHTNING_OCR_API_KEY", "")
_ID     = [0]


def _hdrs():
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _nid():
    _ID[0] += 1
    return _ID[0]


def _call(tool, arguments, client):
    r = client.post(MCP_URL, headers=_hdrs(), json={
        "jsonrpc": "2.0", "id": _nid(), "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    })
    r.raise_for_status()
    data   = r.json()
    result = data.get("result", {})
    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        raise RuntimeError(f"Tool error: {err}")
    return result


def _text(result):
    return "\n".join(c.get("text","") for c in result.get("content",[])
                     if c.get("type")=="text")


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


def _init(client):
    client.post(MCP_URL, headers=_hdrs(), json={
        "jsonrpc":"2.0","id":_nid(),"method":"initialize",
        "params":{"protocolVersion":"2025-03-26",
                  "clientInfo":{"name":"picoclaw","version":"1.0"},
                  "capabilities":{}},
    })
    client.post(MCP_URL, headers=_hdrs(), json={
        "jsonrpc":"2.0","method":"notifications/initialized",
    })


def main():
    parser = argparse.ArgumentParser(prog="pico_ocr", add_help=True)
    parser.add_argument("files",    nargs="*")
    parser.add_argument("--mode",   default="ocr",
                        choices=["ocr","document","free","describe"])
    parser.add_argument("--find",   default="", metavar="TERM")
    parser.add_argument("--json",   action="store_true", dest="json_mode")
    parser.add_argument("--schema", default="")
    parser.add_argument("--batch",  action="store_true")
    parser.add_argument("--describe",action="store_true")
    parser.add_argument("--backends",action="store_true")
    parser.add_argument("--backend-id", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    try:
        with httpx.Client(timeout=240.0) as client:
            _init(client)

            # Backends check
            if args.backends:
                result = _call("list_ocr_backends", {}, client)
                backends = result.get("backends", [])
                for b in backends:
                    status = "✓" if b.get("status")=="ok" else "✗"
                    print(f"  {status} [{b.get('id')}] {b.get('label')} "
                          f"({b.get('kind')}) status={b.get('status')}")
                return

            if not args.files:
                parser.print_help(sys.stderr)
                sys.exit(1)

            # Batch mode
            if args.batch or len(args.files) > 1:
                images = []
                for fp in args.files:
                    try:
                        images.append({
                            "image_base64": _b64(fp),
                            "filename": Path(fp).name,
                            "mode": args.mode,
                        })
                    except Exception as exc:
                        print(f"  Skip {fp}: {exc}", file=sys.stderr)

                result  = _call("ocr_batch", {
                    "images": images, "backend_id": args.backend_id,
                    "auto_fallback": True,
                }, client)
                results = result.get("results", [])
                all_text = []
                for r in results:
                    if r.get("error"):
                        print(f"  [FAIL] {r['filename']}: {r['error']}", file=sys.stderr)
                    else:
                        all_text.append(r.get("text",""))
                output = "\n\n---\n\n".join(all_text)

                if args.json_mode:
                    out_str = json.dumps(results, indent=2)
                    if args.output:
                        Path(args.output).write_text(out_str); return
                    print(out_str); return

                if args.output:
                    Path(args.output).write_text(output)
                    print(f"Saved to: {args.output}", file=sys.stderr)
                else:
                    print(output)
                return

            # Single file
            fp    = args.files[0]
            b64   = _b64(fp)
            fname = Path(fp).name

            # Describe
            if args.describe:
                result = _call("ocr_image", {
                    "image_base64": b64, "filename": fname,
                    "mode": "describe", "auto_fallback": True,
                }, client)
                print(_text(result)); return

            # Find term
            if args.find:
                result = _call("ocr_image", {
                    "image_base64": b64, "filename": fname,
                    "mode": "find", "find_term": args.find, "auto_fallback": True,
                }, client)
                print(_text(result)); return

            # JSON extraction
            if args.json_mode:
                schema = {}
                if args.schema:
                    try:
                        schema = json.loads(args.schema)
                    except json.JSONDecodeError:
                        print("Warning: invalid schema JSON", file=sys.stderr)
                prompt = (
                    f"Extract data matching this schema: {json.dumps(schema)}. " if schema
                    else "Extract all key-value pairs. "
                ) + "Return ONLY valid JSON."
                result = _call("ocr_image", {
                    "image_base64": b64, "filename": fname,
                    "mode": "freeform", "custom_prompt": prompt, "auto_fallback": True,
                }, client)
                import re
                text = _text(result)
                m    = re.search(r"(\{.*\})", text, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        out_str = json.dumps(data, indent=2, ensure_ascii=False)
                        if args.output:
                            Path(args.output).write_text(out_str)
                        else:
                            print(out_str)
                        return
                    except json.JSONDecodeError:
                        pass
                # Fallback: raw
                if args.output:
                    Path(args.output).write_text(text)
                else:
                    print(text)
                return

            # Default: plain OCR
            result = _call("ocr_image", {
                "image_base64": b64, "filename": fname,
                "mode": args.mode, "backend_id": args.backend_id,
                "auto_fallback": True,
            }, client)
            text = _text(result)
            meta = result.get("meta", {})
            print(f"  {meta.get('backend','?')} {meta.get('duration_ms',0)}ms",
                  file=sys.stderr)
            if args.output:
                Path(args.output).write_text(text)
                print(f"Saved to: {args.output}", file=sys.stderr)
            else:
                print(text)

    except httpx.ConnectError:
        print(f"Error: cannot connect to {MCP_URL}. Is lightning-ocr running?",
              file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()