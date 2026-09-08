#!/usr/bin/env bash
# ⚡ lightning-ocr MCP Connector — Universal installer
# Installs configs for all supported AI agents automatically.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_URL="http://localhost:8000/mcp"
STDIO_CMD="python"
STDIO_ARGS='["-m","connector.transport_stdio"]'

echo "⚡ lightning-ocr MCP Connector — Universal Installer"
echo "  Project root: $SCRIPT_DIR"
echo "  MCP HTTP URL: $MCP_URL"
echo ""

# ── Helper: merge into existing JSON config (never overwrite) ──────────────────
# Usage: install_config "Agent Name" "/path/to/config.json" '{"mcpServers":{...}}'
install_config() {
  local name="$1"
  local target="$2"
  local new_entry="$3"
  mkdir -p "$(dirname "$target")"

  if [[ -f "$target" ]]; then
    # File exists — merge lightning-ocr entry into it
    local merged
    merged=$(python3 -c "
import json, sys
try:
    with open('$target') as f:
        existing = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    existing = {}

new = json.loads('''$new_entry''')

# Merge at top level (handles mcpServers, mcp, servers, etc.)
for key, val in new.items():
    if isinstance(val, dict) and key in existing and isinstance(existing[key], dict):
        existing[key].update(val)
    else:
        existing[key] = val

print(json.dumps(existing, indent=2))
" 2>/dev/null)

    if [[ -n "$merged" ]]; then
      echo "$merged" > "$target"
      echo "  ✓ $name → $target (merged)"
    else
      echo "  ⚠ $name → $target (merge failed, leaving unchanged)"
    fi
  else
    # File doesn't exist — create it
    echo "$new_entry" | python3 -m json.tool > "$target" 2>/dev/null || echo "$new_entry" > "$target"
    echo "  ✓ $name → $target (created)"
  fi
}

# ── Claude Desktop ────────────────────────────────────────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
  CLAUDE_DESKTOP="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
elif [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "win32" ]]; then
  CLAUDE_DESKTOP="$APPDATA/Claude/claude_desktop_config.json"
else
  CLAUDE_DESKTOP="$HOME/.config/claude/claude_desktop_config.json"
fi

install_config "Claude Desktop" "$CLAUDE_DESKTOP" \
'{
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {
        "PYTHONPATH": "'"$SCRIPT_DIR"'",
        "TESSERACT_ENABLED": "true"
      }
    }
  }
}'

# ── Claude Code ───────────────────────────────────────────────────────────────
CLAUDE_CODE="$HOME/.claude/settings.json"
install_config "Claude Code (global)" "$CLAUDE_CODE" \
'{
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"}
    }
  }
}'

# ── Cursor ────────────────────────────────────────────────────────────────────
CURSOR_CONFIG="$HOME/.cursor/mcp.json"
install_config "Cursor" "$CURSOR_CONFIG" \
'{
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"}
    }
  }
}'

# ── Codex CLI ─────────────────────────────────────────────────────────────────
CODEX_CONFIG="$HOME/.codex/config.json"
install_config "Codex CLI" "$CODEX_CONFIG" \
'{
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"}
    }
  }
}'

# ── Continue ──────────────────────────────────────────────────────────────────
CONTINUE_DIR="$HOME/.continue/mcpServers"
mkdir -p "$CONTINUE_DIR"
install_config "Continue CLI/IDE" "$CONTINUE_DIR/lightning-ocr.json" \
'{
  "name": "lightning-ocr",
  "version": "1.0.0",
  "schema": "v1",
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"}
    }
  }
}'

# ── Kilo Code ─────────────────────────────────────────────────────────────────
KILO_CONFIG="$HOME/.config/kilo/mcp_settings.json"
install_config "Kilo Code" "$KILO_CONFIG" \
'{
  "mcpServers": {
    "lightning-ocr": {
      "command": "'"$STDIO_CMD"'",
      "args": ["-m","connector.transport_stdio"],
      "cwd": "'"$SCRIPT_DIR"'",
      "env": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"},
      "alwaysAllow": ["ocr_image","ocr_batch","list_ocr_backends","describe_capabilities"],
      "timeout": 120000
    }
  }
}'

# ── OpenCode ──────────────────────────────────────────────────────────────────
OPENCODE_CONFIG="$HOME/.config/opencode/config.json"
install_config "OpenCode" "$OPENCODE_CONFIG" \
'{
  "mcp": {
    "lightning-ocr": {
      "type": "local",
      "command": ["'"$STDIO_CMD"'","-m","connector.transport_stdio"],
      "environment": {"PYTHONPATH": "'"$SCRIPT_DIR"'", "TESSERACT_ENABLED": "true"},
      "enabled": true
    }
  }
}'

echo ""
echo "✅ All agent configs installed!"
echo ""
echo "📋 Remote HTTP/SSE configs (for Cline, Kilocode remote, ADK):"
echo "   Streamable HTTP: POST $MCP_URL"
echo "   SSE (legacy):    GET  ${MCP_URL/\/mcp/\/mcp\/sse}"
echo "   WebSocket:       WS   ws://localhost:8000/mcp/ws"
echo "   Discovery:       GET  http://localhost:8000/.well-known/mcp"
echo ""
echo "🚀 Start the server:"
echo "   docker compose up     (CPU mode)"
echo "   uvicorn app.main:app --port 8000"
echo ""
echo "🔄 Restart your AI agent app to pick up new configs."