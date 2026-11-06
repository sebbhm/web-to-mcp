"""Compact formatting of the responses sent to the model."""

from __future__ import annotations

import json
from typing import Any


def prune(value: Any) -> Any:
    """Recursively removes None fields and empty strings to lighten responses."""
    if isinstance(value, list):
        return [prune(v) for v in value]
    if isinstance(value, dict):
        return {k: prune(v) for k, v in value.items() if v is not None and v != ""}
    return value


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def to_text(value: Any, indent: str = "") -> str:
    """Readable, compact JSON: objects are indented, but each item of an array of objects stays on one line
    (a position, a transaction…). Still valid JSON."""
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        return "[\n" + ",\n".join(f"{indent}  {_compact(x)}" for x in value) + f"\n{indent}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = ",\n".join(f"{indent}  {_compact(k)}: {to_text(v, indent + '  ')}" for k, v in value.items())
        return "{\n" + items + f"\n{indent}}}"
    return _compact(value)
