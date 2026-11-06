# /// script
# requires-python = ">=3.11"
# ///
"""Create a new web-app MCP from the template's skeleton.

    uv run <template>/tools/new.py MySite --name my-site --display "My Site" --domain mysite.com \\
        --cookies session_id --login-url https://mysite.com/login --base-url https://api.mysite.com --prefix ms

The folder is created in the current directory (or --parent). The engine is copied into it and the dependencies
are locked: the server starts right away, with the <prefix>_session tool. What is left: writing the tools in
src/server.py.
"""

import argparse
import getpass
import json
import re
import shlex
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True  # the template may live in a read-only plugin cache
from pack import TEMPLATE, sync  # noqa: E402

TOOLS = Path(__file__).resolve().parent
SKELETON = TEMPLATE / "skeleton"


def escape(value: str, suffix: str) -> str:
    """Values are inserted inside strings: JSON and TOML share the same escaping, Python only has to protect
    its docstrings and string literals."""
    if suffix in (".json", ".toml"):
        return json.dumps(value, ensure_ascii=False)[1:-1]
    if suffix == ".py":
        return value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return value


def render(text: str, values: dict[str, str], suffix: str) -> str:
    for key, value in values.items():
        # COOKIES is an already-formed Python/JSON list: inserted as is.
        text = text.replace("{{" + key + "}}", value if key == "COOKIES" else escape(value, suffix))
    return text


def shell_path(path: Path) -> str:
    """A path ready to paste in a shell: double quotes when needed (spaces), shlex quoting for odd characters."""
    text = str(path)
    if re.search(r'["$`\\!]', text):
        return shlex.quote(text)
    return f'"{text}"' if re.search(r"\s|[()'&;|<>*?#~]", text) else text


def check(args: argparse.Namespace) -> None:
    errors = []
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*", args.folder):
        errors.append("folder: a plain folder name, no path (e.g. MySite); use --parent for the location")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", args.name):
        errors.append("--name: lowercase letters, digits and dashes (e.g. my-site)")
    if not re.fullmatch(r"[a-z][a-z0-9]*", args.prefix):
        errors.append("--prefix: short, lowercase (e.g. ms); it prefixes every tool")
    if "/" in args.domain or ":" in args.domain:
        errors.append("--domain: bare domain, no https:// nor path (e.g. mysite.com)")
    for flag in ("login_url", "base_url"):
        if not getattr(args, flag).startswith("https://"):
            errors.append(f"--{flag.replace('_', '-')}: full https:// URL")
    if errors:
        sys.exit("Invalid parameters:\n- " + "\n- ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="name of the folder to create (e.g. MySite)")
    parser.add_argument("--name", help="technical name of the extension (default: folder name, lowercased)")
    parser.add_argument("--display", help="name shown in Claude Desktop (default: folder name)")
    parser.add_argument("--description", default="", help="one sentence: what the MCP lets you do")
    parser.add_argument("--domain", required=True, help="cookie domain of the session (e.g. mysite.com)")
    parser.add_argument("--cookies", required=True, help="session cookies, comma-separated (see harness/min_cookies.py)")
    parser.add_argument("--login-url", required=True, help="page opened in Chrome when the session is missing")
    parser.add_argument("--base-url", required=True, help="root of the internal API (e.g. https://api.mysite.com)")
    parser.add_argument("--prefix", required=True, help="tool prefix (e.g. ms → ms_session)")
    parser.add_argument("--parent", type=Path, default=Path.cwd(), help="parent folder (default: current directory)")
    args = parser.parse_args()
    args.name = args.name or re.sub(r"[^a-z0-9]+", "-", args.folder.lower()).strip("-")
    args.display = args.display or args.folder
    check(args)

    target = args.parent.expanduser().resolve() / args.folder
    if target.exists():
        sys.exit(f"{target} already exists: pick another folder name.")
    cookies = [c.strip() for c in args.cookies.split(",") if c.strip()]
    values = {
        "NAME": args.name,
        "DISPLAY_NAME": args.display,
        "DESCRIPTION": args.description or f"Read access to {args.display} through your web session.",
        "DOMAIN": args.domain,
        "COOKIES": json.dumps(cookies),
        "COOKIES_TEXT": ", ".join(f"`{c}`" for c in cookies),
        "FIRST_COOKIE": cookies[0],
        "LOGIN_URL": args.login_url,
        "BASE_URL": args.base_url,
        "PREFIX": args.prefix,
        "TOKEN_ENV": f"{args.prefix.upper()}_AUTH_TOKEN",
        "AUTHOR": getpass.getuser(),
        "DATE": date.today().isoformat(),
        "DIR": args.folder,
        "TOOLS_DIR": str(TOOLS),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKELETON, target, ignore=shutil.ignore_patterns("__pycache__", ".venv", "*.mcpb", "uv.lock"))
    try:
        for path in target.rglob("*"):
            if path.is_file():
                path.write_text(render(path.read_text(), values, path.suffix))
        sync(target)
        subprocess.run(["uv", "lock", "--quiet"], cwd=target, check=True)
    except BaseException:
        shutil.rmtree(target)  # no half-created MCP
        raise

    mcp, tools = shell_path(target), TOOLS
    print(
        f"\nMCP created: {target}\n"
        f"  1. Check it starts:   uv run {shell_path(tools / 'mcpcall.py')} {mcp} {args.prefix}_session '{{}}'\n"
        f"  2. Write the tools in {shell_path(target / 'src' / 'server.py')}"
        f" (endpoints found with the scripts in {shell_path(TEMPLATE / 'harness')})\n"
        f"  3. Test:              uv run --no-project python {shell_path(target / 'smoke.py')}\n"
        f"  4. Package:           uv run {shell_path(tools / 'pack.py')} {mcp}"
    )


if __name__ == "__main__":
    main()
