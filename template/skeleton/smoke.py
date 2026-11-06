"""End-to-end test: starts the server the way Claude Desktop does and calls each tool.

    uv run --no-project python smoke.py      (from this folder, or with the full path from anywhere)

No token to provide: the session is read from Chrome. Add to CALLS for every new tool.

The test client (mcpcall.py) comes from the web-to-mcp template tools: the folder recorded when this MCP was
created, or $WEB_TO_MCP_TOOLS if set (needed after the plugin moved, e.g. on an update).
"""

import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
TOOLS_DIR = os.environ.get("WEB_TO_MCP_TOOLS") or """{{TOOLS_DIR}}"""
if not (Path(TOOLS_DIR) / "mcpcall.py").is_file():
    sys.exit(f"mcpcall.py not found in {TOOLS_DIR}: set WEB_TO_MCP_TOOLS to the tools/ folder of the web-to-mcp template.")
sys.path.insert(0, TOOLS_DIR)
from mcpcall import McpProcess, show  # noqa: E402

CALLS = [
    ("{{PREFIX}}_session", {}),
    # ("{{PREFIX}}_example", {"query": "…"}),
]

with McpProcess(Path(__file__).resolve().parent) as mcp:
    failures = [name for name, args in CALLS if show(mcp, name, args) is None]
sys.exit(f"\nFailures: {', '.join(failures)}" if failures else 0)
