# /// script
# requires-python = ">=3.11"
# ///
"""Prepare and package an MCP built on the template.

    uv run <template>/tools/pack.py <mcp_folder>           # copy the engine, lock, validate, package
    uv run <template>/tools/pack.py <mcp_folder> --sync    # only copy the engine (to test locally)

<mcp_folder> is absolute or relative to the current directory. The engine (core/webapp_mcp of the template this
script belongs to) is copied into <mcp>/src/webapp_mcp: the .mcpb stays self-contained, and an engine fix reaches
every MCP at its next package. The manifest's tool list (shown by Claude Desktop at install time) is copied from
the server itself.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

sys.dont_write_bytecode = True  # the template may live in a read-only plugin cache
from mcpcall import McpProcess, mcp_folder  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent
CORE = TEMPLATE / "core"
MCPB = "@anthropic-ai/mcpb@2.1.2"


def requirements(pyproject: Path) -> set[str]:
    deps = tomllib.loads(pyproject.read_text())["project"].get("dependencies", [])
    return {re.split(r"[\s<>=!~;\[]", d, maxsplit=1)[0].lower().replace("_", "-") for d in deps}


def sync(mcp: Path) -> None:
    target = mcp / "src" / "webapp_mcp"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(CORE / "webapp_mcp", target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    missing = requirements(CORE / "pyproject.toml") - requirements(mcp / "pyproject.toml")
    if missing:
        sys.exit(f"Engine dependencies missing from {mcp / 'pyproject.toml'}: {', '.join(sorted(missing))}")
    print(f"Engine copied to {target}")


def run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def first_sentence(text: str | None, limit: int = 300) -> str:
    flat = " ".join((text or "").split())
    # Cut at the end of the first sentence (period followed by a capital letter), not on "e.g." or "24 h.".
    match = re.match(r"(.+?[.!?])\s+(?=[A-ZÀ-Ý])", flat)
    sentence = match.group(1) if match else flat
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def sync_manifest_tools(mcp: Path) -> None:
    """Start the server (which checks along the way that it starts) and copy its tools into manifest.json."""
    with McpProcess(mcp) as server:
        tools = [{"name": t["name"], "description": first_sentence(t.get("description"))} for t in server.tools()]
    path = mcp / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["tools"] = tools
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"Manifest: {len(tools)} tools ({', '.join(t['name'] for t in tools)})")


def pack(mcp: Path) -> None:
    sync(mcp)
    run(["uv", "lock"], mcp)
    sync_manifest_tools(mcp)
    run(["npx", "-y", MCPB, "validate", "manifest.json"], mcp)
    name = json.loads((mcp / "manifest.json").read_text())["name"]
    run(["npx", "-y", MCPB, "pack", ".", f"{name}.mcpb"], mcp)
    print(f"\nPackage ready: {mcp / f'{name}.mcpb'} (double-click it to install it in Claude Desktop)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mcp_dir", type=Path, help="MCP folder (absolute, or relative to the current directory)")
    parser.add_argument("--sync", action="store_true", help="copy the engine without packaging")
    args = parser.parse_args()
    mcp = mcp_folder(args.mcp_dir, need_manifest=True)
    sync(mcp) if args.sync else pack(mcp)
