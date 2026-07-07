"""Agent base — shared call layer for all datarubiks sub-agents.

Uses the `claude` CLI already running in the user's Claude Code session.
No separate API key, no model switching — shells out to `claude -p` which
inherits the active auth and model.

Default model: claude-sonnet-4-6 (the user's current session model).
Override via env: DATARUBIKS_AGENT_MODEL=claude-haiku-4-5-20251001

Fallback chain (first that works wins):
  1. claude CLI subprocess  — reuses Claude Code auth, zero setup
  2. anthropic SDK          — if ANTHROPIC_API_KEY is set
  3. None                   — pipeline continues deterministically

Token discipline:
  - Prompts are pre-trimmed by callers (≤ 300 tokens input)
  - max_tokens cap on SDK path: 80 output tokens
  - Disk cache keyed by sha1(agent_name + prompt) — re-runs cost 0 tokens
  - Cache TTL: 90 days
"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time

_MODEL = os.environ.get("DATARUBIKS_AGENT_MODEL", "claude-sonnet-4-6")
_MAX_OUT = 80
_CACHE_TTL = 90 * 86400
_CACHE_DIR = pathlib.Path(os.environ.get(
    "DATARUBIKS_AGENT_CACHE",
    pathlib.Path.home() / ".cache" / "datarubiks_agents",
))


def _cache_path(agent_name: str, prompt: str) -> pathlib.Path:
    key = hashlib.sha1(f"{agent_name}:{_MODEL}:{prompt}".encode()).hexdigest()
    return _CACHE_DIR / agent_name / f"{key}.json"


def _read_cache(path: pathlib.Path):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL:
            return data["result"]
    except Exception:
        pass
    return None


def _write_cache(path: pathlib.Path, result: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ts": time.time(), "result": result}))


def _via_claude_cli(prompt: str, system: str) -> str | None:
    """Call via `claude -p` subprocess — reuses the active Claude Code session."""
    cli = shutil.which("claude")
    if not cli:
        return None
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        result = subprocess.run(
            [cli, "-p", full_prompt, "--model", _MODEL, "--output-format", "text"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _via_sdk(prompt: str, system: str) -> str | None:
    """Fallback: direct Anthropic SDK call if ANTHROPIC_API_KEY is set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        kwargs = dict(model=_MODEL, max_tokens=_MAX_OUT,
                      messages=[{"role": "user", "content": prompt}])
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        return resp.content[0].text.strip()
    except Exception:
        return None


def call(agent_name: str, prompt: str, system: str = "") -> str | None:
    """Single agent call with cache + dual fallback.

    Returns the model's text response, or None if every path fails.
    None is always safe — callers treat it as "no suggestion, use default."
    """
    cache_path = _cache_path(agent_name, prompt)
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached

    result = _via_claude_cli(prompt, system) or _via_sdk(prompt, system)
    if result:
        _write_cache(cache_path, result)
    return result
