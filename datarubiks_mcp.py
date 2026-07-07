"""MCP server for Data Rubiks batch panel builder.

Exposes the two-stage pipeline as MCP tools so Claude desktop app
can run extraction and assembly directly without manual Terminal commands.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

app = Server("datarubiks")

REPO = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(REPO, ".venv", "bin", "python3")


def _run(cmd):
    import subprocess
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    out = r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")
    return out.strip(), r.returncode


@app.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="extract_folder",
            description="Stage 1: Extract all PDFs in a folder and propose panel groups. Returns terminal output + path to groups.json.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Absolute path to folder containing PDFs"},
                    "workdir": {"type": "string", "description": "Absolute path to output/scratch directory"},
                    "workers": {"type": "integer", "default": 4, "description": "Parallel workers"},
                    "max_pages": {"type": "integer", "description": "Cap pages per PDF (optional, for quick test)"},
                },
                "required": ["folder", "workdir"],
            },
        ),
        types.Tool(
            name="assemble_panels",
            description="Stage 2: Assemble confirmed groups into master.xlsx + schema_changelog.md. Run after user confirms groups.",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Same folder path used in stage 1"},
                    "workdir": {"type": "string", "description": "Same workdir used in stage 1"},
                    "groups_path": {"type": "string", "description": "Absolute path to confirmed groups.json"},
                },
                "required": ["folder", "workdir", "groups_path"],
            },
        ),
        types.Tool(
            name="read_file",
            description="Read a file produced by the engine (groups.json, schema_changelog.md, manifest.json).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to the file"},
                },
                "required": ["path"],
            },
        ),
        types.Tool(
            name="write_groups",
            description="Write an edited groups.json to disk (use after user approves/edits groups).",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to write (e.g. workdir/confirmed_groups.json)"},
                    "groups": {"type": "array", "description": "The groups list (edited or as-is from extract_folder output)"},
                },
                "required": ["path", "groups"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "extract_folder":
        folder = arguments["folder"]
        workdir = arguments["workdir"]
        workers = arguments.get("workers", 4)
        cmd = [PYTHON, "backend/tools/batch_panel.py", folder, workdir, "--workers", str(workers)]
        if arguments.get("max_pages"):
            cmd += ["--max-pages", str(arguments["max_pages"])]
        out, rc = _run(cmd)
        groups_path = os.path.join(workdir, "groups.json")
        groups = None
        if os.path.exists(groups_path):
            with open(groups_path) as f:
                groups = json.load(f)
        return [types.TextContent(type="text", text=json.dumps({
            "output": out,
            "returncode": rc,
            "groups_path": groups_path,
            "groups": groups,
        }, indent=1))]

    elif name == "assemble_panels":
        folder = arguments["folder"]
        workdir = arguments["workdir"]
        groups_path = arguments["groups_path"]
        cmd = [PYTHON, "backend/tools/batch_panel.py", folder, workdir, "--groups", groups_path]
        out, rc = _run(cmd)
        changelog_path = os.path.join(workdir, "schema_changelog.md")
        changelog = None
        if os.path.exists(changelog_path):
            with open(changelog_path) as f:
                changelog = f.read()
        # Loop Spec 3: surface the quality gate's verdict — a short warning
        # string only, never a refusal. batch_panel.py already wrote
        # workdir/quality_report.json; we just summarize it here.
        quality_warning = None
        quality_path = os.path.join(workdir, "quality_report.json")
        if os.path.exists(quality_path):
            with open(quality_path) as f:
                qreport = json.load(f)
            if qreport.get("overall") != "GREEN":
                failing = [k for k, c in qreport.get("checks", {}).items() if not c.get("pass")]
                quality_warning = (
                    f"Quality gate RED on {', '.join(failing)} — see {quality_path} "
                    "before moving on to analysis (Step 4). This is a warning, not a block."
                )
        return [types.TextContent(type="text", text=json.dumps({
            "output": out,
            "returncode": rc,
            "xlsx_path": os.path.join(workdir, "master.xlsx"),
            "changelog_path": changelog_path,
            "changelog": changelog,
            "quality_warning": quality_warning,
        }, indent=1))]

    elif name == "read_file":
        path = arguments["path"]
        if not os.path.exists(path):
            return [types.TextContent(type="text", text=f"File not found: {path}")]
        with open(path) as f:
            content = f.read()
        return [types.TextContent(type="text", text=content)]

    elif name == "write_groups":
        path = arguments["path"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(arguments["groups"], f, indent=1)
        return [types.TextContent(type="text", text=f"Written: {path}")]

    return [types.TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    async with mcp.server.stdio.stdio_server() as (r, w):
        await app.run(r, w, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
