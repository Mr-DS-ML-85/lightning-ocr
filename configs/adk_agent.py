"""
⚡ Google ADK Agent using lightning-ocr MCP via McpToolset
Requires: pip install google-adk
Run:      adk web  OR  python configs/adk_agent.py
"""
from __future__ import annotations

import asyncio
import os

# Google ADK imports
try:
    from google.adk.agents import LlmAgent
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseServerParams
    ADK_AVAILABLE = True
except ImportError:
    ADK_AVAILABLE = False
    print("[adk_agent] google-adk not installed. pip install google-adk")


MCP_URL     = os.getenv("LIGHTNING_OCR_MCP_URL", "http://localhost:8000/mcp/sse")
MCP_API_KEY = os.getenv("LIGHTNING_OCR_API_KEY", "")


# ── ADK McpToolset connection ─────────────────────────────────────────────────
# ADK uses McpToolset with SseServerParams (SSE transport — legacy 2024-11-05).
# The lightning-ocr server keeps the /mcp/sse endpoint for full ADK compatibility.

def create_ocr_agent():
    """Create an ADK LlmAgent with lightning-ocr MCP tools."""
    if not ADK_AVAILABLE:
        raise RuntimeError("google-adk not installed")

    toolset_params = SseServerParams(
        url=MCP_URL,
        headers={"Authorization": f"Bearer {MCP_API_KEY}"} if MCP_API_KEY else {},
    )

    return LlmAgent(
        model       = "gemini-2.0-flash",
        name        = "lightning_ocr_agent",
        description = "OCR and document intelligence agent using lightning-ocr",
        instruction = """You are a document analysis assistant powered by lightning-ocr.
You can:
  - Extract text from images and PDFs (ocr_image tool)
  - Process multiple images at once (ocr_batch tool)
  - Find specific terms in documents (find mode)
  - Convert documents to Markdown (document mode)
  - Analyse charts and figures (figure mode)
  - Retrieve past OCR results (list_jobs, get_job tools)

Always use list_ocr_backends first to check which backends are available.
If one backend fails, auto_fallback=true ensures Tesseract is tried automatically.
""",
        tools = [
            McpToolset(
                connection_params=toolset_params,
                tool_filter=[
                    "ocr_image",
                    "ocr_batch",
                    "list_ocr_backends",
                    "get_job",
                    "list_jobs",
                    "describe_capabilities",
                ],
            )
        ],
    )


# ── NemoClaw ADK integration (alternative — direct HTTP) ─────────────────────

async def nemoclaw_adk_demo() -> None:
    """
    Demo: NemoClaw ADK connector (uses Streamable HTTP, no SSE needed).
    Works with any ADK-compatible framework via direct MCP calls.
    """
    from agents.nemoclaw.skills import NemoClawADKConnector

    connector = NemoClawADKConnector(
        mcp_url = "http://localhost:8000/mcp",
        api_key = MCP_API_KEY,
    )

    print("Available MCP tools for ADK:")
    tools = await connector.get_tools_async()
    for t in tools:
        print(f"  • {t['name']}: {t['description'][:70]}")

    print("\nTest ocr call via NemoClaw ADK connector:")
    # In real ADK usage: tools=[connector] in LlmAgent
    # Direct call demo:
    import base64
    from pathlib import Path
    test_png = Path("tests/fixtures/sample.png")
    if test_png.exists():
        b64 = base64.b64encode(test_png.read_bytes()).decode()
        text = await connector.call_tool_async("ocr_image", {
            "image_base64": b64,
            "mode": "ocr",
            "auto_fallback": True,
        })
        print(f"  OCR result: {text[:200]}")


if __name__ == "__main__":
    if ADK_AVAILABLE:
        agent = create_ocr_agent()
        print(f"ADK Agent created: {agent.name}")
        print(f"Tools: {[t.__class__.__name__ for t in agent.tools]}")
    asyncio.run(nemoclaw_adk_demo())