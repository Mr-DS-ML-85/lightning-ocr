#!/usr/bin/env python3
"""
Hermes — Q&A over a document.

Usage:
  python hermes_qa.py invoice.png "What is the total amount?"
  python hermes_qa.py contract.png "Who are the contracting parties?"
  python hermes_qa.py form.png "What is the submission deadline?"
"""
import argparse
import base64
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description="Hermes document Q&A")
    p.add_argument("file")
    p.add_argument("question")
    p.add_argument("--mcp-url", default="http://localhost:8001/mcp")
    p.add_argument("--api-key", default="")
    p.add_argument("--show-doc", action="store_true", help="Also print the full OCR text")
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
                      "clientInfo":{"name":"hermes-qa","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })
        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"hermes_qa","arguments":{
                "image_base64": b64,
                "filename":     path.name,
                "question":     args.question,
            }},
        })

    result   = r.json().get("result", {})
    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        print(f"Error: {err}", file=sys.stderr); sys.exit(1)

    answer   = "\n".join(c.get("text","") for c in result.get("content",[])
                          if c.get("type")=="text")
    question = result.get("question","")

    print(f"Q: {question}")
    print(f"A: {answer}")

    if args.show_doc:
        print("\n--- Document text ---")
        print(result.get("doc_text",""))


if __name__ == "__main__":
    main()