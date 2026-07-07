#!/usr/bin/env bash
# datarubiks install — run once after unzipping
# Works on macOS and Linux. Requires python3.10+ and the claude CLI.
set -e

ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$ENGINE_DIR/.venv"
SKILL_SRC="$ENGINE_DIR/.claude/skills/datarubiks-panel"
SKILL_DST="$HOME/.claude/skills/datarubiks-panel"
CLAUDE_BIN="$(which claude 2>/dev/null || true)"

echo "=== datarubiks install ==="
echo "Engine: $ENGINE_DIR"

# ── 1. Python venv ────────────────────────────────────────────────────────────
echo ""
echo "→ Creating Python venv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$ENGINE_DIR/requirements.txt" -q
# MCP server dependency
"$VENV/bin/pip" install mcp anthropic -q
echo "  ✓ venv ready at $VENV"

# ── 2. Patch SKILL.md with real engine path ───────────────────────────────────
echo ""
echo "→ Configuring skill..."
mkdir -p "$SKILL_DST"
sed "s|__ENGINE_DIR__|$ENGINE_DIR|g" "$SKILL_SRC/SKILL.md" > "$SKILL_DST/SKILL.md"
echo "  ✓ skill installed → $SKILL_DST"

# ── 3. Register MCP server with Claude Code ───────────────────────────────────
echo ""
echo "→ Registering MCP server..."
if [ -n "$CLAUDE_BIN" ]; then
    "$CLAUDE_BIN" mcp add datarubiks-panel \
        "$VENV/bin/python3" "$ENGINE_DIR/datarubiks_mcp.py" \
        --scope user 2>/dev/null && echo "  ✓ MCP registered via claude CLI" \
        || echo "  ⚠ claude mcp add failed — falling back to manual config"
else
    echo "  ⚠ claude CLI not found — adding to ~/.claude/settings.json manually"
fi

# Manual fallback: patch ~/.claude/settings.json
SETTINGS="$HOME/.claude/settings.json"
MCP_ENTRY=$(cat <<JSON
{
  "datarubiks-panel": {
    "command": "$VENV/bin/python3",
    "args": ["$ENGINE_DIR/datarubiks_mcp.py"]
  }
}
JSON
)
if [ -f "$SETTINGS" ]; then
    python3 - <<PYEOF
import json, pathlib, sys
p = pathlib.Path("$SETTINGS")
cfg = json.loads(p.read_text())
cfg.setdefault("mcpServers", {})["datarubiks-panel"] = {
    "command": "$VENV/bin/python3",
    "args": ["$ENGINE_DIR/datarubiks_mcp.py"]
}
p.write_text(json.dumps(cfg, indent=2))
print("  ✓ MCP entry written to", str(p))
PYEOF
else
    mkdir -p "$(dirname $SETTINGS)"
    python3 -c "
import json, pathlib
p = pathlib.Path('$SETTINGS')
p.write_text(json.dumps({'mcpServers': {'datarubiks-panel': {'command': '$VENV/bin/python3', 'args': ['$ENGINE_DIR/datarubiks_mcp.py']}}}, indent=2))
print('  ✓ Created', str(p))
"
fi

# ── 4. Done ───────────────────────────────────────────────────────────────────
echo ""
echo "=== ✓ datarubiks ready ==="
echo ""
echo "Restart Claude Code, then use:"
echo "  /datarubiks-panel   (skill)"
echo "  or the MCP tools:   extract_folder / assemble_panels"
echo ""
echo "Optional — use Sonnet sub-agents for ambiguous tables:"
echo "  export DATARUBIKS_AGENT_MODEL=claude-sonnet-4-6"
