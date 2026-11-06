# /// script
# requires-python = ">=3.11"
# ///
"""Minimal MCP client (stdio, JSON-RPC): starts a local MCP the way Claude Desktop does and calls its tools.

    uv run <template>/tools/mcpcall.py <mcp_folder>                       # list the tools
    uv run <template>/tools/mcpcall.py <mcp_folder> <tool> '{"arg": 1}'   # call a tool
    Add -v to see the server logs.

<mcp_folder> is absolute or relative to the current directory.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def mcp_folder(path: Path, need_manifest: bool = False) -> Path:
    """Resolve an MCP folder (absolute or relative to the cwd) and check it looks like one."""
    folder = path.expanduser().resolve()
    expected = ["src/server.py", "pyproject.toml"] + (["manifest.json"] if need_manifest else [])
    missing = [name for name in expected if not (folder / name).is_file()]
    if missing:
        sys.exit(f"{folder} is not an MCP folder made from the template (missing: {', '.join(missing)}).")
    return folder


class McpProcess:
    def __init__(self, mcp_dir: Path, verbose: bool = False):
        self.proc = subprocess.Popen(
            ["uv", "run", "--quiet", "--directory", str(mcp_dir), "--frozen", "src/server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None if verbose else subprocess.DEVNULL,
            text=True,
        )
        self._next_id = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "mcpcall", "version": "1"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message: dict) -> None:
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}})
        while line := self.proc.stdout.readline():
            message = json.loads(line)
            if message.get("id") == self._next_id:  # skip the server's notifications
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]
        raise RuntimeError(f"The server stopped during {method} (run again with -v to see its logs)")

    def tools(self) -> list[dict]:
        return self.rpc("tools/list")["tools"]

    def call(self, name: str, arguments: dict | None = None) -> tuple[bool, str]:
        """Returns (is_error, response text)."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        return bool(result.get("isError")), text

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def show(mcp: McpProcess, name: str, arguments: dict | None = None, lines: int = 40) -> str | None:
    """Call a tool and print the first `lines` lines of its response. Returns the text, or None on error."""
    is_error, text = mcp.call(name, arguments)
    all_lines = text.split("\n")
    print(f"\n=== {name} {json.dumps(arguments or {}, ensure_ascii=False)}{'  ❌ ERROR' if is_error else ''} ===")
    print("\n".join(all_lines[:lines]) + (f"\n… ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""))
    return None if is_error else text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mcp_dir", type=Path, help="MCP folder (absolute, or relative to the current directory)")
    parser.add_argument("tool", nargs="?")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of arguments (default {})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    with McpProcess(mcp_folder(args.mcp_dir), args.verbose) as mcp:
        if not args.tool:
            for tool in mcp.tools():
                params = ", ".join(tool.get("input_schema", tool.get("inputSchema", {})).get("properties", {}))
                print(f"- {tool['name']}({params}): {(tool.get('description') or '').splitlines()[0]}")
        else:
            is_error, text = mcp.call(args.tool, json.loads(args.arguments))
            print(("ERROR: " if is_error else "") + text)
            sys.exit(1 if is_error else 0)
