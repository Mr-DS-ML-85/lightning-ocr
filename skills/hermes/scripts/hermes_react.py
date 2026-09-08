#!/usr/bin/env python3
"""
Hermes — Multi-step ReAct reasoning chain.

Usage:
  python hermes_react.py invoice.png "Check if the tax amount is correct"
  python hermes_react.py contract.png "List all payment obligations and their deadlines"
  python hermes_react.py form.png "Extract all dates and tell me which ones are in the past"
"""
import argparse
import base64
import json
import sys
from pathlib import Path

import httpx


def main():
    p = argparse.ArgumentParser(description="Hermes ReAct reasoning chain")
    p.add_argument("file")
    p.add_argument("goal")
    p.add_argument("--mcp-url",    default="http://localhost:8001/mcp")
    p.add_argument("--api-key",    default="")
    p.add_argument("--show-steps", action="store_true", help="Print reasoning steps")
    p.add_argument("--output",     default="")
    args = p.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr); sys.exit(1)

    b64  = base64.b64encode(path.read_bytes()).decode()
    hdrs = {"Content-Type": "application/json"}
    if args.api_key:
        hdrs["Authorization"] = f"Bearer {args.api_key}"

    print(f"Goal: {args.goal}", file=sys.stderr)

    with httpx.Client(timeout=600.0) as client:
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":1,"method":"initialize",
            "params":{"protocolVersion":"2025-03-26",
                      "clientInfo":{"name":"hermes-react","version":"1.0"},
                      "capabilities":{}},
        })
        client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","method":"notifications/initialized",
        })
        r = client.post(args.mcp_url, headers=hdrs, json={
            "jsonrpc":"2.0","id":2,"method":"tools/call",
            "params":{"name":"hermes_react","arguments":{
                "image_base64": b64,
                "filename":     path.name,
                "goal":         args.goal,
            }},
        })

    result = r.json().get("result", {})
    if result.get("isError"):
        err = " ".join(c.get("text","") for c in result.get("content",[]))
        print(f"Error: {err}", file=sys.stderr); sys.exit(1)

    text  = "\n".join(c.get("text","") for c in result.get("content",[])
                       if c.get("type")=="text")
    steps = result.get("steps", [])
    answer = result.get("answer","")

    if args.show_steps and steps:
        print(f"\n--- Reasoning ({len(steps)} steps) ---")
        for s in steps:
            print(f"\nStep {s.get('iteration','')}:")
            if s.get("thought"):
                print(f"  Thought: {s['thought'][:200]}")
            print(f"  Action: {s.get('action','')}")
            print(f"  Observation: {s.get('observation','')[:200]}")

    print(f"\n--- Answer ---")
    print(answer or text)

    if args.output:
        out = f"Goal: {args.goal}\n\nAnswer:\n{answer}\n"
        if steps:
            out += f"\nReasoning steps ({len(steps)}):\n"
            for s in steps:
                out += f"  Step {s.get('iteration','')}: {s.get('action','')}\n"
        Path(args.output).write_text(out)
        print(f"\nSaved to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()