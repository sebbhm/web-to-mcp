"""End-to-end test: starts the server as Claude Desktop does and calls each tool.

    uv run --no-project python examples/search-console-ai/smoke.py      (from the repository root)

No token needed: the session is read from Chrome AI. The test client comes from the repository's template tools,
or from $WEB_TO_MCP_TOOLS. Set SMOKE_PROPERTY to one of your Search Console properties (default: the first one).
"""

import json
import os
import sys
from pathlib import Path

TOOLS_DIR = os.environ.get("WEB_TO_MCP_TOOLS") or str(Path(__file__).resolve().parents[2] / "template" / "tools")
sys.path.insert(0, TOOLS_DIR)
from mcpcall import McpProcess, show  # noqa: E402

PROPERTY = os.environ.get("SMOKE_PROPERTY")

with McpProcess(Path(__file__).parent) as mcp:
    properties = show(mcp, "gsc_list_properties", {})
    listed = json.loads(properties) if properties else []
    prop = PROPERTY or (listed[0]["id"] if listed else None)
    calls = [("gsc_session", {})]
    if prop:
        calls += [
            ("gsc_ai_overview", {"property": prop, "period": "7d", "top": 3, "daily": False}),
            ("gsc_ai_breakdown", {"property": prop, "dimension": "country", "period": "28d", "limit": 5}),
            ("gsc_ai_breakdown", {"property": prop, "dimension": "device", "period": "28d"}),
        ]
    failures = [name for name, args in calls if show(mcp, name, args) is None]
    if properties is None:
        failures.append("gsc_list_properties")
sys.exit(f"\nFailures: {', '.join(failures)}" if failures else 0)
